import apiClient from './client';

export interface SongRequest {
  id: string;
  station_id: string;
  requester_name: string;
  song_title: string;
  song_artist: string | null;
  requester_message: string | null;
  asset_id: string | null;
  status: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
  matched_asset_title: string | null;
  matched_asset_artist: string | null;
}

export interface SongRequestSubmitResponse {
  id: string;
  station_id: string;
  requester_name: string;
  song_title: string;
  song_artist: string | null;
  requester_message: string | null;
  asset_id: string | null;
  status: string;
  created_at: string;
  matched_asset_title: string | null;
  matched_asset_artist: string | null;
  match_confidence: number;
  auto_approved: boolean;
  queue_position: number | null;
  songs_ahead: number | null;
  estimated_wait_minutes: number | null;
}

export interface SongRequestListResponse {
  requests: SongRequest[];
  total: number;
}

export interface TopRequestedItem {
  asset_id: string;
  request_count: number;
  requested_title: string;
  requested_artist: string | null;
  library_title: string | null;
  library_artist: string | null;
}

export const submitSongRequest = (data: {
  station_id: string;
  requester_name: string;
  song_title: string;
  song_artist?: string;
  requester_message?: string;
}) => apiClient.post<SongRequestSubmitResponse>('/song-requests', data).then(r => r.data);

export const getSongRequests = (params?: {
  station_id?: string;
  status?: string;
  skip?: number;
  limit?: number;
}) => apiClient.get<SongRequestListResponse>('/song-requests', { params }).then(r => r.data);

export const updateSongRequest = (id: string, data: { status?: string; asset_id?: string }) =>
  apiClient.patch<SongRequest>(`/song-requests/${id}`, data).then(r => r.data);

export const deleteSongRequest = (id: string) =>
  apiClient.delete(`/song-requests/${id}`);

export const getSongRequestCount = (stationId: string) =>
  apiClient.get<{ count: number }>(`/song-requests/station/${stationId}/count`).then(r => r.data);

export const getTopRequested = (params?: { station_id?: string; limit?: number }) =>
  apiClient.get<{ top_requested: TopRequestedItem[] }>('/song-requests/analytics/top-requested', { params }).then(r => r.data);

export const updateAssetRequestSettings = (assetId: string, data: { auto_approve_requests?: boolean; max_requests_per_day?: number }) =>
  apiClient.patch<{ id: string; auto_approve_requests: boolean; max_requests_per_day: number }>(`/assets/${assetId}/request-settings`, data).then(r => r.data);
