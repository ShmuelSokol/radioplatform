import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getWeatherReadouts,
  createWeatherReadout,
  updateWeatherReadout,
  regenerateWeatherReadout,
  queueWeatherReadout,
  deleteWeatherReadout,
  getTemplatePreview,
} from '../api/weatherReadouts';

export function useWeatherReadouts(params?: {
  station_id?: string;
  date_from?: string;
  date_to?: string;
  status?: string;
}) {
  return useQuery({
    queryKey: ['weather-readouts', params],
    queryFn: () => getWeatherReadouts(params),
    enabled: !!params?.station_id,
  });
}

export function useCreateWeatherReadout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createWeatherReadout,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['weather-readouts'] }),
  });
}

export function useUpdateWeatherReadout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Parameters<typeof updateWeatherReadout>[1] }) =>
      updateWeatherReadout(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['weather-readouts'] }),
  });
}

export function useRegenerateWeatherReadout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: regenerateWeatherReadout,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['weather-readouts'] }),
  });
}

export function useQueueWeatherReadout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: queueWeatherReadout,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['weather-readouts'] }),
  });
}

export function useDeleteWeatherReadout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: deleteWeatherReadout,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['weather-readouts'] }),
  });
}

export function useWeatherTemplatePreview(stationId: string | undefined, template?: string) {
  return useQuery({
    queryKey: ['weather-template-preview', stationId, template],
    queryFn: () => getTemplatePreview(stationId!, template),
    enabled: !!stationId,
  });
}
