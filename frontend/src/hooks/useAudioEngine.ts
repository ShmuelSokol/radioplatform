import { useState, useEffect, useRef, useCallback } from 'react';
import { getAssetAudioUrl } from '../api/assets';
import { holdUpdates } from '../lib/appUpdate';
import type { AssetInfo } from '../types';

const VOLUME_KEY = 'radio_volume';
const MUTE_KEY = 'radio_muted';
const SYNC_INTERVAL = 10000;
const SYNC_THRESHOLD = 5;
const TRACK_CHANGE_COOLDOWN = 15000;
const CROSSFADE_DURATION = 3.0; // seconds
// Manual track change (skip / play-now): quick fade instead of a hard cut
const SKIP_FADE_OUT = 0.6; // seconds
const SKIP_FADE_IN = 0.15; // seconds
// On a cold load, keep the current track on air until the new one can play (capped)
const COLD_LOAD_MAX_WAIT_MS = 4000;
// The player may start a crossfade at most this long before the server reaches the
// crossfade point. Without this bound the player can run ahead of the server a little
// more with every song.
const MAX_LEAD_SECONDS = 1.0;
// A track change that arrives this close to the natural end of the song on air is the
// normal song-to-song change, so it gets the full crossfade rather than the quick skip fade.
const NATURAL_END_WINDOW_SECONDS = 4.0;
// Crossfade trigger check. A timer, not requestAnimationFrame: rAF stops in background tabs.
const CROSSFADE_CHECK_MS = 100;
// A deck this close to the server's position for the current play is playing that play
const REPLAY_TOLERANCE_SECONDS = 4.0;

type DeckLabel = 'A' | 'B';
const otherDeck = (label: DeckLabel): DeckLabel => (label === 'A' ? 'B' : 'A');

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
  // Server start time of the current track (ISO). Used to position a newly started
  // track; `elapsedSeconds` still belongs to the previous track on the render where
  // the track changes.
  currentStartedAt: string | null = null,
  // How long the outgoing item fades on an operator's Next (hard 0 / fade 0.5 / soft 3),
  // sent with the message that announces the change. null = the default quick fade.
  changeFadeSeconds: number | null = null,
): AudioEngineState {
  const changeFadeRef = useRef<number | null>(changeFadeSeconds);
  changeFadeRef.current = changeFadeSeconds;
  const ctxRef = useRef<AudioContext | null>(null);
  const [ctxRunning, setCtxRunning] = useState(false);
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
  // Server start time of the play we last handled, and the asset whose change is still
  // in flight. Together they tell "the same item was queued again" (a new play of the
  // same asset) apart from a late-arriving start time for a change already handled.
  const lastStartedAtRef = useRef<string | null>(null);
  const pendingAssetRef = useRef<string | null>(null);
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
  const crossfadeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // cross_start of the track the server last announced (the one a track change replaces)
  const onAirCrossStartRef = useRef(0);

  // Which asset each deck currently holds (loaded or loading)
  const deckAssetRef = useRef<Record<DeckLabel, string | null>>({ A: null, B: null });
  // While a skip fade-out is running, the fading deck must not be re-loaded (performance.now() ms)
  const fadeBusyUntilRef = useRef(0);
  // Bumped on every track change so stale async work can bail out
  const changeTokenRef = useRef(0);
  // Bumped after the decks swap so the pre-loader re-runs for the new idle deck
  const [swapTick, setSwapTick] = useState(0);
  // Assigned during render so effects in the same pass see the new track's start time
  const startedAtRef = useRef(currentStartedAt);
  startedAtRef.current = currentStartedAt;

  // Where in the track playback should be right now
  const livePosition = useCallback((cueIn: number) => {
    const startedAt = startedAtRef.current;
    if (startedAt) {
      const startMs = new Date(startedAt).getTime();
      if (isFinite(startMs)) return Math.max(cueIn, (Date.now() - startMs) / 1000);
    }
    return Math.max(cueIn, elapsedRef.current);
  }, []);

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
    ctx.onstatechange = () => setCtxRunning(ctx.state === 'running');
    if (ctx.state === 'suspended') await ctx.resume();
    ctxRef.current = ctx;
    setCtxRunning(ctx.state === 'running');

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

  // ── A release never reloads this tab while the player is on ──
  // "On" means sound can actually come out: the audio context is running. On
  // the control room the station is nearly always playing, so holding on
  // isPlaying alone kept operator tabs on an old release forever, even when
  // the operator never switched audio on (the context stays suspended until a
  // click). Listener pages hold separately from the moment Listen is pressed.
  useEffect(() => {
    if (!isPlaying || !ctxRunning) return;
    return holdUpdates();
  }, [isPlaying, ctxRunning]);

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
      if (crossfadeTimerRef.current) clearTimeout(crossfadeTimerRef.current);
      changeTokenRef.current++;
      deckAssetRef.current = { A: null, B: null };
      deckARef.current?.audio.pause();
      deckBRef.current?.audio.pause();
      deckARef.current = null;
      deckBRef.current = null;
      ctxRef.current?.close().catch(() => {});
      ctxRef.current = null;
      setAudioReady(false);
    };
  }, []);

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
    crossfadeTimerRef.current = setTimeout(() => {
      crossfadeTimerRef.current = null;
      activeDeck.audio.pause();
      activeDeckRef.current = otherDeck(activeDeckRef.current);
      crossfadingRef.current = false;
      crossfadeTriggeredRef.current = false;
      trackChangeTimeRef.current = Date.now();
      setSwapTick(t => t + 1);
    }, CROSSFADE_DURATION * 1000 + 100);
  }, [getActiveDeck, getInactiveDeck, replayGainMultiplier]);

  // ── Track changes → bring the new track on air ──
  useEffect(() => {
    if (!audioReady) return;

    const assetId = nowPlayingAsset?.id ?? null;

    if (!isPlaying || !nowPlayingAsset) {
      if (lastAssetId.current !== null) {
        changeTokenRef.current++;
        deckARef.current?.audio.pause();
        deckBRef.current?.audio.pause();
        lastAssetId.current = null;
        lastStartedAtRef.current = null;
        pendingAssetRef.current = null;
      }
      return;
    }

    // Same asset as before: nothing to do, unless the server started a NEW play of it
    // (the same item queued twice in a row) and no deck is playing that new play.
    let replay = false;
    if (assetId === lastAssetId.current) {
      if (!currentStartedAt || currentStartedAt === lastStartedAtRef.current) return;
      const prevMs = lastStartedAtRef.current ? new Date(lastStartedAtRef.current).getTime() : NaN;
      const sameInstant = Math.abs(new Date(currentStartedAt).getTime() - prevMs) < 1000;
      lastStartedAtRef.current = currentStartedAt;
      if (sameInstant) return; // same play, start time only formatted differently
      if (pendingAssetRef.current === assetId) return; // its change is still being brought up
      const onAirLabel = crossfadingRef.current ? otherDeck(activeDeckRef.current) : activeDeckRef.current;
      const onAir = getDeck(onAirLabel);
      const inPlace = !!onAir && deckAssetRef.current[onAirLabel] === assetId && !onAir.audio.paused
        && !onAir.audio.ended && Math.abs(onAir.audio.currentTime - livePosition(currentCueIn)) < REPLAY_TOLERANCE_SECONDS;
      // Already crossfaded into the new play (pre-loaded on the idle deck), or the start
      // time only arrived late for a change already handled.
      if (inPlace) return;
      replay = true;
    }
    lastStartedAtRef.current = currentStartedAt;

    const ctx = ctxRef.current;
    if (!ctx) return;

    const outCross = onAirCrossStartRef.current;
    onAirCrossStartRef.current = currentCrossStart;

    let outLabel = activeDeckRef.current;
    let inLabel = otherDeck(outLabel);

    if (crossfadingRef.current) {
      // We are already crossfading into this track — nothing to do
      if (!replay && deckAssetRef.current[inLabel] === assetId) {
        lastAssetId.current = assetId;
        return;
      }
      // A different track was requested mid-crossfade: settle the swap now and carry on
      if (crossfadeTimerRef.current) {
        clearTimeout(crossfadeTimerRef.current);
        crossfadeTimerRef.current = null;
      }
      activeDeckRef.current = inLabel;
      crossfadingRef.current = false;
      outLabel = inLabel;
      inLabel = otherDeck(outLabel);
    }

    const outDeck = getDeck(outLabel);
    const inDeck = getDeck(inLabel);
    if (!outDeck || !inDeck) return;

    // Already on air: we crossfaded into it before the server announced it.
    // Re-loading here would restart the track and cause an audible jump.
    if (!replay && deckAssetRef.current[outLabel] === assetId && !outDeck.audio.paused) {
      lastAssetId.current = assetId;
      return;
    }

    crossfadeTriggeredRef.current = false;
    lastAssetId.current = assetId;
    pendingAssetRef.current = assetId;
    trackChangeTimeRef.current = Date.now();
    const token = ++changeTokenRef.current;
    const cueIn = currentCueIn;
    const replayGain = replayGainMultiplier(currentReplayGainDb);

    // Natural end of the song on air (the server advanced at the crossfade point) or a
    // manual change (Next / play-now)?
    const outTime = outDeck.audio.currentTime;
    const outDur = outDeck.audio.duration;
    const outEnd = outCross > 0 ? outCross : (isFinite(outDur) && outDur > 0 ? outDur - CROSSFADE_DURATION : 0);
    const naturalEnd = !outDeck.audio.paused && outEnd > 0 && outTime >= outEnd - NATURAL_END_WINDOW_SECONDS;
    // An operator's Next carries the fade they chose; it wins even near the end of the item
    // (a short looped jingle is nearly always "near the end").
    const asked = changeFadeRef.current;
    const operatorFade = asked != null && isFinite(asked) ? Math.min(10, Math.max(0.03, asked)) : null;
    const fadeOut = operatorFade ?? (naturalEnd ? CROSSFADE_DURATION : SKIP_FADE_OUT);
    const fadeIn = SKIP_FADE_IN;

    let wentLive = false;
    const goLive = () => {
      if (wentLive || token !== changeTokenRef.current) return;
      wentLive = true;
      pendingAssetRef.current = null;
      // A natural crossfade still settling must not flip the decks back afterwards
      if (crossfadeTimerRef.current) {
        clearTimeout(crossfadeTimerRef.current);
        crossfadeTimerRef.current = null;
      }
      crossfadingRef.current = false;
      crossfadeTriggeredRef.current = false;
      const now = ctx.currentTime;
      const targetGain = mutedRef.current ? 0 : volumeRef.current * replayGain;

      // Quick fade-out of whatever is on air (no hard cut)
      outDeck.gain.gain.cancelScheduledValues(now);
      outDeck.gain.gain.setValueAtTime(outDeck.gain.gain.value, now);
      outDeck.gain.gain.linearRampToValueAtTime(0, now + fadeOut);
      fadeBusyUntilRef.current = performance.now() + fadeOut * 1000;

      // Bring the new track up at its live position
      // A position at or past the end of the new track means the start time we hold still
      // belongs to the previous track (it arrives separately): start from the top instead.
      let startPos = livePosition(cueIn);
      const inDur = inDeck.audio.duration;
      if (isFinite(inDur) && inDur > 0 && startPos > inDur - 1) startPos = cueIn;
      try { inDeck.audio.currentTime = startPos; } catch { /* not seekable yet */ }
      inDeck.gain.gain.cancelScheduledValues(now);
      inDeck.gain.gain.setValueAtTime(0, now);
      inDeck.gain.gain.linearRampToValueAtTime(targetGain, now + fadeIn);
      inDeck.audio.play().catch(() => {});

      activeDeckRef.current = inLabel;
      trackChangeTimeRef.current = Date.now();

      // Once the fade-out is done, stop the old deck and let the pre-loader use it
      setTimeout(() => {
        if (activeDeckRef.current !== outLabel && !crossfadingRef.current) outDeck.audio.pause();
        setSwapTick(t => t + 1);
      }, fadeOut * 1000 + 50);
    };

    const run = async () => {
      try {
        // Already pre-loaded and ready: go live now, without a round trip for its URL
        if (assetId && deckAssetRef.current[inLabel] === assetId && inDeck.audio.readyState >= 2) {
          goLive();
          return;
        }
        let url: string;
        if (audioUrlOverride) {
          url = audioUrlOverride;
        } else if (assetId) {
          url = await getAssetAudioUrl(assetId);
        } else {
          return;
        }
        if (token !== changeTokenRef.current) return;

        const preloaded = deckAssetRef.current[inLabel] === assetId;
        if (preloaded && inDeck.audio.readyState >= 2) {
          goLive();
          return;
        }

        // Cold load on the idle deck. The current track stays on air until the
        // new one can play, so there is no silent gap while it downloads.
        if (!preloaded) {
          deckAssetRef.current[inLabel] = assetId;
          inDeck.gain.gain.cancelScheduledValues(ctx.currentTime);
          inDeck.gain.gain.setValueAtTime(0, ctx.currentTime);
          inDeck.audio.src = url;
          inDeck.audio.load();
        }
        const onReady = () => goLive();
        inDeck.audio.addEventListener('canplay', onReady, { once: true });
        setTimeout(() => {
          inDeck.audio.removeEventListener('canplay', onReady);
          goLive();
        }, COLD_LOAD_MAX_WAIT_MS);
      } catch {
        // Load failure
      }
    };
    run();
  }, [nowPlayingAsset?.id, currentStartedAt, isPlaying, audioReady, currentCueIn, currentReplayGainDb, audioUrlOverride, getDeck, livePosition, replayGainMultiplier]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Pre-load next track on the idle deck ──
  // Declared after the track-change effect so a track change claims its deck first.
  useEffect(() => {
    if (!audioReady || !nextAssetId) return;
    const currentId = nowPlayingAsset?.id ?? null;

    // The idle deck is free for pre-loading only when it is not mid-crossfade or mid-fade-out,
    // not holding the track that is (about to be) on air, and not already loaded.
    const idleDeckFor = (): { label: DeckLabel; deck: AudioDeck } | null => {
      if (crossfadingRef.current) return null;
      if (performance.now() < fadeBusyUntilRef.current) return null; // re-runs via swapTick
      const activeLabel = activeDeckRef.current;
      const label = otherDeck(activeLabel);
      const deck = getDeck(label);
      if (!deck) return null;
      const held = deckAssetRef.current[label];
      if (held === nextAssetId) return null;
      if (currentId && held === currentId) return null;
      // The active deck already holds the next asset: we crossfaded into it before the server
      // announced it. But if the SAME item is queued twice in a row, the idle deck needs
      // its own copy for the second play.
      if (deckAssetRef.current[activeLabel] === nextAssetId && nextAssetId !== currentId) return null;
      return { label, deck };
    };

    if (!idleDeckFor()) return;
    let cancelled = false;

    const loadNext = async () => {
      try {
        const url = nextAudioUrlOverride ?? await getAssetAudioUrl(nextAssetId);
        if (cancelled) return;
        const idle = idleDeckFor();
        if (!idle) return;
        deckAssetRef.current[idle.label] = nextAssetId;
        idle.deck.audio.pause();
        idle.deck.audio.src = url;
        idle.deck.audio.load();
      } catch {
        // Preload failure is non-critical
      }
    };
    loadNext();
    return () => { cancelled = true; };
  }, [nextAssetId, nextAudioUrlOverride, audioReady, swapTick, nowPlayingAsset?.id, getDeck]);

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

      rafRef.current = requestAnimationFrame(poll);
    };
    rafRef.current = requestAnimationFrame(poll);
    return () => cancelAnimationFrame(rafRef.current);
  }, [audioReady]);

  // ── Client-driven crossfade check (timer-based so it keeps running in background tabs) ──
  useEffect(() => {
    if (!audioReady) return;
    const timer = setInterval(() => {
      if (crossfadingRef.current || crossfadeTriggeredRef.current) return;
      const activeLabel = activeDeckRef.current;
      const activeDeck = getDeck(activeLabel);
      const nextId = nextAssetIdRef.current;
      if (!activeDeck || activeDeck.audio.paused || !nextId) return;
      // The crossfade point belongs to the track the server says is on air. While a
      // change is pending (Next was pressed and the new track is not up yet) the active
      // deck still holds the old track, so that point must not be applied to it.
      if (deckAssetRef.current[activeLabel] !== lastAssetId.current) return;
      // Only crossfade when the idle deck really holds the next track
      if (deckAssetRef.current[otherDeck(activeLabel)] !== nextId) return;

      const ct = activeDeck.audio.currentTime;
      const dur = activeDeck.audio.duration;
      const cs = crossStartRef.current;
      const atCrossStart = cs > 0 && ct >= cs;
      // No cross_start set — crossfade 3s before end to avoid a gap
      const nearEnd = cs <= 0 && isFinite(dur) && dur > 0 && ct >= dur - CROSSFADE_DURATION;
      if (atCrossStart || nearEnd) {
        // Do not run ahead of the server: wait until it is (nearly) at the crossfade
        // point too. It announces the next track there, which brings it on air anyway.
        const startedAt = startedAtRef.current;
        const startMs = startedAt ? new Date(startedAt).getTime() : NaN;
        if (isFinite(startMs)) {
          const serverPos = (Date.now() - startMs) / 1000;
          const crossPoint = cs > 0 ? cs : dur - CROSSFADE_DURATION;
          if (serverPos < crossPoint - MAX_LEAD_SECONDS) return;
        }
        crossfadeTriggeredRef.current = true;
        executeCrossfade();
      }
    }, CROSSFADE_CHECK_MS);
    return () => clearInterval(timer);
  }, [audioReady, getDeck, executeCrossfade]);

  // ── Mix over: a jingle played on top of the item on air, which carries on ──
  // Several pages can hold a connection to the same station; each message plays once.
  useEffect(() => {
    const onMixOver = (event: Event) => {
      const data = (event as CustomEvent).detail as {
        url?: string; replay_gain_db?: number | null; _taken?: boolean;
      };
      const ctx = ctxRef.current;
      const out = mergerRef.current;
      if (!data?.url || data._taken || !ctx || !out || ctx.state !== 'running') return;
      data._taken = true;
      fetch(data.url)
        .then((res) => res.arrayBuffer())
        .then((bytes) => ctx.decodeAudioData(bytes))
        .then((buffer) => {
          const source = ctx.createBufferSource();
          source.buffer = buffer;
          const gain = ctx.createGain();
          gain.gain.value = mutedRef.current
            ? 0
            : volumeRef.current * replayGainMultiplier(data.replay_gain_db ?? 0);
          source.connect(gain);
          gain.connect(out);
          source.onended = () => gain.disconnect();
          source.start();
        })
        .catch(() => {
          // The jingle could not be loaded; the item on air is not affected
        });
    };
    window.addEventListener('kbr-mix-over', onMixOver);
    return () => window.removeEventListener('kbr-mix-over', onMixOver);
  }, [replayGainMultiplier]);

  return { volume, setVolume, muted, toggleMute, vuLevels, audioReady, initAudio };
}
