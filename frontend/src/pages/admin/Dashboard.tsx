import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useAuth } from '../../hooks/useAuth';
import { useStations } from '../../hooks/useStations';
import { useAssets } from '../../hooks/useAssets';
import {
  useQueue, usePlayLog, useLastPlayed, useSkipCurrent, usePlayNext, useAddToQueue,
  useRemoveFromQueue, useStartPlayback, useMoveUp, useMoveDown,
  useWeatherPreview, useReorderDnd,
} from '../../hooks/useQueue';
import { useTimelinePreview } from '../../hooks/useSchedules';
import { useAudioEngine } from '../../hooks/useAudioEngine';
import type { AssetInfo } from '../../types';
import AssetCategoryBadge from '../../components/AssetCategoryBadge';
import { useAssetTypes } from '../../hooks/useAssetTypes';

function fmtDur(sec: number | null): string {
  if (!sec || sec <= 0) return '0:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function fmtDurMs(sec: number | null): string {
  if (!sec || sec <= 0) return '0:00.0';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  const ms = Math.floor((sec % 1) * 10);
  return `${m}:${s.toString().padStart(2, '0')}.${ms}`;
}

function isValidTz(tz: string): boolean {
  try { Intl.DateTimeFormat(undefined, { timeZone: tz }); return true; } catch { return false; }
}

function fmtClock(d: Date, tz?: string): string {
  const opts: Intl.DateTimeFormatOptions = { hour12: true, hour: 'numeric', minute: '2-digit', second: '2-digit' };
  if (tz && isValidTz(tz)) opts.timeZone = tz;
  return d.toLocaleTimeString('en-US', opts);
}

function fmtHMS(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function fmtLastPlayed(iso: string | null | undefined): string {
  if (!iso) return '—';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const TYPE_COLORS: Record<string, string> = {
  music: 'text-cyan-300', spot: 'text-orange-400', shiur: 'text-purple-300',
  jingle: 'text-yellow-200', zmanim: 'text-green-300',
};
// Bottom panel tabs
type BottomTab = 'library' | 'cart' | 'log' | 'timeline';

export default function Dashboard() {
  const { user } = useAuth();
  const { data: stationsData } = useStations();
  const { data: assetsData } = useAssets({ skip: 0, limit: 100 });
  const { data: assetTypesData } = useAssetTypes();
  const ASSET_TYPES = ['all', ...(assetTypesData ?? []).map(t => t.name)];
  const TYPE_LABELS: Record<string, string> = { all: 'All' };
  for (const t of (assetTypesData ?? [])) TYPE_LABELS[t.name] = t.name.charAt(0).toUpperCase() + t.name.slice(1);
  const [clock, setClock] = useState(new Date());
  const [activeTab, setActiveTab] = useState<string>('all');
  const [librarySearch, setLibrarySearch] = useState('');
  const [bottomTab, setBottomTab] = useState<BottomTab>('library');
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [bottomHeight, setBottomHeight] = useState(() => {
    try { const v = localStorage.getItem('radio_panel_h'); return v ? Math.max(120, Math.min(600, parseInt(v))) : 240; }
    catch { return 240; }
  });
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);

  const stations = stationsData?.stations ?? [];
  const [selectedStationId, setSelectedStationId] = useState<string | null>(() => {
    try { return localStorage.getItem('radio_last_station') || null; } catch { return null; }
  });
  // Auto-select first station when stations load
  useEffect(() => {
    if (stations.length > 0 && !selectedStationId) {
      setSelectedStationId(stations[0].id);
    }
  }, [stations, selectedStationId]);
  // Persist selected station
  useEffect(() => {
    if (selectedStationId) {
      try { localStorage.setItem('radio_last_station', selectedStationId); } catch {}
    }
  }, [selectedStationId]);
  const stationId = selectedStationId;
  const rawTz = stations.find((s: any) => s.id === stationId)?.timezone as string | undefined;
  const stationTz = rawTz && isValidTz(rawTz) ? rawTz : undefined;

  const { data: queueData } = useQueue(stationId);
  const { data: lastPlayedMap } = useLastPlayed(stationId);
  const { data: logData } = usePlayLog(stationId, bottomTab === 'log');
  const skipMut = useSkipCurrent(stationId ?? '');
  const playNextMut = usePlayNext(stationId ?? '');
  const addQueueMut = useAddToQueue(stationId ?? '');
  const removeMut = useRemoveFromQueue(stationId ?? '');
  const startMut = useStartPlayback(stationId ?? '');
  const moveUpMut = useMoveUp(stationId ?? '');
  const moveDownMut = useMoveDown(stationId ?? '');
  const weatherPreviewMut = useWeatherPreview(stationId ?? '');
  const reorderDndMut = useReorderDnd(stationId ?? '');
  const previewAudioRef = useRef<HTMLAudioElement | null>(null);
  const [previewPlaying, setPreviewPlaying] = useState(false);
  const [dragEntryId, setDragEntryId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [toastMessage, setToastMessage] = useState<{ text: string; type: 'warn' | 'info' } | null>(null);

  // Timeline preview — deferred until tab is active
  const [timelineTime, setTimelineTime] = useState('');
  const { data: timelineData, isLoading: timelineLoading } = useTimelinePreview(
    bottomTab === 'timeline' ? (stationId ?? null) : null,
    timelineTime || null,
  );

  // ── Client-side real-time countdown ────────────────────────
  // Server sends started_at (ISO) + duration. We interpolate locally.
  const [realElapsed, setRealElapsed] = useState(0);
  const [realRemaining, setRealRemaining] = useState(0);
  const rafRef = useRef<number>(0);
  const serverStartedAt = queueData?.now_playing?.started_at ?? null;
  const serverDuration = queueData?.now_playing?.asset?.duration ?? 0;
  const isPlaying = !!queueData?.now_playing;

  // Clear status message when playback starts
  useEffect(() => {
    if (isPlaying) setStatusMessage(null);
  }, [isPlaying]);

  const updateCountdown = useCallback(() => {
    if (serverStartedAt && serverDuration > 0) {
      const startMs = new Date(serverStartedAt).getTime();
      const nowMs = Date.now();
      const el = Math.max(0, (nowMs - startMs) / 1000);
      const rem = Math.max(0, serverDuration - el);
      setRealElapsed(el);
      setRealRemaining(rem);
    } else {
      setRealElapsed(0);
      setRealRemaining(0);
    }
    rafRef.current = requestAnimationFrame(updateCountdown);
  }, [serverStartedAt, serverDuration]);

  useEffect(() => {
    rafRef.current = requestAnimationFrame(updateCountdown);
    return () => cancelAnimationFrame(rafRef.current);
  }, [updateCountdown]);

  // Clock updates every second
  useEffect(() => {
    const t = setInterval(() => setClock(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  // ── Bottom panel resize ──────────────────────────────────────
  const onResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragRef.current = { startY: e.clientY, startH: bottomHeight };
    let lastH = bottomHeight;
    const onMove = (ev: MouseEvent) => {
      if (!dragRef.current) return;
      const delta = dragRef.current.startY - ev.clientY;
      lastH = Math.max(120, Math.min(600, dragRef.current.startH + delta));
      setBottomHeight(lastH);
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      try { localStorage.setItem('radio_panel_h', String(lastH)); } catch {}
      dragRef.current = null;
    };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  }, [bottomHeight]);

  const assets = assetsData?.assets ?? [];
  const queueEntries = queueData?.entries ?? [];
  const nowPlaying = queueData?.now_playing ?? null;
  const nowAsset = nowPlaying?.asset ?? null;
  const remaining = realRemaining;
  const elapsed = realElapsed;
  const duration = serverDuration || (nowAsset?.duration ?? 0);
  const progress = duration > 0 ? Math.min(100, (elapsed / duration) * 100) : 0;

  const queueDuration = queueData?.queue_duration_seconds ?? 0;
  const nextEntry = queueEntries.find((e: any) => e.status === 'pending');
  const nextAsset = nextEntry?.asset ?? null;
  const playLog = logData?.logs ?? [];

  // Filter assets by type tab
  const filteredAssets = useMemo(() => {
    let list = assets;
    if (activeTab !== 'all') list = list.filter(a => a.asset_type === activeTab);
    if (librarySearch) {
      const q = librarySearch.toLowerCase();
      list = list.filter(a =>
        a.title.toLowerCase().includes(q) ||
        (a.artist ?? '').toLowerCase().includes(q) ||
        (a.category ?? '').toLowerCase().includes(q)
      );
    }
    return list;
  }, [assets, activeTab, librarySearch]);

  const typeCounts = useMemo(() => {
    const c: Record<string, number> = { all: assets.length };
    for (const a of assets) c[a.asset_type] = (c[a.asset_type] || 0) + 1;
    return c;
  }, [assets]);

  // Cart items (jingles + spots)
  const cartItems = useMemo(() =>
    assets.filter(a => a.asset_type === 'jingle' || a.asset_type === 'spot'),
  [assets]);

  // ── Weather preview handler ─────────────────────────────────
  const handlePreviewWeather = useCallback(async () => {
    if (weatherPreviewMut.isPending || previewPlaying) return;
    try {
      const data = await weatherPreviewMut.mutateAsync();
      const urls = [data.time_url, data.weather_url].filter(Boolean) as string[];
      if (urls.length === 0) return;
      setPreviewPlaying(true);

      // Play URLs sequentially using promises
      const playUrl = (url: string): Promise<void> => {
        return new Promise((resolve, reject) => {
          const a = new Audio(url);
          a.onended = () => resolve();
          a.onerror = () => reject();
          a.play().catch(reject);
        });
      };

      (async () => {
        try {
          for (const url of urls) await playUrl(url);
        } catch { /* audio error */ }
        setPreviewPlaying(false);
      })();
    } catch { setPreviewPlaying(false); }
  }, [weatherPreviewMut, previewPlaying]);

  // ── Drag-and-drop queue reorder ──────────────────────────────
  const handleDragStart = useCallback((e: React.DragEvent, entryId: string) => {
    setDragEntryId(entryId);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', entryId);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent, entryId: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverId(entryId);
  }, []);

  const handleDragLeave = useCallback(() => {
    setDragOverId(null);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent, targetEntry: any) => {
    e.preventDefault();
    setDragOverId(null);
    const sourceId = dragEntryId;
    if (!sourceId || sourceId === targetEntry.id) {
      setDragEntryId(null);
      return;
    }
    reorderDndMut.mutate(
      { entryId: sourceId, newPosition: targetEntry.position },
      {
        onSuccess: (data) => {
          if (data.warnings && data.warnings.length > 0) {
            setToastMessage({ text: data.warnings.join(' | '), type: 'warn' });
          }
        },
      },
    );
    setDragEntryId(null);
  }, [dragEntryId, reorderDndMut]);

  const handleDragEnd = useCallback(() => {
    setDragEntryId(null);
    setDragOverId(null);
  }, []);

  // Auto-dismiss toast
  useEffect(() => {
    if (!toastMessage) return;
    const t = setTimeout(() => setToastMessage(null), 6000);
    return () => clearTimeout(t);
  }, [toastMessage]);

  // ── Audio engine ───────────────────────────────────────────
  const audioAsset: AssetInfo | null = nowAsset ? {
    id: nowAsset.id,
    title: nowAsset.title,
    artist: nowAsset.artist,
    asset_type: nowAsset.asset_type,
    category: nowAsset.category,
    duration: nowAsset.duration,
  } : null;

  // Find next upcoming preempt entry for fade-out scheduling
  const nextPreempt = useMemo(() => {
    const now = Date.now();
    return queueEntries.find((e: any) => {
      if (!e.preempt_at || e.status === 'playing') return false;
      return new Date(e.preempt_at).getTime() > now;
    });
  }, [queueEntries]);

  const preemptFadeMs = (queueData as any)?.preempt_fade_ms ?? 2000;

  const {
    volume, setVolume, muted, toggleMute,
    vuLevels, audioReady, initAudio,
  } = useAudioEngine(
    audioAsset, realElapsed, isPlaying,
    nextPreempt?.preempt_at ?? null,
    nextPreempt?.asset_id ?? null,
    preemptFadeMs,
  );

  // VU meter levels: use real audio data when available, else fallback
  const vuLevel = audioReady && isPlaying
    ? Math.round(vuLevels[0] * 30)
    : (isPlaying ? 22 + Math.floor(Math.sin(realElapsed * 2) * 4) : 0);
  const vuLevel2 = audioReady && isPlaying
    ? Math.round(vuLevels[1] * 30)
    : (isPlaying ? 20 + Math.floor(Math.cos(realElapsed * 1.7) * 5) : 0);

  return (
    <div className="-mx-4 sm:-mx-6 lg:-mx-8 -my-6 bg-[#080820] font-mono text-[13px] h-[calc(100vh-3rem)] flex flex-col select-none overflow-hidden">
      <audio ref={previewAudioRef} className="hidden" />

      {/* Toast notification for rule warnings */}
      {toastMessage && (
        <div className={`fixed top-14 left-1/2 -translate-x-1/2 z-50 max-w-lg px-4 py-2 rounded-lg shadow-lg text-[12px] animate-pulse ${
          toastMessage.type === 'warn'
            ? 'bg-amber-900/95 border border-amber-600 text-amber-200'
            : 'bg-cyan-900/95 border border-cyan-600 text-cyan-200'
        }`}>
          <div className="flex items-start gap-2">
            <span className="shrink-0 mt-0.5">{toastMessage.type === 'warn' ? '⚠' : 'ℹ'}</span>
            <span>{toastMessage.text}</span>
            <button onClick={() => setToastMessage(null)} className="shrink-0 ml-2 text-gray-400 hover:text-white">✕</button>
          </div>
        </div>
      )}

      {/* ═══ Status bar ═══ */}
      <div className="bg-[#0a0a28] border-b border-[#2a2a5e] px-3 py-1.5 flex items-center gap-2 md:gap-3 flex-wrap shrink-0">
        {/* Station selector */}
        {stations.length > 0 && (
          <select
            value={selectedStationId ?? ''}
            onChange={(e) => setSelectedStationId(e.target.value || null)}
            className="bg-[#1a1a4e] border border-[#3a3a7e] text-cyan-200 text-[11px] px-1.5 py-0.5 rounded-sm focus:outline-none focus:border-cyan-600"
          >
            {stations.map((s: any) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        )}
        {/* Play / Stop */}
        {isPlaying ? (
          <span className="bg-green-600 text-white px-2 py-0.5 font-bold text-[11px] rounded-sm tracking-wide animate-pulse">
            ON AIR
          </span>
        ) : (
          <button onClick={() => {
            if (!stationId) { setStatusMessage('No station available'); return; }
            setStatusMessage(null);
            initAudio();
            startMut.mutate(undefined, {
              onSuccess: (data: any) => {
                if (data?.now_playing === null || data?.now_playing === undefined) {
                  setStatusMessage(data?.message || 'Queue is empty — add assets from the library first');
                }
              },
              onError: (err: any) => {
                const msg = err?.response?.data?.message;
                setStatusMessage(msg || 'Failed to start playback');
              },
            });
          }}
            disabled={startMut.isPending}
            className="bg-green-700 hover:bg-green-600 text-white px-2 py-0.5 font-bold text-[11px] rounded-sm tracking-wide disabled:opacity-50">
            {startMut.isPending ? '⏳ STARTING...' : '▶ START'}
          </button>
        )}

        {/* Status message */}
        {statusMessage && !isPlaying && (
          <span className="text-amber-400 text-[11px] animate-pulse">{statusMessage}</span>
        )}

        {/* Remaining */}
        <div className="flex items-baseline gap-1">
          <span className="text-gray-500 text-[11px] hidden sm:inline">Remaining</span>
          <span className="text-red-400 text-base md:text-lg font-bold tabular-nums leading-none">{fmtDurMs(remaining)}</span>
        </div>

        {/* Progress bar */}
        <div className="w-20 md:w-32 h-2 bg-[#111] rounded-sm overflow-hidden">
          <div className="h-full bg-gradient-to-r from-green-500 to-yellow-400"
            style={{ width: `${progress}%` }} />
        </div>

        {/* Elapsed / Duration */}
        <div className="flex items-baseline gap-1">
          <span className="text-gray-500 text-[11px] hidden sm:inline">Elapsed</span>
          <span className="text-white text-sm tabular-nums">{fmtDurMs(elapsed)}</span>
          <span className="text-gray-600">/</span>
          <span className="text-gray-400 text-sm tabular-nums">{fmtDur(duration)}</span>
        </div>

        {/* Skip */}
        <button onClick={() => { initAudio(); skipMut.mutate(); }} disabled={!isPlaying}
          className="px-2 py-0.5 bg-[#2a2a5e] hover:bg-[#3a3a7e] text-yellow-300 text-[11px] rounded disabled:opacity-30">
          SKIP ⏭
        </button>

        {/* Audio controls */}
        {!audioReady && isPlaying ? (
          <button onClick={() => initAudio()}
            className="px-2 py-0.5 bg-cyan-800 hover:bg-cyan-700 text-cyan-100 text-[11px] rounded animate-pulse">
            🔊 Enable Audio
          </button>
        ) : audioReady ? (
          <div className="flex items-center gap-1.5">
            <button onClick={toggleMute}
              className="text-[13px] w-5 text-center text-gray-300 hover:text-white">
              {muted ? '🔇' : volume > 0.5 ? '🔊' : volume > 0 ? '🔉' : '🔈'}
            </button>
            <input type="range" min="0" max="1" step="0.01" value={volume}
              onChange={e => setVolume(parseFloat(e.target.value))}
              className="w-16 md:w-20 h-1 accent-cyan-400 cursor-pointer" />
          </div>
        ) : null}

        {/* VU Meters - hidden on small screens */}
        <div className="hidden md:flex flex-1 min-w-[140px] max-w-[300px] flex-col gap-0.5 mx-1">
          <div className="flex items-center gap-1">
            <span className="text-yellow-400 text-[8px] w-5 text-right">L</span>
            <div className="flex-1 h-[5px] bg-[#111] rounded-sm overflow-hidden flex">
              {Array.from({ length: 30 }).map((_, i) => (
                <div key={i} className={`flex-1 mx-px rounded-sm transition-colors duration-150 ${
                  i < vuLevel
                    ? (i < 18 ? 'bg-green-500' : i < 24 ? 'bg-yellow-500' : 'bg-red-500')
                    : (i < 18 ? 'bg-green-900/30' : i < 24 ? 'bg-yellow-900/30' : 'bg-red-900/30')
                }`} />
              ))}
            </div>
          </div>
          <div className="flex items-center gap-1">
            <span className="text-yellow-400 text-[8px] w-5 text-right">R</span>
            <div className="flex-1 h-[5px] bg-[#111] rounded-sm overflow-hidden flex">
              {Array.from({ length: 30 }).map((_, i) => (
                <div key={i} className={`flex-1 mx-px rounded-sm transition-colors duration-150 ${
                  i < vuLevel2
                    ? (i < 18 ? 'bg-green-500' : i < 24 ? 'bg-yellow-500' : 'bg-red-500')
                    : (i < 18 ? 'bg-green-900/30' : i < 24 ? 'bg-yellow-900/30' : 'bg-red-900/30')
                }`} />
              ))}
            </div>
          </div>
        </div>

        {/* Clock */}
        <div className="flex items-baseline gap-1 ml-auto">
          <span className="text-red-400 text-lg md:text-xl font-bold tabular-nums leading-none">{fmtClock(clock, stationTz)}</span>
          {stationTz && <span className="text-gray-500 text-[10px] hidden sm:inline">{stationTz.split('/').pop()?.replace(/_/g, ' ')}</span>}
        </div>
      </div>

      {/* ═══ Now Playing + Next ═══ */}
      <div className="border-b border-[#2a2a5e] shrink-0">
        <div className="flex items-center bg-[#0c0c2c] px-3 py-1 border-b border-[#1e1e4a] min-h-[30px]">
          <span className={`text-[11px] w-16 shrink-0 ${isPlaying ? 'text-green-400' : 'text-gray-600'}`}>
            {isPlaying ? '▶ NOW' : '■ STOP'}
          </span>
          <span className="flex-1 text-yellow-300 text-[15px] font-bold truncate tracking-wide">
            {nowAsset ? `${nowAsset.artist ?? ''} ${nowAsset.artist ? '— ' : ''}${nowAsset.title}` : 'Press START to begin playback'}
          </span>
          <span className={`text-[10px] ml-2 px-1.5 py-0.5 rounded ${
            nowAsset ? 'bg-yellow-900/50 text-yellow-300' : ''}`}>
            {nowAsset?.asset_type ?? ''}
          </span>
          <span className="text-yellow-300/60 text-[11px] ml-2 shrink-0 tabular-nums">{fmtDur(duration)}</span>
        </div>
        <div className="flex items-center bg-[#0c0c2c] px-3 py-0.5 min-h-[24px]">
          <span className="text-gray-500 text-[11px] w-16 shrink-0">NEXT</span>
          <span className="flex-1 text-green-400 truncate text-[13px]">
            {nextAsset ? `${nextAsset.artist ?? ''} ${nextAsset.artist ? '— ' : ''}${nextAsset.title}` : '—'}
          </span>
          <span className={`text-[10px] ml-2 px-1.5 py-0.5 rounded ${
            nextAsset ? 'bg-green-900/50 text-green-300' : ''}`}>
            {nextAsset?.asset_type ?? ''}
          </span>
        </div>
      </div>

      {/* ═══ Main: Queue + Bottom Panel ═══ */}
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">

        {/* Top: Queue */}
        <div className="flex-1 flex flex-col min-h-0 overflow-hidden border-b border-[#2a2a5e]">
          <div className="bg-[#16163e] px-2 py-0.5 text-[10px] text-gray-500 uppercase tracking-wider flex items-center gap-2 shrink-0 border-b border-[#2a2a5e]">
            <span className="font-bold text-gray-300">Queue</span>
            <span>({queueEntries.length}){queueDuration > 0 ? ` — ${fmtHMS(queueDuration)}` : ''}</span>
          </div>
          <div className="flex-1 overflow-y-auto overflow-x-auto">
            {queueEntries.length === 0 ? (
              <div className="text-center text-gray-600 py-6 text-[12px]">
                Queue empty — add assets from the library below, or press START
              </div>
            ) : (
              queueEntries.map((entry: any) => {
                const a = entry.asset;
                const isCur = entry.status === 'playing';
                const isDragging = dragEntryId === entry.id;
                const isDragOver = dragOverId === entry.id && dragEntryId !== entry.id;
                return (<div key={entry.id + '-wrap'}>
                  <div
                    draggable={!isCur}
                    onDragStart={!isCur ? (e) => handleDragStart(e, entry.id) : undefined}
                    onDragOver={!isCur ? (e) => handleDragOver(e, entry.id) : undefined}
                    onDragLeave={!isCur ? handleDragLeave : undefined}
                    onDrop={!isCur ? (e) => handleDrop(e, entry) : undefined}
                    onDragEnd={handleDragEnd}
                    className={`flex items-center px-2 py-[2px] border-b min-w-[500px] transition-colors
                      ${isCur ? 'bg-[#0000aa] text-yellow-300 border-[#12122e]'
                        : entry.preempt_at ? 'bg-orange-950/30 text-orange-200 border-orange-900/30 hover:bg-orange-900/30'
                        : isDragOver ? 'bg-cyan-900/40 border-cyan-500 border-t-2'
                        : isDragging ? 'opacity-40 border-[#12122e]'
                        : 'text-cyan-200 hover:bg-[#14143a] border-[#12122e]'}
                      ${!isCur && !entry.preempt_at ? 'cursor-grab active:cursor-grabbing' : ''}`}
                  >
                    <span className="w-6 text-[11px] shrink-0">
                      {isCur ? '▶' : <span className="text-gray-600 text-[10px]">⠿</span>}
                    </span>
                    <span className="w-[52px] tabular-nums text-[10px] shrink-0 text-gray-500" title={entry.estimated_start ? new Date(entry.estimated_start).toLocaleString(undefined, stationTz ? { timeZone: stationTz } : undefined) : ''}>
                      {entry.estimated_start
                        ? new Date(entry.estimated_start).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, ...(stationTz ? { timeZone: stationTz } : {}) })
                        : ''}
                    </span>
                    <span className="w-[50px] tabular-nums text-[11px] shrink-0">{fmtDur(a?.duration)}</span>
                    <span className="flex-1 truncate text-[12px] min-w-0">
                      {a?.asset_type === 'silence' && entry.blackout_name
                        ? <span className="text-red-400">Scheduled Silence — {entry.blackout_name}</span>
                        : a ? `${a.artist ? a.artist + ' — ' : ''}${a.title}` : '?'}
                      {entry.preempt_at && (
                        <span className="ml-2 text-[9px] text-orange-400 font-mono" title={`Preempts at ${new Date(entry.preempt_at).toLocaleString()}`}>
                          ⏰ {new Date(entry.preempt_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, ...(stationTz ? { timeZone: stationTz } : {}) })}
                        </span>
                      )}
                    </span>
                    <span className={`w-[50px] text-[10px] shrink-0 ${TYPE_COLORS[a?.asset_type] ?? 'text-gray-500'}`}>
                      {a?.asset_type === 'silence' ? <span className="text-red-400">silence</span> : a?.asset_type ?? ''}
                    </span>
                    <span className="w-[60px] text-[10px] shrink-0">
                      {a?.id ? <AssetCategoryBadge assetId={a.id} category={a?.category ?? null} dark compact /> : ''}
                    </span>
                    <span className="w-[55px] text-[10px] text-gray-500 shrink-0 tabular-nums" title={a?.id && lastPlayedMap?.last_played?.[a.id] ? new Date(lastPlayedMap.last_played[a.id]).toLocaleString() : ''}>
                      {fmtLastPlayed(a?.id ? lastPlayedMap?.last_played?.[a.id] : null)}
                    </span>
                    <span className="w-[60px] flex gap-1 text-[10px] shrink-0">
                      {!isCur && (
                        <>
                          <button onClick={() => moveUpMut.mutate(entry.id)} className="text-gray-400 hover:text-white p-1" title="Move up">▲</button>
                          <button onClick={() => moveDownMut.mutate(entry.id)} className="text-gray-400 hover:text-white p-1" title="Move down">▼</button>
                          <button onClick={() => removeMut.mutate(entry.id)} className="text-red-400 hover:text-red-300 p-1" title="Remove">✕</button>
                        </>
                      )}
                    </span>
                  </div>
                </div>);
              })
            )}
          </div>
        </div>

        {/* Bottom panel with tabs — resizable */}
        <div className="flex flex-col shrink-0" style={{ height: bottomHeight }}>
          {/* Drag handle */}
          <div onMouseDown={onResizeStart}
            className="h-1.5 cursor-row-resize bg-[#1a1a4e] hover:bg-cyan-800 border-y border-[#2a2a5e] flex items-center justify-center shrink-0 group">
            <div className="w-10 h-[2px] rounded bg-gray-600 group-hover:bg-cyan-400" />
          </div>
          {/* Tab bar */}
          <div className="bg-[#12123a] flex items-center gap-0 border-b border-[#2a2a5e] shrink-0 overflow-x-auto">
            {(['library', 'cart', 'log', 'timeline'] as BottomTab[]).map(t => (
              <button key={t} onClick={() => setBottomTab(t)}
                className={`px-3 md:px-4 py-1 text-[11px] border-b-2 transition-colors uppercase tracking-wider whitespace-nowrap ${
                  bottomTab === t
                    ? 'border-cyan-400 text-cyan-300 bg-[#1a1a4e]'
                    : 'border-transparent text-gray-500 hover:text-gray-300'
                }`}>
                {t === 'library' ? 'Library' : t === 'cart' ? 'Cart' : t === 'log' ? 'Log' : 'Timeline'}
              </button>
            ))}
            {/* Type filter tabs (only for library) */}
            {bottomTab === 'library' && (
              <>
                <span className="text-gray-600 mx-1 md:mx-2">|</span>
                {ASSET_TYPES.map(t => (
                  <button key={t} onClick={() => setActiveTab(t)}
                    className={`px-1.5 md:px-2 py-1 text-[10px] transition-colors whitespace-nowrap ${
                      activeTab === t ? 'text-cyan-300' : 'text-gray-600 hover:text-gray-400'
                    }`}>
                    {TYPE_LABELS[t]}({typeCounts[t] ?? 0})
                  </button>
                ))}
                <div className="ml-auto mr-2 shrink-0">
                  <input type="text" placeholder="Search..." value={librarySearch}
                    onChange={(e) => setLibrarySearch(e.target.value)}
                    className="bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 text-[10px] px-2 py-0.5 rounded-sm w-24 md:w-36 placeholder-gray-600 focus:outline-none focus:border-cyan-700" />
                </div>
              </>
            )}
          </div>

          {/* Library panel */}
          {bottomTab === 'library' && (
            <div className="flex-1 overflow-y-auto overflow-x-auto bg-[#0a0a28]">
              <div className="min-w-[550px]">
                <div className="bg-[#16163e] flex text-[10px] text-gray-500 uppercase border-b border-[#2a2a5e] px-2 py-0.5 sticky top-0">
                  <span className="w-[50px] shrink-0">Len</span>
                  <span className="w-[120px] shrink-0">Artist</span>
                  <span className="flex-1">Title</span>
                  <span className="w-[50px] shrink-0">Type</span>
                  <span className="w-[50px] shrink-0">Cat</span>
                  <span className="w-[70px] shrink-0">Actions</span>
                </div>
                {filteredAssets.map(asset => (
                  <div key={asset.id} className="flex items-center px-2 py-[2px] border-b border-[#12122e] text-gray-300 hover:bg-[#14143a] hover:text-white group">
                    <span className="w-[50px] tabular-nums text-[11px] shrink-0">{fmtDur(asset.duration)}</span>
                    <span className="w-[120px] truncate text-[11px] font-bold shrink-0">{asset.artist ?? '—'}</span>
                    <span className="flex-1 truncate text-[11px] min-w-0">{asset.title}</span>
                    <span className={`w-[50px] text-[10px] shrink-0 ${TYPE_COLORS[asset.asset_type] ?? 'text-gray-500'}`}>{asset.asset_type}</span>
                    <span className="w-[60px] text-[10px] shrink-0">
                      <AssetCategoryBadge assetId={asset.id} category={asset.category} dark compact />
                    </span>
                    <span className="w-[70px] flex gap-2 shrink-0 md:opacity-0 md:group-hover:opacity-100">
                      <button onClick={() => stationId && addQueueMut.mutate(asset.id)}
                        className="text-green-400 hover:text-green-300 text-[10px] p-1" title="Add to queue">+Q</button>
                      <button onClick={() => stationId && playNextMut.mutate(asset.id)}
                        className="text-yellow-400 hover:text-yellow-300 text-[10px] p-1" title="Play next">Next</button>
                    </span>
                  </div>
                ))}
                {filteredAssets.length === 0 && (
                  <div className="text-center text-gray-600 py-4 text-[11px]">No assets found.</div>
                )}
              </div>
            </div>
          )}

          {/* Cart Machine */}
          {bottomTab === 'cart' && (
            <div className="flex-1 overflow-y-auto bg-[#0a0a28] p-2">
              <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8 gap-1.5">
                {/* Preview Weather card */}
                <button
                  onClick={handlePreviewWeather}
                  disabled={weatherPreviewMut.isPending || previewPlaying}
                  className={`p-2 rounded text-left border transition-colors ${
                    previewPlaying
                      ? 'bg-blue-800/50 border-blue-500 text-blue-200 animate-pulse'
                      : 'bg-blue-900/30 border-blue-800 hover:bg-blue-900/60 text-blue-300'
                  } disabled:opacity-60`}>
                  <div className="text-[10px] truncate font-bold">
                    {weatherPreviewMut.isPending ? '⏳ Loading...' : previewPlaying ? '🔊 Playing...' : '🌤 Preview Weather'}
                  </div>
                  <div className="text-[9px] opacity-60">Time + Weather</div>
                </button>
                {cartItems.map((item) => {
                  const isJingle = item.asset_type === 'jingle';
                  return (
                    <button key={item.id}
                      onClick={() => stationId && playNextMut.mutate(item.id)}
                      className={`p-2 rounded text-left border transition-colors ${
                        isJingle
                          ? 'bg-yellow-900/30 border-yellow-800 hover:bg-yellow-900/60 text-yellow-300'
                          : 'bg-orange-900/30 border-orange-800 hover:bg-orange-900/60 text-orange-300'
                      }`}>
                      <div className="text-[10px] truncate font-bold">{item.title}</div>
                      <div className="text-[9px] opacity-60">{fmtDur(item.duration)} | {item.category ?? item.asset_type}</div>
                    </button>
                  );
                })}
                {cartItems.length === 0 && (
                  <div className="col-span-full text-center text-gray-600 py-6 text-[11px]">No jingles or spots loaded.</div>
                )}
              </div>
            </div>
          )}

          {/* Play Log */}
          {bottomTab === 'log' && (
            <div className="flex-1 overflow-y-auto overflow-x-auto bg-[#0a0a28]">
              <div className="min-w-[450px]">
                <div className="bg-[#16163e] flex text-[10px] text-gray-500 uppercase border-b border-[#2a2a5e] px-2 py-0.5 sticky top-0">
                  <span className="w-[76px] shrink-0">Time</span>
                  <span className="w-[50px] shrink-0">Type</span>
                  <span className="flex-1">Title</span>
                  <span className="w-[80px] shrink-0">Artist</span>
                  <span className="w-[50px] shrink-0">Source</span>
                </div>
                {playLog.map((log: any) => (
                  <div key={log.id} className="flex items-center px-2 py-[2px] border-b border-[#12122e] text-gray-400">
                    <span className="w-[76px] tabular-nums text-[11px] shrink-0">
                      {new Date(log.start_utc).toLocaleTimeString('en-US', { hour12: true, hour: 'numeric', minute: '2-digit', ...(stationTz ? { timeZone: stationTz } : {}) })}
                    </span>
                    <span className={`w-[50px] text-[10px] shrink-0 ${TYPE_COLORS[log.asset_type] ?? 'text-gray-500'}`}>
                      {log.asset_type ?? ''}
                    </span>
                    <span className="flex-1 truncate text-[11px] text-gray-300 min-w-0">{log.title ?? 'Unknown'}</span>
                    <span className="w-[80px] truncate text-[11px] shrink-0">{log.artist ?? '—'}</span>
                    <span className="w-[50px] text-[10px] shrink-0">{log.source}</span>
                  </div>
                ))}
                {playLog.length === 0 && (
                  <div className="text-center text-gray-600 py-6 text-[11px]">No play history yet. Start playback to begin logging.</div>
                )}
              </div>
            </div>
          )}

          {/* Timeline Preview */}
          {bottomTab === 'timeline' && (
            <div className="flex-1 overflow-y-auto bg-[#0a0a28] p-3">
              <div className="flex items-center gap-3 mb-3">
                <input
                  type="datetime-local"
                  value={timelineTime}
                  onChange={e => setTimelineTime(e.target.value)}
                  className="bg-[#1a1a4e] border border-[#3a3a7e] text-cyan-200 text-[11px] px-2 py-1 rounded-sm focus:outline-none focus:border-cyan-600"
                />
                <button
                  onClick={() => setTimelineTime('')}
                  className="px-2 py-1 bg-[#2a2a5e] hover:bg-[#3a3a7e] text-cyan-300 text-[11px] rounded-sm"
                >
                  Now
                </button>
                {timelineLoading && <span className="text-cyan-400 text-[11px] animate-pulse">Loading...</span>}
              </div>

              {timelineData && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  {/* Blackout Status */}
                  <div className={`rounded-lg border p-3 ${
                    timelineData.is_blacked_out
                      ? 'bg-red-900/30 border-red-700'
                      : 'bg-green-900/20 border-green-800'
                  }`}>
                    <div className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Blackout Status</div>
                    <div className={`text-sm font-bold ${timelineData.is_blacked_out ? 'text-red-400' : 'text-green-400'}`}>
                      {timelineData.is_blacked_out ? 'BLACKED OUT' : 'CLEAR'}
                    </div>
                    {timelineData.current_blackout && (
                      <div className="mt-1 text-[11px] text-red-300">
                        {timelineData.current_blackout.name}
                        <div className="text-[10px] text-red-400/70">
                          {new Date(timelineData.current_blackout.start_datetime).toLocaleString()} — {new Date(timelineData.current_blackout.end_datetime).toLocaleString()}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Active Block */}
                  <div className="rounded-lg border bg-[#1a1a4e]/50 border-[#3a3a7e] p-3">
                    <div className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Active Block</div>
                    {timelineData.active_block ? (
                      <>
                        <div className="text-sm font-bold text-cyan-300">{timelineData.active_block.name}</div>
                        <div className="text-[11px] text-gray-400 mt-1">
                          Schedule: {timelineData.active_block.schedule_name ?? '—'}
                        </div>
                        <div className="text-[10px] text-gray-500">
                          {timelineData.active_block.start_time} — {timelineData.active_block.end_time} | {timelineData.active_block.playback_mode}
                        </div>
                      </>
                    ) : (
                      <div className="text-sm text-gray-500">No active block</div>
                    )}
                  </div>

                  {/* Next Blackout */}
                  <div className="rounded-lg border bg-yellow-900/20 border-yellow-800/50 p-3">
                    <div className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Next Blackout</div>
                    {timelineData.next_blackout ? (
                      <>
                        <div className="text-sm font-bold text-yellow-300">{timelineData.next_blackout.name}</div>
                        <div className="text-[10px] text-yellow-400/70 mt-1">
                          {new Date(timelineData.next_blackout.start_datetime).toLocaleString()} — {new Date(timelineData.next_blackout.end_datetime).toLocaleString()}
                        </div>
                      </>
                    ) : (
                      <div className="text-sm text-gray-500">None scheduled</div>
                    )}
                  </div>
                </div>
              )}

              {!timelineData && !timelineLoading && (
                <div className="text-center text-gray-600 py-6 text-[11px]">
                  Select a station to view timeline information.
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ═══ Footer ═══ */}
      <div className="bg-[#12123a] border-t border-[#2a2a5e] px-3 py-0.5 flex items-center justify-between text-[10px] text-gray-500 shrink-0">
        <span>Assets: {assets.length} | Queue: {queueEntries.length}</span>
        <span>{stations.find((s: any) => s.id === stationId)?.name ?? ''} | {stations.find((s: any) => s.id === stationId)?.timezone ?? ''}</span>
        <span>{user?.email ?? ''} ({user?.role ?? ''})</span>
      </div>
    </div>
  );
}
