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
  company_name?: string | null;
  contact_name?: string | null;
  contact_email?: string | null;
  contact_phone?: string | null;
  notes?: string | null;
  user_id?: string | null;
  created_at: string;
  updated_at: string;
}

export const listSponsors = async (): Promise<Sponsor[]> => {
  const res = await apiClient.get<Sponsor[]>('/sponsors');
  return res.data;
};

export const createSponsor = async (data: Partial<Omit<Sponsor, 'id' | 'created_at' | 'updated_at'>> & { name: string }): Promise<Sponsor> => {
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

// ── Client database (the Sponsors page) ───────────────────────────

/** running = on the air now; upcoming = a contract starts later; paused; ended; none = no contract yet */
export type ClientStatus = 'running' | 'upcoming' | 'paused' | 'ended' | 'none';
export type ContractState = 'running' | 'upcoming' | 'paused' | 'ended';

export interface ClientSummary {
  id: string;
  name: string;
  company_name: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  status: ClientStatus;
  active_from: string | null;
  active_until: string | null;
  open_ended: boolean;
  contracts_running: number;
  contracts_total: number;
  spots_on_air: number;
  spots_in_progress: number;
  plays_7_days: number;
  plays_total: number;
  last_played_at: string | null;
  has_portal_login: boolean;
  created_at: string | null;
}

export interface ClientContract {
  id: string;
  name: string | null;
  station_id: string | null;
  station_name: string;
  asset_id: string;
  asset_title: string;
  duration: number | null;
  days_of_week: string;
  dayparts: string;
  plays_per_daypart: number;
  fixed_times: string | null;
  timing_mode: 'hard' | 'soft' | null;
  start_date: string | null;
  end_date: string | null;
  is_active: boolean;
  state: ContractState;
  problem: string | null;
  plays_in_contract: number;
  plays_7_days: number;
  last_played_at: string | null;
  created_at: string | null;
}

export interface ClientSpot {
  key: string;
  kind: 'audio' | 'recording' | 'campaign';
  stage: string;
  stage_label: string;
  title: string;
  script: string | null;
  duration: number | null;
  asset_id: string | null;
  recording_id: string | null;
  campaign_id: string | null;
  status_detail: string | null;
  contract_ids: string[];
  plays_total: number;
  last_played_at: string | null;
  first_played_at: string | null;
  updated_at: string | null;
}

export interface ClientAccount {
  client: ClientSummary;
  notes: string | null;
  contracts: ClientContract[];
  spots: ClientSpot[];
  stages: { key: string; label: string }[];
}

export interface ClientPlay {
  id: string;
  station_id: string;
  station_name: string;
  asset_id: string | null;
  asset_title: string;
  start_utc: string;
  end_utc: string | null;
  local_date: string;
  local_time: string;
  aired_seconds: number | null;
  length_seconds: number | null;
  how: string;
}

export interface ClientPlays {
  total: number;
  page: number;
  limit: number;
  entries: ClientPlay[];
}

export interface PlayFilters {
  page?: number;
  limit?: number;
  station_id?: string;
  asset_id?: string;
  date_from?: string;
  date_to?: string;
}

export const listClients = async (): Promise<ClientSummary[]> =>
  (await apiClient.get<ClientSummary[]>('/sponsors/clients')).data;

export const getClientAccount = async (id: string): Promise<ClientAccount> =>
  (await apiClient.get<ClientAccount>(`/sponsors/${id}/account`)).data;

export const getClientPlays = async (id: string, filters: PlayFilters = {}): Promise<ClientPlays> => {
  const params: Record<string, string | number> = {};
  for (const [k, v] of Object.entries(filters)) if (v !== undefined && v !== '') params[k] = v as string | number;
  return (await apiClient.get<ClientPlays>(`/sponsors/${id}/plays`, { params })).data;
};

export interface ClientMissedPlays {
  days: number;
  missed_past: number;
  missed_coming: number;
  moved_coming: number;
  rows: {
    when: 'past' | 'coming';
    date: string;
    daypart: string;
    station_name: string;
    asset_title: string;
    promised: number;
    played: number;
    missed: number;
    moved: number;
    note: string;
  }[];
}

export const getClientMissedPlays = async (id: string): Promise<ClientMissedPlays> =>
  (await apiClient.get<ClientMissedPlays>(`/sponsors/${id}/missed-plays`)).data;
