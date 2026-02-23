import { useState, useEffect, useRef, useCallback } from 'react';
import { getAssetAudioUrl } from '../api/assets';
import type { AssetInfo } from '../types';

const VOLUME_KEY = 'radio_volume';
const MUTE_KEY = 'radio_muted';
const SYNC_INTERVAL = 5000; // Sync audio position every 5 seconds
const SYNC_THRESHOLD = 2; // Only correct if drift > 2 seconds

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
  nextPreemptAt: string | null = null,
  nextPreemptAssetId: string | null = null,
  preemptFadeMs: number = 2000,
): AudioEngineState {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const analyserLRef = useRef<AnalyserNode | null>(null);
  const analyserRRef = useRef<AnalyserNode | null>(null);
  const analyserDataLRef = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const analyserDataRRef = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const gainRef = useRef<GainNode | null>(null);
  const [volume, setVolumeState] = useState(loadVolume);
  const [muted, setMuted] = useState(loadMuted);
  const [vuLevels, setVuLevels] = useState<[number, number]>([0, 0]);
  const [audioReady, setAudioReady] = useState(false);
  const lastAssetId = useRef<string | null>(null);
  const rafRef = useRef<number>(0);
  const syncTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const elapsedRef = useRef(elapsedSeconds);
  const volumeRef = useRef(volume);
  const mutedRef = useRef(muted);
  const fadingRef = useRef(false);

  // Keep refs in sync
  useEffect(() => { elapsedRef.current = elapsedSeconds; }, [elapsedSeconds]);
  useEffect(() => { volumeRef.current = volume; }, [volume]);
  useEffect(() => { mutedRef.current = muted; }, [muted]);

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

    // Split stereo into L/R channels for accurate VU metering
    const splitter = ctx.createChannelSplitter(2);
    const analyserL = ctx.createAnalyser();
    analyserL.fftSize = 256;
    const analyserR = ctx.createAnalyser();
    analyserR.fftSize = 256;
    analyserLRef.current = analyserL;
    analyserRRef.current = analyserR;
    analyserDataLRef.current = new Uint8Array(analyserL.frequencyBinCount) as Uint8Array<ArrayBuffer>;
    analyserDataRRef.current = new Uint8Array(analyserR.frequencyBinCount) as Uint8Array<ArrayBuffer>;

    const gain = ctx.createGain();
    gain.gain.value = loadMuted() ? 0 : loadVolume();
    gainRef.current = gain;

    source.connect(splitter);
    splitter.connect(analyserL, 0);
    splitter.connect(analyserR, 1);
    source.connect(gain);
    gain.connect(ctx.destination);

    setAudioReady(true);
  }, []);

  // ── Auto-init when playback is active ───────────────────────────
  // If the user returns to the page while playback is running, auto-init audio
  useEffect(() => {
    if (isPlaying && !audioReady) {
      initAudio().catch(() => {});
    }
  }, [isPlaying, audioReady, initAudio]);

  // ── Cleanup on unmount ────────────────────────────────────────

  useEffect(() => {
    return () => {
      cancelAnimationFrame(rafRef.current);
      if (syncTimerRef.current) clearInterval(syncTimerRef.current);
      audioRef.current?.pause();
      audioRef.current = null;
      ctxRef.current?.close().catch(() => {});
      ctxRef.current = null;
      analyserLRef.current = null;
      analyserRRef.current = null;
      gainRef.current = null;
      setAudioReady(false);
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
      // If we were fading, restore gain for the new track
      if (fadingRef.current) {
        fadingRef.current = false;
        const gain = gainRef.current;
        const ctx = ctxRef.current;
        if (gain && ctx) {
          gain.gain.cancelScheduledValues(ctx.currentTime);
          gain.gain.setValueAtTime(mutedRef.current ? 0 : volumeRef.current, ctx.currentTime);
        }
      }
      lastAssetId.current = assetId;
      if (assetId) {
        getAssetAudioUrl(assetId).then((url) => {
          audio.src = url;
          audio.currentTime = Math.max(0, elapsedRef.current);
          audio.play().catch(() => {});
        }).catch(() => {});
      }
    }
  }, [nowPlayingAsset?.id, isPlaying, audioReady]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Preempt fade-out: schedule precise fade before hourly announcements ──

  useEffect(() => {
    if (!nextPreemptAt || !nextPreemptAssetId || !isPlaying || !audioReady) return;
    if (preemptFadeMs <= 0) return;

    const preemptTime = new Date(nextPreemptAt).getTime();
    const fadeStartTime = preemptTime - preemptFadeMs;
    const msUntilFade = fadeStartTime - Date.now();

    // Skip if too far away (>5 min) or already past
    if (msUntilFade > 300000) return;
    // If fade start already passed but preempt hasn't, do an immediate short fade
    const actualDelay = Math.max(0, msUntilFade);
    const remainingMs = preemptTime - Date.now();
    const actualFadeDuration = msUntilFade < 0
      ? Math.max(500, remainingMs) // compress fade to remaining time (min 500ms)
      : preemptFadeMs;
    // If preempt has already passed, skip the fade entirely
    if (remainingMs <= 0) return;

    // Schedule fade-out
    const fadeTimer = setTimeout(() => {
      const gain = gainRef.current;
      const ctx = ctxRef.current;
      if (!gain || !ctx || mutedRef.current) return;

      fadingRef.current = true;
      gain.gain.cancelScheduledValues(ctx.currentTime);
      gain.gain.setValueAtTime(gain.gain.value, ctx.currentTime);
      gain.gain.linearRampToValueAtTime(0, ctx.currentTime + actualFadeDuration / 1000);
    }, actualDelay);

    // Schedule preempt audio switch (after fade completes)
    const switchDelay = actualDelay + actualFadeDuration;
    const preemptAssetId = nextPreemptAssetId; // capture for closure
    const switchTimer = setTimeout(() => {
      const audio = audioRef.current;
      const gain = gainRef.current;
      const ctx = ctxRef.current;
      if (!audio || !gain || !ctx) return;

      // Switch to preempt audio
      lastAssetId.current = preemptAssetId;
      getAssetAudioUrl(preemptAssetId).then((url) => {
        audio.src = url;
        audio.currentTime = 0;
        // Restore gain for the announcement
        gain.gain.cancelScheduledValues(ctx.currentTime);
        gain.gain.setValueAtTime(0, ctx.currentTime);
        gain.gain.linearRampToValueAtTime(
          mutedRef.current ? 0 : volumeRef.current,
          ctx.currentTime + 0.05,
        );
        fadingRef.current = false;
        audio.play().catch(() => {});
      }).catch(() => {});
    }, switchDelay);

    return () => {
      clearTimeout(fadeTimer);
      clearTimeout(switchTimer);
    };
  }, [nextPreemptAt, nextPreemptAssetId, preemptFadeMs, isPlaying, audioReady]);

  // ── Periodic time sync ──────────────────────────────────────────
  // Correct audio.currentTime if it drifts more than SYNC_THRESHOLD from server time

  useEffect(() => {
    if (syncTimerRef.current) clearInterval(syncTimerRef.current);

    if (!audioReady || !isPlaying) return;

    syncTimerRef.current = setInterval(() => {
      const audio = audioRef.current;
      if (!audio || audio.paused || !isFinite(audio.currentTime)) return;

      const serverElapsed = elapsedRef.current;
      const drift = Math.abs(audio.currentTime - serverElapsed);

      if (drift > SYNC_THRESHOLD && serverElapsed > 0) {
        audio.currentTime = serverElapsed;
      }
    }, SYNC_INTERVAL);

    return () => {
      if (syncTimerRef.current) clearInterval(syncTimerRef.current);
    };
  }, [audioReady, isPlaying]);

  // ── Volume sync ───────────────────────────────────────────────

  const setVolume = useCallback((v: number) => {
    const clamped = Math.max(0, Math.min(1, v));
    setVolumeState(clamped);
    const gain = gainRef.current;
    const ctx = ctxRef.current;
    if (gain && ctx && !fadingRef.current) {
      gain.gain.setTargetAtTime(clamped, ctx.currentTime, 0.02);
    }
    try { localStorage.setItem(VOLUME_KEY, String(clamped)); } catch {}
  }, []);

  const toggleMute = useCallback(() => {
    setMuted(prev => {
      const next = !prev;
      const gain = gainRef.current;
      const ctx = ctxRef.current;
      if (gain && ctx && !fadingRef.current) {
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
      const aL = analyserLRef.current;
      const aR = analyserRRef.current;
      const dL = analyserDataLRef.current;
      const dR = analyserDataRRef.current;
      if (aL && aR && dL && dR) {
        aL.getByteFrequencyData(dL);
        aR.getByteFrequencyData(dR);
        let sumL = 0, sumR = 0;
        for (let i = 0; i < dL.length; i++) sumL += dL[i];
        for (let i = 0; i < dR.length; i++) sumR += dR[i];
        const l = sumL / (dL.length * 255);
        const r = sumR / (dR.length * 255);
        setVuLevels([Math.min(1, l * 2.5), Math.min(1, r * 2.5)]);
      }
      rafRef.current = requestAnimationFrame(poll);
    };
    rafRef.current = requestAnimationFrame(poll);
    return () => cancelAnimationFrame(rafRef.current);
  }, [audioReady]);

  return { volume, setVolume, muted, toggleMute, vuLevels, audioReady, initAudio };
}
