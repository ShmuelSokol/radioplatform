import apiClient from './client';

export interface ShowArchive {
  id: string;
  station_id: string;
  title: string;
  description: string | null;
  host_name: string | null;
  recorded_at: string | null;
  duration_seconds: number | null;
  audio_url: string;
  cover_image_url: string | null;
  is_published: boolean;
  download_count: number;
  live_show_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ShowArchiveListResponse {
  archives: ShowArchive[];
  total: number;
}

export const getArchives = (params?: { station_id?: string; skip?: number; limit?: number }) =>
  apiClient.get<ShowArchiveListResponse>('/archives', { params }).then(r => r.data);

export const getArchive = (id: string) =>
  apiClient.get<ShowArchive>(`/archives/${id}`).then(r => r.data);

export const createArchive = (data: {
  station_id: string; title: string; audio_url: string;
  description?: string; host_name?: string; recorded_at?: string;
  duration_seconds?: number; cover_image_url?: string; live_show_id?: string;
}) => apiClient.post<ShowArchive>('/archives', data).then(r => r.data);

export const updateArchive = (id: string, data: Partial<ShowArchive>) =>
  apiClient.patch<ShowArchive>(`/archives/${id}`, data).then(r => r.data);

export const deleteArchive = (id: string) =>
  apiClient.delete(`/archives/${id}`);
