import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import type { QueryClient, QueryKey } from '@tanstack/react-query';
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
import type {
  CreateTemplateData, UpdateTemplateData, CreateSlotData, UpdateSlotData, PlaylistTemplate, TemplateSlot,
} from '../api/playlists';

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

// Step edits show at once: the cached playlists change before the server
// answers, and go back if the save fails. The server rebuilds the queue in the
// background, so its answer is quick too.
type TemplatesCache = PlaylistTemplate[] | PlaylistTemplate | undefined;

const editSlots = (
  qc: QueryClient,
  change: (t: PlaylistTemplate) => PlaylistTemplate,
) => {
  const snapshot = qc.getQueriesData<TemplatesCache>({ queryKey: ['playlist-templates'] });
  for (const [key, data] of snapshot) {
    if (!data) continue;
    qc.setQueryData<TemplatesCache>(key, Array.isArray(data) ? data.map(change) : change(data));
  }
  return snapshot;
};

const optimistic = <V,>(
  qc: QueryClient,
  change: (vars: V) => (t: PlaylistTemplate) => PlaylistTemplate,
) => ({
  onMutate: async (vars: V) => {
    await qc.cancelQueries({ queryKey: ['playlist-templates'] });
    return { snapshot: editSlots(qc, change(vars)) };
  },
  onError: (_e: unknown, _v: V, ctx?: { snapshot: [QueryKey, TemplatesCache][] }) => {
    ctx?.snapshot.forEach(([key, data]) => qc.setQueryData(key, data));
  },
  // Several quick edits (a reorder sends one per moved step) refetch once, after the last
  onSettled: () => {
    if (qc.isMutating({ mutationKey: ['playlist-slot'] }) <= 1) {
      qc.invalidateQueries({ queryKey: ['playlist-templates'] });
    }
  },
});

let tempSeq = 0;

export const useCreateTemplateSlot = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: ['playlist-slot'],
    mutationFn: (data: CreateSlotData) => createSlot(data),
    ...optimistic<CreateSlotData>(qc, (data) => (t) => {
      if (t.id !== data.template_id) return t;
      const now = new Date().toISOString();
      const slot: TemplateSlot = {
        id: `pending-${++tempSeq}`,
        template_id: data.template_id,
        position: data.position,
        asset_type: data.asset_type,
        category: data.category ?? null,
        created_at: now,
        updated_at: now,
      };
      return { ...t, slots: [...t.slots, slot] };
    }),
  });
};

export const useUpdateTemplateSlot = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: ['playlist-slot'],
    mutationFn: ({ id, data }: { id: string; data: UpdateSlotData }) => updateSlot(id, data),
    ...optimistic<{ id: string; data: UpdateSlotData }>(qc, ({ id, data }) => (t) =>
      t.slots.some((s) => s.id === id)
        ? { ...t, slots: t.slots.map((s) => (s.id === id ? { ...s, ...data } as TemplateSlot : s)) }
        : t),
  });
};

export const useDeleteTemplateSlot = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: ['playlist-slot'],
    mutationFn: (id: string) => deleteSlot(id),
    ...optimistic<string>(qc, (id) => (t) =>
      t.slots.some((s) => s.id === id) ? { ...t, slots: t.slots.filter((s) => s.id !== id) } : t),
  });
};

export const useAssetTypes = () => {
  return useQuery({
    queryKey: ['asset-types'],
    queryFn: () => listAssetTypes(),
  });
};
