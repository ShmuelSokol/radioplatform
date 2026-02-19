import React, { useState, useEffect, useRef } from 'react';
import { useStations, useCreateStation, useUpdateStation, useDeleteStation } from '../../hooks/useStations';
import apiClient from '../../api/client';
import Spinner from '../../components/Spinner';

export default function Stations() {
  const { data, isLoading } = useStations();
  const createMutation = useCreateStation();
  const updateMutation = useUpdateStation();
  const deleteMutation = useDeleteStation();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [timezone, setTimezone] = useState('UTC');
  const [expandedStation, setExpandedStation] = useState<string | null>(null);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    await createMutation.mutateAsync({ name, timezone });
    setName('');
    setTimezone('UTC');
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
            <label className="block text-sm font-medium text-gray-700 mb-1">Timezone</label>
            <input
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              className="w-full border rounded px-3 py-2"
            />
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
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Name</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Timezone</th>
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
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{station.type}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{station.timezone}</td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <span className={`px-2 py-1 rounded-full text-xs font-medium ${
                    station.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                  }`}>
                    {station.is_active ? 'Active' : 'Inactive'}
                  </span>
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-right">
                  <button
                    onClick={(e) => { e.stopPropagation(); deleteMutation.mutate(station.id); }}
                    className="text-red-600 hover:text-red-800 text-sm"
                  >
                    Delete
                  </button>
                </td>
              </tr>
              {expandedStation === station.id && (
                <tr>
                  <td colSpan={5} className="px-6 py-4 bg-gray-50 space-y-6">
                    <LocationConfig
                      latitude={station.latitude}
                      longitude={station.longitude}
                      onSave={(lat, lon) => {
                        updateMutation.mutate({ id: station.id, data: { latitude: lat, longitude: lon } });
                      }}
                      saving={updateMutation.isPending}
                    />
                    <AutomationConfig
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
                <td colSpan={5} className="px-6 py-10 text-center text-gray-500">
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

function AutomationConfig({ config, onSave, saving }: {
  config: Record<string, any>;
  onSave: (cfg: Record<string, any>) => void;
  saving: boolean;
}) {
  const [hourlyId, setHourlyId] = useState(config.hourly_station_id ?? false);
  const [hourlyTime, setHourlyTime] = useState(config.hourly_time_announcement ?? false);
  const [weatherOn, setWeatherOn] = useState(config.weather_enabled ?? false);
  const [weatherInterval, setWeatherInterval] = useState(config.weather_interval_minutes ?? 30);

  const handleSave = () => {
    onSave({
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
      <button onClick={handleSave} disabled={saving}
        className="mt-3 px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 text-sm disabled:opacity-50">
        {saving ? 'Saving...' : 'Save Automation Config'}
      </button>
    </div>
  );
}
