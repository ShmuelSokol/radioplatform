import { memo, useCallback, useEffect, useState } from 'react';
import AssetCategoryBadge from '../../../components/AssetCategoryBadge';
import { fmtAgo, fmtDur, fmtHMS, typeStyle } from './shared';

export interface QueueRow {
  id: string;
  asset_id: string;
  position: number;
  status: string;
  estimated_start?: string | null;
  source?: string | null;
  preempt_at?: string | null;
  blackout_name?: string;
  asset: {
    id: string; title: string; artist: string | null; duration: number | null;
    asset_type: string; category: string | null;
  } | null;
}

interface Props {
  entries: QueueRow[];          // already date-filtered
  loadedCount: number;          // entries loaded from the server
  totalCount: number;           // entries the server has in total
  totalDuration: number;
  dates: string[];
  dateFilter: string;
  onDateFilter: (d: string) => void;
  stationTz?: string;
  insertBeforeId: string | null;
  onArmInsert: (entryId: string | null) => void;
  onMoveUp: (entryId: string) => void;
  onMoveDown: (entryId: string) => void;
  onRemove: (entryId: string) => void;
  onReorder: (entryId: string, newPosition: number) => void;
  lastPlayed?: Record<string, string | null>;
}

const PAGE = 150;

function fmtStart(iso: string | null | undefined, tz?: string): { time: string; day: string | null } {
  if (!iso) return { time: '', day: null };
  const d = new Date(iso);
  const tzOpts = tz ? { timeZone: tz } : {};
  const dateStr = d.toLocaleDateString('en-CA', tzOpts);
  const todayStr = new Date().toLocaleDateString('en-CA', tzOpts);
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, ...tzOpts });
  const day = dateStr !== todayStr ? d.toLocaleDateString('en-US', { weekday: 'short', ...tzOpts }) : null;
  return { time, day };
}

function QueuePanel(p: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE);

  useEffect(() => { setLimit(PAGE); }, [p.dateFilter]);

  const onScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 600) {
      setLimit(l => (l < p.entries.length ? l + PAGE : l));
    }
  }, [p.entries.length]);

  const shown = p.entries.slice(0, limit);
  const countLabel = p.dateFilter
    ? `${p.entries.length} shown`
    : p.totalCount > p.loadedCount ? `${p.loadedCount} of ${p.totalCount}` : `${p.loadedCount}`;

  return (
    <section className="flex flex-col min-h-0 h-full bg-[#080820]" aria-label="Queue">
      <div className="shrink-0 flex items-center gap-2 px-3 h-10 bg-[#14143c] border-b border-[#2a2a5e] text-xs text-gray-400">
        <span className="font-bold text-gray-200 uppercase tracking-wider">Up next</span>
        <span className="truncate">
          {countLabel} items
          {p.totalDuration > 0 && ` · ${p.totalDuration >= 86400 ? `${(p.totalDuration / 86400).toFixed(1)} days` : fmtHMS(p.totalDuration)}`}
        </span>
        <select
          aria-label="Show day"
          value={p.dateFilter}
          onChange={(e) => p.onDateFilter(e.target.value)}
          className="ml-auto h-8 bg-[#0a0a28] border border-[#2a2a5e] text-gray-200 text-xs px-1.5 rounded-md focus:outline-none focus:border-cyan-600"
        >
          <option value="">All days</option>
          {p.dates.map(d => (
            <option key={d} value={d}>
              {new Date(d + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
            </option>
          ))}
        </select>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain" onScroll={onScroll} data-testid="queue-scroll">
        {shown.length === 0 && (
          <div className="text-center text-gray-500 py-10 px-4 text-sm">
            {p.loadedCount === 0
              ? 'The queue is empty. Add songs from the library, or press Start.'
              : 'Nothing scheduled for that day.'}
          </div>
        )}

        {shown.map((entry) => {
          const a = entry.asset;
          const isCur = entry.status === 'playing';
          const isSel = selectedId === entry.id;
          const isArmed = p.insertBeforeId === entry.id;
          const isDragOver = dragOverId === entry.id && dragId !== entry.id;
          const start = fmtStart(entry.estimated_start, p.stationTz);
          const isSilence = a?.asset_type === 'silence';
          const title = isSilence && entry.blackout_name
            ? `Scheduled silence: ${entry.blackout_name}`
            : a?.title ?? 'Unknown item';
          const lp = a?.id ? p.lastPlayed?.[a.id] : null;

          let tone = 'text-gray-100 border-[#15153a] hover:bg-[#12123a]';
          if (isCur) tone = 'bg-[#1b1b9a] text-yellow-200 border-[#15153a]';
          else if (entry.preempt_at) tone = 'bg-orange-950/40 text-orange-100 border-orange-900/40 hover:bg-orange-900/30';
          else if (entry.source === 'ad_slot') tone = 'bg-emerald-950/40 text-emerald-100 border-emerald-900/40 hover:bg-emerald-900/30';
          if (isSel && !isCur) tone += ' bg-[#17174a]';

          return (
            <div key={entry.id} data-testid="queue-row" data-entry-id={entry.id}>
              {isArmed && (
                <div className="flex items-center gap-2 px-3 h-10 bg-cyan-500/20 border-y-2 border-cyan-400 text-cyan-100 text-sm font-semibold" data-testid="insert-marker">
                  <span className="text-lg leading-none">↳</span>
                  <span className="flex-1 truncate">Inserting here. Pick a song or button.</span>
                  <button type="button" onClick={() => p.onArmInsert(null)}
                    className="h-8 px-3 rounded-md bg-white/10 hover:bg-white/20 text-xs font-bold">
                    Cancel
                  </button>
                </div>
              )}
              <div
                draggable={!isCur}
                onDragStart={!isCur ? (e) => { setDragId(entry.id); e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', entry.id); } : undefined}
                onDragOver={!isCur ? (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setDragOverId(entry.id); } : undefined}
                onDragLeave={!isCur ? () => setDragOverId(null) : undefined}
                onDrop={!isCur ? (e) => {
                  e.preventDefault();
                  setDragOverId(null);
                  if (dragId && dragId !== entry.id) p.onReorder(dragId, entry.position);
                  setDragId(null);
                } : undefined}
                onDragEnd={() => { setDragId(null); setDragOverId(null); }}
                onClick={() => !isCur && setSelectedId(isSel ? null : entry.id)}
                className={`relative flex items-center gap-2 pl-1.5 pr-2 lg:pr-3 py-1.5 lg:py-0.5 min-h-[52px] lg:min-h-[40px] border-b cursor-pointer group
                  ${tone}
                  ${isDragOver ? 'border-t-2 border-t-cyan-400' : ''}
                  ${dragId === entry.id ? 'opacity-40' : ''}`}
              >
                {/* Insert-here button */}
                {isCur ? (
                  <span className="w-10 lg:w-8 shrink-0 text-center text-base" aria-label="Playing now">▶</span>
                ) : (
                  <button
                    type="button"
                    data-testid="insert-here"
                    onClick={(e) => { e.stopPropagation(); p.onArmInsert(isArmed ? null : entry.id); }}
                    title="Insert a song before this one"
                    aria-label={`Insert a song before ${title}`}
                    className={`w-10 h-10 lg:w-8 lg:h-8 shrink-0 flex items-center justify-center rounded-md text-xl font-bold leading-none
                      ${isArmed ? 'bg-cyan-400 text-black' : 'text-cyan-300 bg-cyan-400/10 hover:bg-cyan-400/30'}`}
                  >
                    +
                  </button>
                )}

                <span className="w-[58px] lg:w-[68px] shrink-0 tabular-nums text-xs text-gray-400 leading-tight">
                  {start.time}
                  {start.day && <span className="block text-[10px] text-gray-500">{start.day}</span>}
                </span>

                <span className="flex-1 min-w-0 leading-tight">
                  <span className={`line-clamp-2 lg:line-clamp-1 break-words text-[15px] lg:text-sm font-medium ${isSilence ? 'text-red-300' : ''}`}>
                    {title}
                    {entry.source === 'ad_slot' && (
                      <span className="ml-2 align-middle text-[10px] bg-emerald-700/70 text-emerald-100 px-1.5 py-px rounded font-bold">SPONSOR</span>
                    )}
                    {entry.preempt_at && (
                      <span className="ml-2 align-middle text-[11px] text-orange-300" title="Plays at this exact time">
                        ⏰ {new Date(entry.preempt_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, ...(p.stationTz ? { timeZone: p.stationTz } : {}) })}
                      </span>
                    )}
                  </span>
                  <span className="block truncate text-xs text-gray-400">
                    {a?.artist || <span className="xl:hidden">{a?.asset_type}</span>}
                    {a?.artist && <span className={`xl:hidden ${typeStyle(a?.asset_type).text}`}> · {a?.asset_type}</span>}
                  </span>
                </span>

                <span className={`hidden xl:block w-16 shrink-0 text-xs ${typeStyle(a?.asset_type).text}`}>{a?.asset_type}</span>
                <span className="hidden 2xl:block w-24 shrink-0 text-xs" onClick={(e) => e.stopPropagation()}>
                  {a?.id && !isSilence ? <AssetCategoryBadge assetId={a.id} category={a.category ?? null} dark compact /> : null}
                </span>
                <span className="hidden 2xl:block w-20 shrink-0 text-xs text-gray-500 tabular-nums" title={lp ? new Date(lp).toLocaleString() : 'Not played recently'}>
                  {fmtAgo(lp)}
                </span>
                <span className="w-11 shrink-0 text-right tabular-nums text-sm text-gray-300">{fmtDur(a?.duration)}</span>

                {/* Desktop quick actions */}
                <span className="hidden lg:flex absolute right-14 top-1/2 -translate-y-1/2 gap-0.5 px-1 rounded-md bg-[#20205a] shadow-lg opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto focus-within:opacity-100 focus-within:pointer-events-auto" onClick={(e) => e.stopPropagation()}>
                  {!isCur && (
                    <>
                      <button type="button" onClick={() => p.onMoveUp(entry.id)} title="Move up" aria-label="Move up" className="w-7 h-7 rounded text-gray-300 hover:text-white hover:bg-white/10">▲</button>
                      <button type="button" onClick={() => p.onMoveDown(entry.id)} title="Move down" aria-label="Move down" className="w-7 h-7 rounded text-gray-300 hover:text-white hover:bg-white/10">▼</button>
                      <button type="button" onClick={() => p.onRemove(entry.id)} title="Remove" aria-label="Remove" className="w-7 h-7 rounded text-red-400 hover:text-red-200 hover:bg-red-500/20">✕</button>
                    </>
                  )}
                </span>
              </div>

              {/* Touch-friendly actions for the selected row */}
              {isSel && !isCur && (
                <div className="flex items-stretch gap-2 px-2 py-2 bg-[#101036] border-b border-[#2a2a5e]" data-testid="row-actions">
                  <button type="button" onClick={() => { p.onArmInsert(entry.id); setSelectedId(null); }}
                    className="flex-1 h-11 rounded-md bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-bold">
                    + Insert before
                  </button>
                  <button type="button" onClick={() => p.onMoveUp(entry.id)} aria-label="Move up"
                    className="w-12 h-11 rounded-md bg-white/10 hover:bg-white/20 text-white text-base">▲</button>
                  <button type="button" onClick={() => p.onMoveDown(entry.id)} aria-label="Move down"
                    className="w-12 h-11 rounded-md bg-white/10 hover:bg-white/20 text-white text-base">▼</button>
                  <button type="button" onClick={() => { p.onRemove(entry.id); setSelectedId(null); }}
                    className="px-3 h-11 rounded-md bg-red-900/70 hover:bg-red-800 text-red-100 text-sm font-bold">
                    Remove
                  </button>
                </div>
              )}
            </div>
          );
        })}

        {p.entries.length > limit && (
          <button type="button" onClick={() => setLimit(l => l + PAGE)}
            className="w-full h-11 text-sm text-cyan-300 hover:bg-white/5">
            Show more ({p.entries.length - limit} more)
          </button>
        )}
      </div>
    </section>
  );
}

export default memo(QueuePanel);
