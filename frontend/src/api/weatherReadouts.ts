import apiClient from './client';

export interface WeatherReadout {
  id: string;
  station_id: string;
  readout_date: string;
  script_text: string;
  weather_data: Record<string, any> | null;
  status: string;
  asset_id: string | null;
  queue_time: string | null;
  generated_by: string;
  created_at: string;
  updated_at: string;
}

export interface WeatherReadoutListResponse {
  readouts: WeatherReadout[];
  total: number;
}

export interface TemplatePreview {
  rendered: string;
  weather: Record<string, any>;
  template: string;
}

export const getWeatherReadouts = (params?: {
  station_id?: string;
  date_from?: string;
  date_to?: string;
  status?: string;
  skip?: number;
  limit?: number;
}) => apiClient.get<WeatherReadoutListResponse>('/weather-readouts', { params }).then(r => r.data);

export const getWeatherReadout = (id: string) =>
  apiClient.get<WeatherReadout>(`/weather-readouts/${id}`).then(r => r.data);

export const createWeatherReadout = (data: {
  station_id: string;
  readout_date?: string;
  template_override?: string;
}) => apiClient.post<WeatherReadout>('/weather-readouts', data).then(r => r.data);

export const updateWeatherReadout = (id: string, data: {
  script_text?: string;
  status?: string;
  asset_id?: string;
  queue_time?: string;
}) => apiClient.patch<WeatherReadout>(`/weather-readouts/${id}`, data).then(r => r.data);

export const regenerateWeatherReadout = (id: string) =>
  apiClient.post<WeatherReadout>(`/weather-readouts/${id}/regenerate`).then(r => r.data);

export const queueWeatherReadout = (id: string) =>
  apiClient.post<{ ok: boolean; queue_entry_id: string }>(`/weather-readouts/${id}/queue`).then(r => r.data);

export const deleteWeatherReadout = (id: string) =>
  apiClient.delete(`/weather-readouts/${id}`);

export const getTemplatePreview = (stationId: string, template?: string) =>
  apiClient.get<TemplatePreview>('/weather-readouts/template-preview', {
    params: { station_id: stationId, ...(template ? { template } : {}) },
  }).then(r => r.data);
