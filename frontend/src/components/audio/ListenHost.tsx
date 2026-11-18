import { lazy, Suspense, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { usePlayerStore } from '../../stores/playerStore';
import { stopStationStream } from '../../lib/stationStream';

const Listen = lazy(() => import('../../pages/public/Listen'));

/** The listener pages (the bar's tests walk them). */
export const LISTENER_PAGES = ['/stations', '/guide', '/hosts', '/archives'];

/** Pages with sound of their own: the station stops there. The control room is one,
 *  but it takes the station's sound over itself instead (pages/admin/Dashboard). */
export function hasOwnSound(pathname: string): boolean {
  return pathname.startsWith('/call/') || /^\/admin\/live\/[^/]+\/(host|screen)$/.test(pathname);
}

/** Pages a playing station carries on under, with the bar at the bottom. */
export function keepsStation(pathname: string): boolean {
  return pathname !== '/admin/dashboard' && !hasOwnSound(pathname) && !listenStationOf(pathname);
}

export function listenStationOf(pathname: string): string | null {
  return /^\/listen\/([^/]+)/.exec(pathname)?.[1] ?? null;
}

/**
 * Holds the Listen page. On /listen/:id it shows it. When someone who is listening
 * moves to another page (a listener page or a staff page), the same page stays alive,
 * hidden, so the sound goes on without a gap or a restart (and without a new tap,
 * which an iPhone would need for a new player). Opening another station's page
 * replaces it: the choice wins. Sound handed over by the control room is taken over
 * here the same way (Listen adopts the tab's running stream).
 */
export default function ListenHost() {
  const { pathname } = useLocation();
  const routeStation = listenStationOf(pathname);
  const playing = usePlayerStore((s) => s.stationId);
  const stationId = routeStation ?? (keepsStation(pathname) ? playing : null);
  const ownSound = hasOwnSound(pathname);
  useEffect(() => {
    if (!ownSound) return;
    usePlayerStore.getState().stop();
    stopStationStream();
  }, [ownSound]);
  if (!stationId) return null;
  return (
    // `contents`: on its own page the Listen page lays itself out as if this holder were not there
    <div className={routeStation ? 'contents' : 'hidden'} data-testid="listen-host" data-station={stationId}>
      <Suspense fallback={routeStation ? <div className="text-center py-10">Loading...</div> : null}>
        <Listen key={stationId} stationId={stationId} />
      </Suspense>
    </div>
  );
}
