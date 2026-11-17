import { memo, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getDayLog, type DayLogEntry } from '../../../api/databank';

/**
 * The play log of one day, like KBR Studio's bottom strip: a list of the last
 * days on the left, and every start and end that day to the millisecond.
 */

const DAYS_BACK = 14;

/** YYYY-MM-DD of a moment in the station's time zone. */
export function localDay(d: Date, tz?: string): string {
  return new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', ...(tz ? { timeZone: tz } : {}) }).format(d);
}

/** The station's today and the days before it, newest first. */
export function recentDays(tz?: string, back = DAYS_BACK, today = localDay(new Date(), tz)): string[] {
  const [y, m, d] = today.split('-').map(Number);
  return Array.from({ length: back }, (_, i) => {
    const day = new Date(Date.UTC(y, m - 1, d - i));
    return day.toISOString().slice(0, 10);
  });
}

/** "Nov 16, 2026 Monday" */
export function dayLabel(day: string): string {
  const [y, m, d] = day.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  const md = dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  return `${md} ${dt.toLocaleDateString('en-US', { weekday: 'long', timeZone: 'UTC' })}`;
}

const msFormatters = new Map<string, Intl.DateTimeFormat>();
/** 22:09:31.674 in the station's time zone */
export function clockMs(iso: string, tz?: string): string {
  const key = tz ?? '';
  let f = msFormatters.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat('en-GB', {
      hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3, hourCycle: 'h23',
      ...(tz ? { timeZone: tz } : {}),
    } as Intl.DateTimeFormatOptions);
    msFormatters.set(key, f);
  }
  return f.format(new Date(iso));
}

interface Line { at: string; what: 'Started' | 'Ended'; entry: DayLogEntry }

function linesOf(logs: DayLogEntry[]): Line[] {
  const lines: Line[] = [];
  for (const e of logs) {
    lines.push({ at: e.start_utc, what: 'Started', entry: e });
    if (e.end_utc) lines.push({ at: e.end_utc, what: 'Ended', entry: e });
  }
  // By the moment (to the millisecond), not the text: "…31+00:00" and "…31.672000+00:00"
  // compare wrongly as text. On the same millisecond, Ended comes before Started.
  const ms = (iso: string) => Math.floor(Date.parse(iso));
  return lines.sort((a, b) => ms(a.at) - ms(b.at) || (a.what === b.what ? 0 : a.what === 'Ended' ? -1 : 1));
}

function nameOf(e: DayLogEntry): string {
  const t = e.artist ? `${e.artist} - ${e.title}` : e.title;
  return e.file && e.file !== `${e.title}` ? `${t}  [${e.file}]` : t;
}

export const DayLog = memo(function DayLog({ stationId, stationTz, compact = false }: {
  stationId: string | null;
  stationTz?: string;
  /** Phone: day picker on top instead of on the left */
  compact?: boolean;
}) {
  // The station's today, checked every 30 s so a page left open over station
  // midnight moves on to the new day (and keeps refreshing it) without a reload
  // (worked out in render, so a station change uses the new station's calendar at once)
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick(n => n + 1), 30_000);
    return () => clearInterval(t);
  }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps -- tick re-reads the clock
  const today = useMemo(() => localDay(new Date(), stationTz), [stationTz, tick]);
  const days = useMemo(() => recentDays(stationTz, DAYS_BACK, today), [stationTz, today]);
  // null = follow the station's today
  const [picked, setPicked] = useState<string | null>(null);
  const [sel, setSel] = useState<number | null>(null);
  // Another station: its lines are not the old station's, so no line stays highlighted, and a
  // day outside the new station's 14 days is dropped rather than coming back later (F-DATABANK-62)
  const [shownStation, setShownStation] = useState(stationId);
  if (shownStation !== stationId) {
    setShownStation(stationId);
    setSel(null);
    if (picked && !days.includes(picked)) setPicked(null);
  }
  const day = picked && days.includes(picked) && picked !== days[0] ? picked : days[0];
  const setDay = (d: string) => setPicked(d === days[0] ? null : d);
  const { data, isLoading, isError } = useQuery({
    queryKey: ['day-log', stationId, day],
    queryFn: () => getDayLog(stationId!, day),
    enabled: !!stationId,
    refetchInterval: day === days[0] ? 15_000 : false,
  });
  const lines = useMemo(() => linesOf(data?.logs ?? []), [data]);

  const picker = compact ? (
    <select className="qn-field h-11 w-full px-2 text-sm" value={day} onChange={e => { setDay(e.target.value); setSel(null); }}
      aria-label="Day" data-testid="daylog-day">
      {days.map(d => <option key={d} value={d}>{dayLabel(d)}</option>)}
    </select>
  ) : (
    <ul className="qn-field w-52 shrink-0 overflow-y-auto text-[12px] bg-white" role="listbox" aria-label="Day" data-testid="daylog-days">
      {days.map(d => (
        <li key={d} role="option" aria-selected={d === day}>
          <button type="button" onClick={() => { setDay(d); setSel(null); }}
            className={`w-full text-left px-1.5 py-0.5 ${d === day ? 'bg-[#000080] text-white' : 'text-black hover:bg-[#e0e0e0]'}`}>
            {dayLabel(d)}
          </button>
        </li>
      ))}
    </ul>
  );

  return (
    <section className={`h-full min-h-0 flex ${compact ? 'flex-col' : 'flex-row'} gap-1 bg-[#c0c0c0] p-1`} aria-label="Play log by day">
      {picker}
      <div className="qn-field flex-1 min-h-0 overflow-y-auto overscroll-contain bg-white text-[12px] leading-[18px] font-mono" data-testid="daylog-lines">
        {isLoading && <p className="p-2 text-gray-600">Loading…</p>}
        {isError && <p className="p-2 text-red-700">Can't load this day's log.</p>}
        {!isLoading && !isError && lines.length === 0 && <p className="p-2 text-gray-600">Nothing aired on {dayLabel(day)}.</p>}
        {lines.map((l, i) => (
          <button type="button" key={`${l.entry.id}-${l.what}`} onClick={() => setSel(i)}
            className={`w-full text-left flex gap-3 px-1.5 ${compact ? 'min-h-[44px] items-center' : ''} ${sel === i ? 'bg-[#000080] text-white' : 'text-black hover:bg-[#eef]'}`}>
            <span className="shrink-0 tabular-nums">{clockMs(l.at, data?.timezone ?? stationTz)}</span>
            <span className="min-w-0 flex-1 truncate">
              {nameOf(l.entry)}{' '}
              <span className={sel === i ? '' : l.what === 'Started' ? 'text-green-800' : 'text-gray-500'}>
                {l.entry.asset_type === 'spot' && l.what === 'Ended' ? 'Spot ended' : l.what}
              </span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
});
