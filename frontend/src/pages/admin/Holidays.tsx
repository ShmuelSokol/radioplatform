import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listHolidays, createHoliday, updateHoliday, deleteHoliday, HolidayWindow } from '../../api/holidays';
import { useStations } from '../../hooks/useStations';
import Spinner from '../../components/Spinner';

export default function Holidays() {
  const queryClient = useQueryClient();
  const { data: holidays, isLoading } = useQuery({ queryKey: ['holidays'], queryFn: listHolidays });
  const { data: stations } = useStations();
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<HolidayWindow | null>(null);

  const [form, setForm] = useState({
    name: '',
    start_datetime: '',
    end_datetime: '',
    is_blackout: true,
    affected_station_ids: [] as string[],
    all_stations: true,
  });

  const createMut = useMutation({
    mutationFn: createHoliday,
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['holidays'] }); setShowForm(false); },
  });

  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<HolidayWindow> }) => updateHoliday(id, data),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['holidays'] }); setShowForm(false); setEditing(null); },
  });

  const deleteMut = useMutation({
    mutationFn: deleteHoliday,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['holidays'] }),
  });

  const resetForm = () => {
    setForm({ name: '', start_datetime: '', end_datetime: '', is_blackout: true, affected_station_ids: [], all_stations: true });
    setEditing(null);
  };

  const handleEdit = (h: HolidayWindow) => {
    setEditing(h);
    const stationIds = h.affected_stations?.station_ids || [];
    setForm({
      name: h.name,
      start_datetime: h.start_datetime.slice(0, 16),
      end_datetime: h.end_datetime.slice(0, 16),
      is_blackout: h.is_blackout,
      affected_station_ids: stationIds,
      all_stations: !h.affected_stations,
    });
    setShowForm(true);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const payload = {
      name: form.name,
      start_datetime: new Date(form.start_datetime).toISOString(),
      end_datetime: new Date(form.end_datetime).toISOString(),
      is_blackout: form.is_blackout,
      affected_stations: form.all_stations ? null : { station_ids: form.affected_station_ids },
      replacement_content: null,
    };

    if (editing) {
      updateMut.mutate({ id: editing.id, data: payload });
    } else {
      createMut.mutate(payload);
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

  if (isLoading) return <div className="p-8">Loading...</div>;

  return (
    <div className="max-w-4xl mx-auto p-8">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-3xl font-bold">Sabbath & Holiday Blackouts</h1>
        <button
          onClick={() => { resetForm(); setShowForm(!showForm); }}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
        >
          {showForm ? 'Cancel' : 'New Blackout'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-white p-6 rounded-lg shadow-md mb-6 space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">Name</label>
            <input required type="text" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
              className="w-full px-4 py-2 border rounded-lg" placeholder="e.g. Shabbat, Yom Kippur" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Start</label>
              <input required type="datetime-local" value={form.start_datetime}
                onChange={e => setForm({ ...form, start_datetime: e.target.value })}
                className="w-full px-4 py-2 border rounded-lg" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">End</label>
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
                {stations?.stations?.map((s: any) => (
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
              ? <><Spinner className="mr-2" />Processing...</>
              : editing ? 'Update' : 'Create'}
          </button>
        </form>
      )}

      <div className="space-y-3">
        {holidays?.length === 0 && <p className="text-gray-500 text-center py-8">No blackout windows configured</p>}
        {holidays?.map(h => {
          const isActive = new Date(h.start_datetime) <= new Date() && new Date(h.end_datetime) > new Date();
          return (
            <div key={h.id} className="bg-white p-4 rounded-lg shadow-md flex justify-between items-center">
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-bold">{h.name}</h3>
                  {isActive && <span className="px-2 py-0.5 bg-red-100 text-red-700 text-xs rounded-full">Active</span>}
                </div>
                <p className="text-sm text-gray-600">
                  {new Date(h.start_datetime).toLocaleString()} — {new Date(h.end_datetime).toLocaleString()}
                </p>
                <p className="text-xs text-gray-400">
                  {h.affected_stations ? `${h.affected_stations.station_ids.length} station(s)` : 'All stations'}
                  {h.is_blackout ? ' · Blackout' : ' · Override'}
                </p>
              </div>
              <div className="flex gap-2">
                <button onClick={() => handleEdit(h)} className="px-3 py-1 bg-blue-100 text-blue-700 rounded hover:bg-blue-200">Edit</button>
                <button onClick={() => { if (confirm('Delete?')) deleteMut.mutate(h.id); }}
                  className="px-3 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200">Delete</button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
