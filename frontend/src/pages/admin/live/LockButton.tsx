import { useState } from 'react';
import { lockCall } from '../../../api/liveShows';
import type { CallInRequest } from '../../../api/liveShows';

/**
 * Lock a call that is on air (a guest, the caller the hour is about): while locked,
 * End Call, Hold and Next leave it alone, so a slip of the hand cannot drop it.
 * Dump with "drop the caller" still drops it, and the caller can always hang up.
 */
export default function LockButton({ showId, caller }: { showId: string | undefined; caller: CallInRequest }) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const locked = !!caller.locked;
  const toggle = async () => {
    if (!showId || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      await lockCall(showId, caller.id, !locked);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      onClick={toggle}
      disabled={busy}
      aria-pressed={locked}
      data-testid="lock-button"
      title={locked ? 'End Call, Hold and Next work on this call again' : 'End Call, Hold and Next leave this call alone until you unlock it'}
      className={`px-3 py-2 rounded text-sm transition disabled:opacity-50 ${locked ? 'bg-yellow-600 hover:bg-yellow-500 text-white font-bold' : 'bg-gray-700 hover:bg-gray-600 text-gray-200'}`}
    >
      {failed ? 'Try again' : locked ? 'Unlock' : 'Lock'}
    </button>
  );
}
