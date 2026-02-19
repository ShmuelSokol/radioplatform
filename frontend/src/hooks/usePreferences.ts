import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from '../api/client';
import type { UserPreferences } from '../types';

const getPreferences = async (): Promise<UserPreferences> => {
  const res = await apiClient.get<UserPreferences>('/users/me/preferences');
  return res.data;
};

const updatePreferences = async (prefs: Partial<UserPreferences>): Promise<UserPreferences> => {
  const res = await apiClient.patch<UserPreferences>('/users/me/preferences', prefs);
  return res.data;
};

export function usePreferences() {
  return useQuery({
    queryKey: ['user-preferences'],
    queryFn: getPreferences,
  });
}

export function useUpdatePreferences() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updatePreferences,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['user-preferences'] }),
  });
}
