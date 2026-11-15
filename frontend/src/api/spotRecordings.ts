import apiClient from "./client";
import type { CopyReview } from "./sponsorship";

export type SpotRecordingStatus = "draft" | "ready" | "approved" | "rejected";

export interface SpotRecording {
  id: string;
  sponsor_id: string;
  title: string;
  script_text: string;
  status: SpotRecordingStatus;
  voice: "placeholder" | "shprach" | "upload" | null;
  has_audio: boolean;
  duration: number | null;
  review: CopyReview | null;
  notes: string | null;
  asset_id: string | null;
  created_at: string;
  updated_at: string;
}

const BASE = "/spot-recordings";

export const listSpotRecordings = async (): Promise<SpotRecording[]> =>
  (await apiClient.get<SpotRecording[]>(BASE)).data;

export const createSpotRecording = async (data: {
  sponsor_id: string;
  title: string;
  script_text: string;
}): Promise<SpotRecording> =>
  (await apiClient.post<SpotRecording>(BASE, data)).data;

export const updateSpotRecording = async (
  id: string,
  data: { title?: string; script_text?: string },
): Promise<SpotRecording> =>
  (await apiClient.put<SpotRecording>(`${BASE}/${id}`, data)).data;

export const generateSpotAudio = async (id: string): Promise<SpotRecording> =>
  (await apiClient.post<SpotRecording>(`${BASE}/${id}/generate`)).data;

export const uploadSpotAudio = async (
  id: string,
  file: File,
): Promise<SpotRecording> => {
  const form = new FormData();
  form.append("file", file);
  return (await apiClient.post<SpotRecording>(`${BASE}/${id}/audio`, form))
    .data;
};

/** The audio needs the login token, so fetch it and hand the player a local URL. */
export const fetchSpotAudioUrl = async (id: string): Promise<string> => {
  const res = await apiClient.get<Blob>(`${BASE}/${id}/audio`, {
    responseType: "blob",
  });
  return URL.createObjectURL(res.data);
};

export const approveSpotRecording = async (
  id: string,
): Promise<SpotRecording> =>
  (await apiClient.post<SpotRecording>(`${BASE}/${id}/approve`)).data;

export const rejectSpotRecording = async (id: string): Promise<SpotRecording> =>
  (await apiClient.post<SpotRecording>(`${BASE}/${id}/reject`)).data;

export const deleteSpotRecording = async (id: string): Promise<void> => {
  await apiClient.delete(`${BASE}/${id}`);
};
