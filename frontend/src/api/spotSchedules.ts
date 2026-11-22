import apiClient from './client';

export type Daypart =
  | 'early_morning' | 'late_morning' | 'early_afternoon' | 'late_afternoon' | 'early_evening' | 'late_evening'
  | 'morning' | 'afternoon' | 'evening' | 'overnight';

/** When a contract may play. The three-hour slots come first; the planner spreads
 * plays evenly over the breaks inside a slot (every break gets 3 before any gets 4). */
export const TIME_SLOTS: { key: Daypart; label: string; hours: string }[] = [
  { key: 'early_morning', label: 'Early morning', hours: '6 AM to 9 AM' },
  { key: 'late_morning', label: 'Late morning', hours: '9 AM to 12 PM' },
  { key: 'early_afternoon', label: 'Early afternoon', hours: '12 PM to 3 PM' },
  { key: 'late_afternoon', label: 'Late afternoon', hours: '3 PM to 6 PM' },
  { key: 'early_evening', label: 'Early evening', hours: '6 PM to 9 PM' },
  { key: 'late_evening', label: 'Late evening', hours: '9 PM to 12 AM' },
  { key: 'overnight', label: 'Overnight', hours: '12 AM to 6 AM' },
];
/** Wider choices, kept for contracts made before the slots */
export const WIDE_DAYPARTS: { key: Daypart; label: string; hours: string }[] = [
  { key: 'morning', label: 'Morning', hours: '6 AM to 12 PM' },
  { key: 'afternoon', label: 'Afternoon', hours: '12 PM to 6 PM' },
  { key: 'evening', label: 'Evening', hours: '6 PM to 12 AM' },
];
/** New contracts start in the six daytime slots */
export const DEFAULT_SLOTS: Daypart[] = TIME_SLOTS.filter((s) => s.key !== 'overnight').map((s) => s.key);

/** "late morning (9 AM to 12 PM)" */
export function daypartText(key: string, capital = false): string {
  const d = [...TIME_SLOTS, ...WIDE_DAYPARTS].find((x) => x.key === key);
  if (!d) return key;
  const label = capital ? d.label : d.label.toLowerCase();
  return `${label} (${d.hours})`;
}

export type TimingMode = 'hard' | 'soft';

export interface TimingDefaults {
  on_the_hour: TimingMode;
  // The half hour (:30): the time check, weather and sponsorships there
  half_hour: TimingMode;
  other: TimingMode;
  // Play the recorded half-hour time check ("The time is 10:30 ...") before the :30 weather
  half_hour_time_check: boolean;
  // No sponsorships between these times every night ("00:35" to "06:00"); both "" = none
  sponsor_quiet_start?: string;
  sponsor_quiet_end?: string;
  // Sponsorships stop this many minutes before Shabbos and Yom Tov (0 = right up to it)
  sponsors_stop_before_blackout_minutes?: number;
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

/** The control room's Sponsor panel: one spot's sponsor, contracts and plays on one station. */
export interface SponsorItemSponsor {
  id: string;
  name: string;
  company_name: string | null;
  contact_phone: string | null;
  contact_email: string | null;
}
export interface SponsorItemContract {
  id: string;
  name: string | null;
  sponsor: SponsorItemSponsor | null;
  station_id: string | null;
  station_name: string;
  on_this_station: boolean;
  state: 'running' | 'paused' | 'ended' | 'not_started';
  days_of_week: string;
  days_text: string;
  dayparts: string[];
  plays_per_daypart: number;
  fixed_times: string[];
  timing_mode: string | null;
  start_date: string | null;
  end_date: string | null;
  is_active: boolean;
}
export interface SponsorItemPlay {
  entry_id: string;
  status: string;
  status_text: string;
  source: string;
  at: string | null;
  due: string | null;
  local_time: string | null;
  local_day: string | null;
  timing_mode: string | null;
  can_skip: boolean;
  can_restore: boolean;
}
export interface SponsorItemMissed {
  when: 'past' | 'coming';
  local_date: string;
  day: string;
  daypart: string;
  promised: number;
  played: number;
  missed: number;
  moved: number;
  note: string;
  /** Set for a single play today that did not air (its time of day is not over yet) */
  local_time?: string;
}
export interface SponsorItem {
  asset: { id: string; title: string; artist: string | null; asset_type: string; category: string | null; duration: number | null };
  station: { id: string; name: string; timezone: string };
  owner: SponsorItemSponsor | null;
  contracts: SponsorItemContract[];
  aired_today: number;
  today: SponsorItemPlay[];
  upcoming: SponsorItemPlay[];
  missed: SponsorItemMissed[];
}

export const getSponsorItem = async (assetId: string, stationId: string): Promise<SponsorItem> =>
  (await apiClient.get<SponsorItem>(`/spot-schedules/for-asset/${assetId}`, { params: { station_id: stationId } })).data;

/** Skip one coming sponsorship play, or put it back (restore). */
export const skipSponsorshipPlay = async (stationId: string, entryId: string, restore = false) =>
  (await apiClient.post<{ entry_id: string; status: string }>(
    `/stations/${stationId}/queue/${entryId}/skip-sponsorship`, null, { params: restore ? { restore: true } : undefined },
  )).data;

/** The control room's Sponsors list: today's sponsorship plays on one station. */
export interface StationDayPlay {
  entry_id: string;
  status: string;
  status_text: string;
  source: string;
  at: string;
  local_time: string;
  asset: { id: string; title: string; artist: string | null; asset_type: string; category: string | null; duration: number | null; sponsor_id?: string | null };
  sponsor_name: string | null;
}
export interface StationDay {
  station: { id: string; name: string; timezone: string };
  date: string;
  plays: StationDayPlay[];
}
export const getStationDay = async (stationId: string): Promise<StationDay> =>
  (await apiClient.get<StationDay>('/spot-schedules/station-day', { params: { station_id: stationId } })).data;
