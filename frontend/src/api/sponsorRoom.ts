import apiClient from './client';

/** How full each time slot is on one station for the next days (GET /spot-schedules/room). */
export interface RoomSponsor {
  sponsor_id: string;
  sponsor_name: string;
  schedule_id: string;
  contract: string | null;
  plays: number;
}

export interface RoomCell {
  date: string; // YYYY-MM-DD, station's own calendar
  slot: string; // early_morning ... overnight
  room: number; // places for sponsorships
  booked: number; // plays the planner put there
  no_room: number; // plays it wanted there and could not seat
  off_air: boolean; // the whole slot is in Shabbos or Yom Tov
  sponsors: RoomSponsor[];
}

export interface SlotRoom {
  station_id: string;
  timezone: string;
  days: number;
  mode: 'blocks' | 'clock';
  spots_off: boolean;
  slots: { key: string; start_hour: number; end_hour: number }[];
  cells: RoomCell[];
}

export const getSlotRoom = async (stationId: string, days = 7): Promise<SlotRoom> =>
  (await apiClient.get<SlotRoom>('/spot-schedules/room', { params: { station_id: stationId, days } })).data;

/** Monday = 0, like a contract's days_of_week */
export function weekday(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number);
  return (new Date(y, m - 1, d).getDay() + 6) % 7;
}

export type Fullness = 'empty' | 'room' | 'nearly' | 'full' | 'off';

export function fullness(c: { room: number; booked: number; no_room: number; off_air: boolean }): Fullness {
  if (c.off_air) return 'off';
  if (c.no_room > 0 || (c.room > 0 && c.booked >= c.room) || (c.room === 0 && c.booked > 0)) return 'full';
  if (c.room === 0) return 'off';
  if (c.booked === 0) return 'empty';
  return c.booked / c.room >= 0.75 ? 'nearly' : 'room';
}

/** The fullest of the coming days for one slot, only on the given weekdays. */
export function fullestDay(data: SlotRoom | undefined, slot: string, days: number[]): RoomCell | null {
  if (!data) return null;
  const cells = data.cells.filter((c) => c.slot === slot && days.includes(weekday(c.date)) && !c.off_air && c.room > 0);
  if (!cells.length) return null;
  const share = (c: RoomCell) => (c.booked + c.no_room) / Math.max(1, c.room);
  return cells.reduce((a, b) => (share(b) > share(a) ? b : a));
}
