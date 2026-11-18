import apiClient from './client';
import type { User, UserListResponse, PublicHostsResponse } from '../types';

export const listUsers = async (skip = 0, limit = 50): Promise<UserListResponse> => {
  const res = await apiClient.get<UserListResponse>('/users', { params: { skip, limit } });
  return res.data;
};

/** Every user, page by page: the server sends at most 100 at a time, and the Users
 *  screen has no paging, so with one call the users after the first 50 never showed. */
export const listAllUsers = async (): Promise<UserListResponse> => {
  const first = await listUsers(0, 100);
  const users = [...first.users];
  while (users.length < first.total) {
    const next = await listUsers(users.length, 100);
    if (!next.users.length) break;
    users.push(...next.users);
  }
  return { ...first, users, total: Math.max(first.total, users.length) };
};

export const createUser = async (data: {
  email: string; password: string; role: string; display_name?: string;
  phone_number?: string; title?: string; alert_preferences?: Record<string, unknown>;
  bio?: string; photo_url?: string; is_public?: boolean; social_links?: Record<string, string>;
  station_ids?: string[] | null;
}): Promise<User> => {
  const res = await apiClient.post<User>('/users', data);
  return res.data;
};

export const updateUser = async (id: string, data: {
  email?: string; password?: string; role?: string; display_name?: string; is_active?: boolean;
  phone_number?: string; title?: string; alert_preferences?: Record<string, unknown>;
  bio?: string; photo_url?: string; is_public?: boolean; social_links?: Record<string, string>;
  station_ids?: string[] | null;
}): Promise<User> => {
  const res = await apiClient.put<User & { new_tokens?: { access_token: string; refresh_token: string } | null }>(
    `/users/${id}`, data,
  );
  // Changing your own password signs your other devices out; the server sends
  // this browser fresh tokens so it stays signed in.
  const fresh = res.data.new_tokens;
  if (fresh?.access_token && fresh.refresh_token) {
    localStorage.setItem('access_token', fresh.access_token);
    localStorage.setItem('refresh_token', fresh.refresh_token);
  }
  return res.data;
};

export const deleteUser = async (id: string): Promise<void> => {
  await apiClient.delete(`/users/${id}`);
};

export const listPublicHosts = async (): Promise<PublicHostsResponse> => {
  const res = await apiClient.get<PublicHostsResponse>('/users/public/hosts');
  return res.data;
};
