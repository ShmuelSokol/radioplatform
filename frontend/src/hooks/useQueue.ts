import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getQueue, getPlayLog, addToQueue, playNext, skipCurrent, moveUp, moveDown, removeFromQueue, startPlayback, getLastPlayed, previewWeather, reorderDnd } from '../api/queue';

export function useQueue(stationId: string | null) {
  return useQuery({
    queryKey: ['queue', stationId],
    queryFn: () => getQueue(stationId!),
    enabled: !!stationId,
    refetchInterval: 5000,
  });
}

export function usePlayLog(stationId: string | null) {
  return useQuery({
    queryKey: ['play-log', stationId],
    queryFn: () => getPlayLog(stationId!, 30),
    enabled: !!stationId,
    refetchInterval: 10000,
  });
}

export function useAddToQueue(stationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (assetId: string) => addToQueue(stationId, assetId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['queue', stationId] }),
  });
}

export function usePlayNext(stationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (assetId: string) => playNext(stationId, assetId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['queue', stationId] }),
  });
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
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (entryId: string) => moveUp(stationId, entryId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['queue', stationId] }),
  });
}

export function useMoveDown(stationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (entryId: string) => moveDown(stationId, entryId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['queue', stationId] }),
  });
}

export function useRemoveFromQueue(stationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (entryId: string) => removeFromQueue(stationId, entryId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['queue', stationId] }),
  });
}

export function useStartPlayback(stationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => startPlayback(stationId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['queue', stationId] }),
  });
}

export function useReorderDnd(stationId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ entryId, newPosition }: { entryId: string; newPosition: number }) =>
      reorderDnd(stationId, entryId, newPosition),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['queue', stationId] }),
  });
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
