import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listStations, createStation, updateStation, deleteStation } from '../api/stations';
import type { Station } from '../types';

export function useStations() {
  return useQuery({
    queryKey: ['stations'],
    queryFn: () => listStations(),
  });
}

export function useCreateStation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<Station>) => createStation(data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['stations'] }),
  });
}

export function useUpdateStation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Station> }) => updateStation(id, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['stations'] }),
  });
}

export function useDeleteStation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteStation(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['stations'] }),
  });
}
