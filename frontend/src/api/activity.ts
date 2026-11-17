import apiClient from './client';

export interface ActivityObject {
  type: string;
  id: string | null;
  name: string | null;
  op: 'create' | 'update' | 'delete';
  fields: Record<string, { old: unknown; new: unknown }>;
  station_id?: string | null;
}

export interface ActivityItem {
  id: string;
  created_at: string;
  user_id: string | null;
  user_email: string | null;
  user_name: string | null;
  user_role: string | null;
  label: string;
  category: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  detail: string | null;
  changes: {
    objects?: ActivityObject[];
    counts?: Record<string, number>;
    request?: unknown;
    params?: Record<string, string>;
  } | null;
  station_id: string | null;
  station_name: string | null;
  method: string | null;
  path: string | null;
  status_code: number | null;
  ip_address: string | null;
  device: string | null;
  user_agent: string | null;
  location: string | null;
  request_id: string | null;
}

export interface ActivityParams {
  user_id?: string;
  station_id?: string;
  category?: string;
  q?: string;
  start?: string;
  end?: string;
  include_views?: boolean;
  failed_only?: boolean;
  after?: string;
  skip?: number;
  limit?: number;
}

export interface ActivityFilters {
  users: { id: string; email: string; name: string | null; role: string; last_seen_at: string | null }[];
  stations: { id: string; name: string }[];
  categories: { id: string; label: string }[];
}

export interface ListenerSessionItem {
  id: string;
  station_id: string;
  station_name: string | null;
  started_at: string | null;
  last_heartbeat: string | null;
  ended_at: string | null;
  listening_now: boolean;
  duration_seconds: number;
  location: string | null;
  ip_address: string | null;
  device: string | null;
  user_agent: string | null;
}

export interface ListenerParams {
  station_id?: string;
  q?: string;
  start?: string;
  end?: string;
  min_seconds?: number;
  skip?: number;
  limit?: number;
}

export const getActivity = async (params: ActivityParams) =>
  (await apiClient.get<{ total: number | null; items: ActivityItem[] }>('/activity', { params })).data;

export const getActivityFilters = async () =>
  (await apiClient.get<ActivityFilters>('/activity/filters')).data;

export const getListenerSessions = async (params: ListenerParams) =>
  (await apiClient.get<{ total: number; items: ListenerSessionItem[] }>('/activity/listeners', { params })).data;

/** Tell the server which screen a signed-in user opened (best effort, never throws). */
export const reportPageView = (path: string, title?: string) => {
  apiClient.post('/activity/page-view', { path, title }).catch(() => {});
};
