import apiClient from './client';

export type Daypart = 'morning' | 'afternoon' | 'evening' | 'overnight';

export interface SpotSchedule {
  id: string;
  sponsor_id: string;
  asset_id: string;
  station_id: string | null;
  name: string | null;
  days_of_week: string; // "0,1,..." 0 = Monday
  dayparts: string; // "morning,afternoon,evening"
  plays_per_daypart: number;
  start_date: string | null;
  end_date: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export type SpotScheduleInput = Omit<SpotSchedule, 'id' | 'created_at' | 'updated_at'>;

export interface PlannedSpot {
  scheduled_at: string;
  local_time: string; // "YYYY-MM-DD HH:MM" in the station's time zone
  daypart: Daypart;
  schedule_id: string;
  sponsor_id: string;
  sponsor_name: string;
  asset_id: string;
  asset_title: string;
}

export interface UnplacedSpot {
  local_date: string;
  daypart: Daypart;
  schedule_id: string;
  sponsor_name: string;
  reason: string;
}

export interface OffAirWindow {
  name: string;
  start: string;
  end: string;
  skipped_plays: number;
}

export interface SpotPlanPreview {
  station_id: string;
  timezone: string;
  days: number;
  planned: PlannedSpot[];
  unplaced: UnplacedSpot[];
  off_air?: OffAirWindow[];
}

export const listSpotSchedules = async (): Promise<SpotSchedule[]> =>
  (await apiClient.get<SpotSchedule[]>('/spot-schedules')).data;

export const createSpotSchedule = async (data: SpotScheduleInput): Promise<SpotSchedule> =>
  (await apiClient.post<SpotSchedule>('/spot-schedules', data)).data;

export const updateSpotSchedule = async (
  id: string,
  data: Partial<SpotScheduleInput>,
): Promise<SpotSchedule> => (await apiClient.put<SpotSchedule>(`/spot-schedules/${id}`, data)).data;

export const deleteSpotSchedule = async (id: string): Promise<void> => {
  await apiClient.delete(`/spot-schedules/${id}`);
};

export const previewSpotPlan = async (stationId: string, days = 7): Promise<SpotPlanPreview> =>
  (
    await apiClient.get<SpotPlanPreview>('/spot-schedules/preview', {
      params: { station_id: stationId, days },
    })
  ).data;
