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

/**
 * Listener pages with a station choice open on Kol B'ramah Main, not on whichever name
 * sorts first (the list is alphabetical, so that was KBR Jerusalem). There is no "main"
 * flag on a station; Main is the one with "Main" in its name. Falls back to the first.
 */
export function mainStationId(stations: { id: string; name?: string }[]): string {
  return (stations.find((s) => /\bmain\b/i.test(s.name || '')) || stations[0])?.id || '';
}

const LISTENER_KEY = 'kbr_listener_station';

/** The station a listener page shows: the one the link names (?station=<id>), else the
 *  one this listener chose before, else Main. */
export function listenerStation(stations: { id: string; name?: string }[], fromLink?: string | null): string {
  const has = (id: string | null | undefined) => !!id && stations.some((s) => String(s.id) === id);
  if (has(fromLink)) return fromLink as string;
  let chosen = '';
  try { chosen = localStorage.getItem(LISTENER_KEY) || ''; } catch { /* private mode */ }
  return has(chosen) ? chosen : mainStationId(stations);
}

export function rememberListenerStation(id: string): void {
  if (!id) return;
  try { localStorage.setItem(LISTENER_KEY, id); } catch { /* private mode */ }
}
