import apiClient from './client';

export const RDS_API_BASE: string = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v1';

export interface RdsSettings {
  station_id: string;
  ps_text: string | null;
  ps_mode: 'static' | 'rotate';
  rotation_seconds: number;
  now_playing_weight: number;
  sponsor_while_spot_airs: boolean;
  fallback_text: string | null;
  default_ps: string;
  default_fallback: string;
}

export interface RdsCheck {
  ok: boolean;
  errors: string[];
  warnings: string[];
  example: string;
  length: number;
}

export interface RdsMessage {
  id: string;
  station_id: string | null;
  name: string | null;
  kind: 'text' | 'sponsor' | 'time' | 'weather';
  text: string;
  ps_text: string | null;
  sponsor_id: string | null;
  sponsor_name: string | null;
  weight: number;
  days_of_week: string;
  start_time: string | null;
  end_time: string | null;
  start_date: string | null;
  end_date: string | null;
  is_active: boolean;
  check: RdsCheck;
}

export type RdsMessageInput = Omit<RdsMessage, 'id' | 'sponsor_name' | 'check'>;

export interface RdsSlot {
  at: string;
  ps: string;
  rt: string;
  kind: 'now_playing' | 'message' | 'sponsor_on_air' | 'fallback';
  message_id: string | null;
  sponsor_id: string | null;
  sponsor_name: string | null;
  changes_at: string | null;
}

export interface RdsPlay {
  id: string;
  station_id: string;
  kind: RdsSlot['kind'];
  text: string;
  ps_text: string | null;
  sponsor_id: string | null;
  sponsor_name: string | null;
  first_seen_at: string;
  last_seen_at: string;
  fetches: number;
}

export interface RdsPlays {
  plays: RdsPlay[];
  sponsor_totals: { sponsor_id: string; sponsor_name: string | null; plays: number; seconds: number }[];
}

export const getRdsSettings = async (stationId: string) =>
  (await apiClient.get<RdsSettings>(`/rds/stations/${stationId}/settings`)).data;

export const saveRdsSettings = async (stationId: string, body: Partial<RdsSettings>) =>
  (await apiClient.put<RdsSettings>(`/rds/stations/${stationId}/settings`, body)).data;

export const listRdsMessages = async (stationId: string) =>
  (await apiClient.get<{ messages: RdsMessage[] }>('/rds/messages', { params: { station_id: stationId } })).data.messages;

/** Every station's lines (the sponsor screens look up a sponsor's lines in it) */
export const listAllRdsMessages = async () =>
  (await apiClient.get<{ messages: RdsMessage[] }>('/rds/messages')).data.messages;

export const createRdsMessage = async (body: RdsMessageInput) =>
  (await apiClient.post<RdsMessage>('/rds/messages', body)).data;

export const updateRdsMessage = async (id: string, body: Partial<RdsMessageInput>) =>
  (await apiClient.patch<RdsMessage>(`/rds/messages/${id}`, body)).data;

export const deleteRdsMessage = async (id: string) => {
  await apiClient.delete(`/rds/messages/${id}`);
};

export const checkRdsText = async (body: { text: string; ps_text?: string | null; kind: string; sponsor_id?: string | null }) =>
  (await apiClient.post<RdsCheck>('/rds/check', body)).data;

export const previewRds = async (stationId: string, minutes = 10) =>
  (await apiClient.get<{ now: RdsSlot; coming: RdsSlot[] }>(`/rds/stations/${stationId}/preview`, { params: { minutes } })).data;

export const listRdsPlays = async (stationId: string, days = 7) =>
  (await apiClient.get<RdsPlays>('/rds/plays', { params: { station_id: stationId, days } })).data;

/** The public feed addresses an RDS encoder polls (absolute, for copying). */
export function rdsFeedUrls(stationId: string) {
  const base = RDS_API_BASE.startsWith('http') ? RDS_API_BASE : `${window.location.origin}${RDS_API_BASE}`;
  return {
    radiotext: `${base}/stations/${stationId}/rds.txt`,
    ps: `${base}/stations/${stationId}/rds-ps.txt`,
    json: `${base}/stations/${stationId}/rds.json`,
  };
}
