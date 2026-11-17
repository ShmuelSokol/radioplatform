import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useStations } from '../../hooks/useStations';
import { useAuthStore } from '../../stores/authStore';
import { isManager } from '../../auth/access';
import { updateAsset } from '../../api/assets';
import {
  listDbItems, listDbSpots, setSpotEndDate,
  type DbItem, type DbSort, type DbSpot,
} from '../../api/databank';
import { listProgramBlocks, type ProgramBlock } from '../../api/programBlocks';
import { fmtDur, isValidTz, useDebounced, useMediaQuery } from './controlroom/shared';
import { usePfl } from './controlroom/pfl';
import { PflDeck } from './controlroom/PflDeck';
import { DayLog } from './controlroom/DayLog';
import { REPORT_BUG_EVENT } from '../../components/BugReporter';
import './controlroom/classic.css';

/**
 * Databank: KBR Studio's "KBR Database" window. The library as plain tables
 * (Songs.db, the jingle list, Spots.db, Blocks.db) sorted by any column, a Find
 * box, Edit mode to change a cell in place, and the play log of any day.
 * See wiki/databank.md.
 */

type Tab = 'songs' | 'jingles' | 'spots' | 'blocks' | 'log';
const TABS: { id: Tab; label: string; file: string }[] = [
  { id: 'songs', label: 'Songs', file: 'Songs.db' },
  { id: 'jingles', label: 'Jingles', file: 'Jingles.db' },
  { id: 'spots', label: 'Spots', file: 'Spots.db' },
  { id: 'blocks', label: 'Blocks', file: 'Blocks.db' },
  { id: 'log', label: 'Log', file: 'Log' },
];
const PAGE = 200;

/** 8/10/2026, the way the old program writes dates (day first) */
function dmy(iso: string | null, tz?: string): string {
  if (!iso) return '';
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00Z` : iso);
  const opts: Intl.DateTimeFormatOptions = { day: 'numeric', month: 'numeric', year: 'numeric', ...(tz && iso.length > 10 ? { timeZone: tz } : { timeZone: 'UTC' }) };
  return new Intl.DateTimeFormat('en-GB', opts).format(d);
}
function hm(iso: string | null, tz?: string): string {
  if (!iso) return '';
  return new Intl.DateTimeFormat('en-GB', { hour: 'numeric', minute: '2-digit', hourCycle: 'h23', ...(tz ? { timeZone: tz } : {}) }).format(new Date(iso));
}
/** Our days are 0 = Monday; KBR Studio writes 0 = Sunday ... 6 = Saturday ("012345" = Sunday to Friday) */
export function kbrWeekdays(days: string): string {
  return days.split(',').map(s => s.trim()).filter(Boolean).map(d => (Number(d) + 1) % 7).sort((a, b) => a - b).join('');
}

/* ── Edit-in-place cell ──────────────────────────────────── */

function EditCell({ value, type = 'text', onSave, onCancel, list }: {
  value: string; type?: 'text' | 'number' | 'date'; list?: string;
  onSave: (v: string) => void; onCancel: () => void;
}) {
  const [v, setV] = useState(value);
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { ref.current?.focus(); ref.current?.select?.(); }, []);
  return (
    <input ref={ref} type={type} value={v} list={list} data-testid="db-edit-input"
      onChange={e => setV(e.target.value)}
      onKeyDown={e => {
        e.stopPropagation();
        if (e.key === 'Enter') onSave(v);
        if (e.key === 'Escape') onCancel();
      }}
      onBlur={() => (v === value ? onCancel() : onSave(v))}
      className="w-full h-full min-h-[24px] px-1 bg-white text-black font-normal outline-none border border-[#000080]" />
  );
}

/* ── Page ────────────────────────────────────────────────── */

export default function Databank() {
  const qc = useQueryClient();
  const isDesktop = useMediaQuery('(min-width: 1024px)');
  const role = useAuthStore(s => s.user?.role);
  const manager = isManager(role);
  const { data: stationData } = useStations();
  const stations = useMemo(() => stationData?.stations ?? [], [stationData]);
  const [stationId, setStationId] = useState<string>(() => {
    try { return localStorage.getItem('radio_last_station') || ''; } catch { return ''; }
  });
  useEffect(() => {
    if (stations.length && !stations.some(s => s.id === stationId)) setStationId(stations[0].id);
  }, [stations, stationId]);
  const station = stations.find(s => s.id === stationId);
  const tz = station?.timezone && isValidTz(station.timezone) ? station.timezone : undefined;

  const [tab, setTabRaw] = useState<Tab>(() => {
    const t = new URLSearchParams(window.location.search).get('tab') as Tab | null;
    return t && TABS.some(x => x.id === t) ? t : 'songs';
  });
  const [find, setFind] = useState('');
  const search = useDebounced(find.trim(), 300);
  const [category, setCategory] = useState('');
  const [sort, setSort] = useState<DbSort>('artist');
  const [desc, setDesc] = useState(false);
  const [page, setPage] = useState(0);
  const [editMode, setEditMode] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; field: string } | null>(null);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const findRef = useRef<HTMLInputElement>(null);
  const pfl = usePfl();

  const setTab = (t: Tab) => {
    setTabRaw(t); setPage(0); setSel(null); setEditing(null); setCategory('');
    setSort(t === 'jingles' ? 'title' : 'artist'); setDesc(false);
  };
  useEffect(() => { setPage(0); }, [search, category, sort, desc]);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 5000);
    return () => clearTimeout(t);
  }, [notice]);

  const assetType = tab === 'jingles' ? 'jingle' : 'music';
  const itemsTab = tab === 'songs' || tab === 'jingles';
  const itemsQ = useQuery({
    queryKey: ['databank-items', assetType, stationId, search, category, sort, desc, page],
    queryFn: () => listDbItems({ asset_type: assetType, station_id: stationId || undefined, search, category, sort, desc, skip: page * PAGE, limit: PAGE }),
    enabled: itemsTab,
    placeholderData: prev => prev,
  });
  const totalQ = useQuery({
    queryKey: ['databank-total', assetType],
    queryFn: () => listDbItems({ asset_type: assetType, sort: 'title', desc: false, skip: 0, limit: 1 }),
    enabled: itemsTab,
    staleTime: 60_000,
  });
  const spotsQ = useQuery({
    queryKey: ['databank-spots', stationId],
    queryFn: () => listDbSpots(stationId || undefined),
    enabled: tab === 'spots',
  });
  const blocksQ = useQuery({
    queryKey: ['program-blocks', stationId],
    queryFn: () => listProgramBlocks(stationId),
    enabled: tab === 'blocks' && !!stationId && manager,
  });

  // Spots and blocks are short lists: sorted and found here
  const spots = useMemo(() => {
    let rows = spotsQ.data?.spots ?? [];
    if (search) {
      const s = search.toLowerCase();
      rows = rows.filter(r => `${r.title} ${r.file} ${r.category ?? ''}`.toLowerCase().includes(s));
    }
    if (category) rows = rows.filter(r => (r.category ?? '').toLowerCase() === category.toLowerCase());
    const key = (r: DbSpot): string | number =>
      sort === 'type' ? (r.category ?? '').toLowerCase()
      : sort === 'length' ? r.length ?? 0
      : sort === 'last_played' ? r.end_date ?? (r.open_ended ? '9999' : '')
      : r.title.toLowerCase();
    return [...rows].sort((a, b) => {
      const x = key(a), y = key(b);
      return (x < y ? -1 : x > y ? 1 : 0) * (desc ? -1 : 1);
    });
  }, [spotsQ.data, search, category, sort, desc]);

  const blockRows = useMemo(() => {
    const rows: { key: string; block: ProgramBlock; time: string }[] = [];
    for (const b of blocksQ.data ?? []) {
      for (const t of b.times.split(',').map(s => s.trim()).filter(Boolean)) rows.push({ key: `${b.id}-${t}`, block: b, time: t });
    }
    const s = search.toLowerCase();
    return rows
      .filter(r => !s || r.block.name.toLowerCase().includes(s))
      .sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : a.block.name.localeCompare(b.block.name)));
  }, [blocksQ.data, search]);

  const items = useMemo(() => itemsQ.data?.items ?? [], [itemsQ.data]);
  const typesSeen = useMemo(() => Array.from(new Set(
    (tab === 'spots' ? spots.map(s => s.category) : items.map(i => i.type)).filter(Boolean) as string[],
  )).sort(), [items, spots, tab]);

  // Status bar numbers, as in the old program: Page, Rec (selected), View (found), Tot (all)
  const ids: string[] = tab === 'spots' ? spots.map(s => s.id) : tab === 'blocks' ? blockRows.map(r => r.key) : items.map(i => i.id);
  const recIdx = sel ? ids.indexOf(sel) : -1;
  const view = itemsTab ? itemsQ.data?.total ?? 0 : tab === 'spots' ? spots.length : tab === 'blocks' ? blockRows.length : 0;
  const tot = itemsTab ? totalQ.data?.total ?? 0 : tab === 'spots' ? spotsQ.data?.spots.length ?? 0 : tab === 'blocks' ? blockRows.length : 0;
  const pages = itemsTab ? Math.max(1, Math.ceil(view / PAGE)) : 1;

  const sortBy = (col: DbSort) => {
    if (sort === col) setDesc(!desc);
    else { setSort(col); setDesc(col === 'vote' || col === 'last_played'); }
  };

  const saveAsset = async (id: string, field: string, raw: string) => {
    setEditing(null);
    const v = raw.trim();
    const body: Record<string, unknown> = {};
    if (field === 'vote') {
      const n = Number(v);
      if (!Number.isInteger(n) || n < 0 || n > 100) { setNotice({ ok: false, text: 'V is a whole number from 0 to 100.' }); return; }
      body.vote = n;
    } else if (field === 'intro') {
      const n = v === '' ? null : Number(v);
      if (n !== null && (!Number.isFinite(n) || n < 0 || n > 600)) { setNotice({ ok: false, text: 'Intro is seconds, 0 to 600.' }); return; }
      body.intro_seconds = n;
    } else if (field === 'title') {
      if (!v) { setNotice({ ok: false, text: 'The title cannot be empty.' }); return; }
      body.title = v;
    } else if (field === 'artist') body.artist = v || null;
    else if (field === 'type') body.category = v || null;
    try {
      await updateAsset(id, body as any);
      setNotice({ ok: true, text: 'Saved.' });
      await qc.invalidateQueries({ queryKey: ['databank-items'] });
      await qc.invalidateQueries({ queryKey: ['databank-spots'] });
      void qc.invalidateQueries({ queryKey: ['assets'] });
    } catch (e: any) {
      setNotice({ ok: false, text: e?.response?.data?.detail ?? 'Could not save that change.' });
    }
  };

  const saveEndDate = async (spot: DbSpot, raw: string) => {
    setEditing(null);
    try {
      await setSpotEndDate(spot.id, raw || null, stationId || undefined);
      setNotice({ ok: true, text: raw ? `${spot.title} now runs until ${dmy(raw)}.` : `${spot.title} now has no end date.` });
      await qc.invalidateQueries({ queryKey: ['databank-spots'] });
      void qc.invalidateQueries({ queryKey: ['spot-schedules'] });
    } catch (e: any) {
      setNotice({ ok: false, text: e?.response?.data?.detail ?? 'Could not change the end date.' });
    }
  };

  // Arrow keys move the selection; Space pre-listens
  const onKey = (e: React.KeyboardEvent) => {
    if (editing || ids.length === 0) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const i = recIdx < 0 ? 0 : Math.min(ids.length - 1, Math.max(0, recIdx + (e.key === 'ArrowDown' ? 1 : -1)));
      setSel(ids[i]);
      document.querySelector(`[data-row="${CSS.escape(ids[i])}"]`)?.scrollIntoView({ block: 'nearest' });
    }
    if (e.key === ' ' && sel && tab !== 'blocks') { e.preventDefault(); void pfl.toggle(sel); }
  };

  const canEdit = manager && editMode;
  const cell = (id: string, field: string, value: string, type: 'text' | 'number' = 'text', list?: string) =>
    editing?.id === id && editing.field === field
      ? <EditCell value={value} type={type} list={list} onSave={v => saveAsset(id, field, v)} onCancel={() => setEditing(null)} />
      : null;
  const startEdit = (id: string, field: string) => (e: React.MouseEvent) => {
    if (!canEdit) return;
    e.stopPropagation();
    setSel(id);
    setEditing({ id, field });
  };

  /* ── Tables ── */

  const Head = ({ col, children, className = '' }: { col?: DbSort; children: React.ReactNode; className?: string }) => (
    <button type="button" disabled={!col} onClick={() => col && sortBy(col)}
      className={`text-left truncate ${col ? 'hover:bg-[#d0d0d0]' : 'cursor-default'} ${className}`}
      aria-sort={col && sort === col ? (desc ? 'descending' : 'ascending') : undefined}>
      {children}{col && sort === col ? (desc ? ' ▼' : ' ▲') : ''}
    </button>
  );

  const rowClass = (id: string, extra = '') =>
    `qn-row w-full text-left ${sel === id ? 'qn-row-cur' : ''} ${pfl.playingId === id ? 'outline outline-1 outline-[#2bff4a] -outline-offset-1' : ''} ${extra}`;

  const songsTable = (
    <div role="table" aria-label={tab === 'songs' ? 'Songs' : 'Jingles'} className="min-w-0">
      {isDesktop ? (
        <>
          <div role="row" className="qn-log-head sticky top-0 z-10 grid h-5" style={{ gridTemplateColumns: tab === 'songs' ? GRID_SONGS : GRID_JINGLES }}>
            {tab === 'songs' && <Head col="artist">Artist</Head>}
            <Head col="title">{tab === 'songs' ? 'Title' : 'File'}</Head>
            <Head col="type">{tab === 'songs' ? 'Type' : 'Group'}</Head>
            {tab === 'songs' && <Head col="vote" className="text-right">V</Head>}
            <Head col="length" className="text-right">Length</Head>
            {tab === 'songs' && <Head col="intro" className="text-right">Intro</Head>}
            <Head col="file">{tab === 'songs' ? 'File' : 'Stored as'}</Head>
            <Head col="last_played">Last played</Head>
            <Head>At</Head>
          </div>
          {items.map((it: DbItem) => (
            <div key={it.id} role="row" data-row={it.id} onClick={() => { setSel(it.id); }} onDoubleClick={() => void pfl.toggle(it.id)}
              className={`${rowClass(it.id)} grid h-[18px] leading-[18px] text-[13px] cursor-default`}
              style={{ gridTemplateColumns: tab === 'songs' ? GRID_SONGS : GRID_JINGLES }}>
              {tab === 'songs' && <span className="truncate px-1" onClick={startEdit(it.id, 'artist')}>{cell(it.id, 'artist', it.artist ?? '') ?? (it.artist ?? '')}</span>}
              <span className="truncate px-1" onClick={startEdit(it.id, 'title')}>{cell(it.id, 'title', it.title) ?? it.title}</span>
              <span className="truncate px-1" onClick={startEdit(it.id, 'type')}>{cell(it.id, 'type', it.type ?? '', 'text', 'db-types') ?? (it.type ?? '')}</span>
              {tab === 'songs' && <span className="px-1 text-right tabular-nums" onClick={startEdit(it.id, 'vote')}>{cell(it.id, 'vote', String(it.vote), 'number') ?? it.vote}</span>}
              <span className="px-1 text-right tabular-nums">{fmtDur(it.length)}</span>
              {tab === 'songs' && <span className="px-1 text-right tabular-nums" onClick={startEdit(it.id, 'intro')}>{cell(it.id, 'intro', it.intro != null ? String(it.intro) : '', 'number') ?? (it.intro ? fmtDur(it.intro) : '')}</span>}
              <span className="truncate px-1 font-normal" title={it.file}>{it.file}</span>
              <span className="px-1 tabular-nums">{dmy(it.last_played, tz)}</span>
              <span className="px-1 tabular-nums">{hm(it.last_played, tz)}</span>
            </div>
          ))}
        </>
      ) : (
        items.map((it: DbItem) => (
          <div key={it.id} role="row" data-row={it.id} onClick={() => setSel(it.id)}
            className={`${rowClass(it.id)} flex items-center gap-2 px-2 min-h-[52px] border-b border-[#202020]`}>
            <button type="button" onClick={e => { e.stopPropagation(); void pfl.toggle(it.id); }}
              aria-label={pfl.playingId === it.id ? `Stop ${it.title}` : `Pre-listen ${it.title}`}
              className="qn-btn w-11 h-11 shrink-0 text-base">{pfl.playingId === it.id ? '■' : '🎧'}</button>
            <span className="flex-1 min-w-0 leading-tight">
              <span className="block truncate text-[15px]" onClick={startEdit(it.id, 'title')}>{cell(it.id, 'title', it.title) ?? it.title}</span>
              <span className="block truncate text-xs font-normal text-[#d0d0d0]">
                {it.artist ? `${it.artist} · ` : ''}<span onClick={startEdit(it.id, 'type')}>{cell(it.id, 'type', it.type ?? '', 'text', 'db-types') ?? (it.type ?? '')}</span>
                {it.last_played ? ` · ${dmy(it.last_played, tz)} ${hm(it.last_played, tz)}` : ''}
              </span>
            </span>
            <span className="shrink-0 text-right text-xs tabular-nums">
              <span className="block">{fmtDur(it.length)}</span>
              {tab === 'songs' && <span className="block font-normal text-[#d0d0d0]" onClick={startEdit(it.id, 'vote')}>V {cell(it.id, 'vote', String(it.vote), 'number') ?? it.vote}</span>}
            </span>
          </div>
        ))
      )}
      {itemsQ.isLoading && <p className="p-3 font-normal">Loading…</p>}
      {!itemsQ.isLoading && items.length === 0 && <p className="p-3 font-normal">{search || category ? 'Nothing matches.' : 'Nothing here yet.'}</p>}
    </div>
  );

  const spotsTable = (
    <div role="table" aria-label="Spots">
      {isDesktop && (
        <div role="row" className="qn-log-head sticky top-0 z-10 grid h-5" style={{ gridTemplateColumns: GRID_SPOTS }}>
          <Head col="title">File</Head>
          <Head col="type">Category</Head>
          <Head col="last_played">End date</Head>
          <Head col="length" className="text-right">Length</Head>
          <Head>Schedules</Head>
        </div>
      )}
      {spots.map(s => {
        const end = editing?.id === s.id && editing.field === 'end'
          ? <EditCell value={s.end_date ?? ''} type="date" onSave={v => saveEndDate(s, v)} onCancel={() => setEditing(null)} />
          : s.schedules === 0 ? <span className="font-normal text-[#a0a0a0]">not scheduled</span>
          : s.open_ended ? <span className="font-normal">no end</span>
          : dmy(s.end_date);
        const endProps = { onClick: s.schedules ? startEdit(s.id, 'end') : undefined, 'data-testid': 'spot-end' };
        const cat = cell(s.id, 'type', s.category ?? '', 'text', 'db-types') ?? (s.category ?? '');
        const red = s.expired ? '!text-[#ff4040]' : '';
        return isDesktop ? (
          <div key={s.id} role="row" data-row={s.id} data-expired={s.expired || undefined} onClick={() => setSel(s.id)} onDoubleClick={() => void pfl.toggle(s.id)}
            className={`${rowClass(s.id, sel === s.id ? '' : red)} grid h-[18px] leading-[18px] text-[13px]`} style={{ gridTemplateColumns: GRID_SPOTS }}>
            <span className="truncate px-1" title={s.file}>{s.title}</span>
            <span className="truncate px-1" onClick={startEdit(s.id, 'type')}>{cat}</span>
            <span className="px-1 tabular-nums" {...endProps}>{end}</span>
            <span className="px-1 text-right tabular-nums">{fmtDur(s.length)}</span>
            <span className="px-1 font-normal">{s.schedules ? `${s.schedules}${s.active ? '' : ' (off)'}` : ''}</span>
          </div>
        ) : (
          <div key={s.id} role="row" data-row={s.id} data-expired={s.expired || undefined} onClick={() => setSel(s.id)}
            className={`${rowClass(s.id, sel === s.id ? '' : red)} flex items-center gap-2 px-2 min-h-[52px] border-b border-[#202020]`}>
            <button type="button" onClick={e => { e.stopPropagation(); void pfl.toggle(s.id); }}
              aria-label={pfl.playingId === s.id ? `Stop ${s.title}` : `Pre-listen ${s.title}`}
              className="qn-btn w-11 h-11 shrink-0 text-base">{pfl.playingId === s.id ? '■' : '🎧'}</button>
            <span className="flex-1 min-w-0 leading-tight">
              <span className="block truncate text-[15px]">{s.title}</span>
              <span className="block truncate text-xs font-normal opacity-80" onClick={startEdit(s.id, 'type')}>{cat || 'no category'}</span>
            </span>
            <span className="shrink-0 text-right text-xs tabular-nums">
              <span className="block min-h-[22px]" {...endProps}>{end}</span>
              <span className="block font-normal opacity-80">{fmtDur(s.length)}</span>
            </span>
          </div>
        );
      })}
      {spotsQ.isLoading && <p className="p-3 font-normal">Loading…</p>}
      {!spotsQ.isLoading && spots.length === 0 && <p className="p-3 font-normal">{search ? 'Nothing matches.' : 'No spots in the library yet.'}</p>}
    </div>
  );

  const blocksTable = (
    <div role="table" aria-label="Blocks">
      {isDesktop && (
        <div role="row" className="qn-log-head sticky top-0 z-10 grid h-5" style={{ gridTemplateColumns: GRID_BLOCKS }}>
          <Head>Flg</Head><Head>Time</Head><Head>Name</Head><Head className="text-right">Recs</Head><Head>Week days</Head><Head>Timing</Head>
        </div>
      )}
      {blockRows.map(r => {
        const b = r.block;
        const dot = b.timing_mode === 'hard'
          ? <span className="inline-block w-3 h-3 rounded-full bg-[#ffff40] border border-[#806000]" title="Hard: on the second" aria-label="Hard" />
          : null;
        const timing = b.timing_mode === 'hard' ? 'hard' : b.latest_minutes ? `soft, ${b.latest_minutes} min` : 'soft';
        const off = b.is_active ? '' : 'opacity-50';
        return isDesktop ? (
          <div key={r.key} role="row" data-row={r.key} onClick={() => setSel(r.key)}
            className={`${rowClass(r.key, off)} grid h-[18px] leading-[18px] text-[13px]`} style={{ gridTemplateColumns: GRID_BLOCKS }}>
            <span className="px-1 flex items-center justify-center">{dot}</span>
            <span className="px-1 text-right tabular-nums">{r.time}</span>
            <span className="truncate px-1">{b.name}{b.is_active ? '' : ' (off)'}</span>
            <span className="px-1 text-right tabular-nums">{b.items.length}</span>
            <span className="px-1 tabular-nums">{kbrWeekdays(b.days_of_week)}</span>
            <span className="px-1 font-normal">{timing}</span>
          </div>
        ) : (
          <div key={r.key} role="row" data-row={r.key} onClick={() => setSel(r.key)}
            className={`${rowClass(r.key, off)} flex items-center gap-2 px-2 min-h-[48px] border-b border-[#202020]`}>
            <span className="w-4 shrink-0 flex justify-center">{dot}</span>
            <span className="w-12 shrink-0 tabular-nums">{r.time}</span>
            <span className="flex-1 min-w-0 truncate">{b.name}{b.is_active ? '' : ' (off)'}</span>
            <span className="shrink-0 text-right text-xs tabular-nums leading-tight">
              <span className="block">{b.items.length} recs</span>
              <span className="block font-normal opacity-80">{kbrWeekdays(b.days_of_week)} · {timing}</span>
            </span>
          </div>
        );
      })}
      {!manager && <p className="p-3 font-normal">Blocks are for managers.</p>}
      {manager && blocksQ.isLoading && <p className="p-3 font-normal">Loading…</p>}
      {manager && !blocksQ.isLoading && blockRows.length === 0 && <p className="p-3 font-normal">No blocks on this station yet.</p>}
    </div>
  );

  const fileName = TABS.find(t => t.id === tab)!.file;

  return (
    <div className="qn h-full flex flex-col overflow-hidden select-none" data-testid="databank" onKeyDown={onKey}>
      <div className="qn-title h-6 shrink-0 flex items-center px-2 gap-2">
        <span className="truncate">KBR Database - [{fileName}]</span>
        <span className="ml-auto font-normal truncate">{station?.name ?? ''}</span>
      </div>

      {/* Toolbar: like the old one (ALL, Find, Edit mode, headphones, DB menu) */}
      <div className="shrink-0 bg-[#c0c0c0] border-b border-[#808080] p-1 flex flex-wrap items-center gap-1">
        <div className="flex gap-1 w-full lg:w-auto" role="tablist" aria-label="Table">
          {TABS.map(t => (
            <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
              className="qn-btn flex-1 lg:flex-none h-11 lg:h-8 px-3 text-sm">{t.label}</button>
          ))}
        </div>
        {tab !== 'log' && (
          <>
            <input ref={findRef} type="search" value={find} onChange={e => setFind(e.target.value)} placeholder="Find"
              aria-label="Find" data-testid="db-find"
              className="qn-field h-11 lg:h-8 px-2 text-[16px] lg:text-sm flex-1 min-w-[8rem] lg:flex-none lg:w-64" />
            <button type="button" onClick={() => { setFind(''); setCategory(''); findRef.current?.focus(); }}
              className="qn-btn h-11 lg:h-8 px-3 text-sm" title="Show every record again">ALL</button>
            {tab !== 'blocks' && (
              <select value={category} onChange={e => setCategory(e.target.value)} aria-label={tab === 'jingles' ? 'Group' : tab === 'spots' ? 'Category' : 'Type'}
                className="qn-field h-11 lg:h-8 px-2 text-sm max-w-[11rem]" data-testid="db-type">
                <option value="">{tab === 'jingles' ? 'Every group' : tab === 'spots' ? 'Every category' : 'Every type'}</option>
                {typesSeen.concat(category && !typesSeen.includes(category) ? [category] : []).map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            )}
            {tab !== 'blocks' && (
              <button type="button" disabled={!sel} onClick={() => sel && pfl.toggle(sel)}
                className="qn-btn h-11 lg:h-8 px-3 text-sm" title="Pre-listen to the selected item (Space). Nothing goes on air."
                aria-label="Pre-listen to the selected item">🎧</button>
            )}
            {manager && tab !== 'blocks' && (
              <button type="button" aria-pressed={editMode} onClick={() => { setEditMode(!editMode); setEditing(null); }}
                className={`qn-btn h-11 lg:h-8 px-3 text-sm ${editMode ? '!text-[#000080]' : ''}`} data-testid="db-edit-mode"
                title="Edit mode: click a cell to change it">{editMode ? '✎ Editing' : 'Edit mode'}</button>
            )}
            {tab === 'blocks' && manager && (
              <Link to="/admin/program-blocks" className="qn-btn h-11 lg:h-8 px-3 text-sm inline-flex items-center">Change blocks</Link>
            )}
            {tab === 'spots' && (
              <Link to="/admin/spot-schedule" className="qn-btn h-11 lg:h-8 px-3 text-sm inline-flex items-center">Spot schedule</Link>
            )}
          </>
        )}
        {stations.length > 1 && (
          <select value={stationId} onChange={e => { setStationId(e.target.value); setSel(null); }} aria-label="Station"
            className="qn-field h-11 lg:h-8 px-2 text-sm lg:ml-auto max-w-[14rem]">
            {stations.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        )}
        {isDesktop && <Link to="/admin/dashboard" className="qn-btn h-8 px-3 text-sm inline-flex items-center" title="Back to the control room">Control room</Link>}
      </div>
      {editMode && tab !== 'log' && tab !== 'blocks' && (
        <div className="shrink-0 bg-[#ffffc0] text-black text-xs px-2 py-1 border-b border-[#808080]">
          Edit mode: {isDesktop ? 'click' : 'tap'} {tab === 'spots' ? 'a category or end date' : 'an artist, title, type, V or intro'} to change it. Enter saves, Esc cancels.
          {tab === 'spots' ? ' A new end date moves every schedule of that spot.' : ''}
        </div>
      )}
      <datalist id="db-types">{typesSeen.map(t => <option key={t} value={t} />)}</datalist>

      <div className="flex-1 min-h-0">
        {tab === 'log' ? (
          <DayLog stationId={stationId || null} stationTz={tz} compact={!isDesktop} />
        ) : (
          <div className="qn-log h-full overflow-auto overscroll-contain" tabIndex={0} data-testid="db-table">
            {itemsTab ? songsTable : tab === 'spots' ? spotsTable : blocksTable}
          </div>
        )}
      </div>

      <PflDeck pfl={pfl} compact={!isDesktop} />

      {notice && (
        <div role="status" className={`shrink-0 px-2 py-1 text-sm ${notice.ok ? 'bg-green-800 text-green-50' : 'bg-red-900 text-red-50'}`}>{notice.text}</div>
      )}

      {/* Status bar: Page, Rec, View, Tot */}
      {tab !== 'log' && (
        <div className="shrink-0 bg-[#c0c0c0] border-t border-white flex items-stretch gap-1 p-0.5 text-[12px] text-black pb-[max(0.125rem,env(safe-area-inset-bottom))]" data-testid="db-status">
          {itemsTab && (
            <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)} className="qn-btn px-2 min-h-[32px]" aria-label="Previous page">◀</button>
          )}
          <span className="qn-sunken px-2 flex items-center">Page {page + 1}{itemsTab && pages > 1 ? ` of ${pages}` : ''}</span>
          {itemsTab && (
            <button type="button" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)} className="qn-btn px-2 min-h-[32px]" aria-label="Next page">▶</button>
          )}
          <span className="qn-sunken px-2 flex items-center">Rec {recIdx >= 0 ? page * PAGE * (itemsTab ? 1 : 0) + recIdx + 1 : 0}</span>
          <span className="qn-sunken px-2 flex items-center" data-testid="db-view">View {view}</span>
          <span className="qn-sunken px-2 flex items-center" data-testid="db-tot">Tot {tot}</span>
          <button type="button" onClick={() => window.dispatchEvent(new CustomEvent(REPORT_BUG_EVENT))}
            className="ml-auto px-2 underline hover:text-red-700 min-h-[32px]">🐞<span className="hidden sm:inline"> Report a bug</span></button>
        </div>
      )}
    </div>
  );
}

const GRID_SONGS = 'minmax(8rem,1.1fr) minmax(10rem,1.5fr) minmax(7rem,0.9fr) 2.5rem 3.5rem 3rem minmax(8rem,1fr) 6rem 3.5rem';
const GRID_JINGLES = 'minmax(12rem,2fr) minmax(7rem,1fr) 4rem minmax(8rem,1.2fr) 6rem 3.5rem';
const GRID_SPOTS = 'minmax(14rem,34rem) minmax(8rem,14rem) 7rem 4rem minmax(5rem,1fr)';
const GRID_BLOCKS = '2.5rem 4rem minmax(14rem,28rem) 3.5rem 6rem minmax(6rem,1fr)';
