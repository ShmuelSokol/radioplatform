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

function apiBase(): string {
  return import.meta.env.VITE_API_URL || '/api/v1';
}

// For pagehide: sent as text/plain so a cross-origin beacon needs no preflight
export const sendLeaveBeacon = (stationId: string, sessionKey: string) => {
  const body = JSON.stringify({ station_id: stationId, session_key: sessionKey });
  try {
    const blob = new Blob([body], { type: 'text/plain' });
    if (navigator.sendBeacon?.(`${apiBase()}/listeners/leave`, blob)) return;
  } catch { /* fall through */ }
  sendDisconnect(stationId, sessionKey).catch(() => {});
};

// Socket that says "listeners changed" (no listener data on it)
export function listenersSocketUrl(): string {
  const wsBase = import.meta.env.VITE_WS_URL;
  const apiUrl = apiBase();
  if (wsBase) return `${wsBase}/api/v1/listeners/ws`;
  if (apiUrl.startsWith('http')) return apiUrl.replace(/^http/, 'ws') + '/listeners/ws';
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${apiUrl}/listeners/ws`;
}

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
