import { useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';
import { insertEntry, moveEntry, removeEntry, reorderEntry } from './queueOptimistic';
import type { QueueDataLike } from './queueOptimistic';
import { getQueue, getPlayLog, addToQueue, playNext, skipCurrent, moveUp, moveDown, removeFromQueue, startPlayback, getLastPlayed, previewWeather, reorderDnd } from '../api/queue';
import type { SkipFade } from '../api/queue';

/** The first rows of the queue: a few hours ahead. Enough for the screen, and
 *  about a tenth of a days-long queue on every 10-second poll (Mac test: 1,451
 *  rows were 708 KB a poll, 4 MB a minute on an iPhone). */
export const QUEUE_FIRST_ROWS = 150;
export const QUEUE_ALL_ROWS = 5000; // the server's most

/** `limit` grows to QUEUE_ALL_ROWS only when the operator scrolls to the end of
 *  what is loaded or picks a day. The key stays ['queue', stationId] so the
 *  optimistic updates elsewhere keep working. */
export function useQueue(stationId: string | null, limit: number = QUEUE_FIRST_ROWS) {
  const q = useQuery({
    queryKey: ['queue', stationId],
    queryFn: () => getQueue(stationId!, limit),
    enabled: !!stationId,
    refetchInterval: 10_000,
  });
  const { refetch } = q;
  const shown = useRef(limit);
  useEffect(() => {
    if (shown.current === limit) return;
    shown.current = limit;
    if (stationId) refetch();
  }, [limit, stationId, refetch]);
  return q;
}

export function usePlayLog(stationId: string | null, enabled = true) {
  return useQuery({
    queryKey: ['play-log', stationId],
    queryFn: () => getPlayLog(stationId!, 30),
    enabled: !!stationId && enabled,
    refetchInterval: 15_000,
  });
}

/**
 * Queue actions show their result immediately: the cached queue is updated on
 * click, rolled back if the server rejects the request, and replaced by the
 * server's version once the request settles.
 */
function useOptimisticQueueMutation<TVars, TData>(
  stationId: string,
  mutationFn: (vars: TVars) => Promise<TData>,
  apply: (data: QueueDataLike, vars: TVars, qc: QueryClient) => QueueDataLike,
) {
  const qc = useQueryClient();
  const key = ['queue', stationId];
  return useMutation({
    mutationFn,
    onMutate: async (vars: TVars) => {
      // Stop an in-flight poll from overwriting the optimistic state.
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<QueueDataLike>(key);
      if (previous?.entries) qc.setQueryData<QueueDataLike>(key, apply(previous, vars, qc));
      return { previous };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(key, ctx.previous);
    },
    onSettled: () => {
      // Only refetch once the last queued action has finished, so a burst of
      // clicks does not flash intermediate server states.
      if (qc.isMutating({ mutationKey: key }) <= 1) qc.invalidateQueries({ queryKey: key });
    },
    mutationKey: key,
    // Send a station's queue actions to the server one at a time, in click order.
    scope: { id: `queue-${stationId}` },
  });
}

/** Find a library asset in any cached asset list, for the placeholder row. */
function findCachedAsset(qc: QueryClient, assetId: string) {
  for (const [, data] of qc.getQueriesData<{ assets?: { id: string }[] }>({ queryKey: ['assets'] })) {
    const hit = data?.assets?.find((a) => a.id === assetId);
    if (hit) return hit;
  }
  return null;
}

function insertCached(where: 'end' | 'next', stationId: string) {
  return (data: QueueDataLike, assetId: string, qc: QueryClient) => {
    const asset = findCachedAsset(qc, assetId);
    return asset ? insertEntry(data, stationId, asset, where) : data;
  };
}

export function useAddToQueue(stationId: string) {
  return useOptimisticQueueMutation(
    stationId,
    (assetId: string) => addToQueue(stationId, assetId),
    insertCached('end', stationId),
  );
}

export function usePlayNext(stationId: string) {
  return useOptimisticQueueMutation(
    stationId,
    (assetId: string) => playNext(stationId, assetId),
    insertCached('next', stationId),
  );
}

export function useSkipCurrent(stationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fade?: SkipFade) => skipCurrent(stationId, fade),
    onMutate: async () => {
      // A poll already in flight would put the old song back
      await qc.cancelQueries({ queryKey: ['queue', stationId] });
    },
    onSuccess: (res) => {
      // The skip response carries the new queue: no second request needed
      if (res?.queue?.entries) qc.setQueryData(['queue', stationId], res.queue);
      else qc.invalidateQueries({ queryKey: ['queue', stationId] });
      // The play log catches up on its own poll
      qc.invalidateQueries({ queryKey: ['play-log', stationId], refetchType: 'none' });
    },
  });
}

export function useMoveUp(stationId: string) {
  return useOptimisticQueueMutation(
    stationId,
    (entryId: string) => moveUp(stationId, entryId),
    (data, entryId) => moveEntry(data, entryId, -1),
  );
}

export function useMoveDown(stationId: string) {
  return useOptimisticQueueMutation(
    stationId,
    (entryId: string) => moveDown(stationId, entryId),
    (data, entryId) => moveEntry(data, entryId, 1),
  );
}

export function useRemoveFromQueue(stationId: string) {
  return useOptimisticQueueMutation(
    stationId,
    (entryId: string) => removeFromQueue(stationId, entryId),
    removeEntry,
  );
}

export function useStartPlayback(stationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => startPlayback(stationId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['queue', stationId] }),
  });
}

export function useReorderDnd(stationId: string) {
  return useOptimisticQueueMutation(
    stationId,
    ({ entryId, newPosition }: { entryId: string; newPosition: number }) =>
      reorderDnd(stationId, entryId, newPosition),
    (data, { entryId, newPosition }) => reorderEntry(data, entryId, newPosition),
  );
}

export function useWeatherPreview(stationId: string) {
  return useMutation({
    mutationFn: () => previewWeather(stationId),
  });
}

export function useLastPlayed(stationId: string | null) {
  return useQuery({
    queryKey: ['last-played', stationId],
    queryFn: () => getLastPlayed(stationId!),
    enabled: !!stationId,
    refetchInterval: 30000,
  });
}
