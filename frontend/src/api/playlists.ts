import apiClient from './client';

export interface TemplateSlot {
  id: string;
  template_id: string;
  position: number;
  asset_type: string;
  category: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlaylistTemplate {
  id: string;
  name: string;
  description: string | null;
  station_id: string | null;
  is_active: boolean;
  slots: TemplateSlot[];
  created_at: string;
  updated_at: string;
}

export interface CreateTemplateData {
  name: string;
  description?: string;
  station_id?: string | null;
  is_active?: boolean;
  slots?: { position: number; asset_type: string; category?: string | null }[];
}

export interface UpdateTemplateData {
  name?: string;
  description?: string;
  station_id?: string | null;
  is_active?: boolean;
}

export interface CreateSlotData {
  template_id: string;
  position: number;
  asset_type: string;
  category?: string | null;
}

export interface UpdateSlotData {
  position?: number;
  asset_type?: string;
  category?: string | null;
}

export interface AssetTypeCombo {
  asset_type: string;
  category: string | null;
}

export const listTemplates = async (stationId?: string): Promise<PlaylistTemplate[]> => {
  const params = stationId ? { station_id: stationId } : {};
  const res = await apiClient.get('/playlists', { params });
  return res.data;
};

export const getTemplate = async (id: string): Promise<PlaylistTemplate> => {
  const res = await apiClient.get(`/playlists/${id}`);
  return res.data;
};

export const createTemplate = async (data: CreateTemplateData): Promise<PlaylistTemplate> => {
  const res = await apiClient.post('/playlists', data);
  return res.data;
};

export const updateTemplate = async (id: string, data: UpdateTemplateData): Promise<PlaylistTemplate> => {
  const res = await apiClient.patch(`/playlists/${id}`, data);
  return res.data;
};

export const deleteTemplate = async (id: string): Promise<void> => {
  await apiClient.delete(`/playlists/${id}`);
};

export const createSlot = async (data: CreateSlotData): Promise<TemplateSlot> => {
  const res = await apiClient.post('/playlists/slots', data);
  return res.data;
};

export const updateSlot = async (id: string, data: UpdateSlotData): Promise<TemplateSlot> => {
  const res = await apiClient.patch(`/playlists/slots/${id}`, data);
  return res.data;
};

export const deleteSlot = async (id: string): Promise<void> => {
  await apiClient.delete(`/playlists/slots/${id}`);
};

export const listAssetTypes = async (): Promise<AssetTypeCombo[]> => {
  const res = await apiClient.get('/playlists/asset-types');
  return res.data;
};
