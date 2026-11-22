import React, { useState, useEffect, useRef } from 'react';
import { useStations, useCreateStation, useUpdateStation, useDeleteStation } from '../../hooks/useStations';
import apiClient from '../../api/client';
import { listAssets } from '../../api/assets';
import type { Asset } from '../../types';
import Spinner from '../../components/Spinner';
import PlayoutBadge from '../../components/audio/PlayoutBadge';
import { useAuth } from '../../hooks/useAuth';

export default function Stations() {
  const { data, isLoading } = useStations();
  const createMutation = useCreateStation();
  const updateMutation = useUpdateStation();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const deleteMutation = useDeleteStation();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [expandedStation, setExpandedStation] = useState<string | null>(null);

  // City search for create form
  const [createQuery, setCreateQuery] = useState('');
  const [createResults, setCreateResults] = useState<GeoResult[]>([]);
  const [createSearching, setCreateSearching] = useState(false);
  const [createShowDropdown, setCreateShowDropdown] = useState(false);
  const [createSelected, setCreateSelected] = useState<GeoResult | null>(null);
  const createDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const createWrapperRef = useRef<HTMLDivElement>(null);
  const [createError, setCreateError] = useState('');

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (createWrapperRef.current && !createWrapperRef.current.contains(e.target as Node)) {
        setCreateShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleCreateSearch = (value: string) => {
    setCreateQuery(value);
    setCreateShowDropdown(true);
    if (createDebounceRef.current) clearTimeout(createDebounceRef.current);
    if (value.length < 2) { setCreateResults([]); return; }
    createDebounceRef.current = setTimeout(async () => {
      setCreateSearching(true);
      try {
        const res = await apiClient.get('/stations/geocode', { params: { q: value } });
        setCreateResults(res.data);
      } catch { setCreateResults([]); }
      setCreateSearching(false);
    }, 350);
  };

  const handleCreateSelect = (result: GeoResult) => {
    setCreateSelected(result);
    setCreateError('');
    setCreateQuery(result.display_name);
    setCreateShowDropdown(false);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    // Without this the button silently did nothing when no city was picked.
    if (!createSelected) { setCreateError('Pick the city from the list under Location.'); return; }
    setCreateError('');
    await createMutation.mutateAsync({
      name,
      latitude: createSelected.latitude,
      longitude: createSelected.longitude,
    });
    setName('');
    setCreateQuery('');
    setCreateSelected(null);
    setShowForm(false);
  };

  if (isLoading) return <div className="text-center py-10">Loading...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Manage Stations</h1>
        <button
          onClick={() => setShowForm(!showForm)}
          className="bg-brand-600 hover:bg-brand-700 text-white px-4 py-2 rounded transition"
        >
          {showForm ? 'Cancel' : 'New Station'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="bg-white shadow rounded-lg p-6 mb-6 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Station Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full border rounded px-3 py-2"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Location</label>
            <div ref={createWrapperRef} className="relative">
              <input
                type="text"
                value={createQuery}
                onChange={e => handleCreateSearch(e.target.value)}
                placeholder="Search for a city..."
                className="w-full border rounded px-3 py-2"
              />
              {createShowDropdown && createQuery.length >= 2 && (
                <div className="absolute z-10 w-full bg-white border rounded-b shadow-lg max-h-48 overflow-y-auto">
                  {createSearching && <div className="px-3 py-2 text-xs text-gray-400">Searching...</div>}
                  {!createSearching && createResults.length === 0 && createQuery.length >= 2 && (
                    <div className="px-3 py-2 text-xs text-gray-400">No results found</div>
                  )}
                  {createResults.map((r, i) => (
                    <button key={i} type="button" onClick={() => handleCreateSelect(r)}
                      className="w-full text-left px-3 py-2 text-sm hover:bg-indigo-50 border-b last:border-b-0">
                      <span className="font-medium">{r.display_name}</span>
                      <span className="text-xs text-gray-400 ml-2">({r.latitude.toFixed(2)}, {r.longitude.toFixed(2)})</span>
                      <span className="text-xs text-indigo-500 ml-2">{r.timezone}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {createError && <p role="alert" className="text-sm text-red-600 mt-1">{createError}</p>}
            {createSelected && (
              <p className="text-xs text-green-600 mt-1">
                {createSelected.display_name} — Timezone: {createSelected.timezone}
              </p>
            )}
          </div>
          <button
            type="submit"
            disabled={createMutation.isPending}
            className="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded transition disabled:opacity-50"
          >
            {createMutation.isPending ? <><Spinner className="mr-2" />Processing...</> : 'Create Station'}
          </button>
        </form>
      )}

      <div className="bg-white shadow rounded-lg overflow-x-auto">
        <table className="phone-cards min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Timezone</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Plays from</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
              <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {data?.stations.map((station) => {
              const config = (station as any).automation_config || {};
              return (
              <React.Fragment key={station.id}>
              <tr className="cursor-pointer hover:bg-gray-50"
                onClick={() => setExpandedStation(expandedStation === station.id ? null : station.id)}>
                <td className="px-6 py-4 whitespace-nowrap font-medium">{station.name}</td>
                <td data-label="Type" className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{station.type}</td>
                <td data-label="Timezone" className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{station.timezone}</td>
                <td data-label="Plays from" className="px-6 py-4 whitespace-nowrap" data-testid="station-plays-from">
                  <PlayoutBadge stationId={station.id} chip />
                </td>
                <td data-label="Status" className="px-6 py-4 whitespace-nowrap">
                  <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                    station.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                  }`}>
                    {station.is_active ? 'Active' : 'Inactive'}
                  </span>
                  {isAdmin ? (
                    <label
                      onClick={(e) => e.stopPropagation()}
                      title="Protected: only admins and staff ticked for this station on the Users page can operate it. Use it for the FM feed."
                      className="ml-3 inline-flex items-center gap-1 text-xs text-gray-700"
                    >
                      <input
                        type="checkbox"
                        checked={!!station.protected}
                        onChange={(e) => updateMutation.mutate({ id: station.id, data: { protected: e.target.checked } })}
                      />
                      Protected
                    </label>
                  ) : station.protected ? (
                    <span className="ml-2 px-2 py-1 rounded-full text-xs font-medium bg-amber-100 text-amber-800">Protected</span>
                  ) : null}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-right">
                  <button
                    data-danger
                    onClick={(e) => { e.stopPropagation(); if (confirm('Delete this station? This cannot be undone.')) deleteMutation.mutate(station.id); }}
                    className="text-red-600 hover:text-red-800 text-sm"
                  >
                    Delete
                  </button>
                </td>
              </tr>
              {expandedStation === station.id && (
                <tr>
                  <td colSpan={6} data-testid="station-panel" className="px-6 py-4 bg-gray-50 space-y-6">
                    <LocationConfig
                      latitude={station.latitude}
                      longitude={station.longitude}
                      onSave={(lat, lon) => {
                        updateMutation.mutate({ id: station.id, data: { latitude: lat, longitude: lon } });
                      }}
                      saving={updateMutation.isPending}
                    />
                    <AutomationConfig
                      stationId={station.id}
                      config={config}
                      onSave={(newConfig) => {
                        updateMutation.mutate({ id: station.id, data: { automation_config: newConfig } });
                      }}
                      saving={updateMutation.isPending}
                    />
                  </td>
                </tr>
              )}
              </React.Fragment>
              );
            })}
            {data?.stations.length === 0 && (
              <tr>
                <td colSpan={6} className="px-6 py-10 text-center text-gray-500">
                  No stations yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface GeoResult {
  display_name: string;
  latitude: number;
  longitude: number;
  timezone: string;
}

function LocationConfig({ latitude, longitude, onSave, saving }: {
  latitude: number | null;
  longitude: number | null;
  onSave: (lat: number, lon: number) => void;
  saving: boolean;
}) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<GeoResult[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [searching, setSearching] = useState(false);
  const [selectedLat, setSelectedLat] = useState<number | null>(latitude);
  const [selectedLon, setSelectedLon] = useState<number | null>(longitude);
  const [selectedName, setSelectedName] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wrapperRef = useRef<HTMLDivElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleSearch = (value: string) => {
    setQuery(value);
    setShowDropdown(true);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (value.length < 2) {
      setResults([]);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      setSearching(true);
      try {
        const res = await apiClient.get('/stations/geocode', { params: { q: value } });
        setResults(res.data);
      } catch {
        setResults([]);
      }
      setSearching(false);
    }, 350);
  };

  const handleSelect = (result: GeoResult) => {
    setSelectedLat(result.latitude);
    setSelectedLon(result.longitude);
    setSelectedName(result.display_name);
    setQuery(result.display_name);
    setShowDropdown(false);
  };

  const handleSave = () => {
    if (selectedLat != null && selectedLon != null) {
      onSave(selectedLat, selectedLon);
    }
  };

  return (
    <div>
      <h4 className="text-sm font-bold text-gray-700 uppercase mb-3">Location</h4>
      {latitude != null && longitude != null && (
        <p className="text-xs text-gray-500 mb-2">
          Current: {latitude.toFixed(4)}, {longitude.toFixed(4)}
        </p>
      )}
      {latitude == null && (
        <p className="text-xs text-red-500 mb-2">
          No location set — required for weather/time announcements and sun-relative scheduling
        </p>
      )}
      <div ref={wrapperRef} className="relative max-w-md">
        <input
          type="text"
          value={query}
          onChange={e => handleSearch(e.target.value)}
          placeholder="Search for a city..."
          className="w-full px-3 py-2 border rounded text-sm"
        />
        {showDropdown && (query.length >= 2) && (
          <div className="absolute z-10 w-full bg-white border rounded-b shadow-lg max-h-48 overflow-y-auto">
            {searching && <div className="px-3 py-2 text-xs text-gray-400">Searching...</div>}
            {!searching && results.length === 0 && query.length >= 2 && (
              <div className="px-3 py-2 text-xs text-gray-400">No results found</div>
            )}
            {results.map((r, i) => (
              <button key={i} type="button"
                onClick={() => handleSelect(r)}
                className="w-full text-left px-3 py-2 text-sm hover:bg-indigo-50 border-b last:border-b-0">
                <span className="font-medium">{r.display_name}</span>
                <span className="text-xs text-gray-400 ml-2">({r.latitude.toFixed(2)}, {r.longitude.toFixed(2)})</span>
              </button>
            ))}
          </div>
        )}
      </div>
      {selectedName && selectedLat != null && (
        <p className="text-xs text-green-600 mt-1">
          Selected: {selectedName} ({selectedLat.toFixed(4)}, {selectedLon!.toFixed(4)})
        </p>
      )}
      <button onClick={handleSave} disabled={saving || selectedLat == null}
        className="mt-2 px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 text-sm disabled:opacity-50">
        {saving ? 'Saving...' : 'Save Location'}
      </button>
    </div>
  );
}

/** The item that plays after a Dump while the delay refills: search the library, pick one. */
function FillerPick({ value, onChange }: {
  value: { id: string; title: string } | null;
  onChange: (v: { id: string; title: string } | null) => void;
}) {
  return (
    <LibraryPick value={value} onChange={onChange} testId="live-filler" label="Filler after a Dump"
      placeholder="Search the library for a jingle" searchLabel="Search the library for a filler" clearLabel="No filler">
      A jingle or station ID that listeners hear right after a Dump, for as long as was cut, so the delay is full
      again when it ends and a second Dump works at once. Pick one at least as long as the delay; a shorter one
      plays once and the rest fills back slowly. With no filler the delay fills back slowly while the host talks.
      Remember to press Save.
    </LibraryPick>
  );
}

/** The coming Friday (today on a Friday) as YYYY-MM-DD, this device's calendar. */
function nextFriday(today: Date = new Date()): string {
  const d = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  d.setDate(d.getDate() + ((5 - d.getDay() + 7) % 7));
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function LibraryPick({ value, onChange, testId, label, placeholder, searchLabel, clearLabel, disabled, children }: {
  value: { id: string; title: string } | null;
  onChange: (v: { id: string; title: string } | null) => void;
  testId: string;
  label: string;
  placeholder: string;
  searchLabel: string;
  clearLabel: string;
  disabled?: boolean;
  children?: React.ReactNode;
}) {
  const [text, setText] = useState('');
  const [found, setFound] = useState<Asset[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const q = text.trim();
    if (q.length < 2) { setFound(null); setFailed(false); return; }
    let stale = false;
    const id = setTimeout(() => {
      listAssets({ search: q, limit: 8, hide_generated: true })
        .then(r => { if (!stale) { setFound(r.assets); setFailed(false); } })
        .catch(() => { if (!stale) { setFound(null); setFailed(true); } });
    }, 300);
    return () => { stale = true; clearTimeout(id); };
  }, [text]);
  const length = (a: Asset) => {
    const s = Math.round(Number(a.duration) || 0);
    return s ? ` (${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')})` : '';
  };
  return (
    <div className="mt-4 max-w-lg text-sm" data-testid={testId}>
      <span className="block font-medium text-gray-700">{label}</span>
      {value ? (
        <div className="mt-1 flex items-center gap-2">
          <span className="px-2 py-1 border rounded bg-gray-50" data-testid={`${testId}-chosen`}>{value.title}</span>
          <button type="button" onClick={() => onChange(null)} data-testid={`${testId}-clear`} disabled={disabled}
            className="shrink-0 whitespace-nowrap px-2 py-1 border rounded text-xs hover:bg-gray-100 disabled:opacity-50">{clearLabel}</button>
        </div>
      ) : (
        <>
          <input type="search" value={text} onChange={e => setText(e.target.value)} disabled={disabled}
            placeholder={placeholder} aria-label={searchLabel}
            className="mt-1 w-full px-2 py-1 border rounded" data-testid={`${testId}-search`} />
          {failed && <div className="mt-1 text-xs text-red-600">The library could not be searched. Try again.</div>}
          {found && found.length === 0 && <div className="mt-1 text-xs text-gray-500">Nothing found.</div>}
          {found && found.length > 0 && (
            <ul className="mt-1 border rounded divide-y" data-testid={`${testId}-results`}>
              {found.map(a => (
                <li key={a.id}>
                  <button type="button" className="w-full text-left px-2 py-1 hover:bg-gray-100"
                    onClick={() => { onChange({ id: String(a.id), title: a.title }); setText(''); setFound(null); }}>
                    {a.title}{length(a)}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {children && <span className="block mt-1 text-xs text-gray-500">{children}</span>}
    </div>
  );
}

interface OmerOverview { first_day: string; last_day: string; recorded: number; total: number }
interface OmerHour { at: string; local: string; omer_day: number | null; text: string | null; recorded: boolean; off_air: string | null }
interface StationOmer { enabled: boolean; switch_minutes: number; place_known: boolean; date: string; sunrise: string | null; shekia: string | null; switch_at: string | null; hours: OmerHour[] }

function longDate(iso: string | null | undefined): string {
  // an answer without the date (a fake or older backend) left the whole Stations page on "Something went wrong"
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
}

function OmerCount({ stationId, on, setOn, switchMinutes, setSwitchMinutes, savedSwitch }: {
  stationId: string;
  on: boolean;
  setOn: (v: boolean) => void;
  switchMinutes: string;
  setSwitchMinutes: (v: string) => void;
  savedSwitch: number;
}) {
  const [overview, setOverview] = useState<OmerOverview | null>(null);
  const [day, setDay] = useState<string>('');
  const [hours, setHours] = useState<StationOmer | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    apiClient.get<OmerOverview>('/omer-count').then(r => {
      if (!live) return;
      setOverview(r.data);
      const today = new Date();
      const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
      setDay(iso >= r.data.first_day && iso <= r.data.last_day ? iso : r.data.first_day);
    }).catch(() => live && setFailed(true));
    return () => { live = false; };
  }, []);
  useEffect(() => {
    if (!day) return;
    let live = true;
    setHours(null);
    apiClient.get<StationOmer>(`/omer-count/station/${stationId}`, { params: { on: day } })
      .then(r => live && setHours(r.data)).catch(() => live && setFailed(true));
    return () => { live = false; };
  }, [day, stationId, savedSwitch]);
  const spoken = hours?.hours.filter(h => h.text) ?? [];
  // Hours in a row that say the same thing (and are all silent or all on the air) as one line
  const runs: { first: OmerHour; last: OmerHour; count: number }[] = [];
  for (const h of spoken) {
    const prev = runs[runs.length - 1];
    if (prev && prev.last.text === h.text && !!prev.last.off_air === !!h.off_air && prev.last.recorded === h.recorded) {
      prev.last = h; prev.count += 1;
    } else runs.push({ first: h, last: h, count: 1 });
  }
  return (
    <div className="mt-4 max-w-lg text-sm" data-testid="omer-count">
      <label className="flex items-start gap-2">
        <input type="checkbox" checked={on} onChange={e => setOn(e.target.checked)} className="mt-1" data-testid="omer-count-on" />
        <span>
          <span className="block font-medium text-gray-700">Omer count on the hour</span>
          <span className="block mt-1 text-xs text-gray-500">
            During Sefiras HaOmer, right after each hour&apos;s time check: &ldquo;Today is the eighth day, which is one week
            and one day of the Omer.&rdquo; At shekia at the station&apos;s own place it moves on to the next day. Never on
            Shabbos or Yom Tov, and only days whose recording is uploaded.
          </span>
        </span>
      </label>
      <div className="mt-2 ml-6 flex flex-wrap items-center gap-2">
        <span>Switch to the next day</span>
        <input type="number" value={switchMinutes} min={0} max={90} step={1} disabled={!on}
          onChange={e => setSwitchMinutes(e.target.value)} aria-label="Minutes after shekia the Omer count moves to the next day"
          className="w-16 px-2 py-1 border rounded" data-testid="omer-count-switch" />
        <span>minutes after shekia (0 = at shekia)</span>
      </div>
      <div className="mt-2 ml-6 text-xs text-gray-600" data-testid="omer-count-status">
        {failed && <span className="text-red-600">The Omer count could not be loaded. Try again.</span>}
        {overview && (
          <span>
            Recordings uploaded: {overview.recorded} of {overview.total}. The count runs from the night before{' '}
            {longDate(overview.first_day)} to {longDate(overview.last_day)} (Erev Shavuos).
          </span>
        )}
      </div>
      {overview && (
        <div className="mt-2 ml-6">
          <label className="flex flex-wrap items-center gap-2">
            <span>What it says on</span>
            <input type="date" value={day} onChange={e => setDay(e.target.value)} className="px-2 py-1 border rounded"
              aria-label="Day to show the Omer count for" data-testid="omer-count-day" />
          </label>
          {hours && !hours.place_known && (
            <div className="mt-1 text-xs text-red-600">This station has no location, so it cannot tell when shekia is. Set its location above.</div>
          )}
          {hours && hours.shekia && (
            <div className="mt-1 text-xs text-gray-500">
              Shekia {hours.shekia}{hours.switch_minutes ? `, switch to the next day at ${hours.switch_at}` : ''} (saved setting).
            </div>
          )}
          {hours && spoken.length === 0 && <div className="mt-1 text-xs text-gray-500">No Omer count on this day.</div>}
          {spoken.length > 0 && (
            <ul className="mt-1 border rounded divide-y text-xs" data-testid="omer-count-hours">
              {runs.map(({ first, last, count }) => (
                <li key={first.at} className="px-2 py-1">
                  <span className="block font-medium">
                    {count > 1 ? `${first.local} to ${last.local}` : first.local}
                    {count > 1 && <span className="font-normal text-gray-500"> (every hour)</span>}
                  </span>
                  <span className={`block ${first.off_air ? 'text-gray-400 line-through' : ''}`}>{first.text}</span>
                  {first.off_air && <span className="block text-gray-500">Silent: {first.off_air}</span>}
                  {!first.off_air && !first.recorded && <span className="block text-amber-700">Not recorded yet, so nothing airs</span>}
                </li>
              ))}
            </ul>
          )}
          <span className="block mt-1 text-xs text-gray-500">Remember to press Save.</span>
        </div>
      )}
    </div>
  );
}

const HOURS = Array.from({ length: 25 }, (_, h) => h);
function hourLabel(h: number): string {
  const x = h % 24;
  return `${x % 12 === 0 ? 12 : x % 12} ${x < 12 ? 'AM' : 'PM'}`;
}

function AutomationConfig({ stationId, config, onSave, saving }: {
  stationId: string;
  config: Record<string, any>;
  onSave: (cfg: Record<string, any>) => void;
  saving: boolean;
}) {
  const [hourlyId, setHourlyId] = useState(config.hourly_station_id ?? false);
  const [hourlyTime, setHourlyTime] = useState(config.hourly_time_announcement ?? false);
  const [weatherOn, setWeatherOn] = useState(config.weather_enabled ?? false);
  const [weatherInterval, setWeatherInterval] = useState(config.weather_interval_minutes ?? 30);
  const [longItems, setLongItems] = useState<string>(config.timed_during_long_items ?? 'pause');
  const [crossfade, setCrossfade] = useState<string>(String(config.crossfade_seconds ?? 1));
  const ids = (config.station_ids ?? {}) as Record<string, any>;
  const [idsOn, setIdsOn] = useState<boolean>(ids.enabled === true);
  // Where they air: in the breaks (Shmuel 2026-11-19: "part of the ad blocks. Or once an hour.
  // Not randomly between songs"); between songs is the old way, kept as a choice
  const [idsWhere, setIdsWhere] = useState<string>(
    ['hourly', 'every_block', 'between_songs'].includes(ids.placement) ? ids.placement : 'hourly');
  const [idsMin, setIdsMin] = useState<string>(String(ids.min_minutes ?? 12));
  const [idsMax, setIdsMax] = useState<string>(String(ids.max_minutes ?? 20));
  const [idsCategory, setIdsCategory] = useState<string>(String(ids.category ?? 'station_id'));
  const [idsFrom, setIdsFrom] = useState<number>(Number(ids.start_hour ?? 0));
  const [idsTo, setIdsTo] = useState<number>(Number(ids.end_hour ?? 24));
  const [liveDelay, setLiveDelay] = useState<string>(String(config.live_delay_seconds ?? 15));
  const [liveDump, setLiveDump] = useState<string>(String(config.live_dump_seconds ?? 0));
  const [liveTimed, setLiveTimed] = useState<string>(config.live_timed_items ?? 'time');
  const [liveRecord, setLiveRecord] = useState<boolean>(config.live_record_shows !== false);
  const [filler, setFiller] = useState<{ id: string; title: string } | null>(
    config.live_dump_filler_asset_id
      ? { id: String(config.live_dump_filler_asset_id), title: String(config.live_dump_filler_title || 'Chosen item') }
      : null);
  const ending = (config.shabbos_ending ?? {}) as Record<string, any>;
  const [endingOn, setEndingOn] = useState<boolean>(ending.enabled === true);
  const [endingSong, setEndingSong] = useState<{ id: string; title: string } | null>(
    ending.song_asset_id ? { id: String(ending.song_asset_id), title: String(ending.song_title || 'Chosen song') } : null);
  const [endingAfter, setEndingAfter] = useState<{ id: string; title: string } | null>(
    ending.after_asset_id ? { id: String(ending.after_asset_id), title: String(ending.after_title || 'Chosen item') } : null);
  const [endingShabbos, setEndingShabbos] = useState<boolean>(ending.shabbos !== false);
  const [endingYomTov, setEndingYomTov] = useState<boolean>(ending.yom_tov === true);
  // This week's candle lighting announcement (a new recording each week), keyed by the Friday's date
  const [candleItems, setCandleItems] = useState<Record<string, { id: string; title: string }>>(() => {
    const ids = (ending.candle_items ?? {}) as Record<string, string>;
    const titles = (ending.candle_titles ?? {}) as Record<string, string>;
    const out: Record<string, { id: string; title: string }> = {};
    for (const [d, id] of Object.entries(ids)) if (id) out[d] = { id: String(id), title: String(titles[d] || 'Chosen item') };
    return out;
  });
  const comingFriday = nextFriday();
  const [candleWeekly, setCandleWeekly] = useState<{ id: string; title: string } | null>(
    ending.candle_every_week
      ? { id: String(ending.candle_every_week), title: String(ending.candle_every_week_title || 'Chosen item') }
      : null);
  const omerCfg = (config.omer_count ?? {}) as Record<string, any>;
  const [omerOn, setOmerOn] = useState<boolean>(omerCfg.enabled !== false);
  const [omerSwitch, setOmerSwitch] = useState<string>(String(omerCfg.switch_minutes ?? 0));

  const handleSave = () => {
    const minutes = (v: string, d: number) => Math.min(240, Math.max(3, Number.isFinite(parseFloat(v)) ? parseFloat(v) : d));
    const idsLow = minutes(idsMin, 12);
    onSave({
      ...config, // keep settings made elsewhere (program clock, weather region)
      timed_during_long_items: longItems,
      crossfade_seconds: Math.min(10, Math.max(0, Number.isFinite(parseFloat(crossfade)) ? parseFloat(crossfade) : 1)),
      live_delay_seconds: Math.min(40, Math.max(0, Number.isFinite(parseFloat(liveDelay)) ? parseFloat(liveDelay) : 15)),
      live_dump_seconds: Math.min(40, Math.max(0, Number.isFinite(parseFloat(liveDump)) ? parseFloat(liveDump) : 0)),
      live_timed_items: liveTimed,
      live_record_shows: liveRecord,
      live_dump_filler_asset_id: filler?.id ?? null,
      live_dump_filler_title: filler?.title ?? null,
      station_ids: {
        ...ids, // keep anything set elsewhere (clear_minutes)
        enabled: idsOn,
        placement: idsWhere,
        min_minutes: idsLow,
        max_minutes: Math.max(idsLow, minutes(idsMax, 20)),
        category: idsCategory.trim() || 'station_id',
        start_hour: idsFrom,
        end_hour: idsTo,
      },
      shabbos_ending: {
        ...ending,
        enabled: endingOn && !!endingSong,
        song_asset_id: endingSong?.id ?? null,
        song_title: endingSong?.title ?? null,
        after_asset_id: endingAfter?.id ?? null,
        after_title: endingAfter?.title ?? null,
        shabbos: endingShabbos,
        yom_tov: endingYomTov,
        candle_items: Object.fromEntries(Object.entries(candleItems).map(([d, v]) => [d, v.id])),
        candle_titles: Object.fromEntries(Object.entries(candleItems).map(([d, v]) => [d, v.title])),
        candle_every_week: candleWeekly?.id ?? null,
        candle_every_week_title: candleWeekly?.title ?? null,
      },
      omer_count: {
        ...omerCfg,
        enabled: omerOn,
        switch_minutes: Math.min(90, Math.max(0, Math.round(Number.isFinite(parseFloat(omerSwitch)) ? parseFloat(omerSwitch) : 0))),
      },
      hourly_station_id: hourlyId,
      hourly_time_announcement: hourlyTime,
      weather_enabled: weatherOn,
      weather_interval_minutes: weatherInterval,
    });
  };

  return (
    <div>
      <h4 className="text-sm font-bold text-gray-700 uppercase mb-3">Automation Config</h4>
      <div className="grid grid-cols-2 gap-4 max-w-lg">
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={hourlyId} onChange={e => setHourlyId(e.target.checked)} />
          Hourly Station ID Jingle
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={hourlyTime} onChange={e => setHourlyTime(e.target.checked)} />
          Hourly Time Announcement
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={weatherOn} onChange={e => setWeatherOn(e.target.checked)} />
          Weather Spots
        </label>
        <div className="flex items-center gap-2 text-sm">
          <label>Interval (min):</label>
          <input type="number" value={weatherInterval} min={5} max={120}
            onChange={e => setWeatherInterval(parseInt(e.target.value) || 30)}
            className="w-20 px-2 py-1 border rounded" disabled={!weatherOn} />
        </div>
      </div>
      <label className="mt-4 block max-w-lg text-sm">
        <span className="block font-medium text-gray-700">When the time check comes during a shiur or story</span>
        <select value={longItems} onChange={e => setLongItems(e.target.value)}
          className="mt-1 w-full px-2 py-1 border rounded" data-testid="timed-during-long-items">
          <option value="pause">Pause it, then carry on where it stopped</option>
          <option value="wait">Wait until it ends</option>
          <option value="cut">Cut it (the time check takes over)</option>
        </select>
        <span className="block mt-1 text-xs text-gray-500">Songs are always faded for the time check as before.</span>
      </label>
      <label className="mt-4 block max-w-lg text-sm">
        <span className="block font-medium text-gray-700">Song crossfade (seconds)</span>
        <input type="number" value={crossfade} min={0} max={10} step={0.5}
          onChange={e => setCrossfade(e.target.value)}
          className="mt-1 w-24 px-2 py-1 border rounded" data-testid="crossfade-seconds" />
        <span className="block mt-1 text-xs text-gray-500">
          How long the end of one song plays over the start of the next. 0 = no overlap: each song ends, then the next starts.
          Sponsorships, time checks, weather, shiurim and stories never overlap, whatever is set here.
        </span>
      </label>
      <div className="mt-4 max-w-lg text-sm">
        <label className="flex items-start gap-2">
          <input type="checkbox" checked={idsOn} onChange={e => setIdsOn(e.target.checked)}
            className="mt-1" data-testid="station-ids-on" />
          <span>
            <span className="block font-medium text-gray-700">Station IDs</span>
            <span className="block mt-1 text-xs text-gray-500">
              A station ID jingle, taking turns so each one is used before any repeats. Once an hour: last in the
              top-of-hour break (none after a shiur). In every break: last in each break. Between songs: the old way,
              every few minutes. Never on Shabbos or Yom Tov.
            </span>
          </span>
        </label>
        <div className="mt-2 ml-6 flex flex-wrap items-center gap-2">
          <span>Where</span>
          <select value={idsWhere} disabled={!idsOn} onChange={e => setIdsWhere(e.target.value)}
            aria-label="Where station IDs play" className="px-2 py-1 border rounded" data-testid="station-ids-where">
            <option value="hourly">Once an hour</option>
            <option value="every_block">In every break</option>
            <option value="between_songs">Between songs</option>
          </select>
        </div>
        <div className="mt-2 ml-6 flex flex-wrap items-center gap-2">
          <span>Every</span>
          <input type="number" value={idsMin} min={3} max={240} step={1} disabled={!idsOn || idsWhere !== 'between_songs'}
            onChange={e => setIdsMin(e.target.value)} aria-label="Fewest minutes between station IDs"
            className="w-16 px-2 py-1 border rounded" data-testid="station-ids-min" />
          <span>to</span>
          <input type="number" value={idsMax} min={3} max={240} step={1} disabled={!idsOn || idsWhere !== 'between_songs'}
            onChange={e => setIdsMax(e.target.value)} aria-label="Most minutes between station IDs"
            className="w-16 px-2 py-1 border rounded" data-testid="station-ids-max" />
          <span>minutes (between songs only), from jingles of category</span>
          <input type="text" value={idsCategory} disabled={!idsOn}
            onChange={e => setIdsCategory(e.target.value)} aria-label="Category of the station ID jingles"
            className="w-44 px-2 py-1 border rounded" data-testid="station-ids-category" />
        </div>
        <div className="mt-2 ml-6 flex flex-wrap items-center gap-2">
          <span>Play them from</span>
          <select value={idsFrom} disabled={!idsOn} onChange={e => setIdsFrom(Number(e.target.value))}
            aria-label="Station IDs start at" className="px-2 py-1 border rounded" data-testid="station-ids-from">
            {HOURS.slice(0, 24).map(h => <option key={h} value={h}>{hourLabel(h)}</option>)}
          </select>
          <span>until</span>
          <select value={idsTo} disabled={!idsOn} onChange={e => setIdsTo(Number(e.target.value))}
            aria-label="Station IDs stop at" className="px-2 py-1 border rounded" data-testid="station-ids-to">
            {HOURS.slice(1).map(h => <option key={h} value={h}>{hourLabel(h)}</option>)}
          </select>
          <span className="text-xs text-gray-500">(12 AM until 12 AM = all day; the station's own time)</span>
        </div>
      </div>
      <div className="mt-4 max-w-lg text-sm" data-testid="shabbos-ending">
        <label className="flex items-start gap-2">
          <input type="checkbox" checked={endingOn} onChange={e => setEndingOn(e.target.checked)}
            className="mt-1" data-testid="shabbos-ending-on" />
          <span>
            <span className="block font-medium text-gray-700">Last song before Shabbos</span>
            <span className="block mt-1 text-xs text-gray-500">
              This song is started at just the right moment so it ends exactly at candle lighting (the start of the
              station&apos;s Shabbos silence). The item after it (the shofar) plays right at candle lighting, then the
              silence. With a candle lighting announcement for that Friday, the announcement ends at candle lighting
              and the song just before it. Time checks, sponsorships and station IDs are kept out of its way. Needs a song.
            </span>
          </span>
        </label>
        <div className="ml-6">
          <LibraryPick value={endingSong} onChange={setEndingSong} testId="shabbos-ending-song" disabled={!endingOn}
            label="Song that ends at candle lighting" placeholder="Search the library for the song"
            searchLabel="Search the library for the last song before Shabbos" clearLabel="Change" />
          <LibraryPick value={endingAfter} onChange={setEndingAfter} testId="shabbos-ending-after" disabled={!endingOn}
            label="Then, at candle lighting" placeholder="Search the library (the shofar)"
            searchLabel="Search the library for the item at candle lighting" clearLabel="Nothing (silence right away)">
            Give it the category shabbos_ending in the Library so it never plays at any other time.
          </LibraryPick>
          <LibraryPick value={candleWeekly} onChange={setCandleWeekly} disabled={!endingOn}
            testId="shabbos-ending-weekly"
            label="Candle lighting item every Friday (the same recording)"
            placeholder="Search the library (the Good Shabbos sign-off)"
            searchLabel="Search the library for the item that ends at candle lighting every Friday"
            clearLabel="None">
            For a recording with no times in it. It ends exactly at candle lighting every week, the song just
            before it. A recording chosen below for one Friday takes its place that week. Category shabbos_ending.
          </LibraryPick>
          <LibraryPick value={candleItems[comingFriday] ?? null} disabled={!endingOn}
            onChange={v => setCandleItems(prev => {
              const next = { ...prev };
              if (v) next[comingFriday] = v; else delete next[comingFriday];
              return next;
            })}
            testId="shabbos-ending-candle"
            label={`Candle lighting announcement for Friday ${comingFriday}`}
            placeholder="Search the library (this week's recording)"
            searchLabel="Search the library for this week's candle lighting announcement" clearLabel="None this week">
            Optional, for a recording made for this week only (one that says this week&apos;s times). It ends
            exactly at candle lighting and the song ends just before it. A week with neither ends with the song.
          </LibraryPick>
          {Object.keys(candleItems).filter(d => d !== comingFriday).sort().map(d => (
            <div key={d} className="mt-1 flex flex-wrap items-center gap-2 text-xs text-gray-600"
              data-testid={`shabbos-ending-candle-${d}`}>
              <span>Friday {d}: {candleItems[d].title}</span>
              <button type="button" disabled={!endingOn} className="underline"
                onClick={() => setCandleItems(prev => { const n = { ...prev }; delete n[d]; return n; })}>
                Remove
              </button>
            </div>
          ))}
          <div className="mt-3 flex flex-wrap gap-4">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={endingShabbos} disabled={!endingOn}
                onChange={e => setEndingShabbos(e.target.checked)} data-testid="shabbos-ending-shabbos" />
              Erev Shabbos
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={endingYomTov} disabled={!endingOn}
                onChange={e => setEndingYomTov(e.target.checked)} data-testid="shabbos-ending-yomtov" />
              Erev Yom Tov
            </label>
          </div>
          <span className="block mt-1 text-xs text-gray-500">Remember to press Save.</span>
        </div>
      </div>
      <OmerCount stationId={stationId} on={omerOn} setOn={setOmerOn} switchMinutes={omerSwitch}
        setSwitchMinutes={setOmerSwitch} savedSwitch={Number(omerCfg.switch_minutes ?? 0)} />
      <h4 className="text-sm font-bold text-gray-700 uppercase mt-6 mb-2">Live shows</h4>
      <label className="block max-w-lg text-sm">
        <span className="block font-medium text-gray-700">Broadcast delay (seconds)</span>
        <input type="number" value={liveDelay} min={0} max={40} step={1}
          onChange={e => setLiveDelay(e.target.value)}
          className="mt-1 w-24 px-2 py-1 border rounded" data-testid="live-delay-seconds" />
        <span className="block mt-1 text-xs text-gray-500">
          Listeners hear a live show this many seconds after it is said, so the Dump button can cut a word
          before it airs. 15 to 20 is usual. 0 = no delay: really live, and nothing can be cut. Up to 40.
          The host can also change it during a show.
        </span>
      </label>
      <label className="mt-4 block max-w-lg text-sm">
        <span className="block font-medium text-gray-700">One Dump cuts (seconds)</span>
        <input type="number" value={liveDump} min={0} max={40} step={1}
          onChange={e => setLiveDump(e.target.value)}
          className="mt-1 w-24 px-2 py-1 border rounded" data-testid="live-dump-seconds" />
        <span className="block mt-1 text-xs text-gray-500">
          0 = everything waiting in the delay (safest). A smaller number, such as 8 of 20, leaves room for a second Dump
          right away, but only cuts what was said in the last 8 seconds.
        </span>
      </label>
      <FillerPick value={filler} onChange={setFiller} />
      <label className="mt-4 block max-w-lg text-sm">
        <span className="block font-medium text-gray-700">Timed items during a live show</span>
        <select value={liveTimed} onChange={e => setLiveTimed(e.target.value)}
          className="mt-1 w-full px-2 py-1 border rounded" data-testid="live-timed-items">
          <option value="time">The time check airs on time; the rest waits for the show to end</option>
          <option value="all">Every hard timed item airs on time (time check, weather, hard sponsorships)</option>
          <option value="none">Nothing interrupts the show</option>
        </select>
        <span className="block mt-1 text-xs text-gray-500">
          The item plays to listeners at its exact time while the host keeps talking; the host's words wait in the delay
          and air after it, so nothing is lost. Needs a delay above 0.
        </span>
      </label>
      <label className="mt-4 flex max-w-lg items-start gap-2 text-sm">
        <input type="checkbox" checked={liveRecord} onChange={e => setLiveRecord(e.target.checked)}
          className="mt-1" data-testid="live-record-shows" />
        <span>
          <span className="block font-medium text-gray-700">Record every live show</span>
          <span className="block mt-1 text-xs text-gray-500">
            The show is recorded as listeners heard it (anything dumped is not in it) and appears in Archives as a
            draft when it ends. Listen to it there and publish it.
          </span>
        </span>
      </label>
      <button onClick={handleSave} disabled={saving}
        className="mt-3 px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 text-sm disabled:opacity-50">
        {saving ? 'Saving...' : 'Save Automation Config'}
      </button>
    </div>
  );
}
