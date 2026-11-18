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
  scheduled_at?: string | null;
}

export interface SponsorStats {
  total_plays_month: number;
  total_plays_alltime: number;
  next_scheduled: string | null;
  next_scheduled_at?: string | null;
  last_played?: string | null;
}

export interface SponsorSpot {
  asset_id: string;
  title: string;
  duration_seconds: number | null;
  last_played_at: string | null;
  next_play_at: string | null;
  plays_month: number;
  plays_alltime: number;
  scheduled: boolean;
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

export const getMySpots = async (): Promise<SponsorSpot[]> => {
  const res = await apiClient.get<SponsorSpot[]>('/sponsor-portal/spots');
  return res.data;
};

export interface MissedPlay {
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
}

export interface MissedPlaysResponse {
  days: number;
  missed_past: number;
  missed_coming: number;
  moved_coming: number;
  rows: MissedPlay[];
}

export const getMissedPlays = async (): Promise<MissedPlaysResponse> => {
  const res = await apiClient.get<MissedPlaysResponse>('/sponsor-portal/missed-plays');
  return res.data;
};
