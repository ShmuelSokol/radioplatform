import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { listAssets, uploadAsset, updateAsset, deleteAsset, getAsset, getAssetAudioUrl, detectSilence, trimAsset, restoreOriginal, bulkSetCategory } from '../api/assets';
import type { ListAssetsParams } from '../api/assets';
import type { AssetListResponse } from '../types';

export function useAssets(params: ListAssetsParams & { enabled?: boolean } = {}) {
  const { enabled = true, ...queryParams } = params;
  return useQuery<AssetListResponse>({
    queryKey: ['assets', queryParams],
    queryFn: () => listAssets(queryParams),
    enabled,
    placeholderData: keepPreviousData,
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

export function useUpdateAsset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Record<string, any> }) => updateAsset(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets'] });
      queryClient.invalidateQueries({ queryKey: ['asset'] });
      queryClient.invalidateQueries({ queryKey: ['queue-items'] });
    },
  });
}

export function useDeleteAsset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteAsset(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['assets'] }),
  });
}

export function useBulkSetCategory() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ assetIds, category }: { assetIds: string[]; category: string }) =>
      bulkSetCategory(assetIds, category),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['assets'] }),
  });
}

export function useAssetDetail(assetId: string | undefined) {
  return useQuery({
    queryKey: ['asset', assetId],
    queryFn: () => getAsset(assetId!),
    enabled: !!assetId,
  });
}

export function useAssetAudioUrl(assetId: string | undefined) {
  return useQuery({
    queryKey: ['asset-audio-url', assetId],
    queryFn: () => getAssetAudioUrl(assetId!),
    enabled: !!assetId,
  });
}

export function useDetectSilence() {
  return useMutation({
    mutationFn: ({ id, thresholdDb, minDuration }: { id: string; thresholdDb?: number; minDuration?: number }) =>
      detectSilence(id, thresholdDb, minDuration),
  });
}

export function useTrimAsset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, trimStart, trimEnd }: { id: string; trimStart: number; trimEnd: number }) =>
      trimAsset(id, trimStart, trimEnd),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets'] });
      queryClient.invalidateQueries({ queryKey: ['asset'] });
      queryClient.invalidateQueries({ queryKey: ['asset-audio-url'] });
    },
  });
}

export function useRestoreOriginal() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => restoreOriginal(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['assets'] });
      queryClient.invalidateQueries({ queryKey: ['asset'] });
      queryClient.invalidateQueries({ queryKey: ['asset-audio-url'] });
    },
  });
}
