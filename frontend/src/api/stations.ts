import apiClient from './client';
import type { Station, StationListResponse, NowPlaying } from '../types';

export const listStations = async (skip = 0, limit = 50): Promise<StationListResponse> => {
  const res = await apiClient.get<StationListResponse>('/stations', { params: { skip, limit } });
  return res.data;
};

/** Every station, page by page: the server sends at most 100 at a time, and with one
 *  call the station pickers left out every station after the first 50. */
export const listAllStations = async (): Promise<StationListResponse> => {
  const first = await listStations(0, 100);
  const stations = [...first.stations];
  while (stations.length < first.total) {
    const next = await listStations(stations.length, 100);
    if (!next.stations.length) break;
    stations.push(...next.stations);
  }
  return { ...first, stations, total: Math.max(first.total, stations.length) };
};

export const getStation = async (id: string): Promise<Station> => {
  const res = await apiClient.get<Station>(`/stations/${id}`);
  return res.data;
};

export const createStation = async (data: Partial<Station>): Promise<Station> => {
  const res = await apiClient.post<Station>('/stations', data);
  return res.data;
};

export const updateStation = async (id: string, data: Partial<Station>): Promise<Station> => {
  const res = await apiClient.put<Station>(`/stations/${id}`, data);
  return res.data;
};

export const deleteStation = async (id: string): Promise<void> => {
  await apiClient.delete(`/stations/${id}`);
};

export const getStreamInfo = async (id: string) => {
  const res = await apiClient.get(`/stations/${id}/stream`);
  return res.data;
};

export const getNowPlaying = async (id: string): Promise<NowPlaying> => {
  const res = await apiClient.get<NowPlaying>(`/stations/${id}/now-playing`);
  return res.data;
};

export const controlPlay = async (id: string) => {
  const res = await apiClient.post(`/stations/${id}/controls/play`);
  return res.data;
};

export const controlPause = async (id: string) => {
  const res = await apiClient.post(`/stations/${id}/controls/pause`);
  return res.data;
};

export const controlStop = async (id: string) => {
  const res = await apiClient.post(`/stations/${id}/controls/stop`);
  return res.data;
};

export const controlPlayNow = async (id: string, assetId: string) => {
  const res = await apiClient.post(`/stations/${id}/controls/play-now`, { asset_id: assetId });
  return res.data;
};

export const controlEnqueue = async (id: string, assetId: string) => {
  const res = await apiClient.post(`/stations/${id}/controls/enqueue`, { asset_id: assetId });
  return res.data;
};
