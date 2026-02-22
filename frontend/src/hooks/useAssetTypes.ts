import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listAssetTypes, createAssetType, updateAssetType, deleteAssetType } from '../api/assetTypes';

export function useAssetTypes() {
  return useQuery({
    queryKey: ['assetTypes'],
    queryFn: listAssetTypes,
    staleTime: 5 * 60 * 1000,
  });
}

export function useCreateAssetType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => createAssetType(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['assetTypes'] }),
  });
}

export function useUpdateAssetType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => updateAssetType(id, name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['assetTypes'] }),
  });
}

export function useDeleteAssetType() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteAssetType(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['assetTypes'] }),
  });
}
