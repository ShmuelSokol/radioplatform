import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listTemplates,
  getTemplate,
  createTemplate,
  updateTemplate,
  deleteTemplate,
  createSlot,
  updateSlot,
  deleteSlot,
  listAssetTypes,
} from '../api/playlists';
import type { CreateTemplateData, UpdateTemplateData, CreateSlotData, UpdateSlotData } from '../api/playlists';

export const usePlaylistTemplates = (stationId?: string) => {
  return useQuery({
    queryKey: ['playlist-templates', stationId],
    queryFn: () => listTemplates(stationId),
  });
};

export const usePlaylistTemplate = (id: string) => {
  return useQuery({
    queryKey: ['playlist-templates', id],
    queryFn: () => getTemplate(id),
    enabled: !!id,
  });
};

export const useCreatePlaylistTemplate = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateTemplateData) => createTemplate(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['playlist-templates'] }),
  });
};

export const useUpdatePlaylistTemplate = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateTemplateData }) => updateTemplate(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['playlist-templates'] }),
  });
};

export const useDeletePlaylistTemplate = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteTemplate(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['playlist-templates'] }),
  });
};

export const useCreateTemplateSlot = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateSlotData) => createSlot(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['playlist-templates'] }),
  });
};

export const useUpdateTemplateSlot = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateSlotData }) => updateSlot(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['playlist-templates'] }),
  });
};

export const useDeleteTemplateSlot = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteSlot(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['playlist-templates'] }),
  });
};

export const useAssetTypes = () => {
  return useQuery({
    queryKey: ['asset-types'],
    queryFn: () => listAssetTypes(),
  });
};
