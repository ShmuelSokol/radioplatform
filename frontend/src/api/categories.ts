import apiClient from './client';

export interface CategoryItem {
  id: string;
  name: string;
  created_at: string | null;
}

export const listCategories = async (): Promise<CategoryItem[]> => {
  const res = await apiClient.get<CategoryItem[]>('/categories');
  return res.data;
};

export const createCategory = async (name: string): Promise<CategoryItem> => {
  const res = await apiClient.post<CategoryItem>('/categories', { name });
  return res.data;
};

export const updateCategory = async (id: string, name: string): Promise<CategoryItem> => {
  const res = await apiClient.patch<CategoryItem>(`/categories/${id}`, { name });
  return res.data;
};

export const deleteCategory = async (id: string): Promise<void> => {
  await apiClient.delete(`/categories/${id}`);
};

/** Where an item can play: main category, extra ones ("Also in"), holiday lists
 *  (backend services/asset_categories.py). */
export interface AssetCategories {
  id: string;
  category: string | null;
  extra_categories: string[];
  holiday_lists: { holiday: string; name: string; all_year: boolean; station_name: string | null }[];
}

export const getAssetCategories = async (assetId: string): Promise<AssetCategories> =>
  (await apiClient.get<AssetCategories>(`/assets/${assetId}/categories`)).data;

export const setAssetExtraCategories = async (assetId: string, extra: string[]): Promise<AssetCategories> =>
  (await apiClient.put<AssetCategories>(`/assets/${assetId}/categories`, { extra_categories: extra })).data;
