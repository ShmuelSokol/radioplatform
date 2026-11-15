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

export function usePfl() {
  const audioRef = useRef<SinkAudio | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);
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
  }, []);
  useEffect(() => () => { audioRef.current?.pause(); }, []);

  const toggle = useCallback(async (id: string) => {
    const wasPlaying = audioRef.current?.dataset.pflId === id;
    audioRef.current?.pause();
    audioRef.current = null;
    if (wasPlaying) { setPlayingId(null); return; }
    setPlayingId(id);
    const clear = () => setPlayingId(cur => (cur === id ? null : cur));
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
      a.onended = clear;
      a.onerror = clear;
      await a.play();
    } catch {
      clear();
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

  return { playingId, toggle, stop, canChooseOutput, outputs, outputId, loadOutputs, chooseOutput };
}

export type Pfl = ReturnType<typeof usePfl>;
