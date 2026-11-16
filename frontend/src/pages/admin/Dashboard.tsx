import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../../hooks/useAuth';
import { useStations } from '../../hooks/useStations';
import {
  useQueue, useLastPlayed, useSkipCurrent, useRemoveFromQueue, useStartPlayback,
  useMoveUp, useMoveDown, useWeatherPreview, useReorderDnd,
  QUEUE_ALL_ROWS, QUEUE_FIRST_ROWS,
} from '../../hooks/useQueue';
import { useLiveShows } from '../../hooks/useLiveShows';
import { useLiveAudio } from '../../hooks/useLiveAudio';
import { useAudioEngine } from '../../hooks/useAudioEngine';
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
import type { QueueRow } from './controlroom/QueuePanel';
import { nextCut } from './controlroom/timing';
import LibraryPanel from './controlroom/LibraryPanel';
import HotButtons from './controlroom/HotButtons';
import { usePfl } from './controlroom/pfl';
import { LivePanel, LogPanel, SchedulePanel } from './controlroom/SidePanels';
import { dayKey, isValidTz, useHotButtons, useMediaQuery, useStored } from './controlroom/shared';
import type { CtlAsset, FireMode, SendHow } from './controlroom/shared';
import { ResumePrompt, useResumeAfterRelease } from '../../components/audio/ResumeAfterRelease';

type Pane = 'queue' | 'buttons' | 'library' | 'live' | 'log' | 'schedule';
type SideTab = 'library' | 'live' | 'log' | 'schedule';

interface Toast { text: string; type: 'ok' | 'warn' | 'error' }

const SIDE_TABS: Array<{ id: SideTab; label: string }> = [
  { id: 'library', label: 'Songs' },
  { id: 'live', label: 'Go live' },
  { id: 'log', label: 'Played' },
  { id: 'schedule', label: 'Schedule' },
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

export default function Dashboard() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const isDesktop = useMediaQuery('(min-width: 1024px)');
  const { data: stationsData } = useStations();
  const stations = useMemo(() => stationsData?.stations ?? [], [stationsData]);

  // ── Station selection (remembered in this browser) ─────────
  const [selectedStationId, setSelectedStationId] = useState<string | null>(() => {
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
  const [fireMode, setFireMode] = useStored<FireMode>('kbr_ctl_fire_mode', 'now');
  const [insertBeforeId, setInsertBeforeId] = useState<string | null>(null);
  const [queueDateFilter, setQueueDateFilter] = useState('');
  useEffect(() => { if (queueDateFilter) loadWholeQueue(); }, [queueDateFilter, loadWholeQueue]);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
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
        const el = Math.min(Math.max(0, (Date.now() - new Date(serverStartedAt).getTime()) / 1000), serverDuration);
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
  const remaining = isPlaying ? Math.max(0, duration - realElapsed) : 0;
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

  const dashStreamUrl = wsNowPlaying?.stream_url || null;
  const [streamFailed, setStreamFailed] = useState(false);
  const dashUseStream = !!dashStreamUrl && !streamFailed;
  const dashStreamAudioRef = useRef<HTMLAudioElement>(null);
  useEffect(() => {
    const audio = dashStreamAudioRef.current;
    if (!audio || !dashUseStream || !isPlaying) return;
    audio.src = dashStreamUrl!;
    audio.play().catch(() => setStreamFailed(true));
    return () => { audio.pause(); audio.src = ''; };
  }, [dashUseStream, isPlaying, dashStreamUrl]);
  useEffect(() => {
    const audio = dashStreamAudioRef.current;
    if (!audio || !dashUseStream) return;
    const onError = () => setStreamFailed(true);
    audio.addEventListener('error', onError);
    return () => audio.removeEventListener('error', onError);
  }, [dashUseStream]);

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
    null, null,
    serverStartedAt,
    wsNowPlaying?.change_fade_seconds ?? null,
    false, // started below, on the first click or key anywhere in this tab
    false, // silent until the speaker button: the meters move either way
  );

  // ── Level meters always; sound on this computer only after the speaker button ──
  // Browsers let audio run only after the person has clicked or typed in the page,
  // so the silent engine starts on the first click or key anywhere here (or at once
  // where the browser already allows it).
  const [listenHere, setListenHere] = useState(false);
  useEffect(() => {
    let done = false;
    const start = () => {
      if (done) return;
      done = true;
      remove();
      void engineInit().catch(() => {});
    };
    const events = ['pointerdown', 'keydown', 'touchstart'] as const;
    const remove = () => events.forEach(e => window.removeEventListener(e, start, true));
    events.forEach(e => window.addEventListener(e, start, true));
    try {
      const probe = new AudioContext();
      if (probe.state === 'running') start();
      void probe.close();
    } catch { /* no Web Audio */ }
    return remove;
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
          setListenHere(false);
          setStatusMessage('Sound moved to another control room tab. Press the speaker here to hear it in this tab.');
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
    unlockLive();
    await engineInit();
  }, [engineInit, unlockLive]);
  // An admin-pushed release reloaded this control room while it played: sound back on
  const resume = useResumeAfterRelease(takeSound, dashUseStream ? true : soundRunning && listenHere);
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
  const vuL = audioReady && isPlaying ? Math.round(vuLevels[0] * 30) : 0;
  const vuR = audioReady && isPlaying ? Math.round(vuLevels[1] * 30) : 0;

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
  const nextNow = useRef({ nextAsset: skipAsset, nextEntryId: skipEntryId, serverStartedAt });
  nextNow.current = { nextAsset: skipAsset, nextEntryId: skipEntryId, serverStartedAt };
  const handleNext = useCallback((fade?: SkipFade) => {
    initAudio();
    const { nextAsset: upNext, nextEntryId: upNextId, serverStartedAt: onAirSince } = nextNow.current;
    // Right after an automatic change the server keeps the incoming item (it is still
    // fading in), so nothing is played ahead then.
    const justChanged = onAirSince ? Date.now() - new Date(onAirSince).getTime() < 4500 : true;
    if (upNext && upNextId && !justChanged) {
      playNextNow(
        { id: upNext.id, title: upNext.title, artist: upNext.artist ?? null, asset_type: upNext.asset_type,
          category: upNext.category ?? null, duration: upNext.duration ?? null },
        fade ? SKIP_FADE_SECONDS[fade] : SKIP_FADE_SECONDS.fade,
      );
    }
    skipMut.mutate(fade, { onError: onMutError });
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
      if (res.queue?.entries) qc.setQueryData(['queue', sid], res.queue);
      else refreshQueue();
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

  const moveUp = useCallback((id: string) => moveUpMut.mutate(id, { onError: onMutError }), [moveUpMut, onMutError]);
  const moveDown = useCallback((id: string) => moveDownMut.mutate(id, { onError: onMutError }), [moveDownMut, onMutError]);
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

  // Off-air time + weather preview
  const [weatherState, setWeatherState] = useState<'idle' | 'loading' | 'playing'>('idle');
  const previewWeather = useCallback(async () => {
    if (weatherState !== 'idle') return;
    setWeatherState('loading');
    try {
      const data = await weatherPreviewMut.mutateAsync();
      const urls = [data.time_url, data.weather_url].filter(Boolean) as string[];
      if (urls.length === 0) {
        setToast({ text: 'No weather announcement is available right now.', type: 'warn' });
        setWeatherState('idle');
        return;
      }
      setWeatherState('playing');
      for (const url of urls) {
        await new Promise<void>((resolve) => {
          const a = new Audio(url);
          a.onended = () => resolve();
          a.onerror = () => resolve();
          a.play().catch(() => resolve());
        });
      }
    } catch {
      setToast({ text: 'Could not load the weather preview.', type: 'error' });
    }
    setWeatherState('idle');
  }, [weatherState, weatherPreviewMut]);

  // ── Keyboard shortcuts (desktop) ───────────────────────────
  const keyActions = useRef({ handleNext, cancelInsert, isPlaying });
  keyActions.current = { handleNext, cancelInsert, isPlaying };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const el = e.target as HTMLElement | null;
      const typing = !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable);
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
      } else if ((e.key === 'n' || e.key === 'N') && !e.repeat && keyActions.current.isPlaying) {
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
    nextAsset: nextAsset,
    elapsed: shownElapsed,
    remaining: remaining,
    duration: duration,
    introSeconds: introSeconds,
    onStart: handleStart,
    starting: startMut.isPending,
    statusMessage: queueNotice ?? statusMessage,
    onNext: handleNext,
    nextBusy: skipMut.isPending,
    looping: looping,
    onLoop: toggleLoop,
    loopBusy: loopBusy,
    audio: { audioReady: listenHere, initAudio: takeSound, volume, setVolume, muted, toggleMute, vuL, vuR },
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
      weatherState={weatherState}
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
    : <SchedulePanel stationId={stationId} />;

  return (
    <div className="qn h-full flex flex-col select-none overflow-hidden" data-testid="control-room">
      {resume.asking && <ResumePrompt onTap={resume.tap} />}
      <audio ref={dashStreamAudioRef} className="hidden" />

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

      <TransportBar {...transport} />

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
            <aside className="w-28 xl:w-32 shrink-0 bg-[#c0c0c0] p-1.5 flex flex-col gap-1 border-l-2 border-white" aria-label="Controls">
              <TransportButtons p={transport} stacked />
              <div className="h-2" />
              <div className="flex flex-col gap-1" role="tablist" aria-label="Lower pane">
                {SIDE_TABS.map(t => (
                  <button key={t.id} type="button" role="tab" aria-selected={sideTab === t.id} onClick={() => setSideTab(t.id)}
                    className={`qn-btn h-8 text-sm ${t.id === 'live' && liveShow ? '!text-red-700' : ''}`}>
                    {t.id === 'live' && liveShow ? '● Live' : t.label}
                  </button>
                ))}
              </div>
            </aside>
          </div>
          <div className="h-[150px] xl:h-[170px] shrink-0">{hotButtons(false, true)}</div>
        </>
      ) : (
        /* ═══ Phone / tablet: one pane + bottom tabs ═══ */
        <>
          <div className="flex-1 min-h-0 flex flex-col">
            {(pane === 'log' || pane === 'schedule') && (
              <div className="shrink-0 flex gap-1 p-1 bg-[#c0c0c0]">
                {(['log', 'schedule'] as Pane[]).map(t => (
                  <button key={t} type="button" onClick={() => setPane(t)} aria-pressed={pane === t}
                    className="qn-btn flex-1 h-9 text-sm">
                    {t === 'log' ? 'Played' : 'Schedule'}
                  </button>
                ))}
              </div>
            )}
            <div className="flex-1 min-h-0">
              {pane === 'queue' ? queuePanel : pane === 'buttons' ? hotButtons(true) : sidePanel(pane)}
            </div>
          </div>
          <nav className="shrink-0 flex gap-1 px-1 pt-1 bg-[#c0c0c0] border-t-2 border-white pb-[max(0.25rem,env(safe-area-inset-bottom))]" aria-label="Control room sections">
            {MOBILE_TABS.map(t => {
              const active = pane === t.id || (t.id === 'log' && pane === 'schedule');
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
        </>
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
