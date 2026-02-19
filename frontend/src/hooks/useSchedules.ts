import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../api/client';

export interface Schedule {
  id: string;
  station_id: string;
  name: string;
  description?: string;
  is_active: boolean;
  priority: number;
  created_at: string;
  updated_at: string;
  blocks?: ScheduleBlock[];
}

export interface ScheduleBlock {
  id: string;
  schedule_id: string;
  name: string;
  description?: string;
  start_time: string;
  end_time: string;
  recurrence_type: 'daily' | 'weekly' | 'monthly' | 'one_time';
  recurrence_pattern?: any[];
  priority: number;
  created_at: string;
  updated_at: string;
  playlist_entries?: PlaylistEntry[];
}

export interface PlaylistEntry {
  id: string;
  block_id: string;
  asset_id: string;
  position: number;
  weight: number;
  playback_mode: 'sequential' | 'shuffle' | 'weighted';
  is_enabled: boolean;
  playback_config?: any;
  created_at: string;
  updated_at: string;
}

export interface CreateScheduleData {
  station_id: string;
  name: string;
  description?: string;
  is_active?: boolean;
  priority?: number;
}

export interface UpdateScheduleData {
  name?: string;
  description?: string;
  is_active?: boolean;
  priority?: number;
}

export interface CreateScheduleBlockData {
  schedule_id: string;
  name: string;
  description?: string;
  start_time: string;
  end_time: string;
  recurrence_type?: 'daily' | 'weekly' | 'monthly' | 'one_time';
  recurrence_pattern?: any[];
  priority?: number;
}

export interface UpdateScheduleBlockData {
  name?: string;
  description?: string;
  start_time?: string;
  end_time?: string;
  recurrence_type?: 'daily' | 'weekly' | 'monthly' | 'one_time';
  recurrence_pattern?: any[];
  priority?: number;
}

export interface CreatePlaylistEntryData {
  block_id: string;
  asset_id: string;
  position?: number;
  weight?: number;
  playback_mode?: 'sequential' | 'shuffle' | 'weighted';
  is_enabled?: boolean;
  playback_config?: any;
}

// ==================== Schedules ====================
export const useSchedules = (stationId?: string) => {
  return useQuery<Schedule[]>({
    queryKey: ['schedules', stationId],
    queryFn: async () => {
      const params = stationId ? { station_id: stationId } : {};
      const response = await apiClient.get('/schedules', { params });
      return response.data;
    },
  });
};

export const useSchedule = (scheduleId: string) => {
  return useQuery<Schedule>({
    queryKey: ['schedules', scheduleId],
    queryFn: async () => {
      const response = await apiClient.get(`/schedules/${scheduleId}`);
      return response.data;
    },
    enabled: !!scheduleId,
  });
};

export const useCreateSchedule = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: CreateScheduleData) => {
      const response = await apiClient.post('/schedules', data);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
    },
  });
};

export const useUpdateSchedule = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, data }: { id: string; data: UpdateScheduleData }) => {
      const response = await apiClient.patch(`/schedules/${id}`, data);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
    },
  });
};

export const useDeleteSchedule = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/schedules/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
    },
  });
};

// ==================== Schedule Blocks ====================
export const useScheduleBlocks = (scheduleId?: string) => {
  return useQuery<ScheduleBlock[]>({
    queryKey: ['schedule-blocks', scheduleId],
    queryFn: async () => {
      const params = scheduleId ? { schedule_id: scheduleId } : {};
      const response = await apiClient.get('/schedules/blocks', { params });
      return response.data;
    },
  });
};

export const useCreateScheduleBlock = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: CreateScheduleBlockData) => {
      const response = await apiClient.post('/schedules/blocks', data);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      queryClient.invalidateQueries({ queryKey: ['schedule-blocks'] });
    },
  });
};

export const useUpdateScheduleBlock = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, data }: { id: string; data: UpdateScheduleBlockData }) => {
      const response = await apiClient.patch(`/schedules/blocks/${id}`, data);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      queryClient.invalidateQueries({ queryKey: ['schedule-blocks'] });
    },
  });
};

export const useDeleteScheduleBlock = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/schedules/blocks/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      queryClient.invalidateQueries({ queryKey: ['schedule-blocks'] });
    },
  });
};

// ==================== Playlist Entries ====================
export const usePlaylistEntries = (blockId?: string) => {
  return useQuery<PlaylistEntry[]>({
    queryKey: ['playlist-entries', blockId],
    queryFn: async () => {
      const params = blockId ? { block_id: blockId } : {};
      const response = await apiClient.get('/schedules/playlist-entries', { params });
      return response.data;
    },
  });
};

export const useCreatePlaylistEntry = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (data: CreatePlaylistEntryData) => {
      const response = await apiClient.post('/schedules/playlist-entries', data);
      return response.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      queryClient.invalidateQueries({ queryKey: ['schedule-blocks'] });
      queryClient.invalidateQueries({ queryKey: ['playlist-entries'] });
    },
  });
};

export const useDeletePlaylistEntry = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete(`/schedules/playlist-entries/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
      queryClient.invalidateQueries({ queryKey: ['schedule-blocks'] });
      queryClient.invalidateQueries({ queryKey: ['playlist-entries'] });
    },
  });
};
