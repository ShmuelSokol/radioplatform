import apiClient from './client';

export interface PlayHistoryEntry {
  id: string;
  station_name: string;
  asset_title: string;
  start_utc: string;
  end_utc: string | null;
  duration_seconds: number | null;
}

export interface PlayHistoryResponse {
  entries: PlayHistoryEntry[];
  total: number;
  page: number;
  limit: number;
}

export interface UpcomingScheduleEntry {
  estimated_date: string;
  station_name: string;
  time_slot: string;
  asset_title: string;
}

export interface SponsorStats {
  total_plays_month: number;
  total_plays_alltime: number;
  next_scheduled: string | null;
}

export const getPlayHistory = async (page = 1, limit = 25): Promise<PlayHistoryResponse> => {
  const res = await apiClient.get<PlayHistoryResponse>('/sponsor-portal/play-history', {
    params: { page, limit },
  });
  return res.data;
};

export const getUpcomingSchedule = async (): Promise<UpcomingScheduleEntry[]> => {
  const res = await apiClient.get<UpcomingScheduleEntry[]>('/sponsor-portal/upcoming-schedule');
  return res.data;
};

export const getSponsorStats = async (): Promise<SponsorStats> => {
  const res = await apiClient.get<SponsorStats>('/sponsor-portal/stats');
  return res.data;
};
