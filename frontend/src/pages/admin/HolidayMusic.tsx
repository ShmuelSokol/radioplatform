import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { rememberStation, stationToShow } from '../../lib/lastStation';
import { useStations } from '../../hooks/useStations';
import { useMayChange } from '../../hooks/useMayChange';
import { listAssets } from '../../api/assets';
import {
  HolidayOverview,
  HolidaySettings,
  addHolidayItems,
  getHolidayDays,
  getHolidayItems,
  getHolidayOverview,
  removeHolidayItem,
  saveHolidaySettings,
  suggestHolidayItems,
  updateHolidayItem,
} from '../../api/holidayMusic';
import Spinner from '../../components/Spinner';

/** "2026-12-05" -> "Sat, Dec 5" (the date is the station's own; no time zone shift) */
function day(iso: string, withYear = false) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', year: withYear ? 'numeric' : undefined, timeZone: 'UTC',
  });
}

function range(a: string, b: string) {
  return a === b ? day(a) : `${day(a)} to ${day(b)}`;
}

function kindLabel(kind: string | null, type: string) {
  if (kind === 'music') return 'Song';
  if (kind === 'long') return type === 'stories' || type === 'story' ? 'Story' : 'Shiur';
  return type;
}

function status(h: HolidayOverview) {
  if (!h.settings.enabled) return { text: 'Off for this station', cls: 'bg-gray-100 text-gray-600' };
  const total = h.items.music + h.items.long;
  if (h.in_season) {
    return total
      ? { text: `On now: 1 in ${h.today_every} today`, cls: 'bg-green-100 text-green-800' }
      : { text: 'In season, but the list is empty', cls: 'bg-amber-100 text-amber-800' };
  }
  const until = Math.round((Date.parse(h.mix_starts) - Date.now()) / 86400000);
  const when = until <= 1 ? 'tomorrow' : `in ${until} days`;
  return total
    ? { text: `Starts ${when}`, cls: 'bg-blue-50 text-blue-800' }
    : { text: `Starts ${when}: list empty`, cls: 'bg-amber-50 text-amber-800' };
}

function counts(h: HolidayOverview) {
  const parts = [];
  parts.push(`${h.items.music} song${h.items.music === 1 ? '' : 's'}`);
  parts.push(`${h.items.long} shiur${h.items.long === 1 ? '' : 'im'} / stor${h.items.long === 1 ? 'y' : 'ies'}`);
  return parts.join(' · ');
}

export default function HolidayMusic() {
  const { data: stationData } = useStations();
  const stations = stationData?.stations ?? [];
  const [params, setParams] = useSearchParams();
  const station = stationToShow(stations, params.get('station'));
  const open = params.get('holiday');
  const setParam = (k: string, v: string | null) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: k === 'station' });
  };

  const { data, isLoading } = useQuery({
    queryKey: ['holiday-music', station],
    queryFn: () => getHolidayOverview(station),
    enabled: !!station,
  });

  return (
    <div className="w-full space-y-6">
      <div className="flex flex-wrap justify-between items-start gap-4">
        <div className="max-w-3xl">
          <h1 className="text-3xl font-bold">Holiday Music</h1>
          <p className="text-gray-600 mt-1">
            The station's normal schedule keeps running. Before each holiday, every so often a song
            from that holiday's list takes the place of a normal song, and a holiday shiur or story the
            place of a normal one. It starts light and comes more often as the holiday gets closer, and
            stops when the holiday ends. Items on a list play only in their season.
          </p>
        </div>
        {stations.length > 1 && (
          <select
            aria-label="Station"
            value={station}
            onChange={(e) => { rememberStation(e.target.value); setParam('station', e.target.value); }}
            className="px-3 py-2 border rounded-lg max-w-full"
          >
            {stations.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        )}
      </div>

      {data?.israel_calendar && (
        <p className="text-sm text-gray-600">Dates follow the Israel calendar for this station.</p>
      )}
      {isLoading && <Spinner />}

      <div className="space-y-3">
        {data?.holidays.map((h) => {
          const st = status(h);
          const isOpen = open === h.key;
          return (
            <div key={h.key} className="border rounded-lg bg-white" data-nav-id={h.key}>
              <button
                type="button"
                onClick={() => setParam('holiday', isOpen ? null : h.key)}
                className="w-full text-left p-4 flex flex-wrap items-center gap-x-4 gap-y-1"
                aria-expanded={isOpen}
              >
                <span className="font-semibold text-lg min-w-[10rem]">{h.name}</span>
                <span className="text-gray-700">{range(h.first_day, h.last_day)}</span>
                <span className={`text-sm px-2 py-0.5 rounded ${st.cls}`}>{st.text}</span>
                <span className="text-sm text-gray-500 w-full sm:w-auto">
                  {h.settings.enabled
                    ? `Mix ${range(h.mix_starts, h.mix_ends)} · ${counts(h)}`
                    : counts(h)}
                </span>
                <span className="ml-auto text-gray-400">{isOpen ? '▾' : '▸'}</span>
              </button>
              {isOpen && <HolidayDetail holiday={h} stationId={station} stations={stations} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function HolidayDetail({
  holiday,
  stationId,
  stations,
}: {
  holiday: HolidayOverview;
  stationId: string;
  stations: { id: string; name: string }[];
}) {
  const mayChange = useMayChange();
  const queryClient = useQueryClient();
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['holiday-music'] });
    queryClient.invalidateQueries({ queryKey: ['holiday-days'] });
    queryClient.invalidateQueries({ queryKey: ['holiday-items'] });
  };
  const stationName = stations.find((s) => s.id === stationId)?.name ?? 'this station';

  const [form, setForm] = useState<HolidaySettings>(holiday.settings);
  useEffect(() => setForm(holiday.settings), [holiday.settings]);
  const dirty = JSON.stringify(form) !== JSON.stringify(holiday.settings);
  const [saved, setSaved] = useState('');
  const save = useMutation({
    mutationFn: () => saveHolidaySettings(holiday.key, stationId, form),
    onSuccess: () => { setSaved('Saved.'); refresh(); },
  });

  const { data: days } = useQuery({
    queryKey: ['holiday-days', holiday.key, stationId, holiday.settings],
    queryFn: () => getHolidayDays(holiday.key, stationId),
  });
  const { data: items } = useQuery({
    queryKey: ['holiday-items', holiday.key, stationId],
    queryFn: () => getHolidayItems(holiday.key, stationId),
  });

  const num = (k: keyof HolidaySettings, label: string, hint?: string, max = 200, aria = label) => (
    <label className="flex flex-wrap items-center gap-2">
      <span>{label}</span>
      <input
        type="number"
        inputMode="numeric"
        min={k === 'lead_days' ? 0 : 1}
        max={max}
        value={form[k] as number}
        disabled={!mayChange}
        onChange={(e) => { setSaved(''); setForm({ ...form, [k]: Number(e.target.value) }); }}
        className="w-20 px-2 py-1.5 border rounded-lg"
        aria-label={aria}
      />
      {hint && <span className="text-gray-500 text-sm">{hint}</span>}
    </label>
  );

  const remove = useMutation({ mutationFn: removeHolidayItem, onSuccess: refresh });
  const change = useMutation({
    mutationFn: (v: { id: string; change: Parameters<typeof updateHolidayItem>[1] }) =>
      updateHolidayItem(v.id, v.change),
    onSuccess: refresh,
  });

  const peak = Math.max(...(days?.days ?? []).map((d) => d.share), 0.0001);
  // The day-by-day box opens on today, not on the first day of the run-up
  const dayBox = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const box = dayBox.current;
    const row = box?.querySelector<HTMLElement>('[data-today="1"]');
    if (box && row) box.scrollTop = Math.max(0, row.offsetTop - box.clientHeight / 2);
  }, [days]);

  return (
    <div className="border-t p-4 space-y-6">
      <section className="space-y-3">
        <h2 className="font-semibold">Settings for {stationName}</h2>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={form.enabled}
            disabled={!mayChange}
            onChange={(e) => { setSaved(''); setForm({ ...form, enabled: e.target.checked }); }}
          />
          <span>Mix in {holiday.name} items on this station</span>
        </label>
        <div className="grid gap-3 sm:grid-cols-2">
          {num('lead_days', 'Start', 'days before the holiday', 90, 'Days before the holiday to start')}
          {num('start_every', 'At the start: 1 holiday item in every', '(15 is about one an hour)')}
          {num('peak_every', 'The day before: 1 in every')}
          {num('during_every', 'On the holiday days on the air: 1 in every')}
        </div>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={form.include_shiurim}
            disabled={!mayChange}
            onChange={(e) => { setSaved(''); setForm({ ...form, include_shiurim: e.target.checked }); }}
          />
          <span>Include shiurim and stories (a holiday shiur takes the place of a normal one)</span>
        </label>
        <label className="flex flex-wrap items-center gap-2">
          <span>Stop</span>
          <select
            value={form.stop}
            disabled={!mayChange}
            onChange={(e) => { setSaved(''); setForm({ ...form, stop: e.target.value as 'end' | 'start' }); }}
            className="px-2 py-1.5 border rounded-lg"
            aria-label="Stop"
          >
            <option value="end">when the holiday ends</option>
            <option value="start">when the holiday begins</option>
          </select>
        </label>
        {mayChange && (
          <div className="flex items-center gap-3">
            <button
              type="button"
              disabled={!dirty || save.isPending}
              onClick={() => save.mutate()}
              className="px-4 py-2 rounded-lg bg-blue-600 text-white disabled:opacity-50"
            >
              {save.isPending ? 'Saving…' : 'Save settings'}
            </button>
            {saved && !dirty && <span className="text-green-700 text-sm">{saved}</span>}
            {save.isError && <span className="text-red-700 text-sm">Could not save. Try again.</span>}
          </div>
        )}
        <p className="text-sm text-gray-500">
          The rate goes up evenly each day from the start to the day before. A holiday song plays at
          most once every 2 hours and a shiur or story once every 6, so a short list plays less often
          than the rate. The station is silent on Yom Tov and Shabbos as always; nothing is mixed in then.
        </p>
      </section>

      {days && days.days.length > 0 && (
        <section>
          <h2 className="font-semibold mb-2">Day by day</h2>
          <div ref={dayBox} className="relative max-h-72 overflow-y-auto border rounded-lg">
            <table className="w-full text-sm">
              <tbody>
                {days.days.map((d) => (
                  <tr key={d.date} data-today={d.today ? '1' : undefined} className={d.today ? 'bg-yellow-50 font-semibold' : ''}>
                    <td className="px-2 py-1 whitespace-nowrap">{day(d.date)}{d.today ? ' (today)' : ''}</td>
                    <td className="px-2 py-1 whitespace-nowrap">
                      {d.every ? `1 in ${d.every}` : 'none'}
                      {d.phase === 'holiday' ? ' · holiday' : ''}
                    </td>
                    <td className="px-2 py-1 w-1/2">
                      <div
                        className={`h-2 rounded ${d.phase === 'holiday' ? 'bg-purple-400' : 'bg-blue-400'}`}
                        style={{ width: `${Math.round((d.share / peak) * 100)}%` }}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="space-y-2">
        <h2 className="font-semibold">On the list ({items?.length ?? 0})</h2>
        {items && items.length === 0 && (
          <p className="text-gray-600">Nothing yet. Add songs and shiurim below.</p>
        )}
        <ul className="divide-y border rounded-lg">
          {items?.map((it) => (
            <li key={it.id} className="p-2 flex flex-wrap items-center gap-x-3 gap-y-1">
              <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100">{kindLabel(it.kind, it.asset_type)}</span>
              <span className="font-medium break-words min-w-0">{it.title}</span>
              {it.artist && <span className="text-gray-500 text-sm">{it.artist}</span>}
              <span className="text-xs text-gray-500">{it.station_name ? `${it.station_name} only` : 'All stations'}</span>
              {it.all_year && <span className="text-xs text-gray-500">also all year</span>}
              {mayChange && (
                <span className="ml-auto flex flex-wrap gap-2 text-sm">
                  <button
                    type="button"
                    className="underline text-blue-700"
                    onClick={() => change.mutate({
                      id: it.id,
                      change: it.station_id ? { every_station: true } : { station_id: stationId },
                    })}
                  >
                    {it.station_id ? 'All stations' : `${stationName} only`}
                  </button>
                  <button
                    type="button"
                    className="underline text-blue-700"
                    onClick={() => change.mutate({ id: it.id, change: { all_year: !it.all_year } })}
                  >
                    {it.all_year ? 'Holiday only' : 'Also all year'}
                  </button>
                  <button
                    type="button"
                    className="underline text-red-700"
                    onClick={() => remove.mutate(it.id)}
                  >
                    Remove
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>
      </section>

      {mayChange && <AddItems holiday={holiday} stationId={stationId} stationName={stationName} onAdded={refresh} />}
    </div>
  );
}

function AddItems({
  holiday,
  stationId,
  stationName,
  onAdded,
}: {
  holiday: HolidayOverview;
  stationId: string;
  stationName: string;
  onAdded: () => void;
}) {
  const [search, setSearch] = useState('');
  const [term, setTerm] = useState('');
  const [mode, setMode] = useState<'suggest' | 'search' | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [onlyHere, setOnlyHere] = useState(false);
  const [result, setResult] = useState('');

  const { data: suggestions, isFetching: suggesting } = useQuery({
    queryKey: ['holiday-suggest', holiday.key],
    queryFn: () => suggestHolidayItems(holiday.key),
    enabled: mode === 'suggest',
  });
  const { data: found, isFetching: searching } = useQuery({
    queryKey: ['holiday-search', term],
    queryFn: () => listAssets({ search: term, limit: 50 }),
    enabled: mode === 'search' && term.length > 1,
  });
  const rows = mode === 'suggest'
    ? (suggestions ?? []).map((s) => ({ id: s.asset_id, title: s.title, artist: s.artist, type: s.asset_type }))
    : (found?.assets ?? [])
        .filter((a) => ['music', 'shiur', 'stories', 'story', 'daf_yomi', 'lecture'].includes(a.asset_type))
        .map((a) => ({ id: String(a.id), title: a.title, artist: a.artist ?? null, type: a.asset_type }));

  const add = useMutation({
    mutationFn: () => addHolidayItems(holiday.key, [...picked], onlyHere ? stationId : null),
    onSuccess: (r) => {
      setResult(`Added ${r.added}.${r.already_listed ? ` ${r.already_listed} were already on the list.` : ''}`);
      setPicked(new Set());
      onAdded();
    },
  });
  const toggle = (id: string) => {
    const next = new Set(picked);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setPicked(next);
  };

  return (
    <section className="space-y-3 border rounded-lg p-3 bg-gray-50">
      <h2 className="font-semibold">Add to the {holiday.name} list</h2>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => { setMode('suggest'); setResult(''); }}
          className="px-3 py-2 rounded-lg border bg-white"
        >
          Suggest from titles
        </button>
        <form
          className="flex gap-2 flex-1 min-w-[14rem]"
          onSubmit={(e) => { e.preventDefault(); setTerm(search.trim()); setMode('search'); setResult(''); }}
        >
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search the library"
            className="flex-1 min-w-0 px-3 py-2 border rounded-lg"
            aria-label="Search the library"
          />
          <button type="submit" className="px-3 py-2 rounded-lg border bg-white">Search</button>
        </form>
      </div>
      {(suggesting || searching) && <Spinner />}
      {mode && !suggesting && !searching && rows.length === 0 && (
        <p className="text-gray-600 text-sm">
          {mode === 'suggest' ? 'No titles in the library name this holiday. Try a search.' : 'Nothing found.'}
        </p>
      )}
      {rows.length > 0 && (
        <ul className="max-h-72 overflow-y-auto divide-y border rounded-lg bg-white">
          {rows.map((r) => (
            <li key={r.id}>
              <label className="p-2 flex items-center gap-2">
                <input type="checkbox" checked={picked.has(r.id)} onChange={() => toggle(r.id)} />
                <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100">{r.type}</span>
                <span className="min-w-0 break-words">{r.title}</span>
                {r.artist && <span className="text-gray-500 text-sm">{r.artist}</span>}
              </label>
            </li>
          ))}
        </ul>
      )}
      {rows.length > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={onlyHere} onChange={(e) => setOnlyHere(e.target.checked)} />
            <span>{stationName} only (otherwise every station)</span>
          </label>
          <button
            type="button"
            disabled={!picked.size || add.isPending}
            onClick={() => add.mutate()}
            className="px-4 py-2 rounded-lg bg-blue-600 text-white disabled:opacity-50"
          >
            Add {picked.size || ''} to the list
          </button>
        </div>
      )}
      {result && <p className="text-green-700 text-sm">{result}</p>}
    </section>
  );
}
