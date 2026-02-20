import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listCampaigns,
  getCampaign,
  createCampaign,
  updateCampaign,
  listDrafts,
  createDraft,
  listComments,
  createComment,
} from '../api/campaigns';

export function useCampaigns() {
  return useQuery({
    queryKey: ['campaigns'],
    queryFn: listCampaigns,
  });
}

export function useCampaign(id: string) {
  return useQuery({
    queryKey: ['campaign', id],
    queryFn: () => getCampaign(id),
    enabled: !!id,
  });
}

export function useCreateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: createCampaign,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['campaigns'] }),
  });
}

export function useUpdateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Parameters<typeof updateCampaign>[1] }) =>
      updateCampaign(id, data),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ['campaigns'] });
      qc.invalidateQueries({ queryKey: ['campaign', vars.id] });
    },
  });
}

export function useDrafts(campaignId: string) {
  return useQuery({
    queryKey: ['campaign-drafts', campaignId],
    queryFn: () => listDrafts(campaignId),
    enabled: !!campaignId,
  });
}

export function useCreateDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ campaignId, data }: { campaignId: string; data: Parameters<typeof createDraft>[1] }) =>
      createDraft(campaignId, data),
    onSuccess: (_, vars) => qc.invalidateQueries({ queryKey: ['campaign-drafts', vars.campaignId] }),
  });
}

export function useComments(campaignId: string) {
  return useQuery({
    queryKey: ['campaign-comments', campaignId],
    queryFn: () => listComments(campaignId),
    enabled: !!campaignId,
  });
}

export function useCreateComment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ campaignId, data }: { campaignId: string; data: Parameters<typeof createComment>[1] }) =>
      createComment(campaignId, data),
    onSuccess: (_, vars) => qc.invalidateQueries({ queryKey: ['campaign-comments', vars.campaignId] }),
  });
}
