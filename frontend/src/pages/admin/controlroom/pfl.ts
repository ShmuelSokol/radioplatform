import { useCallback, useEffect, useRef, useState } from 'react';
import { getAssetAudioUrl } from '../../../api/assets';

/**
 * PFL (pre-fade listen): hear an item in this browser only, never on the station.
 * One player for the whole control room, so starting a second item stops the first.
 * Where the browser allows it (Chrome, Edge, new Safari on a Mac) the sound can be
 * sent to a second output such as the headphones while the main output carries air.
 *
 * Besides library items it plays a run of clips that are not in the library (the
 * time & weather preview: time check, then weather) as one item, so the deck's
 * play/pause, seek and stop work across the whole run.
 */

/** Deck id of the time & weather preview (not an asset id). */
export const WEATHER_PFL_ID = 'preview:time-weather';

/** One clip of a run played as one pre-listen item. */
export interface PflClip { url: string; label: string }

interface Seq { id: string; label: string; clips: PflClip[]; index: number; durations: number[] }

const OUTPUT_KEY = 'kbr_pfl_output';

export interface PflOutput { id: string; label: string }

type SinkAudio = HTMLAudioElement & { setSinkId?: (id: string) => Promise<void> };

const readOutput = (): string => {
  try { return localStorage.getItem(OUTPUT_KEY) ?? ''; } catch { return ''; }
};

export const UNPLAYABLE_MESSAGE = "Can't play this file: its audio is in an old format browsers don't play (often MP2 saved as .mp3). It needs converting.";
export const NETWORK_MESSAGE = "Couldn't load this file. Check the connection and press ▶ to try again.";

export function usePfl() {
  const audioRef = useRef<SinkAudio | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
  // The item in the pre-listen player (shown in the PFL deck), playing or paused
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  // Why the item in the deck cannot be heard (file the browser cannot play, or not reachable)
  const [error, setError] = useState<string | null>(null);
  const [outputId, setOutputId] = useState<string>(readOutput);
  const [outputs, setOutputs] = useState<PflOutput[]>([]);
  const outputRef = useRef(outputId);
  outputRef.current = outputId;
  // A run of clips in the deck (time & weather preview), or null for a library item
  const seqRef = useRef<Seq | null>(null);
  const [seq, setSeq] = useState<{ label: string; clips: string[] } | null>(null);
  const [clipIndex, setClipIndex] = useState(0);

  /** Let go of whatever is in the player (one item or a run of clips). */
  const release = useCallback(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    seqRef.current = null;
    setSeq(null);
    setClipIndex(0);
  }, []);

  const canChooseOutput = typeof HTMLMediaElement !== 'undefined'
    && 'setSinkId' in HTMLMediaElement.prototype
    && !!navigator.mediaDevices?.enumerateDevices;

  const stop = useCallback(() => {
    release();
    setPlayingId(null);
    setLoadedId(null);
    setPaused(false);
    setUrl(null);
    setError(null);
  }, [release]);
  useEffect(() => () => { audioRef.current?.pause(); }, []);

  const toggle = useCallback(async (id: string) => {
    const cur = audioRef.current;
    if (cur?.dataset.pflId === id && cur.paused && cur.src) {
      // Paused in the deck: ▶ carries on from where it stopped
      if (cur.ended) cur.currentTime = 0;
      try { await cur.play(); setPaused(false); setPlayingId(id); } catch { /* blocked */ }
      return;
    }
    const wasPlaying = audioRef.current?.dataset.pflId === id;
    release();
    if (wasPlaying) { setPlayingId(null); setLoadedId(null); setPaused(false); setUrl(null); setError(null); return; }
    setPlayingId(id);
    setLoadedId(id);
    setPaused(false);
    setUrl(null);
    setError(null);
    // At the end the deck stays open, paused at the end, so it can be played again
    const ended = () => { setPlayingId(cur => (cur === id ? null : cur)); setPaused(true); };
    // A file that cannot be heard keeps the deck open with the reason, instead of closing it
    const fail = (message: string) => {
      if (audioRef.current && audioRef.current.dataset.pflId !== id) return; // another item took over
      setPlayingId(cur => (cur === id ? null : cur));
      setPaused(true);
      setError(message);
    };
    // Chrome reports a file it could not fetch at all (connection dropped, server
    // down) as "not supported", the same as an MP2 file. Ask for its first bytes to
    // tell them apart: only a file that arrives but cannot be decoded is unplayable.
    const reachable = async (src: string) => {
      try {
        const res = await fetch(src, { headers: { Range: 'bytes=0-1' }, cache: 'no-store' });
        return res.ok;
      } catch {
        return false;
      }
    };
    const mediaFailed = async (a: HTMLAudioElement) => {
      const code = a.error?.code;
      if (code === 2) { fail(NETWORK_MESSAGE); return; }
      fail(a.src && !(await reachable(a.src)) ? NETWORK_MESSAGE : UNPLAYABLE_MESSAGE);
    };
    try {
      const a = new Audio() as SinkAudio;
      a.dataset.pflId = id;
      audioRef.current = a;
      const url = await getAssetAudioUrl(id);
      if (audioRef.current !== a) return; // another item was started meanwhile
      if (outputRef.current && a.setSinkId) {
        // A headset that was unplugged must not silence the pre-listen: fall back to the default output.
        await a.setSinkId(outputRef.current).catch(() => undefined);
      }
      a.src = url;
      a.preload = 'auto';
      a.onended = ended;
      a.onerror = () => { void mediaFailed(a); };
      setUrl(url);
      await a.play();
    } catch (e) {
      const name = (e as { name?: string } | null)?.name;
      if (name === 'NotAllowedError') {
        // The browser wants a click first: leave it paused in the deck, ▶ starts it
        setPlayingId(cur => (cur === id ? null : cur));
        setPaused(true);
      } else if (name === 'AbortError') {
        // Replaced by another item
      } else if (name === 'NotSupportedError' || audioRef.current?.error) {
        if (audioRef.current) await mediaFailed(audioRef.current);
        else fail(UNPLAYABLE_MESSAGE);
      } else {
        fail(NETWORK_MESSAGE);
      }
    }
  }, [release]);

  /** Show clip `i` of the run in the player, at `at` seconds into it. */
  const loadClip = useCallback((i: number, at: number, play: boolean) => {
    const s = seqRef.current;
    const a = audioRef.current;
    if (!s || !a) return;
    if (i !== s.index || a.ended) {
      s.index = i;
      setClipIndex(i);
      a.src = s.clips[i].url;
      if (at > 0) a.addEventListener('loadedmetadata', () => { a.currentTime = at; }, { once: true });
    } else {
      a.currentTime = at;
    }
    if (play) {
      a.play().then(() => { setPaused(false); setPlayingId(s.id); }).catch(() => { setPlayingId(null); setPaused(true); });
    }
  }, []);

  /**
   * Play clips one after another as one pre-listen item (the time & weather
   * preview). Starting it stops whatever was in the deck, and the other way round.
   */
  const playClips = useCallback(async (id: string, label: string, clips: PflClip[]) => {
    release();
    if (clips.length === 0) return;
    const s: Seq = { id, label, clips, index: 0, durations: clips.map(() => 0) };
    // One element for the whole run: once the browser let it play (a tap on an
    // iPhone), the next clip may play from it too without another tap.
    const a = new Audio() as SinkAudio;
    a.dataset.pflId = id;
    a.preload = 'auto';
    seqRef.current = s;
    audioRef.current = a;
    setSeq({ label, clips: clips.map(c => c.label) });
    setClipIndex(0);
    setPlayingId(id);
    setLoadedId(id);
    setPaused(false);
    setUrl(null);
    setError(null);
    // The lengths of the later clips, so the bar covers the whole run from the start
    clips.forEach((c, i) => {
      if (i === 0) return;
      const probe = document.createElement('audio');
      probe.preload = 'metadata';
      probe.onloadedmetadata = () => {
        if (seqRef.current === s && Number.isFinite(probe.duration)) s.durations[i] = probe.duration;
        probe.removeAttribute('src');
      };
      probe.src = c.url;
    });
    a.onloadedmetadata = () => { if (Number.isFinite(a.duration)) s.durations[s.index] = a.duration; };
    a.onended = () => {
      if (seqRef.current !== s) return;
      if (s.index < s.clips.length - 1) loadClip(s.index + 1, 0, true);
      else { setPlayingId(cur => (cur === id ? null : cur)); setPaused(true); }
    };
    a.onerror = () => {
      if (seqRef.current !== s) return;
      setPlayingId(cur => (cur === id ? null : cur));
      setPaused(true);
      setError(NETWORK_MESSAGE);
    };
    if (outputRef.current && a.setSinkId) await a.setSinkId(outputRef.current).catch(() => undefined);
    if (seqRef.current !== s) return; // something else was started meanwhile
    a.src = clips[0].url;
    try {
      await a.play();
    } catch (e) {
      if (seqRef.current !== s) return;
      const name = (e as { name?: string } | null)?.name;
      if (name === 'AbortError') return;
      setPlayingId(cur => (cur === id ? null : cur));
      setPaused(true);
      // The browser wants a tap first: leave it paused in the deck, ▶ starts it
      if (name !== 'NotAllowedError') setError(NETWORK_MESSAGE);
    }
  }, [release, loadClip]);

  const listOutputs = useCallback(async (): Promise<PflOutput[]> => {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices
      .filter(d => d.kind === 'audiooutput' && d.deviceId && d.deviceId !== 'default' && d.deviceId !== 'communications')
      .map((d, i) => ({ id: d.deviceId, label: d.label || `Output ${i + 1}` }));
  }, []);

  /**
   * Browsers only name the outputs after the user has allowed the microphone once,
   * so this asks for it, lets go of the microphone at once, and then lists them.
   */
  const loadOutputs = useCallback(async () => {
    if (!canChooseOutput) return;
    try {
      let list = await listOutputs();
      if (list.length === 0 || list.every(o => o.label.startsWith('Output '))) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          stream.getTracks().forEach(t => t.stop());
          list = await listOutputs();
        } catch { /* refused: keep whatever names we have */ }
      }
      setOutputs(list);
    } catch {
      setOutputs([]);
    }
  }, [canChooseOutput, listOutputs]);

  const chooseOutput = useCallback((id: string) => {
    setOutputId(id);
    try { localStorage.setItem(OUTPUT_KEY, id); } catch { /* private mode */ }
    const a = audioRef.current;
    if (a?.setSinkId) a.setSinkId(id).catch(() => undefined);
  }, []);

  /** Pause or carry on with the item in the deck (Space in the deck). */
  const playPause = useCallback(async () => {
    const a = audioRef.current;
    if (!a) return;
    const id = a.dataset.pflId ?? null;
    if (a.paused) {
      const atEnd = a.ended || (!!a.duration && a.currentTime >= a.duration - 0.05);
      const s = seqRef.current;
      if (s && s.clips.length > 1 && (atEnd || s.index > 0)) {
        // A run of clips: at the end of one go on to the next, at the very end start over
        if (atEnd) { loadClip(s.index < s.clips.length - 1 ? s.index + 1 : 0, 0, true); return; }
      } else if (atEnd) a.currentTime = 0;
      try { await a.play(); setPaused(false); setPlayingId(id); } catch { /* blocked */ }
    } else {
      a.pause();
      setPaused(true);
      setPlayingId(null);
    }
  }, [loadClip]);

  /** Jump to a point in the item (seconds); keeps playing or paused as it was. */
  const seek = useCallback((seconds: number) => {
    const a = audioRef.current;
    if (!a) return;
    const s = seqRef.current;
    if (s) {
      // Across a run of clips: find the clip that holds this point
      const target = Math.max(0, seconds);
      let start = 0;
      for (let i = 0; i < s.clips.length; i++) {
        const d = s.durations[i];
        const last = i === s.clips.length - 1;
        if (!d || target < start + d || last) {
          const into = target - start;
          loadClip(i, Math.max(0, d ? Math.min(d - 0.01, into) : into), !a.paused);
          return;
        }
        start += d;
      }
      return;
    }
    const d = Number.isFinite(a.duration) ? a.duration : 0;
    a.currentTime = Math.max(0, d ? Math.min(d - 0.01, seconds) : seconds);
  }, [loadClip]);

  /** Where the deck is: seconds in and the whole length (across a run of clips too). */
  const position = useCallback((): { time: number; duration: number } | null => {
    const a = audioRef.current;
    if (!a) return null;
    const s = seqRef.current;
    const own = Number.isFinite(a.duration) && a.duration > 0 ? a.duration : 0;
    if (!s) return { time: a.currentTime, duration: own };
    if (own) s.durations[s.index] = own;
    let before = 0;
    for (let i = 0; i < s.index; i++) before += s.durations[i];
    const total = s.durations.reduce((n, d) => n + d, 0);
    const time = before + a.currentTime;
    return { time, duration: Math.max(total, time) };
  }, []);

  /** Where each clip of a run starts (null while a length before it is not known yet). */
  const clipStarts = useCallback((): (number | null)[] => {
    const s = seqRef.current;
    if (!s) return [];
    let at: number | null = 0;
    return s.clips.map((_, i) => {
      const here = at;
      at = at !== null && s.durations[i] ? at + s.durations[i] : null;
      return here;
    });
  }, []);

  /** Load the item in the deck again after it could not be loaded. */
  const retry = useCallback(() => {
    const s = seqRef.current;
    const id = audioRef.current?.dataset.pflId ?? loadedId;
    if (s) { void playClips(s.id, s.label, s.clips); return; }
    if (!id) return;
    stop();
    void toggle(id);
  }, [loadedId, playClips, stop, toggle]);

  return { playingId, loadedId, paused, url, error, seq, clipIndex, playPause, seek, position, clipStarts, toggle, playClips, retry, stop, canChooseOutput, outputs, outputId, loadOutputs, chooseOutput };
}

export type Pfl = ReturnType<typeof usePfl>;
