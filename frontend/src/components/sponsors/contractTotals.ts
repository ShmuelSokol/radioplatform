/**
 * How many times a contract plays: a day and over its dates, and how long each play
 * is. Shown at the top of the Edit contract dialog (live as it changes), on the
 * client page's contract rows and on Spot Schedule (Shmuel 2026-11-18: "I want to
 * see clearly how many times per day the spot will play. And for how long").
 */

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export interface ContractShape {
  days_of_week: string; // "0,1,2" with 0 = Monday
  dayparts: string;
  plays_per_daypart: number;
  fixed_times: string | null;
  start_date: string | null;
  end_date: string | null;
}

export interface ContractTotals {
  perDay: number;
  slots: number;
  days: number[];
  airDays: number | null; // days it plays between its dates (null: no end date)
  total: number | null;
  leftFromToday: number | null;
}

function parseDay(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function isoToday(): string {
  const t = new Date();
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
}

function countDays(from: string, to: string, days: number[]): number {
  let n = 0;
  const end = parseDay(to);
  for (let d = parseDay(from); d <= end; d.setDate(d.getDate() + 1)) {
    if (days.includes((d.getDay() + 6) % 7)) n += 1;
  }
  return n;
}

export function contractTotals(c: ContractShape, today: string = isoToday()): ContractTotals {
  const days = c.days_of_week.split(',').filter(Boolean).map(Number);
  const times = (c.fixed_times ?? '').split(',').filter(Boolean);
  const slots = c.fixed_times ? times.length : c.dayparts.split(',').filter(Boolean).length;
  const perDay = c.fixed_times ? times.length : slots * Math.max(0, c.plays_per_daypart || 0);
  if (!c.end_date) return { perDay, slots, days, airDays: null, total: null, leftFromToday: null };
  const start = c.start_date || today;
  const airDays = start > c.end_date ? 0 : countDays(start, c.end_date, days);
  const fromToday = start > today ? start : today;
  const leftDays = fromToday > c.end_date ? 0 : countDays(fromToday, c.end_date, days);
  return { perDay, slots, days, airDays, total: airDays * perDay, leftFromToday: leftDays * perDay };
}

export function dayList(days: number[]): string {
  const sorted = [...days].sort();
  if (sorted.length === 7) return 'every day';
  if (!sorted.length) return 'no days';
  const run = sorted.every((d, i) => i === 0 || d === sorted[i - 1] + 1);
  if (run && sorted.length > 2) return `${DAY_NAMES[sorted[0]]} to ${DAY_NAMES[sorted[sorted.length - 1]]}`;
  return sorted.map((d) => DAY_NAMES[d]).join(', ');
}

function shortDate(iso: string): string {
  return parseDay(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

export function length(seconds: number | null | undefined): string {
  if (!seconds) return '';
  const s = Math.round(seconds);
  return s >= 60 ? `${Math.floor(s / 60)} min ${s % 60 ? `${s % 60} s` : ''}`.trim() : `${s} seconds`;
}

/** "6 times a day, Mon to Fri" */
export function perDayText(t: ContractTotals): string {
  return `${t.perDay} ${t.perDay === 1 ? 'time' : 'times'} a day, ${dayList(t.days)}`;
}

/** "Nov 19 to Dec 18: 22 days, 132 plays (120 from today)" or "From Nov 19, no end date" */
export function runText(c: ContractShape, t: ContractTotals): string {
  if (t.total == null) return c.start_date ? `From ${shortDate(c.start_date)}, no end date` : 'No end date';
  const from = c.start_date ? shortDate(c.start_date) : 'today';
  const left = t.leftFromToday != null && t.leftFromToday !== t.total ? ` (${t.leftFromToday} from today)` : '';
  return `${from} to ${shortDate(c.end_date!)}: ${t.airDays} ${t.airDays === 1 ? 'day' : 'days'}, ${t.total} ${t.total === 1 ? 'play' : 'plays'}${left}`;
}

/** One line for a contract row */
export function totalsLine(c: ContractShape, seconds?: number | null): string {
  const t = contractTotals(c);
  const each = length(seconds);
  return `${perDayText(t)}${each ? `, ${each} each` : ''}. ${runText(c, t)}.`;
}
