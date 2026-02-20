import apiClient from './client';

export interface HolidayWindow {
  id: string;
  name: string;
  start_datetime: string;
  end_datetime: string;
  is_blackout: boolean;
  affected_stations: { station_ids: string[] } | null;
  replacement_content: string | null;
  reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface HolidayListResponse {
  holidays: HolidayWindow[];
  total: number;
}

export interface HolidayFilters {
  skip?: number;
  limit?: number;
  reason?: string;
  status?: string;
  station_id?: string;
  start_after?: string;
  start_before?: string;
}

export interface AutoGenerateResponse {
  created: number;
  skipped: number;
}

export const listHolidays = async (filters?: HolidayFilters): Promise<HolidayListResponse> => {
  const params: Record<string, string | number> = {};
  if (filters?.skip) params.skip = filters.skip;
  if (filters?.limit) params.limit = filters.limit;
  if (filters?.reason) params.reason = filters.reason;
  if (filters?.status) params.status = filters.status;
  if (filters?.station_id) params.station_id = filters.station_id;
  if (filters?.start_after) params.start_after = filters.start_after;
  if (filters?.start_before) params.start_before = filters.start_before;
  const res = await apiClient.get<HolidayListResponse>('/holidays', { params });
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
