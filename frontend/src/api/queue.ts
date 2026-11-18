import apiClient from './client';

export const getQueue = async (stationId: string, limit = 2000) => {
  const res = await apiClient.get(`/stations/${stationId}/queue`, { params: { limit } });
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

/** How the item on air ends when the operator moves on: cut, quick fade, or long fade. */
export type SkipFade = 'hard' | 'fade' | 'soft';

export const skipCurrent = async (stationId: string, fade?: SkipFade) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/skip`, null, fade ? { params: { fade } } : undefined);
  return res.data;
};

export const fireNowRequest = async (stationId: string, assetId: string, clientRequestId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/fire-now`, {
    asset_id: assetId, mode: 'fade', client_request_id: clientRequestId,
  });
  return res.data as { id: string; on_air: boolean; duplicate?: boolean; queue?: unknown };
};

export const insertAfter = async (stationId: string, assetIds: string[], afterEntryId: string | null) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/insert`, {
    asset_ids: assetIds, after_entry_id: afterEntryId,
  });
  return res.data as { ids: string[]; queue?: unknown };
};

export const moveUp = async (stationId: string, entryId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/move-up`, { entry_id: entryId, new_position: 0 });
  return res.data as { message: string; warnings?: string[] };
};

export const moveDown = async (stationId: string, entryId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/move-down`, { entry_id: entryId, new_position: 0 });
  return res.data as { message: string; warnings?: string[] };
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

export const reorderDnd = async (stationId: string, entryId: string, newPosition: number) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/reorder-dnd`, {
    entry_id: entryId,
    new_position: newPosition,
  });
  return res.data as { message: string; warnings: string[] };
};

export const getLastPlayed = async (stationId: string) => {
  const res = await apiClient.get(`/stations/${stationId}/queue/last-played`);
  return res.data;
};

export const previewWeather = async (stationId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/preview-weather`);
  return res.data as { time_url: string | null; weather_url: string | null; time_text: string | null; weather_text: string | null };
};

/** Loop the item on air: it plays again each time it ends, until switched off, Next or play now. */
export const setLoop = async (stationId: string, on: boolean) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/loop`, { on });
  return res.data as { loop: boolean; asset_id: string | null; queue?: { entries?: unknown[] } };
};

/** KBR Studio's HS (hard start) and Spots mode switches; leave one out to keep it. */
export interface StationModes { hard_start: boolean; spots: boolean }
export const setStationModes = async (stationId: string, modes: Partial<StationModes>) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/modes`, modes);
  return res.data as { modes: StationModes; queue?: { entries?: unknown[] } };
};

/** Panic: cut whatever is on air now (even a time check) and play the next item. */
export const panicRequest = async (stationId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/panic`);
  return res.data as { message: string; queue?: { entries?: unknown[] } };
};

/** Play an asset on top of the item on air; the queue does not move. */
export const mixOverRequest = async (stationId: string, assetId: string, clientRequestId: string) => {
  const res = await apiClient.post(`/stations/${stationId}/queue/mix-over`, {
    asset_id: assetId, client_request_id: clientRequestId,
  });
  return res.data as { on_air: boolean; duplicate?: boolean };
};
