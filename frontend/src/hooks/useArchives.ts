import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getArchives, createArchive, updateArchive, deleteArchive } from '../api/archives';

export function useArchives(params?: { station_id?: string }) {
  return useQuery({
    queryKey: ['archives', params],
    queryFn: () => getArchives(params),
  });
}

export function useCreateArchive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createArchive,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['archives'] }),
  });
}

export function useUpdateArchive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<any> }) => updateArchive(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['archives'] }),
  });
}

export function useDeleteArchive() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteArchive,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['archives'] }),
  });
}
