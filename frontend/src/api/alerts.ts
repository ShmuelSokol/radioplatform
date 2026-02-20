import apiClient from './client';

export interface AlertData {
  id: string;
  station_id: string | null;
  alert_type: string;
  severity: string;
  title: string;
  message: string;
  context: Record<string, unknown> | null;
  is_resolved: boolean;
  resolved_at: string | null;
  resolved_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface AlertListResponse {
  alerts: AlertData[];
  total: number;
  unresolved_count: number;
}

export const getAlerts = async (params?: {
  skip?: number;
  limit?: number;
  severity?: string;
  alert_type?: string;
  is_resolved?: boolean;
}): Promise<AlertListResponse> => {
  const res = await apiClient.get<AlertListResponse>('/alerts', { params });
  return res.data;
};

export const getUnresolvedCount = async (): Promise<number> => {
  const res = await apiClient.get<{ unresolved_count: number }>('/alerts/unresolved-count');
  return res.data.unresolved_count;
};

export const resolveAlert = async (id: string): Promise<AlertData> => {
  const res = await apiClient.patch<AlertData>(`/alerts/${id}/resolve`);
  return res.data;
};

export const reopenAlert = async (id: string): Promise<AlertData> => {
  const res = await apiClient.patch<AlertData>(`/alerts/${id}/reopen`);
  return res.data;
};

export const deleteAlert = async (id: string): Promise<void> => {
  await apiClient.delete(`/alerts/${id}`);
};
