import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getSongRequests, submitSongRequest, updateSongRequest, deleteSongRequest, getSongRequestCount } from '../api/songRequests';

export function useSongRequests(params?: { station_id?: string; status?: string }) {
  return useQuery({
    queryKey: ['song-requests', params],
    queryFn: () => getSongRequests(params),
    refetchInterval: 15000,
  });
}

export function useSongRequestCount(stationId: string | undefined) {
  return useQuery({
    queryKey: ['song-request-count', stationId],
    queryFn: () => getSongRequestCount(stationId!),
    enabled: !!stationId,
    refetchInterval: 30000,
  });
}

export function useSubmitSongRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: submitSongRequest,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['song-requests'] });
      qc.invalidateQueries({ queryKey: ['song-request-count'] });
    },
  });
}

export function useUpdateSongRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: { status?: string; asset_id?: string } }) =>
      updateSongRequest(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['song-requests'] });
    },
  });
}

export function useDeleteSongRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteSongRequest,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['song-requests'] });
    },
  });
}
