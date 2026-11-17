import apiClient from './client';

/** One row of the Songs / Jingles table (KBR Studio's Songs.db). */
export interface DbItem {
  id: string;
  artist: string | null;
  title: string;
  type: string | null; // the category, e.g. "1a old hit fast"
  asset_type: string;
  vote: number;
  length: number | null;
  intro: number | null;
  file: string;
  last_played: string | null;
}

export type DbSort = 'artist' | 'title' | 'type' | 'vote' | 'length' | 'intro' | 'file' | 'last_played' | 'added';

export type DbFiles = 'all' | 'mpeg' | 'present';

export interface DbItemsParams {
  asset_type: string;
  station_id?: string;
  search?: string;
  category?: string;
  files?: DbFiles;
  sort: DbSort;
  desc: boolean;
  skip: number;
  limit: number;
}

export const listDbItems = async (p: DbItemsParams): Promise<{ total: number; items: DbItem[] }> => {
  const params: Record<string, unknown> = { ...p };
  if (!p.search) delete params.search;
  if (!p.category) delete params.category;
  if (!p.files || p.files === 'all') delete params.files;
  if (!p.station_id) delete params.station_id;
  return (await apiClient.get('/databank/items', { params })).data;
};

/** One row of Spots.db. */
export interface DbSpot {
  id: string;
  title: string;
  file: string;
  category: string | null;
  end_date: string | null; // YYYY-MM-DD, latest end of its schedules
  open_ended: boolean;
  schedules: number;
  active: boolean;
  expired: boolean;
  length: number | null;
}

export const listDbSpots = async (stationId?: string): Promise<{ today: string; spots: DbSpot[] }> =>
  (await apiClient.get('/databank/spots', { params: stationId ? { station_id: stationId } : {} })).data;

export const setSpotEndDate = async (assetId: string, endDate: string | null, stationId?: string) =>
  (await apiClient.patch(`/databank/spots/${assetId}/end-date`, { end_date: endDate },
    { params: stationId ? { station_id: stationId } : {} })).data;

export interface DayLogEntry {
  id: string;
  asset_id: string | null;
  title: string;
  artist: string | null;
  asset_type: string | null;
  file: string;
  start_utc: string;
  end_utc: string | null;
  source: string;
}

export const getDayLog = async (stationId: string, day: string): Promise<{ day: string; timezone: string; logs: DayLogEntry[] }> =>
  (await apiClient.get('/databank/log', { params: { station_id: stationId, day } })).data;
