import { Link, useLocation } from 'react-router-dom';
import { usePlayerStore } from '../../stores/playerStore';
import { LISTENER_PAGES } from './ListenHost';

/** The bar at the bottom of the other listener pages while a station plays: what is on,
 *  a way back to the station's page, and Stop. The sound itself is the Listen page's
 *  (kept alive by ListenHost). */
export default function AudioPlayer() {
  const { pathname } = useLocation();
  const { stationId, stationName, currentTrack, stop } = usePlayerStore();
  if (!stationId || !LISTENER_PAGES.includes(pathname)) return null;

  return (
    <>
      {/* room under the page so the bar never covers its last lines */}
      <div className="h-16" aria-hidden />
      <div data-testid="player-bar" role="region" aria-label="Now playing"
        className="fixed bottom-0 left-0 right-0 z-50 bg-gray-900 text-white border-t border-gray-700 px-3 py-2 flex items-center gap-3">
        <Link to={`/listen/${stationId}`} className="flex items-center gap-3 flex-1 min-w-0 min-h-[44px]" title="Back to the station's page">
          <span className="w-10 h-10 bg-gray-700 rounded flex items-center justify-center text-lg flex-shrink-0" aria-hidden>&#9835;</span>
          <span className="min-w-0">
            <span className="block text-sm font-medium truncate" data-testid="player-bar-title">{currentTrack?.title || 'On air'}</span>
            <span className="block text-xs text-gray-300 truncate">
              {currentTrack?.artist ? `${currentTrack.artist} · ` : ''}{stationName}
            </span>
          </span>
        </Link>
        <button type="button" onClick={stop}
          className="bg-red-600 hover:bg-red-700 text-white px-4 min-h-[44px] rounded text-sm font-medium flex-shrink-0">
          Stop
        </button>
      </div>
    </>
  );
}
