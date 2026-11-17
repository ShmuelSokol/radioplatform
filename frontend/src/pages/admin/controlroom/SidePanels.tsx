import { memo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { usePlayLog } from '../../../hooks/useQueue';
import { useTimelinePreview } from '../../../hooks/useSchedules';
import { useCreateLiveShow, useEndLiveShow, useLiveShows, useStartLiveShow } from '../../../hooks/useLiveShows';
import { typeStyle } from './shared';
import { useAlerts, useResolveAlert } from '../../../hooks/useAlerts';

/* ── Play log ─────────────────────────────────────────────── */

export const LogPanel = memo(function LogPanel({ stationId, stationTz }: { stationId: string | null; stationTz?: string }) {
  const { data } = usePlayLog(stationId, true);
  const logs: any[] = data?.logs ?? [];
  return (
    <section className="h-full overflow-y-auto overscroll-contain bg-[#0a0a28]" aria-label="Recently played">
      {logs.map(log => (
        <div key={log.id} className="flex items-center gap-2 px-3 min-h-[44px] border-b border-[#15153a]">
          <span className="w-[68px] shrink-0 tabular-nums text-xs text-gray-400">
            {new Date(log.start_utc).toLocaleTimeString('en-US', { hour12: true, hour: 'numeric', minute: '2-digit', ...(stationTz ? { timeZone: stationTz } : {}) })}
          </span>
          <span className="flex-1 min-w-0 leading-tight">
            <span className="block truncate text-sm text-gray-100">{log.title ?? 'Unknown'}</span>
            <span className="block truncate text-xs text-gray-400">
              {log.artist ? `${log.artist} · ` : ''}
              <span className={typeStyle(log.asset_type).text}>{log.asset_type ?? ''}</span>
            </span>
          </span>
          <span className="shrink-0 text-[11px] text-gray-500">{log.source}</span>
        </div>
      ))}
      {logs.length === 0 && (
        <div className="text-center text-gray-500 py-10 px-4 text-sm">Nothing has played yet.</div>
      )}
    </section>
  );
});

/* ── Schedule status ──────────────────────────────────────── */

export const SchedulePanel = memo(function SchedulePanel({ stationId }: { stationId: string | null }) {
  const [at, setAt] = useState('');
  const { data, isLoading } = useTimelinePreview(stationId, at || null);
  const range = (b: { start_datetime: string; end_datetime: string }) =>
    `${new Date(b.start_datetime).toLocaleString()} to ${new Date(b.end_datetime).toLocaleString()}`;
  return (
    <section className="h-full overflow-y-auto overscroll-contain bg-[#0a0a28] p-3 space-y-3" aria-label="Schedule status">
      <div className="flex items-center gap-2">
        <input type="datetime-local" value={at} onChange={e => setAt(e.target.value)} aria-label="Check a time"
          className="flex-1 min-w-0 h-10 bg-[#14143c] border border-[#3a3a7e] text-cyan-100 text-base lg:text-sm px-2 rounded-md focus:outline-none focus:border-cyan-400" />
        <button type="button" onClick={() => setAt('')}
          className="h-10 px-3 rounded-md bg-white/10 hover:bg-white/20 text-sm font-semibold text-gray-100">Now</button>
      </div>
      {isLoading && <div className="text-cyan-300 text-sm">Loading…</div>}
      {data && (
        <>
          <div className={`rounded-lg border p-3 ${data.is_blacked_out ? 'bg-red-900/30 border-red-700' : 'bg-green-900/20 border-green-800'}`}>
            <div className="text-[11px] uppercase tracking-wider text-gray-400">Blackout</div>
            <div className={`text-base font-bold ${data.is_blacked_out ? 'text-red-300' : 'text-green-300'}`}>
              {data.is_blacked_out ? 'Blacked out' : 'Clear to broadcast'}
            </div>
            {data.current_blackout && (
              <div className="mt-1 text-sm text-red-200">{data.current_blackout.name}
                <div className="text-xs text-red-300/70">{range(data.current_blackout)}</div>
              </div>
            )}
          </div>
          <div className="rounded-lg border bg-[#14143c] border-[#3a3a7e] p-3">
            <div className="text-[11px] uppercase tracking-wider text-gray-400">Schedule block</div>
            {data.active_block ? (
              <>
                <div className="text-base font-bold text-cyan-200">{data.active_block.name}</div>
                <div className="text-sm text-gray-400">{data.active_block.schedule_name ?? ''}</div>
                <div className="text-xs text-gray-500">
                  {data.active_block.start_time} to {data.active_block.end_time} · {data.active_block.playback_mode}
                </div>
              </>
            ) : <div className="text-sm text-gray-500">No block is active</div>}
          </div>
          <div className="rounded-lg border bg-yellow-900/20 border-yellow-800/50 p-3">
            <div className="text-[11px] uppercase tracking-wider text-gray-400">Next blackout</div>
            {data.next_blackout ? (
              <>
                <div className="text-base font-bold text-yellow-200">{data.next_blackout.name}</div>
                <div className="text-xs text-yellow-300/70">{range(data.next_blackout)}</div>
              </>
            ) : <div className="text-sm text-gray-500">None scheduled</div>}
          </div>
        </>
      )}
      <Link to="/admin/schedules" className="block text-sm text-cyan-300 hover:underline">Open schedules</Link>
    </section>
  );
});

/* ── Go live ──────────────────────────────────────────────── */

export const LivePanel = memo(function LivePanel({ stationId, stationTz }: { stationId: string | null; stationTz?: string }) {
  const navigate = useNavigate();
  const { data } = useLiveShows(stationId ? { station_id: stationId } : undefined);
  const createMut = useCreateLiveShow();
  const startMut = useStartLiveShow();
  const endMut = useEndLiveShow();
  const [title, setTitle] = useState('');
  const [confirmEnd, setConfirmEnd] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const shows = (data?.shows ?? []).filter(s => !stationId || s.station_id === stationId);
  const live = shows.filter(s => s.status === 'live');
  const upcoming = shows
    .filter(s => s.status === 'scheduled')
    .sort((a, b) => (a.scheduled_start ?? '').localeCompare(b.scheduled_start ?? ''));
  const busy = createMut.isPending || startMut.isPending;

  const fail = (e: any) => setError(e?.response?.data?.detail || e?.response?.data?.message || 'That did not work. Try again.');

  const goLiveNow = async () => {
    if (!stationId || !title.trim()) return;
    setError(null);
    try {
      const show = await createMut.mutateAsync({ station_id: stationId, title: title.trim() });
      await startMut.mutateAsync(show.id);
      navigate(`/admin/live/${show.id}/host`);
    } catch (e) { fail(e); }
  };

  const startShow = async (id: string) => {
    setError(null);
    try {
      await startMut.mutateAsync(id);
      navigate(`/admin/live/${id}/host`);
    } catch (e) { fail(e); }
  };

  const when = (iso: string | null) => iso
    ? new Date(iso).toLocaleString('en-US', { weekday: 'short', hour: 'numeric', minute: '2-digit', ...(stationTz ? { timeZone: stationTz } : {}) })
    : 'No time set';

  return (
    <section className="h-full overflow-y-auto overscroll-contain bg-[#0a0a28] p-3 space-y-4" aria-label="Go live">
      {error && <div className="rounded-md bg-red-900/50 border border-red-600 text-red-100 text-sm px-3 py-2">{error}</div>}

      {live.map(show => (
        <div key={show.id} className="rounded-lg border-2 border-red-500 bg-red-950/50 p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-red-400 animate-pulse" />
            <span className="text-xs font-extrabold tracking-wider text-red-200">LIVE NOW</span>
          </div>
          <div className="text-base font-bold text-white">{show.title}</div>
          <div className="grid grid-cols-2 gap-2">
            <Link to={`/admin/live/${show.id}/host`} className="h-11 flex items-center justify-center rounded-md bg-white text-black text-sm font-bold">Host console</Link>
            <Link to={`/admin/live/${show.id}/screen`} className="h-11 flex items-center justify-center rounded-md bg-white/10 hover:bg-white/20 text-white text-sm font-bold">Call screener</Link>
          </div>
          {confirmEnd === show.id ? (
            <div className="grid grid-cols-2 gap-2">
              <button type="button" disabled={endMut.isPending}
                onClick={() => endMut.mutate(show.id, { onSettled: () => setConfirmEnd(null), onError: fail })}
                className="h-11 rounded-md bg-red-600 hover:bg-red-500 text-white text-sm font-bold disabled:opacity-50">
                {endMut.isPending ? 'Ending…' : 'Yes, end the show'}
              </button>
              <button type="button" onClick={() => setConfirmEnd(null)}
                className="h-11 rounded-md bg-white/10 hover:bg-white/20 text-white text-sm font-bold">Keep going</button>
            </div>
          ) : (
            <button type="button" onClick={() => setConfirmEnd(show.id)}
              className="w-full h-11 rounded-md bg-red-900/70 hover:bg-red-800 text-red-100 text-sm font-bold">
              End show and return to automation
            </button>
          )}
        </div>
      ))}

      {live.length === 0 && (
        <div className="rounded-lg border border-[#3a3a7e] bg-[#14143c] p-3 space-y-2">
          <div className="text-sm font-bold text-white">Go live now</div>
          <p className="text-xs text-gray-400">Starts a live show on this station and opens the host console with your microphone and caller queue.</p>
          <input type="text" value={title} onChange={e => setTitle(e.target.value)} placeholder="Show name" maxLength={120}
            onKeyDown={(e) => { if (e.key === 'Enter') goLiveNow(); }}
            className="w-full h-11 bg-[#0a0a28] border border-[#3a3a7e] text-white text-base lg:text-sm px-3 rounded-md placeholder-gray-500 focus:outline-none focus:border-red-400" />
          <button type="button" onClick={goLiveNow} disabled={busy || !title.trim() || !stationId}
            className="w-full h-12 rounded-md bg-red-600 hover:bg-red-500 text-white text-base font-extrabold disabled:opacity-40">
            {busy ? 'Starting…' : '● Go live'}
          </button>
        </div>
      )}

      <div>
        <div className="px-1 pb-1 text-[11px] font-bold uppercase tracking-wider text-gray-500">Scheduled shows</div>
        {upcoming.length === 0 && <div className="px-1 text-sm text-gray-500">No shows are scheduled.</div>}
        <div className="space-y-2">
          {upcoming.map(show => (
            <div key={show.id} className="flex items-center gap-2 rounded-lg border border-[#2a2a5e] bg-[#101036] p-2.5">
              <div className="flex-1 min-w-0 leading-tight">
                <div className="truncate text-sm font-semibold text-white">{show.title}</div>
                <div className="text-xs text-gray-400">{when(show.scheduled_start)}</div>
              </div>
              <button type="button" onClick={() => startShow(show.id)} disabled={busy || live.length > 0}
                title={live.length > 0 ? 'End the current live show first' : 'Start this show now'}
                className="cr-tap h-10 px-3 shrink-0 rounded-md bg-red-600 hover:bg-red-500 text-white text-sm font-bold disabled:opacity-40">
                Start
              </button>
            </div>
          ))}
        </div>
        <Link to="/admin/live" className="cr-tap flex items-center mt-3 px-1 text-sm text-cyan-300 hover:underline">Schedule or edit live shows</Link>
      </div>
    </section>
  );
});

/* ── Errors (KBR Studio's Errors button): open problems on this station ── */

/** When an alert happened, in station time: "3:04 PM" today, "Nov 9 3:04 PM" on another
 *  day, so an alert from last week does not read like one from today. */
export function alertWhen(iso: string, tz?: string, now: Date = new Date()): string {
  const d = new Date(iso);
  const zone = tz ? { timeZone: tz } : {};
  const day = (x: Date) => x.toLocaleDateString('en-CA', zone);
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', ...zone });
  return day(d) === day(now) ? time : `${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', ...zone })} ${time}`;
}

export const ErrorsPanel = memo(function ErrorsPanel({ stationId, stationTz, canSee }: {
  stationId: string | null; stationTz?: string; canSee: boolean;
}) {
  const { data, isLoading, isError } = useAlerts(canSee ? { is_resolved: false, limit: 100, station_id: stationId ?? undefined } : null, true);
  const resolve = useResolveAlert();
  const rows = (data?.alerts ?? []).filter(a => !a.station_id || a.station_id === stationId);
  const sev = (s: string) => (s === 'critical' ? 'text-red-400' : s === 'warning' ? 'text-amber-300' : 'text-cyan-300');
  return (
    <section className="h-full overflow-y-auto overscroll-contain bg-[#0a0a28]" aria-label="Errors" data-testid="errors-panel">
      {!canSee && <p className="p-4 text-sm text-gray-400">Errors are shown to managers.</p>}
      {canSee && isLoading && <p className="p-4 text-sm text-gray-400">Loading…</p>}
      {canSee && isError && <p className="p-4 text-sm text-red-300">Can't load the errors.</p>}
      {canSee && !isLoading && !isError && rows.length === 0 && (
        <p className="p-4 text-sm text-gray-400">No open errors for this station.</p>
      )}
      {rows.map(a => (
        <div key={a.id} className="flex items-start gap-2 px-3 py-2 border-b border-[#15153a]">
          <span className="w-[68px] shrink-0 tabular-nums text-xs text-gray-400 pt-0.5" data-testid="alert-when">
            {alertWhen(a.updated_at || a.created_at, stationTz)}
          </span>
          <span className="flex-1 min-w-0 leading-tight">
            <span className={`block text-sm font-bold ${sev(a.severity)}`}>{a.title}</span>
            <span className="block text-xs text-gray-300 break-words">{a.message}</span>
          </span>
          <button type="button" onClick={() => resolve.mutate(a.id)} disabled={resolve.isPending}
            className="qn-btn shrink-0 h-9 px-2 text-xs" aria-label={`Mark "${a.title}" as dealt with`}>Done</button>
        </div>
      ))}
      {canSee && <Link to="/admin/alerts" className="block px-3 py-3 text-xs text-cyan-300 underline">All alerts</Link>}
    </section>
  );
});
