import apiClient from './client';
import type {
  ReviewQueue,
  ReviewQueueListResponse,
  ReviewItem,
  ReviewItemListResponse,
  ReviewAction,
} from '../types';

export const createReviewQueue = async (
  name: string,
  assetIds: string[],
  description?: string,
): Promise<ReviewQueue> => {
  const res = await apiClient.post<ReviewQueue>('/reviews/queues', {
    name,
    asset_ids: assetIds,
    description,
  });
  return res.data;
};

export const listReviewQueues = async (skip = 0, limit = 50): Promise<ReviewQueueListResponse> => {
  const res = await apiClient.get<ReviewQueueListResponse>('/reviews/queues', {
    params: { skip, limit },
  });
  return res.data;
};

export const getReviewQueue = async (id: string): Promise<ReviewQueue> => {
  const res = await apiClient.get<ReviewQueue>(`/reviews/queues/${id}`);
  return res.data;
};

export const listQueueItems = async (queueId: string): Promise<ReviewItemListResponse> => {
  const res = await apiClient.get<ReviewItemListResponse>(`/reviews/queues/${queueId}/items`);
  return res.data;
};

export const getNextItem = async (queueId: string): Promise<ReviewItem | null> => {
  const res = await apiClient.get<ReviewItem | null>(`/reviews/queues/${queueId}/next`);
  return res.data;
};

export const updateReviewItem = async (
  itemId: string,
  data: { status?: string; notes?: string; version: number },
): Promise<ReviewItem> => {
  const res = await apiClient.patch<ReviewItem>(`/reviews/items/${itemId}`, data);
  return res.data;
};

export const batchUpdateItems = async (
  queueId: string,
  itemIds: string[],
  status: string,
): Promise<{ updated: number }> => {
  const res = await apiClient.post<{ updated: number }>(`/reviews/queues/${queueId}/batch-update`, {
    item_ids: itemIds,
    status,
  });
  return res.data;
};

export const getAssetHistory = async (assetId: string): Promise<ReviewAction[]> => {
  const res = await apiClient.get<ReviewAction[]>(`/reviews/assets/${assetId}/history`);
  return res.data;
};

export const addAssetComment = async (assetId: string, comment: string): Promise<void> => {
  await apiClient.post(`/reviews/assets/${assetId}/comment`, { comment });
};

export const getQueueActivity = async (queueId: string): Promise<ReviewAction[]> => {
  const res = await apiClient.get<ReviewAction[]>(`/reviews/queues/${queueId}/activity`);
  return res.data;
};
