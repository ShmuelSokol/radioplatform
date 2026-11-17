import type { QueueRow } from './QueuePanel';
import { serverNow } from '../../../lib/serverClock';

/** When a queued timed item cuts the song on air, or null if it waits for the song to end. */
export function cutAt(e: QueueRow): string | null {
  if (!e.preempt_at) return null;
  if (e.hard_timed === undefined) {
    // older server without hard_timed: sponsor slots are soft unless they say otherwise
    return e.source === 'ad_slot' ? null : e.preempt_at;
  }
  return e.hard_timed ? e.preempt_at : (e.hard_by ?? null);
}

/** The next queued item that cuts the song on air, and when. */
export function nextCut(rows: QueueRow[], now = serverNow()): { preempt_at: string; asset_id: string } | null {
  let best: { preempt_at: string; asset_id: string; t: number } | null = null;
  for (const e of rows) {
    if (e.status !== 'pending') continue;
    const at = cutAt(e);
    if (!at) continue;
    const t = new Date(at).getTime();
    if (t > now && (!best || t < best.t)) best = { preempt_at: at, asset_id: e.asset_id, t };
  }
  return best && { preempt_at: best.preempt_at, asset_id: best.asset_id };
}
