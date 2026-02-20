import { useState } from 'react';
import { useAlerts, useResolveAlert, useReopenAlert, useDeleteAlert } from '../../hooks/useAlerts';
import { useAuthStore } from '../../stores/authStore';
import Spinner from '../../components/Spinner';

const SEVERITY_COLORS: Record<string, string> = {
  info: 'bg-blue-900 text-blue-300',
  warning: 'bg-amber-900 text-amber-300',
  critical: 'bg-red-900 text-red-300',
};

const SEVERITY_DOT: Record<string, string> = {
  info: 'bg-blue-400',
  warning: 'bg-amber-400',
  critical: 'bg-red-400',
};

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

export default function Alerts() {
  const { user } = useAuthStore();
  const isAdmin = user?.role === 'admin';

  const [severityFilter, setSeverityFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('unresolved');

  const is_resolved = statusFilter === 'resolved' ? true : statusFilter === 'unresolved' ? false : undefined;

  const { data, isLoading } = useAlerts({
    severity: severityFilter || undefined,
    alert_type: typeFilter || undefined,
    is_resolved,
    limit: 100,
  });
  const resolveMut = useResolveAlert();
  const reopenMut = useReopenAlert();
  const deleteMut = useDeleteAlert();

  const alerts = data?.alerts ?? [];
  const unresolvedCount = data?.unresolved_count ?? 0;

  return (
    <div className="-mx-4 sm:-mx-6 lg:-mx-8 -my-6 bg-[#080820] font-mono text-[13px] min-h-[calc(100vh-4rem)] flex flex-col text-white p-4">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-cyan-300">
          Alerts
          {unresolvedCount > 0 && (
            <span className="ml-2 px-2 py-0.5 bg-red-700 text-white text-[11px] rounded-full">
              {unresolvedCount} unresolved
            </span>
          )}
        </h1>
      </div>

      {/* Filters */}
      <div className="bg-[#12123a] border border-[#2a2a5e] rounded p-3 mb-4 flex flex-wrap gap-3 items-end">
        <div>
          <label className="block text-[11px] text-gray-400 mb-1">Severity</label>
          <select value={severityFilter} onChange={e => setSeverityFilter(e.target.value)}
            className="bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700">
            <option value="">All</option>
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="critical">Critical</option>
          </select>
        </div>
        <div>
          <label className="block text-[11px] text-gray-400 mb-1">Type</label>
          <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}
            className="bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700">
            <option value="">All</option>
            <option value="schedule_conflict">Schedule Conflict</option>
            <option value="playback_gap">Playback Gap</option>
            <option value="queue_empty">Queue Empty</option>
            <option value="asset_missing">Asset Missing</option>
            <option value="stream_down">Stream Down</option>
            <option value="blackout_start">Blackout Start</option>
            <option value="blackout_end">Blackout End</option>
            <option value="system">System</option>
          </select>
        </div>
        <div>
          <label className="block text-[11px] text-gray-400 mb-1">Status</label>
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
            className="bg-[#0a0a28] border border-[#2a2a5e] text-cyan-200 px-2 py-1 rounded text-sm focus:outline-none focus:border-cyan-700">
            <option value="">All</option>
            <option value="unresolved">Unresolved</option>
            <option value="resolved">Resolved</option>
          </select>
        </div>
      </div>

      {isLoading ? (
        <div className="text-gray-500 text-center py-8">Loading alerts...</div>
      ) : alerts.length === 0 ? (
        <div className="text-center py-12 text-gray-600">
          <svg className="w-12 h-12 mx-auto mb-3 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
          </svg>
          No alerts found
        </div>
      ) : (
        <div className="bg-[#0a0a28] border border-[#2a2a5e] rounded overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-[#16163e] text-[10px] text-gray-500 uppercase">
                <th className="text-left px-3 py-2 w-8"></th>
                <th className="text-left px-3 py-2">Severity</th>
                <th className="text-left px-3 py-2">Type</th>
                <th className="text-left px-3 py-2">Title</th>
                <th className="text-left px-3 py-2 hidden lg:table-cell">Message</th>
                <th className="text-left px-3 py-2">Time</th>
                <th className="text-left px-3 py-2">Status</th>
                <th className="text-left px-3 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map(alert => (
                <tr key={alert.id} className="border-t border-[#1a1a3e] hover:bg-[#14143a]">
                  <td className="px-3 py-1.5">
                    <span className={`inline-block w-2 h-2 rounded-full ${SEVERITY_DOT[alert.severity] ?? 'bg-gray-400'}`} />
                  </td>
                  <td className="px-3 py-1.5">
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${SEVERITY_COLORS[alert.severity] ?? 'bg-gray-800 text-gray-400'}`}>
                      {alert.severity.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-gray-400 text-[11px]">
                    {alert.alert_type.replace(/_/g, ' ')}
                  </td>
                  <td className="px-3 py-1.5 text-cyan-300 max-w-[200px] truncate">{alert.title}</td>
                  <td className="px-3 py-1.5 text-gray-400 text-[11px] max-w-[300px] truncate hidden lg:table-cell">{alert.message}</td>
                  <td className="px-3 py-1.5 text-gray-500 text-[11px] whitespace-nowrap">{timeAgo(alert.created_at)}</td>
                  <td className="px-3 py-1.5">
                    {alert.is_resolved ? (
                      <span className="text-green-400 text-[11px]">Resolved</span>
                    ) : (
                      <span className="text-amber-400 text-[11px]">Open</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 flex gap-2">
                    {!alert.is_resolved ? (
                      <button onClick={() => resolveMut.mutate(alert.id)}
                        disabled={resolveMut.isPending}
                        className="text-green-400 hover:text-green-300 text-[11px] disabled:opacity-50">
                        {resolveMut.isPending ? <Spinner className="mr-1" /> : null}Resolve
                      </button>
                    ) : (
                      <button onClick={() => reopenMut.mutate(alert.id)}
                        disabled={reopenMut.isPending}
                        className="text-amber-400 hover:text-amber-300 text-[11px] disabled:opacity-50">
                        Reopen
                      </button>
                    )}
                    {isAdmin && (
                      <button onClick={() => { if (confirm('Delete this alert?')) deleteMut.mutate(alert.id); }}
                        className="text-red-400 hover:text-red-300 text-[11px]">Delete</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
