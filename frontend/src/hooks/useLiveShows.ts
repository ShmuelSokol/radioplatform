import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getLiveShows,
  getLiveShow,
  createLiveShow,
  updateLiveShow,
  deleteLiveShow,
  startLiveShow,
  endLiveShow,
  getShowCalls,
  approveCall,
  rejectCall,
  putCallerOnAir,
  endCall,
  updateCallInfo,
  getTimeRemaining,
} from '../api/liveShows';

export function useLiveShows(params?: { station_id?: string; status?: string }) {
  return useQuery({
    queryKey: ['live-shows', params],
    queryFn: () => getLiveShows(params),
    refetchInterval: 10000,
  });
}

export function useLiveShow(id: string | undefined) {
  return useQuery({
    queryKey: ['live-show', id],
    queryFn: () => getLiveShow(id!),
    enabled: !!id,
    refetchInterval: 5000,
  });
}

export function useCreateLiveShow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createLiveShow,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['live-shows'] }),
  });
}

export function useUpdateLiveShow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Parameters<typeof updateLiveShow>[1] }) =>
      updateLiveShow(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['live-shows'] });
      queryClient.invalidateQueries({ queryKey: ['live-show'] });
    },
  });
}

export function useDeleteLiveShow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteLiveShow,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['live-shows'] }),
  });
}

export function useStartLiveShow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: startLiveShow,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['live-shows'] });
      queryClient.invalidateQueries({ queryKey: ['live-show'] });
    },
  });
}

export function useEndLiveShow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: endLiveShow,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['live-shows'] });
      queryClient.invalidateQueries({ queryKey: ['live-show'] });
    },
  });
}

export function useShowCalls(showId: string | undefined) {
  return useQuery({
    queryKey: ['show-calls', showId],
    queryFn: () => getShowCalls(showId!),
    enabled: !!showId,
    refetchInterval: 15000,
  });
}

export function useApproveCall() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ showId, callId }: { showId: string; callId: string }) =>
      approveCall(showId, callId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['show-calls'] }),
  });
}

export function useRejectCall() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ showId, callId }: { showId: string; callId: string }) =>
      rejectCall(showId, callId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['show-calls'] }),
  });
}

export function usePutCallerOnAir() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ showId, callId }: { showId: string; callId: string }) =>
      putCallerOnAir(showId, callId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['show-calls'] }),
  });
}

export function useEndCall() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ showId, callId }: { showId: string; callId: string }) =>
      endCall(showId, callId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['show-calls'] }),
  });
}

export function useUpdateCallInfo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ showId, callId, data }: {
      showId: string;
      callId: string;
      data: { caller_name?: string; notes?: string };
    }) => updateCallInfo(showId, callId, data),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['show-calls'] }),
  });
}

export function useTimeRemaining(showId: string | undefined) {
  return useQuery({
    queryKey: ['time-remaining', showId],
    queryFn: () => getTimeRemaining(showId!),
    enabled: !!showId,
    refetchInterval: 10000,
  });
}
