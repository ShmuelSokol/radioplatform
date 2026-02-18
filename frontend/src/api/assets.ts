import apiClient from './client';
import type { Asset, AssetListResponse } from '../types';

export const listAssets = async (skip = 0, limit = 50): Promise<AssetListResponse> => {
  const res = await apiClient.get<AssetListResponse>('/assets', { params: { skip, limit } });
  return res.data;
};

export const getAsset = async (id: string): Promise<Asset> => {
  const res = await apiClient.get<Asset>(`/assets/${id}`);
  return res.data;
};

export const uploadAsset = async (file: File, title: string): Promise<Asset> => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('title', title);
  const res = await apiClient.post<Asset>('/assets/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
};

export const transcodeAsset = async (id: string, codec = 'aac', bitrate = '128k') => {
  const res = await apiClient.post(`/assets/${id}/transcode`, { codec, bitrate });
  return res.data;
};

export const clipAsset = async (id: string, start: number, duration: number) => {
  const res = await apiClient.post(`/assets/${id}/clip`, { start, duration });
  return res.data;
};

export const deleteAsset = async (id: string): Promise<void> => {
  await apiClient.delete(`/assets/${id}`);
};
