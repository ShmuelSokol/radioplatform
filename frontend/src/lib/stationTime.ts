// Times on staff pages are the STATION's clock, not the clock of whoever is looking:
// a manager abroad, or a test browser on UTC, must read "Fri 4:12 PM" for a Lakewood
// blackout just as the studio does. These helpers turn a moment into a station's wall
// time and back, for display and for <input type="datetime-local">.

const parts = (d: Date, timeZone: string) => {
  const out: Record<string, number> = {};
  for (const p of new Intl.DateTimeFormat('en-US', {
    timeZone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(d)) {
    if (p.type !== 'literal') out[p.type] = Number(p.value);
  }
  return out;
};

/** Is this a zone name the browser knows? */
export function validZone(timeZone: string | null | undefined): timeZone is string {
  if (!timeZone) return false;
  try { new Intl.DateTimeFormat('en-US', { timeZone }); return true; } catch { return false; }
}

/** "Fri, Nov 20, 4:12 PM EST" on the station's clock. */
export function formatInZone(iso: string, timeZone: string | null | undefined): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    timeZoneName: 'short', ...(validZone(timeZone) ? { timeZone } : {}),
  });
}

/** A moment as the value of a datetime-local box showing the station's clock. */
export function toZonedInput(iso: string, timeZone: string | null | undefined): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  if (!validZone(timeZone)) return iso.slice(0, 16);
  const p = parts(d, timeZone);
  const two = (n: number) => String(n).padStart(2, '0');
  return `${p.year}-${two(p.month)}-${two(p.day)}T${two(p.hour)}:${two(p.minute)}`;
}

/** The moment a datetime-local value means on the station's clock, as ISO (UTC). */
export function fromZonedInput(value: string, timeZone: string | null | undefined): string {
  if (!validZone(timeZone)) return new Date(value).toISOString();
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!m) return new Date(value).toISOString();
  const want = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
  // Guess the moment as if the wall time were UTC, see what the station's clock shows
  // then, and move by the difference. Twice, so a guess across a clock change settles.
  let guess = want;
  for (let i = 0; i < 2; i++) {
    const p = parts(new Date(guess), timeZone);
    const shown = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute);
    guess += want - shown;
  }
  return new Date(guess).toISOString();
}
