import { useCallback, useMemo } from 'react';
import { useSchedules } from '../../../hooks/useSchedules';
import type { Schedule, ScheduleBlock } from '../../../hooks/useSchedules';
import { usePlaylistTemplates } from '../../../hooks/usePlaylists';
import type { PlaylistTemplate, TemplateSlot } from '../../../api/playlists';
import { fmtDur, isSponsorSpot } from './shared';
import type { QueueRow } from './QueuePanel';

/**
 * Why a queue row is in the queue: the playlist (and the schedule block that started it)
 * an automatic song was drawn from, or what else put it there (a sponsorship contract, a
 * program block, the break settings, a person, a request, a holiday list...).
 * Shmuel 2026-11-19: "what playlist is it playing from? I want to be able to click into
 * the playlist and open up the reason why the song is playing, exactly, from the queue".
 */
export type OriginKind =
  | 'playlist' | 'block-songs' | 'block-gone' | 'no-block' | 'station-id' | 'holiday'
  | 'sponsorship' | 'program-block' | 'time' | 'weather' | 'resume' | 'manual' | 'request'
  | 'loop' | 'shabbos' | 'silence' | 'other';

export interface OriginLink { label: string; to: string }

export interface Origin {
  kind: OriginKind;
  /** A few words for the "Playing from" column */
  short: string;
  /** One heading line for the Why panel */
  title: string;
  /** Plain sentences: what put it there */
  lines: string[];
  /** Where to change it */
  links: OriginLink[];
  /** The playlist steps this song could have come from (playlist rows) */
  steps?: { n: number; slot: TemplateSlot; match: boolean }[];
  schedule?: Schedule;
  block?: ScheduleBlock;
  playlist?: PlaylistTemplate;
}

const DAYS: Record<string, string> = {
  '0': 'Mon', '1': 'Tue', '2': 'Wed', '3': 'Thu', '4': 'Fri', '5': 'Sat', '6': 'Sun',
  monday: 'Mon', tuesday: 'Tue', wednesday: 'Wed', thursday: 'Thu', friday: 'Fri', saturday: 'Sat', sunday: 'Sun',
  mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun',
};

function clock(hhmm: string | undefined | null): string {
  if (!hhmm) return '';
  const [h, m] = hhmm.slice(0, 5).split(':').map(Number);
  if (Number.isNaN(h)) return hhmm;
  const ap = h >= 12 ? 'PM' : 'AM';
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m ? `${h12}:${String(m).padStart(2, '0')} ${ap}` : `${h12} ${ap}`;
}

function sun(ev: string | null | undefined, off: number | null | undefined): string {
  if (!ev) return '';
  if (!off) return ev;
  return `${Math.abs(off)} min ${off < 0 ? 'before' : 'after'} ${ev}`;
}

/** "6 AM to 10 PM, every day" in the words the Schedules page uses. */
export function blockWhen(b: ScheduleBlock): string {
  const from = b.start_sun_event ? sun(b.start_sun_event, b.start_sun_offset) : clock(b.start_time);
  const to = b.end_sun_event ? sun(b.end_sun_event, b.end_sun_offset) : clock(b.end_time);
  const span = b.until_next ? `from ${from} until the next playlist starts` : `${from} to ${to}`;
  const days = b.recurrence_type === 'weekly' && b.recurrence_pattern?.length
    ? b.recurrence_pattern.map((d) => DAYS[String(d).toLowerCase()] ?? String(d)).join(', ')
    : ({ daily: 'every day', weekly: 'weekly', monthly: 'monthly', one_time: 'one time' } as Record<string, string>)[b.recurrence_type] ?? b.recurrence_type;
  return `${span}, ${days}`;
}

const low = (s: string | null | undefined) => (s ?? '').trim().toLowerCase();

/** The steps of a playlist that draw this kind and song type (a step with no type draws any). */
export function matchingSteps(tpl: PlaylistTemplate, assetType: string | undefined, category: string | null | undefined) {
  return [...(Array.isArray(tpl.slots) ? tpl.slots : [])]
    .sort((a, b) => a.position - b.position)
    .map((slot, i) => ({
      n: i + 1,
      slot,
      match: low(slot.asset_type) === low(assetType) && (!slot.category || low(slot.category) === low(category)),
    }));
}

interface Lookup {
  blocks: Map<string, { block: ScheduleBlock; schedule: Schedule }>;
  playlists: Map<string, PlaylistTemplate>;
  loaded: boolean;
}

export function originOf(row: QueueRow, look: Lookup): Origin {
  const a = row.asset;
  const source = row.source ?? 'auto';
  const cat = low(a?.category);
  const type = a?.asset_type ?? '';
  const holidayNote = row.holiday
    ? `It is on the ${row.holiday} list (Holiday Music), which mixes in more of that list as the holiday comes closer.`
    : null;
  const holidayLink: OriginLink[] = row.holiday ? [{ label: `Open the ${row.holiday} list`, to: `/admin/holiday-music?holiday=${encodeURIComponent(row.holiday)}` }] : [];

  if (type === 'silence') {
    return { kind: 'silence', short: 'Off the air', title: 'Scheduled silence',
      lines: [row.blackout_name ? `The station is off the air for ${row.blackout_name}.` : 'The station is off the air at this time (Shabbos or Yom Tov).'],
      links: [{ label: 'Open Holidays (off-air times)', to: '/admin/holidays' }] };
  }
  if (source.startsWith('resume@')) {
    const at = Number(source.slice(7));
    return { kind: 'resume', short: 'Rest of paused item', title: 'The rest of an item a timed item paused',
      lines: [`A timed item (time check, sponsorship or weather) paused it ${Number.isFinite(at) ? `at ${fmtDur(at)}` : ''}. It carries on from there.`,
        'What a timed item does to a shiur or story on air (pause, wait or cut) is a station setting.'],
      links: [{ label: 'Open Stations (timed items during a shiur)', to: '/admin/stations' }] };
  }
  if (isSponsorSpot(source, a)) {
    return { kind: 'sponsorship', short: 'Sponsorship', title: 'A sponsorship play',
      lines: ['Planned by the sponsor’s contract into one of this station’s breaks.', 'Open the Sponsor panel for the sponsor, the contract and its plays.'],
      links: [{ label: 'Open All contracts', to: '/admin/spot-schedule' }] };
  }
  if (source === 'shabbos_end') {
    return { kind: 'shabbos', short: 'Before Shabbos', title: 'Last song before Shabbos',
      lines: ['Timed to end exactly at candle lighting, from the station’s “Last song before Shabbos” setting.'],
      links: [{ label: 'Open Stations (Last song before Shabbos)', to: '/admin/stations' }] };
  }
  if (source === 'block') {
    if (cat === 'time' || cat === 'weather') {
      return { kind: cat === 'time' ? 'time' : 'weather', short: cat === 'time' ? 'Time check' : 'Weather', title: `Part of a program block (${cat === 'time' ? 'time check' : 'weather'})`,
        lines: ['A program block places it at its clock time, together with the other items of that block.'],
        links: [{ label: 'Open Program Blocks', to: '/admin/program-blocks' }] };
    }
    return { kind: 'program-block', short: 'Program block', title: 'Part of a program block',
      lines: ['A program block (a named run of items at set clock times) placed it here.'],
      links: [{ label: 'Open Program Blocks', to: '/admin/program-blocks' }] };
  }
  if (cat === 'time' || cat === 'zmanim' || cat === 'weather') {
    const isTime = cat !== 'weather';
    return { kind: isTime ? 'time' : 'weather', short: isTime ? 'Time check' : 'Weather',
      title: isTime ? 'The time check on the hour (or half hour)' : 'The weather in the break',
      lines: ['Queued for this station’s :00 / :30 break by the break settings.'],
      links: [{ label: 'Open the break settings', to: '/admin/spot-schedule' }] };
  }
  if (source === 'manual' || source === 'staff') {
    return { kind: 'manual', short: 'Added by hand', title: 'Added by a person',
      lines: ['Someone put it in the queue by hand (control room, Library or a cart button). Automatic redraws leave it where it is.'],
      links: [{ label: 'Open Activity (who did what)', to: '/admin/activity' }] };
  }
  if (source === 'request') {
    return { kind: 'request', short: 'Song request', title: 'A listener asked for it',
      lines: ['A song request that was approved and queued.'],
      links: [{ label: 'Open Song Requests', to: '/admin/requests' }] };
  }
  if (source === 'loop') {
    return { kind: 'loop', short: 'Loop', title: 'Loop is on',
      lines: ['The operator turned Loop on: this item repeats until Loop is turned off or Next is pressed.'], links: [] };
  }
  if (cat.startsWith('station_id')) {
    return { kind: 'station-id', short: 'Station ID', title: 'A station ID between songs',
      lines: ['Sprinkled between songs by the station’s “Station IDs between songs” setting.'],
      links: [{ label: 'Open Stations (Station IDs between songs)', to: '/admin/stations' }] };
  }

  // An automatic song: the schedule block it was drawn for, and that block's playlist
  const hit = row.block_id ? look.blocks.get(row.block_id) : undefined;
  if (hit) {
    const { block, schedule } = hit;
    const tpl = block.playlist_template_id ? look.playlists.get(String(block.playlist_template_id)) : undefined;
    const blockLink: OriginLink = { label: `Open the block “${block.name}”`, to: `/admin/schedules/${schedule.id}/blocks?block=${block.id}` };
    const where = `Schedule “${schedule.name}”, block “${block.name}” (${blockWhen(block)}).`;
    if (tpl) {
      const steps = matchingSteps(tpl, type, a?.category);
      const hits = steps.filter((s) => s.match);
      const songType = a?.category ? `song type “${a.category}”` : `kind “${type}”`;
      return {
        kind: row.holiday ? 'holiday' : 'playlist',
        short: row.holiday ? `${tpl.name} · ${row.holiday}` : tpl.name,
        title: `Playlist “${tpl.name}”`,
        lines: [
          where,
          row.holiday
            ? holidayNote as string
            : hits.length
              ? `Drawn for step ${hits.map((s) => s.n).join(' or ')} of ${steps.length}, which asks for ${songType}; the longest-rested song of that type goes first.`
              : `It is ${songType}, which no step of this playlist asks for now (the playlist may have changed after it was drawn).`,
        ],
        links: [
          { label: `Open the playlist “${tpl.name}”`, to: `/admin/playlists?open=${tpl.id}${hits[0] ? `&step=${hits[0].slot.id}` : ''}` },
          blockLink,
          ...holidayLink,
        ],
        steps, schedule, block, playlist: tpl,
      };
    }
    return { kind: 'block-songs', short: block.name, title: `Block “${block.name}”`,
      lines: [where, holidayNote ?? 'Drawn from the songs listed in this block (it has no playlist).'],
      links: [blockLink, ...holidayLink], schedule, block };
  }
  if (row.holiday) {
    return { kind: 'holiday', short: `${row.holiday} list`, title: `The ${row.holiday} list`, lines: [holidayNote as string], links: holidayLink };
  }
  if (row.block_id) {
    return { kind: 'block-gone', short: look.loaded ? 'Removed block' : '…', title: 'A schedule block that is no longer there',
      lines: look.loaded ? ['Drawn for a schedule block that has since been deleted or moved to another schedule. The next redraw replaces it.'] : ['Loading the schedule…'],
      links: [{ label: 'Open Schedules', to: '/admin/schedules' }] };
  }
  if (source === 'auto' || source === 'scheduler' || source === 'fallback') {
    return { kind: 'no-block', short: 'Station rules', title: 'Not from a playlist',
      lines: ['No schedule block with a playlist was on at this time, so the station’s automatic fill picked it: the station’s rules, or any music when there are none.'],
      links: [{ label: 'Open Rules', to: '/admin/rules' }, { label: 'Open Schedules', to: '/admin/schedules' }] };
  }
  return { kind: 'other', short: source, title: 'Queued by the system', lines: [`Source: ${source}.`], links: [] };
}

/** Looks up why each queue row is there for one station (schedules and playlists, read once a minute). */
export function useQueueOrigins(stationId: string | null | undefined) {
  const { data: schedules, isSuccess: sOk } = useSchedules(stationId ?? undefined);
  const { data: templates, isSuccess: tOk } = usePlaylistTemplates();
  const look = useMemo<Lookup>(() => {
    const blocks = new Map<string, { block: ScheduleBlock; schedule: Schedule }>();
    for (const s of Array.isArray(schedules) ? schedules : []) for (const b of Array.isArray(s.blocks) ? s.blocks : []) blocks.set(String(b.id), { block: b, schedule: s });
    const playlists = new Map<string, PlaylistTemplate>();
    for (const t of Array.isArray(templates) ? templates : []) playlists.set(String(t.id), t);
    return { blocks, playlists, loaded: sOk && tOk };
  }, [schedules, templates, sOk, tOk]);
  return useCallback((row: QueueRow) => originOf(row, look), [look]);
}
