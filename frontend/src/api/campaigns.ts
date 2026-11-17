import apiClient from './client';

export interface Campaign {
  id: string;
  sponsor_id: string;
  sponsor_name?: string | null;
  name: string;
  description: string | null;
  status: string;
  start_date: string | null;
  end_date: string | null;
  budget_cents: number | null;
  target_rules: Record<string, any> | null;
  created_at: string;
  updated_at: string;
}

export interface CampaignDraft {
  id: string;
  campaign_id: string;
  version: number;
  script_text: string | null;
  audio_file_path: string | null;
  notes: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  user_email: string | null;
  user_display_name: string | null;
}

export interface CampaignComment {
  id: string;
  campaign_id: string;
  draft_id: string | null;
  user_id: string;
  body: string;
  created_at: string;
  user_email: string | null;
  user_display_name: string | null;
}

export const CAMPAIGN_STATUSES = ['draft', 'in_review', 'approved', 'active', 'paused', 'completed'] as const;
export type CampaignStatus = typeof CAMPAIGN_STATUSES[number];

/** Staff may pass a sponsor to see only its campaigns; a sponsor login always gets its own. */
export const listCampaigns = async (opts: { sponsorId?: string; limit?: number } = {}): Promise<Campaign[]> => {
  const params: Record<string, string | number> = {};
  if (opts.sponsorId) params.sponsor_id = opts.sponsorId;
  if (opts.limit) params.limit = opts.limit;
  const res = await apiClient.get<Campaign[]>('/campaigns', { params });
  return res.data;
};

export const getCampaign = async (id: string): Promise<Campaign> => {
  const res = await apiClient.get<Campaign>(`/campaigns/${id}`);
  return res.data;
};

export const createCampaign = async (data: {
  name: string;
  /** Staff name the sponsor; ignored for a sponsor login (it always creates for itself). */
  sponsor_id?: string;
  description?: string;
  start_date?: string;
  end_date?: string;
  budget_cents?: number;
}): Promise<Campaign> => {
  const res = await apiClient.post<Campaign>('/campaigns', data);
  return res.data;
};

export const updateCampaign = async (id: string, data: Partial<Campaign>): Promise<Campaign> => {
  const res = await apiClient.patch<Campaign>(`/campaigns/${id}`, data);
  return res.data;
};

export const updateCampaignStatus = async (id: string, status: string): Promise<Campaign> => {
  const res = await apiClient.patch<Campaign>(`/campaigns/${id}/status`, { status });
  return res.data;
};

export const listDrafts = async (campaignId: string): Promise<CampaignDraft[]> => {
  const res = await apiClient.get<CampaignDraft[]>(`/campaigns/${campaignId}/drafts`);
  return res.data;
};

export const createDraft = async (campaignId: string, data: {
  script_text?: string;
  notes?: string;
}): Promise<CampaignDraft> => {
  const res = await apiClient.post<CampaignDraft>(`/campaigns/${campaignId}/drafts`, data);
  return res.data;
};

export const uploadDraftAudio = async (campaignId: string, draftId: string, file: File): Promise<{ audio_file_path: string }> => {
  const formData = new FormData();
  formData.append('file', file);
  const res = await apiClient.post(`/campaigns/${campaignId}/drafts/${draftId}/upload-audio`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return res.data;
};

export const listComments = async (campaignId: string): Promise<CampaignComment[]> => {
  const res = await apiClient.get<CampaignComment[]>(`/campaigns/${campaignId}/comments`);
  return res.data;
};

export const createComment = async (campaignId: string, data: {
  body: string;
  draft_id?: string;
}): Promise<CampaignComment> => {
  const res = await apiClient.post<CampaignComment>(`/campaigns/${campaignId}/comments`, data);
  return res.data;
};
