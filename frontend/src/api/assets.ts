import apiClient from './client';
import type { Asset, AssetListResponse } from '../types';

export interface ListAssetsParams {
  skip?: number;
  limit?: number;
  search?: string;
  asset_type?: string;
  category?: string;
  title_search?: string;
  artist_search?: string;
  album_search?: string;
  duration_min?: number;
  duration_max?: number;
}

export const listAssets = async (
  params: ListAssetsParams = {},
): Promise<AssetListResponse> => {
  const query: Record<string, any> = {};
  if (params.skip != null) query.skip = params.skip;
  if (params.limit != null) query.limit = params.limit;
  if (params.search) query.search = params.search;
  if (params.asset_type) query.asset_type = params.asset_type;
  if (params.category) query.category = params.category;
  if (params.title_search) query.title_search = params.title_search;
  if (params.artist_search) query.artist_search = params.artist_search;
  if (params.album_search) query.album_search = params.album_search;
  if (params.duration_min != null) query.duration_min = params.duration_min;
  if (params.duration_max != null) query.duration_max = params.duration_max;
  const res = await apiClient.get<AssetListResponse>('/assets', { params: query });
  return res.data;
};

export interface BulkCategoryParams {
  assetIds?: string[];
  category: string;
  // Filter-based selection:
  asset_type?: string;
  category_filter?: string;
  title_search?: string;
  artist_search?: string;
  album_search?: string;
  duration_min?: number;
  duration_max?: number;
}

export const bulkSetCategory = async (
  params: BulkCategoryParams,
): Promise<{ updated: number }> => {
  const body: Record<string, any> = { category: params.category };
  if (params.assetIds) body.asset_ids = params.assetIds;
  if (params.asset_type) body.asset_type = params.asset_type;
  if (params.category_filter) body.category_filter = params.category_filter;
  if (params.title_search) body.title_search = params.title_search;
  if (params.artist_search) body.artist_search = params.artist_search;
  if (params.album_search) body.album_search = params.album_search;
  if (params.duration_min != null) body.duration_min = params.duration_min;
  if (params.duration_max != null) body.duration_max = params.duration_max;
  const res = await apiClient.patch<{ updated: number }>('/assets/bulk-category', body);
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

export const updateAsset = async (id: string, data: Partial<Asset>): Promise<Asset> => {
  const res = await apiClient.patch<Asset>(`/assets/${id}`, data);
  return res.data;
};

export const deleteAsset = async (id: string): Promise<void> => {
  await apiClient.delete(`/assets/${id}`);
};

export const getAssetAudioUrl = async (id: string): Promise<string> => {
  const res = await apiClient.get<{ url: string }>(`/assets/${id}/audio-url`);
  return res.data.url;
};

export const detectSilence = async (
  id: string,
  thresholdDb = -30,
  minDuration = 0.5,
): Promise<{ silence_regions: Array<{ start: number; end: number; duration: number }> }> => {
  const res = await apiClient.post(`/assets/${id}/detect-silence`, null, {
    params: { threshold_db: thresholdDb, min_duration: minDuration },
  });
  return res.data;
};

export const trimAsset = async (
  id: string,
  trimStart: number,
  trimEnd: number,
): Promise<Asset> => {
  const res = await apiClient.post<Asset>(`/assets/${id}/trim`, null, {
    params: { trim_start: trimStart, trim_end: trimEnd },
  });
  return res.data;
};

export const restoreOriginal = async (id: string): Promise<Asset> => {
  const res = await apiClient.post<Asset>(`/assets/${id}/restore-original`);
  return res.data;
};

export interface MixRequest {
  backtrack_asset_id: string;
  overlay_asset_id: string;
  output_title: string;
  output_asset_type?: string;
  bt_trim_start?: number;
  bt_trim_end?: number;
  bt_target_dur?: number;
  bt_volume?: number;
  ov_volume?: number;
  bt_fade_in?: number;
  bt_fade_out?: number;
  bt_fade_out_start?: number;
  ov_fade_in?: number;
  ov_fade_out?: number;
  ov_fade_out_start?: number;
}

export const mixTracks = async (body: MixRequest): Promise<Asset> => {
  const res = await apiClient.post<Asset>('/studio/mix', body);
  return res.data;
};

export interface BulkAutoTrimParams {
  asset_ids?: string[];
  // Filter-based selection:
  asset_type?: string;
  category?: string;
  title_search?: string;
  artist_search?: string;
  album_search?: string;
  duration_min?: number;
  duration_max?: number;
  threshold_db?: number;
  min_silence?: number;
}

export interface BulkAutoTrimStatus {
  job_id: string;
  status: 'running' | 'completed' | 'failed' | 'queued';
  total: number;
  processed: number;
  trimmed: number;
  skipped: number;
  errors: number;
}

export const bulkAutoTrim = async (params: BulkAutoTrimParams): Promise<{ job_id: string }> => {
  const res = await apiClient.post<{ job_id: string }>('/assets/bulk-auto-trim', params);
  return res.data;
};

export const getBulkAutoTrimStatus = async (jobId: string): Promise<BulkAutoTrimStatus> => {
  const res = await apiClient.get<BulkAutoTrimStatus>(`/assets/bulk-auto-trim/status/${jobId}`);
  return res.data;
};

// --- Audio Enhancement ---

export interface EnhanceFilter {
  name: string;
  params: Record<string, number>;
}

export interface EnhanceRequest {
  filters?: EnhanceFilter[];
  preset?: string;
}

export interface EnhancePreviewRequest {
  filters?: EnhanceFilter[];
  preset?: string;
  start_seconds?: number;
  duration_seconds?: number;
}

export interface EnhancePreset {
  [presetName: string]: EnhanceFilter[];
}

export const getEnhancePresets = async (): Promise<{ presets: EnhancePreset }> => {
  const res = await apiClient.get<{ presets: EnhancePreset }>('/assets/enhance-presets');
  return res.data;
};

export const enhanceAsset = async (id: string, body: EnhanceRequest): Promise<Asset> => {
  const res = await apiClient.post<Asset>(`/assets/${id}/enhance`, body);
  return res.data;
};

export const enhancePreview = async (id: string, body: EnhancePreviewRequest): Promise<Blob> => {
  const res = await apiClient.post(`/assets/${id}/enhance-preview`, body, {
    responseType: 'blob',
  });
  return res.data;
};

// --- Audience / Student Question Detection ---

export interface AudienceSegment {
  start: number;
  end: number;
  duration: number;
}

export const detectAudience = async (
  id: string,
  quietThresholdDb = -25,
  silenceThresholdDb = -45,
  minDuration = 1.0,
): Promise<{ audience_segments: AudienceSegment[]; count: number }> => {
  const res = await apiClient.post(`/assets/${id}/detect-audience`, null, {
    params: {
      quiet_threshold_db: quietThresholdDb,
      silence_threshold_db: silenceThresholdDb,
      min_duration: minDuration,
    },
  });
  return res.data;
};
