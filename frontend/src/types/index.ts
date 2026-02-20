export interface AlertPreferences {
  sms_enabled?: boolean;
  whatsapp_enabled?: boolean;
  min_severity?: string;
}

export interface SocialLinks {
  twitter?: string;
  instagram?: string;
  website?: string;
  [key: string]: string | undefined;
}

export interface User {
  id: string;
  email: string;
  role: 'admin' | 'manager' | 'viewer' | 'sponsor';
  is_active: boolean;
  display_name: string | null;
  phone_number: string | null;
  title: string | null;
  alert_preferences: AlertPreferences | null;
  bio: string | null;
  photo_url: string | null;
  is_public: boolean;
  social_links: SocialLinks | null;
}

export interface PublicHost {
  id: string;
  display_name: string;
  title: string | null;
  bio: string | null;
  photo_url: string | null;
  social_links: SocialLinks | null;
}

export interface PublicHostsResponse {
  hosts: PublicHost[];
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
  automation_config: Record<string, any> | null;
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
  asset_type: string;
  category: string | null;
  created_at: string | null;
  last_played_at: string | null;
  sponsor_id: string | null;
  sponsor_name: string | null;
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

// Queue
export interface QueueEntry {
  id: string;
  station_id: string;
  asset_id: string;
  position: number;
  status: 'pending' | 'playing' | 'played' | 'skipped';
  asset: Asset | null;
}

export interface QueueNowPlaying extends QueueEntry {
  started_at: string;
  elapsed_seconds: number;
  remaining_seconds: number;
}

export interface QueueListResponse {
  entries: QueueEntry[];
  total: number;
  now_playing: QueueNowPlaying | null;
}

/** Minimal asset info needed by the synth engine */
export interface AssetInfo {
  id: string;
  title: string;
  artist: string | null;
  asset_type: string;
  category: string | null;
  duration: number | null;
}

// Rules
export interface ScheduleRule {
  id: string;
  name: string;
  description: string | null;
  rule_type: string;
  asset_type: string;
  category: string | null;
  hour_start: number;
  hour_end: number;
  days_of_week: string;
  interval_minutes: number | null;
  songs_between: number | null;
  priority: number;
  is_active: boolean;
  constraints: Record<string, unknown> | null;
  station_id?: string | null;
}

export interface RuleListResponse {
  rules: ScheduleRule[];
  total: number;
}

export interface ScheduleSlot {
  time: string;
  asset_type: string;
  category: string | null;
  rule_name: string;
  duration_minutes: number | null;
}

export interface SchedulePreview {
  date: string;
  slots: ScheduleSlot[];
  total_hours: number;
}

export interface UserListResponse {
  users: User[];
  total: number;
}

// Silence Detection & Trim
export interface SilenceRegion {
  start: number;
  end: number;
  duration: number;
}

// Review System
export type ReviewStatus = 'pending' | 'in_review' | 'approved' | 'rejected' | 'flagged';

export interface ReviewQueue {
  id: string;
  name: string;
  description: string | null;
  status: 'open' | 'in_progress' | 'completed' | 'cancelled';
  created_by: string;
  total_items: number;
  reviewed_items: number;
  created_at: string;
  updated_at: string;
}

export interface ReviewItem {
  id: string;
  queue_id: string;
  asset_id: string;
  position: number;
  status: ReviewStatus;
  assigned_to: string | null;
  notes: string | null;
  version: number;
  asset: Asset | null;
}

export interface ReviewAction {
  id: string;
  review_item_id: string | null;
  asset_id: string;
  user_id: string;
  action_type: string;
  comment: string | null;
  details: Record<string, unknown> | null;
  created_at: string;
  user_email?: string;
}

export interface ReviewQueueListResponse {
  queues: ReviewQueue[];
  total: number;
}

export interface ReviewItemListResponse {
  items: ReviewItem[];
  total: number;
}

// User Preferences
export interface UserPreferences {
  preview_start_seconds: number;
  preview_end_seconds: number;
  default_silence_threshold_db: number;
  default_silence_min_duration: number;
  extra_preferences: Record<string, unknown>;
}
