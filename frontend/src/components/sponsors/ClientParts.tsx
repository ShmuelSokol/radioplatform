import type { ClientContract, ClientStatus, ContractState } from '../../api/sponsors';
import { formatInZone } from '../../lib/stationTime';

/** The station's home clock (Lakewood) for times that belong to a client, not one station. */
export const HOME_ZONE = 'America/New_York';

export const CLIENT_STATUS_LABEL: Record<ClientStatus, string> = {
  running: 'On the air',
  upcoming: 'Starting soon',
  paused: 'Paused',
  ended: 'Ended',
  none: 'No contract yet',
};

export const CONTRACT_STATE_LABEL: Record<ContractState, string> = {
  running: 'Running',
  upcoming: 'Starts later',
  paused: 'Paused',
  ended: 'Ended',
};

const COLORS: Record<string, string> = {
  running: 'bg-green-100 text-green-800',
  upcoming: 'bg-blue-100 text-blue-800',
  paused: 'bg-yellow-100 text-yellow-800',
  ended: 'bg-gray-200 text-gray-700',
  none: 'bg-gray-100 text-gray-500',
};

export function StatusPill({ status, label }: { status: string; label: string }) {
  return (
    <span
      data-testid="client-status"
      className={`inline-block px-2.5 py-0.5 rounded-full text-xs font-semibold whitespace-nowrap ${COLORS[status] ?? COLORS.none}`}
    >
      {label}
    </span>
  );
}

/** "Nov 18, 2026" from "2026-11-18" (a calendar day, no time zone involved). */
export function day(iso: string | null | undefined): string {
  if (!iso) return '';
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/** "Nov 1, 2026 to Dec 31, 2026", "Since Nov 1, 2026 (no end date)", or "" */
export function span(from: string | null, until: string | null, openEnded: boolean): string {
  if (!from && !until) return '';
  if (openEnded) return from ? `Since ${day(from)} (no end date)` : 'No end date';
  if (from && until) return `${day(from)} to ${day(until)}`;
  return from ? `From ${day(from)}` : `Until ${day(until)}`;
}

export function when(iso: string | null | undefined, zone: string = HOME_ZONE): string {
  return iso ? formatInZone(iso, zone) : '';
}

export function seconds(n: number | null | undefined): string {
  if (n == null) return '';
  const s = Math.round(n);
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : `${s} s`;
}

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const DAYPART: Record<string, string> = {
  morning: 'morning (6 AM to 12 PM)',
  afternoon: 'afternoon (12 to 6 PM)',
  evening: 'evening (6 PM to 12 AM)',
  overnight: 'overnight (12 to 6 AM)',
};

function clock(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number);
  return `${h % 12 === 0 ? 12 : h % 12}:${String(m).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`;
}

/** A contract's settings in words: "Sun to Thu, 2 times each morning, evening, after the song". */
export function describeContract(c: Pick<ClientContract, 'days_of_week' | 'dayparts' | 'plays_per_daypart' | 'fixed_times' | 'timing_mode'>): string {
  const days = c.days_of_week.split(',').filter(Boolean).map(Number);
  const dayText = days.length === 7 ? 'Every day' : days.map((d) => DAYS[d]).join(', ');
  const timing =
    c.timing_mode === 'hard' ? ', on the second (song fades)' : c.timing_mode === 'soft' ? ', after the song ends' : '';
  if (c.fixed_times) return `${dayText} at ${c.fixed_times.split(',').map(clock).join(', ')}${timing}`;
  const times = c.plays_per_daypart === 1 ? 'once' : `${c.plays_per_daypart} times`;
  const parts = c.dayparts.split(',').map((p) => DAYPART[p] ?? p).join(', ');
  return `${dayText}, ${times} each ${parts}${timing}`;
}
