import apiClient from './client';

export const getQueue = async (stationId: string) => {
  const res = await apiClient.get(`/stations/${stationId}/queue`);
  return res.data;
};

export const getPlayLog = async (stationId: string, limit = 50) => {
  const res = await apiClient.get(`/stations/${stationId}/queue/log`, { params: { limit } });
  return res.data;
};

export const addToQueue = async (stationId: string, assetId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/add`, { asset_id: assetId });
  return res.data;
};

export const playNext = async (stationId: string, assetId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/play-next`, { asset_id: assetId });
  return res.data;
};

export const skipCurrent = async (stationId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/skip`);
  return res.data;
};

export const moveUp = async (stationId: string, entryId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/move-up`, { entry_id: entryId, new_position: 0 });
  return res.data;
};

export const moveDown = async (stationId: string, entryId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/move-down`, { entry_id: entryId, new_position: 0 });
  return res.data;
};

export const removeFromQueue = async (stationId: string, entryId: string): Promise<void> => {
  await apiClient.delete(`/stations/${stationId}/queue/${entryId}`);
};

export const startPlayback = async (stationId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/start`);
  return res.data;
};

export const bulkAddToQueue = async (stationId: string, assetIds: string[]) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/bulk-add`, { asset_ids: assetIds });
  return res.data;
};

export const getLastPlayed = async (stationId: string) => {
  const res = await apiClient.get(`/stations/${stationId}/queue/last-played`);
  return res.data;
};

export const previewWeather = async (stationId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/preview-weather`);
  return res.data as { time_url: string | null; weather_url: string | null; time_text: string | null; weather_text: string | null };
};
