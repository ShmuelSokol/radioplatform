import { useEffect, useState } from 'react';
import { changeMarkedCaller, getMarkedCallers } from '../../../api/liveShows';
import type { MarkedCaller } from '../../../api/liveShows';

/**
 * Every caller the staff marked as priority or blocked, on any show: the place to
 * lift a block (or a priority) when the person is not calling right now.
 * `refresh` changes whenever a caller card changed a standing.
 */
export default function MarkedCallers({ refresh }: { refresh: string }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<MarkedCaller[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let stop = false;
    getMarkedCallers()
      .then(r => { if (!stop) { setRows(r); setFailed(false); } })
      .catch(() => { if (!stop) setFailed(true); });
    return () => { stop = true; };
  }, [refresh]);

  const makeNormal = async (id: string) => {
    try {
      await changeMarkedCaller(id, { standing: 'normal' });
      setRows(prev => (prev ? prev.filter(r => r.id !== id) : prev));
    } catch {
      setFailed(true);
    }
  };

  const blocked = rows?.filter(r => r.standing === 'blocked').length ?? 0;
  const priority = rows?.filter(r => r.standing === 'priority').length ?? 0;
  return (
    <div className="mt-6 bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4" data-testid="marked-callers">
      <button onClick={() => setOpen(o => !o)} aria-expanded={open} data-testid="marked-callers-toggle"
        className="w-full flex items-center justify-between gap-2 text-left py-1">
        <span className="text-sm font-bold text-gray-400 uppercase">Priority and blocked callers</span>
        <span className="text-[11px] text-gray-400">
          {rows === null ? '' : `${priority} priority, ${blocked} blocked`} {open ? '▲' : '▼'}
        </span>
      </button>
      {failed && <div className="text-[11px] text-red-400 mt-1" role="status">The list could not be read or changed. Try again.</div>}
      {open && rows !== null && (
        rows.length === 0 ? (
          <div className="text-sm text-gray-500 mt-2">Nobody is marked. Use Priority or Block on a caller's card.</div>
        ) : (
          <ul className="mt-2 space-y-2">
            {rows.map(r => (
              <li key={r.id} className="flex flex-wrap items-center gap-2 border-t border-[#2a2a5e] pt-2" data-testid="marked-caller">
                <span className={`text-[10px] text-white px-2 py-0.5 rounded-full font-bold uppercase ${r.standing === 'blocked' ? 'bg-red-800' : 'bg-yellow-600'}`}>
                  {r.standing}
                </span>
                <span className="text-sm text-white font-medium break-words min-w-0">{r.name || r.phone || 'No name'}</span>
                <span className="text-[11px] text-gray-500">{r.phone || 'Web caller'}</span>
                {r.note && <span className="text-[11px] text-gray-300 break-words min-w-0">{r.note}</span>}
                <button onClick={() => makeNormal(r.id)} data-testid="marked-caller-clear"
                  className="ml-auto bg-gray-700 hover:bg-gray-600 text-gray-200 px-3 py-2 rounded text-[11px] transition">
                  {r.standing === 'blocked' ? 'Allow again' : 'Not priority'}
                </button>
              </li>
            ))}
          </ul>
        )
      )}
    </div>
  );
}
