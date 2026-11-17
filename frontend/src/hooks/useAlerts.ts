import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getAlerts, getUnresolvedCount, resolveAlert, reopenAlert, deleteAlert, resolveAllAlerts } from '../api/alerts';

export function useAlerts(filters?: {
  skip?: number;
  limit?: number;
  severity?: string;
  alert_type?: string;
  is_resolved?: boolean;
  station_id?: string;
} | null, live = false) {
  return useQuery({
    queryKey: ['alerts', filters],
    queryFn: () => getAlerts(filters!),
    enabled: !!filters,
    // live: the control room's Errors tab, where an on-air alarm must show within seconds
    staleTime: live ? 0 : 60_000,
    refetchInterval: live ? 10_000 : false,
    refetchOnWindowFocus: live ? true : undefined,
  });
}

export function useUnresolvedCount(enabled = true) {
  return useQuery({
    queryKey: ['alerts', 'unresolved-count'],
    queryFn: () => getUnresolvedCount(),
    refetchInterval: 30_000,
    enabled,
  });
}

export function useResolveAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => resolveAlert(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alerts'] });
    },
  });
}

export function useReopenAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => reopenAlert(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alerts'] });
    },
  });
}

export function useDeleteAlert() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteAlert(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alerts'] });
    },
  });
}

export function useResolveAllAlerts() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => resolveAllAlerts(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alerts'] });
    },
  });
}
