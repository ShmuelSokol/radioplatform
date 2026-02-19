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
