import apiClient from './client';

export interface EmergencyAlertRow {
  id: string;
  station_id: string | null;
  station_name: string | null;
  kind: 'alert' | 'test' | 'rmt' | 'rwt' | string;
  source: 'device' | 'staff' | 'line' | string;
  title: string | null;
  requested_by: string | null;
  status: 'sent' | 'on_air' | 'done' | 'failed' | 'cancelled' | string;
  has_tones: boolean;
  audio_url: string | null;
  requested_at: string | null;
  started_at: string | null;
  ended_at: string | null;
  seconds_on_air: number | null;
  error: string | null;
}

export interface EmergencyStationStatus {
  station_id: string;
  name: string;
  player: 'engine' | 'browser';
  box: boolean;
  on_air: { alert_id: string; since: number; source: string; kind: string; title: string | null } | null;
}

export const getEmergencyStatus = async () =>
  (await apiClient.get<{ stations: EmergencyStationStatus[]; device_api: boolean }>('/emergency/status')).data;

export const getEmergencyLog = async (skip = 0, limit = 50) =>
  (await apiClient.get<{ alerts: EmergencyAlertRow[]; total: number }>('/emergency', { params: { skip, limit } })).data;

export const sendEmergencyTest = async (stationIds: string[], file: File, title: string) => {
  const form = new FormData();
  form.append('file', file);
  form.append('station_ids', stationIds.join(','));
  form.append('kind', 'test');
  if (title) form.append('title', title);
  return (await apiClient.post<{ alerts: EmergencyAlertRow[]; sent: number; failed: number }>(
    '/emergency/activate', form, { timeout: 60000 })).data;
};

export const stopEmergencyAlert = async (alertId: string) =>
  (await apiClient.post<{ ok: boolean; status: string }>(`/emergency/${alertId}/stop`)).data;

export const getEmergencyActive = async () =>
  (await apiClient.get<{ on_air: Record<string, EmergencyStationStatus['on_air']> }>('/emergency/active')).data;
