import { useState } from 'react';
import { useRules, useCreateRule, useUpdateRule, useDeleteRule, useSchedulePreview } from '../../hooks/useRules';
import { useStations } from '../../hooks/useStations';
import type { ScheduleRule } from '../../types';
import Spinner from '../../components/Spinner';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const TYPE_COLORS: Record<string, string> = {
  music: 'bg-cyan-900 text-cyan-300', spot: 'bg-orange-900 text-orange-300',
  shiur: 'bg-purple-900 text-purple-300', jingle: 'bg-yellow-900 text-yellow-300',
  zmanim: 'bg-green-900 text-green-300',
};

export default function Rules() {
  const { data, isLoading } = useRules();
  const { data: stationsData } = useStations();
  const stations = stationsData?.stations ?? [];
  const createMut = useCreateRule();
  const updateMut = useUpdateRule();
  const deleteMut = useDeleteRule();

  const [showForm, setShowForm] = useState(false);
  const [editId, setEditId] = useState<string | null>(null);
  const [form, setForm] = useState<Partial<ScheduleRule>>({
    name: '', description: '', rule_type: 'rotation', asset_type: 'music',
    category: '', hour_start: 0, hour_end: 24, days_of_week: '0,1,2,3,4,5,6',
    interval_minutes: undefined, songs_between: undefined, priority: 10, is_active: true,
    station_id: null,
  });

  // Schedule preview
  const today = new Date().toISOString().slice(0, 10);
  const [previewDate, setPreviewDate] = useState(today);
  const [showPreview, setShowPreview] = useState(false);
  const { data: preview, isLoading: previewLoading } = useSchedulePreview(showPreview ? previewDate : null);

  const rules = data?.rules ?? [];

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const payload = {
      ...form,
      category: form.category || undefined,
      interval_minutes: form.interval_minutes || undefined,
      songs_between: form.songs_between || undefined,
    };
    if (editId) {
      updateMut.mutate({ id: editId, data: payload }, { onSuccess: () => resetForm() });
    } else {
      createMut.mutate(payload, { onSuccess: () => resetForm() });
    }
  };

  const resetForm = () => {
    setShowForm(false);
    setEditId(null);
    setForm({
      name: '', description: '', rule_type: 'rotation', asset_type: 'music',
      category: '', hour_start: 0, hour_end: 24, days_of_week: '0,1,2,3,4,5,6',
      interval_minutes: undefined, songs_between: undefined, priority: 10, is_active: true,
      station_id: null,
    });
  };

  const startEdit = (r: ScheduleRule) => {
    setEditId(r.id);
    setForm({ ...r });
    setShowForm(true);
  };

  return (
    <div className="-mx-4 sm:-mx-6 lg:-mx-8 -my-6 bg-[#080820] font-mono text-[13px] min-h-[calc(100vh-4rem)] flex flex-col text-white p-4 overflow-y-auto">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-cyan-300">Schedule Rules</h1>
        <div className="flex gap-2">
          <button onClick={() => setShowPreview(!showPreview)}
            className="px-3 py-1 bg-purple-700 hover:bg-purple-600 text-white text-sm rounded">
            {showPreview ? 'Hide Preview' : 'Preview Schedule'}
          </button>
          <button onClick={() => { resetForm(); setShowForm(!showForm); }}
            className="px-3 py-1 bg-green-700 hover:bg-green-600 text-white text-sm rounded">
            {showForm ? 'Cancel' : '+ New Rule'}
          </button>
        </div>
      </div>

      {/* Rule Form */}
      {showForm && (
        <form onSubmit={handleSubmit} className="bg-[#12123a] border border-[#2a2a5e] rounded p-4 mb-4 grid grid-cols-3 gap-3">
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Rule Name</label>
            <input value={form.name ?? ''} onChange={e => setForm({ ...form, name: e.target.value })} required
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Rule Type</label>
            <select value={form.rule_type ?? 'rotation'} onChange={e => setForm({ ...form, rule_type: e.target.value })}
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700">
              <option value="rotation">Rotation (every N songs)</option>
              <option value="interval">Interval (every N minutes)</option>
              <option value="fixed_time">Fixed Time (on the hour)</option>
              <option value="daypart">Daypart (time range)</option>
            </select>
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Asset Type</label>
            <select value={form.asset_type ?? 'music'} onChange={e => setForm({ ...form, asset_type: e.target.value })}
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700">
              <option value="music">Music</option>
              <option value="spot">Spot</option>
              <option value="shiur">Shiur</option>
              <option value="jingle">Jingle</option>
              <option value="zmanim">Zmanim</option>
            </select>
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Category (optional)</label>
            <input value={form.category ?? ''} onChange={e => setForm({ ...form, category: e.target.value })}
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Hours ({form.hour_start}:00 — {form.hour_end}:00)</label>
            <div className="flex gap-2">
              <input type="number" min={0} max={24} value={form.hour_start ?? 0}
                onChange={e => setForm({ ...form, hour_start: +e.target.value })}
                className="w-1/2 bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
              <input type="number" min={0} max={24} value={form.hour_end ?? 24}
                onChange={e => setForm({ ...form, hour_end: +e.target.value })}
                className="w-1/2 bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
            </div>
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Priority (higher = more important)</label>
            <input type="number" min={1} max={100} value={form.priority ?? 10}
              onChange={e => setForm({ ...form, priority: +e.target.value })}
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
          </div>
          {form.rule_type === 'interval' && (
            <div>
              <label className="block text-[11px] text-gray-400 mb-1">Interval (minutes)</label>
              <input type="number" min={1} value={form.interval_minutes ?? ''}
                onChange={e => setForm({ ...form, interval_minutes: +e.target.value || undefined })}
                className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
            </div>
          )}
          {form.rule_type === 'rotation' && (
            <div>
              <label className="block text-[11px] text-gray-400 mb-1">Songs Between</label>
              <input type="number" min={1} value={form.songs_between ?? ''}
                onChange={e => setForm({ ...form, songs_between: +e.target.value || undefined })}
                className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
            </div>
          )}
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Days</label>
            <div className="flex gap-1 flex-wrap">
              {DAYS.map((d, i) => {
                const selected = (form.days_of_week ?? '').split(',').includes(String(i));
                return (
                  <button key={d} type="button" onClick={() => {
                    const current = (form.days_of_week ?? '').split(',').filter(Boolean);
                    const si = String(i);
                    const next = selected ? current.filter(x => x !== si) : [...current, si];
                    setForm({ ...form, days_of_week: next.sort().join(',') });
                  }}
                    className={`px-1.5 py-0.5 text-[10px] rounded ${
                      selected ? 'bg-cyan-700 text-white' : 'bg-[#0a0a28] text-gray-500'}`}>
                    {d}
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Description</label>
            <input value={form.description ?? ''} onChange={e => setForm({ ...form, description: e.target.value })}
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700" />
          </div>
          <div>
            <label className="block text-[11px] text-gray-400 mb-1">Station (blank = global)</label>
            <select value={form.station_id ?? ''} onChange={e => setForm({ ...form, station_id: e.target.value || null })}
              className="w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700">
              <option value="">Global (all stations)</option>
              {stations.map(s => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          </div>
          <div className="col-span-3 flex gap-2">
            <button type="submit" disabled={createMut.isPending || updateMut.isPending}
              className="px-4 py-1.5 bg-cyan-700 hover:bg-cyan-600 text-white rounded text-sm disabled:opacity-50">
              {createMut.isPending || updateMut.isPending
                ? <><Spinner className="mr-2" />Processing...</>
                : editId ? 'Update Rule' : 'Create Rule'}
            </button>
            <label className="flex items-center gap-1 text-[11px] text-gray-400">
              <input type="checkbox" checked={form.is_active ?? true}
                onChange={e => setForm({ ...form, is_active: e.target.checked })} />
              Active
            </label>
          </div>
        </form>
      )}

      {/* Rules Table */}
      {isLoading ? (
        <div className="text-gray-500 text-center py-8">Loading rules...</div>
      ) : (
        <div className="bg-[#0a0a28] border border-[#2a2a5e] rounded overflow-x-auto mb-4">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-[#16163e] text-[10px] text-gray-500 uppercase">
                <th className="text-left px-3 py-2">Name</th>
                <th className="text-left px-3 py-2">Station</th>
                <th className="text-left px-3 py-2">Type</th>
                <th className="text-left px-3 py-2">Content</th>
                <th className="text-left px-3 py-2">Hours</th>
                <th className="text-left px-3 py-2">Days</th>
                <th className="text-left px-3 py-2">Freq</th>
                <th className="text-left px-3 py-2">Pri</th>
                <th className="text-left px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rules.map(r => (
                <tr key={r.id} className={`border-t border-[#1a1a3e] hover:bg-[#14143a] ${!r.is_active ? 'opacity-40' : ''}`}>
                  <td className="px-3 py-1.5 text-cyan-300">{r.name}</td>
                  <td className="px-3 py-1.5 text-[10px]">
                    {r.station_id
                      ? <span className="px-1.5 py-0.5 bg-indigo-900 text-indigo-300 rounded">
                          {stations.find(s => s.id === r.station_id)?.name ?? 'Station'}
                        </span>
                      : <span className="text-gray-600">global</span>
                    }
                  </td>
                  <td className="px-3 py-1.5 text-gray-400 text-[11px]">{r.rule_type}</td>
                  <td className="px-3 py-1.5">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${TYPE_COLORS[r.asset_type] ?? 'bg-gray-800 text-gray-300'}`}>
                      {r.asset_type}
                    </span>
                    {r.category && <span className="text-[10px] text-gray-500 ml-1">/ {r.category}</span>}
                  </td>
                  <td className="px-3 py-1.5 text-gray-400 text-[11px] tabular-nums">{r.hour_start}:00–{r.hour_end}:00</td>
                  <td className="px-3 py-1.5 text-[10px] text-gray-500">
                    {r.days_of_week.split(',').map(d => DAYS[+d]?.[0] ?? '?').join('')}
                  </td>
                  <td className="px-3 py-1.5 text-gray-400 text-[11px]">
                    {r.interval_minutes ? `${r.interval_minutes}m` : r.songs_between ? `${r.songs_between} songs` : '—'}
                  </td>
                  <td className="px-3 py-1.5 text-yellow-300 text-[11px]">{r.priority}</td>
                  <td className="px-3 py-1.5 flex gap-2">
                    <button onClick={() => startEdit(r)} className="text-yellow-400 hover:text-yellow-300 text-[11px]">Edit</button>
                    <button onClick={() => { if (confirm('Delete rule?')) deleteMut.mutate(r.id); }}
                      className="text-red-400 hover:text-red-300 text-[11px]">Del</button>
                    <button onClick={() => updateMut.mutate({ id: r.id, data: { is_active: !r.is_active } })}
                      className={`text-[11px] ${r.is_active ? 'text-orange-400' : 'text-green-400'}`}>
                      {r.is_active ? 'Off' : 'On'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rules.length === 0 && <div className="text-center text-gray-600 py-6">No rules. Create one to get started.</div>}
        </div>
      )}

      {/* Schedule Preview */}
      {showPreview && (
        <div className="bg-[#0a0a28] border border-[#2a2a5e] rounded overflow-hidden">
          <div className="bg-[#16163e] px-3 py-2 flex items-center gap-3 border-b border-[#2a2a5e]">
            <span className="text-[11px] text-gray-400">Preview Date:</span>
            <input type="date" value={previewDate} onChange={e => setPreviewDate(e.target.value)}
              className="bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-0.5 rounded text-sm focus:outline-none focus:border-cyan-700" />
            <span className="text-[11px] text-gray-500">
              {preview ? `${preview.slots.length} slots generated` : ''}
            </span>
          </div>
          {previewLoading ? (
            <div className="text-center text-gray-500 py-4">Generating preview...</div>
          ) : preview ? (
            <div className="max-h-[400px] overflow-y-auto">
              <table className="w-full text-[12px]">
                <thead className="sticky top-0 bg-[#16163e]">
                  <tr className="text-[10px] text-gray-500 uppercase">
                    <th className="text-left px-3 py-1">Time</th>
                    <th className="text-left px-3 py-1">Type</th>
                    <th className="text-left px-3 py-1">Category</th>
                    <th className="text-left px-3 py-1">Rule</th>
                    <th className="text-left px-3 py-1">Duration</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.slots.slice(0, 200).map((slot, i) => (
                    <tr key={i} className="border-t border-[#12122e] hover:bg-[#14143a]">
                      <td className="px-3 py-0.5 text-white tabular-nums">{slot.time}</td>
                      <td className="px-3 py-0.5">
                        <span className={`px-1 py-0.5 rounded text-[10px] ${TYPE_COLORS[slot.asset_type] ?? 'bg-gray-800 text-gray-300'}`}>
                          {slot.asset_type}
                        </span>
                      </td>
                      <td className="px-3 py-0.5 text-gray-400">{slot.category ?? '—'}</td>
                      <td className="px-3 py-0.5 text-gray-500">{slot.rule_name}</td>
                      <td className="px-3 py-0.5 text-gray-400 tabular-nums">{slot.duration_minutes}m</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
