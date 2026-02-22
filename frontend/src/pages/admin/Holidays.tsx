import React, { useState, useMemo } from 'react';
import { useHolidays, useCreateHoliday, useUpdateHoliday, useDeleteHoliday } from '../../hooks/useHolidays';
import { useStations } from '../../hooks/useStations';
import { HolidayWindow, HolidayFilters } from '../../api/holidays';
import Spinner from '../../components/Spinner';

const REASON_OPTIONS = [
  'Shabbos', 'Rosh Hashanah', 'Yom Kippur', 'Sukkot',
  'Shemini Atzeret', 'Pesach', 'Shavuot', 'Manual',
] as const;

const REASON_COLORS: Record<string, string> = {
  'Shabbos': 'bg-blue-100 text-blue-800',
  'Rosh Hashanah': 'bg-orange-100 text-orange-800',
  'Yom Kippur': 'bg-red-100 text-red-800',
  'Sukkot': 'bg-green-100 text-green-800',
  'Shemini Atzeret': 'bg-teal-100 text-teal-800',
  'Pesach': 'bg-purple-100 text-purple-800',
  'Shavuot': 'bg-indigo-100 text-indigo-800',
  'Manual': 'bg-gray-100 text-gray-700',
};

function formatDt(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', timeZoneName: 'short',
  });
}

function durationHours(start: string, end: string): string {
  const ms = new Date(end).getTime() - new Date(start).getTime();
  return (ms / 3600000).toFixed(1) + 'h';
}

function getStatus(start: string, end: string): 'upcoming' | 'active' | 'past' {
  const now = Date.now();
  if (new Date(start).getTime() > now) return 'upcoming';
  if (new Date(end).getTime() > now) return 'active';
  return 'past';
}

export default function Holidays() {
  const { data: stationsData } = useStations();
  const stationList = stationsData?.stations || [];

  // Filters
  const [reasonFilter, setReasonFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('active_upcoming');
  const [stationFilter, setStationFilter] = useState('');

  const filters: HolidayFilters = useMemo(() => ({
    limit: 500,
    reason: reasonFilter || undefined,
    status: statusFilter || undefined,
    station_id: stationFilter || undefined,
  }), [reasonFilter, statusFilter, stationFilter]);

  const { data, isLoading } = useHolidays(filters);
  const holidays = data?.holidays || [];

  // Form state
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<HolidayWindow | null>(null);
  const [form, setForm] = useState({
    name: '',
    reason: '',
    start_datetime: '',
    end_datetime: '',
    is_blackout: true,
    affected_station_ids: [] as string[],
    all_stations: true,
  });

  const createMut = useCreateHoliday();
  const updateMut = useUpdateHoliday();
  const deleteMut = useDeleteHoliday();

  const resetForm = () => {
    setForm({ name: '', reason: '', start_datetime: '', end_datetime: '', is_blackout: true, affected_station_ids: [], all_stations: true });
    setEditing(null);
  };

  const handleEdit = (h: HolidayWindow) => {
    setEditing(h);
    setForm({
      name: h.name,
      reason: h.reason || '',
      start_datetime: h.start_datetime.slice(0, 16),
      end_datetime: h.end_datetime.slice(0, 16),
      is_blackout: h.is_blackout,
      affected_station_ids: h.affected_stations?.station_ids || [],
      all_stations: !h.affected_stations,
    });
    setShowForm(true);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const payload = {
      name: form.name,
      reason: form.reason || null,
      start_datetime: new Date(form.start_datetime).toISOString(),
      end_datetime: new Date(form.end_datetime).toISOString(),
      is_blackout: form.is_blackout,
      affected_stations: form.all_stations ? null : { station_ids: form.affected_station_ids },
      replacement_content: null,
    };

    if (editing) {
      updateMut.mutate({ id: editing.id, data: payload }, {
        onSuccess: () => { setShowForm(false); resetForm(); },
      });
    } else {
      createMut.mutate(payload as any, {
        onSuccess: () => { setShowForm(false); resetForm(); },
      });
    }
  };

  const toggleStation = (id: string) => {
    setForm(f => ({
      ...f,
      affected_station_ids: f.affected_station_ids.includes(id)
        ? f.affected_station_ids.filter(s => s !== id)
        : [...f.affected_station_ids, id],
    }));
  };

  return (
    <div className="max-w-6xl mx-auto p-8">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-3xl font-bold">Sabbath & Holiday Blackouts</h1>
        <button
          onClick={() => { resetForm(); setShowForm(!showForm); }}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
        >
          {showForm ? 'Cancel' : 'Add Blackout'}
        </button>
      </div>

      {/* Filter Bar */}
      <div className="bg-white p-4 rounded-lg shadow-md mb-4 flex flex-wrap gap-4 items-end">
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">Reason</label>
          <select value={reasonFilter} onChange={e => setReasonFilter(e.target.value)}
            className="px-3 py-2 border rounded-lg text-sm">
            <option value="">All</option>
            {REASON_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">Status</label>
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
            className="px-3 py-2 border rounded-lg text-sm">
            <option value="">All</option>
            <option value="active_upcoming">Active & Upcoming</option>
            <option value="active">Active</option>
            <option value="upcoming">Upcoming</option>
            <option value="past">Past</option>
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1">Station</label>
          <select value={stationFilter} onChange={e => setStationFilter(e.target.value)}
            className="px-3 py-2 border rounded-lg text-sm">
            <option value="">All Stations</option>
            {stationList.map((s: any) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>
        {data && (
          <div className="text-sm text-gray-500 ml-auto self-end pb-2">
            {data.total} result{data.total !== 1 ? 's' : ''}
          </div>
        )}
      </div>

      {/* Create/Edit Form */}
      {showForm && (
        <form onSubmit={handleSubmit} className="bg-white p-6 rounded-lg shadow-md mb-4 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Name</label>
              <input required type="text" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
                className="w-full px-4 py-2 border rounded-lg" placeholder="e.g. Shabbos Feb 20, 2026" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Reason</label>
              <select value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })}
                className="w-full px-4 py-2 border rounded-lg">
                <option value="">Auto-detect from name</option>
                {REASON_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Music Off (Start)</label>
              <input required type="datetime-local" value={form.start_datetime}
                onChange={e => setForm({ ...form, start_datetime: e.target.value })}
                className="w-full px-4 py-2 border rounded-lg" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Music On (End)</label>
              <input required type="datetime-local" value={form.end_datetime}
                onChange={e => setForm({ ...form, end_datetime: e.target.value })}
                className="w-full px-4 py-2 border rounded-lg" />
            </div>
          </div>
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={form.is_blackout} onChange={e => setForm({ ...form, is_blackout: e.target.checked })} />
              <span className="text-sm">Full blackout (silence)</span>
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={form.all_stations} onChange={e => setForm({ ...form, all_stations: e.target.checked })} />
              <span className="text-sm">All stations</span>
            </label>
          </div>
          {!form.all_stations && (
            <div>
              <label className="block text-sm font-medium mb-1">Affected Stations</label>
              <div className="flex flex-wrap gap-2">
                {stationList.map((s: any) => (
                  <button key={s.id} type="button" onClick={() => toggleStation(s.id)}
                    className={`px-3 py-1 rounded-full text-sm ${form.affected_station_ids.includes(s.id)
                      ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700'}`}>
                    {s.name}
                  </button>
                ))}
              </div>
            </div>
          )}
          <button type="submit" disabled={createMut.isPending || updateMut.isPending}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50">
            {createMut.isPending || updateMut.isPending
              ? <><Spinner className="mr-2" />Saving...</>
              : editing ? 'Update' : 'Create'}
          </button>
        </form>
      )}

      {/* Data Table */}
      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner /></div>
      ) : holidays.length === 0 ? (
        <p className="text-gray-500 text-center py-12">No blackout windows found</p>
      ) : (
        <div className="bg-white rounded-lg shadow-md overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Reason</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Name</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Music Off</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Music On</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600">Duration</th>
                  <th className="text-center px-4 py-3 font-medium text-gray-600">Status</th>
                  <th className="text-right px-4 py-3 font-medium text-gray-600">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {holidays.map(h => {
                  const status = getStatus(h.start_datetime, h.end_datetime);
                  const reasonClass = REASON_COLORS[h.reason || 'Manual'] || REASON_COLORS['Manual'];
                  return (
                    <tr key={h.id} className="hover:bg-gray-50">
                      <td className="px-4 py-2.5">
                        <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${reasonClass}`}>
                          {h.reason || 'Manual'}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 font-medium text-gray-900">{h.name}</td>
                      <td className="px-4 py-2.5 text-gray-600">{formatDt(h.start_datetime)}</td>
                      <td className="px-4 py-2.5 text-gray-600">{formatDt(h.end_datetime)}</td>
                      <td className="px-4 py-2.5 text-right text-gray-600">{durationHours(h.start_datetime, h.end_datetime)}</td>
                      <td className="px-4 py-2.5 text-center">
                        {status === 'active' && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
                            <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
                            Active
                          </span>
                        )}
                        {status === 'upcoming' && (
                          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">Upcoming</span>
                        )}
                        {status === 'past' && (
                          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-500">Past</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <button onClick={() => handleEdit(h)}
                          className="p-1.5 text-gray-400 hover:text-blue-600 rounded hover:bg-blue-50" title="Edit">
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                          </svg>
                        </button>
                        <button onClick={() => { if (confirm('Delete this blackout window?')) deleteMut.mutate(h.id); }}
                          className="p-1.5 text-gray-400 hover:text-red-600 rounded hover:bg-red-50 ml-1" title="Delete">
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                          </svg>
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
