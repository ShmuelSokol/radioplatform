import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { listAssets } from '../../api/assets';
import { listAssetTypes } from '../../api/playlists';
import {
  BlockItem,
  BlockTiming,
  ProgramBlock,
  createProgramBlock,
  deleteProgramBlock,
  listProgramBlocks,
  previewProgramBlocks,
  updateProgramBlock,
} from '../../api/programBlocks';
import { useStations } from '../../hooks/useStations';
import Spinner from '../../components/Spinner';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

/** soft-limit = after the song, but no later than a number of minutes. */
type Timing = BlockTiming | 'soft-limit';

function timingText(mode: BlockTiming, latest: number | null, short = false) {
  if (mode === 'hard') return 'on the second';
  if (latest) return `after the song, at most ${latest} min late`;
  return short ? 'after the song' : 'after the song finishes';
}

const emptyForm = {
  id: '',
  name: '',
  days: [0, 1, 2, 3, 4, 5, 6],
  times: [] as string[],
  newTime: '',
  everyHourAt: '00',
  timing: 'hard' as Timing,
  latest: 5,
  items: [] as BlockItem[],
};

function clock(hhmm: string) {
  const [h, m] = hhmm.split(':').map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`;
}

function length(seconds: number) {
  const s = Math.round(seconds);
  // 0 means none of the items has a known length yet (e.g. the time and
  // weather recordings are made shortly before air).
  if (s <= 0) return 'length not known yet';
  return `runs ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function minutesText(seconds: number) {
  const m = Math.floor(seconds / 60);
  const sec = seconds % 60;
  if (!sec) return `${m} minute${m === 1 ? '' : 's'}`;
  return m ? `${m}:${String(sec).padStart(2, '0')} minutes` : `${sec} seconds`;
}

function itemText(item: BlockItem) {
  if (item.label) return item.label;
  if (item.kind === 'time') return 'Time announcement';
  if (item.kind === 'weather') return 'Weather';
  if (item.kind === 'sponsors') {
    const limit = item.max_seconds ? `up to ${minutesText(item.max_seconds)} of sponsorships` : '';
    if (!item.count) return limit || '1 sponsor place';
    const places = `${item.count} sponsor place${item.count === 1 ? '' : 's'}`;
    return limit ? `${places}, ${limit}` : places;
  }
  if (item.kind === 'category')
    return item.category ? `${item.asset_type} / ${item.category}` : item.asset_type;
  return 'Recording';
}

function itemHint(item: BlockItem) {
  if (item.kind === 'asset') return 'this recording every time';
  if (item.kind === 'category') return 'takes turns: the one that has rested longest';
  if (item.kind === 'time') return "the hour's time announcement";
  if (item.kind === 'sponsors')
    return item.max_seconds
      ? 'filled from the sponsorships that want this time of day; what does not fit goes to the next block with room'
      : 'filled from the sponsorships that want this time of day (Spot Schedule)';
  return "the day's weather";
}

/** "Every hour at :00" when the times are the same minute of all 24 hours. */
function timesText(csv: string) {
  const times = csv.split(',').filter(Boolean);
  const minutes = new Set(times.map((t) => t.slice(3)));
  if (times.length === 24 && minutes.size === 1) return `Every hour at :${[...minutes][0]}`;
  if (times.length > 6) return `${times.length} times a day, from ${clock(times[0])}`;
  return times.map(clock).join(', ');
}

export default function ProgramBlocks() {
  const queryClient = useQueryClient();
  const { data: stationData } = useStations();
  const stations = stationData?.stations ?? [];
  const [stationId, setStationId] = useState('');
  const station = stationId || stations[0]?.id || '';

  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState('');
  const [form, setFormState] = useState(emptyForm);
  const setForm = (next: typeof emptyForm) => {
    setFormState(next);
    setError('');
  };
  const [search, setSearch] = useState('');
  const [combo, setCombo] = useState('');
  const [places, setPlaces] = useState(3);
  const [sponsorMinutes, setSponsorMinutes] = useState('');

  const { data: blocks, isLoading } = useQuery({
    queryKey: ['program-blocks', station],
    queryFn: () => listProgramBlocks(station),
    enabled: !!station,
  });
  const { data: preview } = useQuery({
    queryKey: ['program-blocks-preview', station],
    queryFn: () => previewProgramBlocks(station, 24),
    enabled: !!station,
  });
  const { data: combos } = useQuery({
    queryKey: ['asset-type-combos'],
    queryFn: listAssetTypes,
    enabled: showForm,
  });
  const { data: found, isFetching: searching } = useQuery({
    queryKey: ['block-asset-search', search],
    queryFn: () => listAssets({ search, limit: 12 }),
    enabled: showForm && search.trim().length >= 2,
  });

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['program-blocks'] });
    queryClient.invalidateQueries({ queryKey: ['program-blocks-preview'] });
    queryClient.invalidateQueries({ queryKey: ['queue'] });
  };
  const close = () => {
    setShowForm(false);
    setForm(emptyForm);
    setSearch('');
    setCombo('');
  };
  const failed = () => setError('Could not save the block. Check the fields and try again.');
  const createMut = useMutation({
    mutationFn: createProgramBlock,
    onSuccess: () => {
      refresh();
      close();
    },
    onError: failed,
  });
  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<ProgramBlock> }) =>
      updateProgramBlock(id, data),
    onSuccess: () => {
      refresh();
      close();
    },
    onError: failed,
  });
  const toggleMut = useMutation({
    mutationFn: (b: ProgramBlock) => updateProgramBlock(b.id, { is_active: !b.is_active }),
    onSuccess: refresh,
  });
  const deleteMut = useMutation({ mutationFn: deleteProgramBlock, onSuccess: refresh });

  const edit = (b: ProgramBlock) => {
    setForm({
      ...emptyForm,
      id: b.id,
      name: b.name,
      days: b.days_of_week.split(',').filter(Boolean).map(Number),
      times: b.times.split(',').filter(Boolean),
      timing: b.timing_mode === 'soft' && b.latest_minutes ? 'soft-limit' : b.timing_mode,
      latest: b.latest_minutes ?? 5,
      items: b.items,
    });
    setShowForm(true);
    window.scrollTo({ top: 0 });
  };

  const addItem = (item: BlockItem) => setForm({ ...form, items: [...form.items, item] });
  const move = (index: number, by: number) => {
    const items = [...form.items];
    const [item] = items.splice(index, 1);
    items.splice(index + by, 0, item);
    setForm({ ...form, items });
  };

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    // A time still sitting in the box (not yet added with the button) counts too.
    const times = [...new Set([...form.times, ...(form.newTime ? [form.newTime] : [])])].sort();
    if (!form.name.trim()) return setError('Give the block a name.');
    if (!form.days.length) return setError('Choose at least one day.');
    if (!times.length) return setError('Add at least one time of day.');
    if (!form.items.length) return setError('Add at least one item to the block.');
    const data = {
      station_id: station,
      name: form.name.trim(),
      is_active: true,
      days_of_week: [...form.days].sort().join(','),
      times: times.join(','),
      timing_mode: (form.timing === 'hard' ? 'hard' : 'soft') as BlockTiming,
      latest_minutes: form.timing === 'soft-limit' ? form.latest : null,
      items: form.items,
    };
    if (form.id) {
      const { is_active: _keep, ...changes } = data;
      updateMut.mutate({ id: form.id, data: changes });
    } else createMut.mutate(data);
  };

  const lengthOf = (id: string) =>
    preview?.occurrences.find((o) => o.block_id === id)?.seconds ?? null;
  const saving = createMut.isPending || updateMut.isPending;

  return (
    <div className="w-full space-y-8">
      <div className="flex flex-wrap justify-between items-start gap-4">
        <div>
          <h1 className="text-3xl font-bold">Program Blocks</h1>
          <p className="text-gray-600 mt-1">
            A block is one event that plays a run of items in order, such as a station ID, the
            weather, a PSA, a sponsor and a program, at the clock times you choose.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {stations.length > 1 && (
            <select
              aria-label="Station"
              value={station}
              onChange={(e) => setStationId(e.target.value)}
              className="px-3 py-2 border rounded-lg"
            >
              {stations.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            onClick={() => (showForm ? close() : setShowForm(true))}
            className="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700"
          >
            {showForm ? 'Cancel' : 'New block'}
          </button>
        </div>
      </div>

      {showForm && (
        <form onSubmit={submit} className="bg-white rounded-lg shadow-md p-5 space-y-5">
          <h2 className="text-xl font-semibold">{form.id ? 'Edit block' : 'New block'}</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label htmlFor="block-name" className="block text-sm font-medium mb-1">
                Name
              </label>
              <input
                id="block-name"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Weather Sponsors"
                className="w-full px-3 py-2 border rounded-lg"
              />
            </div>
            <div>
              <label htmlFor="block-timing" className="block text-sm font-medium mb-1">
                How the time is kept
              </label>
              <select
                id="block-timing"
                value={form.timing}
                onChange={(e) => setForm({ ...form, timing: e.target.value as Timing })}
                className="w-full px-3 py-2 border rounded-lg"
              >
                <option value="hard">On the second: the song on air fades out</option>
                <option value="soft">After the song on air finishes</option>
                <option value="soft-limit">After the song, but no later than…</option>
              </select>
              {form.timing === 'soft-limit' && (
                <label className="flex items-center gap-2 mt-2 text-sm">
                  Fade the song if it is still playing
                  <input
                    type="number"
                    min={1}
                    max={60}
                    aria-label="Minutes to wait at most"
                    value={form.latest}
                    onChange={(e) =>
                      setForm({ ...form, latest: Math.max(1, Math.min(60, Number(e.target.value) || 1)) })
                    }
                    className="w-20 px-2 py-1.5 border rounded-lg"
                  />
                  minutes after the time
                </label>
              )}
            </div>
          </div>

          <div>
            <span className="block text-sm font-medium mb-1">Days</span>
            <div className="flex flex-wrap gap-2">
              {DAYS.map((d, i) => (
                <button
                  type="button"
                  key={d}
                  aria-pressed={form.days.includes(i)}
                  onClick={() =>
                    setForm({
                      ...form,
                      days: form.days.includes(i)
                        ? form.days.filter((x) => x !== i)
                        : [...form.days, i],
                    })
                  }
                  className={`px-3 py-2 rounded-lg border ${
                    form.days.includes(i) ? 'bg-blue-600 text-white border-blue-600' : 'bg-white'
                  }`}
                >
                  {d}
                </button>
              ))}
            </div>
          </div>

          <div>
            <span className="block text-sm font-medium mb-1">Times (station time)</span>
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <span className="text-sm text-gray-700">Every hour at</span>
              <select
                aria-label="Minute of every hour"
                value={form.everyHourAt}
                onChange={(e) => setForm({ ...form, everyHourAt: e.target.value })}
                className="px-2 py-1.5 border rounded-lg"
              >
                {['00', '15', '30', '45'].map((m) => (
                  <option key={m} value={m}>
                    :{m}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() =>
                  setForm({
                    ...form,
                    times: Array.from(
                      { length: 24 },
                      (_, h) => `${String(h).padStart(2, '0')}:${form.everyHourAt}`,
                    ),
                  })
                }
                className="px-3 py-1.5 border rounded-lg hover:bg-gray-50"
              >
                Set
              </button>
              {!!form.times.length && (
                <button
                  type="button"
                  onClick={() => setForm({ ...form, times: [] })}
                  className="px-3 py-1.5 text-sm text-gray-600 hover:text-red-600"
                >
                  Clear all
                </button>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {form.times.map((t) => (
                <span
                  key={t}
                  className="inline-flex items-center bg-gray-100 rounded-lg pl-3 text-sm"
                >
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

          <div>
            <span className="block text-sm font-medium mb-1">What plays, in order</span>
            {!form.items.length && (
              <p className="text-sm text-gray-500 mb-2">Nothing yet. Add items below.</p>
            )}
            <ol className="space-y-2 mb-3" data-testid="block-items">
              {form.items.map((item, i) => (
                <li
                  key={i}
                  className="flex flex-wrap items-center gap-2 border rounded-lg px-3 py-2"
                >
                  <span className="text-gray-500 w-5">{i + 1}.</span>
                  <span className="flex-1 min-w-[10rem]">
                    <span className="font-medium">{itemText(item)}</span>
                    <span className="block text-xs text-gray-500">{itemHint(item)}</span>
                  </span>
                  <button
                    type="button"
                    aria-label={`Move ${itemText(item)} up`}
                    disabled={i === 0}
                    onClick={() => move(i, -1)}
                    className="min-w-[40px] min-h-[40px] border rounded-lg disabled:opacity-30"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    aria-label={`Move ${itemText(item)} down`}
                    disabled={i === form.items.length - 1}
                    onClick={() => move(i, 1)}
                    className="min-w-[40px] min-h-[40px] border rounded-lg disabled:opacity-30"
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    aria-label={`Remove ${itemText(item)}`}
                    onClick={() =>
                      setForm({ ...form, items: form.items.filter((_, x) => x !== i) })
                    }
                    className="min-w-[40px] min-h-[40px] text-lg text-gray-500 hover:text-red-600"
                  >
                    ×
                  </button>
                </li>
              ))}
            </ol>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 bg-gray-50 rounded-lg p-3">
              <div>
                <label htmlFor="block-search" className="block text-sm font-medium mb-1">
                  Add one recording
                </label>
                <input
                  id="block-search"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search the library"
                  className="w-full px-3 py-2 border rounded-lg"
                />
                {search.trim().length >= 2 && (
                  <div className="mt-1 border rounded-lg bg-white max-h-48 overflow-y-auto">
                    {searching && <p className="px-3 py-2 text-sm text-gray-500">Searching…</p>}
                    {!searching && found?.assets?.length === 0 && (
                      <p className="px-3 py-2 text-sm text-gray-500">Nothing found.</p>
                    )}
                    {found?.assets?.map((a) => (
                      <button
                        type="button"
                        key={a.id}
                        onClick={() => {
                          addItem({ kind: 'asset', asset_id: a.id, label: a.title });
                          setSearch('');
                        }}
                        className="block w-full text-left px-3 py-2 text-sm hover:bg-blue-50"
                      >
                        {a.title}
                        <span className="text-gray-500"> · {a.asset_type}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <label htmlFor="block-combo" className="block text-sm font-medium mb-1">
                  Add a type that takes turns
                </label>
                <div className="flex gap-2">
                  <select
                    id="block-combo"
                    value={combo}
                    onChange={(e) => setCombo(e.target.value)}
                    className="flex-1 min-w-0 px-3 py-2 border rounded-lg"
                  >
                    <option value="">Choose a type</option>
                    {combos?.map((c) => {
                      const value = `${c.asset_type}|${c.category ?? ''}`;
                      return (
                        <option key={value} value={value}>
                          {c.category ? `${c.asset_type} / ${c.category}` : c.asset_type}
                        </option>
                      );
                    })}
                  </select>
                  <button
                    type="button"
                    disabled={!combo}
                    onClick={() => {
                      const [asset_type, category] = combo.split('|');
                      addItem(
                        category
                          ? { kind: 'category', asset_type, category }
                          : { kind: 'category', asset_type },
                      );
                      setCombo('');
                    }}
                    className="px-3 py-2 border rounded-lg bg-white hover:bg-gray-50 disabled:opacity-40"
                  >
                    Add
                  </button>
                </div>
                <div className="flex flex-wrap items-center gap-2 mt-3">
                  <input
                    type="number"
                    min={1}
                    max={10}
                    aria-label="Number of sponsor places"
                    value={places}
                    onChange={(e) => setPlaces(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
                    className="w-16 px-2 py-2 border rounded-lg"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      const seconds = Math.round((Number(sponsorMinutes) || 0) * 60);
                      addItem(
                        seconds >= 15
                          ? { kind: 'sponsors', max_seconds: Math.min(900, seconds) }
                          : { kind: 'sponsors', count: places },
                      );
                    }}
                    className="px-3 py-2 border rounded-lg bg-white hover:bg-gray-50"
                  >
                    Add sponsor places
                  </button>
                  <label className="flex items-center gap-2 text-sm text-gray-700">
                    at most
                    <input
                      type="number"
                      min={0.25}
                      max={15}
                      step={0.25}
                      placeholder="any"
                      aria-label="Most minutes of sponsorships in this block"
                      data-testid="sponsor-minutes"
                      value={sponsorMinutes}
                      onChange={(e) => setSponsorMinutes(e.target.value)}
                      className="w-20 px-2 py-2 border rounded-lg"
                    />
                    minutes (with a time limit, as many as fit in it; the number of places is not used)
                  </label>
                </div>
                <div className="flex flex-wrap gap-2 mt-3">
                  <button
                    type="button"
                    onClick={() => addItem({ kind: 'time' })}
                    className="px-3 py-2 border rounded-lg bg-white hover:bg-gray-50"
                  >
                    Add the time
                  </button>
                  <button
                    type="button"
                    onClick={() => addItem({ kind: 'weather' })}
                    className="px-3 py-2 border rounded-lg bg-white hover:bg-gray-50"
                  >
                    Add the weather
                  </button>
                </div>
              </div>
            </div>
          </div>

          {error && (
            <p role="alert" className="text-red-700 text-sm">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={saving}
            className="bg-blue-600 text-white px-5 py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? 'Saving…' : form.id ? 'Save changes' : 'Save block'}
          </button>
        </form>
      )}

      <section className="space-y-3">
        {isLoading && <Spinner />}
        {!isLoading && !blocks?.length && (
          <div className="bg-white rounded-lg shadow-md p-6 text-gray-600">
            No blocks yet. Until there is one, the station plays its built-in time and weather on
            the hour.
          </div>
        )}
        {blocks?.map((b) => {
          const seconds = lengthOf(b.id);
          return (
            <div key={b.id} className="bg-white rounded-lg shadow-md p-4" data-testid="block-card">
              <div className="flex flex-wrap justify-between items-start gap-3">
                <div className="min-w-0">
                  <h3 className="font-bold text-lg">
                    {b.name}
                    {!b.is_active && (
                      <span className="ml-2 text-xs font-medium bg-gray-200 text-gray-700 rounded px-2 py-0.5">
                        Off
                      </span>
                    )}
                  </h3>
                  <p className="text-sm text-gray-600">
                    {timesText(b.times)} ·{' '}
                    {b.days_of_week.split(',').length === 7
                      ? 'every day'
                      : b.days_of_week
                          .split(',')
                          .map((d) => DAYS[Number(d)])
                          .join(', ')}{' '}
                    · {timingText(b.timing_mode, b.latest_minutes)}
                    {seconds != null && ` · ${length(seconds)}`}
                  </p>
                  <p className="text-sm text-gray-800 mt-1">
                    {b.items.map(itemText).join('  →  ')}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => edit(b)}
                    className="px-3 py-2 border rounded-lg hover:bg-gray-50"
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    onClick={() => toggleMut.mutate(b)}
                    className="px-3 py-2 border rounded-lg hover:bg-gray-50"
                  >
                    {b.is_active ? 'Turn off' : 'Turn on'}
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteMut.mutate(b.id)}
                    className="px-3 py-2 border rounded-lg text-red-700 hover:bg-red-50"
                  >
                    Delete
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </section>

      {!!preview?.occurrences.length && (
        <section>
          <h2 className="text-xl font-semibold mb-2">Next 24 hours</h2>
          <div className="bg-white rounded-lg shadow-md divide-y">
            {preview.occurrences.map((o) => (
              <div key={`${o.block_id}-${o.at}`} className="px-4 py-3">
                <div className="flex flex-wrap items-baseline gap-x-3">
                  <span className="font-mono tabular-nums">{o.local_time}</span>
                  <span className="font-medium">{o.block_name}</span>
                  <span className="text-sm text-gray-600">
                    {timingText(o.timing_mode, o.latest_minutes, true)} · {length(o.seconds)}
                  </span>
                </div>
                <p className="text-sm text-gray-700">
                  {o.items
                    .filter((i) => !i.problem && !i.open_place)
                    .map((i) => (i.title && !['time', 'weather', 'sponsor'].includes(i.kind) ? i.title : i.label))
                    .join('  →  ')}
                </p>
                {o.items.some((i) => i.open_place) && (
                  <p className="text-sm text-gray-500">
                    {o.items.filter((i) => i.open_place).length} sponsor place
                    {o.items.filter((i) => i.open_place).length === 1 ? '' : 's'} still open
                  </p>
                )}
                {o.problems.map((p) => (
                  <p key={p} className="text-sm text-amber-700">
                    Left out: {p}
                  </p>
                ))}
              </div>
            ))}
          </div>
          <p className="text-sm text-gray-500 mt-2">
            Lengths leave out the time and weather, which are prepared closer to air time.
          </p>
        </section>
      )}
    </div>
  );
}
