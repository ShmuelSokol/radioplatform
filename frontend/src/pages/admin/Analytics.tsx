import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  getAnalyticsSummary,
  getPlayCounts,
  getTopAssets,
  getCategoryBreakdown,
  getHourlyDistribution,
} from '../../api/analytics';
import { useStations } from '../../hooks/useStations';

function Bar({ value, max, label, sublabel }: { value: number; max: number; label: string; sublabel?: string }) {
  const pct = max > 0 ? (value / max) * 100 : 0;
  return (
    <div className="flex items-center gap-3 text-sm">
      <div className="w-32 truncate text-right text-gray-600" title={label}>{label}</div>
      <div className="flex-1 bg-gray-100 rounded-full h-5 overflow-hidden">
        <div className="bg-blue-500 h-full rounded-full transition-all" style={{ width: `${pct}%` }} />
      </div>
      <div className="w-12 text-right font-mono text-gray-700">{value}</div>
      {sublabel && <div className="w-24 text-xs text-gray-400">{sublabel}</div>}
    </div>
  );
}

export default function Analytics() {
  const { data: stations } = useStations();
  const [stationId, setStationId] = useState<string>('');
  const [days, setDays] = useState(7);

  const sid = stationId || undefined;

  const { data: summary } = useQuery({
    queryKey: ['analytics-summary', sid, days],
    queryFn: () => getAnalyticsSummary(sid, days),
  });

  const { data: playCounts } = useQuery({
    queryKey: ['analytics-play-counts', sid, days],
    queryFn: () => getPlayCounts(sid, days),
  });

  const { data: topAssets } = useQuery({
    queryKey: ['analytics-top-assets', sid, days],
    queryFn: () => getTopAssets(sid, days),
  });

  const { data: categories } = useQuery({
    queryKey: ['analytics-categories', sid, days],
    queryFn: () => getCategoryBreakdown(sid, days),
  });

  const { data: hourly } = useQuery({
    queryKey: ['analytics-hourly', sid, days],
    queryFn: () => getHourlyDistribution(sid, days),
  });

  const maxPlays = Math.max(...(playCounts?.map(d => d.plays) || [1]));
  const maxAssetPlays = Math.max(...(topAssets?.map(a => a.play_count) || [1]));
  const maxCatPlays = Math.max(...(categories?.map(c => c.play_count) || [1]));
  const maxHourly = Math.max(...(hourly?.map(h => h.plays) || [1]));

  return (
    <div className="max-w-6xl mx-auto p-8">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-3xl font-bold">Analytics</h1>
        <div className="flex gap-3">
          <select value={stationId} onChange={e => setStationId(e.target.value)}
            className="px-3 py-1.5 border rounded-lg text-sm">
            <option value="">All Stations</option>
            {stations?.stations?.map((s: any) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
          <select value={days} onChange={e => setDays(Number(e.target.value))}
            className="px-3 py-1.5 border rounded-lg text-sm">
            <option value={7}>Last 7 days</option>
            <option value={14}>Last 14 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
        </div>
      </div>

      {/* Summary Cards */}
      {summary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          <div className="bg-white p-4 rounded-lg shadow-md text-center">
            <div className="text-3xl font-bold text-blue-600">{summary.total_plays}</div>
            <div className="text-sm text-gray-500">Total Plays</div>
          </div>
          <div className="bg-white p-4 rounded-lg shadow-md text-center">
            <div className="text-3xl font-bold text-green-600">{summary.unique_assets}</div>
            <div className="text-sm text-gray-500">Unique Assets</div>
          </div>
          <div className="bg-white p-4 rounded-lg shadow-md text-center">
            <div className="text-3xl font-bold text-purple-600">{summary.total_airtime_hours}h</div>
            <div className="text-sm text-gray-500">Total Airtime</div>
          </div>
          <div className="bg-white p-4 rounded-lg shadow-md text-center">
            <div className="text-3xl font-bold text-orange-600">{summary.avg_plays_per_day}</div>
            <div className="text-sm text-gray-500">Avg Plays/Day</div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Daily Play Counts */}
        <div className="bg-white p-6 rounded-lg shadow-md">
          <h2 className="text-lg font-bold mb-4">Daily Plays</h2>
          <div className="space-y-1.5">
            {playCounts?.map(d => (
              <Bar key={d.date} value={d.plays} max={maxPlays} label={d.date} />
            ))}
            {(!playCounts || playCounts.length === 0) && (
              <p className="text-gray-400 text-center py-4">No data</p>
            )}
          </div>
        </div>

        {/* Top Assets */}
        <div className="bg-white p-6 rounded-lg shadow-md">
          <h2 className="text-lg font-bold mb-4">Top Assets</h2>
          <div className="space-y-1.5">
            {topAssets?.slice(0, 10).map(a => (
              <Bar key={a.id} value={a.play_count} max={maxAssetPlays}
                label={a.title} sublabel={a.artist || a.category || ''} />
            ))}
            {(!topAssets || topAssets.length === 0) && (
              <p className="text-gray-400 text-center py-4">No data</p>
            )}
          </div>
        </div>

        {/* Category Breakdown */}
        <div className="bg-white p-6 rounded-lg shadow-md">
          <h2 className="text-lg font-bold mb-4">By Category</h2>
          <div className="space-y-1.5">
            {categories?.map((c, i) => (
              <Bar key={i} value={c.play_count} max={maxCatPlays}
                label={c.category} sublabel={c.asset_type} />
            ))}
            {(!categories || categories.length === 0) && (
              <p className="text-gray-400 text-center py-4">No data</p>
            )}
          </div>
        </div>

        {/* Hourly Distribution */}
        <div className="bg-white p-6 rounded-lg shadow-md">
          <h2 className="text-lg font-bold mb-4">By Hour</h2>
          <div className="space-y-1.5">
            {hourly?.map(h => (
              <Bar key={h.hour} value={h.plays} max={maxHourly}
                label={`${h.hour.toString().padStart(2, '0')}:00`} />
            ))}
            {(!hourly || hourly.length === 0) && (
              <p className="text-gray-400 text-center py-4">No data</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
