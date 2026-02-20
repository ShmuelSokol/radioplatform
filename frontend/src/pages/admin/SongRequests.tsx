import { useState } from 'react';
import { useSongRequests, useUpdateSongRequest, useDeleteSongRequest, useTopRequested } from '../../hooks/useSongRequests';
import Spinner from '../../components/Spinner';

const STATUS_COLORS: Record<string, string> = {
  pending: 'bg-blue-900 text-blue-300',
  approved: 'bg-green-900 text-green-300',
  queued: 'bg-yellow-900 text-yellow-300',
  played: 'bg-gray-800 text-gray-400',
  rejected: 'bg-red-900 text-red-300',
};

const STATUS_DOT: Record<string, string> = {
  pending: 'bg-blue-400',
  approved: 'bg-green-400',
  queued: 'bg-yellow-400',
  played: 'bg-gray-500',
  rejected: 'bg-red-400',
};

const TABS = [
  { key: '', label: 'All' },
  { key: 'pending', label: 'Pending' },
  { key: 'approved', label: 'Approved' },
  { key: 'queued', label: 'Queued' },
  { key: 'rejected', label: 'Rejected' },
];

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

function isAutoApproved(req: { status: string; reviewed_by: string | null }): boolean {
  return (req.status === 'queued' || req.status === 'approved') && req.reviewed_by === null;
}

export default function SongRequests() {
  const [statusFilter, setStatusFilter] = useState('');
  const [analyticsOpen, setAnalyticsOpen] = useState(false);

  const { data, isLoading } = useSongRequests({
    status: statusFilter || undefined,
  });
  const updateMut = useUpdateSongRequest();
  const deleteMut = useDeleteSongRequest();
  const { data: topData, isLoading: topLoading } = useTopRequested();

  const requests = data?.requests ?? [];
  const total = data?.total ?? 0;
  const topRequested = topData?.top_requested ?? [];

  const handleApprove = (id: string) => {
    updateMut.mutate({ id, data: { status: 'approved' } });
  };

  const handleReject = (id: string) => {
    updateMut.mutate({ id, data: { status: 'rejected' } });
  };

  const handleDelete = (id: string) => {
    if (confirm('Delete this song request?')) {
      deleteMut.mutate(id);
    }
  };

  return (
    <div className="-mx-4 sm:-mx-6 lg:-mx-8 -my-6 bg-[#080820] font-mono text-[13px] min-h-[calc(100vh-4rem)] flex flex-col text-white p-4">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-cyan-300">
          Song Requests
          {total > 0 && (
            <span className="ml-2 px-2 py-0.5 bg-[#2a2a5e] text-cyan-200 text-[11px] rounded-full">
              {total} total
            </span>
          )}
        </h1>
      </div>

      {/* Most Requested Analytics Panel */}
      <div className="mb-4">
        <button
          onClick={() => setAnalyticsOpen(!analyticsOpen)}
          className="flex items-center gap-2 text-sm text-cyan-400 hover:text-cyan-300 transition"
        >
          <svg className={`w-3 h-3 transition-transform ${analyticsOpen ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          Most Requested Songs
        </button>
        {analyticsOpen && (
          <div className="mt-2 bg-[#0a0a28] border border-[#2a2a5e] rounded p-3">
            {topLoading ? (
              <div className="text-gray-500 text-center py-3 flex items-center justify-center gap-2">
                <Spinner /> Loading...
              </div>
            ) : topRequested.length === 0 ? (
              <p className="text-gray-500 text-center py-2">No matched requests yet</p>
            ) : (
              <div className="space-y-1">
                {topRequested.map((item, i) => (
                  <div key={item.asset_id} className="flex items-center gap-3 py-1 border-b border-[#1a1a3e] last:border-0">
                    <span className="text-gray-500 w-5 text-right text-[11px]">#{i + 1}</span>
                    <div className="flex-1 min-w-0">
                      <span className="text-white font-medium truncate">{item.library_title || item.requested_title}</span>
                      {(item.library_artist || item.requested_artist) && (
                        <span className="text-gray-400 ml-2">{item.library_artist || item.requested_artist}</span>
                      )}
                    </div>
                    <span className="px-2 py-0.5 bg-cyan-900 text-cyan-200 text-[11px] rounded-full font-bold">
                      {item.request_count} req{item.request_count !== 1 ? 's' : ''}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Tab filters */}
      <div className="flex gap-1 mb-4">
        {TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => setStatusFilter(tab.key)}
            className={`px-3 py-1.5 rounded text-sm transition ${
              statusFilter === tab.key
                ? 'bg-cyan-800 text-cyan-100'
                : 'bg-[#12123a] text-gray-400 hover:bg-[#1a1a4e] hover:text-gray-200 border border-[#2a2a5e]'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="text-gray-500 text-center py-8 flex items-center justify-center gap-2">
          <Spinner /> Loading requests...
        </div>
      ) : requests.length === 0 ? (
        <div className="text-center py-12 text-gray-600">
          <svg className="w-12 h-12 mx-auto mb-3 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
          </svg>
          No song requests found
        </div>
      ) : (
        <>
          {/* Desktop table */}
          <div className="hidden md:block bg-[#0a0a28] border border-[#2a2a5e] rounded overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-[#16163e] text-[10px] text-gray-500 uppercase">
                  <th className="text-left px-3 py-2 w-8"></th>
                  <th className="text-left px-3 py-2">Requester</th>
                  <th className="text-left px-3 py-2">Song</th>
                  <th className="text-left px-3 py-2">Artist</th>
                  <th className="text-left px-3 py-2">Matched Asset</th>
                  <th className="text-left px-3 py-2 hidden lg:table-cell">Message</th>
                  <th className="text-left px-3 py-2">Status</th>
                  <th className="text-left px-3 py-2">Time</th>
                  <th className="text-left px-3 py-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {requests.map(req => (
                  <tr key={req.id} className="border-t border-[#1a1a3e] hover:bg-[#14143a]">
                    <td className="px-3 py-1.5">
                      <span className={`inline-block w-2 h-2 rounded-full ${STATUS_DOT[req.status] ?? 'bg-gray-400'}`} />
                    </td>
                    <td className="px-3 py-1.5 text-cyan-300 max-w-[140px] truncate">{req.requester_name}</td>
                    <td className="px-3 py-1.5 text-white max-w-[200px] truncate font-medium">{req.song_title}</td>
                    <td className="px-3 py-1.5 text-gray-400 max-w-[140px] truncate">{req.song_artist || '--'}</td>
                    <td className="px-3 py-1.5 max-w-[180px] truncate">
                      {req.matched_asset_title ? (
                        <span className="text-green-400 text-[11px]">
                          {req.matched_asset_title}
                          {req.matched_asset_artist && <span className="text-gray-500"> - {req.matched_asset_artist}</span>}
                        </span>
                      ) : (
                        <span className="text-gray-600 text-[11px]">--</span>
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-gray-500 text-[11px] max-w-[200px] truncate hidden lg:table-cell">
                      {req.requester_message || '--'}
                    </td>
                    <td className="px-3 py-1.5">
                      <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${STATUS_COLORS[req.status] ?? 'bg-gray-800 text-gray-400'}`}>
                        {req.status.toUpperCase()}
                        {isAutoApproved(req) && (
                          <span className="ml-1 opacity-70">AUTO</span>
                        )}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-gray-500 text-[11px] whitespace-nowrap">{timeAgo(req.created_at)}</td>
                    <td className="px-3 py-1.5">
                      <div className="flex gap-2">
                        {req.status === 'pending' && (
                          <>
                            <button
                              onClick={() => handleApprove(req.id)}
                              disabled={updateMut.isPending}
                              className="text-green-400 hover:text-green-300 text-[11px] disabled:opacity-50"
                            >
                              Approve
                            </button>
                            <button
                              onClick={() => handleReject(req.id)}
                              disabled={updateMut.isPending}
                              className="text-amber-400 hover:text-amber-300 text-[11px] disabled:opacity-50"
                            >
                              Reject
                            </button>
                          </>
                        )}
                        <button
                          onClick={() => handleDelete(req.id)}
                          disabled={deleteMut.isPending}
                          className="text-red-400 hover:text-red-300 text-[11px] disabled:opacity-50"
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

          {/* Mobile card list */}
          <div className="md:hidden space-y-2">
            {requests.map(req => (
              <div key={req.id} className="bg-[#12123a] border border-[#2a2a5e] rounded-lg p-3">
                <div className="flex items-start justify-between mb-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${STATUS_DOT[req.status] ?? 'bg-gray-400'}`} />
                      <span className="text-white font-medium truncate">{req.song_title}</span>
                    </div>
                    {req.song_artist && (
                      <p className="text-gray-400 text-[11px] ml-4">{req.song_artist}</p>
                    )}
                    {req.matched_asset_title && (
                      <p className="text-green-400 text-[11px] ml-4 mt-0.5">
                        Matched: {req.matched_asset_title}
                        {req.matched_asset_artist && ` - ${req.matched_asset_artist}`}
                      </p>
                    )}
                  </div>
                  <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold flex-shrink-0 ml-2 ${STATUS_COLORS[req.status] ?? 'bg-gray-800 text-gray-400'}`}>
                    {req.status.toUpperCase()}
                    {isAutoApproved(req) && <span className="ml-1 opacity-70">AUTO</span>}
                  </span>
                </div>
                <div className="flex items-center justify-between text-[11px]">
                  <div>
                    <span className="text-cyan-300">{req.requester_name}</span>
                    <span className="text-gray-600 mx-1">&middot;</span>
                    <span className="text-gray-500">{timeAgo(req.created_at)}</span>
                  </div>
                  <div className="flex gap-2">
                    {req.status === 'pending' && (
                      <>
                        <button onClick={() => handleApprove(req.id)} className="text-green-400 hover:text-green-300">Approve</button>
                        <button onClick={() => handleReject(req.id)} className="text-amber-400 hover:text-amber-300">Reject</button>
                      </>
                    )}
                    <button onClick={() => handleDelete(req.id)} className="text-red-400 hover:text-red-300">Delete</button>
                  </div>
                </div>
                {req.requester_message && (
                  <p className="text-gray-500 text-[11px] mt-2 border-t border-[#1a1a3e] pt-2 line-clamp-2">
                    {req.requester_message}
                  </p>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
