import { lazy, Suspense } from 'react';
import { useLocation } from 'react-router-dom';
import { usePlayerStore } from '../../stores/playerStore';

const Listen = lazy(() => import('../../pages/public/Listen'));

/** Listener pages a station keeps playing on. Staff pages and the call-in page have
 *  their own sound, so the station stops when the listener goes there. */
export const LISTENER_PAGES = ['/stations', '/guide', '/hosts', '/archives'];

export function listenStationOf(pathname: string): string | null {
  return /^\/listen\/([^/]+)/.exec(pathname)?.[1] ?? null;
}

/**
 * Holds the Listen page. On /listen/:id it shows it. When a listener who is listening
 * moves to another listener page, the same page stays alive, hidden, so the sound goes
 * on without a gap or a restart (and without a new tap, which an iPhone would need for
 * a new player). Opening another station's page replaces it: the choice wins.
 */
export default function ListenHost() {
  const { pathname } = useLocation();
  const routeStation = listenStationOf(pathname);
  const playing = usePlayerStore((s) => s.stationId);
  const stationId = routeStation ?? (LISTENER_PAGES.includes(pathname) ? playing : null);
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
