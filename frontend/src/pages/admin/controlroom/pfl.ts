import { useCallback, useEffect, useRef, useState } from 'react';
import { getAssetAudioUrl } from '../../../api/assets';

/**
 * PFL (pre-fade listen): hear an item in this browser only, never on the station.
 * One player for the whole control room, so starting a second item stops the first.
 * Where the browser allows it (Chrome, Edge, new Safari on a Mac) the sound can be
 * sent to a second output such as the headphones while the main output carries air.
 */

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

  const canChooseOutput = typeof HTMLMediaElement !== 'undefined'
    && 'setSinkId' in HTMLMediaElement.prototype
    && !!navigator.mediaDevices?.enumerateDevices;

  const stop = useCallback(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    setPlayingId(null);
    setLoadedId(null);
    setPaused(false);
    setUrl(null);
    setError(null);
  }, []);
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
    audioRef.current?.pause();
    audioRef.current = null;
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
  }, []);

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
      if (a.ended || (a.duration && a.currentTime >= a.duration - 0.05)) a.currentTime = 0;
      try { await a.play(); setPaused(false); setPlayingId(id); } catch { /* blocked */ }
    } else {
      a.pause();
      setPaused(true);
      setPlayingId(null);
    }
  }, []);

  /** Jump to a point in the item (seconds); keeps playing or paused as it was. */
  const seek = useCallback((seconds: number) => {
    const a = audioRef.current;
    if (!a) return;
    const d = Number.isFinite(a.duration) ? a.duration : 0;
    a.currentTime = Math.max(0, d ? Math.min(d - 0.01, seconds) : seconds);
  }, []);

  /** The pre-listen audio element, for the deck to read the time from. */
  const getAudio = useCallback(() => audioRef.current, []);

  return { playingId, loadedId, paused, url, error, playPause, seek, getAudio, toggle, stop, canChooseOutput, outputs, outputId, loadOutputs, chooseOutput };
}

export type Pfl = ReturnType<typeof usePfl>;
