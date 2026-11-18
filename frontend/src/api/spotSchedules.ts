import apiClient from './client';

export type Daypart = 'morning' | 'afternoon' | 'evening' | 'overnight';

export type TimingMode = 'hard' | 'soft';

export interface TimingDefaults {
  on_the_hour: TimingMode;
  // The half hour (:30): the time check, weather and sponsorships there
  half_hour: TimingMode;
  other: TimingMode;
  // Play the recorded half-hour time check ("The time is 10:30 ...") before the :30 weather
  half_hour_time_check: boolean;
}

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
  fixed_times: string | null; // "07:00,13:30" station time; replaces the daypart spread
  timing_mode: TimingMode | null; // null = the station's default for that slot
  created_at: string;
  updated_at: string;
  /** After a save: plays that do not fit in the coming days */
  warnings?: string[];
}

export type SpotScheduleInput = Omit<SpotSchedule, 'id' | 'created_at' | 'updated_at' | 'warnings'>;

export interface PlannedSpot {
  scheduled_at: string;
  local_time: string; // "YYYY-MM-DD HH:MM" in the station's time zone
  daypart: Daypart;
  timing_mode: TimingMode;
  fixed: boolean;
  block: string | null; // the program block whose sponsor place this is
  moved_from?: Daypart | null; // its own time of day was full, so it rolled to this block
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
  count: number;
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
  /** Spots mode is off in the control room: nothing is planned until it is switched on */
  spots_off?: boolean;
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

export const getTimingDefaults = async (stationId: string): Promise<TimingDefaults> =>
  (await apiClient.get<TimingDefaults>('/spot-schedules/timing-defaults', { params: { station_id: stationId } })).data;

export const setTimingDefaults = async (stationId: string, defaults: TimingDefaults): Promise<TimingDefaults> =>
  (await apiClient.put<TimingDefaults>('/spot-schedules/timing-defaults', { station_id: stationId, ...defaults })).data;
