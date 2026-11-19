import { memo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { usePlayLog } from '../../../hooks/useQueue';
import { useTimelinePreview } from '../../../hooks/useSchedules';
import { useCreateLiveShow, useEndLiveShow, useLiveShows, useStartLiveShow } from '../../../hooks/useLiveShows';
import { clockTimeSec, typeStyle } from './shared';
import { useMayChange } from '../../../hooks/useMayChange';
import { useAlerts, useResolveAlert } from '../../../hooks/useAlerts';

/* ── Play log ─────────────────────────────────────────────── */

/** When an item started, to the second, on the station's clock: "8:30:05 AM". */
function PlayedAt({ iso, tz }: { iso: string; tz?: string }) {
  const { time, ampm } = clockTimeSec(iso, tz);
  return (
    <span className="w-[80px] shrink-0 whitespace-nowrap tabular-nums text-xs text-[#c0c0c0]" data-testid="played-at">
      {time}<span className="ml-0.5 text-[10px]">{ampm}</span>
    </span>
  );
}

export const LogPanel = memo(function LogPanel({ stationId, stationTz }: { stationId: string | null; stationTz?: string }) {
  const { data } = usePlayLog(stationId, true);
  const logs: any[] = data?.logs ?? [];
  return (
    <section className="h-full overflow-y-auto overscroll-contain qn-log" aria-label="Recently played">
      {logs.map(log => (
        <div key={log.id} className="qn-row flex items-center gap-2 px-3 min-h-[44px] border-b border-[#222]">
          <PlayedAt iso={log.start_utc} tz={stationTz} />
          <span className="flex-1 min-w-0 leading-tight">
            <span className="block truncate text-sm">{log.title ?? 'Unknown'}</span>
            <span className="block truncate text-xs font-normal text-[#c0c0c0]">
              {log.artist ? `${log.artist} · ` : ''}
              <span className={typeStyle(log.asset_type).text}>{log.asset_type ?? ''}</span>
            </span>
          </span>
          <span className="shrink-0 text-[11px] font-normal text-[#a0a0a0]">{log.source}</span>
        </div>
      ))}
      {logs.length === 0 && (
        <div className="text-center text-[#c0c0c0] font-normal py-10 px-4 text-sm">Nothing has played yet.</div>
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
    <section className="h-full overflow-y-auto overscroll-contain bg-[#c0c0c0] text-black p-3 space-y-3" aria-label="Schedule status">
      <div className="flex items-center gap-2">
        <input type="datetime-local" value={at} onChange={e => setAt(e.target.value)} aria-label="Check a time"
          className="qn-field flex-1 min-w-0 h-11 lg:h-8 text-base lg:text-sm px-2" />
        <button type="button" onClick={() => setAt('')}
          className="qn-btn cr-tap h-11 lg:h-8 px-3 text-sm">Now</button>
      </div>
      {isLoading && <div className="text-sm">Loading…</div>}
      {data && (
        <>
          <div className="bg-black qn-sunken px-2 py-1.5">
            <div className="qn-lcd-label">Blackout</div>
            <div className={`text-base font-bold ${data.is_blacked_out ? 'text-[#ff5050]' : 'text-[var(--qn-digit)]'}`}>
              {data.is_blacked_out ? 'Blacked out' : 'Clear to broadcast'}
            </div>
            {data.current_blackout && (
              <div className="mt-1 text-sm font-bold text-[var(--qn-yellow)]">{data.current_blackout.name}
                <div className="text-xs font-normal text-white">{range(data.current_blackout)}</div>
              </div>
            )}
          </div>
          <div className="bg-white qn-sunken p-2">
            <div className="text-xs font-bold">Schedule block</div>
            {data.active_block ? (
              <>
                <div className="text-base font-bold text-[#000080]">{data.active_block.name}</div>
                <div className="text-sm">{data.active_block.schedule_name ?? ''}</div>
                <div className="text-xs">
                  {data.active_block.start_time} to {data.active_block.end_time} · {data.active_block.playback_mode}
                </div>
              </>
            ) : <div className="text-sm">No block is active</div>}
          </div>
          <div className="bg-white qn-sunken p-2">
            <div className="text-xs font-bold">Next blackout</div>
            {data.next_blackout ? (
              <>
                <div className="text-base font-bold text-[#800000]">{data.next_blackout.name}</div>
                <div className="text-xs">{range(data.next_blackout)}</div>
              </>
            ) : <div className="text-sm">None scheduled</div>}
          </div>
        </>
      )}
      <Link to="/admin/schedules" className="qn-btn cr-tap h-11 lg:h-8 px-3 text-sm flex items-center justify-center !no-underline !text-black">Open schedules</Link>
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
  // Going live is for admins, managers and DJs (the Live screens' rule); a viewer sees what is on
  const mayGoLive = useMayChange('/admin/live');

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
    <section className="h-full overflow-y-auto overscroll-contain bg-[#c0c0c0] text-black p-3 space-y-3" aria-label="Go live">
      {error && <div className="bg-white qn-sunken px-2 py-1.5 text-sm font-bold text-red-700" role="alert">{error}</div>}

      {live.map(show => (
        <div key={show.id} className="space-y-2">
          <div className="bg-black qn-sunken px-2 py-1.5">
            <div className="flex items-center gap-2">
              <span className="w-2.5 h-2.5 rounded-full bg-red-500 animate-pulse" />
              <span className="text-xs font-extrabold tracking-wider text-[#ff5050]">LIVE NOW</span>
            </div>
            <div className="text-base font-bold leading-tight break-words text-[var(--qn-digit)]">{show.title}</div>
          </div>
          {mayGoLive && <div className="grid grid-cols-2 gap-2">
            <Link to={`/admin/live/${show.id}/host`} className="qn-btn h-11 lg:h-9 flex items-center justify-center text-sm font-bold !no-underline !text-black">Host console</Link>
            <Link to={`/admin/live/${show.id}/screen`} className="qn-btn h-11 lg:h-9 flex items-center justify-center text-sm font-bold !no-underline !text-black">Call screener</Link>
          </div>}
          {!mayGoLive ? null : confirmEnd === show.id ? (
            <div className="grid grid-cols-2 gap-2">
              <button type="button" disabled={endMut.isPending}
                onClick={() => endMut.mutate(show.id, { onSettled: () => setConfirmEnd(null), onError: fail })}
                className="qn-btn h-11 lg:h-9 text-sm font-bold !text-red-700">
                {endMut.isPending ? 'Ending…' : 'Yes, end the show'}
              </button>
              <button type="button" onClick={() => setConfirmEnd(null)}
                className="qn-btn h-11 lg:h-9 text-sm font-bold">Keep going</button>
            </div>
          ) : (
            <button type="button" onClick={() => setConfirmEnd(show.id)}
              className="qn-btn w-full h-11 lg:h-9 text-sm font-bold !text-red-700">
              End show and return to automation
            </button>
          )}
        </div>
      ))}

      {live.length === 0 && !mayGoLive && <p className="px-1 text-sm" data-testid="live-view-only">No live show is on. Your login can watch this screen but not go live.</p>}
      {live.length === 0 && mayGoLive && (
        <div className="space-y-2">
          <div className="qn-title h-7 flex items-center px-2 text-sm">Go live now</div>
          <p className="text-xs">Starts a live show on this station and opens the host console with your microphone and caller queue.</p>
          <input type="text" value={title} onChange={e => setTitle(e.target.value)} placeholder="Show name" maxLength={120}
            aria-label="Show name"
            onKeyDown={(e) => { if (e.key === 'Enter') goLiveNow(); }}
            className="qn-field w-full h-11 lg:h-9 text-base lg:text-sm px-2 placeholder-[#808080]" />
          <button type="button" onClick={goLiveNow} disabled={busy || !title.trim() || !stationId}
            className="qn-btn w-full h-12 lg:h-10 text-base font-extrabold enabled:!text-red-700">
            {busy ? 'Starting…' : '● Go live'}
          </button>
        </div>
      )}

      <div>
        <div className="qn-title h-7 flex items-center px-2 text-sm mb-2">Scheduled shows</div>
        {upcoming.length === 0 && <div className="px-1 text-sm">No shows are scheduled.</div>}
        {upcoming.length > 0 && <div className="bg-white qn-sunken p-0.5">
          {upcoming.map(show => (
            <div key={show.id} className="flex items-center gap-2 px-2 py-1.5 border-b border-[#c0c0c0] last:border-b-0">
              <div className="flex-1 min-w-0 leading-tight">
                <div className="truncate text-sm font-bold">{show.title}</div>
                <div className="text-xs">{when(show.scheduled_start)}</div>
              </div>
              {mayGoLive && <button type="button" onClick={() => startShow(show.id)} disabled={busy || live.length > 0}
                title={live.length > 0 ? 'End the current live show first' : 'Start this show now'}
                className="qn-btn cr-tap h-11 lg:h-8 px-3 shrink-0 text-sm font-bold enabled:!text-red-700">
                Start
              </button>}
            </div>
          ))}
        </div>}
        <Link to="/admin/live" className="qn-btn cr-tap h-11 lg:h-8 mt-3 px-3 text-sm flex items-center justify-center !no-underline !text-black">Schedule or edit live shows</Link>
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

/** How many times the server folded this problem into one alert (context.repeats), and its
 *  newest wording, so a second Panic visibly changes the row instead of looking like nothing. */
function repeatsOf(a: { context?: Record<string, unknown> | null }): number {
  const n = Number(a.context?.repeats);
  return Number.isFinite(n) ? n : 1;
}
function lastMessage(a: { message: string; context?: Record<string, unknown> | null }): string {
  const m = a.context?.last_message;
  return typeof m === 'string' && m ? m : a.message;
}

export const ErrorsPanel = memo(function ErrorsPanel({ stationId, stationTz, canSee }: {
  stationId: string | null; stationTz?: string; canSee: boolean;
}) {
  const { data, isLoading, isError } = useAlerts(canSee ? { is_resolved: false, limit: 100, station_id: stationId ?? undefined } : null, true);
  const resolve = useResolveAlert();
  const rows = (data?.alerts ?? []).filter(a => !a.station_id || a.station_id === stationId);
  const sev = (s: string) => (s === 'critical' ? 'text-red-500' : s === 'warning' ? 'text-[var(--qn-orange)]' : 'text-[var(--qn-yellow)]');
  return (
    <section className="h-full overflow-y-auto overscroll-contain qn-log" aria-label="Errors" data-testid="errors-panel">
      {!canSee && <p className="p-4 text-sm font-normal text-[#c0c0c0]">Errors are shown to managers.</p>}
      {canSee && isLoading && <p className="p-4 text-sm font-normal text-[#c0c0c0]">Loading…</p>}
      {canSee && isError && <p className="p-4 text-sm text-[#ff5050]">Can't load the errors.</p>}
      {canSee && !isLoading && !isError && rows.length === 0 && (
        <p className="p-4 text-sm font-normal text-[#c0c0c0]">No open errors for this station.</p>
      )}
      {rows.map(a => (
        <div key={a.id} className="qn-row flex items-start gap-2 px-3 py-2 border-b border-[#222]">
          <span className="w-[68px] shrink-0 tabular-nums text-xs text-[#c0c0c0] pt-0.5" data-testid="alert-when">
            {alertWhen(a.updated_at || a.created_at, stationTz)}
          </span>
          <span className="flex-1 min-w-0 leading-tight">
            <span className={`block text-sm font-bold ${sev(a.severity)}`}>{a.title}</span>
            <span className="block text-xs font-normal text-white break-words">{lastMessage(a)}</span>
            {repeatsOf(a) > 1 && (
              <span className="block text-xs font-normal text-[var(--qn-orange)]" data-testid="alert-repeats">
                {repeatsOf(a)} times, first {alertWhen(a.created_at, stationTz)}, last {alertWhen(a.updated_at || a.created_at, stationTz)}
              </span>
            )}
          </span>
          <button type="button" onClick={() => resolve.mutate(a.id)} disabled={resolve.isPending}
            className="qn-btn shrink-0 h-9 px-2 text-xs" aria-label={`Mark "${a.title}" as dealt with`}>Done</button>
        </div>
      ))}
      {canSee && <Link to="/admin/alerts" className="block px-3 py-3 text-xs text-[#00ffff] underline">All alerts</Link>}
    </section>
  );
});
