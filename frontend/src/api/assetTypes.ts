import apiClient from './client';

export interface AssetTypeItem {
  id: string;
  name: string;
  created_at: string | null;
}

export const listAssetTypes = async (): Promise<AssetTypeItem[]> => {
  const res = await apiClient.get<AssetTypeItem[]>('/asset-types');
  return res.data;
};

export const createAssetType = async (name: string): Promise<AssetTypeItem> => {
  const res = await apiClient.post<AssetTypeItem>('/asset-types', { name });
  return res.data;
};

export const updateAssetType = async (id: string, name: string): Promise<AssetTypeItem> => {
  const res = await apiClient.patch<AssetTypeItem>(`/asset-types/${id}`, { name });
  return res.data;
};

export const deleteAssetType = async (id: string): Promise<void> => {
  await apiClient.delete(`/asset-types/${id}`);
};
