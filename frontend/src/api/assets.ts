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

export const uploadAsset = async (
  file: File,
  title: string,
  format = 'mp3',
  artist?: string,
  album?: string,
  asset_type = 'music',
  category?: string,
): Promise<Asset> => {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('title', title);
  formData.append('format', format);
  if (artist) formData.append('artist', artist);
  if (album) formData.append('album', album);
  formData.append('asset_type', asset_type);
  if (category) formData.append('category', category);
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

export const downloadAsset = async (id: string, title: string, format = 'original'): Promise<void> => {
  const res = await apiClient.get(`/assets/${id}/download`, {
    params: { format },
    responseType: 'blob',
  });
  const ext = format === 'original' ? 'mp3' : format;
  const url = window.URL.createObjectURL(new Blob([res.data]));
  const a = document.createElement('a');
  a.href = url;
  a.download = `${title}.${ext}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
};

export const deleteAsset = async (id: string): Promise<void> => {
  await apiClient.delete(`/assets/${id}`);
};
