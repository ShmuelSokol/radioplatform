import { useState } from 'react';
import { useArchives, useCreateArchive, useUpdateArchive, useDeleteArchive } from '../../hooks/useArchives';
import { useStations } from '../../hooks/useStations';
import type { ShowArchive } from '../../api/archives';

function formatDuration(seconds: number | null): string {
  if (!seconds) return '-';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

interface ArchiveFormState {
  station_id: string;
  title: string;
  description: string;
  host_name: string;
  audio_url: string;
  cover_image_url: string;
  duration_seconds: string;
  recorded_at: string;
  live_show_id: string;
}

const emptyForm: ArchiveFormState = {
  station_id: '',
  title: '',
  description: '',
  host_name: '',
  audio_url: '',
  cover_image_url: '',
  duration_seconds: '',
  recorded_at: '',
  live_show_id: '',
};

export default function AdminArchives() {
  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ArchiveFormState>(emptyForm);
  const [stationFilter, setStationFilter] = useState<string>('');

  const { data: stationsData } = useStations();
  const { data, isLoading } = useArchives(stationFilter ? { station_id: stationFilter } : undefined);
  const createMut = useCreateArchive();
  const updateMut = useUpdateArchive();
  const deleteMut = useDeleteArchive();

  const stations = stationsData?.stations ?? [];
  const archives = data?.archives ?? [];

  const stationName = (id: string) => stations.find(s => s.id === id)?.name ?? 'Unknown';

  const openCreate = () => {
    setEditingId(null);
    setForm(emptyForm);
    setShowModal(true);
  };

  const openEdit = (a: ShowArchive) => {
    setEditingId(a.id);
    setForm({
      station_id: a.station_id,
      title: a.title,
      description: a.description ?? '',
      host_name: a.host_name ?? '',
      audio_url: a.audio_url,
      cover_image_url: a.cover_image_url ?? '',
      duration_seconds: a.duration_seconds?.toString() ?? '',
      recorded_at: a.recorded_at ? new Date(a.recorded_at).toISOString().slice(0, 16) : '',
      live_show_id: a.live_show_id ?? '',
    });
    setShowModal(true);
  };

  const handleSubmit = async () => {
    if (editingId) {
      await updateMut.mutateAsync({
        id: editingId,
        data: {
          title: form.title || undefined,
          description: form.description || undefined,
          host_name: form.host_name || undefined,
          audio_url: form.audio_url || undefined,
          cover_image_url: form.cover_image_url || undefined,
        },
      });
    } else {
      await createMut.mutateAsync({
        station_id: form.station_id,
        title: form.title,
        audio_url: form.audio_url,
        description: form.description || undefined,
        host_name: form.host_name || undefined,
        cover_image_url: form.cover_image_url || undefined,
        duration_seconds: form.duration_seconds ? parseInt(form.duration_seconds) : undefined,
        recorded_at: form.recorded_at || undefined,
        live_show_id: form.live_show_id || undefined,
      });
    }
    setShowModal(false);
    setForm(emptyForm);
    setEditingId(null);
  };

  const handleDelete = (id: string) => {
    if (confirm('Delete this archive? This cannot be undone.')) {
      deleteMut.mutate(id);
    }
  };

  const handleTogglePublished = (archive: ShowArchive) => {
    updateMut.mutate({
      id: archive.id,
      data: { is_published: !archive.is_published },
    });
  };

  const inputClass = 'w-full bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-3 py-2 rounded text-sm focus:outline-none focus:border-cyan-700 placeholder:text-gray-600';
  const labelClass = 'block text-[11px] text-gray-400 mb-1 uppercase tracking-wide';

  return (
    <div className="-mx-4 sm:-mx-6 lg:-mx-8 -my-6 bg-[#080820] font-mono text-[13px] min-h-[calc(100vh-4rem)] flex flex-col text-white p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-cyan-300">
          Show Archives
          {data && <span className="ml-2 text-gray-500 text-sm font-normal">({data.total})</span>}
        </h1>
        <div className="flex items-center gap-3">
          <select
            value={stationFilter}
            onChange={e => setStationFilter(e.target.value)}
            className="bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700"
          >
            <option value="">All Stations</option>
            {stations.map(s => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
          <button
            onClick={openCreate}
            className="bg-cyan-700 hover:bg-cyan-600 text-white px-3 py-1.5 rounded text-sm font-medium transition"
          >
            + New Archive
          </button>
        </div>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="text-gray-500 text-center py-8">Loading archives...</div>
      ) : archives.length === 0 ? (
        <div className="text-center py-12 text-gray-600">
          <svg className="w-12 h-12 mx-auto mb-3 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
          </svg>
          No archives yet. Click "+ New Archive" to add one.
        </div>
      ) : (
        <div className="bg-[#0a0a28] border border-[#2a2a5e] rounded overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-[#16163e] text-[10px] text-gray-500 uppercase">
                <th className="text-left px-3 py-2">Title</th>
                <th className="text-left px-3 py-2">Station</th>
                <th className="text-left px-3 py-2 hidden md:table-cell">Host</th>
                <th className="text-left px-3 py-2 hidden lg:table-cell">Duration</th>
                <th className="text-left px-3 py-2 hidden lg:table-cell">Recorded</th>
                <th className="text-left px-3 py-2">Published</th>
                <th className="text-left px-3 py-2">Created</th>
                <th className="text-left px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {archives.map(a => (
                <tr key={a.id} className="border-t border-[#1a1a3e] hover:bg-[#14143a]">
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      {a.cover_image_url ? (
                        <img src={a.cover_image_url} alt="" className="w-8 h-8 rounded object-cover flex-shrink-0" />
                      ) : (
                        <div className="w-8 h-8 rounded bg-gradient-to-br from-indigo-700 to-purple-800 flex items-center justify-center flex-shrink-0">
                          <svg className="w-4 h-4 text-white/40" fill="currentColor" viewBox="0 0 24 24">
                            <path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z" />
                          </svg>
                        </div>
                      )}
                      <span className="text-cyan-300 truncate max-w-[200px]">{a.title}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2 text-gray-400 text-[11px]">{stationName(a.station_id)}</td>
                  <td className="px-3 py-2 text-gray-400 text-[11px] hidden md:table-cell">{a.host_name ?? '-'}</td>
                  <td className="px-3 py-2 text-gray-500 text-[11px] hidden lg:table-cell">{formatDuration(a.duration_seconds)}</td>
                  <td className="px-3 py-2 text-gray-500 text-[11px] hidden lg:table-cell">
                    {a.recorded_at ? new Date(a.recorded_at).toLocaleDateString() : '-'}
                  </td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => handleTogglePublished(a)}
                      className={`text-[11px] px-2 py-0.5 rounded font-medium transition ${
                        a.is_published
                          ? 'bg-green-900/60 text-green-300 hover:bg-green-800/60'
                          : 'bg-gray-800 text-gray-500 hover:bg-gray-700'
                      }`}
                    >
                      {a.is_published ? 'Published' : 'Draft'}
                    </button>
                  </td>
                  <td className="px-3 py-2 text-gray-500 text-[11px] whitespace-nowrap">{timeAgo(a.created_at)}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => openEdit(a)}
                        className="text-cyan-400 hover:text-cyan-300 text-[11px] transition"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(a.id)}
                        className="text-red-400 hover:text-red-300 text-[11px] transition"
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
            <div className="px-5 py-4 border-b border-[#2a2a5e] flex items-center justify-between">
              <h2 className="text-lg font-bold text-cyan-300">
                {editingId ? 'Edit Archive' : 'New Archive'}
              </h2>
              <button onClick={() => setShowModal(false)} className="text-gray-500 hover:text-white text-xl">&times;</button>
            </div>
            <div className="p-5 space-y-4">
              {!editingId && (
                <div>
                  <label className={labelClass}>Station *</label>
                  <select
                    value={form.station_id}
                    onChange={e => setForm(f => ({ ...f, station_id: e.target.value }))}
                    className={inputClass}
                  >
                    <option value="">Select station...</option>
                    {stations.map(s => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                </div>
              )}
              <div>
                <label className={labelClass}>Title *</label>
                <input
                  type="text"
                  value={form.title}
                  onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
                  placeholder="Show title"
                  className={inputClass}
                />
              </div>
              <div>
                <label className={labelClass}>Audio URL *</label>
                <input
                  type="url"
                  value={form.audio_url}
                  onChange={e => setForm(f => ({ ...f, audio_url: e.target.value }))}
                  placeholder="https://example.com/audio.mp3"
                  className={inputClass}
                />
              </div>
              <div>
                <label className={labelClass}>Description</label>
                <textarea
                  value={form.description}
                  onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                  placeholder="Episode description..."
                  rows={3}
                  className={inputClass}
                />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className={labelClass}>Host Name</label>
                  <input
                    type="text"
                    value={form.host_name}
                    onChange={e => setForm(f => ({ ...f, host_name: e.target.value }))}
                    placeholder="Host name"
                    className={inputClass}
                  />
                </div>
                <div>
                  <label className={labelClass}>Duration (seconds)</label>
                  <input
                    type="number"
                    value={form.duration_seconds}
                    onChange={e => setForm(f => ({ ...f, duration_seconds: e.target.value }))}
                    placeholder="3600"
                    className={inputClass}
                  />
                </div>
              </div>
              <div>
                <label className={labelClass}>Cover Image URL</label>
                <input
                  type="url"
                  value={form.cover_image_url}
                  onChange={e => setForm(f => ({ ...f, cover_image_url: e.target.value }))}
                  placeholder="https://example.com/cover.jpg"
                  className={inputClass}
                />
              </div>
              {!editingId && (
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className={labelClass}>Recorded At</label>
                    <input
                      type="datetime-local"
                      value={form.recorded_at}
                      onChange={e => setForm(f => ({ ...f, recorded_at: e.target.value }))}
                      className={inputClass}
                    />
                  </div>
                  <div>
                    <label className={labelClass}>Live Show ID</label>
                    <input
                      type="text"
                      value={form.live_show_id}
                      onChange={e => setForm(f => ({ ...f, live_show_id: e.target.value }))}
                      placeholder="Optional"
                      className={inputClass}
                    />
                  </div>
                </div>
              )}
            </div>
            <div className="px-5 py-4 border-t border-[#2a2a5e] flex items-center justify-end gap-3">
              <button
                onClick={() => setShowModal(false)}
                className="px-4 py-2 text-sm text-gray-400 hover:text-white transition"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                disabled={!form.title || !form.audio_url || (!editingId && !form.station_id) || createMut.isPending || updateMut.isPending}
                className="bg-cyan-700 hover:bg-cyan-600 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-2 rounded text-sm font-medium transition"
              >
                {createMut.isPending || updateMut.isPending ? 'Saving...' : editingId ? 'Update' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
