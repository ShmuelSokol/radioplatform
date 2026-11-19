import { memo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getStationDay } from '../../../api/spotSchedules';
import type { StationDayPlay } from '../../../api/spotSchedules';

/**
 * The control room's Sponsors list: every sponsorship play on this station today
 * (aired, on air, waiting, skipped), one tap each to open the Sponsorship panel.
 */
export const SponsorsToday = memo(function SponsorsToday({ stationId, onOpen }: {
  stationId: string | null;
  onOpen: (play: StationDayPlay) => void;
}) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['sponsors-today', stationId],
    queryFn: () => getStationDay(stationId as string),
    enabled: !!stationId,
    refetchInterval: 30_000,
    staleTime: 30_000,
  });
  const plays = data?.plays ?? [];
  const waiting = plays.filter(p => p.status === 'pending').length;
  const aired = plays.filter(p => p.status === 'played').length;
  return (
    <section className="h-full flex flex-col qn-log" aria-label="Sponsorships today" data-testid="sponsors-today">
      <div className="shrink-0 px-3 py-1.5 text-xs qn-log-head" data-testid="sponsors-today-summary">
        {data ? `Sponsorships today on ${data.station.name}: ${aired} aired, ${waiting} waiting. Tap one for its sponsor, contract and plays.` : isLoading ? 'Loading…' : ''}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain">
        {plays.map(p => (
          <button key={p.entry_id} type="button" onClick={() => onOpen(p)} data-testid="sponsors-today-row" data-nav-id={`sponsor-play-${p.entry_id}`}
            className="qn-row w-full text-left flex items-center gap-2 px-3 min-h-[44px] border-b border-[#222] focus:bg-[#303030] focus:outline-none">
            <span className="w-[68px] shrink-0 tabular-nums text-xs text-[#c0c0c0]">{p.local_time}</span>
            <span className="flex-1 min-w-0 leading-tight">
              <span className="block truncate text-sm text-[var(--qn-orange)]">{p.sponsor_name || p.asset.title}</span>
              {p.sponsor_name && <span className="block truncate text-xs font-normal text-[#c0c0c0]">{p.asset.title}</span>}
            </span>
            <span className={`shrink-0 text-[11px] font-bold ${
              p.status === 'played' ? 'text-[var(--qn-digit)]' : p.status === 'playing' ? 'text-[#ff5050]'
                : p.status === 'pending' ? 'text-[var(--qn-yellow)]' : 'text-[#808080] line-through'}`}>{p.status_text}</span>
            <span className="shrink-0 text-[#c0c0c0]" aria-hidden="true">›</span>
          </button>
        ))}
        {data && plays.length === 0 && (
          <div className="text-center text-[#c0c0c0] font-normal py-10 px-4 text-sm" data-testid="sponsors-today-empty">No sponsorships on this station today.</div>
        )}
        {isError && (
          <div className="text-center text-white font-normal py-6 px-4 text-sm">
            Could not load today's sponsorships. <button type="button" onClick={() => refetch()} className="qn-btn px-2 h-8 text-xs !text-black">Try again</button>
          </div>
        )}
      </div>
    </section>
  );
});
