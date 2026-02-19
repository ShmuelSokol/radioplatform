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
