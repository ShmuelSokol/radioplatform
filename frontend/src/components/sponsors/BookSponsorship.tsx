import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getClientAccount, listSponsors } from '../../api/sponsors';
import { listAssets } from '../../api/assets';
import { DEFAULT_SLOTS, TIME_SLOTS, createSpotSchedule, type Daypart, type SpotScheduleInput, type TimingMode } from '../../api/spotSchedules';
import { fullestDay, fullness, getSlotRoom, type Fullness } from '../../api/sponsorRoom';
import { useStations } from '../../hooks/useStations';
import { useEscape } from '../../hooks/useEscape';
import { stationToShow } from '../../lib/lastStation';
import Spinner from '../Spinner';
import { contractTotals, dayList, length, runText } from './contractTotals';
import { RdsBadge, useSponsorRds } from './SponsorRds';

/**
 * Book a sponsorship: one window, four steps (spot, stations and days, time slots,
 * check and save). Opened from the client page, the Sponsorship board and the
 * Sponsors menu. On the time slots step each slot shows how full it already is
 * (GET /spot-schedules/room), so a slot with room is picked before saving rather
 * than found full afterwards. Saves with POST /spot-schedules, which plans the plays.
 */

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const STEPS = ['Spot', 'Stations and days', 'Time slots', 'Check and save'];

function toggle<T>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
}

const chip = (on: boolean) =>
  `min-h-[44px] px-3 rounded-lg border text-sm text-left ${on ? 'bg-blue-600 text-white border-blue-600' : 'bg-white'}`;
const field = 'w-full min-w-0 px-3 py-2 border rounded-lg';

export const FULLNESS_TEXT: Record<Fullness, string> = {
  empty: 'Empty',
  room: 'Room',
  nearly: 'Nearly full',
  full: 'Full',
  off: 'No room',
};

/** A sunken bar filled by how full a slot is (green, then yellow, then red). */
export function RoomBar({ booked, room, state }: { booked: number; room: number; state: Fullness }) {
  const pct = room > 0 ? Math.min(100, Math.round((booked / room) * 100)) : state === 'full' ? 100 : 0;
  const colour = state === 'full' ? 'bg-red-600' : state === 'nearly' ? 'bg-yellow-500' : 'bg-green-600';
  return (
    <div className="h-2.5 w-full bg-white border border-gray-400" aria-hidden="true">
      <div className={`h-full ${colour}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export interface BookingStart {
  sponsorId?: string;
  assetId?: string; // the spot to book (from Record Spots)
  stationId?: string;
  day?: number; // Monday = 0
  slot?: string;
}

export default function BookSponsorship({ start, onClose }: { start?: BookingStart; onClose: () => void }) {
  const queryClient = useQueryClient();
  const close = useCallback(() => onClose(), [onClose]);
  useEscape(true, close);

  const { data: stationData } = useStations();
  const stations = useMemo(() => stationData?.stations ?? [], [stationData]);
  const { data: sponsors } = useQuery({ queryKey: ['sponsors'], queryFn: listSponsors, enabled: !start?.sponsorId });

  const rds = useSponsorRds();
  const [step, setStep] = useState(0);
  const [sponsorId, setSponsorId] = useState(start?.sponsorId ?? '');
  const [assetId, setAssetId] = useState('');
  const [assetTitle, setAssetTitle] = useState('');
  const [assetSeconds, setAssetSeconds] = useState<number | null>(null);
  const [search, setSearch] = useState('');
  const [stationId, setStationId] = useState(start?.stationId ?? '');
  const [days, setDays] = useState<number[]>(() => {
    const usual = [0, 1, 2, 3, 4, 6]; // Sunday to Friday
    // Opened from a box on the board: the usual week, with that box's day in it
    return start?.day != null && !usual.includes(start.day) ? [...usual, start.day] : usual;
  });
  const [slots, setSlots] = useState<Daypart[]>(start?.slot ? [start.slot as Daypart] : DEFAULT_SLOTS.slice(0, 2));
  const [perSlot, setPerSlot] = useState(1);
  const [firstDay, setFirstDay] = useState('');
  const [lastDay, setLastDay] = useState('');
  const [name, setName] = useState('');
  const [timing, setTiming] = useState<'' | TimingMode>('');
  const [error, setError] = useState('');
  const [saved, setSaved] = useState<string[] | null>(null);

  const { data: account } = useQuery({
    queryKey: ['sponsor-account', sponsorId],
    queryFn: () => getClientAccount(sponsorId),
    enabled: !!sponsorId,
  });
  const clientName = account?.client.name ?? sponsors?.find((s) => s.id === sponsorId)?.name ?? '';
  // The client's own audio that can air: never a rejected copy or a stand-in voice
  const spots = (account?.spots ?? []).filter((s) => s.asset_id && s.stage !== 'rejected' && !/stand-?in/i.test(s.title));
  // Opened for one spot (Record Spots): choose it once the client's spots are loaded
  const wanted = start?.assetId ? spots.find((s) => s.asset_id === start.assetId) : undefined;
  useEffect(() => {
    if (wanted && !assetId) { setAssetId(wanted.asset_id!); setAssetTitle(wanted.title); setAssetSeconds(wanted.duration ?? null); }
    // only when the spot first becomes known
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted?.asset_id]);
  const inWorks = (account?.spots ?? []).filter((s) => !s.asset_id && s.kind === 'recording');
  const { data: found, isFetching: searching } = useQuery({
    queryKey: ['spot-search', search],
    queryFn: () => listAssets({ search, limit: 10 }),
    enabled: search.trim().length >= 2,
  });

  // Room is shown for the chosen station, or for one station when the booking is for all
  const roomStation = stationId || stationToShow(stations);
  const roomStationName = stations.find((s) => String(s.id) === String(roomStation))?.name ?? '';
  const { data: room, isLoading: roomLoading } = useQuery({
    queryKey: ['slot-room', roomStation],
    queryFn: () => getSlotRoom(roomStation, 7),
    enabled: !!roomStation && step >= 2,
  });

  const shape = {
    days_of_week: [...days].sort().join(','),
    dayparts: slots.join(','),
    plays_per_daypart: perSlot,
    fixed_times: null,
    start_date: firstDay || null,
    end_date: lastDay || null,
  };
  const totals = contractTotals(shape);
  const seconds = assetSeconds;
  const stationText = stationId ? stations.find((s) => String(s.id) === String(stationId))?.name ?? 'one station' : 'every station';
  const slotText = TIME_SLOTS.filter((s) => slots.includes(s.key)).map((s) => s.hours.replace(' to ', '-')).join(', ');

  // Chosen slots with no sponsorship place on any chosen day in the coming week
  const noRoomSlots = room ? TIME_SLOTS.filter((s) => slots.includes(s.key) && !fullestDay(room, s.key, days)) : [];

  const save = useMutation({
    mutationFn: (data: SpotScheduleInput) => createSpotSchedule(data),
    onSuccess: (s) => {
      for (const key of ['sponsor-account', 'sponsor-clients', 'spot-schedules', 'spot-plan', 'slot-room']) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
      setSaved(s.warnings ?? []);
    },
    onError: (e: unknown) => {
      const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setError(typeof detail === 'string' ? `Could not save: ${detail}` : 'Could not save. Check the steps and try again.');
    },
  });

  const problem = (s: number): string => {
    if (s === 0 && !sponsorId) return 'Choose the client.';
    if (s === 0 && !assetId) return 'Choose the spot that will air.';
    if (s === 1 && !days.length) return 'Choose at least one day.';
    if (s === 2 && !slots.length) return 'Choose at least one time slot.';
    if (s === 3 && firstDay && lastDay && firstDay > lastDay) return 'The last day is before the first day.';
    return '';
  };
  const next = () => {
    const p = problem(step);
    setError(p);
    if (!p) setStep(step + 1);
  };
  const submit = () => {
    const p = [0, 1, 2, 3].map(problem).find(Boolean);
    if (p) return setError(p);
    setError('');
    save.mutate({
      sponsor_id: sponsorId,
      asset_id: assetId,
      station_id: stationId || null,
      name: name.trim() || null,
      days_of_week: shape.days_of_week,
      dayparts: shape.dayparts,
      plays_per_daypart: perSlot,
      start_date: firstDay || null,
      end_date: lastDay || null,
      is_active: true,
      fixed_times: null,
      timing_mode: timing || null,
    });
  };

  const pickSpot = (id: string, title: string, duration?: number | null) => { setAssetId(id); setAssetTitle(title); setAssetSeconds(duration ?? null); setError(''); };

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto p-2 sm:p-6" onMouseDown={(e) => { if (e.target === e.currentTarget && (saved || (step === 0 && !assetId))) onClose(); }}>
      <div role="dialog" aria-label="Book a sponsorship" data-testid="book-sponsorship" className="bg-white rounded-xl shadow-xl w-full max-w-3xl p-4 sm:p-6 space-y-4 my-auto">
        <div className="flex items-start justify-between gap-3">
          <h2 className="text-xl font-semibold break-words">Book a sponsorship{clientName ? `: ${clientName}` : ''}</h2>
          <button type="button" onClick={onClose} className="min-h-[44px] px-3 border rounded-lg text-sm">Close</button>
        </div>

        {saved ? (
          <div className="space-y-3" data-testid="booking-saved">
            <p className="text-green-700 font-medium" role="status">Booked. The plays are planned.</p>
            {saved.length > 0 && (
              <div className="bg-yellow-50 border border-yellow-300 rounded-lg p-3 text-sm">
                <p className="font-medium mb-1">Not everything fits in the coming days:</p>
                <ul className="list-disc ml-5">{saved.map((w) => <li key={w}>{w}</li>)}</ul>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              <Link to={`/admin/sponsors/${sponsorId}?tab=contracts`} onClick={onClose} className="min-h-[44px] px-4 py-2 bg-blue-600 text-white rounded-lg">Open {clientName || 'the client'}</Link>
              {!rds.isLoading && !rds.isError && !rds.isSet(sponsorId) && (
                <Link to={`/admin/sponsors/${sponsorId}?tab=rds`} onClick={onClose} className="min-h-[44px] px-4 py-2 border rounded-lg">Add radio text (RDS)</Link>
              )}
              <button type="button" onClick={onClose} className="min-h-[44px] px-4 border rounded-lg">Done</button>
            </div>
          </div>
        ) : (
          <>
            <ol className="flex flex-wrap gap-1" aria-label="Steps">
              {STEPS.map((label, i) => (
                <li key={label}>
                  <button
                    type="button"
                    aria-current={i === step ? 'step' : undefined}
                    aria-pressed={i === step}
                    disabled={i > step}
                    onClick={() => i < step && setStep(i)}
                    className={`min-h-[40px] px-3 border rounded-lg text-sm ${i === step ? 'bg-blue-600 text-white font-semibold' : 'bg-white'} disabled:opacity-60`}
                  >
                    {i + 1}.<span className={`ml-1 ${i === step ? '' : 'hidden sm:inline'}`}>{label}</span>{i < step ? ' ✓' : ''}
                  </button>
                </li>
              ))}
            </ol>

            {step === 0 && (
              <div className="space-y-3" data-testid="step-spot">
                {!start?.sponsorId && (
                  <label className="block text-sm">Client
                    <select className={field} value={sponsorId} onChange={(e) => { setSponsorId(e.target.value); setAssetId(''); }} aria-label="Client">
                      <option value="">Choose a client</option>
                      {sponsors?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                    </select>
                  </label>
                )}
                {sponsorId && (
                  <>
                    <p className="text-sm font-medium">Which spot airs?</p>
                    {spots.length ? (
                      <div className="grid gap-2">
                        {spots.map((s) => (
                          <button key={s.key} type="button" aria-pressed={assetId === s.asset_id} className={chip(assetId === s.asset_id)} onClick={() => pickSpot(s.asset_id!, s.title, s.duration)}>
                            <span className="font-medium">{s.title}</span>
                            <span className="ml-2 text-xs opacity-80">{[length(s.duration), s.stage_label].filter(Boolean).join(' · ')}</span>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-gray-600">{clientName || 'This client'} has no spot ready to air yet.</p>
                    )}
                    {inWorks.length > 0 && (
                      <p className="text-xs text-gray-600">Still being made: {inWorks.map((s) => s.title).join(', ')}. They can be chosen once approved.</p>
                    )}
                    <details className="text-sm" open={!spots.length}>
                      <summary className="cursor-pointer">Use a recording from the library</summary>
                      <input value={search} onChange={(e) => setSearch(e.target.value)} className={`${field} mt-2`} placeholder="Type part of its title" aria-label="Search the library" />
                      {searching && <p className="text-xs text-gray-500 mt-1">Searching...</p>}
                      {!!found?.assets?.length && (
                        <div className="grid gap-1 mt-2">
                          {found.assets.map((a) => (
                            <button key={a.id} type="button" aria-pressed={assetId === a.id} className={chip(assetId === a.id)} onClick={() => pickSpot(a.id, a.title, a.duration)}>
                              {a.title}{a.duration ? <span className="ml-2 text-xs opacity-80">{length(a.duration)}</span> : null}
                            </button>
                          ))}
                        </div>
                      )}
                    </details>
                    <Link to={`/admin/spot-recordings?sponsor=${sponsorId}`} onClick={onClose} className="inline-block text-sm text-blue-700 underline">Write and record a new spot instead</Link>
                  </>
                )}
              </div>
            )}

            {step === 1 && (
              <div className="space-y-4" data-testid="step-days">
                <label className="block text-sm">Station
                  <select className={field} value={stationId} onChange={(e) => setStationId(e.target.value)} aria-label="Station">
                    <option value="">Every station</option>
                    {stations.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                  </select>
                </label>
                <fieldset>
                  <legend className="text-sm font-medium mb-1">Days</legend>
                  <div className="flex flex-wrap gap-2">
                    {DAYS.map((d, i) => (
                      <button key={d} type="button" aria-pressed={days.includes(i)} className={chip(days.includes(i))} onClick={() => setDays(toggle(days, i))}>{d}</button>
                    ))}
                  </div>
                  <p className="text-xs text-gray-500 mt-1">Nothing airs during Shabbos or Yom Tov, whatever days are chosen.</p>
                </fieldset>
                <div className="grid grid-cols-2 gap-3">
                  <label className="block text-sm">First day
                    <input type="date" className={field} value={firstDay} onChange={(e) => setFirstDay(e.target.value)} />
                  </label>
                  <label className="block text-sm">Last day
                    <input type="date" className={field} value={lastDay} onChange={(e) => setLastDay(e.target.value)} />
                  </label>
                </div>
                <p className="text-xs text-gray-500 -mt-2">Leave the first day empty to start today, and the last day empty for no end.</p>
              </div>
            )}

            {step === 2 && (
              <div className="space-y-3" data-testid="step-slots">
                <p className="text-sm text-gray-700">
                  Tap the slots it should air in. Plays go anywhere in a slot, to the break with the fewest sponsorships.
                  {roomStationName && <> The bars show how full each slot already is on {roomStationName}, on its fullest day this week.</>}
                </p>
                {roomLoading && <Spinner />}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  {TIME_SLOTS.map((s) => {
                    const cell = fullestDay(room, s.key, days);
                    const state: Fullness = cell ? fullness(cell) : 'off';
                    const on = slots.includes(s.key);
                    return (
                      <button key={s.key} type="button" aria-pressed={on} data-slot={s.key} className={`${chip(on)} py-2 flex flex-col items-stretch gap-1`} onClick={() => setSlots(toggle(slots, s.key))}>
                        <span className="block font-semibold">{s.hours}{on ? ' ✓' : ''}</span>
                        {cell ? (
                          <>
                            <RoomBar booked={cell.booked + cell.no_room} room={cell.room} state={state} />
                            <span className="block text-xs" data-testid="slot-room">
                              {cell.room > 0 ? `${cell.booked} of ${cell.room} places taken` : `${cell.booked} booked`}
                              {state === 'nearly' || state === 'full' ? ` · ${FULLNESS_TEXT[state].toLowerCase()}` : ''}
                            </span>
                          </>
                        ) : (
                          <span className="block text-xs opacity-80">{room ? 'No sponsorship room on these days' : ' '}</span>
                        )}
                      </button>
                    );
                  })}
                </div>
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <label className="flex items-center gap-2">Plays in each slot
                    <select aria-label="Plays in each slot" className="px-2 py-1 border rounded-lg" value={perSlot} onChange={(e) => setPerSlot(Number(e.target.value))}>
                      {Array.from({ length: 6 }, (_, i) => i + 1).map((n) => <option key={n} value={n}>{n}</option>)}
                    </select>
                  </label>
                  <span className="text-gray-700" data-testid="booking-per-day">About {totals.perDay} {totals.perDay === 1 ? 'play' : 'plays'} a day, {dayList(totals.days)}</span>
                </div>
              </div>
            )}

            {step === 3 && (
              <div className="space-y-3" data-testid="step-check">
                <div className="bg-yellow-50 border border-gray-800 p-3 text-sm leading-relaxed" data-testid="booking-sentence">
                  <p className="font-semibold">{clientName}{name.trim() ? ` · ${name.trim()}` : ''}</p>
                  <p>
                    Airs about <b>{totals.perDay} {totals.perDay === 1 ? 'time' : 'times'} a day</b> on <b>{stationText}</b>, {dayList(totals.days)},{' '}
                    {perSlot === 1 ? 'once' : `${perSlot} times`} in each of these slots: {slotText}. {runText(shape, totals)}.
                  </p>
                  <p>Spot: {assetTitle}{seconds ? ` (${length(seconds)})` : ''}.</p>
                  {!rds.isLoading && !rds.isError && (
                    <p className="mt-1" data-testid="booking-rds">
                      <RdsBadge set={rds.isSet(sponsorId)} />{' '}
                      {rds.isSet(sponsorId)
                        ? `Radios show: ${rds.lines(sponsorId).find((m) => m.is_active)?.text ?? ''}`
                        : 'Radios will show the spot\'s title while it airs. Add a radio text on the client page after saving.'}
                    </p>
                  )}
                </div>
                {TIME_SLOTS.filter((s) => slots.includes(s.key)).some((s) => {
                  const c = fullestDay(room, s.key, days);
                  return c && ['nearly', 'full'].includes(fullness(c));
                }) && (
                  <p className="text-sm text-yellow-800" data-testid="booking-nearly-full">Some of these slots are full or nearly full. A play with no room moves to the next break with room, and anything left over shows under Missed plays.</p>
                )}
                {noRoomSlots.length > 0 && (
                  <p className="text-sm text-red-700" data-testid="booking-no-room">
                    {roomStationName} has no sponsorship places on these days in: {noRoomSlots.map((s) => s.hours.replace(' to ', '-')).join(', ')}. Plays booked there will not air until places are added (Program Blocks). Go back to choose another slot, or save anyway.
                  </p>
                )}
                <label className="block text-sm">Name for this contract (optional)
                  <input className={field} value={name} onChange={(e) => setName(e.target.value)} placeholder="For example: Winter sale" />
                </label>
                <details className="text-sm">
                  <summary className="cursor-pointer">More choices</summary>
                  <label className="block mt-2">How it keeps its time
                    <select className={field} value={timing} onChange={(e) => setTiming(e.target.value as '' | TimingMode)} aria-label="How it keeps its time">
                      <option value="">The station's usual choice for that break</option>
                      <option value="hard">On the second (the song fades out)</option>
                      <option value="soft">After the song finishes</option>
                    </select>
                  </label>
                  <p className="text-xs text-gray-500 mt-1">To play at exact clock times instead of slots, save it and use Edit on the contract.</p>
                </details>
              </div>
            )}

            {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
            <div className="flex flex-wrap justify-between gap-2">
              <button type="button" className="min-h-[44px] px-4 border rounded-lg" onClick={() => (step ? setStep(step - 1) : onClose())}>
                {step ? '‹ Back' : 'Cancel'}
              </button>
              {step < 3 ? (
                <button type="button" className="min-h-[44px] px-4 bg-blue-600 text-white rounded-lg" onClick={next}>Next ›</button>
              ) : (
                <button type="button" disabled={save.isPending} className="min-h-[44px] px-4 bg-green-600 text-white rounded-lg disabled:opacity-50" onClick={submit}>
                  {save.isPending ? <Spinner /> : 'Save and plan the plays'}
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
