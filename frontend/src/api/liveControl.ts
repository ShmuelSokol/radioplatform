/**
 * Live-control actions used by the control room (pages/admin/Dashboard.tsx).
 *
 * This is the ONE place that decides which backend calls mean "fire now" and
 * "insert at this point". Today they are composed from the existing queue
 * endpoints. When the playout engine ships dedicated atomic endpoints, switch
 * the bodies here and nothing else in the UI has to change.
 */
import { addToQueue, playNext, skipCurrent, reorderDnd } from './queue';

export interface InsertResult {
  warnings: string[];
}

/** Put the asset on air right away: queue it as next, then advance to it. */
export const fireNow = async (stationId: string, assetId: string) => {
  await playNext(stationId, assetId);
  return skipCurrent(stationId);
};

/** Play the asset as soon as the current item ends. */
export const queueNext = (stationId: string, assetId: string) => playNext(stationId, assetId);

/** Add the asset to the end of the queue. */
export const queueEnd = (stationId: string, assetId: string) => addToQueue(stationId, assetId);

/**
 * Insert the asset so it plays at `position` (i.e. just before the entry that
 * currently holds that position). Returns scheduling-rule warnings, if any.
 */
export const insertAt = async (
  stationId: string,
  assetId: string,
  position: number,
): Promise<InsertResult> => {
  const added = (await addToQueue(stationId, assetId)) as { id: string; position: number };
  if (added.position === position) return { warnings: [] };
  const res = await reorderDnd(stationId, added.id, position);
  return { warnings: res.warnings ?? [] };
};
