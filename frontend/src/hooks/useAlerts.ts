import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { getAlerts, getUnresolvedCount, resolveAlert, reopenAlert, deleteAlert } from '../api/alerts';

export function useAlerts(filters?: {
  skip?: number;
  limit?: number;
  severity?: string;
  alert_type?: string;
  is_resolved?: boolean;
}) {
  return useQuery({
    queryKey: ['alerts', filters],
    queryFn: () => getAlerts(filters),
  });
}

export function useUnresolvedCount() {
  return useQuery({
    queryKey: ['alerts', 'unresolved-count'],
    queryFn: () => getUnresolvedCount(),
    refetchInterval: 30_000,
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
