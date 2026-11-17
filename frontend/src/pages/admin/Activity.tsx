import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  getActivity, getActivityFilters, getListenerSessions,
  type ActivityItem, type ActivityObject, type ListenerSessionItem,
} from '../../api/activity';

/**
 * Admin Activity page: who did what on the site (every change, sign-in and
 * screen opened by staff), with filters, search, paging and live updates; and a
 * Listeners tab with each listening session. Backend: api/v1/activity.py,
 * see wiki/activity-log.md.
 */

const PAGE_SIZE = 50;
const LIVE_EVERY_MS = 5000;

const CATEGORY_STYLE: Record<string, string> = {
  signin: 'bg-sky-900/70 text-sky-200',
  control: 'bg-red-900/70 text-red-200',
  live: 'bg-orange-900/70 text-orange-200',
  library: 'bg-emerald-900/70 text-emerald-200',
  schedule: 'bg-amber-900/70 text-amber-200',
  sponsors: 'bg-yellow-900/70 text-yellow-200',
  users: 'bg-purple-900/70 text-purple-200',
  settings: 'bg-cyan-900/70 text-cyan-200',
  view: 'bg-gray-800 text-gray-300',
  other: 'bg-gray-800 text-gray-300',
};

const TYPE_NAMES: Record<string, string> = {
  queue_entries: 'Queue item', assets: 'Library item', stations: 'Station', users: 'User',
  schedules: 'Schedule', schedule_blocks: 'Schedule block', playlist_entries: 'Playlist entry',
  playlist_templates: 'Playlist', template_slots: 'Playlist slot', categories: 'Category',
  program_blocks: 'Program block', sponsors: 'Sponsor', spot_schedules: 'Spot schedule',
  spot_recordings: 'Spot recording', live_shows: 'Live show', call_in_requests: 'Caller',
  holiday_windows: 'Blackout', schedule_rules: 'Rule', now_playing: 'On air', play_logs: 'Play log',
  site_release: 'Site update', alerts: 'Alert',
};

const inputCls = 'bg-[#0a0a28] border border-[#2a2a5e] text-cyan-100 px-2 py-1.5 rounded focus:outline-none focus:border-cyan-600 min-w-0';

function fmtTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date(); yesterday.setDate(today.getDate() - 1);
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' });
  if (d.toDateString() === today.toDateString()) return `Today ${time}`;
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${time}`;
  return `${d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })} ${time}`;
}

function fmtDuration(sec: number): string {
  if (sec < 60) return `${sec}s`;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function show(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

function pretty(field: string): string {
  return field.replace(/_/g, ' ');
}

/** Start of a day in local time, as ISO. */
function dayStart(dateStr: string): string {
  return new Date(`${dateStr}T00:00:00`).toISOString();
}
function dayAfter(dateStr: string): string {
  const d = new Date(`${dateStr}T00:00:00`);
  d.setDate(d.getDate() + 1);
  return d.toISOString();
}
function localDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function useFilters() {
  return useQuery({ queryKey: ['activity', 'filters'], queryFn: getActivityFilters, staleTime: 5 * 60_000 });
}

function Pager({ page, total, onPage }: { page: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const from = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const to = Math.min(total, (page + 1) * PAGE_SIZE);
  return (
    <div className="flex items-center justify-between gap-2 py-2 text-[12px] text-gray-400">
      <span>{from}–{to} of {total.toLocaleString()}</span>
      <div className="flex gap-2">
        <button type="button" disabled={page === 0} onClick={() => onPage(page - 1)}
          className="px-3 min-h-[36px] rounded bg-[#1a1a4e] text-cyan-200 disabled:opacity-40">‹ Newer</button>
        <button type="button" disabled={page + 1 >= pages} onClick={() => onPage(page + 1)}
          className="px-3 min-h-[36px] rounded bg-[#1a1a4e] text-cyan-200 disabled:opacity-40">Older ›</button>
      </div>
    </div>
  );
}

function DatePresets({ from, to, setFrom, setTo }: { from: string; to: string; setFrom: (v: string) => void; setTo: (v: string) => void }) {
  const today = localDate(new Date());
  const daysAgo = (n: number) => { const d = new Date(); d.setDate(d.getDate() - n); return localDate(d); };
  const presets: [string, string, string][] = [
    ['Today', today, today],
    ['Yesterday', daysAgo(1), daysAgo(1)],
    ['7 days', daysAgo(6), today],
    ['30 days', daysAgo(29), today],
    ['All', '', ''],
  ];
  return (
    <div className="flex flex-wrap gap-1">
      {presets.map(([label, f, t]) => {
        const on = from === f && to === t;
        return (
          <button key={label} type="button" onClick={() => { setFrom(f); setTo(t); }}
            className={`px-2.5 min-h-[32px] rounded text-[12px] ${on ? 'bg-cyan-700 text-white' : 'bg-[#1a1a4e] text-cyan-200 hover:bg-[#24245e]'}`}>
            {label}
          </button>
        );
      })}
    </div>
  );
}

function ObjectChanges({ obj }: { obj: ActivityObject }) {
  const fields = Object.entries(obj.fields || {});
  const typeName = TYPE_NAMES[obj.type] || pretty(obj.type);
  const verb = obj.op === 'create' ? 'Added' : obj.op === 'delete' ? 'Deleted' : 'Changed';
  return (
    <div className="border border-[#2a2a5e] rounded p-2">
      <div className="text-[12px] text-gray-300 mb-1">
        <span className={obj.op === 'delete' ? 'text-red-300' : obj.op === 'create' ? 'text-green-300' : 'text-amber-300'}>{verb}</span>{' '}
        {typeName}{obj.name ? <> · <span className="text-white">{obj.name}</span></> : null}
      </div>
      {fields.length > 0 && (
        <div className="space-y-1">
          {fields.map(([k, v]) => (
            <div key={k} className="text-[12px] grid grid-cols-1 sm:grid-cols-[10rem_1fr] gap-x-2">
              <span className="text-gray-500">{pretty(k)}</span>
              <span className="break-words min-w-0">
                {obj.op === 'update' && (
                  <><span className="text-red-300/80 line-through decoration-red-500/50">{show(v.old)}</span>{' → '}</>
                )}
                <span className="text-green-200">{show(v.new)}</span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ActivityDetails({ item }: { item: ActivityItem }) {
  const objects = item.changes?.objects ?? [];
  const counts = item.changes?.counts ?? {};
  const countText = Object.entries(counts)
    .filter(([, n]) => n > 1)
    .map(([k, n]) => { const [t, op] = k.split(':'); return `${n} × ${TYPE_NAMES[t] || pretty(t)} ${op === 'create' ? 'added' : op === 'delete' ? 'deleted' : 'changed'}`; });
  return (
    <div className="mt-2 space-y-2 pl-0 sm:pl-4">
      {objects.length > 0 ? objects.map((o, i) => <ObjectChanges key={i} obj={o} />)
        : item.category !== 'view' && item.category !== 'signin' && (
          <div className="text-[12px] text-gray-500">No saved fields changed.</div>
        )}
      {countText.length > 0 && <div className="text-[12px] text-gray-400">{countText.join(' · ')}</div>}
      {item.changes?.request !== undefined && (
        <details className="text-[12px]">
          <summary className="cursor-pointer text-gray-400">What was sent</summary>
          <pre className="mt-1 p-2 bg-[#05051a] rounded overflow-x-auto text-[11px] text-gray-300 whitespace-pre-wrap break-words">
            {JSON.stringify(item.changes.request, null, 2)}
          </pre>
        </details>
      )}
      <div className="text-[11px] text-gray-500 break-words">
        {item.method} {item.path}
        {item.request_id && <> · request {item.request_id}</>}
        {item.user_agent && <div className="mt-0.5">{item.user_agent}</div>}
      </div>
    </div>
  );
}

function StatusBadge({ code }: { code: number | null }) {
  if (!code || code < 400) return null;
  const text = code === 403 ? 'Refused' : code === 401 ? 'Wrong password' : code === 404 ? 'Not found' : code === 422 ? 'Invalid' : 'Failed';
  return <span className="px-1.5 py-0.5 rounded bg-red-800 text-red-100 text-[11px]">{text}</span>;
}

function ActivityRow({ item, fresh }: { item: ActivityItem; fresh: boolean }) {
  const [open, setOpen] = useState(false);
  const who = item.user_name || item.user_email || 'Someone';
  return (
    <li className={`border-b border-[#1e1e4a] px-3 py-2 transition-colors ${fresh ? 'bg-cyan-900/30' : ''}`} data-testid="activity-row">
      <button type="button" onClick={() => setOpen(o => !o)} className="w-full text-left">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-[12px] text-gray-400 tabular-nums shrink-0 w-full sm:w-44">{fmtTime(item.created_at)}</span>
          <span className="text-cyan-200 font-semibold">{who}</span>
          {item.user_role && <span className="text-[10px] uppercase text-gray-500">{item.user_role}</span>}
          <span className={`px-1.5 py-0.5 rounded text-[11px] ${CATEGORY_STYLE[item.category] || CATEGORY_STYLE.other}`}>{item.label}</span>
          <StatusBadge code={item.status_code} />
          {item.detail && item.category !== 'view' && <span className="text-white break-words min-w-0">{item.detail}</span>}
          {item.station_name && item.station_name !== item.detail && <span className="text-[11px] px-1.5 py-0.5 rounded bg-[#1a1a4e] text-indigo-200">{item.station_name}</span>}
        </div>
        <div className="text-[11px] text-gray-500 mt-0.5 sm:pl-[11.5rem] flex flex-wrap gap-x-3">
          {item.device && <span>{item.device}</span>}
          {item.location && <span>{item.location}</span>}
          {item.ip_address && <span>{item.ip_address}</span>}
          <span className="text-gray-600">{open ? 'Hide details ▲' : 'Details ▼'}</span>
        </div>
      </button>
      {open && <ActivityDetails item={item} />}
    </li>
  );
}

function StaffActivity() {
  const { data: filters } = useFilters();
  const [userId, setUserId] = useState('');
  const [stationId, setStationId] = useState('');
  const [category, setCategory] = useState('');
  const [search, setSearch] = useState('');
  const [q, setQ] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [showViews, setShowViews] = useState(true);
  const [failedOnly, setFailedOnly] = useState(false);
  const [page, setPage] = useState(0);
  const [live, setLive] = useState(true);

  useEffect(() => { const t = setTimeout(() => setQ(search.trim()), 300); return () => clearTimeout(t); }, [search]);
  useEffect(() => { setPage(0); }, [userId, stationId, category, q, from, to, showViews, failedOnly]);

  const params = useMemo(() => ({
    user_id: userId || undefined,
    station_id: stationId || undefined,
    category: category || undefined,
    q: q || undefined,
    start: from ? dayStart(from) : undefined,
    end: to ? dayAfter(to) : undefined,
    include_views: showViews,
    failed_only: failedOnly || undefined,
  }), [userId, stationId, category, q, from, to, showViews, failedOnly]);

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['activity', 'list', params, page],
    queryFn: () => getActivity({ ...params, skip: page * PAGE_SIZE, limit: PAGE_SIZE }),
    staleTime: 0,
  });

  // Live: on the first page, fetch rows newer than what is shown and put them on top.
  const [extra, setExtra] = useState<ActivityItem[]>([]);
  const [freshIds, setFreshIds] = useState<Set<string>>(new Set());
  useEffect(() => { setExtra([]); setFreshIds(new Set()); }, [data]);
  const items = useMemo(() => {
    const seen = new Set<string>();
    return [...extra, ...(data?.items ?? [])].filter(i => (seen.has(i.id) ? false : (seen.add(i.id), true)));
  }, [extra, data]);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const liveOn = live && page === 0 && !(to && to < localDate(new Date()));

  useEffect(() => {
    if (!liveOn || !data) return;
    let stopped = false;
    const tick = async () => {
      if (document.visibilityState !== 'visible') return;
      const newest = itemsRef.current[0]?.created_at;
      // Look back a little: rows are written a moment after they happen
      const after = newest ? new Date(new Date(newest).getTime() - 30_000).toISOString() : new Date(Date.now() - 60_000).toISOString();
      try {
        const res = await getActivity({ ...params, after, limit: 100 });
        if (stopped) return;
        const known = new Set(itemsRef.current.map(i => i.id));
        const fresh = res.items.filter(i => !known.has(i.id));
        if (fresh.length) {
          setExtra(prev => [...fresh, ...prev].sort((a, b) => b.created_at.localeCompare(a.created_at)));
          setFreshIds(new Set(fresh.map(i => i.id)));
        }
      } catch { /* try again next tick */ }
    };
    const id = setInterval(tick, LIVE_EVERY_MS);
    return () => { stopped = true; clearInterval(id); };
  }, [liveOn, data, params]);

  const total = (data?.total ?? 0) + extra.length;
  const people = filters?.users ?? [];

  return (
    <div className="flex flex-col min-h-0 flex-1">
      <div className="bg-[#12123a] border border-[#2a2a5e] rounded p-3 mb-3 space-y-2">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2">
          <label className="flex flex-col text-[11px] text-gray-400 gap-1">Person
            <select value={userId} onChange={e => setUserId(e.target.value)} className={inputCls}>
              <option value="">Everyone</option>
              {people.map(u => <option key={u.id} value={u.id}>{u.name ? `${u.name} (${u.email})` : u.email} · {u.role}</option>)}
            </select>
          </label>
          <label className="flex flex-col text-[11px] text-gray-400 gap-1">Station
            <select value={stationId} onChange={e => setStationId(e.target.value)} className={inputCls}>
              <option value="">All stations</option>
              {(filters?.stations ?? []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <label className="flex flex-col text-[11px] text-gray-400 gap-1">What
            <select value={category} onChange={e => setCategory(e.target.value)} className={inputCls}>
              <option value="">Everything</option>
              {(filters?.categories ?? []).map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </label>
          <label className="flex flex-col text-[11px] text-gray-400 gap-1">Search
            <input type="search" value={search} onChange={e => setSearch(e.target.value)}
              placeholder="Song, action, person, IP…" className={inputCls} />
          </label>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <DatePresets from={from} to={to} setFrom={setFrom} setTo={setTo} />
          <label className="flex items-center gap-1 text-[11px] text-gray-400">From
            <input type="date" value={from} onChange={e => setFrom(e.target.value)} className={inputCls} />
          </label>
          <label className="flex items-center gap-1 text-[11px] text-gray-400">To
            <input type="date" value={to} onChange={e => setTo(e.target.value)} className={inputCls} />
          </label>
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-2 text-[12px] text-gray-300">
          <label className="flex items-center gap-2 min-h-[32px]">
            <input type="checkbox" checked={showViews} onChange={e => setShowViews(e.target.checked)} className="w-4 h-4" />
            Show screens opened
          </label>
          <label className="flex items-center gap-2 min-h-[32px]">
            <input type="checkbox" checked={failedOnly} onChange={e => setFailedOnly(e.target.checked)} className="w-4 h-4" />
            Only refused or failed
          </label>
          <label className="flex items-center gap-2 min-h-[32px]">
            <input type="checkbox" checked={live} onChange={e => setLive(e.target.checked)} className="w-4 h-4" />
            Live
            {liveOn && <span className="inline-block w-2 h-2 rounded-full bg-green-400 animate-pulse" aria-label="updating live" />}
          </label>
        </div>
      </div>

      {isLoading ? (
        <div className="text-gray-500 text-center py-8">Loading activity…</div>
      ) : isError ? (
        <div className="text-center py-8 text-red-300">
          Could not load the activity. <button onClick={() => refetch()} className="underline text-cyan-300">Try again</button>
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-gray-500">Nothing matches these filters yet.</div>
      ) : (
        <>
          <ul className="bg-[#0a0a28] border border-[#2a2a5e] rounded overflow-y-auto flex-1 min-h-0" data-testid="activity-list">
            {items.map(i => <ActivityRow key={i.id} item={i} fresh={freshIds.has(i.id)} />)}
          </ul>
          <Pager page={page} total={total} onPage={setPage} />
        </>
      )}
    </div>
  );
}

function ListenerSessions() {
  const { data: filters } = useFilters();
  const [stationId, setStationId] = useState('');
  const [search, setSearch] = useState('');
  const [q, setQ] = useState('');
  const [from, setFrom] = useState(localDate(new Date()));
  const [to, setTo] = useState(localDate(new Date()));
  const [minMinutes, setMinMinutes] = useState(0);
  const [page, setPage] = useState(0);
  useEffect(() => { const t = setTimeout(() => setQ(search.trim()), 300); return () => clearTimeout(t); }, [search]);
  useEffect(() => { setPage(0); }, [stationId, q, from, to, minMinutes]);

  const params = {
    station_id: stationId || undefined,
    q: q || undefined,
    start: from ? dayStart(from) : undefined,
    end: to ? dayAfter(to) : undefined,
    min_seconds: minMinutes * 60 || undefined,
    skip: page * PAGE_SIZE,
    limit: PAGE_SIZE,
  };
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['activity', 'listeners', params],
    queryFn: () => getListenerSessions(params),
    refetchInterval: () => (document.visibilityState === 'visible' && page === 0 ? 15_000 : false),
  });
  const items: ListenerSessionItem[] = data?.items ?? [];
  const totalMinutes = Math.round(items.reduce((s, i) => s + i.duration_seconds, 0) / 60);

  return (
    <div className="flex flex-col min-h-0 flex-1">
      <div className="bg-[#12123a] border border-[#2a2a5e] rounded p-3 mb-3 space-y-2">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          <label className="flex flex-col text-[11px] text-gray-400 gap-1">Station
            <select value={stationId} onChange={e => setStationId(e.target.value)} className={inputCls}>
              <option value="">All stations</option>
              {(filters?.stations ?? []).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          <label className="flex flex-col text-[11px] text-gray-400 gap-1">Search
            <input type="search" value={search} onChange={e => setSearch(e.target.value)}
              placeholder="City, state, country, IP, device…" className={inputCls} />
          </label>
          <label className="flex flex-col text-[11px] text-gray-400 gap-1">Listened at least
            <select value={minMinutes} onChange={e => setMinMinutes(Number(e.target.value))} className={inputCls}>
              <option value={0}>Any time</option>
              <option value={1}>1 minute</option>
              <option value={5}>5 minutes</option>
              <option value={15}>15 minutes</option>
              <option value={60}>1 hour</option>
            </select>
          </label>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <DatePresets from={from} to={to} setFrom={setFrom} setTo={setTo} />
          <label className="flex items-center gap-1 text-[11px] text-gray-400">From
            <input type="date" value={from} onChange={e => setFrom(e.target.value)} className={inputCls} />
          </label>
          <label className="flex items-center gap-1 text-[11px] text-gray-400">To
            <input type="date" value={to} onChange={e => setTo(e.target.value)} className={inputCls} />
          </label>
        </div>
      </div>
      {isLoading ? (
        <div className="text-gray-500 text-center py-8">Loading listeners…</div>
      ) : isError ? (
        <div className="text-center py-8 text-red-300">
          Could not load the listeners. <button onClick={() => refetch()} className="underline text-cyan-300">Try again</button>
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-gray-500">No listening in this period.</div>
      ) : (
        <>
          <div className="text-[12px] text-gray-400 mb-1">This page: {items.length} sessions, {totalMinutes.toLocaleString()} minutes listened</div>
          <ul className="bg-[#0a0a28] border border-[#2a2a5e] rounded overflow-y-auto flex-1 min-h-0" data-testid="listener-list">
            {items.map(s => (
              <li key={s.id} className="border-b border-[#1e1e4a] px-3 py-2">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span className="text-[12px] text-gray-400 tabular-nums w-full sm:w-44">{fmtTime(s.started_at)}</span>
                  <span className="text-white">{s.location || 'Unknown place'}</span>
                  <span className="text-[11px] px-1.5 py-0.5 rounded bg-[#1a1a4e] text-indigo-200">{s.station_name || 'Station'}</span>
                  <span className="text-cyan-200 tabular-nums">{fmtDuration(s.duration_seconds)}</span>
                  {s.listening_now && <span className="px-1.5 py-0.5 rounded bg-green-800 text-green-100 text-[11px]">Listening now</span>}
                </div>
                <div className="text-[11px] text-gray-500 mt-0.5 sm:pl-[11.5rem] flex flex-wrap gap-x-3">
                  {s.device && <span>{s.device}</span>}
                  {s.ip_address && <span>{s.ip_address}</span>}
                  {!s.listening_now && (s.ended_at || s.last_heartbeat) && <span>until {fmtTime(s.ended_at || s.last_heartbeat)}</span>}
                </div>
              </li>
            ))}
          </ul>
          <Pager page={page} total={data?.total ?? 0} onPage={setPage} />
        </>
      )}
    </div>
  );
}

export default function Activity() {
  const [tab, setTab] = useState<'staff' | 'listeners'>(() => {
    try { return localStorage.getItem('activity-tab') === 'listeners' ? 'listeners' : 'staff'; } catch { return 'staff'; }
  });
  useEffect(() => { try { localStorage.setItem('activity-tab', tab); } catch { /* private mode */ } }, [tab]);

  return (
    <div className="-mx-4 sm:-mx-6 lg:-mx-8 -my-6 bg-[#080820] text-[13px] min-h-[calc(100vh-4rem)] sm:h-[calc(100vh-4rem)] flex flex-col text-white p-3 sm:p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h1 className="text-xl font-bold text-cyan-300">Activity</h1>
        <div className="flex rounded overflow-hidden border border-[#2a2a5e]" role="tablist">
          {([['staff', 'Who did what'], ['listeners', 'Listeners']] as const).map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
              className={`px-3 min-h-[36px] ${tab === id ? 'bg-cyan-700 text-white' : 'bg-[#12123a] text-cyan-200'}`}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {tab === 'staff' ? <StaffActivity /> : <ListenerSessions />}
    </div>
  );
}
