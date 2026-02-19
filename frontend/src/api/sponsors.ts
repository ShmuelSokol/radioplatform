import apiClient from './client';

export interface Sponsor {
  id: string;
  name: string;
  length_seconds: number;
  priority: number;
  audio_file_path: string;
  target_rules: {
    hour_start?: number;
    hour_end?: number;
    max_per_hour?: number;
    songs_between?: number;
    interval_minutes?: number;
  } | null;
  insertion_policy: 'between_tracks' | 'every_n_songs' | 'fixed_interval';
  created_at: string;
  updated_at: string;
}

export const listSponsors = async (): Promise<Sponsor[]> => {
  const res = await apiClient.get<Sponsor[]>('/sponsors');
  return res.data;
};

export const createSponsor = async (data: Omit<Sponsor, 'id' | 'created_at' | 'updated_at'>): Promise<Sponsor> => {
  const res = await apiClient.post<Sponsor>('/sponsors', data);
  return res.data;
};

export const updateSponsor = async (id: string, data: Partial<Sponsor>): Promise<Sponsor> => {
  const res = await apiClient.put<Sponsor>(`/sponsors/${id}`, data);
  return res.data;
};

export const deleteSponsor = async (id: string): Promise<void> => {
  await apiClient.delete(`/sponsors/${id}`);
};
