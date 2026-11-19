import apiClient from './client';

/** How one station mixes in one holiday's list (backend services/holiday_music.py). */
export interface HolidaySettings {
  enabled: boolean;
  lead_days: number; // days before the first day that the mix starts
  start_every: number; // 1 holiday item in every N ordinary ones at the start
  peak_every: number; // ... on the day before the holiday
  during_every: number; // ... on the holiday's own days on the air
  include_shiurim: boolean;
  stop: 'end' | 'start';
}

export interface HolidayOverview {
  key: string;
  name: string;
  settings: HolidaySettings;
  items: { music: number; long: number; other: number };
  first_day: string;
  last_day: string;
  mix_starts: string;
  mix_ends: string;
  today_share: number;
  today_every: number | null;
  in_season: boolean;
  days_until: number;
}

export interface HolidayOverviewResponse {
  station_id: string;
  station_name: string;
  israel_calendar: boolean;
  today: string;
  holidays: HolidayOverview[];
}

export interface HolidayDay {
  date: string;
  share: number;
  every: number | null;
  phase: 'before' | 'holiday';
  today: boolean;
}

export interface HolidayItem {
  id: string;
  asset_id: string;
  title: string;
  artist: string | null;
  album: string | null;
  duration: number | null;
  asset_type: string;
  category: string | null;
  kind: 'music' | 'long' | null;
  station_id: string | null;
  station_name: string | null;
  all_year: boolean;
}

export interface HolidaySuggestion {
  asset_id: string;
  title: string;
  artist: string | null;
  album: string | null;
  duration: number | null;
  asset_type: string;
  category: string | null;
  kind: 'music' | 'long' | null;
  matched: string | null;
}

export const getHolidayOverview = async (stationId: string) =>
  (await apiClient.get<HolidayOverviewResponse>('/holiday-music', { params: { station_id: stationId } })).data;

export const getHolidayDays = async (holiday: string, stationId: string) =>
  (await apiClient.get<{ days: HolidayDay[] }>(`/holiday-music/${holiday}/days`, { params: { station_id: stationId } }))
    .data;

export const saveHolidaySettings = async (holiday: string, stationId: string, settings: Partial<HolidaySettings>) =>
  (await apiClient.put(`/holiday-music/${holiday}/settings`, settings, { params: { station_id: stationId } })).data;

export const getHolidayItems = async (holiday: string, stationId?: string) =>
  (await apiClient.get<HolidayItem[]>(`/holiday-music/${holiday}/items`, {
    params: stationId ? { station_id: stationId } : {},
  })).data;

export const addHolidayItems = async (
  holiday: string,
  assetIds: string[],
  stationId: string | null,
  allYear = false,
) =>
  (await apiClient.post<{ added: number; already_listed: number; removed_from_queues: number }>(
    `/holiday-music/${holiday}/items`,
    { asset_ids: assetIds, station_id: stationId, all_year: allYear },
  )).data;

export const updateHolidayItem = async (
  id: string,
  change: { all_year?: boolean; station_id?: string; every_station?: boolean },
) => (await apiClient.patch(`/holiday-music/items/${id}`, change)).data;

export const removeHolidayItem = async (id: string) => (await apiClient.delete(`/holiday-music/items/${id}`)).data;

export const suggestHolidayItems = async (holiday: string, q?: string) =>
  (await apiClient.get<HolidaySuggestion[]>(`/holiday-music/${holiday}/suggest`, { params: q ? { q } : {} })).data;
