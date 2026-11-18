import { useState, useEffect, useLayoutEffect, useMemo, useRef, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { useStations } from '../../hooks/useStations';
import {
  useQueue, useLastPlayed, useSkipCurrent, useRemoveFromQueue, useStartPlayback,
  useMoveUp, useMoveDown, useWeatherPreview, useReorderDnd,
  QUEUE_ALL_ROWS, QUEUE_FIRST_ROWS, applyActionQueue,
} from '../../hooks/useQueue';
import { useLiveShows } from '../../hooks/useLiveShows';
import { useLiveAudio } from '../../hooks/useLiveAudio';
import { useAudioEngine, METER_SEGMENTS, SILENT_WAV } from '../../hooks/useAudioEngine';
import type { LiveStream } from '../../lib/liveStream';
import { adoptStationStream, playStationStream, releaseStationStream, runningStationStream, stationStreamElement, stationStreamSounding, stopStationStream } from '../../lib/stationStream';
import { usePlayerStore } from '../../stores/playerStore';
import { displayTitle } from '../../lib/displayTitle';
import { useNowPlayingWS } from '../../hooks/useNowPlayingWS';
import { fireNow, insertAt, loopCurrent, mixOver, queueEnd, queueNext } from '../../api/liveControl';
import type { SkipFade } from '../../api/queue';
import { insertEntry as addQueuePlaceholder } from '../../hooks/queueOptimistic';
import type { QueueDataLike } from '../../hooks/queueOptimistic';
import type { AssetInfo } from '../../types';
import TransportBar, { TransportButtons } from './controlroom/TransportBar';
import type { TransportProps } from './controlroom/TransportBar';
import { REPORT_BUG_EVENT } from '../../components/BugReporter';
import QueuePanel from './controlroom/QueuePanel';
import SongPanel, { isSponsorship } from './controlroom/SongPanel';
import type { SongTarget } from './controlroom/SongPanel';
import SponsorPanel from './controlroom/SponsorPanel';
import type { QueueRow } from './controlroom/QueuePanel';
import { nextCut } from './controlroom/timing';
import LibraryPanel from './controlroom/LibraryPanel';
import HotButtons from './controlroom/HotButtons';
import { usePfl, WEATHER_PFL_ID, type PflClip } from './controlroom/pfl';
import { PflDeck } from './controlroom/PflDeck';
import { ErrorsPanel, LivePanel, LogPanel, SchedulePanel } from './controlroom/SidePanels';
import ModeButtons from './controlroom/ModeButtons';
import { panicRequest, setStationModes, type StationModes } from '../../api/queue';
import { canAccess, canChange, isManager } from '../../auth/access';
import { dayKey, isValidTz, useHotButtons, useMediaQuery, usePhoneLandscape, useStored } from './controlroom/shared';
import type { CtlAsset, FireMode, SendHow } from './controlroom/shared';
import { ResumePrompt, useResumeAfterRelease } from '../../components/audio/ResumeAfterRelease';
import { useMayChange } from '../../hooks/useMayChange';
import { serverNow } from '../../lib/serverClock';

type Pane = 'queue' | 'buttons' | 'library' | 'live' | 'log' | 'schedule' | 'errors';
type SideTab = 'library' | 'live' | 'log' | 'schedule' | 'errors';

interface Toast { text: string; type: 'ok' | 'warn' | 'error' }

const SIDE_TABS: Array<{ id: SideTab; label: string }> = [
  { id: 'library', label: 'Songs' },
  { id: 'live', label: 'Go live' },
  { id: 'log', label: 'Played' },
  { id: 'schedule', label: 'Schedule' },
  { id: 'errors', label: 'Errors' },
];

const MOBILE_TABS: Array<{ id: Pane; label: string; icon: string }> = [
  { id: 'queue', label: 'Queue', icon: '☰' },
  { id: 'buttons', label: 'Buttons', icon: '▦' },
  { id: 'library', label: 'Library', icon: '♪' },
  { id: 'live', label: 'Go live', icon: '●' },
  { id: 'log', label: 'More', icon: '⋯' },
];

// Fade lengths the server uses for each Next button (queue.py SKIP_FADE_SECONDS)
const SKIP_FADE_SECONDS: Record<SkipFade, number> = { hard: 0, fade: 0.5, soft: 3 };

// Share of the height Up next gets above the lower pane (the old fixed 3:2)
const SPLIT_DEFAULT = 0.6;
const SPLIT_MIN = 0.15;
const SPLIT_MAX = 0.85;

const LISTEN_KEY = 'kbr-cr-listen';
// How long the header may say "Starting…" for the server stream, and after how long
// without sound a tap starts the stream afresh
const STREAM_STARTING_MAX_MS = 8000;
const STREAM_RESTART_AFTER_MS = 4000;

export default function Dashboard() {
  const { user } = useAuth();
  // The one rule for who may run the station from this screen (admin, manager, DJ); a
  // viewer watches. Every panel asks the same hook.
  const mayOperate = useMayChange();
  const qc = useQueryClient();
  const isDesktop = useMediaQuery('(min-width: 1024px)');
  // Phone held sideways: the page scrolls so the control room fills the screen with the
  // site menu just above it (classic.css "phone landscape"); start scrolled to it.
  const phoneLandscape = usePhoneLandscape();
  // classic.css lays the page out sideways under html.cr-land (set before paint)
  useLayoutEffect(() => {
    document.documentElement.classList.toggle('cr-land', phoneLandscape);
    return () => document.documentElement.classList.remove('cr-land');
  }, [phoneLandscape]);
  useEffect(() => {
    if (!phoneLandscape) return;
    const el = document.querySelector('[data-testid="control-room"]');
    const t = window.setTimeout(() => el?.scrollIntoView({ block: 'start' }), 50);
    return () => window.clearTimeout(t);
  }, [phoneLandscape]);
  const { data: stationsData } = useStations();
  const stations = useMemo(() => stationsData?.stations ?? [], [stationsData]);

  // ── Station selection (remembered in this browser) ─────────
  // A station this tab is listening to (from a listener page, or this screen left a
  // moment ago) is the one shown: its sound carries on here
  const [selectedStationId, setSelectedStationId] = useState<string | null>(() => {
    const listening = runningStationStream()?.stationId;
    if (listening) return listening;
    try { return localStorage.getItem('radio_last_station') || null; } catch { return null; }
  });
  useEffect(() => {
    if (stations.length === 0) return;
    if (!selectedStationId || !stations.some(s => s.id === selectedStationId)) {
      setSelectedStationId(stations[0].id);
    }
  }, [stations, selectedStationId]);
  useEffect(() => {
    if (selectedStationId) {
      try { localStorage.setItem('radio_last_station', selectedStationId); } catch { /* private mode */ }
    }
  }, [selectedStationId]);
  const stationId = selectedStationId;
  const station = stations.find(s => s.id === stationId);
  const stationTz = station?.timezone && isValidTz(station.timezone) ? station.timezone : undefined;

  // ── Server state ───────────────────────────────────────────
  // Load a few hours of the queue; the rest only when asked for (scroll to the end, pick a day)
  const [queueAllFor, setQueueAllFor] = useState<string | null>(null);
  const queueAll = !!stationId && queueAllFor === stationId;
  const { data: queueData, isError: queueFailed } = useQueue(stationId, queueAll ? QUEUE_ALL_ROWS : QUEUE_FIRST_ROWS);
  // Back from the background: throttled timers may have left the queue minutes old
  useEffect(() => {
    const onVisible = () => {
      if (!document.hidden && stationId) void qc.invalidateQueries({ queryKey: ['queue', stationId] });
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [qc, stationId]);
  const loadWholeQueue = useCallback(() => { if (stationId) setQueueAllFor(stationId); }, [stationId]);
  const loadFirstHours = useCallback(() => setQueueAllFor(null), []);
  const { nowPlaying: wsNowPlaying } = useNowPlayingWS(stationId ?? '');
  const { data: lastPlayedMap } = useLastPlayed(stationId);
  const { data: liveData } = useLiveShows(stationId ? { station_id: stationId } : undefined);
  const skipMut = useSkipCurrent(stationId ?? '');
  const removeMut = useRemoveFromQueue(stationId ?? '');
  const startMut = useStartPlayback(stationId ?? '');
  const moveUpMut = useMoveUp(stationId ?? '');
  const moveDownMut = useMoveDown(stationId ?? '');
  const reorderMut = useReorderDnd(stationId ?? '');
  const weatherPreviewMut = useWeatherPreview(stationId ?? '');

  const queueEntries: QueueRow[] = queueData?.entries ?? [];
  // No queue yet is not the same as an empty queue: say so rather than "Stopped"
  const queueNotice = stationId && !queueData
    ? (queueFailed ? "Can't reach the station. Trying again…" : 'Loading the queue…')
    : null;
  const nowPlaying = queueData?.now_playing ?? null;
  const nowAsset = nowPlaying?.asset ?? null;
  const isPlaying = !!nowPlaying;
  // What Next will really play: the server says which entry (it skips items timed
  // for later). Older servers do not, so fall back to the first waiting row.
  const nextEntryId = (queueData as { next_entry_id?: string | null } | undefined)?.next_entry_id;
  const nextAsset = (nextEntryId !== undefined
    ? queueEntries.find(e => e.id === nextEntryId)
    : queueEntries.find(e => e.status === 'pending'))?.asset ?? null;
  // What pressing Next plays right now. It differs from "up next" when a timed item
  // (the :30 weather) is due by the end of the song but not yet: Next skips it.
  const skipIdRaw = (queueData as { skip_entry_id?: string | null } | undefined)?.skip_entry_id;
  const skipEntryId = skipIdRaw !== undefined ? skipIdRaw : nextEntryId;
  const skipAsset = skipIdRaw !== undefined
    ? queueEntries.find(e => e.id === skipIdRaw)?.asset ?? null
    : nextAsset;
  const liveShow = (liveData?.shows ?? []).find(s => s.status === 'live' && (!stationId || s.station_id === stationId)) ?? null;

  // ── UI state ───────────────────────────────────────────────
  const [pane, setPane] = useState<Pane>('queue');
  const [sideTab, setSideTab] = useStored<SideTab>('kbr_ctl_side_tab', 'library');
  // The right-hand button column: does it hold more than is showing below?
  const controlsRef = useRef<HTMLElement>(null);
  const [controlsMore, setControlsMore] = useState(false);
  const checkControlsMore = useCallback(() => {
    const el = controlsRef.current;
    setControlsMore(!!el && el.scrollHeight - el.scrollTop - el.clientHeight > 6);
  }, []);
  useEffect(() => {
    const el = controlsRef.current;
    if (!el) return;
    checkControlsMore();
    const watch = new ResizeObserver(checkControlsMore);
    watch.observe(el);
    return () => watch.disconnect();
  }, [isDesktop, checkControlsMore]);
  const [fireMode, setFireMode] = useStored<FireMode>('kbr_ctl_fire_mode', 'now');
  const [insertBeforeId, setInsertBeforeId] = useState<string | null>(null);
  const [queueDateFilter, setQueueDateFilter] = useState('');
  useEffect(() => { if (queueDateFilter) loadWholeQueue(); }, [queueDateFilter, loadWholeQueue]);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  // Quick actions panel for one song (the one on air or a queue row)
  const [songTarget, setSongTarget] = useState<SongTarget | null>(null);
  const openRowSong = useCallback((row: QueueRow) => {
    if (row.asset) setSongTarget({ asset: row.asset, onAir: row.status === 'playing', source: row.source ?? null });
  }, []);
  const closeSong = useCallback(() => setSongTarget(null), []);
  const songToast = useCallback((text: string, type: Toast['type']) => setToast({ text, type }), []);
  const [busyId, setBusyId] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const { pins, toggle: togglePin, cart, selectPage, selectSet, addSet, renameSet, deleteSet } = useHotButtons(stationId);
  const pinnedIds = useMemo(() => new Set(pins.map(p => p.id)), [pins]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), toast.type === 'ok' ? 2500 : 6000);
    return () => clearTimeout(t);
  }, [toast]);
  useEffect(() => { if (isPlaying) setStatusMessage(null); }, [isPlaying]);
  // Leaving a station cancels a pending insert on it.
  useEffect(() => { setInsertBeforeId(null); setQueueDateFilter(''); }, [stationId]);

  // The marked insert point disappears once that item starts playing or is removed.
  const insertEntry = insertBeforeId ? queueEntries.find(e => e.id === insertBeforeId && e.status === 'pending') : undefined;
  useEffect(() => {
    if (insertBeforeId && queueData && !insertEntry) setInsertBeforeId(null);
  }, [insertBeforeId, insertEntry, queueData]);
  const insertLabel = insertEntry ? (insertEntry.asset?.title ?? 'this item') : null;

  // ── Countdown driven by the server's start time ────────────
  const [realElapsed, setRealElapsed] = useState(0);
  const serverStartedAt = wsNowPlaying?.started_at ?? nowPlaying?.started_at ?? null;
  const wsEndsAt = wsNowPlaying?.ends_at ?? null;
  const serverDuration = wsEndsAt && serverStartedAt
    ? Math.max(0, (new Date(wsEndsAt).getTime() - new Date(serverStartedAt).getTime()) / 1000)
    : (nowAsset?.duration ?? 0);
  useEffect(() => {
    const tick = () => {
      if (serverStartedAt && serverDuration > 0) {
        const el = Math.min(Math.max(0, (serverNow() - new Date(serverStartedAt).getTime()) / 1000), serverDuration);
        setRealElapsed(el);
      } else {
        setRealElapsed(0);
      }
    };
    tick();
    const t = setInterval(tick, 200);
    return () => clearInterval(t);
  }, [serverStartedAt, serverDuration]);
  // Stopped: the last now-playing message can still be around, so show no time at all
  const duration = isPlaying ? (serverDuration || (nowAsset?.duration ?? 0)) : 0;
  const shownElapsed = isPlaying ? realElapsed : 0;
  const naturalRemaining = isPlaying ? Math.max(0, duration - realElapsed) : 0;
  // Intro length is counted from where the item starts on air, so the countdown is simply intro minus elapsed.
  const introLength = Number((nowAsset as { intro_seconds?: number | null } | null)?.intro_seconds ?? wsNowPlaying?.asset?.intro_seconds ?? 0);
  const introSeconds = isPlaying && introLength > realElapsed ? introLength - realElapsed : 0;

  // ── Audio monitor (what the operator hears in this browser) ─
  // A live show on air: the host is heard here instead of the music (the station's
  // queue is paused), and only in the tab that holds the sound.
  const [liveEnabled, setLiveEnabled] = useState(false);
  const live = useLiveAudio(stationId, liveEnabled);
  const staleNowPlaying = useRef<typeof wsNowPlaying | undefined>(undefined);
  if (live.onAir && staleNowPlaying.current === undefined) staleNowPlaying.current = wsNowPlaying;
  if (!live.onAir && staleNowPlaying.current !== undefined && staleNowPlaying.current !== wsNowPlaying) {
    staleNowPlaying.current = undefined;
  }
  const musicAllowed = !live.onAir && staleNowPlaying.current === undefined;
  const audioAsset: AssetInfo | null = wsNowPlaying?.asset_id && wsNowPlaying.asset ? {
    id: wsNowPlaying.asset_id,
    title: wsNowPlaying.asset.title,
    artist: wsNowPlaying.asset.artist ?? null,
    asset_type: nowAsset?.asset_type ?? 'music',
    category: nowAsset?.category ?? null,
    duration: nowAsset?.duration ?? null,
  } : nowAsset ? {
    id: nowAsset.id, title: nowAsset.title, artist: nowAsset.artist,
    asset_type: nowAsset.asset_type, category: nowAsset.category, duration: nowAsset.duration,
  } : null;

  // The next moment a timed item cuts the song on air. Only a hard item does that at its
  // time (and a soft one at its limit); a soft item waits for the song to end, so the
  // player must not fade for it, or the song goes silent until it would have ended.
  const nextPreempt = useMemo(() => nextCut(queueEntries), [queueEntries]);
  const preemptFadeMs = queueData?.preempt_fade_ms ?? 2000;
  // A hard timed item (time check, hard block) cuts the song at its time: Remaining and the
  // progress bar count down to that cut, not to the end of the file (Shmuel, 9:59 AM:
  // "Why does it show 1:45 remaining if it'll fade for the 10am time report?").
  const cutInSec = isPlaying && nextPreempt
    ? (new Date(nextPreempt.preempt_at).getTime() - serverNow()) / 1000
    : null;
  const cutsShort = cutInSec !== null && cutInSec > 0 && cutInSec < naturalRemaining;
  const remaining = cutsShort ? cutInSec : naturalRemaining;
  const cutAtIso = cutsShort ? nextPreempt!.preempt_at : null;

  const dashStreamUrl = wsNowPlaying?.stream_url || null;
  const [streamFailed, setStreamFailed] = useState(false);
  // A stream that failed is tried again on another station's stream
  useEffect(() => { setStreamFailed(false); }, [dashStreamUrl]);
  const dashUseStream = !!dashStreamUrl && !streamFailed;
  // The tab's one stream player (lib/stationStream): it outlives this screen, so the
  // sound carries on to the next page and back without a new tap
  const dashStreamAudioRef = useRef<HTMLAudioElement>(stationStreamElement());
  // The server stream is played the way the Listen page plays it (lib/liveStream:
  // a short start, the HLS copy on an iPhone), and only while this device listens.
  // It is started inside the tap (takeSound): Safari refuses a stream that an
  // effect starts, and the page then fell back to the built-in player.
  const [streamSounding, setStreamSounding] = useState(false);
  const streamRef = useRef<{ url: string; stream: LiveStream; startedAt: number } | null>(null);
  const streamSoundingRef = useRef(false);
  streamSoundingRef.current = streamSounding;
  // "Starting…" is shown for a stream on its way, and only for so long: after that the
  // button says Listen again and a tap starts the stream afresh (it must never stay
  // on "Starting…").
  const [streamStarting, setStreamStarting] = useState(false);
  const startingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stopStream = useCallback(() => {
    streamRef.current?.stream.stop();
    streamRef.current = null;
  }, []);
  const startStream = useCallback((url: string) => {
    if (!stationIdRef.current) return;
    stopStream();
    streamRef.current = { url, startedAt: Date.now(), stream: playStationStream(stationIdRef.current, url, () => { streamRef.current = null; setStreamFailed(true); }) };
    setStreamStarting(true);
    if (startingTimer.current) clearTimeout(startingTimer.current);
    startingTimer.current = setTimeout(() => setStreamStarting(false), STREAM_STARTING_MAX_MS);
  }, [stopStream]);
  useEffect(() => () => { if (startingTimer.current) clearTimeout(startingTimer.current); }, []);
  // Whether the stream element really plays (it is the tab's shared one, not in this screen)
  useEffect(() => {
    const audio = dashStreamAudioRef.current;
    const on = () => setStreamSounding(!!streamRef.current);
    const off = () => setStreamSounding(false);
    const offEvents = ['pause', 'emptied', 'ended', 'error'] as const;
    audio.addEventListener('playing', on);
    offEvents.forEach((e) => audio.addEventListener(e, off));
    return () => {
      audio.removeEventListener('playing', on);
      offEvents.forEach((e) => audio.removeEventListener(e, off));
    };
  }, []);
  useEffect(() => {
    const audio = dashStreamAudioRef.current;
    if (!audio || !dashUseStream) return;
    const onError = () => setStreamFailed(true);
    audio.addEventListener('error', onError);
    return () => audio.removeEventListener('error', onError);
  }, [dashUseStream]);
  const stationIdRef = useRef(stationId);
  stationIdRef.current = stationId;
  // Sound already running for this station (from a listener page, or this screen a
  // moment ago): taken over, not restarted, and shown as on. Until the station's
  // stream address is known it is left alone.
  const [adopted, setAdopted] = useState(false);
  useEffect(() => {
    if (!adopted) return;
    const t = setTimeout(() => setAdopted(false), 8000);
    return () => clearTimeout(t);
  }, [adopted]);
  const streamUrlRef = useRef<string | null>(null);
  streamUrlRef.current = dashUseStream ? dashStreamUrl : null;
  const isPlayingRef = useRef(isPlaying);
  isPlayingRef.current = isPlaying;

  // The engine always follows the station here, silently unless this computer
  // listens: it drives the level meters. With a stream, the stream is what is heard
  // and the engine only measures.
  const { volume, setVolume, muted, toggleMute, vuLevels, audioReady, soundRunning, initAudio: engineInit, playNextNow, setMonitor } = useAudioEngine(
    !musicAllowed ? null : audioAsset, realElapsed, !musicAllowed ? false : isPlaying,
    nextPreempt?.preempt_at ?? null,
    nextPreempt?.asset_id ?? null,
    preemptFadeMs,
    wsNowPlaying?.asset?.cue_in ?? 0,
    wsNowPlaying?.asset?.cue_out ?? 0,
    wsNowPlaying?.asset?.cross_start ?? 0,
    wsNowPlaying?.asset?.replay_gain_db ?? 0,
    wsNowPlaying?.next_asset?.id ?? null,
    wsNowPlaying?.next_asset?.cue_in ?? 0,
    wsNowPlaying?.next_asset?.replay_gain_db ?? 0,
    // Ready-made file addresses from the now-playing message: no round trip before the first sound
    wsNowPlaying?.asset?.audio_url ?? null,
    wsNowPlaying?.next_asset?.audio_url ?? null,
    serverStartedAt,
    wsNowPlaying?.change_fade_seconds ?? null,
    false, // started below, on the first click or key anywhere in this tab
    false, // silent until the speaker button: the meters move either way
    true,
    wsNowPlaying?.asset?.overlap_seconds,
    wsNowPlaying?.next_asset?.overlap_seconds,
    wsNowPlaying?.asset?.end_fade_seconds,
  );

  // ── Level meters always; sound on this computer only after the Listen button ──
  // Browsers let audio run only after a real tap, click or key in the page, so the
  // silent engine starts on the first one anywhere here (or at once where the
  // browser already allows it).
  // Listening is remembered across reloads (like Mute): the first tap anywhere turns
  // the sound back on, and until it has, the header shows the Listen button, never
  // the Mute control (it looked switched on over silence).
  // Only click and keydown: iPhone Safari allows no sound from touchstart or
  // pointerdown, and the page used to spend its one try there, so with listening
  // remembered no tap ever gave sound (Shmuel, 2026-11-17: silent for 3 minutes).
  // The taps are heard until sound really runs, and again after the phone took the
  // sound away (a call, the lock screen).
  const [listenHere, setListenHere] = useState(() => {
    try { return localStorage.getItem(LISTEN_KEY) === '1'; } catch { return false; }
  });
  const takeSoundRef = useRef<() => Promise<void>>(async () => {});
  // The stream element really plays, or is not needed right now
  const streamWanted = listenHere && dashUseStream && isPlaying;
  // On the server stream, sound is on when the stream element plays (taken over from
  // another page it plays before any tap here has started the meters)
  const soundLive = streamWanted ? streamSounding : soundRunning;
  useEffect(() => {
    if (soundLive && soundRunning) return;
    const start = (e: Event) => {
      // The Listen button does this itself (and lifts Mute); starting here first
      // re-drew the header before the button's own handler ran, which then never did
      if ((e.target as Element | null)?.closest?.('[data-listen-button]')) return;
      if (soundLive) { void engineInit().catch(() => {}); return; }   // only the meters are missing
      let wanted = false;
      try { wanted = localStorage.getItem(LISTEN_KEY) === '1'; } catch { /* none */ }
      void (wanted ? takeSoundRef.current() : engineInit()).catch(() => {});
    };
    const events = ['click', 'keydown'] as const;
    const remove = () => events.forEach(e => window.removeEventListener(e, start, true));
    events.forEach(e => window.addEventListener(e, start, true));
    return remove;
  }, [engineInit, soundLive, soundRunning]);
  useEffect(() => {
    // Where the browser already allows sound (a desktop that trusts the site): at once
    try {
      const probe = new AudioContext();
      if (probe.state === 'running') {
        let wanted = false;
        try { wanted = localStorage.getItem(LISTEN_KEY) === '1'; } catch { /* none */ }
        void (wanted ? takeSoundRef.current() : engineInit()).catch(() => {});
      }
      void probe.close();
    } catch { /* no Web Audio */ }
  }, [engineInit]);
  useEffect(() => { setMonitor(listenHere && !dashUseStream); }, [setMonitor, listenHere, dashUseStream]);

  // ── One control room tab makes sound at a time ──
  // A tab that switches sound on tells the others, and they go quiet (their meters
  // keep moving).
  const soundElsewhere = useRef(false);
  const holdsSound = useRef(false);
  const soundChannel = useRef<BroadcastChannel | null>(null);
  useEffect(() => {
    if (typeof BroadcastChannel === 'undefined') return;
    const ch = new BroadcastChannel('kbr-control-room-sound');
    soundChannel.current = ch;
    ch.onmessage = (e: MessageEvent) => {
      if (e.data?.type === 'taken') {
        soundElsewhere.current = true;
        if (holdsSound.current) {
          holdsSound.current = false;
          setListenHere(false); // the other tab now remembers listening
          const moved = 'Sound moved to another control room tab. Press the speaker here to hear it in this tab.';
          setStatusMessage(moved);
          // The status line only shows while nothing is on air: say it where it is seen
          setToast({ text: moved, type: 'warn' });
        }
      } else if (e.data?.type === 'released') {
        soundElsewhere.current = false;
      }
    };
    const onLeave = () => { if (holdsSound.current) ch.postMessage({ type: 'released' }); };
    window.addEventListener('pagehide', onLeave);
    return () => { onLeave(); window.removeEventListener('pagehide', onLeave); ch.close(); soundChannel.current = null; };
  }, []);
  const unlockLive = live.unlock;
  const takeSound = useCallback(async () => {
    soundElsewhere.current = false;
    if (!holdsSound.current) soundChannel.current?.postMessage({ type: 'taken' });
    holdsSound.current = true;
    setStatusMessage(null);
    setListenHere(true);
    try { localStorage.setItem(LISTEN_KEY, '1'); } catch { /* none */ }
    unlockLive();
    // Still inside the tap: the server stream starts here, or, with none known yet,
    // the stream element plays a moment of silence so it may start later by itself
    const el = dashStreamAudioRef.current;
    const url = streamUrlRef.current;
    if (url && isPlayingRef.current) {
      // (again when it was started a while ago and still gives no sound)
      const cur = streamRef.current;
      const stuck = !!cur && !streamSoundingRef.current && Date.now() - cur.startedAt > STREAM_RESTART_AFTER_MS;
      if (cur?.url !== url || el?.paused || stuck) startStream(url);
    } else if (el && !streamRef.current && !el.getAttribute('src')) {
      el.src = SILENT_WAV;
      el.play().then(() => { if (!streamRef.current) el.pause(); }).catch(() => {});
    }
    await engineInit();
  }, [engineInit, unlockLive, startStream]);
  takeSoundRef.current = takeSound;
  // The Listen button: sound on here, and an old Mute lifted (Listen must give sound)
  const listenPressed = useCallback(async () => {
    if (muted) toggleMute();
    await takeSound();
  }, [muted, toggleMute, takeSound]);
  // A stream that becomes known after the tap (the station moved to the server
  // stream, or another station was picked) starts by itself; one that is no longer
  // wanted stops, so a phone that only watches downloads nothing.
  useEffect(() => {
    if (adopted && (!dashStreamUrl || !isPlaying)) return;
    if (!streamWanted || !dashStreamUrl) { stopStream(); return; }
    if (adopted) setAdopted(false);
    if (streamRef.current?.url !== dashStreamUrl && (soundRunning || streamSoundingRef.current)) startStream(dashStreamUrl);
  }, [streamWanted, dashStreamUrl, soundRunning, startStream, stopStream, adopted, isPlaying]);
  // (after the effect above, which would otherwise stop what is taken over here)
  useEffect(() => {
    const run = adoptStationStream(stationId);
    if (!run) return;
    streamRef.current = { url: run.url, stream: run.stream, startedAt: Date.now() };
    setAdopted(true);
    setStreamSounding(stationStreamSounding());
    soundElsewhere.current = false;
    if (!holdsSound.current) soundChannel.current?.postMessage({ type: 'taken' });
    holdsSound.current = true;
    setListenHere(true);
    try { localStorage.setItem(LISTEN_KEY, '1'); } catch { /* none */ }
    usePlayerStore.getState().clear(stationId ?? undefined);
  }, [stationId]);
  // Leaving this screen while it gives sound: the sound carries on and the next page
  // takes it over (the Listen page, kept by ListenHost, with the bar at the bottom)
  const leavingRef = useRef({ listenHere, soundLive, stationName: '', title: '', artist: '' });
  leavingRef.current = {
    listenHere, soundLive,
    stationName: station?.name ?? '',
    title: displayTitle(wsNowPlaying?.asset?.title) ?? '',
    artist: displayTitle(wsNowPlaying?.asset?.artist) ?? '',
  };
  useEffect(() => () => {
    const id = stationIdRef.current;
    const was = leavingRef.current;
    const stream = streamRef.current?.stream;
    const streaming = !!stream && !!runningStationStream(id);
    if (id && was.listenHere && was.stationName && (streaming || was.soundLive)) {
      const store = usePlayerStore.getState();
      store.handOver(id, was.stationName, () => stopStationStream());
      store.setTrack(was.title ? { title: was.title, artist: was.artist || undefined } : null);
      if (streaming) releaseStationStream(stream);
      streamRef.current = null;
      return;
    }
    stopStream();
  }, [stopStream]);
  // An admin-pushed release reloaded this control room while it played: sound back on
  const resume = useResumeAfterRelease(takeSound, soundLive && listenHere);
  // Action buttons (Next, Start, play now) count as the click that starts the meters;
  // they never switch sound on by themselves
  const initAudio = useCallback(async () => { await engineInit(); }, [engineInit]);
  useEffect(() => {
    const el = dashStreamAudioRef.current;
    if (!el) return;
    el.muted = !listenHere || muted;
    el.volume = volume;
  }, [volume, muted, listenHere]);
  const setLiveLevel = live.setLevel;
  useEffect(() => { setLiveLevel(volume, muted); }, [setLiveLevel, volume, muted]);
  useEffect(() => { setLiveEnabled(audioReady && listenHere); }, [audioReady, listenHere]);
  const vuL = audioReady && isPlaying ? Math.round(vuLevels[0] * METER_SEGMENTS) : 0;
  const vuR = audioReady && isPlaying ? Math.round(vuLevels[1] * METER_SEGMENTS) : 0;

  // ── Queue derived data ─────────────────────────────────────
  const queueDates = useMemo(() => {
    const dates = new Set<string>();
    for (const e of queueEntries) {
      const ts = e.estimated_start || e.preempt_at;
      if (ts) dates.add(dayKey(ts, stationTz));
    }
    return Array.from(dates).sort();
  }, [queueEntries, stationTz]);

  const filteredQueueEntries = useMemo(() => {
    if (!queueDateFilter) return queueEntries;
    return queueEntries.filter(e => {
      const ts = e.estimated_start || e.preempt_at;
      if (!ts) return false;
      return dayKey(ts, stationTz) === queueDateFilter;
    });
  }, [queueEntries, queueDateFilter, stationTz]);

  // ── Actions ────────────────────────────────────────────────
  // Latest values for stable callbacks (panels are memoised; the clock ticks 5x a second).
  const latest = useRef({ stationId, queueEntries, insertBeforeId, busyId, initAudio });
  latest.current = { stationId, queueEntries, insertBeforeId, busyId, initAudio };

  const refreshQueue = useCallback(() => {
    const sid = latest.current.stationId;
    qc.invalidateQueries({ queryKey: ['queue', sid] });
    qc.invalidateQueries({ queryKey: ['play-log', sid] });
  }, [qc]);

  // The server pushes a message the moment the track on air changes. Refresh the
  // queue and played log right then instead of waiting for the next poll.
  const pushedTrack = wsNowPlaying ? `${wsNowPlaying.asset_id ?? ''}|${wsNowPlaying.started_at ?? ''}` : null;
  const lastPushedTrack = useRef<{ sid: string | null; track: string | null }>({ sid: null, track: null });
  useEffect(() => {
    const last = lastPushedTrack.current;
    lastPushedTrack.current = { sid: stationId, track: pushedTrack };
    // The first message after opening a station is the current state, not a change.
    if (last.sid !== stationId || last.track === null || pushedTrack === null) return;
    if (last.track === pushedTrack) return;
    qc.invalidateQueries({ queryKey: ['last-played', stationId] });
    // After Next, the answer to the click has already put the new queue in place.
    const cached = qc.getQueryData<{ now_playing?: { asset_id?: string } | null }>(['queue', stationId]);
    const pushedId = pushedTrack.split('|')[0];
    if (pushedId && cached?.now_playing?.asset_id === pushedId) {
      qc.invalidateQueries({ queryKey: ['play-log', stationId] });
      return;
    }
    refreshQueue();
  }, [pushedTrack, stationId, refreshQueue, qc]);

  const send = useCallback(async (asset: CtlAsset, how: SendHow) => {
    const { stationId: sid, queueEntries: entries, insertBeforeId: targetId, busyId: busy } = latest.current;
    if (!sid || busy) return;
    setBusyId(asset.id);
    const target = how === 'insert' ? entries.find(e => e.id === targetId && e.status === 'pending') : undefined;
    // Show the song in the queue right away; the server's answer replaces this
    // row when the request settles, and a failure puts the old queue back.
    const queueKey = ['queue', sid];
    let before: QueueDataLike | undefined;
    if (how !== 'now' && how !== 'mix') {
      await qc.cancelQueries({ queryKey: queueKey });
      before = qc.getQueryData<QueueDataLike>(queueKey);
      if (before) {
        const where = how === 'end' ? 'end' : target ? { beforeId: target.id } : 'next';
        qc.setQueryData(queueKey, addQueuePlaceholder(before, sid, { ...asset }, where));
      }
    }
    try {
      if (how === 'insert') {
        if (target) {
          // The marker sits before `target`, so the new item goes after the entry above it.
          const pendingRows = entries.filter(e => e.status === 'pending');
          const above = pendingRows[pendingRows.findIndex(e => e.id === target.id) - 1];
          await insertAt(sid, asset.id, above ? above.id : null);
          setToast({ text: `Inserted “${asset.title}”`, type: 'ok' });
        } else {
          await queueNext(sid, asset.id);
          setInsertBeforeId(null);
          setToast({ text: `That spot already passed, so “${asset.title}” will play next.`, type: 'warn' });
        }
      } else if (how === 'mix') {
        latest.current.initAudio();
        await mixOver(sid, asset.id);
        setToast({ text: `Playing over the current item: “${asset.title}”`, type: 'ok' });
      } else if (how === 'now') {
        latest.current.initAudio();
        await fireNow(sid, asset.id);
        setToast({ text: `On air now: “${asset.title}”`, type: 'ok' });
      } else if (how === 'next') {
        await queueNext(sid, asset.id);
        setToast({ text: `Playing next: “${asset.title}”`, type: 'ok' });
      } else {
        await queueEnd(sid, asset.id);
        setToast({ text: `Added to the end: “${asset.title}”`, type: 'ok' });
      }
    } catch (err: any) {
      if (before) qc.setQueryData(queueKey, before);
      const msg = err?.response?.data?.detail || err?.response?.data?.message;
      if (err?.response?.status !== 403) setToast({ text: typeof msg === 'string' ? msg : `Could not send “${asset.title}”. Check the connection and try again.`, type: 'error' });
    } finally {
      setBusyId(null);
      refreshQueue();
    }
  }, [qc, refreshQueue]);

  const onMutError = useCallback((err?: unknown) => {
    // A refusal is not a connection problem; the app-wide notice explains it.
    if ((err as { response?: { status?: number } } | undefined)?.response?.status === 403) return;
    setToast({ text: 'That did not go through. Check the connection and try again.', type: 'error' });
  }, []);

  // Next is heard here at once: the item the server will move to (it says which) is
  // brought up before the answer arrives; listeners change when the server announces it.
  // Next never skips a time check, weather or sponsorship on air (the server keeps it)
  const onAirEntry = queueEntries.find(e => e.status === 'playing');
  const onAirTimed = !!onAirEntry && (!!onAirEntry.preempt_at || onAirEntry.source === 'ad_slot' || onAirEntry.source === 'block');
  const nextNow = useRef({ nextAsset: skipAsset, nextEntryId: skipEntryId, serverStartedAt, onAirTimed });
  nextNow.current = { nextAsset: skipAsset, nextEntryId: skipEntryId, serverStartedAt, onAirTimed };
  const handleNext = useCallback((fade?: SkipFade) => {
    initAudio();
    const { nextAsset: upNext, nextEntryId: upNextId, serverStartedAt: onAirSince, onAirTimed: timedOnAir } = nextNow.current;
    // Right after an automatic change the server keeps the incoming item (it is still
    // fading in), so nothing is played ahead then.
    const justChanged = onAirSince ? serverNow() - new Date(onAirSince).getTime() < 4500 : true;
    if (upNext && upNextId && !justChanged && !timedOnAir) {
      playNextNow(
        { id: upNext.id, title: upNext.title, artist: upNext.artist ?? null, asset_type: upNext.asset_type,
          category: upNext.category ?? null, duration: upNext.duration ?? null },
        fade ? SKIP_FADE_SECONDS[fade] : SKIP_FADE_SECONDS.fade,
      );
    }
    skipMut.mutate(fade, {
      onError: onMutError,
      onSuccess: (data: any) => {
        // The server keeps an item that has only just started (a timed item such as the
        // weather, or the song an automatic change is fading in): one press, one change
        if (data?.protected) setToast({ text: 'The time, weather and sponsorships always play to the end. Next works again after it.', type: 'warn' });
        else if (data?.kept_incoming) setToast({ text: 'That item just started, so it keeps playing. Press Next again to skip it.', type: 'warn' });
      },
    });
  }, [initAudio, skipMut, onMutError, playNextNow]);

  // Loop: the item on air repeats until Loop is switched off, Next, or a play-now.
  const looping = !!(queueData as { loop_asset_id?: string | null } | undefined)?.loop_asset_id;
  const [loopBusy, setLoopBusy] = useState(false);
  const toggleLoop = useCallback(async () => {
    const sid = latest.current.stationId;
    if (!sid) return;
    setLoopBusy(true);
    try {
      const res = await loopCurrent(sid, !looping);
      if (!applyActionQueue(qc, sid, res.queue)) refreshQueue();
      setToast(res.loop
        ? { text: 'Loop is on: this item repeats until you switch Loop off or press Next.', type: 'warn' }
        : { text: 'Loop is off. This play finishes, then the queue carries on.', type: 'ok' });
    } catch (err: any) {
      const msg = err?.response?.data?.detail;
      setToast({ text: typeof msg === 'string' ? msg : 'Loop did not go through. Check the connection and try again.', type: 'error' });
    } finally {
      setLoopBusy(false);
    }
  }, [looping, qc, refreshQueue]);

  // HS and Spots mode switches, Panic (KBR Studio's control panel)
  // Until the queue (which carries the modes) has loaded, the real state is unknown:
  // the buttons stay greyed so nobody presses them on a wrong state
  const knownModes = (queueData as { modes?: StationModes } | undefined)?.modes;
  const modes = knownModes ?? { hard_start: true, spots: true };
  const [modesBusy, setModesBusy] = useState(false);
  // A mode change made in another control room arrives on the now-playing channel
  useEffect(() => {
    const onModes = (ev: Event) => {
      const d = (ev as CustomEvent<{ station_id?: string; hard_start?: boolean; spots?: boolean }>).detail;
      if (!d?.station_id || typeof d.hard_start !== 'boolean' || typeof d.spots !== 'boolean') return;
      const next = { hard_start: d.hard_start, spots: d.spots };
      qc.setQueryData(['queue', d.station_id], (old: unknown) => (old && typeof old === 'object' ? { ...old, modes: next } : old));
    };
    window.addEventListener('kbr-modes', onModes);
    return () => window.removeEventListener('kbr-modes', onModes);
  }, [qc]);
  const changeModes = useCallback(async (next: Partial<StationModes>) => {
    const sid = latest.current.stationId;
    if (!sid) return;
    setModesBusy(true);
    try {
      const res = await setStationModes(sid, next);
      if (!applyActionQueue(qc, sid, res.queue)) refreshQueue();
      if (next.hard_start !== undefined) {
        setToast(res.modes.hard_start
          ? { text: 'HS is on: the time check and hard blocks start on the second again.', type: 'ok' }
          : { text: 'HS is off: timed items now wait for the song on air to end.', type: 'warn' });
      } else {
        setToast(res.modes.spots
          ? { text: 'Spots mode is on: scheduled sponsorships play again.', type: 'ok' }
          : { text: 'Spots mode is off: no sponsorships go on the air until you switch it back on.', type: 'warn' });
      }
    } catch (err: any) {
      const msg = err?.response?.data?.detail;
      setToast({ text: typeof msg === 'string' ? msg : 'That did not go through. Check the connection and try again.', type: 'error' });
    } finally {
      setModesBusy(false);
    }
  }, [qc, refreshQueue]);
  const doPanic = useCallback(async (armedOn: string) => {
    const sid = latest.current.stationId;
    // Armed on another station than the one now shown: cut nothing
    if (!sid || sid !== armedOn) return;
    setModesBusy(true);
    try {
      const res = await panicRequest(sid);
      if (!applyActionQueue(qc, sid, res.queue)) refreshQueue();
      // The server logs a "Panic pressed" alert: show it in the Errors tab now (F-MODES-42)
      void qc.invalidateQueries({ queryKey: ['alerts'] });
      setToast(res.message === 'Skipped'
        ? { text: 'Panic: cut what was on air and started the next item.', type: 'warn' }
        : { text: `Panic: ${res.message || 'the server did not say what happened. Check what is on air.'}`, type: 'error' });
    } catch (err: any) {
      const msg = err?.response?.data?.detail;
      setToast({ text: typeof msg === 'string' ? msg : 'Panic did not go through. Check the connection and try again.', type: 'error' });
    } finally {
      setModesBusy(false);
    }
  }, [qc, refreshQueue]);
  const modeProps = {
    stationId: stationId ?? null,
    hardStart: modes.hard_start, spots: modes.spots, busy: modesBusy || !stationId, modesKnown: !!knownModes,
    canChange: mayOperate,
    onHardStart: () => changeModes({ hard_start: !modes.hard_start }),
    onSpots: () => changeModes({ spots: !modes.spots }),
    onPanic: doPanic,
  };

  const handleStart = useCallback(() => {
    if (!stationId) { setStatusMessage('No station available'); return; }
    setStatusMessage(null);
    initAudio();
    startMut.mutate(undefined, {
      onSuccess: (data: any) => {
        if (data?.now_playing === null || data?.now_playing === undefined) {
          setStatusMessage(data?.message || 'The queue is empty. Add something from the library first.');
        }
      },
      onError: (err: any) => setStatusMessage(err?.response?.data?.message || 'Could not start playback'),
    });
  }, [stationId, initAudio, startMut]);

  // Arrows show the same reminders as drag-and-drop (rule and category conflicts)
  const onMoved = useCallback((data: { warnings?: string[] } | undefined) => {
    if (data?.warnings?.length) setToast({ text: data.warnings.join(' | '), type: 'warn' });
  }, []);
  const moveUp = useCallback((id: string) => moveUpMut.mutate(id, { onSuccess: onMoved, onError: onMutError }), [moveUpMut, onMoved, onMutError]);
  const moveDown = useCallback((id: string) => moveDownMut.mutate(id, { onSuccess: onMoved, onError: onMutError }), [moveDownMut, onMoved, onMutError]);
  const remove = useCallback((id: string) => removeMut.mutate(id, { onError: onMutError }), [removeMut, onMutError]);
  const reorder = useCallback((entryId: string, newPosition: number) => {
    reorderMut.mutate({ entryId, newPosition }, {
      onSuccess: (data) => {
        if (data.warnings?.length) setToast({ text: data.warnings.join(' | '), type: 'warn' });
      },
      onError: onMutError,
    });
  }, [reorderMut, onMutError]);

  const armInsert = useCallback((entryId: string | null) => {
    setInsertBeforeId(entryId);
    if (entryId) {
      // On a phone the library is a separate tab; on desktop make sure it is showing.
      setPane('library');
      setSideTab('library');
    }
  }, [setSideTab]);
  const cancelInsert = useCallback(() => {
    setInsertBeforeId(null);
    setPane(p => (p === 'library' || p === 'buttons' ? 'queue' : p));
  }, []);

  // Pre-listen (PFL): one off-air player shared by the cart and the library
  const pfl = usePfl();

  // Off-air time + weather preview: played in the pre-listen deck (time, then
  // weather, as one item with play/pause, seek and stop), never on air
  const [weatherLoading, setWeatherLoading] = useState(false);
  const playClips = pfl.playClips;
  const previewWeather = useCallback(async () => {
    if (weatherLoading) return;
    setWeatherLoading(true);
    try {
      const data = await weatherPreviewMut.mutateAsync();
      const clips: PflClip[] = [];
      if (data.time_url) clips.push({ url: data.time_url, label: 'Time' });
      if (data.weather_url) clips.push({ url: data.weather_url, label: 'Weather' });
      if (clips.length === 0) setToast({ text: 'No weather announcement is available right now.', type: 'warn' });
      else void playClips(WEATHER_PFL_ID, 'Time & weather', clips);
    } catch {
      setToast({ text: 'Could not load the weather preview.', type: 'error' });
    } finally {
      setWeatherLoading(false);
    }
  }, [weatherLoading, weatherPreviewMut, playClips]);

  // ── Keyboard shortcuts (desktop) ───────────────────────────
  const keyActions = useRef({ handleNext, cancelInsert, isPlaying, mayOperate });
  keyActions.current = { handleNext, cancelInsert, isPlaying, mayOperate };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      const typing = !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable);
      // Keys pressed in the pre-listen deck belong to it: N there must never skip on air
      if (el?.closest?.('[data-testid="pfl-deck"]')) return;
      if (e.key === 'Escape') {
        if (typing) (el as HTMLElement).blur();
        keyActions.current.cancelInsert();
        return;
      }
      if (typing) return;
      if (e.key === '/') {
        e.preventDefault();
        setPane('library');
        setSideTab('library');
        // Focus at once when the box is already on screen, so fast typing is not lost.
        if (searchRef.current) searchRef.current.focus();
        else setTimeout(() => searchRef.current?.focus(), 0);
      } else if ((e.key === 'n' || e.key === 'N') && !e.repeat && keyActions.current.isPlaying && keyActions.current.mayOperate) {
        keyActions.current.handleNext('hard');
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setSideTab]);

  // ── Panels ─────────────────────────────────────────────────
  const transport: TransportProps = {
    stations: stations,
    stationId: stationId,
    onStation: setSelectedStationId,
    isPlaying: isPlaying,
    nowAsset: nowAsset,
    onNowSong: nowAsset ? () => setSongTarget({ asset: nowAsset, onAir: true, source: queueEntries.find((e) => e.status === 'playing')?.source ?? null }) : undefined,
    nextAsset: nextAsset,
    elapsed: shownElapsed,
    remaining: remaining,
    duration: duration,
    cutAt: cutAtIso,
    introSeconds: introSeconds,
    onStart: handleStart,
    starting: startMut.isPending,
    statusMessage: queueNotice ?? statusMessage,
    onNext: handleNext,
    nextBusy: skipMut.isPending,
    looping: looping,
    onLoop: toggleLoop,
    loopBusy: loopBusy,
    audio: { audioReady: listenHere && soundLive, starting: listenHere && soundRunning && !soundLive && streamStarting, initAudio: listenPressed, volume, setVolume, muted, toggleMute, vuL, vuR },
    stationTz: stationTz,
    liveShow: liveShow ? { id: liveShow.id, title: liveShow.title } : null,
  };
  const noStation = !stationId;
  const queuePanel = (
    <QueuePanel
      entries={filteredQueueEntries}
      notice={queueNotice}
      loadedCount={queueEntries.length}
      totalCount={queueData?.total_all ?? queueEntries.length}
      totalDuration={queueData?.total_queue_duration_seconds ?? queueData?.queue_duration_seconds ?? 0}
      dates={queueDates}
      dateFilter={queueDateFilter}
      onDateFilter={setQueueDateFilter}
      onNeedAll={loadWholeQueue}
      onAtTop={loadFirstHours}
      stationTz={stationTz}
      insertBeforeId={insertBeforeId}
      onArmInsert={armInsert}
      onMoveUp={moveUp}
      onMoveDown={moveDown}
      onRemove={remove}
      onReorder={reorder}
      lastPlayed={lastPlayedMap?.last_played}
      onSong={openRowSong}
    />
  );
  const hotButtons = (wide: boolean, bar = false) => (
    <HotButtons
      pins={pins}
      sets={cart.sets}
      setIndex={cart.set}
      pageIndex={cart.page}
      onSelectSet={selectSet}
      onSelectPage={selectPage}
      onAddSet={addSet}
      onRenameSet={renameSet}
      onDeleteSet={deleteSet}
      onTogglePin={togglePin}
      onSend={send}
      busyId={busyId}
      fireMode={fireMode}
      onFireMode={setFireMode}
      inserting={insertLabel !== null}
      onCancelInsert={cancelInsert}
      disabled={noStation}
      onPreviewWeather={previewWeather}
      weatherLoading={weatherLoading}
      pfl={pfl}
      wide={wide}
      bar={bar}
    />
  );
  const libraryPanel = (
    <LibraryPanel
      insertLabel={insertLabel}
      onCancelInsert={cancelInsert}
      onSend={send}
      busyId={busyId}
      pinnedIds={pinnedIds}
      onTogglePin={togglePin}
      searchRef={searchRef}
      disabled={noStation}
      pflId={pfl.playingId}
      onPfl={pfl.toggle}
    />
  );
  // Drag bar between Up next and the lower pane: the share of the height Up next gets
  const splitBoxRef = useRef<HTMLDivElement>(null);
  const [split, setSplitStored] = useStored<number>('kbr-cr-split', SPLIT_DEFAULT);
  const [splitLive, setSplitLive] = useState<number | null>(null);
  const clampSplit = (v: number) => Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, v));
  const setSplit = (v: number) => { setSplitLive(null); setSplitStored(clampSplit(v)); };
  const splitAt = (clientY: number) => {
    const box = splitBoxRef.current?.getBoundingClientRect();
    return box && box.height > 0 ? clampSplit((clientY - box.top) / box.height) : null;
  };
  const onSplitDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    const v = splitAt(e.clientY);
    if (v !== null) setSplitLive(v);
  };
  const onSplitMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (splitLive === null) return;
    const v = splitAt(e.clientY);
    if (v !== null) setSplitLive(v);
  };
  const onSplitUp = () => { if (splitLive !== null) setSplit(splitLive); };
  const onSplitKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowUp') { e.preventDefault(); setSplit(split - 0.05); }
    if (e.key === 'ArrowDown') { e.preventDefault(); setSplit(split + 0.05); }
  };
  const splitShown = clampSplit(splitLive ?? split);

  const sidePanel = (tab: Pane) =>
    tab === 'library' ? libraryPanel
    : tab === 'live' ? <LivePanel stationId={stationId} stationTz={stationTz} />
    : tab === 'log' ? <LogPanel stationId={stationId} stationTz={stationTz} />
    : tab === 'errors' ? <ErrorsPanel stationId={stationId} stationTz={stationTz} canSee={isManager(user?.role)} />
    : <SchedulePanel stationId={stationId} />;

  return (
    <div className="qn cr-root h-full flex flex-col select-none overflow-hidden" data-testid="control-room">
      {resume.asking && <ResumePrompt onTap={resume.tap} />}

      {toast && (
        <div role="status" data-testid="toast"
          className={`fixed left-1/2 -translate-x-1/2 z-[60] w-[calc(100%-1.5rem)] max-w-md px-3 py-2.5 rounded-lg shadow-xl text-sm flex items-start gap-2
            bottom-[calc(4.5rem+env(safe-area-inset-bottom))] lg:bottom-10
            ${toast.type === 'ok' ? 'bg-green-800 border border-green-500 text-green-50'
              : toast.type === 'warn' ? 'bg-amber-900 border border-amber-500 text-amber-50'
              : 'bg-red-900 border border-red-500 text-red-50'}`}>
          <span className="flex-1">{toast.text}</span>
          <button type="button" onClick={() => setToast(null)} aria-label="Dismiss" className="shrink-0 w-6 h-6 opacity-70 hover:opacity-100">✕</button>
        </div>
      )}

      {songTarget && stationId && !songTarget.itemOptions && isSponsorship(songTarget) && (
        <SponsorPanel target={{ ...songTarget, onAir: !!nowAsset && nowAsset.id === songTarget.asset.id }} stationId={stationId}
          canSkip={canChange(user?.role, '/admin/dashboard')} canManage={isManager(user?.role)}
          canOpen={(path) => canAccess(user?.role, path)}
          onItemOptions={() => setSongTarget({ ...songTarget, itemOptions: true })}
          onClose={closeSong} onToast={songToast} />
      )}
      {songTarget && (songTarget.itemOptions || !stationId || !isSponsorship(songTarget)) && (
        <SongPanel target={{ ...songTarget, onAir: !!nowAsset && nowAsset.id === songTarget.asset.id }} stationId={stationId ?? null} stationName={station?.name ?? null} canEdit={isManager(user?.role)}
          onClose={closeSong} onToast={songToast} />
      )}

      <TransportBar {...transport} landscape={phoneLandscape}
        modeButtons={phoneLandscape ? <ModeButtons p={modeProps} stacked={false} grid /> : undefined}
        pflDeck={phoneLandscape ? <PflDeck pfl={pfl} column /> : undefined} />

      {isDesktop ? (
        /* ═══ Desktop, laid out like the old program: log over songs, buttons on the right, cart along the bottom ═══ */
        <>
          <div className="flex-1 min-h-0 flex">
            <div ref={splitBoxRef} className="flex-1 min-w-0 min-h-0 flex flex-col">
              <main className="min-h-0" data-testid="queue-area" style={{ flex: `${splitShown} 1 0` }}>{queuePanel}</main>
              <div
                role="separator"
                aria-orientation="horizontal"
                aria-label="Drag to make Up next taller or shorter"
                aria-valuemin={Math.round(SPLIT_MIN * 100)}
                aria-valuemax={Math.round(SPLIT_MAX * 100)}
                aria-valuenow={Math.round(splitShown * 100)}
                tabIndex={0}
                title="Drag to make Up next taller or shorter (double-click to reset)"
                data-testid="split-handle"
                onPointerDown={onSplitDown}
                onPointerMove={onSplitMove}
                onPointerUp={onSplitUp}
                onPointerCancel={onSplitUp}
                onDoubleClick={() => setSplit(SPLIT_DEFAULT)}
                onKeyDown={onSplitKey}
                className="qn-splitter shrink-0 h-2 cursor-row-resize touch-none"
              />
              <div className="min-h-0" style={{ flex: `${1 - splitShown} 1 0` }}>{sidePanel(sideTab)}</div>
            </div>
            {/* The whole column fits a 900 px high window (buttons are a little lower under 1000 px);
                on a shorter one it scrolls, and "more" at the bottom says so until the end is reached */}
            <aside ref={controlsRef} onScroll={checkControlsMore}
              className="w-28 xl:w-32 shrink-0 min-h-0 overflow-y-auto bg-[#c0c0c0] p-1.5 flex flex-col gap-1 border-l-2 border-white [&>*]:shrink-0" aria-label="Controls">
              {/* Loop, Go next and the fades stay at the top when a short window makes the column
                  scroll (a scrolled column hid half of Loop under the transport, 2026-11-18) */}
              <div className="sticky top-0 z-10 -mt-1.5 pt-1.5 pb-1 bg-[#c0c0c0]" data-testid="cr-transport-pinned">
                <TransportButtons p={transport} stacked />
              </div>
              <div className="h-1 [@media(max-height:1000px)]:hidden" />
              {/* HS, Spots mode, Panic and DB right under the transport, so they are on screen even on a
                  1280x720 window (F-MODES-54); the lower-pane tabs come after and may scroll */}
              <ModeButtons p={modeProps} stacked />
              <Link to="/admin/databank" className="qn-btn h-8 [@media(max-height:1000px)]:h-7 text-sm flex items-center justify-center" title="The library as tables: songs, jingles, spots, blocks, log by day"
                data-testid="cr-databank">DB</Link>
              <div className="h-2 [@media(max-height:1000px)]:h-1" />
              <div className="flex flex-col gap-1" role="tablist" aria-label="Lower pane">
                {SIDE_TABS.map(t => (
                  <button key={t.id} type="button" role="tab" aria-selected={sideTab === t.id} onClick={() => setSideTab(t.id)}
                    className={`qn-btn h-8 [@media(max-height:1000px)]:h-7 text-sm ${t.id === 'live' && liveShow ? '!text-red-700' : ''}`}>
                    {t.id === 'live' && liveShow ? '● Live' : t.label}
                  </button>
                ))}
              </div>
              {controlsMore && (
                <button type="button" tabIndex={-1} data-testid="cr-controls-more" title="More buttons below: scroll or press here"
                  onClick={() => controlsRef.current?.scrollTo({ top: controlsRef.current.scrollHeight, behavior: 'smooth' })}
                  className="sticky bottom-0 z-10 -mt-6 -mb-1.5 h-5 text-[11px] font-bold leading-none bg-[#000080] text-white border-t border-white">
                  ▼ more
                </button>
              )}
            </aside>
          </div>
          <PflDeck pfl={pfl} />
          <div className="h-[150px] xl:h-[170px] shrink-0">{hotButtons(false, true)}</div>
        </>
      ) : (
        /* ═══ Phone / tablet: one pane + bottom tabs (beside the transport in landscape, see classic.css) ═══ */
        <div className="cr-side flex-1 min-h-0 flex flex-col">
          <div className="flex-1 min-h-0 flex flex-col">
            {/* Sideways, HS, Spots and Panic sit under Go next in the left column */}
            {!phoneLandscape && (pane === 'log' || pane === 'schedule' || pane === 'errors') && (
              <ModeButtons p={modeProps} stacked={false} />
            )}
            {(pane === 'log' || pane === 'schedule' || pane === 'errors') && (
              <div className="shrink-0 flex gap-1 p-1 bg-[#c0c0c0]">
                {(['log', 'schedule', 'errors'] as Pane[]).map(t => (
                  <button key={t} type="button" onClick={() => setPane(t)} aria-pressed={pane === t}
                    className="qn-btn cr-tap flex-1 h-9 text-sm">
                    {t === 'log' ? 'Played' : t === 'schedule' ? 'Schedule' : 'Errors'}
                  </button>
                ))}
                <Link to="/admin/databank?tab=log" className="qn-btn cr-tap flex-1 h-9 text-sm flex items-center justify-center" data-testid="cr-databank">Databank</Link>
              </div>
            )}
            <div className="flex-1 min-h-0">
              {pane === 'queue' ? queuePanel : pane === 'buttons' ? hotButtons(true) : sidePanel(pane)}
            </div>
          </div>
          {!phoneLandscape && <PflDeck pfl={pfl} compact />}
          <nav className="cr-tabs shrink-0 flex gap-1 px-1 pt-1 bg-[#c0c0c0] border-t-2 border-white pb-[max(0.25rem,env(safe-area-inset-bottom))]" aria-label="Control room sections">
            {MOBILE_TABS.map(t => {
              const active = pane === t.id || (t.id === 'log' && (pane === 'schedule' || pane === 'errors'));
              return (
                <button key={t.id} type="button" onClick={() => setPane(t.id)} aria-current={active ? 'page' : undefined}
                  className={`qn-btn flex-1 h-12 flex flex-col items-center justify-center gap-0.5 text-[11px]
                    ${active ? '!bg-[#ffff80]' : ''}
                    ${t.id === 'live' && liveShow ? '!text-red-700' : ''}`}>
                  <span className="text-lg leading-none" aria-hidden="true">{t.icon}</span>
                  {t.label}
                </button>
              );
            })}
          </nav>
        </div>
      )}

      {isDesktop && (
        <footer className="shrink-0 h-5 px-2 flex items-center justify-between gap-4 bg-[#c0c0c0] border-t border-[#808080] text-[11px] text-black">
          <span className="flex items-center gap-3">
            <span>Shortcuts: <b>N</b> next · <b>/</b> search · <b>Esc</b> cancel insert</span>
            <button type="button" onClick={() => window.dispatchEvent(new CustomEvent(REPORT_BUG_EVENT))}
              className="underline hover:text-red-700">🐞 Report a bug</button>
          </span>
          <span className="truncate">{station?.name ?? ''}{station?.timezone ? ` · ${station.timezone}` : ''}</span>
          <span className="truncate">{user?.email ?? ''}{user?.role ? ` (${user.role})` : ''}</span>
        </footer>
      )}
    </div>
  );
}
