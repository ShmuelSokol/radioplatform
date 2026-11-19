import { totalsLine } from '../../components/sponsors/contractTotals';
import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { rememberStation, stationToShow } from '../../lib/lastStation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { listSponsors } from '../../api/sponsors';
import { getAssetAudioUrl, listAssets } from '../../api/assets';
import {
  createSpotSchedule,
  TimingMode,
  deleteSpotSchedule,
  listSpotSchedules,
  previewSpotPlan,
  updateSpotSchedule,
  Daypart,
  DEFAULT_SLOTS,
  TIME_SLOTS,
  WIDE_DAYPARTS,
  daypartText,
  PlannedSpot,
  SpotSchedule,
} from '../../api/spotSchedules';
import { useStations } from '../../hooks/useStations';
import Spinner from '../../components/Spinner';
import ContractEditor from '../../components/sponsors/ContractEditor';
import BreakSettings from '../../components/sponsors/BreakSettings';
import BookSponsorship from '../../components/sponsors/BookSponsorship';
import type { Asset } from '../../types';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const emptyForm = {
  sponsor_id: '',
  station_id: '',
  days: [0, 1, 2, 3, 4, 5, 6] as number[],
  dayparts: [...DEFAULT_SLOTS] as Daypart[],
  plays_per_daypart: 1,
  when: 'spread' as 'spread' | 'exact',
  times: [] as string[], // "HH:MM" station time
  newTime: '',
  timing: '' as '' | TimingMode, // '' = the station's default for that slot
  start_date: '',
  end_date: '',
};

function toggle<T>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
}

function formatTime(local: string): string {
  // "YYYY-MM-DD HH:MM" (station time) → "6:15 PM"
  const [h, m] = local.slice(11).split(':').map(Number);
  const suffix = h >= 12 ? 'PM' : 'AM';
  return `${h % 12 === 0 ? 12 : h % 12}:${String(m).padStart(2, '0')} ${suffix}`;
}

function clock(hhmm: string): string {
  return formatTime(`0000-00-00 ${hhmm}`);
}

const TIMING_LABEL: Record<TimingMode, string> = {
  hard: 'On the second (song fades out)',
  soft: 'After the song finishes',
};

function formatDay(isoDate: string): string {
  const [y, mo, d] = isoDate.split('-').map(Number);
  return new Date(y, mo - 1, d).toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
  });
}

function describe(s: SpotSchedule): string {
  const days = s.days_of_week.split(',').map(Number);
  const dayText = days.length === 7 ? 'Every day' : days.map((d) => DAYS[d]).join(', ');
  const parts = s.dayparts
    .split(',')
    .map((p) => daypartText(p))
    .join(', ');
  const how = s.timing_mode ? `, ${TIMING_LABEL[s.timing_mode].toLowerCase()}` : '';
  if (s.fixed_times) return `${dayText} at ${s.fixed_times.split(',').map(clock).join(', ')}${how}`;
  const times = s.plays_per_daypart === 1 ? 'once' : `${s.plays_per_daypart} times`;
  return `${dayText}, ${times} each ${parts}${how}`;
}

function formatWhen(iso: string, timeZone: string) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: 'short',
      hour: 'numeric',
      minute: '2-digit',
      timeZone,
    });
  } catch {
    return new Date(iso).toLocaleString();
  }
}

export default function SpotSchedules() {
  const queryClient = useQueryClient();
  const { data: stationData } = useStations();
  const stations = stationData?.stations ?? [];
  const { data: sponsors } = useQuery({ queryKey: ['sponsors'], queryFn: listSponsors });
  const { data: schedules, isLoading } = useQuery({
    queryKey: ['spot-schedules'],
    queryFn: listSpotSchedules,
  });

  const [error, setError] = useState('');
  // "New contract" on a client's page opens the form with that client chosen
  const [params] = useSearchParams();
  const forSponsor = params.get('sponsor') || '';
  const [form, setFormState] = useState(forSponsor ? { ...emptyForm, sponsor_id: forSponsor } : emptyForm);
  // A message about the form goes away as soon as the form is changed.
  const setForm = (next: typeof emptyForm) => {
    setFormState(next);
    setError('');
  };
  const [showForm, setShowForm] = useState(false);
  // Opened for one client (older links): the booking window, for that client
  const [booking, setBooking] = useState(!!forSponsor);
  const [spotSearch, setSpotSearch] = useState('');
  const [spot, setSpot] = useState<Asset | null>(null);
  // Opened from the control room's Sponsor panel: ?station= shows that station's
  // look-ahead, ?contract= points at that contract in the list
  const linkedContract = params.get('contract') || '';
  const [previewStation, setPreviewStation] = useState(params.get('station') || '');
  const [playing, setPlaying] = useState<{ id: string; url: string } | null>(null);
  const [editing, setEditing] = useState<SpotSchedule | null>(null);

  // The look-ahead opens on a station that has sponsorships: opening on the first
  // station (Main) showed "none planned" while 180 plays were queued on two others.
  const scheduledStation = (schedules ?? []).find((x) => x.is_active && x.station_id)?.station_id;
  const withSchedules = stations.find((st) => String(st.id) === String(scheduledStation))?.id;
  // ...unless a station was being worked on in this browser: then that one
  const stationId = stationToShow(stations, previewStation, withSchedules);

  const { data: spotResults, isFetching: searching } = useQuery({
    queryKey: ['spot-search', spotSearch],
    queryFn: () => listAssets({ search: spotSearch, limit: 15 }),
    enabled: showForm && spotSearch.trim().length >= 2,
  });

  const { data: plan, isLoading: planLoading } = useQuery({
    queryKey: ['spot-plan', stationId],
    queryFn: () => previewSpotPlan(stationId, 7),
    enabled: !!stationId,
  });

  // What the last save could not fit, said at once
  const [saveWarnings, setSaveWarnings] = useState<string[]>([]);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['spot-schedules'] });
    queryClient.invalidateQueries({ queryKey: ['spot-plan'] });
  };

  const createMut = useMutation({
    mutationFn: createSpotSchedule,
    onSuccess: (saved) => {
      refresh();
      setSaveWarnings(saved.warnings ?? []);
      setShowForm(false);
      setForm(emptyForm);
      setSpot(null);
      setSpotSearch('');
      setError('');
    },
    onError: () => setError('Could not save the schedule. Check the fields and try again.'),
  });
  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<SpotSchedule> }) =>
      updateSpotSchedule(id, data),
    onSuccess: (saved) => {
      refresh();
      setSaveWarnings(saved.warnings ?? []);
    },
  });
  const deleteMut = useMutation({ mutationFn: deleteSpotSchedule, onSuccess: refresh });

  useEffect(() => {
    if (!linkedContract || !schedules?.length) return;
    document.getElementById(`contract-${linkedContract}`)?.scrollIntoView({ block: 'center' });
  }, [linkedContract, schedules]);

  const sponsorName = (id: string) => sponsors?.find((s) => s.id === id)?.name ?? 'Unknown sponsor';
  const stationName = (id: string | null) =>
    id ? stations.find((s) => s.id === id)?.name ?? 'Unknown station' : 'All stations';

  const listen = async (assetId: string) => {
    if (playing?.id === assetId) return setPlaying(null);
    try {
      setPlaying({ id: assetId, url: await getAssetAudioUrl(assetId) });
    } catch {
      setPlaying(null);
    }
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.sponsor_id || !spot) return setError('Choose a sponsor and a spot.');
    const exact = form.when === 'exact';
    if (!form.days.length) return setError('Choose at least one day.');
    // A time still sitting in the box (not yet added with the button) counts too.
    const times = [...new Set([...form.times, ...(form.newTime ? [form.newTime] : [])])].sort();
    if (exact && !times.length) return setError('Add at least one exact time.');
    if (!exact && !form.dayparts.length) return setError('Choose at least one time of day.');
    createMut.mutate({
      sponsor_id: form.sponsor_id,
      asset_id: spot.id,
      station_id: form.station_id || null,
      name: null,
      days_of_week: [...form.days].sort().join(','),
      dayparts: form.dayparts.join(','),
      plays_per_daypart: form.plays_per_daypart,
      fixed_times: exact ? times.join(',') : null,
      timing_mode: form.timing || null,
      start_date: form.start_date || null,
      end_date: form.end_date || null,
      is_active: true,
    });
  };

  const unplacedPlays = (plan?.unplaced ?? []).reduce((n, u) => n + (u.count ?? 1), 0);
  const byDay = useMemo(() => {
    const groups = new Map<string, PlannedSpot[]>();
    for (const p of plan?.planned ?? []) {
      const day = p.local_time.slice(0, 10);
      groups.set(day, [...(groups.get(day) ?? []), p]);
    }
    return [...groups.entries()];
  }, [plan]);

  if (isLoading) return <div className="p-8">Loading...</div>;

  return (
    <div className="w-full space-y-8">
      <div className="flex flex-wrap justify-between items-start gap-4">
        <div>
          <h1 className="text-3xl font-bold">All contracts</h1>
          <p className="text-gray-600 mt-1">
            Every client's contracts in one list: which spot, when it airs and until when. The
            next 7 days on one station are below.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button onClick={() => setBooking(true)} className="px-4 py-2 bg-blue-600 text-white rounded-lg whitespace-nowrap" data-testid="book-open">
            Book a sponsorship
          </button>
          <button onClick={() => setShowForm((v) => !v)} className="px-3 py-2 border rounded-lg text-sm whitespace-nowrap">
            {showForm ? 'Cancel' : 'Book at exact clock times'}
          </button>
          <Link to="/admin/sponsorship-board" className="px-3 py-2 border rounded-lg text-sm whitespace-nowrap">Sponsorship board</Link>
        </div>
      </div>
      {booking && <BookSponsorship start={{ sponsorId: params.get('sponsor') || undefined }} onClose={() => setBooking(false)} />}

      {showForm && (
        <form onSubmit={submit} className="bg-white rounded-xl shadow p-6 space-y-5">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Sponsor</label>
              <select
                value={form.sponsor_id}
                onChange={(e) => setForm({ ...form, sponsor_id: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg"
              >
                <option value="">Choose a sponsor</option>
                {sponsors?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Station</label>
              <select
                value={form.station_id}
                onChange={(e) => setForm({ ...form, station_id: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg"
              >
                <option value="">All stations</option>
                {stations.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Spot (from the library)</label>
            {spot ? (
              <div className="flex items-center gap-3 border rounded-lg px-3 py-2">
                <span className="flex-1">
                  {spot.title}
                  {spot.duration ? ` (${Math.round(spot.duration)}s)` : ''}
                </span>
                <button type="button" onClick={() => listen(spot.id)} className="text-blue-600 text-sm">
                  {playing?.id === spot.id ? 'Stop' : 'Listen'}
                </button>
                <button type="button" onClick={() => setSpot(null)} className="text-gray-500 text-sm">
                  Change
                </button>
              </div>
            ) : (
              <>
                <input
                  value={spotSearch}
                  onChange={(e) => setSpotSearch(e.target.value)}
                  className="w-full px-3 py-2 border rounded-lg"
                  placeholder="Type part of the spot's title"
                />
                {searching && <p className="text-sm text-gray-500 mt-1">Searching...</p>}
                {!!spotResults?.assets?.length && (
                  <ul className="border rounded-lg mt-1 divide-y max-h-56 overflow-y-auto">
                    {spotResults.assets.map((a) => (
                      <li key={a.id} className="flex items-center gap-3 px-3 py-2 hover:bg-gray-50">
                        <button type="button" onClick={() => setSpot(a)} className="flex-1 text-left">
                          {a.title}
                          <span className="text-gray-500 text-sm">
                            {' '}
                            {a.asset_type}
                            {a.duration ? `, ${Math.round(a.duration)}s` : ''}
                          </span>
                        </button>
                        <button type="button" onClick={() => listen(a.id)} className="text-blue-600 text-sm">
                          {playing?.id === a.id ? 'Stop' : 'Listen'}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                {spotSearch.trim().length >= 2 && !searching && spotResults?.assets?.length === 0 && (
                  <p className="text-sm text-gray-500 mt-1">Nothing in the library matches.</p>
                )}
              </>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">Days</label>
            <div className="flex flex-wrap gap-2">
              {DAYS.map((d, i) => (
                <button
                  type="button"
                  key={d}
                  onClick={() => setForm({ ...form, days: toggle(form.days, i) })}
                  className={`px-3 py-1 rounded-lg border ${
                    form.days.includes(i) ? 'bg-blue-600 text-white border-blue-600' : 'bg-white'
                  }`}
                >
                  {d}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">When it plays</label>
            <div className="flex flex-wrap gap-2">
              {([
                ['spread', 'Spread through the day'],
                ['exact', 'At exact times'],
              ] as const).map(([key, label]) => (
                <button
                  type="button"
                  key={key}
                  aria-pressed={form.when === key}
                  onClick={() => setForm({ ...form, when: key })}
                  className={`px-3 py-1.5 rounded-lg border ${
                    form.when === key ? 'bg-blue-600 text-white border-blue-600' : 'bg-white'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {form.when === 'exact' ? (
            <div>
              <label className="block text-sm font-medium mb-1">Exact times (station time)</label>
              <div className="flex flex-wrap items-center gap-2">
                {form.times.map((t) => (
                  <span key={t} className="inline-flex items-center gap-1 bg-gray-100 rounded-lg pl-3 pr-1 py-1 text-sm">
                    {clock(t)}
                    <button
                      type="button"
                      aria-label={`Remove ${clock(t)}`}
                      onClick={() => setForm({ ...form, times: form.times.filter((x) => x !== t) })}
                      className="min-w-[44px] min-h-[44px] sm:min-w-[32px] sm:min-h-[32px] text-lg text-gray-500 hover:text-red-600"
                    >
                      ×
                    </button>
                  </span>
                ))}
                <input
                  type="time"
                  aria-label="Time to add"
                  value={form.newTime}
                  onChange={(e) => setForm({ ...form, newTime: e.target.value })}
                  className="px-3 py-1.5 border rounded-lg"
                />
                <button
                  type="button"
                  disabled={!form.newTime}
                  onClick={() =>
                    setForm({
                      ...form,
                      times: [...new Set([...form.times, form.newTime])].sort(),
                      newTime: '',
                    })
                  }
                  className="px-3 py-1.5 border rounded-lg hover:bg-gray-50 disabled:opacity-40"
                >
                  Add time
                </button>
              </div>
            </div>
          ) : (
            <>
          <div>
            <label className="block text-sm font-medium mb-1">Time slots (station time)</label>
            <p className="text-xs text-gray-500 mb-2">
              Plays go anywhere in the slot, to the break with the fewest sponsorships so far.
            </p>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2" data-testid="time-slots">
              {[...TIME_SLOTS, ...WIDE_DAYPARTS.filter((w) => form.dayparts.includes(w.key))].map((p) => (
                <button
                  type="button"
                  key={p.key}
                  onClick={() => setForm({ ...form, dayparts: toggle(form.dayparts, p.key) })}
                  className={`px-3 py-2 rounded-lg border text-left ${
                    form.dayparts.includes(p.key) ? 'bg-blue-600 text-white border-blue-600' : 'bg-white'
                  }`}
                >
                  <div className="font-medium">{p.label}</div>
                  <div className="text-xs opacity-80">{p.hours}</div>
                </button>
              ))}
            </div>
          </div>
              <div className="max-w-xs">
              <label className="block text-sm font-medium mb-1">Plays in each slot</label>
              <input
                type="number"
                min={1}
                max={12}
                value={form.plays_per_daypart}
                onChange={(e) => setForm({ ...form, plays_per_daypart: Number(e.target.value) || 1 })}
                className="w-full px-3 py-2 border rounded-lg"
              />
              </div>
            </>
          )}

          <div>
            <label className="block text-sm font-medium mb-1">How the time is kept</label>
            <select
              value={form.timing}
              onChange={(e) => setForm({ ...form, timing: e.target.value as '' | TimingMode })}
              className="w-full md:w-auto px-3 py-2 border rounded-lg"
            >
              <option value="">Station default for that slot</option>
              <option value="hard">{TIMING_LABEL.hard}</option>
              <option value="soft">{TIMING_LABEL.soft}</option>
            </select>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Starts (optional)</label>
              <input
                type="date"
                value={form.start_date}
                onChange={(e) => setForm({ ...form, start_date: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Ends (optional)</label>
              <input
                type="date"
                value={form.end_date}
                onChange={(e) => setForm({ ...form, end_date: e.target.value })}
                className="w-full px-3 py-2 border rounded-lg"
              />
            </div>
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}
          <button
            type="submit"
            disabled={createMut.isPending}
            className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50"
          >
            {createMut.isPending ? <Spinner /> : 'Save schedule'}
          </button>
        </form>
      )}

      {playing && <audio src={playing.url} autoPlay onEnded={() => setPlaying(null)} />}
      {editing && <ContractEditor contract={editing} sponsorId={editing.sponsor_id} onClose={() => setEditing(null)} />}

      <div className="bg-white rounded-xl shadow">
        <div className="px-6 py-4 border-b">
          <h2 className="text-lg font-semibold">Contracts</h2>
        </div>
        {!schedules?.length ? (
          <p className="p-6 text-gray-500">
            No contracts yet. Add the first with Book a sponsorship at the top of the page.
          </p>
        ) : (
          <ul className="divide-y">
            {[...schedules].sort((a, b) => sponsorName(a.sponsor_id).localeCompare(sponsorName(b.sponsor_id))).map((s) => (
              <li key={s.id} id={`contract-${s.id}`} data-testid="spot-contract"
                className={`px-6 py-4 flex flex-wrap items-center gap-3 ${s.id === linkedContract ? 'bg-yellow-100 outline outline-2 outline-yellow-500' : ''}`}>
                <div className="flex-1 min-w-[16rem]">
                  <p className="font-medium">
                    <Link to={`/admin/sponsors/${s.sponsor_id}`} className="hover:underline">
                      {sponsorName(s.sponsor_id)}
                    </Link>
                    {!s.is_active && (
                      <span className="ml-2 text-xs bg-gray-200 text-gray-700 px-2 py-0.5 rounded">
                        Paused
                      </span>
                    )}
                  </p>
                  <p className="text-sm text-gray-600">
                    {s.name ? `${s.name}: ` : ''}{describe(s)} on {stationName(s.station_id)}
                  </p>
                  <p className="text-sm font-medium" data-testid="schedule-totals">{totalsLine(s)}</p>
                </div>
                <button onClick={() => listen(s.asset_id)} className="text-blue-600 text-sm">
                  {playing?.id === s.asset_id ? 'Stop' : 'Listen'}
                </button>
                <button
                  onClick={() => setEditing(s)}
                  data-testid="contract-edit"
                  className="text-sm px-3 py-1 rounded-lg bg-blue-600 text-white font-semibold"
                >
                  Edit
                </button>
                <button
                  onClick={() => updateMut.mutate({ id: s.id, data: { is_active: !s.is_active } })}
                  className="text-sm px-3 py-1 border rounded-lg hover:bg-gray-50"
                >
                  {s.is_active ? 'Pause' : 'Resume'}
                </button>
                <button
                  onClick={() => {
                    if (window.confirm(`Remove this contract for ${sponsorName(s.sponsor_id)}? Its coming plays are taken out of the queue.`))
                      deleteMut.mutate(s.id);
                  }}
                  className="text-sm px-3 py-1 border border-red-200 text-red-600 rounded-lg hover:bg-red-50"
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="bg-white rounded-xl shadow">
        <div className="px-6 py-4 border-b flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Next 7 days</h2>
          <select
            value={stationId}
            onChange={(e) => { setPreviewStation(e.target.value); rememberStation(e.target.value); }}
            className="px-3 py-1 border rounded-lg"
          >
            {stations.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
        {planLoading ? (
          <div className="p-6">
            <Spinner />
          </div>
        ) : (
          <div className="p-6 space-y-5">
            <BreakSettings stationId={stationId} />
            {plan?.spots_off && (
              <div className="bg-amber-50 border border-amber-200 text-amber-900 rounded-lg p-4 text-sm" data-testid="spots-off-note">
                <p className="font-semibold">Spots mode is off on this station</p>
                <p>No sponsor spots go on the air, so none are planned here. Switch Spots mode back on in the control room to plan them again.</p>
              </div>
            )}
            {!!saveWarnings.length && (
              <div
                className="bg-red-50 border border-red-200 text-red-900 rounded-lg p-4"
                role="alert"
                data-testid="save-warnings"
              >
                <div className="flex justify-between gap-3">
                  <p className="font-semibold">Saved, but not every play has room</p>
                  <button type="button" className="text-sm underline" onClick={() => setSaveWarnings([])}>
                    Dismiss
                  </button>
                </div>
                <ul className="text-sm mt-1 list-disc list-inside">
                  {saveWarnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}
            {!!plan?.unplaced.length && (
              <div className="bg-amber-50 border border-amber-200 text-amber-900 rounded-lg p-4">
                <p className="font-semibold">
                  {unplacedPlays} play{unplacedPlays === 1 ? '' : 's'} could not be given a place
                </p>
                <ul className="text-sm mt-1 list-disc list-inside">
                  {plan.unplaced.slice(0, 12).map((u, i) => (
                    <li key={i}>
                      {u.sponsor_name}, {formatDay(u.local_date)}, {daypartText(u.daypart)}
                      {u.count > 1 ? ` (${u.count} plays)` : ''}: {u.reason}
                    </li>
                  ))}
                </ul>
                {plan.unplaced.length > 12 && (
                  <p className="text-sm mt-1">…and {plan.unplaced.length - 12} more.</p>
                )}
              </div>
            )}
            {!!plan?.off_air?.length && (
              <div className="bg-blue-50 border border-blue-200 text-blue-900 rounded-lg p-4 text-sm">
                <p className="font-semibold">No spots while the station is off the air</p>
                <ul className="mt-1 list-disc list-inside">
                  {plan.off_air.map((w, i) => (
                    <li key={i}>
                      {w.name}: {formatWhen(w.start, plan.timezone)} to{' '}
                      {formatWhen(w.end, plan.timezone)}. {w.skipped_plays} play
                      {w.skipped_plays === 1 ? '' : 's'} left out; the rest were moved to open
                      times around it.
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!byDay.length ? (
              <p className="text-gray-500">No sponsorships are planned on this station in the next 7 days.</p>
            ) : (
              byDay.map(([day, spots]) => (
                <div key={day}>
                  <p className="font-medium text-gray-800 mb-2">
                    {formatDay(day)}{' '}
                    <span className="text-sm text-gray-500 font-normal">
                      {spots.length} play{spots.length === 1 ? '' : 's'}
                    </span>
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {spots.map((p, i) => (
                      <span
                        key={`${p.scheduled_at}-${i}`}
                        className="text-sm bg-gray-100 rounded-lg px-3 py-1"
                        title={p.asset_title}
                      >
                        <span className="font-medium">{formatTime(p.local_time)}</span>{' '}
                        {p.sponsor_name}
                        {p.block && (
                          <span className="ml-2 text-xs text-gray-600">in {p.block}</span>
                        )}
                        {p.moved_from && (
                          <span className="ml-2 text-xs font-semibold text-amber-700" title={`The ${p.moved_from} blocks were full, so this play moved here`}>
                            moved from {p.moved_from}
                          </span>
                        )}
                        {p.timing_mode === 'hard' && (
                          <span className="ml-2 text-xs font-semibold text-red-700" title={TIMING_LABEL.hard}>
                            on the second
                          </span>
                        )}
                      </span>
                    ))}
                  </div>
                </div>
              ))
            )}
            {plan && (
              <p className="text-xs text-gray-500">Times are in the station's time zone ({plan.timezone}).</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
