import { useQueries } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getHolidayOverview } from '../../api/holidayMusic';

/**
 * A note on the Playlists and Schedule blocks pages while a holiday's list is being mixed
 * in on a station: the holiday mix sits on top of every playlist (one holiday item in N
 * ordinary ones of the same kind), so it is not a step in any playlist. The note says so
 * and links to Holiday Music, where it is set (see wiki/holiday-music.md).
 */
export default function HolidayMixNote({ stations }: { stations: { id: string; name: string }[] }) {
  const results = useQueries({
    queries: stations.map(s => ({
      queryKey: ['holiday-music', s.id],
      queryFn: () => getHolidayOverview(s.id),
      staleTime: 5 * 60_000,
    })),
  });
  const lines = results.flatMap((r, i) => {
    const station = stations[i];
    return (r.data?.holidays ?? [])
      .filter(h => h.in_season && h.settings.enabled && h.items.music + h.items.long > 0 && h.today_every)
      .map(h => ({ station, h }));
  });
  if (!lines.length) return null;
  return (
    <div className="mb-4 max-w-3xl rounded-lg border border-purple-200 bg-purple-50 px-4 py-3 text-sm text-purple-900"
      data-testid="holiday-mix-note">
      {lines.map(({ station, h }) => (
        <p key={station.id + h.key}>
          <strong>{h.name}</strong> items are mixed in on <strong>{station.name}</strong> now, on top of
          these playlists: 1 in {h.today_every} songs today{h.settings.include_shiurim ? ' (shiurim and stories too)' : ''}.{' '}
          <Link to={`/admin/holiday-music?station=${station.id}&holiday=${h.key}`} className="underline font-medium">
            Change it on Holiday Music
          </Link>
        </p>
      ))}
    </div>
  );
}
