import apiClient from './client';
import type { User, UserListResponse } from '../types';

export const listUsers = async (skip = 0, limit = 50): Promise<UserListResponse> => {
  const res = await apiClient.get<UserListResponse>('/users', { params: { skip, limit } });
  return res.data;
};

export const createUser = async (data: {
  email: string; password: string; role: string; display_name?: string;
  phone_number?: string; title?: string; alert_preferences?: Record<string, unknown>;
}): Promise<User> => {
  const res = await apiClient.post<User>('/users', data);
  return res.data;
};

export const updateUser = async (id: string, data: {
  email?: string; password?: string; role?: string; display_name?: string; is_active?: boolean;
  phone_number?: string; title?: string; alert_preferences?: Record<string, unknown>;
}): Promise<User> => {
  const res = await apiClient.put<User>(`/users/${id}`, data);
  return res.data;
};

export const deleteUser = async (id: string): Promise<void> => {
  await apiClient.delete(`/users/${id}`);
};
