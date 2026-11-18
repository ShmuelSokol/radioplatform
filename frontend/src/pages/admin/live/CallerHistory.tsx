import { useState } from 'react';
import { setCallerStanding } from '../../../api/liveShows';
import type { CallInRequest, CallerStanding } from '../../../api/liveShows';

const day = (iso: string) => new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
const times = (n: number) => (n === 1 ? 'once' : `${n} times`);

/**
 * Who is this caller to the station: how often they called before and were on air,
 * and what the staff decided about them (priority, blocked, a note about the person).
 * On every caller card of the Call Screener and the Host Console. `readOnly` shows
 * it without the buttons. A call that does not say who is calling shows nothing.
 */
export default function CallerHistory({ showId, caller, readOnly = false }: {
  showId: string | undefined;
  caller: CallInRequest;
  readOnly?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [sure, setSure] = useState(false);       // Block asks once more, on the button itself
  const [failed, setFailed] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState('');
  const h = caller.history;
  if (!h) return null;

  const change = async (data: { standing?: CallerStanding; note?: string }) => {
    if (!showId || busy) return;
    setBusy(true);
    setFailed(null);
    try {
      await setCallerStanding(showId, caller.id, data);
      setEditing(false);
    } catch (e: unknown) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setFailed(typeof detail === 'string' ? detail : 'Not saved. Try again.');
    } finally {
      setBusy(false);
      setSure(false);
    }
  };

  const small = 'px-2 py-1.5 rounded text-[11px] transition disabled:opacity-50';
  return (
    <div className="mb-2" data-testid="caller-history">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-400">
        {h.standing === 'priority' && (
          <span className="bg-yellow-600 text-white px-2 py-0.5 rounded-full font-bold uppercase" data-testid="caller-priority">Priority</span>
        )}
        {h.standing === 'blocked' && (
          <span className="bg-red-800 text-white px-2 py-0.5 rounded-full font-bold uppercase" data-testid="caller-blocked">Blocked</span>
        )}
        <span data-testid="caller-history-line">
          {h.calls === 0
            ? 'First call'
            : `Called ${times(h.calls)} before, on air ${h.on_air === 0 ? 'never' : times(h.on_air)}${h.last_at ? `, last ${day(h.last_at)}` : ''}`}
        </span>
      </div>
      {h.note && !editing && (
        <div className="text-[11px] text-gray-300 mt-1 whitespace-pre-wrap break-words" data-testid="caller-person-note">About the caller: {h.note}</div>
      )}
      {!readOnly && editing && (
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            value={text}
            onChange={e => setText(e.target.value)}
            maxLength={500}
            autoFocus
            aria-label="Note about this person, kept for their later calls"
            placeholder="About this person, kept for their later calls"
            onKeyDown={e => {
              if (e.key === 'Enter') change({ note: text });
              if (e.key === 'Escape') { e.stopPropagation(); setEditing(false); }
            }}
            className="flex-1 min-w-[10rem] bg-[#0a0a28] border border-[#2a2a5e] rounded px-2 py-1 text-sm text-white"
          />
          <button onClick={() => change({ note: text })} disabled={busy} className={`${small} bg-cyan-800 hover:bg-cyan-700 text-cyan-200`}>Save</button>
          <button onClick={() => setEditing(false)} className={`${small} bg-gray-700 hover:bg-gray-600 text-gray-200`}>Cancel</button>
        </div>
      )}
      {!readOnly && !editing && (
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <button
            onClick={() => change({ standing: h.standing === 'priority' ? 'normal' : 'priority' })}
            disabled={busy}
            data-testid="caller-priority-button"
            title="Priority callers are listed first, on every show"
            className={`${small} bg-gray-700 hover:bg-gray-600 text-gray-200`}
          >
            {h.standing === 'priority' ? 'Not priority' : 'Priority'}
          </button>
          {h.standing === 'blocked' ? (
            <button onClick={() => change({ standing: 'normal' })} disabled={busy} data-testid="caller-unblock-button"
              className={`${small} bg-gray-700 hover:bg-gray-600 text-gray-200`}>
              Allow again
            </button>
          ) : sure ? (
            <>
              <button onClick={() => change({ standing: 'blocked' })} disabled={busy} data-testid="caller-block-sure"
                className={`${small} bg-red-800 hover:bg-red-700 text-white font-bold`}>
                {caller.status === 'on_air' ? 'Yes, block later calls' : 'Yes, block and remove'}
              </button>
              <button onClick={() => setSure(false)} className={`${small} bg-gray-700 hover:bg-gray-600 text-gray-200`}>No</button>
            </>
          ) : (
            <button onClick={() => setSure(true)} disabled={busy} data-testid="caller-block-button"
              title="Their calls are refused from now on, on every show"
              className={`${small} bg-gray-700 hover:bg-gray-600 text-gray-200`}>
              Block
            </button>
          )}
          <button onClick={() => { setText(h.note || ''); setFailed(null); setEditing(true); }} data-testid="caller-person-note-button"
            className="text-[11px] text-gray-400 hover:text-cyan-400 underline px-1 py-1">
            {h.note ? 'edit about' : 'about the caller'}
          </button>
        </div>
      )}
      {failed && <div className="text-[11px] text-red-400 mt-1" role="status">{failed}</div>}
    </div>
  );
}
