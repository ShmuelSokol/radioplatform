/**
 * Pure helpers that apply a queue action to the cached queue response so the
 * UI changes the moment a button is clicked. The server response (refetched
 * when the request settles) always replaces whatever these produce.
 */

export interface QueueEntryLike {
  id: string;
  status: string;
  position: number;
  estimated_start?: string | null;
  estimate_firm?: boolean;
  preempt_at?: string | null;
  [key: string]: unknown;
}

export interface QueueDataLike {
  entries: QueueEntryLike[];
  total?: number;
  total_all?: number;
  [key: string]: unknown;
}

export const OPTIMISTIC_PREFIX = 'optimistic-';

/** A movable entry: waiting to play and not pinned to a clock time. */
const isRegular = (e: QueueEntryLike) => e.status === 'pending' && !e.preempt_at;

const withEntries = (data: QueueDataLike, entries: QueueEntryLike[]): QueueDataLike => {
  const delta = entries.length - data.entries.length;
  return {
    ...data,
    entries,
    total: data.total != null ? data.total + delta : data.total,
    total_all: data.total_all != null ? data.total_all + delta : data.total_all,
  };
};

/**
 * Put `moved` into the slot at `toIdx`, keeping each slot's position and
 * estimated start time (the time belongs to the slot, not the song).
 */
function reslot(entries: QueueEntryLike[], order: QueueEntryLike[]): QueueEntryLike[] {
  return order.map((e, i) =>
    e === entries[i]
      ? e
      : { ...e, position: entries[i].position, estimated_start: entries[i].estimated_start, estimate_firm: entries[i].estimate_firm },
  );
}

export function removeEntry(data: QueueDataLike, entryId: string): QueueDataLike {
  if (!data.entries.some((e) => e.id === entryId)) return data;
  return withEntries(data, data.entries.filter((e) => e.id !== entryId));
}

export function moveEntry(data: QueueDataLike, entryId: string, direction: -1 | 1): QueueDataLike {
  const from = data.entries.findIndex((e) => e.id === entryId);
  if (from < 0 || data.entries[from].status !== 'pending') return data;
  let to = from + direction;
  while (to >= 0 && to < data.entries.length && !isRegular(data.entries[to])) to += direction;
  if (to < 0 || to >= data.entries.length) return data;
  const order = data.entries.slice();
  [order[from], order[to]] = [order[to], order[from]];
  return { ...data, entries: reslot(data.entries, order) };
}

/** Drag-and-drop: move the entry to the slot currently holding `newPosition`. */
export function reorderEntry(data: QueueDataLike, entryId: string, newPosition: number): QueueDataLike {
  const from = data.entries.findIndex((e) => e.id === entryId);
  const to = data.entries.findIndex((e) => e.status === 'pending' && e.position === newPosition);
  if (from < 0 || to < 0 || from === to) return data;
  // Only the regular entries between the two slots shift; pinned ones stay put.
  const lo = Math.min(from, to);
  const hi = Math.max(from, to);
  const slots: number[] = [];
  for (let i = lo; i <= hi; i++) {
    if (i === from || i === to || isRegular(data.entries[i])) slots.push(i);
  }
  const moving = slots.map((i) => data.entries[i]);
  if (from < to) moving.push(moving.shift()!);
  else moving.unshift(moving.pop()!);
  const order = data.entries.slice();
  slots.forEach((slot, i) => { order[slot] = moving[i]; });
  return { ...data, entries: reslot(data.entries, order) };
}

/** Add a placeholder row for a song the server has not confirmed yet. */
export function insertEntry(
  data: QueueDataLike,
  stationId: string,
  asset: { id: string; [key: string]: unknown },
  where: 'end' | 'next' | { beforeId: string },
): QueueDataLike {
  const maxPos = data.entries.reduce((m, e) => (e.status === 'pending' ? Math.max(m, e.position) : m), 0);
  const entry: QueueEntryLike = {
    id: `${OPTIMISTIC_PREFIX}${asset.id}-${data.entries.length}`,
    station_id: stationId,
    asset_id: asset.id,
    position: maxPos + 1,
    status: 'pending',
    estimated_start: null,
    source: 'manual',
    preempt_at: null,
    asset,
  };
  if (where === 'end') return withEntries(data, [...data.entries, entry]);
  let idx = 0;
  if (where === 'next') {
    // "Play next": directly after whatever is playing now.
    data.entries.forEach((e, i) => { if (e.status === 'playing') idx = i + 1; });
  } else {
    // "Insert here": directly before the marked entry.
    idx = data.entries.findIndex((e) => e.id === where.beforeId);
    if (idx < 0) return data;
  }
  const next = data.entries[idx];
  entry.position = next ? next.position : maxPos + 1;
  entry.estimated_start = next?.estimated_start ?? null;
  entry.estimate_firm = next?.estimate_firm;
  const entries = data.entries.slice();
  entries.splice(idx, 0, entry);
  return withEntries(data, entries);
}
