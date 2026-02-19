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
    mutationFn: ({ file, title, format, artist, album, asset_type, category }: {
      file: File;
      title: string;
      format?: string;
      artist?: string;
      album?: string;
      asset_type?: string;
      category?: string;
    }) => uploadAsset(file, title, format, artist, album, asset_type, category),
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
