import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listSponsors, createSponsor, updateSponsor, deleteSponsor, Sponsor } from '../../api/sponsors';
import Spinner from '../../components/Spinner';

export default function Sponsors() {
  const queryClient = useQueryClient();
  const { data: sponsors, isLoading } = useQuery({ queryKey: ['sponsors'], queryFn: listSponsors });
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Sponsor | null>(null);

  const [form, setForm] = useState({
    name: '',
    length_seconds: 30,
    priority: 0,
    audio_file_path: '',
    insertion_policy: 'between_tracks' as 'between_tracks' | 'every_n_songs' | 'fixed_interval',
    hour_start: 6,
    hour_end: 22,
    max_per_hour: 4,
    songs_between: 6,
  });

  const createMut = useMutation({
    mutationFn: createSponsor,
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['sponsors'] }); setShowForm(false); },
  });

  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Sponsor> }) => updateSponsor(id, data),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['sponsors'] }); setShowForm(false); setEditing(null); },
  });

  const deleteMut = useMutation({
    mutationFn: deleteSponsor,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['sponsors'] }),
  });

  const resetForm = () => {
    setForm({ name: '', length_seconds: 30, priority: 0, audio_file_path: '', insertion_policy: 'between_tracks', hour_start: 6, hour_end: 22, max_per_hour: 4, songs_between: 6 });
    setEditing(null);
  };

  const handleEdit = (s: Sponsor) => {
    setEditing(s);
    setForm({
      name: s.name,
      length_seconds: s.length_seconds,
      priority: s.priority,
      audio_file_path: s.audio_file_path,
      insertion_policy: s.insertion_policy,
      hour_start: s.target_rules?.hour_start ?? 6,
      hour_end: s.target_rules?.hour_end ?? 22,
      max_per_hour: s.target_rules?.max_per_hour ?? 4,
      songs_between: s.target_rules?.songs_between ?? 6,
    });
    setShowForm(true);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const payload = {
      name: form.name,
      length_seconds: form.length_seconds,
      priority: form.priority,
      audio_file_path: form.audio_file_path,
      insertion_policy: form.insertion_policy,
      target_rules: {
        hour_start: form.hour_start,
        hour_end: form.hour_end,
        max_per_hour: form.max_per_hour,
        songs_between: form.songs_between,
      },
    };

    if (editing) {
      updateMut.mutate({ id: editing.id, data: payload });
    } else {
      createMut.mutate(payload);
    }
  };

  if (isLoading) return <div className="p-8">Loading...</div>;

  return (
    <div className="max-w-4xl mx-auto p-8">
      <div className="flex justify-between items-center mb-6">
        <div className="flex items-center gap-4">
          <h1 className="text-3xl font-bold">Sponsors & Ads</h1>
          <Link to="/sponsor/login" className="text-sm text-indigo-600 hover:text-indigo-800 border border-indigo-200 px-3 py-1 rounded-lg hover:bg-indigo-50 transition">
            Client Portal
          </Link>
        </div>
        <button onClick={() => { resetForm(); setShowForm(!showForm); }}
          className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700">
          {showForm ? 'Cancel' : 'New Sponsor'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="bg-white p-6 rounded-lg shadow-md mb-6 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Name</label>
              <input required type="text" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
                className="w-full px-4 py-2 border rounded-lg" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Audio File Path</label>
              <input required type="text" value={form.audio_file_path}
                onChange={e => setForm({ ...form, audio_file_path: e.target.value })}
                className="w-full px-4 py-2 border rounded-lg" placeholder="sponsors/spot.mp3" />
            </div>
          </div>
          <div className="grid grid-cols-4 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Length (sec)</label>
              <input type="number" value={form.length_seconds}
                onChange={e => setForm({ ...form, length_seconds: parseFloat(e.target.value) })}
                className="w-full px-4 py-2 border rounded-lg" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Priority</label>
              <input type="number" value={form.priority}
                onChange={e => setForm({ ...form, priority: parseInt(e.target.value) })}
                className="w-full px-4 py-2 border rounded-lg" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Insertion Policy</label>
              <select value={form.insertion_policy}
                onChange={e => setForm({ ...form, insertion_policy: e.target.value as any })}
                className="w-full px-4 py-2 border rounded-lg">
                <option value="between_tracks">Between Tracks</option>
                <option value="every_n_songs">Every N Songs</option>
                <option value="fixed_interval">Fixed Interval</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Songs Between</label>
              <input type="number" value={form.songs_between}
                onChange={e => setForm({ ...form, songs_between: parseInt(e.target.value) })}
                className="w-full px-4 py-2 border rounded-lg" />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1">Active Hours (start)</label>
              <input type="number" min={0} max={23} value={form.hour_start}
                onChange={e => setForm({ ...form, hour_start: parseInt(e.target.value) })}
                className="w-full px-4 py-2 border rounded-lg" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Active Hours (end)</label>
              <input type="number" min={0} max={24} value={form.hour_end}
                onChange={e => setForm({ ...form, hour_end: parseInt(e.target.value) })}
                className="w-full px-4 py-2 border rounded-lg" />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1">Max per Hour</label>
              <input type="number" value={form.max_per_hour}
                onChange={e => setForm({ ...form, max_per_hour: parseInt(e.target.value) })}
                className="w-full px-4 py-2 border rounded-lg" />
            </div>
          </div>
          <button type="submit" disabled={createMut.isPending || updateMut.isPending}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50">
            {createMut.isPending || updateMut.isPending
              ? <><Spinner className="mr-2" />Processing...</>
              : editing ? 'Update' : 'Create'}
          </button>
        </form>
      )}

      <div className="space-y-3">
        {sponsors?.length === 0 && <p className="text-gray-500 text-center py-8">No sponsors configured</p>}
        {sponsors?.map(s => (
          <div key={s.id} className="bg-white p-4 rounded-lg shadow-md flex justify-between items-center">
            <div>
              <h3 className="font-bold">{s.name}</h3>
              <p className="text-sm text-gray-600">
                {s.length_seconds}s · Priority {s.priority} · {s.insertion_policy.replace(/_/g, ' ')}
              </p>
              <p className="text-xs text-gray-400">
                {s.target_rules?.hour_start ?? 0}:00-{s.target_rules?.hour_end ?? 24}:00
                · max {s.target_rules?.max_per_hour ?? '?'}/hr
                · every {s.target_rules?.songs_between ?? '?'} songs
              </p>
            </div>
            <div className="flex gap-2">
              <button onClick={() => handleEdit(s)} className="px-3 py-1 bg-blue-100 text-blue-700 rounded hover:bg-blue-200">Edit</button>
              <button onClick={() => { if (confirm('Delete?')) deleteMut.mutate(s.id); }}
                className="px-3 py-1 bg-red-100 text-red-700 rounded hover:bg-red-200">Delete</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
