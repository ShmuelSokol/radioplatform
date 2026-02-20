import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, useEffect, useCallback } from 'react';
import {
  crmRegister,
  crmLogin,
  rateSong,
  removeRating,
  getMyRatings,
  getActiveRaffles,
  enterRaffle,
  getCrmMembers,
  getSongRankings,
  getRaffles,
  createRaffle,
  updateRaffle,
  drawRaffle,
  getRaffleEntries,
  deactivateCrmMember,
  getCrmPin,
  setCrmPin,
  clearCrmPin,
  type LoginResponse,
  type RegisterRequest,
  type RateRequest,
  type RaffleCreate,
} from '../api/crm';

// ── CRM Auth Hook ─────────────────────────────────────────────

export function useCrmAuth() {
  const [pin, setPin] = useState<string | null>(getCrmPin());
  const [profile, setProfile] = useState<LoginResponse | null>(null);
  const queryClient = useQueryClient();

  const isLoggedIn = !!pin && !!profile;

  // Auto-login on mount if PIN exists in localStorage
  useEffect(() => {
    const storedPin = getCrmPin();
    if (storedPin && !profile) {
      crmLogin(storedPin)
        .then((res) => {
          setProfile(res);
          setPin(storedPin);
        })
        .catch(() => {
          clearCrmPin();
          setPin(null);
        });
    }
  }, []);

  const register = useCallback(async (data: RegisterRequest) => {
    const res = await crmRegister(data);
    setCrmPin(res.pin);
    setPin(res.pin);
    // Auto-login after registration
    const loginRes = await crmLogin(res.pin);
    setProfile(loginRes);
    return res;
  }, []);

  const login = useCallback(async (pinValue: string) => {
    const res = await crmLogin(pinValue);
    setCrmPin(pinValue);
    setPin(pinValue);
    setProfile(res);
    return res;
  }, []);

  const logout = useCallback(() => {
    clearCrmPin();
    setPin(null);
    setProfile(null);
    queryClient.invalidateQueries({ queryKey: ['crm'] });
  }, [queryClient]);

  const refreshProfile = useCallback(async () => {
    const storedPin = getCrmPin();
    if (storedPin) {
      const res = await crmLogin(storedPin);
      setProfile(res);
    }
  }, []);

  return { pin, profile, isLoggedIn, register, login, logout, refreshProfile };
}

// ── My Ratings Hook ───────────────────────────────────────────

export function useMyRatings(enabled = true) {
  return useQuery({
    queryKey: ['crm', 'my-ratings'],
    queryFn: getMyRatings,
    enabled: enabled && !!getCrmPin(),
  });
}

export function useRateSong() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: RateRequest) => rateSong(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['crm', 'my-ratings'] });
    },
  });
}

export function useRemoveRating() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (assetId: string) => removeRating(assetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['crm', 'my-ratings'] });
    },
  });
}

// ── Raffles (public) ──────────────────────────────────────────

export function useActiveRaffles() {
  return useQuery({
    queryKey: ['crm', 'active-raffles'],
    queryFn: getActiveRaffles,
  });
}

export function useEnterRaffle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (raffleId: string) => enterRaffle(raffleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['crm', 'active-raffles'] });
    },
  });
}

// ── Admin: Members ────────────────────────────────────────────

export function useCrmMembers(skip = 0, limit = 50, search?: string) {
  return useQuery({
    queryKey: ['crm', 'members', skip, limit, search],
    queryFn: () => getCrmMembers(skip, limit, search),
  });
}

export function useDeactivateMember() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deactivateCrmMember(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['crm', 'members'] });
    },
  });
}

// ── Admin: Song Rankings ──────────────────────────────────────

export function useSongRankings(skip = 0, limit = 50) {
  return useQuery({
    queryKey: ['crm', 'song-rankings', skip, limit],
    queryFn: () => getSongRankings(skip, limit),
  });
}

// ── Admin: Raffles ────────────────────────────────────────────

export function useRafflesAdmin(skip = 0, limit = 50) {
  return useQuery({
    queryKey: ['crm', 'raffles', skip, limit],
    queryFn: () => getRaffles(skip, limit),
  });
}

export function useCreateRaffle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: RaffleCreate) => createRaffle(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['crm', 'raffles'] });
    },
  });
}

export function useUpdateRaffle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<RaffleCreate> }) => updateRaffle(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['crm', 'raffles'] });
    },
  });
}

export function useDrawRaffle() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => drawRaffle(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['crm', 'raffles'] });
    },
  });
}

export function useRaffleEntries(raffleId: string | null) {
  return useQuery({
    queryKey: ['crm', 'raffle-entries', raffleId],
    queryFn: () => getRaffleEntries(raffleId!),
    enabled: !!raffleId,
  });
}
