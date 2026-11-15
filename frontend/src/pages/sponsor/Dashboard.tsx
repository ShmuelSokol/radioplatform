import { useState } from 'react';
import { usePlayHistory, useUpcomingSchedule, useSponsorStats, useMySpots } from '../../hooks/useSponsorPortal';
import Spinner from '../../components/Spinner';

export default function SponsorDashboard() {
  const [page, setPage] = useState(1);
  const limit = 25;

  const { data: stats, isLoading: statsLoading } = useSponsorStats();
  const { data: history, isLoading: historyLoading } = usePlayHistory(page, limit);
  const { data: upcoming, isLoading: upcomingLoading } = useUpcomingSchedule();
  const { data: spots, isLoading: spotsLoading } = useMySpots();
  const when = (iso?: string | null) =>
    iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'Not yet';

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-800">Dashboard</h1>

      {/* Stats cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white rounded-xl shadow p-5">
          <p className="text-sm text-gray-500">Plays This Month</p>
          <p className="text-3xl font-bold text-indigo-600">
            {statsLoading ? <Spinner /> : stats?.total_plays_month ?? 0}
          </p>
        </div>
        <div className="bg-white rounded-xl shadow p-5">
          <p className="text-sm text-gray-500">Total Plays (All Time)</p>
          <p className="text-3xl font-bold text-indigo-600">
            {statsLoading ? <Spinner /> : stats?.total_plays_alltime ?? 0}
          </p>
        </div>
        <div className="bg-white rounded-xl shadow p-5">
          <p className="text-sm text-gray-500">Last Played</p>
          <p className="text-lg font-semibold text-gray-700">
            {statsLoading ? <Spinner /> : when(stats?.last_played)}
          </p>
        </div>
        <div className="bg-white rounded-xl shadow p-5">
          <p className="text-sm text-gray-500">Next Scheduled</p>
          <p className="text-lg font-semibold text-gray-700">
            {statsLoading ? <Spinner /> : stats?.next_scheduled ?? 'N/A'}
          </p>
        </div>
      </div>

      {/* My spots */}
      <div className="bg-white rounded-xl shadow">
        <div className="px-5 py-4 border-b">
          <h2 className="text-lg font-semibold text-gray-800">My Spots</h2>
        </div>
        {spotsLoading ? (
          <div className="p-8 text-center"><Spinner /></div>
        ) : !spots?.length ? (
          <div className="p-8 text-center text-gray-400">No spots yet</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-left">
                <tr>
                  <th className="px-5 py-3">Spot</th>
                  <th className="px-5 py-3">Length</th>
                  <th className="px-5 py-3">Last played</th>
                  <th className="px-5 py-3">Plays next</th>
                  <th className="px-5 py-3">This month</th>
                  <th className="px-5 py-3">All time</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {spots.map((spot) => (
                  <tr key={spot.asset_id} className="hover:bg-gray-50">
                    <td className="px-5 py-3 font-medium text-gray-800">{spot.title}</td>
                    <td className="px-5 py-3">
                      {spot.duration_seconds ? `${Math.round(spot.duration_seconds)}s` : '-'}
                    </td>
                    <td className="px-5 py-3">{when(spot.last_played_at)}</td>
                    <td className="px-5 py-3">
                      {spot.next_play_at ? when(spot.next_play_at) : spot.scheduled ? 'Soon' : 'Not scheduled'}
                    </td>
                    <td className="px-5 py-3">{spot.plays_month}</td>
                    <td className="px-5 py-3">{spot.plays_alltime}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Play History */}
      <div className="bg-white rounded-xl shadow">
        <div className="px-5 py-4 border-b">
          <h2 className="text-lg font-semibold text-gray-800">Play History</h2>
        </div>
        {historyLoading ? (
          <div className="p-8 text-center"><Spinner /></div>
        ) : !history?.entries?.length ? (
          <div className="p-8 text-center text-gray-400">No play history yet</div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-gray-500 text-left">
                  <tr>
                    <th className="px-5 py-3">Date</th>
                    <th className="px-5 py-3">Time</th>
                    <th className="px-5 py-3">Station</th>
                    <th className="px-5 py-3">Spot</th>
                    <th className="px-5 py-3">Duration</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {history.entries.map((entry) => {
                    const d = new Date(entry.start_utc);
                    return (
                      <tr key={entry.id} className="hover:bg-gray-50">
                        <td className="px-5 py-3">{d.toLocaleDateString()}</td>
                        <td className="px-5 py-3">{d.toLocaleTimeString()}</td>
                        <td className="px-5 py-3">{entry.station_name}</td>
                        <td className="px-5 py-3">{entry.asset_title}</td>
                        <td className="px-5 py-3">
                          {entry.duration_seconds ? `${entry.duration_seconds.toFixed(0)}s` : '-'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {/* Pagination */}
            <div className="px-5 py-3 border-t flex items-center justify-between text-sm text-gray-500">
              <span>
                Page {history.page} of {Math.ceil(history.total / history.limit) || 1}
                {' '}({history.total} total)
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  className="px-3 py-1 border rounded hover:bg-gray-100 disabled:opacity-40"
                >
                  Prev
                </button>
                <button
                  onClick={() => setPage((p) => p + 1)}
                  disabled={page >= Math.ceil((history?.total ?? 0) / limit)}
                  className="px-3 py-1 border rounded hover:bg-gray-100 disabled:opacity-40"
                >
                  Next
                </button>
              </div>
            </div>
          </>
        )}
      </div>

      {/* Upcoming Schedule */}
      <div className="bg-white rounded-xl shadow">
        <div className="px-5 py-4 border-b">
          <h2 className="text-lg font-semibold text-gray-800">Upcoming Plays (Next 7 Days)</h2>
        </div>
        {upcomingLoading ? (
          <div className="p-8 text-center"><Spinner /></div>
        ) : !upcoming?.length ? (
          <div className="p-8 text-center text-gray-400">Nothing scheduled yet</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-500 text-left">
                <tr>
                  <th className="px-5 py-3">Date</th>
                  <th className="px-5 py-3">Station</th>
                  <th className="px-5 py-3">Time (station time)</th>
                  <th className="px-5 py-3">Spot</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {upcoming.slice(0, 30).map((entry, i) => (
                  <tr key={i} className="hover:bg-gray-50">
                    <td className="px-5 py-3">{entry.estimated_date}</td>
                    <td className="px-5 py-3">{entry.station_name}</td>
                    <td className="px-5 py-3">{entry.time_slot}</td>
                    <td className="px-5 py-3">{entry.asset_title}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
