/**
 * Plays a live MP3 stream (the playout engine's Icecast mount) with a short start
 * and a small delay behind live.
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
 */

// How far behind the newest audio received playback is kept (seconds)
const TARGET_DELAY = 1.0;
// Further behind than this (after a stall), jump forward to TARGET_DELAY
const MAX_DELAY = 2.5;
// Audio already played is dropped from the buffer after this long (seconds)
const KEEP_BEHIND = 20;

export interface LiveStream {
  stop: () => void;
  mode: 'mse' | 'hls' | 'plain';
}

// Give up on HLS and use the plain stream if nothing is heard after this long (ms)
const HLS_START_TIMEOUT = 8000;

/** The engine's HLS copy of an Icecast mount: /live-<id> -> /hls/<id>/mp3.m3u8 */
export function hlsUrlFor(url: string): string | null {
  const m = url.match(/^(https?:\/\/[^/]+)\/live-([0-9a-f-]{36})$/i);
  return m ? `${m[1]}/hls/${m[2]}/mp3.m3u8` : null;
}

function nativeHls(audio: HTMLAudioElement): boolean {
  return audio.canPlayType('application/vnd.apple.mpegurl') !== '';
}

function plainStream(audio: HTMLAudioElement, url: string, onFail: () => void): LiveStream {
  audio.src = url;
  audio.play().catch(onFail);
  return { mode: 'plain', stop: () => { audio.pause(); audio.removeAttribute('src'); audio.load(); } };
}

function hlsStream(audio: HTMLAudioElement, hls: string, url: string, onFail: () => void): LiveStream {
  let stopped = false;
  let fellBack = false;
  let plain: LiveStream | null = null;
  const cleanup = () => {
    clearTimeout(timer);
    audio.removeEventListener('error', fallBack);
    audio.removeEventListener('playing', started);
  };
  function fallBack() {
    if (stopped || fellBack) return;
    fellBack = true;
    cleanup();
    plain = plainStream(audio, url, onFail);
  }
  function started() { clearTimeout(timer); }
  const timer = setTimeout(() => { if (audio.paused || audio.currentTime === 0) fallBack(); }, HLS_START_TIMEOUT);
  audio.addEventListener('error', fallBack);
  audio.addEventListener('playing', started);
  audio.src = hls;
  // In the click, so iOS allows sound; a refusal here is final (no gesture later)
  audio.play().catch(() => { if (!fellBack) { stopped = true; cleanup(); onFail(); } });
  return {
    mode: 'hls',
    stop: () => {
      stopped = true;
      cleanup();
      if (plain) { plain.stop(); return; }
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    },
  };
}

function mseSupported(): boolean {
  const MS = (window as unknown as { MediaSource?: typeof MediaSource }).MediaSource;
  return !!MS && typeof MS.isTypeSupported === 'function' && MS.isTypeSupported('audio/mpeg');
}

/** Start playing `url` on `audio`. Call inside the click so the browser allows sound. */
export function startLiveStream(audio: HTMLAudioElement, url: string, onFail: () => void): LiveStream {
  if (!mseSupported()) {
    const hls = hlsUrlFor(url);
    if (hls && nativeHls(audio)) return hlsStream(audio, hls, url, onFail);
    return plainStream(audio, url, onFail);
  }

  const ms = new MediaSource();
  const objectUrl = URL.createObjectURL(ms);
  const abort = new AbortController();
  let stopped = false;
  let failed = false;
  audio.src = objectUrl;
  // In the click: the element may play from now on, as soon as data arrives
  audio.play().catch(() => { /* retried when the first data is in */ });

  const fallBack = () => {
    if (stopped || failed) return;
    failed = true;
    abort.abort();
    URL.revokeObjectURL(objectUrl);
    audio.src = url;
    audio.play().catch(onFail);
  };

  const keepNearLive = () => {
    const b = audio.buffered;
    if (!b.length) return;
    const end = b.end(b.length - 1);
    if (end - audio.currentTime > MAX_DELAY || audio.currentTime < b.start(b.length - 1)) {
      audio.currentTime = Math.max(b.start(b.length - 1), end - TARGET_DELAY);
    }
    if (audio.paused && !stopped) audio.play().catch(() => {});
  };

  ms.addEventListener('sourceopen', async () => {
    let sb: SourceBuffer;
    try {
      sb = ms.addSourceBuffer('audio/mpeg');
      sb.mode = 'sequence';
    } catch {
      fallBack();
      return;
    }
    const pending: Uint8Array[] = [];
    const pump = () => {
      if (stopped || failed || sb.updating || !pending.length) return;
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
        fallBack();
      }
    };
    sb.addEventListener('updateend', () => { keepNearLive(); pump(); });

    try {
      const res = await fetch(url, { signal: abort.signal, cache: 'no-store' });
      if (!res.ok || !res.body) throw new Error(`stream ${res.status}`);
      const reader = res.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done || stopped) break;
        if (value?.length) { pending.push(value); pump(); }
      }
      if (!stopped) fallBack(); // the server closed the stream: let the element reconnect
    } catch {
      if (!stopped) fallBack();
    }
  }, { once: true });

  return {
    mode: 'mse',
    stop: () => {
      stopped = true;
      abort.abort();
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      URL.revokeObjectURL(objectUrl);
    },
  };
}

function concat(parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}
