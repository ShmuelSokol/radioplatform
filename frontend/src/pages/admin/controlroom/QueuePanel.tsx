import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { clockTime, clockTimeSec, dayKey, fmtAgo, fmtDur, fmtHMS, useMediaQuery, weekdayShort } from './shared';

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
  /** Shown instead of "empty" while the queue has not loaded or cannot be reached */
  notice?: string | null;
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

// Only the rows on screen (plus a margin) are drawn, so the list scrolls the
// same with 50 items or 15,000. That needs every row to have a known height:
// the classes on the row, the insert marker and the action bar below must
// match these numbers.
const ROW_DESKTOP = 26;   // lg:h-[26px]
const ROW_PHONE = 66;     // h-[66px]
const MARKER_H = 40;      // h-10
const ACTIONS_H = 61;     // h-[61px]
const OVERSCAN = 30;      // extra rows drawn above and below, so a fast fling rarely outruns the redraw
const STEP = 6;           // redraw only after scrolling this many rows

// Projected air time to the second, as on the running log operators know.
function fmtStart(iso: string | null | undefined, today: string, tz?: string): { time: string; sub: string } {
  if (!iso) return { time: '', sub: '' };
  const d = new Date(iso);
  const { time, ampm } = clockTimeSec(d, tz);
  return { time, sub: dayKey(d, tz) !== today ? `${ampm} ${weekdayShort(d, tz)}` : ampm };
}

function QueuePanel(p: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const isDesktop = useMediaQuery('(min-width: 1024px)');
  const rowH = isDesktop ? ROW_DESKTOP : ROW_PHONE;
  const scrollRef = useRef<HTMLDivElement>(null);
  const [firstRow, setFirstRow] = useState(0);   // top of the window, in steps of STEP rows
  const [viewH, setViewH] = useState(800);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const measure = () => setViewH(el.clientHeight || 800);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Back to the top when the day changes.
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
    setFirstRow(0);
  }, [p.dateFilter]);

  const onScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    const row = Math.floor(e.currentTarget.scrollTop / rowH / STEP) * STEP;
    setFirstRow(prev => (prev === row ? prev : row));
  }, [rowH]);

  const n = p.entries.length;
  const start = Math.max(0, Math.min(firstRow - OVERSCAN, n));
  const end = Math.min(n, firstRow + Math.ceil(viewH / rowH) + STEP + OVERSCAN);
  const shown = p.entries.slice(start, end);
  // The insert marker and the action bar add height to one row each.
  const armedIdx = p.insertBeforeId ? p.entries.findIndex(e => e.id === p.insertBeforeId) : -1;
  const selIdx = selectedId ? p.entries.findIndex(e => e.id === selectedId && e.status !== 'playing') : -1;
  const offsetOf = (i: number) =>
    i * rowH + (armedIdx >= 0 && armedIdx < i ? MARKER_H : 0) + (selIdx >= 0 && selIdx < i ? ACTIONS_H : 0);
  const padTop = offsetOf(start);
  const padBottom = offsetOf(n) - offsetOf(end);
  const today = dayKey(Date.now(), p.stationTz);
  const countLabel = p.dateFilter
    ? `${p.entries.length} shown`
    : p.totalCount > p.loadedCount ? `${p.loadedCount} of ${p.totalCount}` : `${p.loadedCount}`;

  return (
    <section className="flex flex-col min-h-0 h-full qn-log" aria-label="Queue">
      <div className="shrink-0 flex items-center gap-2 px-2 h-8 bg-[#c0c0c0] text-black text-xs font-normal border-b border-[#808080]">
        <span className="font-bold">Up next</span>
        <span className="truncate">
          {countLabel} items
          {p.totalDuration > 0 && ` · ${p.totalDuration >= 86400 ? `${(p.totalDuration / 86400).toFixed(1)} days` : fmtHMS(p.totalDuration)}`}
        </span>
        <select
          aria-label="Show day"
          value={p.dateFilter}
          onChange={(e) => p.onDateFilter(e.target.value)}
          className="qn-field ml-auto h-6 text-xs px-1"
        >
          <option value="">All days</option>
          {p.dates.map(d => (
            <option key={d} value={d}>
              {new Date(d + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
            </option>
          ))}
        </select>
      </div>
      <div className="hidden lg:flex shrink-0 h-[18px] items-stretch qn-log-head text-[11px] leading-[18px] pr-3" aria-hidden="true">
        <span className="w-7 shrink-0" />
        <span className="w-[92px] shrink-0">Time</span>
        <span className="w-12 shrink-0">Length</span>
        <span className="flex-1">Title</span>
        <span className="hidden xl:block w-28 shrink-0">Type</span>
        <span className="hidden xl:block w-20 shrink-0">Last played</span>
      </div>

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto overscroll-contain" onScroll={onScroll} data-testid="queue-scroll">
        {n === 0 && (
          <div className="text-center text-[var(--qn-yellow)] py-10 px-4 text-sm font-normal">
            {p.notice ? p.notice : p.loadedCount === 0
              ? 'The queue is empty. Add songs from the library, or press Start.'
              : 'Nothing scheduled for that day.'}
          </div>
        )}

        {padTop > 0 && <div style={{ height: padTop }} aria-hidden="true" />}
        {shown.map((entry) => {
          const a = entry.asset;
          const isCur = entry.status === 'playing';
          const isSel = selectedId === entry.id;
          const isArmed = p.insertBeforeId === entry.id;
          const isDragOver = dragOverId === entry.id && dragId !== entry.id;
          const start = fmtStart(entry.estimated_start, today, p.stationTz);
          const isSilence = a?.asset_type === 'silence';
          const title = isSilence && entry.blackout_name
            ? `Scheduled silence: ${entry.blackout_name}`
            : a?.title ?? 'Unknown item';
          const lp = a?.id ? p.lastPlayed?.[a.id] : null;

          let tone = 'qn-row';
          if (isCur) tone = 'qn-row-cur';
          else if (entry.preempt_at || entry.source === 'ad_slot' || entry.source === 'block') tone = 'qn-row qn-row-timed';
          if (isSel && !isCur) tone += ' qn-row-sel';

          return (
            <div key={entry.id} data-testid="queue-row" data-entry-id={entry.id}>
              {isArmed && (
                <div className="flex items-center gap-2 px-3 h-10 bg-[#00ffff] text-black text-sm font-bold" data-testid="insert-marker">
                  <span className="text-lg leading-none">↳</span>
                  <span className="flex-1 truncate">Inserting here. Pick a song or button.</span>
                  <button type="button" onClick={() => p.onArmInsert(null)}
                    className="qn-btn h-8 px-3 text-xs">
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
                className={`relative flex items-center gap-2 lg:gap-0 pl-1.5 lg:pl-0.5 pr-2 lg:pr-3 h-[66px] lg:h-[26px] overflow-hidden border-b border-[#222] lg:border-0 cursor-pointer group
                  ${tone}
                  ${isDragOver ? 'border-t-2 border-t-cyan-400' : ''}
                  ${dragId === entry.id ? 'opacity-40' : ''}`}
              >
                {/* Insert-here button */}
                {isCur ? (
                  <span className="w-10 lg:w-7 shrink-0 text-center text-base lg:text-xs" aria-label="Playing now">▶</span>
                ) : (
                  <button
                    type="button"
                    data-testid="insert-here"
                    onClick={(e) => { e.stopPropagation(); p.onArmInsert(isArmed ? null : entry.id); }}
                    title="Insert a song before this one"
                    aria-label={`Insert a song before ${title}`}
                    className={`w-10 h-10 lg:w-6 lg:h-[22px] lg:mr-1 shrink-0 flex items-center justify-center text-xl lg:text-base font-bold leading-none
                      ${isArmed ? 'bg-[#00ffff] text-black' : 'text-[#00ffff] hover:bg-[#003838]'}`}
                  >
                    +
                  </button>
                )}

                <span className="w-[62px] lg:w-[92px] shrink-0 tabular-nums text-[13px] leading-tight" data-testid="air-time">
                  {start.time}
                  <span className="block lg:inline lg:ml-1 text-[10px] opacity-70">{start.sub}</span>
                </span>
                <span className="hidden lg:block w-12 shrink-0 tabular-nums text-[13px]">{fmtDur(a?.duration)}</span>

                <span className="flex-1 min-w-0 leading-tight">
                  <span className={`line-clamp-2 lg:line-clamp-1 break-words text-[15px] lg:text-[13px] ${isSilence ? 'text-red-400' : ''}`}>
                    <span className="hidden lg:inline">{a?.artist ? `${a.artist}  ` : ''}</span>{title}
                    {entry.source === 'ad_slot' && (
                      <span className="ml-2 align-middle text-[10px] bg-[#ffa64d] text-black px-1 py-px font-bold">SPONSOR</span>
                    )}
                    {entry.preempt_at && (
                      <span className="ml-2 align-middle text-[11px]" title="Plays at this exact time">
                        ⏰ {clockTime(entry.preempt_at, p.stationTz)}
                      </span>
                    )}
                  </span>
                  <span className="lg:hidden block truncate text-xs font-normal opacity-80">
                    {a?.artist ? `${a.artist} · ` : ''}{a?.category || a?.asset_type}
                  </span>
                </span>

                <span className="hidden xl:block w-28 shrink-0 truncate text-[13px]" title={a?.asset_type}>{a?.category || a?.asset_type}</span>
                <span className="hidden xl:block w-20 shrink-0 text-[13px] tabular-nums" title={lp ? new Date(lp).toLocaleString() : 'Not played recently'}>
                  {fmtAgo(lp)}
                </span>
                <span className="lg:hidden w-11 shrink-0 text-right tabular-nums text-sm">{fmtDur(a?.duration)}</span>

                {/* Desktop quick actions */}
                <span className="hidden lg:flex absolute right-28 top-1/2 -translate-y-1/2 gap-0.5 px-0.5 bg-[#c0c0c0] text-black shadow-lg opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto focus-within:opacity-100 focus-within:pointer-events-auto" onClick={(e) => e.stopPropagation()}>
                  {!isCur && (
                    <>
                      <button type="button" onClick={() => p.onMoveUp(entry.id)} title="Move up" aria-label="Move up" className="qn-btn w-6 h-5 text-[10px]">▲</button>
                      <button type="button" onClick={() => p.onMoveDown(entry.id)} title="Move down" aria-label="Move down" className="qn-btn w-6 h-5 text-[10px]">▼</button>
                      <button type="button" onClick={() => p.onRemove(entry.id)} title="Remove" aria-label="Remove" className="qn-btn w-6 h-5 text-[10px] !text-red-700">✕</button>
                    </>
                  )}
                </span>
              </div>

              {/* Touch-friendly actions for the selected row */}
              {isSel && !isCur && (
                <div className="flex items-stretch gap-2 px-2 py-2 h-[61px] bg-[#c0c0c0] border-b border-[#808080]" data-testid="row-actions">
                  <button type="button" onClick={() => { p.onArmInsert(entry.id); setSelectedId(null); }}
                    className="qn-btn flex-1 h-11 text-sm">
                    + Insert before
                  </button>
                  <button type="button" onClick={() => p.onMoveUp(entry.id)} aria-label="Move up"
                    className="qn-btn w-12 h-11 text-base">▲</button>
                  <button type="button" onClick={() => p.onMoveDown(entry.id)} aria-label="Move down"
                    className="qn-btn w-12 h-11 text-base">▼</button>
                  <button type="button" onClick={() => { p.onRemove(entry.id); setSelectedId(null); }}
                    className="qn-btn px-3 h-11 text-sm !text-red-700">
                    Remove
                  </button>
                </div>
              )}
            </div>
          );
        })}

        {padBottom > 0 && <div style={{ height: padBottom }} aria-hidden="true" />}
      </div>
    </section>
  );
}

export default memo(QueuePanel);
