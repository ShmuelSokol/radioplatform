import apiClient from './client';

// ── Public (no auth) ──

export const sendHeartbeat = async (stationId: string, sessionKey: string) => {
  const res = await apiClient.post('/listeners/heartbeat', {
    station_id: stationId,
    session_key: sessionKey,
  });
  return res.data;
};

export const sendDisconnect = async (stationId: string, sessionKey: string) => {
  const res = await apiClient.post('/listeners/disconnect', {
    station_id: stationId,
    session_key: sessionKey,
  });
  return res.data;
};

// ── Admin (require_manager) ──

export interface LiveListenerStation {
  station_id: string;
  station_name: string;
  listeners: number;
  regions: Array<{
    country: string;
    region: string;
    city: string;
    count: number;
  }>;
}

export interface LiveListenersResponse {
  total_listeners: number;
  stations: LiveListenerStation[];
}

export interface TodayStats {
  date: string;
  total_sessions: number;
  unique_listeners: number;
  total_minutes: number;
  active_now: number;
  peak_today: number;
}

export interface HistoryDay {
  date: string;
  sessions: number;
  unique_listeners: number;
  total_minutes: number;
}

export interface RegionEntry {
  country: string;
  region: string;
  city: string;
  sessions: number;
  unique_listeners: number;
  total_minutes: number;
}

export const getLiveListeners = async (): Promise<LiveListenersResponse> => {
  const res = await apiClient.get<LiveListenersResponse>('/listeners/live');
  return res.data;
};

export const getTodayStats = async (): Promise<TodayStats> => {
  const res = await apiClient.get<TodayStats>('/listeners/today');
  return res.data;
};

export const getListenerHistory = async (days = 30): Promise<HistoryDay[]> => {
  const res = await apiClient.get<{ days: HistoryDay[] }>('/listeners/history', {
    params: { days },
  });
  return res.data.days;
};

export const getListenerRegions = async (days = 7): Promise<RegionEntry[]> => {
  const res = await apiClient.get<{ regions: RegionEntry[] }>('/listeners/regions', {
    params: { days },
  });
  return res.data.regions;
};
