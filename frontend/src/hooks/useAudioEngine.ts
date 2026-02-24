import { useState, useEffect, useRef, useCallback } from 'react';
import { getAssetAudioUrl } from '../api/assets';
import type { AssetInfo } from '../types';

const VOLUME_KEY = 'radio_volume';
const MUTE_KEY = 'radio_muted';
const SYNC_INTERVAL = 10000;
const SYNC_THRESHOLD = 5;
const TRACK_CHANGE_COOLDOWN = 15000;
const CROSSFADE_DURATION = 3.0; // seconds

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

/** Per-deck state: Audio element + Web Audio nodes */
interface AudioDeck {
  audio: HTMLAudioElement;
  source: MediaElementAudioSourceNode;
  gain: GainNode;
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
  // New crossfade params
  currentCueIn: number = 0,
  _currentCueOut: number = 0,
  currentCrossStart: number = 0,
  currentReplayGainDb: number = 0,
  nextAssetId: string | null = null,
  nextCueIn: number = 0,
  nextReplayGainDb: number = 0,
  // Optional direct audio URL (for public Listen page — no auth needed)
  audioUrlOverride: string | null = null,
  nextAudioUrlOverride: string | null = null,
): AudioEngineState {
  const ctxRef = useRef<AudioContext | null>(null);
  const analyserLRef = useRef<AnalyserNode | null>(null);
  const analyserRRef = useRef<AnalyserNode | null>(null);
  const analyserDataLRef = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const analyserDataRRef = useRef<Uint8Array<ArrayBuffer> | null>(null);
  const mergerRef = useRef<ChannelMergerNode | null>(null);
  const splitterRef = useRef<ChannelSplitterNode | null>(null);

  const deckARef = useRef<AudioDeck | null>(null);
  const deckBRef = useRef<AudioDeck | null>(null);
  const activeDeckRef = useRef<'A' | 'B'>('A');
  const crossfadingRef = useRef(false);

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
  const trackChangeTimeRef = useRef<number>(0);

  // Crossfade tracking
  const crossStartRef = useRef(currentCrossStart);
  const nextAssetIdRef = useRef(nextAssetId);
  const nextCueInRef = useRef(nextCueIn);
  const nextReplayGainDbRef = useRef(nextReplayGainDb);
  const nextAudioUrlRef = useRef(nextAudioUrlOverride);
  const crossfadeTriggeredRef = useRef(false);

  // Keep refs in sync
  useEffect(() => { elapsedRef.current = elapsedSeconds; }, [elapsedSeconds]);
  useEffect(() => { volumeRef.current = volume; }, [volume]);
  useEffect(() => { mutedRef.current = muted; }, [muted]);
  useEffect(() => { crossStartRef.current = currentCrossStart; }, [currentCrossStart]);
  useEffect(() => {
    nextAssetIdRef.current = nextAssetId;
    nextCueInRef.current = nextCueIn;
    nextReplayGainDbRef.current = nextReplayGainDb;
    nextAudioUrlRef.current = nextAudioUrlOverride;
  }, [nextAssetId, nextCueIn, nextReplayGainDb, nextAudioUrlOverride]);

  // Helper: compute gain from replay gain dB
  const replayGainMultiplier = useCallback((db: number) => {
    return Math.pow(10, db / 20);
  }, []);

  // Helper: get deck by label
  const getDeck = useCallback((label: 'A' | 'B'): AudioDeck | null => {
    return label === 'A' ? deckARef.current : deckBRef.current;
  }, []);

  const getActiveDeck = useCallback((): AudioDeck | null => {
    return getDeck(activeDeckRef.current);
  }, [getDeck]);

  const getInactiveDeck = useCallback((): AudioDeck | null => {
    return getDeck(activeDeckRef.current === 'A' ? 'B' : 'A');
  }, [getDeck]);

  // Create a deck
  const createDeck = useCallback((ctx: AudioContext): AudioDeck => {
    const audio = new Audio();
    audio.crossOrigin = 'anonymous';
    audio.preload = 'auto';
    const source = ctx.createMediaElementSource(audio);
    const gain = ctx.createGain();
    gain.gain.value = 0; // start silent
    source.connect(gain);
    return { audio, source, gain };
  }, []);

  // ── Init audio context + dual decks ──
  const initAudio = useCallback(async () => {
    if (ctxRef.current && deckARef.current && deckBRef.current) {
      if (ctxRef.current.state === 'suspended') await ctxRef.current.resume();
      setAudioReady(true);
      return;
    }

    const ctx = new AudioContext();
    if (ctx.state === 'suspended') await ctx.resume();
    ctxRef.current = ctx;

    // Create merger → splitter → analysers → destination
    const merger = ctx.createChannelMerger(2);
    const splitter = ctx.createChannelSplitter(2);
    const analyserL = ctx.createAnalyser();
    analyserL.fftSize = 256;
    const analyserR = ctx.createAnalyser();
    analyserR.fftSize = 256;

    merger.connect(splitter);
    splitter.connect(analyserL, 0);
    splitter.connect(analyserR, 1);
    merger.connect(ctx.destination);

    mergerRef.current = merger;
    splitterRef.current = splitter;
    analyserLRef.current = analyserL;
    analyserRRef.current = analyserR;
    analyserDataLRef.current = new Uint8Array(analyserL.frequencyBinCount) as Uint8Array<ArrayBuffer>;
    analyserDataRRef.current = new Uint8Array(analyserR.frequencyBinCount) as Uint8Array<ArrayBuffer>;

    // Create two decks
    const deckA = createDeck(ctx);
    const deckB = createDeck(ctx);
    deckA.gain.connect(merger);
    deckB.gain.connect(merger);
    deckARef.current = deckA;
    deckBRef.current = deckB;
    activeDeckRef.current = 'A';

    setAudioReady(true);
  }, [createDeck]);

  // ── Auto-init when playback is active ──
  useEffect(() => {
    if (isPlaying && !audioReady) {
      initAudio().catch(() => {});
    }
  }, [isPlaying, audioReady, initAudio]);

  // ── Cleanup on unmount ──
  useEffect(() => {
    return () => {
      cancelAnimationFrame(rafRef.current);
      if (syncTimerRef.current) clearInterval(syncTimerRef.current);
      deckARef.current?.audio.pause();
      deckBRef.current?.audio.pause();
      deckARef.current = null;
      deckBRef.current = null;
      ctxRef.current?.close().catch(() => {});
      ctxRef.current = null;
      setAudioReady(false);
    };
  }, []);

  // ── Pre-load next track on inactive deck ──
  useEffect(() => {
    if (!audioReady || !nextAssetId) return;
    const inactiveDeck = getInactiveDeck();
    if (!inactiveDeck) return;

    const loadNext = async () => {
      try {
        let url: string;
        if (nextAudioUrlOverride) {
          url = nextAudioUrlOverride;
        } else {
          url = await getAssetAudioUrl(nextAssetId);
        }
        // Only set src if it's different (avoid reloading same track)
        if (inactiveDeck.audio.src !== url) {
          inactiveDeck.audio.src = url;
          inactiveDeck.audio.load();
        }
      } catch {
        // Preload failure is non-critical
      }
    };
    loadNext();
  }, [nextAssetId, nextAudioUrlOverride, audioReady, getInactiveDeck]);

  // ── Crossfade execution ──
  const executeCrossfade = useCallback(() => {
    const ctx = ctxRef.current;
    const activeDeck = getActiveDeck();
    const inactiveDeck = getInactiveDeck();
    if (!ctx || !activeDeck || !inactiveDeck || crossfadingRef.current) return;

    crossfadingRef.current = true;

    const targetGain = mutedRef.current ? 0 : volumeRef.current * replayGainMultiplier(nextReplayGainDbRef.current);

    // Seek inactive deck to cue-in point and play
    inactiveDeck.audio.currentTime = nextCueInRef.current;
    inactiveDeck.gain.gain.cancelScheduledValues(ctx.currentTime);
    inactiveDeck.gain.gain.setValueAtTime(0, ctx.currentTime);
    inactiveDeck.gain.gain.linearRampToValueAtTime(targetGain, ctx.currentTime + CROSSFADE_DURATION);
    inactiveDeck.audio.play().catch(() => {});

    // Fade out active deck
    activeDeck.gain.gain.cancelScheduledValues(ctx.currentTime);
    activeDeck.gain.gain.setValueAtTime(activeDeck.gain.gain.value, ctx.currentTime);
    activeDeck.gain.gain.linearRampToValueAtTime(0, ctx.currentTime + CROSSFADE_DURATION);

    // After crossfade completes, swap decks
    setTimeout(() => {
      activeDeck.audio.pause();
      activeDeckRef.current = activeDeckRef.current === 'A' ? 'B' : 'A';
      crossfadingRef.current = false;
      crossfadeTriggeredRef.current = false;
      trackChangeTimeRef.current = Date.now();
    }, CROSSFADE_DURATION * 1000 + 100);
  }, [getActiveDeck, getInactiveDeck, replayGainMultiplier]);

  // ── Track changes → play on active deck ──
  useEffect(() => {
    if (!audioReady) return;
    const activeDeck = getActiveDeck();
    if (!activeDeck) return;

    const assetId = nowPlayingAsset?.id ?? null;

    if (!isPlaying || !nowPlayingAsset) {
      if (lastAssetId.current !== null) {
        deckARef.current?.audio.pause();
        deckBRef.current?.audio.pause();
        lastAssetId.current = null;
      }
      return;
    }

    if (assetId !== lastAssetId.current) {
      // If we were crossfading into this track, it's already playing on the new active deck
      if (crossfadingRef.current) {
        lastAssetId.current = assetId;
        return;
      }

      crossfadeTriggeredRef.current = false;
      lastAssetId.current = assetId;
      trackChangeTimeRef.current = Date.now();

      const ctx = ctxRef.current;
      if (!ctx) return;

      // Apply replay gain
      const targetGain = mutedRef.current ? 0 : volumeRef.current * replayGainMultiplier(currentReplayGainDb);

      const loadAndPlay = async () => {
        try {
          let url: string;
          if (audioUrlOverride) {
            url = audioUrlOverride;
          } else if (assetId) {
            url = await getAssetAudioUrl(assetId);
          } else {
            return;
          }

          activeDeck.audio.src = url;
          activeDeck.audio.currentTime = Math.max(currentCueIn, elapsedRef.current);
          activeDeck.gain.gain.cancelScheduledValues(ctx.currentTime);
          activeDeck.gain.gain.setValueAtTime(targetGain, ctx.currentTime);
          activeDeck.audio.play().catch(() => {});
        } catch {
          // Load failure
        }
      };
      loadAndPlay();
    }
  }, [nowPlayingAsset?.id, isPlaying, audioReady, currentCueIn, currentReplayGainDb, audioUrlOverride, getActiveDeck, replayGainMultiplier]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Preempt fade-out: schedule precise fade before hourly announcements ──
  useEffect(() => {
    if (!nextPreemptAt || !nextPreemptAssetId || !isPlaying || !audioReady) return;
    if (preemptFadeMs <= 0) return;

    const preemptTime = new Date(nextPreemptAt).getTime();
    const fadeStartTime = preemptTime - preemptFadeMs;
    const msUntilFade = fadeStartTime - Date.now();

    if (msUntilFade > 300000) return;
    const actualDelay = Math.max(0, msUntilFade);
    const remainingMs = preemptTime - Date.now();
    const actualFadeDuration = msUntilFade < 0
      ? Math.max(500, remainingMs)
      : preemptFadeMs;
    if (remainingMs <= 0) return;

    const fadeTimer = setTimeout(() => {
      const activeDeck = getActiveDeck();
      const ctx = ctxRef.current;
      if (!activeDeck || !ctx || mutedRef.current) return;

      activeDeck.gain.gain.cancelScheduledValues(ctx.currentTime);
      activeDeck.gain.gain.setValueAtTime(activeDeck.gain.gain.value, ctx.currentTime);
      activeDeck.gain.gain.linearRampToValueAtTime(0, ctx.currentTime + actualFadeDuration / 1000);
    }, actualDelay);

    return () => clearTimeout(fadeTimer);
  }, [nextPreemptAt, nextPreemptAssetId, preemptFadeMs, isPlaying, audioReady, getActiveDeck]);

  // ── Periodic time sync ──
  useEffect(() => {
    if (syncTimerRef.current) clearInterval(syncTimerRef.current);
    if (!audioReady || !isPlaying) return;

    syncTimerRef.current = setInterval(() => {
      const activeDeck = getActiveDeck();
      if (!activeDeck || activeDeck.audio.paused || !isFinite(activeDeck.audio.currentTime)) return;
      if (Date.now() - trackChangeTimeRef.current < TRACK_CHANGE_COOLDOWN) return;

      const serverElapsed = elapsedRef.current;
      if (serverElapsed <= 0) return;

      const drift = serverElapsed - activeDeck.audio.currentTime;
      if (drift > SYNC_THRESHOLD) {
        activeDeck.audio.currentTime = serverElapsed;
      }
    }, SYNC_INTERVAL);

    return () => {
      if (syncTimerRef.current) clearInterval(syncTimerRef.current);
    };
  }, [audioReady, isPlaying, getActiveDeck]);

  // ── Volume sync ──
  const setVolume = useCallback((v: number) => {
    const clamped = Math.max(0, Math.min(1, v));
    setVolumeState(clamped);
    const ctx = ctxRef.current;
    const activeDeck = getActiveDeck();
    if (activeDeck && ctx && !crossfadingRef.current) {
      const rg = replayGainMultiplier(currentReplayGainDb);
      activeDeck.gain.gain.setTargetAtTime(clamped * rg, ctx.currentTime, 0.02);
    }
    try { localStorage.setItem(VOLUME_KEY, String(clamped)); } catch {}
  }, [getActiveDeck, replayGainMultiplier, currentReplayGainDb]);

  const toggleMute = useCallback(() => {
    setMuted(prev => {
      const next = !prev;
      const ctx = ctxRef.current;
      const activeDeck = getActiveDeck();
      if (activeDeck && ctx && !crossfadingRef.current) {
        const rg = replayGainMultiplier(currentReplayGainDb);
        activeDeck.gain.gain.setTargetAtTime(
          next ? 0 : loadVolume() * rg,
          ctx.currentTime, 0.02
        );
      }
      try { localStorage.setItem(MUTE_KEY, String(next)); } catch {}
      return next;
    });
  }, [getActiveDeck, replayGainMultiplier, currentReplayGainDb]);

  // ── VU meter polling (~30fps) + client-driven crossfade check ──
  useEffect(() => {
    const poll = () => {
      // VU metering
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

      // Client-driven crossfade check
      const activeDeck = getActiveDeck();
      if (activeDeck && !crossfadingRef.current && !crossfadeTriggeredRef.current) {
        const ct = activeDeck.audio.currentTime;
        const cs = crossStartRef.current;
        if (cs > 0 && ct >= cs && nextAssetIdRef.current) {
          crossfadeTriggeredRef.current = true;
          executeCrossfade();
        }
      }

      rafRef.current = requestAnimationFrame(poll);
    };
    rafRef.current = requestAnimationFrame(poll);
    return () => cancelAnimationFrame(rafRef.current);
  }, [audioReady, getActiveDeck, executeCrossfade]);

  return { volume, setVolume, muted, toggleMute, vuLevels, audioReady, initAudio };
}
