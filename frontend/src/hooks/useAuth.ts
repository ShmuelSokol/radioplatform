import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getMe } from '../api/auth';
import { useAuthStore } from '../stores/authStore';
import { useEffect } from 'react';

export function useAuth() {
  const { isAuthenticated, setUser, logout } = useAuthStore();
  const queryClient = useQueryClient();

  const { data: user, isLoading, refetch } = useQuery({
    queryKey: ['me'],
    queryFn: getMe,
    enabled: isAuthenticated,
    retry: false,
  });

  useEffect(() => {
    if (user) {
      setUser(user);
    }
  }, [user, setUser]);

  // Forget the previous account on sign-out so the next person to sign in on
  // this browser is never judged by the last person's role.
  useEffect(() => {
    if (!isAuthenticated) queryClient.removeQueries({ queryKey: ['me'] });
  }, [isAuthenticated, queryClient]);

  return { user: isAuthenticated ? user ?? null : null, isLoading, isAuthenticated, logout, refetch };
}
