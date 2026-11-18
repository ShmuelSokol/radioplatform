/**
 * The station last worked on in this browser. The control room remembers it; every staff
 * page with its own station picker opens on it too, so someone who was just working on
 * one station does not find themselves editing another (pages used to open on the first
 * station in the list). Choosing a station on any of those pages moves it for all.
 */
const KEY = 'radio_last_station';

export function lastStationId(): string {
  try { return localStorage.getItem(KEY) || ''; } catch { return ''; }
}

export function rememberStation(id: string | null | undefined): void {
  if (!id) return;
  try { localStorage.setItem(KEY, id); } catch { /* private mode */ }
}

/** The station a page should show: the one chosen on this page, else the one last worked
 *  on (if it still exists), else `fallback`, else the first in the list. */
export function stationToShow(stations: { id: string }[], chosen?: string | null, fallback?: string | null): string {
  if (chosen) return chosen;
  const last = lastStationId();
  if (last && stations.some((s) => String(s.id) === last)) return last;
  return fallback || stations[0]?.id || '';
}
