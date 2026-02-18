export interface User {
  id: string;
  email: string;
  role: 'admin' | 'manager' | 'viewer';
  is_active: boolean;
  display_name: string | null;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface Station {
  id: string;
  name: string;
  type: 'internet' | 'ota' | 'both';
  timezone: string;
  latitude: number | null;
  longitude: number | null;
  stream_url: string | null;
  broadcast_config: Record<string, unknown> | null;
  is_active: boolean;
  description: string | null;
  logo_url: string | null;
  channels: ChannelStream[];
}

export interface ChannelStream {
  id: string;
  channel_name: string;
  bitrate: number;
  codec: string;
  hls_manifest_path: string | null;
  listeners_count: number;
}

export interface Asset {
  id: string;
  title: string;
  artist: string | null;
  album: string | null;
  duration: number | null;
  file_path: string;
  album_art_path: string | null;
  metadata_extra: Record<string, unknown> | null;
  created_by: string | null;
}

export interface NowPlaying {
  state: 'playing' | 'paused' | 'stopped';
  now_playing: {
    asset_id: string;
    title: string;
    file_path: string;
    duration: number;
  } | null;
  upcoming: Array<{
    asset_id: string;
    title: string;
    file_path: string;
    duration: number;
  }>;
}

export interface StationListResponse {
  stations: Station[];
  total: number;
}

export interface AssetListResponse {
  assets: Asset[];
  total: number;
}
