import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';
import { insertEntry, moveEntry, removeEntry, reorderEntry } from './queueOptimistic';
import type { QueueDataLike } from './queueOptimistic';
import { getQueue, getPlayLog, addToQueue, playNext, skipCurrent, moveUp, moveDown, removeFromQueue, startPlayback, getLastPlayed, previewWeather, reorderDnd } from '../api/queue';

export function useQueue(stationId: string | null) {
  return useQuery({
    queryKey: ['queue', stationId],
    queryFn: () => getQueue(stationId!),
    enabled: !!stationId,
    refetchInterval: 10_000,
  });
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
    mutationFn: () => skipCurrent(stationId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['queue', stationId] });
      qc.invalidateQueries({ queryKey: ['play-log', stationId] });
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
