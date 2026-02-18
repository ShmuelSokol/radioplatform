import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listRules, createRule, updateRule, deleteRule, previewSchedule } from '../api/rules';
import type { ScheduleRule } from '../types';

export function useRules() {
  return useQuery({ queryKey: ['rules'], queryFn: () => listRules() });
}

export function useCreateRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<ScheduleRule>) => createRule(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['rules'] }),
  });
}

export function useUpdateRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<ScheduleRule> }) => updateRule(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['rules'] }),
  });
}

export function useDeleteRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteRule(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['rules'] }),
  });
}

export function useSchedulePreview(date: string | null) {
  return useQuery({
    queryKey: ['schedule-preview', date],
    queryFn: () => previewSchedule(date!),
    enabled: !!date,
  });
}
