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

export const putCallerOnAir = async (showId: string, callId: string, force = false): Promise<CallInRequest> => {
  const res = await apiClient.post<CallInRequest>(
    `/live-shows/${showId}/calls/${callId}/on-air`, null, force ? { params: { force: true } } : undefined);
  return res.data;
};

/**
 * Put a caller on air from a button. While the show's delay is too short to dump
 * the caller the server refuses; the person is told why and may go ahead anyway.
 * Returns an error text to show, or null.
 */
export const putCallerOnAirAsking = async (showId: string, callId: string): Promise<string | null> => {
  try {
    await putCallerOnAir(showId, callId);
    return null;
  } catch (err: any) {
    const detail = err?.response?.data?.detail;
    if (detail?.code === 'delay_not_safe') {
      if (!window.confirm(`${detail.message}\n\nPut the caller on air anyway?`)) return null;
      try {
        await putCallerOnAir(showId, callId, true);
        return null;
      } catch (again: any) {
        const d = again?.response?.data?.detail;
        return typeof d === 'string' ? d : 'Could not put the caller on air';
      }
    }
    return typeof detail === 'string' ? detail : 'Could not put the caller on air. Another caller may be on air.';
  }
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

// --- Broadcast delay (Dump, exit, build) ---

export interface AirStatus {
  show_id: string;
  /** off = not on air; pre-roll = filling before listeners hear anything */
  mode: 'off' | 'pre-roll' | 'steady' | 'building' | 'shrinking' | 'exiting' | 'no delay' | 'insert';
  on_air: boolean;
  /** Seconds the show still waits for a timed item on the station to end (pre-roll only) */
  hold?: number;
  /** Seconds waiting in the delay now: what a Dump can still cut */
  delay: number;
  target: number;
  configured: number;
  /** Seconds one Dump cuts (0 = everything waiting) */
  dump_size: number;
  safe: boolean;
  dumps: number;
  finishing?: boolean;
  auto_exit?: boolean;
  insert?: { title: string; kind: string; remaining: number } | null;
  next_insert?: { title: string; kind: string; in: number } | null;
  host_silent_for?: number;
}

export const getAirStatus = async (showId: string): Promise<AirStatus> => {
  const res = await apiClient.get<AirStatus>(`/live-shows/${showId}/air`);
  return res.data;
};

export const dumpShow = async (
  showId: string, dropCaller = false,
): Promise<{ dumped_seconds: number; dropped_callers: string[]; air: AirStatus }> => {
  const res = await apiClient.post(`/live-shows/${showId}/dump`, { drop_caller: dropCaller });
  return res.data;
};

export const setShowDelay = async (
  showId: string, action: 'exit' | 'build', seconds?: number,
): Promise<AirStatus> => {
  const res = await apiClient.post<AirStatus>(`/live-shows/${showId}/delay`, { action, seconds });
  return res.data;
};

export interface DumpRecord {
  id: string;
  dumped_at: string | null;
  by: string | null;
  cut_seconds: number;
  /** Seconds of what listeners heard just before the cut, at the start of the recording */
  heard_seconds: number;
  caller_name: string | null;
  has_audio: boolean;
}

export const getShowDumps = async (showId: string): Promise<DumpRecord[]> => {
  const res = await apiClient.get<{ dumps: DumpRecord[] }>(`/live-shows/${showId}/dumps`);
  return res.data.dumps;
};

/** The recording of one Dump (staff only, so it is fetched with the sign-in, not linked) */
export const getDumpAudio = async (showId: string, dumpId: string): Promise<Blob> => {
  const res = await apiClient.get(`/live-shows/${showId}/dumps/${dumpId}/audio`, { responseType: 'blob' });
  return res.data as Blob;
};
