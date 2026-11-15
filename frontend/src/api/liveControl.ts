/**
 * Live-control actions used by the control room (pages/admin/Dashboard.tsx).
 *
 * This is the ONE place that decides which backend calls mean "fire now" and
 * "insert at this point". Each is a single request; the server contract is in
 * wiki/operator-actions.md.
 */
import { addToQueue, playNext, fireNowRequest, insertAfter, setLoop, mixOverRequest } from './queue';

/** Two taps on the same button inside this window count as one. */
const DOUBLE_TAP_MS = 1500;
const lastFire = new Map<string, { at: number; requestId: string }>();

const newRequestId = () =>
  (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;

/**
 * Put the asset on air right away. A second tap on the same item within a moment
 * reuses the request id, and the server answers it without playing the item twice.
 */
/** One request id per tap; a second tap on the same item within a moment reuses it. */
const tapRequestId = (kind: string, stationId: string, assetId: string) => {
  const key = `${kind}:${stationId}:${assetId}`;
  const prev = lastFire.get(key);
  const now = Date.now();
  const requestId = prev && now - prev.at < DOUBLE_TAP_MS ? prev.requestId : newRequestId();
  lastFire.set(key, { at: now, requestId });
  return requestId;
};

export const fireNow = (stationId: string, assetId: string) =>
  fireNowRequest(stationId, assetId, tapRequestId('fire', stationId, assetId));

/**
 * Mix over: play the asset on top of the item on air, which carries on. The queue
 * does not move. A double tap plays it once, the same way as fireNow.
 */
export const mixOver = (stationId: string, assetId: string) =>
  mixOverRequest(stationId, assetId, tapRequestId('mix', stationId, assetId));

/** Play the asset as soon as the current item ends. */
export const queueNext = (stationId: string, assetId: string) => playNext(stationId, assetId);

/** Add the asset to the end of the queue. */
export const queueEnd = (stationId: string, assetId: string) => addToQueue(stationId, assetId);

/**
 * Insert the asset right after the given queue entry; null means at the top,
 * so it plays next.
 */
export const insertAt = (stationId: string, assetId: string, afterEntryId: string | null) =>
  insertAfter(stationId, [assetId], afterEntryId);

/** Switch looping of the item on air on or off. */
export const loopCurrent = (stationId: string, on: boolean) => setLoop(stationId, on);
