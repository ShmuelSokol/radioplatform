import apiClient from './client';

// --- Types ---

export interface LiveShow {
  id: string;
  station_id: string;
  host_user_id: string | null;
  title: string;
  description: string | null;
  status: 'scheduled' | 'live' | 'ended' | 'cancelled';
  broadcast_mode: 'webrtc' | 'icecast';
  scheduled_start: string | null;
  scheduled_end: string | null;
  actual_start: string | null;
  actual_end: string | null;
  twilio_conference_sid: string | null;
  icecast_mount: string | null;
  calls_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface CallInRequest {
  id: string;
  live_show_id: string;
  caller_phone: string;
  caller_name: string | null;
  status: 'waiting' | 'screening' | 'approved' | 'on_air' | 'completed' | 'rejected' | 'abandoned';
  twilio_call_sid: string | null;
  hold_start: string | null;
  air_start: string | null;
  air_end: string | null;
  notes: string | null;
  screened_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface LiveShowListResponse {
  shows: LiveShow[];
  total: number;
}

export interface CallListResponse {
  calls: CallInRequest[];
  total: number;
}

// --- API functions ---

export const getLiveShows = async (params?: {
  station_id?: string;
  status?: string;
  skip?: number;
  limit?: number;
}): Promise<LiveShowListResponse> => {
  const res = await apiClient.get<LiveShowListResponse>('/live-shows', { params });
  return res.data;
};

export const getLiveShow = async (id: string): Promise<LiveShow> => {
  const res = await apiClient.get<LiveShow>(`/live-shows/${id}`);
  return res.data;
};

export const createLiveShow = async (data: {
  station_id: string;
  title: string;
  description?: string;
  broadcast_mode?: string;
  scheduled_start?: string;
  scheduled_end?: string;
  calls_enabled?: boolean;
}): Promise<LiveShow> => {
  const res = await apiClient.post<LiveShow>('/live-shows', data);
  return res.data;
};

export const updateLiveShow = async (id: string, data: Partial<LiveShow>): Promise<LiveShow> => {
  const res = await apiClient.patch<LiveShow>(`/live-shows/${id}`, data);
  return res.data;
};

export const deleteLiveShow = async (id: string): Promise<void> => {
  await apiClient.delete(`/live-shows/${id}`);
};

export const startLiveShow = async (id: string): Promise<LiveShow> => {
  const res = await apiClient.post<LiveShow>(`/live-shows/${id}/start`);
  return res.data;
};

export const endLiveShow = async (id: string): Promise<LiveShow> => {
  const res = await apiClient.post<LiveShow>(`/live-shows/${id}/end`);
  return res.data;
};

export const getShowCalls = async (showId: string): Promise<CallListResponse> => {
  const res = await apiClient.get<CallListResponse>(`/live-shows/${showId}/calls`);
  return res.data;
};

export const approveCall = async (showId: string, callId: string): Promise<CallInRequest> => {
  const res = await apiClient.post<CallInRequest>(`/live-shows/${showId}/calls/${callId}/approve`);
  return res.data;
};

export const rejectCall = async (showId: string, callId: string): Promise<CallInRequest> => {
  const res = await apiClient.post<CallInRequest>(`/live-shows/${showId}/calls/${callId}/reject`);
  return res.data;
};

export const putCallerOnAir = async (showId: string, callId: string): Promise<CallInRequest> => {
  const res = await apiClient.post<CallInRequest>(`/live-shows/${showId}/calls/${callId}/on-air`);
  return res.data;
};

export const endCall = async (showId: string, callId: string): Promise<CallInRequest> => {
  const res = await apiClient.post<CallInRequest>(`/live-shows/${showId}/calls/${callId}/end-call`);
  return res.data;
};

export const updateCallInfo = async (
  showId: string,
  callId: string,
  data: { caller_name?: string; notes?: string },
): Promise<CallInRequest> => {
  const res = await apiClient.patch<CallInRequest>(`/live-shows/${showId}/calls/${callId}`, data);
  return res.data;
};

export const getTimeRemaining = async (showId: string): Promise<{ seconds: number | null }> => {
  const res = await apiClient.get<{ seconds: number | null }>(`/live-shows/${showId}/time-remaining`);
  return res.data;
};
