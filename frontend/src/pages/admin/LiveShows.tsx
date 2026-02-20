import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useStations } from '../../hooks/useStations';
import {
  useLiveShows,
  useCreateLiveShow,
  useDeleteLiveShow,
  useStartLiveShow,
} from '../../hooks/useLiveShows';
import type { LiveShow } from '../../api/liveShows';

const STATUS_COLORS: Record<string, string> = {
  scheduled: 'bg-blue-700 text-blue-200',
  live: 'bg-red-700 text-red-200 animate-pulse',
  ended: 'bg-gray-700 text-gray-300',
  cancelled: 'bg-yellow-800 text-yellow-300',
};

const TABS = ['all', 'scheduled', 'live', 'ended'] as const;

export default function LiveShows() {
  const [activeTab, setActiveTab] = useState<string>('all');
  const [showModal, setShowModal] = useState(false);

  // Form state
  const [title, setTitle] = useState('');
  const [stationId, setStationId] = useState('');
  const [broadcastMode, setBroadcastMode] = useState('webrtc');
  const [scheduledStart, setScheduledStart] = useState('');
  const [scheduledEnd, setScheduledEnd] = useState('');
  const [callsEnabled, setCallsEnabled] = useState(true);

  const { data: stationsData } = useStations();
  const stations = stationsData?.stations ?? [];

  const statusFilter = activeTab === 'all' ? undefined : activeTab;
  const { data, isLoading } = useLiveShows({ status: statusFilter });
  const shows = data?.shows ?? [];

  const createMut = useCreateLiveShow();
  const deleteMut = useDeleteLiveShow();
  const startMut = useStartLiveShow();

  const handleCreate = () => {
    if (!title || !stationId) return;
    createMut.mutate(
      {
        station_id: stationId,
        title,
        broadcast_mode: broadcastMode,
        scheduled_start: scheduledStart || undefined,
        scheduled_end: scheduledEnd || undefined,
        calls_enabled: callsEnabled,
      },
      {
        onSuccess: () => {
          setShowModal(false);
          setTitle('');
          setStationId('');
          setBroadcastMode('webrtc');
          setScheduledStart('');
          setScheduledEnd('');
          setCallsEnabled(true);
        },
      },
    );
  };

  const fmt = (iso: string | null) => {
    if (!iso) return '--';
    return new Date(iso).toLocaleString([], {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  };

  return (
    <div className="max-w-6xl mx-auto py-6 px-4">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-cyan-300">Live Shows</h1>
        <button
          onClick={() => setShowModal(true)}
          className="bg-cyan-700 hover:bg-cyan-600 text-white text-sm px-4 py-2 rounded transition"
        >
          + New Show
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 mb-4">
        {TABS.map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-3 py-1 rounded text-sm capitalize transition ${
              activeTab === tab
                ? 'bg-cyan-700 text-white'
                : 'bg-[#1a1a4e] text-gray-400 hover:text-white'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Show list */}
      {isLoading ? (
        <p className="text-gray-500 text-center py-10">Loading...</p>
      ) : shows.length === 0 ? (
        <p className="text-gray-500 text-center py-10">No shows found</p>
      ) : (
        <div className="grid gap-3">
          {shows.map((show: LiveShow) => (
            <div
              key={show.id}
              className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-4 flex items-center justify-between"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-white font-medium truncate">{show.title}</span>
                  <span className={`text-[10px] px-2 py-0.5 rounded-full uppercase font-bold ${STATUS_COLORS[show.status]}`}>
                    {show.status}
                  </span>
                  <span className="text-[10px] text-gray-500 uppercase">{show.broadcast_mode}</span>
                </div>
                <div className="text-[11px] text-gray-500">
                  {fmt(show.scheduled_start)} &mdash; {fmt(show.scheduled_end)}
                  {' | '}
                  {stations.find(s => s.id === show.station_id)?.name ?? 'Unknown Station'}
                  {show.calls_enabled && ' | Calls ON'}
                </div>
              </div>

              <div className="flex items-center gap-2 ml-4 flex-shrink-0">
                {show.status === 'scheduled' && (
                  <button
                    onClick={() => startMut.mutate(show.id)}
                    className="text-[11px] bg-green-800 hover:bg-green-700 text-green-200 px-3 py-1 rounded transition"
                  >
                    Go Live
                  </button>
                )}
                {show.status === 'live' && (
                  <>
                    <Link
                      to={`/admin/live/${show.id}/host`}
                      className="text-[11px] bg-red-800 hover:bg-red-700 text-red-200 px-3 py-1 rounded transition"
                    >
                      Host Console
                    </Link>
                    <Link
                      to={`/admin/live/${show.id}/screen`}
                      className="text-[11px] bg-purple-800 hover:bg-purple-700 text-purple-200 px-3 py-1 rounded transition"
                    >
                      Screener
                    </Link>
                  </>
                )}
                {(show.status === 'scheduled' || show.status === 'ended') && (
                  <button
                    onClick={() => {
                      if (confirm('Delete this show?')) deleteMut.mutate(show.id);
                    }}
                    className="text-[11px] bg-red-900 hover:bg-red-800 text-red-400 px-3 py-1 rounded transition"
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-6 w-full max-w-md">
            <h2 className="text-lg font-bold text-cyan-300 mb-4">Create Live Show</h2>

            <div className="space-y-3">
              <div>
                <label className="block text-[11px] text-gray-400 mb-1">Title</label>
                <input
                  type="text"
                  value={title}
                  onChange={e => setTitle(e.target.value)}
                  className="w-full bg-[#0a0a28] border border-[#2a2a5e] rounded px-3 py-2 text-sm text-white"
                  placeholder="Show title"
                />
              </div>

              <div>
                <label className="block text-[11px] text-gray-400 mb-1">Station</label>
                <select
                  value={stationId}
                  onChange={e => setStationId(e.target.value)}
                  className="w-full bg-[#0a0a28] border border-[#2a2a5e] rounded px-3 py-2 text-sm text-white"
                >
                  <option value="">Select station...</option>
                  {stations.map(s => (
                    <option key={s.id} value={s.id}>{s.name}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-[11px] text-gray-400 mb-1">Broadcast Mode</label>
                <select
                  value={broadcastMode}
                  onChange={e => setBroadcastMode(e.target.value)}
                  className="w-full bg-[#0a0a28] border border-[#2a2a5e] rounded px-3 py-2 text-sm text-white"
                >
                  <option value="webrtc">WebRTC (Browser Mic)</option>
                  <option value="icecast">Icecast (BUTT/Mixxx)</option>
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-[11px] text-gray-400 mb-1">Start Time</label>
                  <input
                    type="datetime-local"
                    value={scheduledStart}
                    onChange={e => setScheduledStart(e.target.value)}
                    className="w-full bg-[#0a0a28] border border-[#2a2a5e] rounded px-3 py-2 text-sm text-white"
                  />
                </div>
                <div>
                  <label className="block text-[11px] text-gray-400 mb-1">End Time (Hard Stop)</label>
                  <input
                    type="datetime-local"
                    value={scheduledEnd}
                    onChange={e => setScheduledEnd(e.target.value)}
                    className="w-full bg-[#0a0a28] border border-[#2a2a5e] rounded px-3 py-2 text-sm text-white"
                  />
                </div>
              </div>

              <label className="flex items-center gap-2 text-sm text-gray-300">
                <input
                  type="checkbox"
                  checked={callsEnabled}
                  onChange={e => setCallsEnabled(e.target.checked)}
                  className="rounded"
                />
                Enable call-ins
              </label>
            </div>

            <div className="flex justify-end gap-2 mt-6">
              <button
                onClick={() => setShowModal(false)}
                className="text-sm text-gray-400 hover:text-white px-4 py-2 transition"
              >
                Cancel
              </button>
              <button
                onClick={handleCreate}
                disabled={createMut.isPending || !title || !stationId}
                className="text-sm bg-cyan-700 hover:bg-cyan-600 text-white px-4 py-2 rounded transition disabled:opacity-50"
              >
                {createMut.isPending ? 'Creating...' : 'Create'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
