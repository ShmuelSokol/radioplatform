import { useState, useEffect, useRef, useCallback } from 'react';
import { getAssetAudioUrl } from '../api/assets';
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
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const analyserDataRef = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const [volume, setVolumeState] = useState(loadVolume);
  const [muted, setMuted] = useState(loadMuted);
  const [vuLevels, setVuLevels] = useState<[number, number]>([0, 0]);
  const [audioReady, setAudioReady] = useState(false);
  const lastAssetId = useRef<string | null>(null);
  const rafRef = useRef<number>(0);

  // ── Init audio context + element ──────────────────────────────

  const initAudio = useCallback(async () => {
    if (ctxRef.current && audioRef.current) {
      if (ctxRef.current.state === 'suspended') await ctxRef.current.resume();
      setAudioReady(true);
      return;
    }

    const audio = new Audio();
    audio.crossOrigin = 'anonymous';
    audio.preload = 'auto';
    audioRef.current = audio;

    const ctx = new AudioContext();
    if (ctx.state === 'suspended') await ctx.resume();
    ctxRef.current = ctx;

    const source = ctx.createMediaElementSource(audio);

    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    analyserRef.current = analyser;
    analyserDataRef.current = new Uint8Array(analyser.frequencyBinCount) as Uint8Array<ArrayBuffer>;

    const gain = ctx.createGain();
    gain.gain.value = loadMuted() ? 0 : loadVolume();
    gainRef.current = gain;

    source.connect(analyser);
    analyser.connect(gain);
    gain.connect(ctx.destination);

    setAudioReady(true);
  }, []);

  // ── Cleanup on unmount ────────────────────────────────────────

  useEffect(() => {
    return () => {
      cancelAnimationFrame(rafRef.current);
      audioRef.current?.pause();
      audioRef.current = null;
      ctxRef.current?.close().catch(() => {});
      ctxRef.current = null;
    };
  }, []);

  // ── Track changes → play real audio ───────────────────────────

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !audioReady) return;

    const assetId = nowPlayingAsset?.id ?? null;

    if (!isPlaying || !nowPlayingAsset) {
      if (lastAssetId.current !== null) {
        audio.pause();
        lastAssetId.current = null;
      }
      return;
    }

    if (assetId !== lastAssetId.current) {
      lastAssetId.current = assetId;
      if (assetId) {
        getAssetAudioUrl(assetId).then((url) => {
          audio.src = url;
          audio.currentTime = Math.max(0, elapsedSeconds);
          audio.play().catch(() => {});
        }).catch(() => {});
      }
    }
  }, [nowPlayingAsset?.id, isPlaying, audioReady]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Volume sync ───────────────────────────────────────────────

  const setVolume = useCallback((v: number) => {
    const clamped = Math.max(0, Math.min(1, v));
    setVolumeState(clamped);
    const gain = gainRef.current;
    const ctx = ctxRef.current;
    if (gain && ctx) {
      gain.gain.setTargetAtTime(clamped, ctx.currentTime, 0.02);
    }
    try { localStorage.setItem(VOLUME_KEY, String(clamped)); } catch {}
  }, []);

  const toggleMute = useCallback(() => {
    setMuted(prev => {
      const next = !prev;
      const gain = gainRef.current;
      const ctx = ctxRef.current;
      if (gain && ctx) {
        gain.gain.setTargetAtTime(
          next ? 0 : loadVolume(),
          ctx.currentTime, 0.02
        );
      }
      try { localStorage.setItem(MUTE_KEY, String(next)); } catch {}
      return next;
    });
  }, []);

  // ── VU meter polling (~30fps) ─────────────────────────────────

  useEffect(() => {
    const poll = () => {
      const analyser = analyserRef.current;
      const data = analyserDataRef.current;
      if (analyser && data) {
        analyser.getByteFrequencyData(data);
        const len = data.length;
        const half = len >> 1;
        let sumL = 0, sumR = 0;
        for (let i = 0; i < half; i++) sumL += data[i];
        for (let i = half; i < len; i++) sumR += data[i];
        const l = sumL / (half * 255);
        const r = sumR / (half * 255);
        setVuLevels([Math.min(1, l * 2.5), Math.min(1, r * 2.5)]);
      }
      rafRef.current = requestAnimationFrame(poll);
    };
    rafRef.current = requestAnimationFrame(poll);
    return () => cancelAnimationFrame(rafRef.current);
  }, [audioReady]);

  return { volume, setVolume, muted, toggleMute, vuLevels, audioReady, initAudio };
}
