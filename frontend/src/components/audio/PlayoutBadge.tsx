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

function describe(s: PlayoutStatus): { label: string; dot: string; title: string; chip?: string } {
  if (s.mode === 'browser') {
    return {
      label: 'Browser player', dot: 'bg-gray-400', chip: '#6b7280',
      title: s.engine
        ? 'The stream server runs this station, but listeners are still on the built-in player (each page plays the files itself).'
        : 'Each listener\'s page plays the files itself (built-in player), not the stream server.',
    };
  }
  if (!s.engine_ok) {
    return { label: 'Server stream: not answering', dot: 'bg-red-500 animate-pulse', chip: '#b91c1c',
      title: 'Listeners are on the stream server, but it did not answer the last check.' };
  }
  if (!s.in_sync) {
    return { label: 'Server stream: out of step', dot: 'bg-yellow-400', chip: '#a16207',
      title: `The stream server is playing "${s.engine_title ?? '?'}", which is not what the station's queue shows as on air.` };
  }
  return { label: 'Server stream', dot: 'bg-green-500', chip: '#15803d',
    title: 'Every listener hears the same stream from the server, at the same moment. Server and queue agree on what is on air.' };
}

/**
 * `listener`: the public pages. Listeners never see the engine's health or its track
 * titles ("out of step", "not answering"): a server-stream station shows a plain
 * "Live stream", the browser player shows nothing. The control room keeps the detail.
 *
 * `chip`: the staff pages (control room, Manage Stations). The same words on a solid
 * coloured label, so it is seen at a glance (Shmuel 2026-11-17: "I also want to be able
 * to see if the station is running on server or not"; the small grey text was missed).
 * Colours are inline: the admin skin restyles Tailwind colour classes.
 */
export default function PlayoutBadge({ stationId, dark = false, listener = false, chip = false, className = '' }: { stationId: string | null | undefined; dark?: boolean; listener?: boolean; chip?: boolean; className?: string }) {
  const { data } = usePlayoutStatus();
  const s = playoutOf(data, stationId);
  if (!s) return null;
  if (listener && s.mode !== 'server') return null;
  const d = listener
    ? { label: 'Live stream', dot: 'bg-green-500', title: 'Everyone hears the same live stream.' }
    : describe(s);
  if (chip && !listener) {
    const c = describe(s);
    return (
      <span title={c.title} data-testid="playout-badge" data-mode={s.mode} data-chip="1"
        style={{ backgroundColor: c.chip, color: '#fff' }}
        className={`inline-flex items-center gap-1.5 rounded px-1.5 py-1 text-[11px] font-bold leading-none whitespace-nowrap ${className}`}>
        <span className="inline-block w-2 h-2 rounded-full bg-white" />
        {c.label}
      </span>
    );
  }
  return (
    <span title={d.title} data-testid="playout-badge" data-mode={s.mode}
      className={`inline-flex items-center gap-1.5 text-[11px] leading-none whitespace-nowrap ${dark ? 'text-gray-200' : 'text-gray-600'} ${className}`}>
      <span className={`inline-block w-2 h-2 rounded-full ${d.dot}`} />
      {d.label}
    </span>
  );
}
