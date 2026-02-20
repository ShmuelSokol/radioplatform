import { useState } from 'react';
import {
  useLiveListeners,
  useTodayStats,
  useListenerHistory,
  useListenerRegions,
} from '../../hooks/useListeners';

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-[#12123a] border border-[#2a2a5e] rounded-xl p-5">
      <div className="text-xs text-gray-400 uppercase font-bold">{label}</div>
      <div className="text-3xl font-bold text-white mt-1">{value}</div>
      {sub && <div className="text-xs text-gray-500 mt-1">{sub}</div>}
    </div>
  );
}

function HistoryChart({ data }: { data: Array<{ date: string; total_minutes: number; unique_listeners: number }> }) {
  if (!data.length) return <div className="text-gray-500 text-sm">No data yet</div>;

  const maxMinutes = Math.max(...data.map((d) => d.total_minutes), 1);
  const maxListeners = Math.max(...data.map((d) => d.unique_listeners), 1);

  return (
    <div className="space-y-1">
      {/* Simple bar chart */}
      <div className="flex items-end gap-[2px] h-40">
        {data.map((d) => {
          const heightPct = (d.total_minutes / maxMinutes) * 100;
          return (
            <div key={d.date} className="flex-1 flex flex-col items-center group relative">
              <div
                className="w-full bg-cyan-600 rounded-t-sm min-h-[2px] transition-all hover:bg-cyan-400"
                style={{ height: `${heightPct}%` }}
              />
              {/* Tooltip */}
              <div className="absolute bottom-full mb-1 hidden group-hover:block bg-gray-900 border border-gray-700 rounded px-2 py-1 text-[10px] text-white whitespace-nowrap z-10">
                {d.date}: {Math.round(d.total_minutes)} min, {d.unique_listeners} listeners
              </div>
            </div>
          );
        })}
      </div>
      {/* X-axis labels (show every ~5th date) */}
      <div className="flex gap-[2px]">
        {data.map((d, i) => (
          <div key={d.date} className="flex-1 text-center">
            {i % Math.max(1, Math.floor(data.length / 7)) === 0 ? (
              <span className="text-[9px] text-gray-600">{d.date.slice(5)}</span>
            ) : null}
          </div>
        ))}
      </div>
      <div className="flex justify-between text-[10px] text-gray-600 mt-1">
        <span>Max: {Math.round(maxMinutes)} min/day</span>
        <span>Max: {maxListeners} unique/day</span>
      </div>
    </div>
  );
}

export default function LiveListeners() {
  const [historyDays, setHistoryDays] = useState(30);
  const [regionDays, setRegionDays] = useState(7);

  const { data: live } = useLiveListeners();
  const { data: today } = useTodayStats();
  const { data: history } = useListenerHistory(historyDays);
  const { data: regions } = useListenerRegions(regionDays);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <h1 className="text-2xl font-bold text-white">Live Listeners</h1>

      {/* ── Summary Cards ─────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Active Now"
          value={live?.total_listeners ?? 0}
          sub={`${live?.stations?.length ?? 0} station${(live?.stations?.length ?? 0) !== 1 ? 's' : ''}`}
        />
        <StatCard
          label="Today Sessions"
          value={today?.total_sessions ?? 0}
          sub={`${today?.unique_listeners ?? 0} unique`}
        />
        <StatCard
          label="Today Minutes"
          value={Math.round(today?.total_minutes ?? 0).toLocaleString()}
          sub={`${Math.round((today?.total_minutes ?? 0) / 60)} hrs`}
        />
        <StatCard
          label="Peak Today"
          value={today?.peak_today ?? 0}
          sub="concurrent listeners"
        />
      </div>

      {/* ── Active Stations ────────────────────────────────────────── */}
      <section className="bg-[#12123a] border border-[#2a2a5e] rounded-xl p-6 space-y-4">
        <h2 className="text-lg font-semibold text-cyan-300">Active Stations</h2>
        {!live?.stations?.length ? (
          <div className="text-gray-500 text-sm py-4">No active listeners right now</div>
        ) : (
          <div className="space-y-3">
            {live.stations.map((s) => (
              <div key={s.station_id} className="border border-[#2a2a5e] rounded-lg p-4">
                <div className="flex items-center gap-3 mb-2">
                  <span className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />
                  <span className="font-semibold text-white">{s.station_name}</span>
                  <span className="text-cyan-400 font-bold text-lg ml-auto">{s.listeners}</span>
                  <span className="text-xs text-gray-500">listener{s.listeners !== 1 ? 's' : ''}</span>
                </div>
                {s.regions.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-2">
                    {s.regions.map((r, i) => (
                      <span
                        key={i}
                        className="px-2 py-0.5 bg-[#1a1a4e] rounded text-[11px] text-gray-300"
                      >
                        {r.city && r.city !== r.country ? `${r.city}, ` : ''}
                        {r.region && r.region !== r.country ? `${r.region}, ` : ''}
                        {r.country}
                        <span className="text-cyan-400 ml-1 font-bold">{r.count}</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── Historical Chart ───────────────────────────────────────── */}
      <section className="bg-[#12123a] border border-[#2a2a5e] rounded-xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-amber-300">Listening History</h2>
          <div className="flex gap-2">
            {[7, 14, 30, 90].map((d) => (
              <button
                key={d}
                onClick={() => setHistoryDays(d)}
                className={`px-3 py-1 rounded text-xs ${
                  historyDays === d
                    ? 'bg-amber-700 text-white'
                    : 'bg-[#1a1a4e] text-gray-400 hover:text-white'
                }`}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>
        <HistoryChart data={history ?? []} />
        {/* Summary table */}
        {history && history.length > 0 && (
          <div className="overflow-x-auto max-h-48 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="text-gray-500 sticky top-0 bg-[#12123a]">
                <tr>
                  <th className="text-left py-1 px-2">Date</th>
                  <th className="text-right py-1 px-2">Sessions</th>
                  <th className="text-right py-1 px-2">Unique</th>
                  <th className="text-right py-1 px-2">Minutes</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {[...history].reverse().map((d) => (
                  <tr key={d.date} className="border-t border-[#1a1a4e]">
                    <td className="py-1 px-2">{d.date}</td>
                    <td className="text-right py-1 px-2">{d.sessions}</td>
                    <td className="text-right py-1 px-2">{d.unique_listeners}</td>
                    <td className="text-right py-1 px-2">{Math.round(d.total_minutes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Regions ────────────────────────────────────────────────── */}
      <section className="bg-[#12123a] border border-[#2a2a5e] rounded-xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-emerald-300">Listener Regions</h2>
          <div className="flex gap-2">
            {[7, 14, 30, 90].map((d) => (
              <button
                key={d}
                onClick={() => setRegionDays(d)}
                className={`px-3 py-1 rounded text-xs ${
                  regionDays === d
                    ? 'bg-emerald-700 text-white'
                    : 'bg-[#1a1a4e] text-gray-400 hover:text-white'
                }`}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>
        {!regions?.length ? (
          <div className="text-gray-500 text-sm py-4">No region data yet</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-gray-500 text-xs">
                <tr>
                  <th className="text-left py-2 px-3">Location</th>
                  <th className="text-right py-2 px-3">Sessions</th>
                  <th className="text-right py-2 px-3">Unique</th>
                  <th className="text-right py-2 px-3">Minutes</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {regions.map((r, i) => (
                  <tr key={i} className="border-t border-[#1a1a4e]">
                    <td className="py-2 px-3">
                      <span className="font-medium text-white">{r.country}</span>
                      {r.region && <span className="text-gray-500"> / {r.region}</span>}
                      {r.city && <span className="text-gray-600"> / {r.city}</span>}
                    </td>
                    <td className="text-right py-2 px-3">{r.sessions}</td>
                    <td className="text-right py-2 px-3">{r.unique_listeners}</td>
                    <td className="text-right py-2 px-3">{Math.round(r.total_minutes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
