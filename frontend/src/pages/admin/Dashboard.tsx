import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useAuth } from '../../hooks/useAuth';
import { useStations } from '../../hooks/useStations';
import { useAssets } from '../../hooks/useAssets';
import {
  useQueue, usePlayLog, useSkipCurrent, usePlayNext, useAddToQueue,
  useRemoveFromQueue, useStartPlayback, useMoveUp, useMoveDown,
} from '../../hooks/useQueue';

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

function fmtClock(d: Date): string {
  return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

const ASSET_TYPES = ['all', 'music', 'spot', 'shiur', 'jingle', 'zmanim'] as const;
const TYPE_LABELS: Record<string, string> = {
  all: 'All', music: 'Music', spot: 'Spots', shiur: 'Shiurim', jingle: 'Jingles', zmanim: 'Zmanim',
};
const TYPE_COLORS: Record<string, string> = {
  music: 'text-cyan-300', spot: 'text-orange-400', shiur: 'text-purple-300',
  jingle: 'text-yellow-200', zmanim: 'text-green-300',
};
const CAT_COLORS: Record<string, string> = {
  med_fast: 'text-cyan-300', purim: 'text-orange-400', relax: 'text-green-300',
  shabbos: 'text-yellow-200', slow: 'text-blue-300', lively: 'text-red-300',
  daf_yomi: 'text-red-400', parsha: 'text-purple-300', halacha: 'text-blue-200',
  intro: 'text-yellow-300', outro: 'text-yellow-200', news: 'text-blue-300',
  station_id: 'text-white', hourly_id: 'text-amber-300', netz: 'text-yellow-400', shkia: 'text-red-400',
  weather: 'text-blue-300', call_in: 'text-yellow-300', retail: 'text-green-200',
  community: 'text-red-200', service: 'text-cyan-200',
};

// Bottom panel tabs
type BottomTab = 'library' | 'cart' | 'log';

export default function Dashboard() {
  const { user } = useAuth();
  const { data: stationsData } = useStations();
  const { data: assetsData } = useAssets(0, 2000);
  const [clock, setClock] = useState(new Date());
  const [activeTab, setActiveTab] = useState<string>('all');
  const [librarySearch, setLibrarySearch] = useState('');
  const [bottomTab, setBottomTab] = useState<BottomTab>('library');

  const stations = stationsData?.stations ?? [];
  const stationId = stations[0]?.id ?? null;

  const { data: queueData } = useQueue(stationId);
  const { data: logData } = usePlayLog(stationId);
  const skipMut = useSkipCurrent(stationId ?? '');
  const playNextMut = usePlayNext(stationId ?? '');
  const addQueueMut = useAddToQueue(stationId ?? '');
  const removeMut = useRemoveFromQueue(stationId ?? '');
  const startMut = useStartPlayback(stationId ?? '');
  const moveUpMut = useMoveUp(stationId ?? '');
  const moveDownMut = useMoveDown(stationId ?? '');

  // ── Client-side real-time countdown ────────────────────────
  // Server sends started_at (ISO) + duration. We interpolate locally.
  const [realElapsed, setRealElapsed] = useState(0);
  const [realRemaining, setRealRemaining] = useState(0);
  const rafRef = useRef<number>(0);
  const serverStartedAt = queueData?.now_playing?.started_at ?? null;
  const serverDuration = queueData?.now_playing?.asset?.duration ?? 0;
  const isPlaying = !!queueData?.now_playing;

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

  const assets = assetsData?.assets ?? [];
  const queueEntries = queueData?.entries ?? [];
  const nowPlaying = queueData?.now_playing ?? null;
  const nowAsset = nowPlaying?.asset ?? null;
  const remaining = realRemaining;
  const elapsed = realElapsed;
  const duration = serverDuration || (nowAsset?.duration ?? 0);
  const progress = duration > 0 ? Math.min(100, (elapsed / duration) * 100) : 0;

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

  // VU meter animation level (driven by realElapsed for smooth animation)
  const vuLevel = isPlaying ? 22 + Math.floor(Math.sin(realElapsed * 2) * 4) : 0;
  const vuLevel2 = isPlaying ? 20 + Math.floor(Math.cos(realElapsed * 1.7) * 5) : 0;

  return (
    <div className="-mx-4 sm:-mx-6 lg:-mx-8 -my-6 bg-[#080820] font-mono text-[13px] min-h-[calc(100vh-3rem)] flex flex-col select-none">

      {/* ═══ Status bar ═══ */}
      <div className="bg-[#0a0a28] border-b border-[#2a2a5e] px-3 py-1.5 flex items-center gap-3 flex-wrap shrink-0">
        {/* Play / Stop */}
        {isPlaying ? (
          <span className="bg-green-600 text-white px-2 py-0.5 font-bold text-[11px] rounded-sm tracking-wide animate-pulse">
            ON AIR
          </span>
        ) : (
          <button onClick={() => stationId && startMut.mutate()}
            className="bg-green-700 hover:bg-green-600 text-white px-2 py-0.5 font-bold text-[11px] rounded-sm tracking-wide">
            ▶ START
          </button>
        )}

        {/* Remaining */}
        <div className="flex items-baseline gap-1">
          <span className="text-gray-500 text-[11px]">Remaining</span>
          <span className="text-red-400 text-lg font-bold tabular-nums leading-none">{fmtDurMs(remaining)}</span>
        </div>

        {/* Progress bar */}
        <div className="w-32 h-2 bg-[#111] rounded-sm overflow-hidden">
          <div className="h-full bg-gradient-to-r from-green-500 to-yellow-400"
            style={{ width: `${progress}%` }} />
        </div>

        {/* Elapsed / Duration */}
        <div className="flex items-baseline gap-1">
          <span className="text-gray-500 text-[11px]">Elapsed</span>
          <span className="text-white text-sm tabular-nums">{fmtDurMs(elapsed)}</span>
          <span className="text-gray-600">/</span>
          <span className="text-gray-400 text-sm tabular-nums">{fmtDur(duration)}</span>
        </div>

        {/* VU Meters */}
        <div className="flex-1 min-w-[140px] max-w-[300px] flex flex-col gap-0.5 mx-1">
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

        {/* Skip */}
        <button onClick={() => skipMut.mutate()} disabled={!isPlaying}
          className="px-2 py-0.5 bg-[#2a2a5e] hover:bg-[#3a3a7e] text-yellow-300 text-[11px] rounded disabled:opacity-30">
          SKIP ⏭
        </button>

        {/* Clock */}
        <div className="flex items-baseline gap-1 ml-auto">
          <span className="text-red-400 text-xl font-bold tabular-nums leading-none">{fmtClock(clock)}</span>
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
        <div className="flex-1 flex flex-col min-h-0 border-b border-[#2a2a5e]">
          <div className="bg-[#16163e] px-2 py-0.5 text-[10px] text-gray-500 uppercase tracking-wider flex items-center gap-2 shrink-0 border-b border-[#2a2a5e]">
            <span className="font-bold text-gray-300">Queue</span>
            <span>({queueEntries.length})</span>
            <span className="w-6"></span>
            <span className="w-[50px]">Len</span>
            <span className="flex-1">Title</span>
            <span className="w-[60px]">Type</span>
            <span className="w-[60px]">Cat</span>
            <span className="w-[60px]">Actions</span>
          </div>
          <div className="flex-1 overflow-y-auto">
            {queueEntries.length === 0 ? (
              <div className="text-center text-gray-600 py-6 text-[12px]">
                Queue empty — add assets from the library below, or press START
              </div>
            ) : (
              queueEntries.map((entry: any) => {
                const a = entry.asset;
                const isCur = entry.status === 'playing';
                return (
                  <div key={entry.id} className={`flex items-center px-2 py-[2px] border-b border-[#12122e]
                    ${isCur ? 'bg-[#0000aa] text-yellow-300' : 'text-cyan-200 hover:bg-[#14143a]'}`}>
                    <span className="w-6 text-[11px]">{isCur ? '▶' : entry.position}</span>
                    <span className="w-[50px] tabular-nums text-[11px]">{fmtDur(a?.duration)}</span>
                    <span className="flex-1 truncate text-[12px]">
                      {a ? `${a.artist ? a.artist + ' — ' : ''}${a.title}` : '?'}
                    </span>
                    <span className={`w-[60px] text-[10px] ${TYPE_COLORS[a?.asset_type] ?? 'text-gray-500'}`}>
                      {a?.asset_type ?? ''}
                    </span>
                    <span className={`w-[60px] text-[10px] ${CAT_COLORS[a?.category] ?? 'text-gray-600'}`}>
                      {a?.category ?? ''}
                    </span>
                    <span className="w-[60px] flex gap-1 text-[10px]">
                      {!isCur && (
                        <>
                          <button onClick={() => moveUpMut.mutate(entry.id)} className="text-gray-400 hover:text-white" title="Move up">▲</button>
                          <button onClick={() => moveDownMut.mutate(entry.id)} className="text-gray-400 hover:text-white" title="Move down">▼</button>
                          <button onClick={() => removeMut.mutate(entry.id)} className="text-red-400 hover:text-red-300" title="Remove">✕</button>
                        </>
                      )}
                    </span>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Bottom panel with tabs */}
        <div className="h-[240px] flex flex-col shrink-0">
          {/* Tab bar */}
          <div className="bg-[#12123a] flex items-center gap-0 border-b border-[#2a2a5e] shrink-0">
            {(['library', 'cart', 'log'] as BottomTab[]).map(t => (
              <button key={t} onClick={() => setBottomTab(t)}
                className={`px-4 py-1 text-[11px] border-b-2 transition-colors uppercase tracking-wider ${
                  bottomTab === t
                    ? 'border-cyan-400 text-cyan-300 bg-[#1a1a4e]'
                    : 'border-transparent text-gray-500 hover:text-gray-300'
                }`}>
                {t === 'library' ? 'Library' : t === 'cart' ? 'Cart Machine' : 'Play Log'}
              </button>
            ))}
            {/* Type filter tabs (only for library) */}
            {bottomTab === 'library' && (
              <>
                <span className="text-gray-600 mx-2">|</span>
                {ASSET_TYPES.map(t => (
                  <button key={t} onClick={() => setActiveTab(t)}
                    className={`px-2 py-1 text-[10px] transition-colors ${
                      activeTab === t ? 'text-cyan-300' : 'text-gray-600 hover:text-gray-400'
                    }`}>
                    {TYPE_LABELS[t]}({typeCounts[t] ?? 0})
                  </button>
                ))}
                <div className="ml-auto mr-2">
                  <input type="text" placeholder="Search..." value={librarySearch}
                    onChange={(e) => setLibrarySearch(e.target.value)}
                    className="bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 text-[10px] px-2 py-0.5 rounded-sm w-36 placeholder-gray-600 focus:outline-none focus:border-cyan-700" />
                </div>
              </>
            )}
          </div>

          {/* Library panel */}
          {bottomTab === 'library' && (
            <>
              <div className="bg-[#16163e] flex text-[10px] text-gray-500 uppercase border-b border-[#2a2a5e] px-2 py-0.5 shrink-0">
                <span className="w-[50px]">Len</span>
                <span className="w-[140px]">Artist</span>
                <span className="flex-1">Title</span>
                <span className="w-[60px]">Type</span>
                <span className="w-[60px]">Cat</span>
                <span className="w-[70px]">Actions</span>
              </div>
              <div className="flex-1 overflow-y-auto bg-[#0a0a28]">
                {filteredAssets.map(asset => (
                  <div key={asset.id} className="flex items-center px-2 py-[2px] border-b border-[#12122e] text-gray-300 hover:bg-[#14143a] hover:text-white group">
                    <span className="w-[50px] tabular-nums text-[11px]">{fmtDur(asset.duration)}</span>
                    <span className="w-[140px] truncate text-[11px] font-bold">{asset.artist ?? '—'}</span>
                    <span className="flex-1 truncate text-[11px]">{asset.title}</span>
                    <span className={`w-[60px] text-[10px] ${TYPE_COLORS[asset.asset_type] ?? 'text-gray-500'}`}>{asset.asset_type}</span>
                    <span className={`w-[60px] text-[10px] ${CAT_COLORS[asset.category ?? ''] ?? 'text-gray-600'}`}>{asset.category ?? ''}</span>
                    <span className="w-[70px] flex gap-2 opacity-0 group-hover:opacity-100">
                      <button onClick={() => stationId && addQueueMut.mutate(asset.id)}
                        className="text-green-400 hover:text-green-300 text-[10px]" title="Add to queue">+Q</button>
                      <button onClick={() => stationId && playNextMut.mutate(asset.id)}
                        className="text-yellow-400 hover:text-yellow-300 text-[10px]" title="Play next">Next</button>
                    </span>
                  </div>
                ))}
                {filteredAssets.length === 0 && (
                  <div className="text-center text-gray-600 py-4 text-[11px]">No assets found.</div>
                )}
              </div>
            </>
          )}

          {/* Cart Machine */}
          {bottomTab === 'cart' && (
            <div className="flex-1 overflow-y-auto bg-[#0a0a28] p-2">
              <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 gap-1.5">
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
            <>
              <div className="bg-[#16163e] flex text-[10px] text-gray-500 uppercase border-b border-[#2a2a5e] px-2 py-0.5 shrink-0">
                <span className="w-[70px]">Time</span>
                <span className="w-[60px]">Type</span>
                <span className="flex-1">Title</span>
                <span className="w-[100px]">Artist</span>
                <span className="w-[60px]">Source</span>
              </div>
              <div className="flex-1 overflow-y-auto bg-[#0a0a28]">
                {playLog.map((log: any) => (
                  <div key={log.id} className="flex items-center px-2 py-[2px] border-b border-[#12122e] text-gray-400">
                    <span className="w-[70px] tabular-nums text-[11px]">
                      {new Date(log.start_utc).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' })}
                    </span>
                    <span className={`w-[60px] text-[10px] ${TYPE_COLORS[log.asset_type] ?? 'text-gray-500'}`}>
                      {log.asset_type ?? ''}
                    </span>
                    <span className="flex-1 truncate text-[11px] text-gray-300">{log.title ?? 'Unknown'}</span>
                    <span className="w-[100px] truncate text-[11px]">{log.artist ?? '—'}</span>
                    <span className="w-[60px] text-[10px]">{log.source}</span>
                  </div>
                ))}
                {playLog.length === 0 && (
                  <div className="text-center text-gray-600 py-6 text-[11px]">No play history yet. Start playback to begin logging.</div>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* ═══ Footer ═══ */}
      <div className="bg-[#12123a] border-t border-[#2a2a5e] px-3 py-0.5 flex items-center justify-between text-[10px] text-gray-500 shrink-0">
        <span>Assets: {assets.length} | Queue: {queueEntries.length}</span>
        <span>{stations[0]?.name ?? ''} | {stations[0]?.timezone ?? ''}</span>
        <span>{user?.email ?? ''} ({user?.role ?? ''})</span>
      </div>
    </div>
  );
}
