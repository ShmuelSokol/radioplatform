import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listReviewQueues,
  getReviewQueue,
  listQueueItems,
  getNextItem,
  createReviewQueue,
  updateReviewItem,
  batchUpdateItems,
  getAssetHistory,
  addAssetComment,
  getQueueActivity,
} from '../api/reviews';

export function useReviewQueues(skip = 0, limit = 50) {
  return useQuery({
    queryKey: ['review-queues', skip, limit],
    queryFn: () => listReviewQueues(skip, limit),
  });
}

export function useReviewQueue(id: string | undefined) {
  return useQuery({
    queryKey: ['review-queue', id],
    queryFn: () => getReviewQueue(id!),
    enabled: !!id,
  });
}

export function useQueueItems(queueId: string | undefined) {
  return useQuery({
    queryKey: ['queue-items', queueId],
    queryFn: () => listQueueItems(queueId!),
    enabled: !!queueId,
  });
}

export function useNextReviewItem(queueId: string | undefined) {
  return useQuery({
    queryKey: ['next-review-item', queueId],
    queryFn: () => getNextItem(queueId!),
    enabled: !!queueId,
  });
}

export function useCreateReviewQueue() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, assetIds, description }: { name: string; assetIds: string[]; description?: string }) =>
      createReviewQueue(name, assetIds, description),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['review-queues'] }),
  });
}

export function useUpdateReviewItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ itemId, data }: { itemId: string; data: { status?: string; notes?: string; version: number } }) =>
      updateReviewItem(itemId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue-items'] });
      queryClient.invalidateQueries({ queryKey: ['next-review-item'] });
      queryClient.invalidateQueries({ queryKey: ['review-queue'] });
    },
  });
}

export function useBatchUpdate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ queueId, itemIds, status }: { queueId: string; itemIds: string[]; status: string }) =>
      batchUpdateItems(queueId, itemIds, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue-items'] });
      queryClient.invalidateQueries({ queryKey: ['review-queues'] });
    },
  });
}

export function useAssetHistory(assetId: string | undefined) {
  return useQuery({
    queryKey: ['asset-history', assetId],
    queryFn: () => getAssetHistory(assetId!),
    enabled: !!assetId,
  });
}

export function useAddComment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ assetId, comment }: { assetId: string; comment: string }) =>
      addAssetComment(assetId, comment),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['asset-history'] }),
  });
}

export function useQueueActivity(queueId: string | undefined) {
  return useQuery({
    queryKey: ['queue-activity', queueId],
    queryFn: () => getQueueActivity(queueId!),
    enabled: !!queueId,
  });
}
