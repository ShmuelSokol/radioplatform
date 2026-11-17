import { useState, useEffect, useRef, useCallback } from 'react';
import { getAssetAudioUrl } from '../api/assets';
import { holdUpdates, noteItemChange } from '../lib/appUpdate';
import { serverNow } from '../lib/serverClock';
import type { AssetInfo } from '../types';

const VOLUME_KEY = 'radio_volume';
const MUTE_KEY = 'radio_muted';

// Meters: RMS level in dBFS. The bar spans METER_FLOOR_DB to 0 dBFS; the red
// part (last 6 of 30 segments) starts at -8 dBFS, where only very hot audio goes.
export const METER_FLOOR_DB = -40;
const METER_FALL_DB_PER_FRAME = 0.35; // ~20 dB/s at 60 frames a second

function dbfs(samples: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  const rms = Math.sqrt(sum / samples.length);
  return rms > 0 ? Math.max(-100, 20 * Math.log10(rms)) : -100;
}
const SYNC_INTERVAL = 10000;
const SYNC_THRESHOLD = 5;
const TRACK_CHANGE_COOLDOWN = 15000;
const CROSSFADE_DURATION = 3.0; // seconds
// At a song-to-song crossfade the incoming item comes up this fast; only the outgoing one
// fades over CROSSFADE_DURATION. A 3 s fade-in of the incoming song over the quiet tail of
// the outgoing one was heard as 2 to 3 s of near silence at every change.
const CROSSFADE_FADE_IN = 0.4; // seconds
// Manual track change (skip / play-now): quick fade instead of a hard cut
const SKIP_FADE_OUT = 0.6; // seconds
const SKIP_FADE_IN = 0.15; // seconds
// On a cold load, keep the current track on air until the new one can play (capped)
const COLD_LOAD_MAX_WAIT_MS = 4000;
/** Where the song on air hands over: its analysed crossfade point, but never later than
 *  3 s before the end of the file actually loaded. A stored length or analysis can be
 *  longer than the file (the old analysis gave every item 180 s); then the song ran out
 *  and the listener heard silence until the server caught up. */
// Items this short (time checks, station IDs) are not crossfaded: they play to their
// end, like the server's hand-over rule (SHORT_ITEM_SECONDS in api/v1/queue.py)
const SHORT_ITEM_SECONDS = 20;
const SHORT_ITEM_HANDOVER_SECONDS = 0.3;
// A short item still playing when the server moves on is let finish, up to this long
const LET_FINISH_MAX_SECONDS = 8;
function handOverPoint(crossStart: number, fileDuration: number): number {
  const short = isFinite(fileDuration) && fileDuration > 0 && fileDuration < SHORT_ITEM_SECONDS;
  const fileEnd = short
    ? Math.max(0.1, fileDuration - SHORT_ITEM_HANDOVER_SECONDS)
    : isFinite(fileDuration) && fileDuration > CROSSFADE_DURATION
      ? fileDuration - CROSSFADE_DURATION : 0;
  if (crossStart > 0 && fileEnd > 0) return Math.min(crossStart, fileEnd);
  return crossStart > 0 ? crossStart : fileEnd;
}
// A change heard less than this far into the new item starts it from the top (no seek)
const START_FROM_TOP_SECONDS = 1.0;
// A timed item's lead-in fade ends this long after its due time (see the preempt fade)
const PREEMPT_FADE_OVERHANG_MS = 400;
// A hard timed item is loaded on the idle deck this long before its time
const TIMED_PRELOAD_AHEAD_MS = 60_000;

/** Seek only when it moves the position: even a seek to where the element already
 *  is makes Safari re-decode and can repeat a few hundred milliseconds. */
function seekIfNeeded(audio: HTMLAudioElement, position: number) {
  if (Math.abs(audio.currentTime - position) < 0.05) return;
  try { audio.currentTime = position; } catch { /* not seekable yet */ }
}
// The player may start a crossfade at most this long before the server reaches the
// crossfade point. Without this bound the player can run ahead of the server a little
// more with every song.
const MAX_LEAD_SECONDS = 1.0;
// A track change that arrives this close to the natural end of the song on air is the
// normal song-to-song change, so it gets the full crossfade rather than the quick skip fade.
const NATURAL_END_WINDOW_SECONDS = 4.0;
// Crossfade trigger check. A timer, not requestAnimationFrame: rAF stops in background tabs.
const CROSSFADE_CHECK_MS = 100;
// 50 ms of silence, played inside the Listen click to unlock both decks
const SILENT_WAV = 'data:audio/wav;base64,UklGRkQDAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YSADAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==';
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
  /** Sound can actually come out (the browser let the audio context start). */
  soundRunning: boolean;
  initAudio: () => Promise<void>;
  /** Stop making sound in this tab (another tab took over). A later initAudio brings it back. */
  releaseAudio: () => void;
  /** This computer is playing the sound out loud (the meters move either way) */
  monitoring: boolean;
  setMonitor: (on: boolean) => void;
  /** Operator's Next: bring this item up here at once, before the server's answer
   *  arrives. Listeners still change when the server announces it. */
  playNextNow: (asset: AssetInfo, fadeSeconds: number | null) => void;
}

// How long a local Next waits for the server to announce the same item before the
// player goes back to following the server
const LOCAL_NEXT_CONFIRM_MS = 4000;

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
  // Switch sound on by itself when the station is playing. The control room passes
  // false: sound starts only on a click there, so a second tab never doubles it.
  autoInit: boolean = true,
  // Whether this tab plays the sound out loud from the start. The control room
  // passes false: the engine runs silently so the level meters always move, and
  // the speaker button turns the sound on for this computer only.
  monitorDefault: boolean = true,
): AudioEngineState {
  // ── Operator's Next heard at once (control room) ──
  // The item the operator moved to, played here before the server confirms it. While
  // it stands, the player follows it instead of the server's now-playing.
  const [localNext, setLocalNext] = useState<{
    asset: AssetInfo; startedAt: string; cueIn: number; replayGainDb: number; fade: number | null; at: number;
  } | null>(null);
  const serverAssetId = nowPlayingAsset?.id ?? null;
  const serverStartedMs = currentStartedAt ? new Date(currentStartedAt).getTime() : NaN;
  useEffect(() => {
    if (!localNext) return;
    // The server announced a change made after the click: the same item (confirmed)
    // or another one (the server chose differently, follow it)
    if (serverAssetId === localNext.asset.id
      || (isFinite(serverStartedMs) && serverStartedMs >= localNext.at - 1000)) {
      setLocalNext(null);
      return;
    }
    const t = setTimeout(() => setLocalNext(null), LOCAL_NEXT_CONFIRM_MS);
    return () => clearTimeout(t);
  }, [localNext, serverAssetId, serverStartedMs]);
  if (localNext) {
    nowPlayingAsset = localNext.asset;
    currentStartedAt = localNext.startedAt;
    currentCueIn = localNext.cueIn;
    currentReplayGainDb = localNext.replayGainDb;
    currentCrossStart = 0;
    changeFadeSeconds = localNext.fade;
    // What follows is unknown until the server answers (the old "next" is this item)
    nextAssetId = null;
  }

  const changeFadeRef = useRef<number | null>(changeFadeSeconds);
  changeFadeRef.current = changeFadeSeconds;
  const ctxRef = useRef<AudioContext | null>(null);
  const [ctxRunning, setCtxRunning] = useState(false);
  const analyserLRef = useRef<AnalyserNode | null>(null);
  const analyserRRef = useRef<AnalyserNode | null>(null);
  const analyserDataLRef = useRef<Float32Array<ArrayBuffer> | null>(null);
  const analyserDataRRef = useRef<Float32Array<ArrayBuffer> | null>(null);
  const meterDbRef = useRef<[number, number]>([METER_FLOOR_DB, METER_FLOOR_DB]);
  const busRef = useRef<GainNode | null>(null);
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
  // Local monitoring: volume and mute live on one output gain after the meters,
  // so the meters show the station's level whatever this computer hears
  const [monitoring, setMonitoringState] = useState(monitorDefault);
  const monitoringRef = useRef(monitorDefault);
  const outGainRef = useRef<GainNode | null>(null);
  const outLevel = () => (monitoringRef.current && !mutedRef.current ? volumeRef.current : 0);
  const applyOut = useCallback(() => {
    const ctx = ctxRef.current; const out = outGainRef.current;
    const level = monitoringRef.current && !mutedRef.current ? volumeRef.current : 0;
    if (ctx && out) out.gain.setTargetAtTime(level, ctx.currentTime, 0.02);
  }, []);
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
  // Bumped when sound is switched on mid-item, so the item on air is brought up again
  const [listenTick, setListenTick] = useState(0);
  const isPlayingRef = useRef(isPlaying);
  isPlayingRef.current = isPlaying;
  // Assigned during render so effects in the same pass see the new track's start time
  const startedAtRef = useRef(currentStartedAt);
  startedAtRef.current = currentStartedAt;

  // Where in the track playback should be right now
  const livePosition = useCallback((cueIn: number) => {
    const startedAt = startedAtRef.current;
    if (startedAt) {
      const startMs = new Date(startedAt).getTime();
      if (isFinite(startMs)) return Math.max(cueIn, (serverNow() - startMs) / 1000);
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
      // Still inside the click: start the decks now. Safari refuses play() that a
      // click did not start, so a deck loaded before Listen stayed silent until a
      // later item; one started (or started and paused) here may play from now on.
      for (const label of ['A', 'B'] as DeckLabel[]) {
        const deck = label === 'A' ? deckARef.current : deckBRef.current;
        if (!deck.audio.src || !deck.audio.paused) continue;
        const onAir = label === activeDeckRef.current && isPlayingRef.current
          && deckAssetRef.current[label] != null && deckAssetRef.current[label] === lastAssetId.current;
        if (onAir) {
          const pos = livePosition(0);
          if (isFinite(deck.audio.duration) && pos < deck.audio.duration - 1 && Math.abs(deck.audio.currentTime - pos) > 1) {
            try { deck.audio.currentTime = pos; } catch { /* not seekable yet */ }
          }
          deck.audio.play().catch(() => {});
        } else {
          deck.audio.play().then(() => {
            if (label !== activeDeckRef.current && !crossfadingRef.current) deck.audio.pause();
          }).catch(() => {});
        }
      }
      if (ctxRef.current.state === 'suspended') await ctxRef.current.resume();
      // Sound switched on mid-item: the browser refused to start the deck before
      // this click, so nothing is playing. Bring the item on air up again now
      // instead of waiting for the next item.
      const deck = activeDeckRef.current === 'A' ? deckARef.current : deckBRef.current;
      if (isPlayingRef.current && deck.audio.paused && !crossfadingRef.current) {
        lastAssetId.current = null;
        lastStartedAtRef.current = null;
        deckAssetRef.current[activeDeckRef.current] = null;
        setListenTick((t) => t + 1);
      }
      setAudioReady(true);
      return;
    }

    // Build the whole graph before waiting on anything. Without a click Safari never
    // settles resume(), and a later Listen click used to build a second graph that
    // the first call then replaced with empty decks: no sound until the next item.
    const ctx = new AudioContext();
    ctx.onstatechange = () => setCtxRunning(ctx.state === 'running');
    ctxRef.current = ctx;
    if (ctx.state === 'suspended') ctx.resume().catch(() => {});
    setCtxRunning(ctx.state === 'running');

    // decks → stereo bus → speakers, and bus → splitter → left/right meters.
    // The bus used to be a ChannelMerger: each merger input is mono, so a deck
    // connected to input 0 was mixed down to mono and came out of the LEFT
    // speaker only (heard by Shmuel, 2026-11-16).
    const bus = ctx.createGain();
    // Always two channels out: a mono file is spread to both speakers (and meters)
    bus.channelCount = 2;
    bus.channelCountMode = 'explicit';
    bus.channelInterpretation = 'speakers';
    const splitter = ctx.createChannelSplitter(2);
    const analyserL = ctx.createAnalyser();
    // 2048 samples is about 46 ms at 44.1 kHz: one meter reading per window
    analyserL.fftSize = 2048;
    const analyserR = ctx.createAnalyser();
    analyserR.fftSize = 2048;

    bus.connect(splitter);
    splitter.connect(analyserL, 0);
    splitter.connect(analyserR, 1);
    const outGain = ctx.createGain();
    outGain.gain.value = outLevel();
    bus.connect(outGain);
    outGain.connect(ctx.destination);
    outGainRef.current = outGain;

    busRef.current = bus;
    splitterRef.current = splitter;
    analyserLRef.current = analyserL;
    analyserRRef.current = analyserR;
    analyserDataLRef.current = new Float32Array(analyserL.fftSize) as Float32Array<ArrayBuffer>;
    analyserDataRRef.current = new Float32Array(analyserR.fftSize) as Float32Array<ArrayBuffer>;

    // Create two decks
    const deckA = createDeck(ctx);
    const deckB = createDeck(ctx);
    deckA.gain.connect(bus);
    deckB.gain.connect(bus);
    deckARef.current = deckA;
    deckBRef.current = deckB;
    activeDeckRef.current = 'A';

    // Still inside the click: play a moment of silence on both decks. Safari lets an
    // element that a click started play again later; the listener page loads the
    // item on air only after this click, and its play() was refused, so one press
    // of Listen gave no sound until the volume slider was touched (2026-11-16).
    for (const deck of [deckA, deckB]) {
      if (deck.audio.src) continue;
      deck.audio.src = SILENT_WAV;
      deck.audio.play().then(() => {
        if (deck.audio.src === SILENT_WAV) deck.audio.pause();
      }).catch(() => {});
    }

    setAudioReady(true);
  }, [createDeck, livePosition]);

  // ── A release never reloads this tab while the player is on ──
  // "On" means sound can actually come out: the audio context is running. On
  // the control room the station is nearly always playing, so holding on
  // isPlaying alone kept operator tabs on an old release forever, even when
  // the operator never switched audio on (the context stays suspended until a
  // click). Listener pages hold separately from the moment Listen is pressed.
  useEffect(() => {
    if (!isPlaying || !ctxRunning || !monitoring) return;
    return holdUpdates();
  }, [isPlaying, ctxRunning, monitoring]);

  // A pushed release that includes playing tabs reloads right after an item change
  useEffect(() => {
    if (isPlaying && ctxRunning && monitoringRef.current && nowPlayingAsset?.id) noteItemChange();
  }, [nowPlayingAsset?.id, currentStartedAt]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Auto-init when playback is active ──
  useEffect(() => {
    if (autoInit && isPlaying && !audioReady) {
      initAudio().catch(() => {});
    }
  }, [autoInit, isPlaying, audioReady, initAudio]);

  const releaseAudio = useCallback(() => {
    if (crossfadeTimerRef.current) clearTimeout(crossfadeTimerRef.current);
    crossfadingRef.current = false;
    deckARef.current?.audio.pause();
    deckBRef.current?.audio.pause();
    ctxRef.current?.suspend().catch(() => {});
    setAudioReady(false);
  }, []);

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

    const targetGain = replayGainMultiplier(nextReplayGainDbRef.current);

    // Seek inactive deck to cue-in point and play
    seekIfNeeded(inactiveDeck.audio, nextCueInRef.current);
    inactiveDeck.gain.gain.cancelScheduledValues(ctx.currentTime);
    inactiveDeck.gain.gain.setValueAtTime(0, ctx.currentTime);
    inactiveDeck.gain.gain.linearRampToValueAtTime(targetGain, ctx.currentTime + CROSSFADE_FADE_IN);
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
        // Stopped: the next start must join the station where it is then, never
        // carry on from here. A listener pressing Stop then Listen heard the item go on
        // from where they stopped (the deck was played again in the click and taken as
        // already on air). So the decks are silenced and forget what they hold.
        for (const label of ['A', 'B'] as DeckLabel[]) {
          const deck = label === 'A' ? deckARef.current : deckBRef.current;
          if (!deck) continue;
          deck.audio.pause();
          const ctx = ctxRef.current;
          if (ctx) {
            deck.gain.gain.cancelScheduledValues(ctx.currentTime);
            deck.gain.gain.setValueAtTime(0, ctx.currentTime);
          }
          deckAssetRef.current[label] = null;
        }
        crossfadingRef.current = false;
        if (crossfadeTimerRef.current) {
          clearTimeout(crossfadeTimerRef.current);
          crossfadeTimerRef.current = null;
        }
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
    const outEnd = handOverPoint(outCross, outDur);
    const naturalEnd = !outDeck.audio.paused && outEnd > 0 && outTime >= outEnd - NATURAL_END_WINDOW_SECONDS;
    // An operator's Next carries the fade they chose; it wins even near the end of the item
    // (a short looped jingle is nearly always "near the end").
    const asked = changeFadeRef.current;
    const operatorFade = asked != null && isFinite(asked) ? Math.min(10, Math.max(0.03, asked)) : null;
    const fadeOut = operatorFade ?? (naturalEnd ? CROSSFADE_DURATION : SKIP_FADE_OUT);
    const fadeIn = SKIP_FADE_IN;
    // A short spoken item (time check, weather) that started late on this page is
    // still talking when the server moves on: let it finish, never talk over it.
    // All Day Stories, 10 PM 2026-11-16: the paused story came back 1.8 s into the
    // time check. An operator's Next still cuts at once.
    const letFinish = operatorFade == null && !replay && !outDeck.audio.paused && !outDeck.audio.ended
      && deckAssetRef.current[outLabel] != null
      && isFinite(outDur) && outDur > 0 && outDur < SHORT_ITEM_SECONDS
      && outDur - outTime > 0.05 && outDur - outTime < LET_FINISH_MAX_SECONDS
      // behind the server's hand-over point: it started late here
      && outTime < outEnd - 0.5;

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
      const targetGain = replayGain;

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
      // Within the item's first moment, start from the top rather than seek a fraction
      // of a second in: Safari lands an MP3 seek on an earlier frame, so the opening
      // was heard twice. Being that little behind the server is inaudible.
      if (startPos - cueIn < START_FROM_TOP_SECONDS) startPos = cueIn;
      seekIfNeeded(inDeck.audio, startPos);
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

    // Bring the new item on air, after the short item on air has finished if it should
    const goLiveAfterOut = () => {
      if (!letFinish) { goLive(); return; }
      const left = outDeck.audio.paused || outDeck.audio.ended ? 0 : outDur - outDeck.audio.currentTime;
      if (left <= 0.05) { goLive(); return; }
      const onEnded = () => goLive();
      outDeck.audio.addEventListener('ended', onEnded, { once: true });
      setTimeout(() => {
        outDeck.audio.removeEventListener('ended', onEnded);
        goLive();
      }, Math.min(LET_FINISH_MAX_SECONDS, left) * 1000 + 100);
    };

    const run = async () => {
      try {
        // Already pre-loaded and ready: go live now, without a round trip for its URL
        if (assetId && deckAssetRef.current[inLabel] === assetId && inDeck.audio.readyState >= 2) {
          goLiveAfterOut();
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
          goLiveAfterOut();
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
        const onReady = () => goLiveAfterOut();
        inDeck.audio.addEventListener('canplay', onReady, { once: true });
        setTimeout(() => {
          inDeck.audio.removeEventListener('canplay', onReady);
          goLiveAfterOut();
        }, COLD_LOAD_MAX_WAIT_MS);
      } catch {
        // Load failure
      }
    };
    run();
  }, [nowPlayingAsset?.id, currentStartedAt, isPlaying, audioReady, listenTick, currentCueIn, currentReplayGainDb, audioUrlOverride, getDeck, livePosition, replayGainMultiplier]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── What the idle deck should hold: normally the next track, but a hard timed
  // item (program block, hard sponsorship) that cuts in before the song ends is
  // what comes next, so it is loaded a minute ahead. A cold load at its due time
  // left half a second of silence in the control room.
  const [timedSoon, setTimedSoon] = useState(false);
  useEffect(() => {
    setTimedSoon(false);
    if (!nextPreemptAt || !nextPreemptAssetId) return;
    const due = new Date(nextPreemptAt).getTime();
    if (!isFinite(due)) return;
    const startMs = currentStartedAt ? new Date(currentStartedAt).getTime() : NaN;
    const dur = nowPlayingAsset?.duration ?? null;
    // Due after the song on air ends by itself: the next track comes first
    if (isFinite(startMs) && dur && due >= startMs + dur * 1000 - CROSSFADE_DURATION * 1000) return;
    const wait = due - TIMED_PRELOAD_AHEAD_MS - serverNow();
    if (wait <= 0) { setTimedSoon(true); return; }
    const t = setTimeout(() => setTimedSoon(true), wait);
    return () => clearTimeout(t);
  }, [nextPreemptAt, nextPreemptAssetId, currentStartedAt, nowPlayingAsset?.duration]);
  const preloadId = timedSoon && nextPreemptAssetId ? nextPreemptAssetId : nextAssetId;
  const preloadUrlOverride = preloadId === nextAssetId ? nextAudioUrlOverride : null;

  // ── Pre-load next track on the idle deck ──
  // Declared after the track-change effect so a track change claims its deck first.
  useEffect(() => {
    const nextAssetId = preloadId;
    const nextAudioUrlOverride = preloadUrlOverride;
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
  }, [preloadId, preloadUrlOverride, audioReady, swapTick, nowPlayingAsset?.id, getDeck]);

  // ── Preempt fade-out: schedule precise fade before hourly announcements ──
  useEffect(() => {
    if (!nextPreemptAt || !nextPreemptAssetId || !isPlaying || !audioReady) return;
    if (preemptFadeMs <= 0) return;

    // The fade reaches silence a little after the due time: the timed item is
    // announced a moment after it (the server starts it at the due time, then the
    // message travels). Ending at the due time itself left a gap of dead air.
    const preemptTime = new Date(nextPreemptAt).getTime() + PREEMPT_FADE_OVERHANG_MS;
    const fadeStartTime = preemptTime - preemptFadeMs;
    const msUntilFade = fadeStartTime - serverNow();

    if (msUntilFade > 300000) return;
    const actualDelay = Math.max(0, msUntilFade);
    const remainingMs = preemptTime - serverNow();
    const actualFadeDuration = msUntilFade < 0
      ? Math.max(500, remainingMs)
      : preemptFadeMs;
    if (remainingMs <= 0) return;

    const fadeTimer = setTimeout(() => {
      const activeDeck = getActiveDeck();
      const ctx = ctxRef.current;
      if (!activeDeck || !ctx) return;

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
      // The server's elapsed time belongs to the item it says is on air. When the player
      // has already handed over to the next item (its file ended before the stored
      // length), that time must not be applied to the new item.
      if (deckAssetRef.current[activeDeckRef.current] !== lastAssetId.current) return;

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
    volumeRef.current = clamped;
    applyOut();
    try { localStorage.setItem(VOLUME_KEY, String(clamped)); } catch {}
  }, [applyOut]);

  /** Hear the station on this computer (true) or only watch the meters (false). */
  const setMonitor = useCallback((on: boolean) => {
    monitoringRef.current = on;
    setMonitoringState(on);
    applyOut();
  }, [applyOut]);

  const toggleMute = useCallback(() => {
    setMuted(prev => {
      const next = !prev;
      mutedRef.current = next;
      applyOut();
      try { localStorage.setItem(MUTE_KEY, String(next)); } catch {}
      return next;
    });
  }, [applyOut]);

  // ── VU meter polling (~30fps) + client-driven crossfade check ──
  useEffect(() => {
    const poll = () => {
      // VU metering
      const aL = analyserLRef.current;
      const aR = analyserRRef.current;
      const dL = analyserDataLRef.current;
      const dR = analyserDataRRef.current;
      if (aL && aR && dL && dR) {
        // Real level of what goes on air (after replay gain, before the local
        // speaker gain, so Listen and Mute never change it): RMS in dBFS over
        // the last ~46 ms, with a quick rise and a ~20 dB/s fall like a
        // hardware meter. 0..1 maps METER_FLOOR_DB..0 dBFS.
        aL.getFloatTimeDomainData(dL);
        aR.getFloatTimeDomainData(dR);
        const prev = meterDbRef.current;
        const next = [dbfs(dL), dbfs(dR)].map((db, i) =>
          db >= prev[i] ? prev[i] + (db - prev[i]) * 0.6 : Math.max(db, prev[i] - METER_FALL_DB_PER_FRAME),
        ) as [number, number];
        meterDbRef.current = next;
        const frac = (db: number) => Math.min(1, Math.max(0, (db - METER_FLOOR_DB) / -METER_FLOOR_DB));
        const l = frac(next[0]);
        const r = frac(next[1]);
        setVuLevels(v => (Math.abs(v[0] - l) < 0.005 && Math.abs(v[1] - r) < 0.005 ? v : [l, r]));
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
      const crossPoint = handOverPoint(crossStartRef.current, dur);
      if (crossPoint > 0 && ct >= crossPoint) {
        // Do not run ahead of the server: wait until it is (nearly) at the crossfade
        // point too. It announces the next track there, which brings it on air anyway.
        const startedAt = startedAtRef.current;
        const startMs = startedAt ? new Date(startedAt).getTime() : NaN;
        if (isFinite(startMs)) {
          const serverPos = (serverNow() - startMs) / 1000;
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
      const out = busRef.current;
      if (!data?.url || data._taken || !ctx || !out || ctx.state !== 'running') return;
      data._taken = true;
      fetch(data.url)
        .then((res) => res.arrayBuffer())
        .then((bytes) => ctx.decodeAudioData(bytes))
        .then((buffer) => {
          const source = ctx.createBufferSource();
          source.buffer = buffer;
          const gain = ctx.createGain();
          gain.gain.value = replayGainMultiplier(data.replay_gain_db ?? 0);
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

  const playNextNow = useCallback((asset: AssetInfo, fadeSeconds: number | null) => {
    if (!ctxRef.current || ctxRef.current.state !== 'running') return; // not listening here
    const now = serverNow(); // compared with the server's start times
    setLocalNext({
      asset,
      startedAt: new Date(now).toISOString(),
      // cue and loudness of the item the server said is next, when it is this one
      cueIn: nextAssetIdRef.current === asset.id ? nextCueInRef.current : 0,
      replayGainDb: nextAssetIdRef.current === asset.id ? nextReplayGainDbRef.current : 0,
      fade: fadeSeconds,
      at: now,
    });
  }, []);

  return { volume, setVolume, muted, toggleMute, vuLevels, audioReady, soundRunning: ctxRunning, initAudio, playNextNow, releaseAudio, monitoring, setMonitor };
}
