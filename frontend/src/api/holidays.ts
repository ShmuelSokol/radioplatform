import apiClient from './client';

export interface HolidayWindow {
  id: string;
  name: string;
  start_datetime: string;
  end_datetime: string;
  is_blackout: boolean;
  affected_stations: { station_ids: string[] } | null;
  replacement_content: string | null;
  created_at: string;
  updated_at: string;
}

export interface AutoGenerateResponse {
  created: number;
  skipped: number;
}

export interface SilenceAssetResponse {
  id: string;
  title: string;
  file_path: string;
  already_existed: boolean;
}

export const listHolidays = async (): Promise<HolidayWindow[]> => {
  const res = await apiClient.get<HolidayWindow[]>('/holidays');
  return res.data;
};

export const createHoliday = async (data: Omit<HolidayWindow, 'id' | 'created_at' | 'updated_at'>): Promise<HolidayWindow> => {
  const res = await apiClient.post<HolidayWindow>('/holidays', data);
  return res.data;
};

export const updateHoliday = async (id: string, data: Partial<HolidayWindow>): Promise<HolidayWindow> => {
  const res = await apiClient.put<HolidayWindow>(`/holidays/${id}`, data);
  return res.data;
};

export const deleteHoliday = async (id: string): Promise<void> => {
  await apiClient.delete(`/holidays/${id}`);
};

export const autoGenerateBlackouts = async (
  stationId: string,
  monthsAhead: number = 12,
): Promise<AutoGenerateResponse> => {
  const res = await apiClient.post<AutoGenerateResponse>('/holidays/auto-generate', {
    station_id: stationId,
    months_ahead: monthsAhead,
  });
  return res.data;
};

export interface PreviewWindowItem {
  name: string;
  start_datetime: string;
  end_datetime: string;
  duration_hours: number;
}

export interface PreviewResponse {
  total: number;
  shabbos_count: number;
  yom_tov_count: number;
  windows: PreviewWindowItem[];
}

export const previewBlackouts = async (
  stationId: string,
  monthsAhead: number = 12,
): Promise<PreviewResponse> => {
  const res = await apiClient.post<PreviewResponse>('/holidays/preview', {
    station_id: stationId,
    months_ahead: monthsAhead,
  });
  return res.data;
};

export const ensureSilenceAsset = async (): Promise<SilenceAssetResponse> => {
  const res = await apiClient.post<SilenceAssetResponse>('/holidays/ensure-silence-asset');
  return res.data;
};
