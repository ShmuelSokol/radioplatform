import apiClient from "./client";

export interface GeneratedItem {
  at: string;
  local_time: string;
  block: string;
  asset_type: string;
  category: string | null;
  asset_id: string;
  title: string;
  artist: string | null;
  duration: number;
  artist_rule_relaxed: boolean;
}

export interface PlaylistGeneration {
  station_id: string;
  enabled: boolean;
  hours: number;
  artist_distance: number;
  queued_hours: number;
  preview_hours: number;
  notes: string[];
  preview: GeneratedItem[];
}

export interface GenerationSettings {
  station_id: string;
  enabled: boolean;
  hours: number;
  artist_distance: number;
}

export const getPlaylistGeneration = async (
  stationId: string,
): Promise<PlaylistGeneration> =>
  (
    await apiClient.get<PlaylistGeneration>("/playlists/generation", {
      params: { station_id: stationId },
    })
  ).data;

export const savePlaylistGeneration = async (
  settings: GenerationSettings,
): Promise<void> => {
  await apiClient.put("/playlists/generation", settings);
};

export const regeneratePlaylist = async (
  stationId: string,
): Promise<{ removed: number; queued_hours: number }> =>
  (
    await apiClient.post("/playlists/generation/regenerate", null, {
      params: { station_id: stationId },
    })
  ).data;
