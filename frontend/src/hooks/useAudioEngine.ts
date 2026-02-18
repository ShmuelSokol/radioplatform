import { useState, useEffect, useRef, useCallback } from 'react';
import { SynthEngine } from '../audio/synthEngine';
import type { AssetInfo } from '../types';

const VOLUME_KEY = 'radio_volume';
const MUTE_KEY = 'radio_muted';

function loadVolume(): number {
  try {
    const v = localStorage.getItem(VOLUME_KEY);
    return v !== null ? Math.max(0, Math.min(1, parseFloat(v))) : 0.7;
  } catch { return 0.7; }
}

function loadMuted(): boolean {
  try { return localStorage.getItem(MUTE_KEY) === 'true'; }
  catch { return false; }
}

export interface AudioEngineState {
  volume: number;
  setVolume: (v: number) => void;
  muted: boolean;
  toggleMute: () => void;
  vuLevels: [number, number];
  audioReady: boolean;
  initAudio: () => Promise<void>;
}

export function useAudioEngine(
  nowPlayingAsset: AssetInfo | null,
  elapsedSeconds: number,
  isPlaying: boolean,
): AudioEngineState {
  const engineRef = useRef<SynthEngine | null>(null);
  const [volume, setVolumeState] = useState(loadVolume);
  const [muted, setMuted] = useState(loadMuted);
  const [vuLevels, setVuLevels] = useState<[number, number]>([0, 0]);
  const [audioReady, setAudioReady] = useState(false);
  const lastAssetId = useRef<string | null>(null);
  const rafRef = useRef<number>(0);

  // ── Init ──────────────────────────────────────────────────────

  const initAudio = useCallback(async () => {
    if (engineRef.current?.ready) {
      setAudioReady(true);
      return;
    }
    const engine = new SynthEngine();
    await engine.init();
    engine.setVolume(loadVolume());
    engine.setMuted(loadMuted());
    engineRef.current = engine;
    setAudioReady(true);
  }, []);

  // ── Cleanup on unmount ────────────────────────────────────────

  useEffect(() => {
    return () => {
      cancelAnimationFrame(rafRef.current);
      engineRef.current?.destroy();
      engineRef.current = null;
    };
  }, []);

  // ── Track changes → play/stop ─────────────────────────────────

  useEffect(() => {
    const engine = engineRef.current;
    if (!engine?.ready) return;

    const assetId = nowPlayingAsset?.id ?? null;

    if (!isPlaying || !nowPlayingAsset) {
      if (lastAssetId.current !== null) {
        engine.stop();
        lastAssetId.current = null;
      }
      return;
    }

    if (assetId !== lastAssetId.current) {
      engine.playTrack(nowPlayingAsset, elapsedSeconds);
      lastAssetId.current = assetId;
    }
  }, [nowPlayingAsset?.id, isPlaying, audioReady]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Volume sync ───────────────────────────────────────────────

  const setVolume = useCallback((v: number) => {
    const clamped = Math.max(0, Math.min(1, v));
    setVolumeState(clamped);
    engineRef.current?.setVolume(clamped);
    try { localStorage.setItem(VOLUME_KEY, String(clamped)); } catch {}
  }, []);

  const toggleMute = useCallback(() => {
    setMuted(prev => {
      const next = !prev;
      engineRef.current?.setMuted(next);
      try { localStorage.setItem(MUTE_KEY, String(next)); } catch {}
      return next;
    });
  }, []);

  // ── VU meter polling (~30fps) ─────────────────────────────────

  useEffect(() => {
    const poll = () => {
      const engine = engineRef.current;
      if (engine?.ready) {
        setVuLevels(engine.getAnalyserLevels());
      }
      rafRef.current = requestAnimationFrame(poll);
    };
    rafRef.current = requestAnimationFrame(poll);
    return () => cancelAnimationFrame(rafRef.current);
  }, [audioReady]);

  return { volume, setVolume, muted, toggleMute, vuLevels, audioReady, initAudio };
}
