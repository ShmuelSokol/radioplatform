import apiClient from './client';

export interface AnalyticsSummary {
  period_days: number;
  total_plays: number;
  unique_assets: number;
  total_airtime_hours: number;
  avg_plays_per_day: number;
  plays_by_source: Record<string, number>;
}

export interface PlayCountEntry {
  date: string;
  plays: number;
}

export interface TopAsset {
  id: string;
  title: string;
  artist: string | null;
  asset_type: string;
  category: string | null;
  play_count: number;
}

export interface CategoryBreakdown {
  category: string;
  asset_type: string;
  play_count: number;
}

export interface HourlyEntry {
  hour: number;
  plays: number;
}

export const getAnalyticsSummary = async (stationId?: string, days = 7): Promise<AnalyticsSummary> => {
  const params: Record<string, string> = { days: String(days) };
  if (stationId) params.station_id = stationId;
  const res = await apiClient.get<AnalyticsSummary>('/analytics/summary', { params });
  return res.data;
};

export const getPlayCounts = async (stationId?: string, days = 7): Promise<PlayCountEntry[]> => {
  const params: Record<string, string> = { days: String(days) };
  if (stationId) params.station_id = stationId;
  const res = await apiClient.get<{ data: PlayCountEntry[] }>('/analytics/play-counts', { params });
  return res.data.data;
};

export const getTopAssets = async (stationId?: string, days = 7, limit = 20): Promise<TopAsset[]> => {
  const params: Record<string, string> = { days: String(days), limit: String(limit) };
  if (stationId) params.station_id = stationId;
  const res = await apiClient.get<{ assets: TopAsset[] }>('/analytics/top-assets', { params });
  return res.data.assets;
};

export const getCategoryBreakdown = async (stationId?: string, days = 7): Promise<CategoryBreakdown[]> => {
  const params: Record<string, string> = { days: String(days) };
  if (stationId) params.station_id = stationId;
  const res = await apiClient.get<{ categories: CategoryBreakdown[] }>('/analytics/category-breakdown', { params });
  return res.data.categories;
};

export const getHourlyDistribution = async (stationId?: string, days = 7): Promise<HourlyEntry[]> => {
  const params: Record<string, string> = { days: String(days) };
  if (stationId) params.station_id = stationId;
  const res = await apiClient.get<{ hours: HourlyEntry[] }>('/analytics/hourly-distribution', { params });
  return res.data.hours;
};
