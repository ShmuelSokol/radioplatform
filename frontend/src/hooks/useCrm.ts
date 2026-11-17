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

/** HTTP status of a failed request (undefined: no answer, e.g. the network is down). */
export function crmErrorStatus(err: unknown): number | undefined {
  return (err as { response?: { status?: number } })?.response?.status;
}

/** The server's own words for a refusal, when it sent some. */
export function crmErrorDetail(err: unknown): string | null {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' && detail ? detail : null;
}

/** The words a listener sees for a failed PIN login. */
export function crmLoginErrorText(err: unknown): string {
  const status = crmErrorStatus(err);
  if (status === 429) return crmErrorDetail(err) ?? 'Too many wrong PINs. Please wait a few minutes and try again.';
  if (status === 401 || status === 403 || status === 422) return 'Invalid PIN';
  if (status === undefined) return 'Could not reach the server. Please try again.';
  return 'Could not log in right now. Please try again.';
}

export function useCrmAuth() {
  const [pin, setPin] = useState<string | null>(getCrmPin());
  const [profile, setProfile] = useState<LoginResponse | null>(null);
  // A saved PIN is being tried on page load: the page shows "Signing you in..."
  // instead of flashing the PIN box
  const [autoLoginPending, setAutoLoginPending] = useState<boolean>(() => !!getCrmPin());
  // The saved PIN is kept, but signing in failed for another reason (network, 5xx, 429)
  const [autoLoginError, setAutoLoginError] = useState('');
  const queryClient = useQueryClient();

  const isLoggedIn = !!pin && !!profile;

  // Auto-login on mount if PIN exists in localStorage
  useEffect(() => {
    const storedPin = getCrmPin();
    if (!storedPin || profile) { setAutoLoginPending(false); return; }
    crmLogin(storedPin)
      .then((res) => {
        setProfile(res);
        setPin(storedPin);
      })
      .catch((err) => {
        // Only a PIN the server refuses is forgotten. A network error, a 5xx or a
        // 429 (too many wrong PINs from this address) used to delete it for good,
        // and with it the listener's only way back into their account.
        if (crmErrorStatus(err) === 401) {
          clearCrmPin();
          setPin(null);
        } else {
          setAutoLoginError(crmErrorStatus(err) === 429
            ? crmLoginErrorText(err)
            : 'Could not sign you in right now. Your PIN is saved; reload the page to try again.');
        }
      })
      .finally(() => setAutoLoginPending(false));
  }, []);

  /** Register, then log in. `loggedIn` is false when the account was made but the
   *  follow-up login failed: the PIN is still the listener's and must be shown. */
  const register = useCallback(async (data: RegisterRequest) => {
    const res = await crmRegister(data);
    setCrmPin(res.pin);
    setPin(res.pin);
    setAutoLoginError('');
    try {
      const loginRes = await crmLogin(res.pin);
      setProfile(loginRes);
      return { ...res, loggedIn: true };
    } catch {
      return { ...res, loggedIn: false };
    }
  }, []);

  const login = useCallback(async (pinValue: string) => {
    const res = await crmLogin(pinValue);
    setCrmPin(pinValue);
    setPin(pinValue);
    setProfile(res);
    setAutoLoginError('');
    return res;
  }, []);

  const logout = useCallback(() => {
    clearCrmPin();
    setPin(null);
    setProfile(null);
    setAutoLoginError('');
    queryClient.invalidateQueries({ queryKey: ['crm'] });
  }, [queryClient]);

  const refreshProfile = useCallback(async () => {
    const storedPin = getCrmPin();
    if (storedPin) {
      const res = await crmLogin(storedPin);
      setProfile(res);
    }
  }, []);

  return { pin, profile, isLoggedIn, autoLoginPending, autoLoginError, register, login, logout, refreshProfile };
}

// ── My Ratings Hook ───────────────────────────────────────────

export function useMyRatings(enabled = true, pin: string | null = null) {
  return useQuery({
    // Per PIN: after Logout and a login with another PIN, the old member's ratings must not show
    queryKey: ['crm', 'my-ratings', pin],
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

export function useActiveRaffles(stationId?: string) {
  return useQuery({
    queryKey: ['crm', 'active-raffles', stationId ?? null],
    queryFn: () => getActiveRaffles(stationId),
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
