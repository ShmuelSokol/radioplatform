import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../../hooks/useAuth';
import { useStations } from '../../hooks/useStations';
import {
  useQueue, useLastPlayed, useSkipCurrent, useRemoveFromQueue, useStartPlayback,
  useMoveUp, useMoveDown, useWeatherPreview, useReorderDnd,
} from '../../hooks/useQueue';
import { useLiveShows } from '../../hooks/useLiveShows';
import { useAudioEngine } from '../../hooks/useAudioEngine';
import { useNowPlayingWS } from '../../hooks/useNowPlayingWS';
import { fireNow, insertAt, queueEnd, queueNext } from '../../api/liveControl';
import type { SkipFade } from '../../api/queue';
import { insertEntry as addQueuePlaceholder } from '../../hooks/queueOptimistic';
import type { QueueDataLike } from '../../hooks/queueOptimistic';
import type { AssetInfo } from '../../types';
import TransportBar from './controlroom/TransportBar';
import QueuePanel from './controlroom/QueuePanel';
import type { QueueRow } from './controlroom/QueuePanel';
import LibraryPanel from './controlroom/LibraryPanel';
import HotButtons from './controlroom/HotButtons';
import { usePfl } from './controlroom/pfl';
import { LivePanel, LogPanel, SchedulePanel } from './controlroom/SidePanels';
import { dayKey, isValidTz, useHotButtons, useMediaQuery, useStored } from './controlroom/shared';
import type { CtlAsset, FireMode, SendHow } from './controlroom/shared';

type Pane = 'queue' | 'buttons' | 'library' | 'live' | 'log' | 'schedule';
type SideTab = 'library' | 'live' | 'log' | 'schedule';

interface Toast { text: string; type: 'ok' | 'warn' | 'error' }

const SIDE_TABS: Array<{ id: SideTab; label: string }> = [
  { id: 'library', label: 'Library' },
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
  const { data: queueData } = useQueue(stationId);
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
  const nowPlaying = queueData?.now_playing ?? null;
  const nowAsset = nowPlaying?.asset ?? null;
  const isPlaying = !!nowPlaying;
  const nextAsset = queueEntries.find(e => e.status === 'pending')?.asset ?? null;
  const liveShow = (liveData?.shows ?? []).find(s => s.status === 'live' && (!stationId || s.station_id === stationId)) ?? null;

  // ── UI state ───────────────────────────────────────────────
  const [pane, setPane] = useState<Pane>('queue');
  const [sideTab, setSideTab] = useStored<SideTab>('kbr_ctl_side_tab', 'library');
  const [fireMode, setFireMode] = useStored<FireMode>('kbr_ctl_fire_mode', 'now');
  const [insertBeforeId, setInsertBeforeId] = useState<string | null>(null);
  const [queueDateFilter, setQueueDateFilter] = useState('');
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
  const duration = serverDuration || (nowAsset?.duration ?? 0);
  const remaining = Math.max(0, duration - realElapsed);
  // Intro length is counted from where the item starts on air, so the countdown is simply intro minus elapsed.
  const introLength = Number((nowAsset as { intro_seconds?: number | null } | null)?.intro_seconds ?? wsNowPlaying?.asset?.intro_seconds ?? 0);
  const introSeconds = isPlaying && introLength > realElapsed ? introLength - realElapsed : 0;

  // ── Audio monitor (what the operator hears in this browser) ─
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

  const nextPreempt = useMemo(() => {
    const now = Date.now();
    return queueEntries.find(e => e.preempt_at && e.status !== 'playing' && new Date(e.preempt_at).getTime() > now);
  }, [queueEntries]);
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

  const { volume, setVolume, muted, toggleMute, vuLevels, audioReady, initAudio } = useAudioEngine(
    dashUseStream ? null : audioAsset, realElapsed, dashUseStream ? false : isPlaying,
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
  );
  useEffect(() => {
    if (dashStreamAudioRef.current) dashStreamAudioRef.current.volume = muted ? 0 : volume;
  }, [volume, muted]);
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
    if (how !== 'now') {
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
      setToast({ text: typeof msg === 'string' ? msg : `Could not send “${asset.title}”. Check the connection and try again.`, type: 'error' });
    } finally {
      setBusyId(null);
      refreshQueue();
    }
  }, [qc, refreshQueue]);

  const onMutError = useCallback(() => {
    setToast({ text: 'That did not go through. Check the connection and try again.', type: 'error' });
  }, []);

  const handleNext = useCallback((fade?: SkipFade) => {
    initAudio();
    skipMut.mutate(fade, { onError: onMutError });
  }, [initAudio, skipMut, onMutError]);

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
  const noStation = !stationId;
  const queuePanel = (
    <QueuePanel
      entries={filteredQueueEntries}
      loadedCount={queueEntries.length}
      totalCount={queueData?.total_all ?? queueEntries.length}
      totalDuration={queueData?.total_queue_duration_seconds ?? queueData?.queue_duration_seconds ?? 0}
      dates={queueDates}
      dateFilter={queueDateFilter}
      onDateFilter={setQueueDateFilter}
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
  const hotButtons = (wide: boolean) => (
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
  const sidePanel = (tab: Pane) =>
    tab === 'library' ? libraryPanel
    : tab === 'live' ? <LivePanel stationId={stationId} stationTz={stationTz} />
    : tab === 'log' ? <LogPanel stationId={stationId} stationTz={stationTz} />
    : <SchedulePanel stationId={stationId} />;

  return (
    <div className="h-full flex flex-col bg-[#080820] text-gray-200 font-sans select-none overflow-hidden" data-testid="control-room">
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

      <TransportBar
        stations={stations}
        stationId={stationId}
        onStation={setSelectedStationId}
        isPlaying={isPlaying}
        nowAsset={nowAsset}
        nextAsset={nextAsset}
        elapsed={realElapsed}
        remaining={remaining}
        duration={duration}
        introSeconds={introSeconds}
        onStart={handleStart}
        starting={startMut.isPending}
        statusMessage={statusMessage}
        onNext={handleNext}
        nextBusy={skipMut.isPending}
        audio={{ audioReady, initAudio, volume, setVolume, muted, toggleMute, vuL, vuR }}
        stationTz={stationTz}
        liveShow={liveShow ? { id: liveShow.id, title: liveShow.title } : null}
      />

      {isDesktop ? (
        /* ═══ Desktop: buttons | queue | library ═══ */
        <div className="flex-1 min-h-0 flex">
          <aside className="w-56 xl:w-72 2xl:w-80 shrink-0 border-r border-[#2a2a5e] min-h-0">
            {hotButtons(false)}
          </aside>
          <main className="flex-1 min-w-0 min-h-0">{queuePanel}</main>
          <aside className="w-[340px] xl:w-[440px] 2xl:w-[520px] shrink-0 border-l border-[#2a2a5e] min-h-0 flex flex-col">
            <div className="shrink-0 flex bg-[#14143c] border-b border-[#2a2a5e]" role="tablist">
              {SIDE_TABS.map(t => (
                <button key={t.id} type="button" role="tab" aria-selected={sideTab === t.id} onClick={() => setSideTab(t.id)}
                  className={`flex-1 h-10 text-sm font-semibold border-b-2 ${
                    sideTab === t.id ? 'border-cyan-400 text-cyan-200 bg-[#1a1a4e]' : 'border-transparent text-gray-400 hover:text-gray-200'
                  }`}>
                  {t.id === 'live' && liveShow ? '● Live' : t.label}
                </button>
              ))}
            </div>
            <div className="flex-1 min-h-0">{sidePanel(sideTab)}</div>
          </aside>
        </div>
      ) : (
        /* ═══ Phone / tablet: one pane + bottom tabs ═══ */
        <>
          <div className="flex-1 min-h-0 flex flex-col">
            {(pane === 'log' || pane === 'schedule') && (
              <div className="shrink-0 flex bg-[#14143c] border-b border-[#2a2a5e]">
                {(['log', 'schedule'] as Pane[]).map(t => (
                  <button key={t} type="button" onClick={() => setPane(t)}
                    className={`flex-1 h-10 text-sm font-semibold border-b-2 ${pane === t ? 'border-cyan-400 text-cyan-200' : 'border-transparent text-gray-400'}`}>
                    {t === 'log' ? 'Played' : 'Schedule'}
                  </button>
                ))}
              </div>
            )}
            <div className="flex-1 min-h-0">
              {pane === 'queue' ? queuePanel : pane === 'buttons' ? hotButtons(true) : sidePanel(pane)}
            </div>
          </div>
          <nav className="shrink-0 flex bg-[#0c0c2c] border-t border-[#2a2a5e] pb-[env(safe-area-inset-bottom)]" aria-label="Control room sections">
            {MOBILE_TABS.map(t => {
              const active = pane === t.id || (t.id === 'log' && pane === 'schedule');
              return (
                <button key={t.id} type="button" onClick={() => setPane(t.id)} aria-current={active ? 'page' : undefined}
                  className={`flex-1 h-14 flex flex-col items-center justify-center gap-0.5 text-[11px] font-semibold
                    ${active ? 'text-cyan-300' : 'text-gray-500'}
                    ${t.id === 'live' && liveShow ? '!text-red-400' : ''}`}>
                  <span className="text-lg leading-none" aria-hidden="true">{t.icon}</span>
                  {t.label}
                </button>
              );
            })}
          </nav>
        </>
      )}

      {isDesktop && (
        <footer className="shrink-0 h-6 px-3 flex items-center justify-between gap-4 bg-[#0c0c2c] border-t border-[#2a2a5e] text-[11px] text-gray-500">
          <span>Shortcuts: <b className="text-gray-400">N</b> next · <b className="text-gray-400">/</b> search · <b className="text-gray-400">Esc</b> cancel insert</span>
          <span className="truncate">{station?.name ?? ''}{station?.timezone ? ` · ${station.timezone}` : ''}</span>
          <span className="truncate">{user?.email ?? ''}{user?.role ? ` (${user.role})` : ''}</span>
        </footer>
      )}
    </div>
  );
}
