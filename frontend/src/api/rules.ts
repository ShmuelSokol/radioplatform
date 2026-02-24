import apiClient from './client';
import type { ScheduleRule, RuleListResponse, SchedulePreview } from '../types';

export const listRules = async (skip = 0, limit = 50, stationId?: string | null): Promise<RuleListResponse> => {
  const params: Record<string, unknown> = { skip, limit };
  if (stationId) params.station_id = stationId;
  const res = await apiClient.get<RuleListResponse>('/rules', { params });
  return res.data;
};

export const createRule = async (data: Partial<ScheduleRule>): Promise<ScheduleRule> => {
  const res = await apiClient.post<ScheduleRule>('/rules', data);
  return res.data;
};

export const updateRule = async (id: string, data: Partial<ScheduleRule>): Promise<ScheduleRule> => {
  const res = await apiClient.put<ScheduleRule>(`/rules/${id}`, data);
  return res.data;
};

export const deleteRule = async (id: string): Promise<void> => {
  await apiClient.delete(`/rules/${id}`);
};

export const previewSchedule = async (date: string): Promise<SchedulePreview> => {
  const res = await apiClient.get<SchedulePreview>('/rules/preview', { params: { date } });
  return res.data;
};
