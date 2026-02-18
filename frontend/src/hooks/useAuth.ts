import { useQuery } from '@tanstack/react-query';
import { getMe } from '../api/auth';
import { useAuthStore } from '../stores/authStore';
import { useEffect } from 'react';

export function useAuth() {
  const { isAuthenticated, setUser, logout } = useAuthStore();

  const { data: user, isLoading } = useQuery({
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

  return { user: user ?? null, isLoading, isAuthenticated, logout };
}
