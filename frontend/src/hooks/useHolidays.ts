import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listHolidays, createHoliday, updateHoliday, deleteHoliday, HolidayFilters } from '../api/holidays';

export function useHolidays(filters?: HolidayFilters) {
  return useQuery({
    queryKey: ['holidays', filters],
    queryFn: () => listHolidays(filters),
    staleTime: 60_000,
  });
}

export function useCreateHoliday() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createHoliday,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['holidays'] });
    },
  });
}

export function useUpdateHoliday() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Parameters<typeof updateHoliday>[1]> }) =>
      updateHoliday(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['holidays'] });
    },
  });
}

export function useDeleteHoliday() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteHoliday(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['holidays'] });
    },
  });
}
