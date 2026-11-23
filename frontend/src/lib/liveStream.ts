/**
 * Plays a live MP3 stream (the playout engine's Icecast mount) with a short start
 * and a small delay behind live, and keeps it playing: a dropped or stuck connection
 * is reconnected by itself, for as long as the page wants the sound.
 *
 * A plain <audio src=stream> waits for about 200 KB before it starts (5 s at
 * 320 kbps, measured in Chrome) and then plays 2.3 s behind what it has, so a
 * listener heard Next 3-4 s after the press. Here the stream is fetched and fed to
 * Media Source Extensions: playback starts on the first data and is kept close to
 * the newest audio.
 *
 * iPhone Safari has no MSE for MP3, and its plain element waited about 10 s before
 * the first sound (Shmuel, 2026-11-17). It plays the engine's HLS copy of the same
 * stream natively instead (1 s segments, /hls/<station id>/mp3.m3u8): first sound in
 * about a second, and lock screen and CarPlay work as for any native stream. If the
 * HLS copy is missing or does not start, it falls back to the plain element.
 *
 * Reconnecting (2026-11-19): a connection that ends, errors, or stops giving data
 * used to be the end of the sound. MSE switched to the plain element after the first
 * drop, and the plain element simply ended at the second, silent for good while the
 * page still showed the station playing ("why did stream stop?"). Now every way of
 * playing reconnects with a growing wait (1, 2, 4, 8, then every 10 s) until the page
 * stops it. The element is the one a tap unlocked, so it may play again without a
 * new tap (Chrome keeps the page's permission, Safari the element's). If the browser
 * refuses anyway, or something else paused it (the lock screen, a phone call), the
 * stream says 'blocked' and the page asks for one tap.
 *
 * Taken away just after the start (2026-11-20): on Shmuel's iPhone the stream played
 * about a second and stopped, and only "mute, then Listen again" brought it back. The
 * phone had paused the element itself (iPhone Safari pauses a page's player when it
 * moves the sound between the page's players or briefly takes the audio session);
 * that was shown as 'blocked' and waited for a tap. A pause that is not this player's,
 * within RESUME_WINDOW_MS of the sound starting, is now answered with play() on the
 * same element, which a tap already unlocked, so no new tap is needed. At most
 * RESUME_TRIES times a minute; after that, or when the browser refuses, 'blocked'.
 * A pause after steady sound (the lock screen, headphones out) is still left alone.
 *
 * The state is on the element: `audio.dataset.streamState` and a STREAM_STATE_EVENT
 * event each time it changes (the element is the tab's shared one, lib/stationStream).
 */

// How far behind the newest audio received playback is kept (seconds)
const TARGET_DELAY = 1.0;
// Further behind than this (after a stall), jump forward to TARGET_DELAY
const MAX_DELAY = 2.5;
// Audio already played is dropped from the buffer after this long (seconds)
const KEEP_BEHIND = 20;

export type StreamState = 'starting' | 'playing' | 'reconnecting' | 'blocked' | 'stopped';
export const STREAM_STATE_EVENT = 'kbr-stream-state';

export interface LiveStream {
  stop: () => void;
  mode: 'mse' | 'hls' | 'plain';
}

// Give up on HLS and use the plain stream if nothing is heard after this long (ms)
const HLS_START_TIMEOUT = 8000;
// Playing but the time has not moved (or no data came) for this long: reconnect (ms)
export const STALL_TIMEOUT = 6000;
// How often the stream is looked at (ms)
const WATCH_EVERY = 1000;
// Waits before each reconnect attempt (ms); the last one repeats
export const RETRY_DELAYS = [1000, 2000, 4000, 8000, 10000];
// HLS reloads that did not bring the sound back before trying the plain stream
const HLS_RELOADS_BEFORE_PLAIN = 3;
// A pause that is not this player's this soon after the sound started: play again (ms)
export const RESUME_WINDOW_MS = 20_000;
// How long after such a pause play() is called again (ms)
export const RESUME_AFTER_MS = 400;
// At most this many such resumes a minute; then the page asks for a tap
export const RESUME_TRIES = 3;

/** The engine's HLS copy of an Icecast mount: /live-<id> -> /hls/<id>/mp3.m3u8 */
export function hlsUrlFor(url: string): string | null {
  const m = url.match(/^(https?:\/\/[^/]+)\/live-([0-9a-f-]{36})$/i);
  return m ? `${m[1]}/hls/${m[2]}/mp3.m3u8` : null;
}

function nativeHls(audio: HTMLAudioElement): boolean {
  return audio.canPlayType('application/vnd.apple.mpegurl') !== '';
}

function mseSupported(): boolean {
  const MS = (window as unknown as { MediaSource?: typeof MediaSource }).MediaSource;
  return !!MS && typeof MS.isTypeSupported === 'function' && MS.isTypeSupported('audio/mpeg');
}

/** The stream state on a stream element ('stopped' when it plays nothing). */
export function streamStateOf(audio: HTMLAudioElement | null | undefined): StreamState {
  const s = audio?.dataset?.streamState as StreamState | undefined;
  return s ?? 'stopped';
}

function setState(audio: HTMLAudioElement, s: StreamState) {
  const ds = audio.dataset as DOMStringMap | undefined;
  if (!ds || ds.streamState === s) return;
  ds.streamState = s;
  try { audio.dispatchEvent(new CustomEvent(STREAM_STATE_EVENT, { detail: s })); } catch { /* no events (tests) */ }
}

const refused = (e: unknown) => (e as { name?: string } | null)?.name === 'NotAllowedError';

/** Start playing `url` on `audio`. Call inside the click so the browser allows sound.
 *  `onFail` is called once if the stream cannot start at all (the first play is
 *  refused); later trouble is reconnected, or shown as 'blocked'. */
export function startLiveStream(audio: HTMLAudioElement, url: string, onFail: () => void): LiveStream {
  type Mode = LiveStream['mode'];
  const hls = !mseSupported() ? hlsUrlFor(url) : null;
  let mode: Mode = mseSupported() ? 'mse' : hls && nativeHls(audio) ? 'hls' : 'plain';

  let stopped = false;
  let everPlayed = false;
  let failedOnce = false;
  let attempt = 0;               // reconnects since the sound last ran
  let hlsReloads = 0;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  let startTimer: ReturnType<typeof setTimeout> | undefined;
  let lastTime = -1;
  let lastMoveAt = Date.now();
  let playingSince = 0;
  // MSE pieces: one attempt (a MediaSource) per generation, one fetch at a time in it
  let objectUrl: string | null = null;
  let mseGen = 0;
  let fetchAbort: AbortController | null = null;
  let wake: (() => void) | null = null;
  let lastDataAt = Date.now();
  const startedAt = Date.now();
  // Pauses this player answered by playing again (times), and the one waiting to
  let resumes: number[] = [];
  let resumeTimer: ReturnType<typeof setTimeout> | undefined;

  const fail = () => {
    if (failedOnce || stopped) return;
    failedOnce = true;
    stopAll();
    onFail();
  };

  // Each play() call; a refusal of an older one is not this attempt's (2026-11-20: the
  // MSE attempt's play(), interrupted by the plain element's new load when the stream
  // has no CORS, was read as the plain element failing, and the page gave up the stream)
  let playGen = 0;
  const tryPlay = (first: boolean) => {
    const gen = ++playGen;
    let p: Promise<void> | undefined;
    try { p = audio.play(); } catch (e) { p = Promise.reject(e); }
    Promise.resolve(p).catch((e) => {
      if (stopped || gen !== playGen) return;
      if (!everPlayed && first && mode !== 'mse') { fail(); return; }
      // The browser wants a tap: say so and wait for the page to start us again
      if (refused(e)) { setState(audio, 'blocked'); clearTimeout(retryTimer); retryTimer = undefined; return; }
      // MSE plays when its data comes; anything else is tried again
      if (mode !== 'mse') scheduleReconnect();
    });
  };

  // ── MSE ──
  const clearMse = () => {
    mseGen += 1;
    fetchAbort?.abort();
    fetchAbort = null;
    wake?.();
    wake = null;
    if (objectUrl) { try { URL.revokeObjectURL(objectUrl); } catch { /* gone */ } }
    objectUrl = null;
  };
  const startMse = () => {
    clearMse();
    const gen = mseGen;
    const live = () => !stopped && gen === mseGen;
    const ms = new MediaSource();
    objectUrl = URL.createObjectURL(ms);
    audio.src = objectUrl;
    // In the click: the element may play from now on, as soon as data arrives
    tryPlay(true);

    const keepNearLive = () => {
      const b = audio.buffered;
      if (!b.length) return;
      const end = b.end(b.length - 1);
      if (end - audio.currentTime > MAX_DELAY || audio.currentTime < b.start(b.length - 1)) {
        audio.currentTime = Math.max(b.start(b.length - 1), end - TARGET_DELAY);
      }
      if (audio.paused && !stopped && streamStateOf(audio) !== 'blocked') tryPlay(false);
    };

    ms.addEventListener('sourceopen', async () => {
      if (!live()) return;
      let sb: SourceBuffer;
      try {
        sb = ms.addSourceBuffer('audio/mpeg');
        sb.mode = 'sequence';
      } catch {
        toPlain();
        return;
      }
      const pending: Uint8Array[] = [];
      const pump = () => {
        if (!live() || sb.updating || !pending.length) return;
        // Drop what was played long ago, so a long session does not fill memory
        const b = sb.buffered;
        if (b.length && audio.currentTime - b.start(0) > KEEP_BEHIND * 2) {
          try { sb.remove(b.start(0), audio.currentTime - KEEP_BEHIND); return; } catch { /* try next time */ }
        }
        const chunk = pending.length === 1 ? pending[0] : concat(pending);
        pending.length = 0;
        try {
          sb.appendBuffer(chunk as BufferSource);
        } catch {
          toPlain();
        }
      };
      sb.addEventListener('updateend', () => { if (live()) { keepNearLive(); pump(); } });

      // One connection after another into the same buffer: a dropped connection is
      // fetched again and its audio carries on the same timeline (sequence mode)
      let connections = 0;
      for (;;) {
        if (!live()) return;
        const ac = new AbortController();
        fetchAbort = ac;
        let gotData = false;
        try {
          lastDataAt = Date.now();
          connections += 1;
          const res = await fetch(url, { signal: ac.signal, cache: 'no-store' });
          if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
          const reader = res.body.getReader();
          for (;;) {
            const { done, value } = await reader.read();
            if (done || !live()) break;
            if (value?.length) { gotData = true; lastDataAt = Date.now(); pending.push(value); pump(); }
          }
        } catch {
          // The very first connection gave nothing (no CORS on the mount, or an error
          // answer): the plain element tries; if it cannot play either, onFail
          if (connections === 1 && !gotData && !everPlayed && live()) { toPlain(); return; }
        }
        // The very first connection closed with no data at all: same as above
        if (connections === 1 && !gotData && !everPlayed && live()) { toPlain(); return; }
        if (!live()) return;
        setState(audio, 'reconnecting');
        const wait = RETRY_DELAYS[Math.min(attempt, RETRY_DELAYS.length - 1)];
        attempt += 1;
        await new Promise<void>((r) => {
          const t = setTimeout(() => { wake = null; r(); }, wait);
          wake = () => { clearTimeout(t); wake = null; r(); };
        });
      }
    }, { once: true });
  };
  const toPlain = () => {
    if (stopped) return;
    clearMse();
    clearTimeout(startTimer);
    mode = 'plain';
    stream.mode = 'plain';
    audio.src = url;
    tryPlay(!everPlayed);
  };

  // ── HLS and plain: the element fetches by itself; reconnect = load it again ──
  const startElement = (first: boolean) => {
    audio.src = mode === 'hls' && hls ? hls : url;
    tryPlay(first);
    if (mode === 'hls' && !everPlayed) {
      clearTimeout(startTimer);
      startTimer = setTimeout(() => {
        if (!stopped && !everPlayed && mode === 'hls') toPlain();
      }, HLS_START_TIMEOUT);
    }
  };

  function scheduleReconnect() {
    if (stopped || retryTimer !== undefined) return;
    if (streamStateOf(audio) === 'blocked') return;
    setState(audio, 'reconnecting');
    const wait = RETRY_DELAYS[Math.min(attempt, RETRY_DELAYS.length - 1)];
    attempt += 1;
    retryTimer = setTimeout(() => {
      retryTimer = undefined;
      if (stopped) return;
      lastMoveAt = Date.now();
      lastTime = -1;
      if (mode === 'mse') {
        // A stuck MSE element with data still arriving: start a fresh MSE attempt
        startMse();
        return;
      }
      if (mode === 'hls') {
        hlsReloads += 1;
        if (hlsReloads > HLS_RELOADS_BEFORE_PLAIN) { toPlain(); return; }
      }
      startElement(false);
    }, wait);
  }

  // ── Events and the watch ──
  const onPlaying = () => {
    if (stopped) return;
    clearTimeout(startTimer);
    everPlayed = true;
    if (!playingSince) playingSince = Date.now();
    lastMoveAt = Date.now();
    setState(audio, 'playing');
  };
  const onEnded = () => { if (!stopped) { playingSince = 0; scheduleReconnect(); } };
  // Paused, and not by this player (it pauses only when stopped): the phone took the
  // sound. Just after the start, take it back; otherwise wait for a tap.
  const onForeignPause = () => {
    if (stopped || audio.ended || resumeTimer !== undefined) return;
    if (streamStateOf(audio) !== 'playing') return;
    const now = Date.now();
    const soon = playingSince > 0 && now - playingSince < RESUME_WINDOW_MS;
    resumes = resumes.filter((t) => now - t < 60_000);
    playingSince = 0;
    if (!soon || resumes.length >= RESUME_TRIES) { setState(audio, 'blocked'); return; }
    resumes.push(now);
    resumeTimer = setTimeout(() => {
      resumeTimer = undefined;
      if (stopped || !audio.paused) return;
      // MSE: back near the newest audio (keepNearLive does it on the next data);
      // HLS and plain: the element goes on from where it was paused
      let p: Promise<void> | undefined;
      try { p = audio.play(); } catch (e) { p = Promise.reject(e); }
      Promise.resolve(p).catch((e) => {
        if (stopped) return;
        if (refused(e)) { setState(audio, 'blocked'); return; }
        scheduleReconnect();
      });
    }, RESUME_AFTER_MS);
  };
  const onPause = () => { if (!stopped && audio.paused) onForeignPause(); };
  const onError = () => {
    if (stopped) return;
    // An error before HLS ever played: the HLS copy is missing, use the plain stream
    if (mode === 'hls' && !everPlayed) { toPlain(); return; }
    if (mode === 'mse') { toPlain(); return; }
    playingSince = 0;
    scheduleReconnect();
  };
  const onOnline = () => {
    if (stopped || streamStateOf(audio) !== 'reconnecting') return;
    attempt = 0;
    if (mode === 'mse') { wake?.(); return; } // the fetch loop tries again at once
    clearTimeout(retryTimer);
    retryTimer = undefined;
    scheduleReconnect();
  };
  const watch = () => {
    if (stopped) return;
    const now = Date.now();
    const state = streamStateOf(audio);
    if (audio.currentTime !== lastTime) { lastTime = audio.currentTime; lastMoveAt = now; }
    // Sound ran steadily for a while: the next trouble starts the waits from the beginning
    if (state === 'playing' && playingSince && now - playingSince > 10_000) { attempt = 0; hlsReloads = 0; }
    if (state === 'blocked') return;
    if (state === 'starting' && !everPlayed) {
      // Never started (the server took the connection but sent nothing): try again
      if (mode !== 'hls' && now - startedAt > 15_000 && now - lastMoveAt > 15_000) { lastMoveAt = now; scheduleReconnect(); }
      return;
    }
    // Paused by something other than this player (lock screen, a call, headphones out,
    // or the phone just after the start): the 'pause' event normally gets here first
    if (resumeTimer !== undefined) return;
    if (state === 'playing' && audio.paused && !audio.ended) { onForeignPause(); return; }
    if (mode === 'mse') {
      // The connection is open but nothing comes (a network black hole): drop it, the
      // fetch loop connects again
      if (fetchAbort && now - lastDataAt > STALL_TIMEOUT && state !== 'reconnecting') {
        playingSince = 0;
        lastDataAt = now;
        fetchAbort.abort();
        return;
      }
      // Data comes but the element is stuck: a fresh MediaSource
      if (!audio.paused && now - lastDataAt < 2000 && now - lastMoveAt > STALL_TIMEOUT && retryTimer === undefined) {
        playingSince = 0;
        scheduleReconnect();
      }
      return;
    }
    if (!audio.paused && now - lastMoveAt > STALL_TIMEOUT && retryTimer === undefined) {
      playingSince = 0;
      scheduleReconnect();
    }
  };

  function stopAll() {
    stopped = true;
    clearTimeout(retryTimer);
    clearTimeout(startTimer);
    clearTimeout(resumeTimer);
    resumeTimer = undefined;
    clearInterval(watchTimer);
    audio.removeEventListener('playing', onPlaying);
    audio.removeEventListener('pause', onPause);
    audio.removeEventListener('ended', onEnded);
    audio.removeEventListener('error', onError);
    if (typeof window !== 'undefined' && window.removeEventListener) window.removeEventListener('online', onOnline);
    clearMse();
    audio.pause();
    audio.removeAttribute('src');
    audio.load();
    setState(audio, 'stopped');
  }

  const stream: LiveStream = {
    mode,
    stop: () => { if (!stopped) stopAll(); },
  };

  setState(audio, 'starting');
  audio.addEventListener('playing', onPlaying);
  audio.addEventListener('pause', onPause);
  audio.addEventListener('ended', onEnded);
  audio.addEventListener('error', onError);
  if (typeof window !== 'undefined' && window.addEventListener) window.addEventListener('online', onOnline);
  const watchTimer = setInterval(watch, WATCH_EVERY);

  if (mode === 'mse') startMse();
  else startElement(true);
  return stream;
}

function concat(parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}
