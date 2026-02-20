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
}

export interface SongRequestListResponse {
  requests: SongRequest[];
  total: number;
}

export const submitSongRequest = (data: {
  station_id: string;
  requester_name: string;
  song_title: string;
  song_artist?: string;
  requester_message?: string;
}) => apiClient.post<SongRequest>('/song-requests', data).then(r => r.data);

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
