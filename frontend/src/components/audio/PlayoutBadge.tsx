import { useQuery } from '@tanstack/react-query';
import apiClient from '../../api/client';

/**
 * Where a station plays from, with a health dot (Shmuel: "how do I know when it's
 * playing from the server or not?"). "Server stream": every listener hears the one
 * stream from the playout server, so everyone hears the same thing at the same
 * moment. "Browser player": each page plays the files itself.
 */
export interface PlayoutStatus {
  mode: 'server' | 'browser';
  engine?: boolean;
  engine_ok?: boolean;
  in_sync?: boolean;
  engine_title?: string | null;
}

export function usePlayoutStatus() {
  return useQuery({
    queryKey: ['playout-status'],
    queryFn: async () =>
      (await apiClient.get<{ stations: Record<string, PlayoutStatus>; default: PlayoutStatus }>('/playout-status')).data,
    refetchInterval: 15000,
    staleTime: 10000,
  });
}

export function playoutOf(data: { stations: Record<string, PlayoutStatus> } | undefined, stationId: string | null | undefined): PlayoutStatus | null {
  if (!data || !stationId) return null;
  return data.stations?.[stationId] ?? { mode: 'browser' };
}

function describe(s: PlayoutStatus): { label: string; dot: string; title: string } {
  if (s.mode === 'browser') {
    return {
      label: 'Browser player', dot: 'bg-gray-400',
      title: s.engine
        ? 'The stream server runs this station, but listeners are still on the built-in player (each page plays the files itself).'
        : 'Each listener\'s page plays the files itself (built-in player), not the stream server.',
    };
  }
  if (!s.engine_ok) {
    return { label: 'Server stream: not answering', dot: 'bg-red-500 animate-pulse',
      title: 'Listeners are on the stream server, but it did not answer the last check.' };
  }
  if (!s.in_sync) {
    return { label: 'Server stream: out of step', dot: 'bg-yellow-400',
      title: `The stream server is playing "${s.engine_title ?? '?'}", which is not what the station's queue shows as on air.` };
  }
  return { label: 'Server stream', dot: 'bg-green-500',
    title: 'Every listener hears the same stream from the server, at the same moment. Server and queue agree on what is on air.' };
}

export default function PlayoutBadge({ stationId, dark = false, className = '' }: { stationId: string | null | undefined; dark?: boolean; className?: string }) {
  const { data } = usePlayoutStatus();
  const s = playoutOf(data, stationId);
  if (!s) return null;
  const d = describe(s);
  return (
    <span title={d.title} data-testid="playout-badge" data-mode={s.mode}
      className={`inline-flex items-center gap-1.5 text-[11px] leading-none whitespace-nowrap ${dark ? 'text-gray-200' : 'text-gray-600'} ${className}`}>
      <span className={`inline-block w-2 h-2 rounded-full ${d.dot}`} />
      {d.label}
    </span>
  );
}
