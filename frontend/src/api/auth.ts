import apiClient from './client';
import type { TokenResponse, User } from '../types';

export const login = async (email: string, password: string): Promise<TokenResponse> => {
  const res = await apiClient.post<TokenResponse>('/auth/login', { email, password });
  return res.data;
};

export const refreshToken = async (refresh_token: string): Promise<TokenResponse> => {
  const res = await apiClient.post<TokenResponse>('/auth/refresh', { refresh_token });
  return res.data;
};

export const getMe = async (): Promise<User> => {
  const res = await apiClient.get<User>('/auth/me');
  return res.data;
};
