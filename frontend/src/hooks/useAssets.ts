import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listAssets, uploadAsset, deleteAsset } from '../api/assets';

export function useAssets(skip = 0, limit = 100) {
  return useQuery({
    queryKey: ['assets', skip, limit],
    queryFn: () => listAssets(skip, limit),
  });
}

export function useUploadAsset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, title }: { file: File; title: string }) => uploadAsset(file, title),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['assets'] }),
  });
}

export function useDeleteAsset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteAsset(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['assets'] }),
  });
}
