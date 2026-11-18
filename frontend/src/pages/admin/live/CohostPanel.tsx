import { useEffect, useState } from 'react';
import { getAirStatus } from '../../../api/liveShows';

type Cohost = { on: boolean; muted: boolean; setMuted: (m: boolean) => void; start: () => void; stop: () => void; error: string | null };

/**
 * Co-host: a second (or third) person joins the show from another place, on this
 * page, without being the host. Also lists who is co-hosting now, for the host.
 * `named` comes from the show's socket; the list is read once as well, for a page
 * opened later.
 */
export default function CohostPanel({ showId, co, named, hosting }: {
  showId: string | undefined;
  co: Cohost;
  named: string[] | null;
  hosting: boolean;
}) {
  const [read, setRead] = useState<string[]>([]);
  useEffect(() => {
    if (!showId) return;
    let stop = false;
    const ask = () => getAirStatus(showId).then(a => { if (!stop) setRead(a.cohosts ?? []); }).catch(() => {});
    ask();
    const t = setInterval(ask, 10000);
    return () => { stop = true; clearInterval(t); };
  }, [showId]);
  const names = named ?? read;

  return (
    <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4" data-testid="cohost-panel">
      <div className="text-[11px] text-gray-400 uppercase mb-2">Co-host</div>
      <div className="text-sm text-gray-300 mb-3" data-testid="cohost-names">
        {names.length === 0 ? 'No co-host on the show.' : `On the show with the host: ${names.join(', ')}`}
      </div>
      {co.on ? (
        <>
          <div className="text-sm font-bold text-red-400 mb-2" role="status" data-testid="cohost-on">
            You are on the show as a co-host{co.muted ? ', muted' : ''}. Listeners hear you after the delay.
          </div>
          <div className="flex gap-2">
            <button onClick={() => co.setMuted(!co.muted)} aria-pressed={co.muted} data-testid="cohost-mute"
              className={`flex-1 px-3 py-3 rounded text-sm font-bold transition ${co.muted ? 'bg-yellow-500 text-black' : 'bg-gray-700 hover:bg-gray-600 text-gray-200'}`}>
              {co.muted ? 'Unmute' : 'Mute'}
            </button>
            <button onClick={co.stop} data-testid="cohost-leave"
              className="flex-1 bg-red-800 hover:bg-red-700 text-red-200 px-3 py-3 rounded text-sm font-bold transition">
              Leave the show
            </button>
          </div>
        </>
      ) : (
        <>
          <button onClick={co.start} disabled={hosting} data-testid="cohost-join"
            title={hosting ? 'You are the host: your microphone is on the show already' : undefined}
            className="w-full bg-gray-700 hover:bg-gray-600 text-gray-200 px-3 py-3 rounded text-sm font-bold transition disabled:opacity-50">
            Join as co-host
          </button>
          <div className="text-[11px] text-gray-500 mt-2">
            For a second person in another place while someone else hosts. You hear the host and the callers here; use headphones.
          </div>
        </>
      )}
      {co.error && <div className="text-[11px] text-red-400 mt-2" role="status" data-testid="cohost-error">{co.error}</div>}
    </div>
  );
}
