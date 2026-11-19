import { totalsLine } from '../../components/sponsors/contractTotals';
import { useEffect, useMemo, useState } from 'react';
import ContractEditor from '../../components/sponsors/ContractEditor';
import BookSponsorship from '../../components/sponsors/BookSponsorship';
import { useCampaigns } from '../../hooks/useCampaigns';
import { CampaignFacts, StatusBadge } from '../../components/campaigns/CampaignParts';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  deleteSponsor,
  getClientAccount,
  getClientMissedPlays,
  getClientPlays,
  updateSponsor,
  type ClientAccount,
  type ClientContract,
  type ClientSpot,
  type PlayFilters,
} from '../../api/sponsors';
import { daypartText, updateSpotSchedule } from '../../api/spotSchedules';
import { getAssetAudioUrl } from '../../api/assets';
import { fetchSpotAudioUrl } from '../../api/spotRecordings';
import Spinner from '../../components/Spinner';
import { useMayChange } from '../../hooks/useMayChange';
import {
  CLIENT_STATUS_LABEL,
  CONTRACT_STATE_LABEL,
  StatusPill,
  day,
  describeContract,
  seconds,
  span,
  when,
} from '../../components/sponsors/ClientParts';

/**
 * One client, the way a traffic system's advertiser screen shows it: their spots
 * through the whole flow (wording, recording, approval, on the air, finished),
 * their contracts with each one's settings and plays, the log of every airing
 * (affidavit), the plays they missed, and their details.
 */

type Tab = 'contracts' | 'spots' | 'plays' | 'missed' | 'requests' | 'details';
const TABS: { key: Tab; label: string }[] = [
  { key: 'contracts', label: 'Contracts' },
  { key: 'spots', label: 'Spots' },
  { key: 'plays', label: 'What aired' },
  { key: 'missed', label: 'Missed plays' },
  { key: 'requests', label: 'Requests' },
  { key: 'details', label: 'Details' },
];

export default function SponsorDetail() {
  const { sponsorId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.key === params.get('tab'))?.key ?? 'contracts') as Tab;
  const setTab = (t: Tab) => {
    const next = new URLSearchParams(params);
    if (t === 'contracts') next.delete('tab'); else next.set('tab', t);
    setParams(next, { replace: true });
  };
  const mayBook = useMayChange('/admin/spot-schedule');
  const [booking, setBooking] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ['sponsor-account', sponsorId],
    queryFn: () => getClientAccount(sponsorId),
    enabled: !!sponsorId,
  });
  const missed = useQuery({
    queryKey: ['sponsor-missed', sponsorId],
    queryFn: () => getClientMissedPlays(sponsorId),
    enabled: !!sponsorId,
  });

  if (isLoading) return <div className="p-8"><Spinner /> Loading the client...</div>;
  if (isError || !data)
    return (
      <div className="p-6 space-y-3">
        <p className="text-red-600">This client could not be found.</p>
        <Link to="/admin/sponsors" className="text-blue-700 underline">Back to all clients</Link>
      </div>
    );
  const c = data.client;
  const missedPast = missed.data?.missed_past ?? 0;

  return (
    <div className="w-full space-y-4">
      {booking && <BookSponsorship start={{ sponsorId: c.id }} onClose={() => setBooking(false)} />}
      <Link to="/admin/sponsors" className="text-sm text-blue-700 hover:underline">← All clients</Link>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-3xl font-bold break-words">{c.name}</h1>
          <div className="flex flex-wrap items-center gap-2 mt-1 text-sm text-gray-600">
            <StatusPill status={c.status} label={CLIENT_STATUS_LABEL[c.status]} />
            <span>{span(c.active_from, c.active_until, c.open_ended) || 'No contract yet'}</span>
            {(c.contact_name || c.contact_phone) && <span>· {[c.contact_name, c.contact_phone].filter(Boolean).join(', ')}</span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {mayBook && (
            <button onClick={() => setBooking(true)} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm" data-testid="book-open">
              Book a sponsorship
            </button>
          )}
          <Link to={`/admin/spot-recordings?sponsor=${c.id}`} className="px-3 py-2 border rounded-lg text-sm">Record a spot</Link>
        </div>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2" data-testid="client-tiles">
        <Tile label="Contracts running" value={`${c.contracts_running} of ${c.contracts_total}`} />
        <Tile label="Spots on the air" value={String(c.spots_on_air)} />
        <Tile label="Spots in the works" value={String(c.spots_in_progress)} />
        <Tile label="Plays, last 7 days" value={String(c.plays_7_days)} />
        <Tile label="Last played" value={when(c.last_played_at) || 'Never'} small />
        <Tile label={`Missed, last ${missed.data?.days ?? 14} days`} value={missed.isLoading ? '...' : String(missedPast)} warn={missedPast > 0} />
      </div>

      <div className="flex flex-wrap gap-1 border-b" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm rounded-t-lg border border-b-0 ${tab === t.key ? 'bg-blue-600 text-white' : 'bg-white'}`}
          >
            {t.label}
            {t.key === 'spots' ? ` (${data.spots.length})` : t.key === 'contracts' ? ` (${data.contracts.length})` : ''}
          </button>
        ))}
      </div>

      {tab === 'spots' && <SpotsTab account={data} onPlays={(assetId) => setParams({ tab: 'plays', spot: assetId }, { replace: true })} />}
      {tab === 'contracts' && <ContractsTab account={data} />}
      {tab === 'plays' && <PlaysTab account={data} />}
      {tab === 'missed' && <MissedTab loading={missed.isLoading} data={missed.data} />}
      {tab === 'requests' && <RequestsTab clientId={c.id} />}
      {tab === 'details' && <DetailsTab account={data} />}
    </div>
  );
}

function Tile({ label, value, small, warn }: { label: string; value: string; small?: boolean; warn?: boolean }) {
  return (
    <div className={`bg-white rounded-lg shadow p-3 ${warn ? 'border border-red-300' : ''}`}>
      <div className="text-xs text-gray-500">{label}</div>
      <div className={`${small ? 'text-sm' : 'text-xl'} font-semibold ${warn ? 'text-red-700' : ''}`}>{value}</div>
    </div>
  );
}

// ── listening ───────────────────────────────────────────────────────

function useListen() {
  const [playing, setPlaying] = useState<{ key: string; url: string } | null>(null);
  const [failed, setFailed] = useState('');
  const toggle = async (key: string, load: () => Promise<string>) => {
    setFailed('');
    if (playing?.key === key) return setPlaying(null);
    try {
      setPlaying({ key, url: await load() });
    } catch {
      setFailed(key);
    }
  };
  const player = playing ? <audio src={playing.url} autoPlay onEnded={() => setPlaying(null)} /> : null;
  return { playing, failed, toggle, player };
}

// ── spots, by where they are in the flow ────────────────────────────

const STAGE_HELP: Record<string, string> = {
  wording: 'The script is written but the wording is not approved yet.',
  approved_not_recorded: 'The wording is approved; the spot still has to be recorded.',
  recorded_needs_approval: 'Audio is made; listen and approve it in Record Spots.',
  ready_not_scheduled: 'Approved audio that no contract uses (for example an older version).',
  starts_soon: 'In a contract that has not started yet.',
  on_air: 'In a running contract.',
  paused: 'Its contract is paused.',
  finished: 'Its contracts are over.',
  rejected: 'Rejected, or its audio cannot air.',
};

// Sections in order of what matters most: what is on the air, what comes next, what
// is still being made; the strip above them keeps the order of the flow itself.
const SECTION_ORDER = [
  'on_air', 'starts_soon', 'paused', 'recorded_needs_approval', 'approved_not_recorded', 'wording',
  'ready_not_scheduled', 'finished', 'rejected',
];

function SpotsTab({ account, onPlays }: { account: ClientAccount; onPlays: (assetId: string) => void }) {
  const listen = useListen();
  const byStage = useMemo(() => {
    const m = new Map<string, ClientSpot[]>();
    for (const s of account.spots) m.set(s.stage, [...(m.get(s.stage) ?? []), s]);
    return m;
  }, [account.spots]);

  if (!account.spots.length)
    return (
      <p className="p-6 bg-white rounded-lg shadow text-gray-500">
        No spots yet. Write one in <Link className="text-blue-700 underline" to={`/admin/spot-recordings?sponsor=${account.client.id}`}>Record Spots</Link>.
      </p>
    );

  return (
    <div className="space-y-4">
      {listen.player}
      <ol className="flex flex-wrap gap-1 text-xs" aria-label="The flow of a spot" data-testid="spot-flow">
        {account.stages.map((st, i) => (
          <li key={st.key} className={`px-2 py-1 rounded border ${byStage.has(st.key) ? 'bg-blue-50 border-blue-300 font-semibold' : 'bg-white text-gray-400'}`}>
            {i + 1}. {st.label} ({byStage.get(st.key)?.length ?? 0})
          </li>
        ))}
      </ol>
      {SECTION_ORDER.map((k) => account.stages.find((st) => st.key === k))
        .filter((st): st is { key: string; label: string } => !!st && byStage.has(st.key))
        .map((st) => (
          <section key={st.key} className="bg-white rounded-lg shadow" data-testid={`stage-${st.key}`}>
            <div className="px-4 py-2 border-b">
              <h2 className="font-semibold">{st.label}</h2>
              <p className="text-xs text-gray-500">{STAGE_HELP[st.key]}</p>
            </div>
            <ul className="divide-y">
              {byStage.get(st.key)!.map((s) => (
                <SpotLine key={s.key} s={s} account={account} listen={listen} onPlays={onPlays} />
              ))}
            </ul>
          </section>
        ))}
    </div>
  );
}

function SpotLine({ s, account, listen, onPlays }: {
  s: ClientSpot; account: ClientAccount; listen: ReturnType<typeof useListen>; onPlays: (assetId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const contracts = account.contracts.filter((c) => s.contract_ids.includes(c.id));
  const canListen = !!s.asset_id || (s.kind === 'recording' && s.stage === 'recorded_needs_approval');
  const load = () => (s.asset_id ? getAssetAudioUrl(s.asset_id) : fetchSpotAudioUrl(s.recording_id!));
  return (
    <li className="px-4 py-3 space-y-1" data-testid="spot-line">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-medium break-words min-w-0">{s.title}</span>
        {s.duration != null && <span className="text-xs text-gray-500">{seconds(s.duration)}</span>}
        <span className="text-xs text-gray-500">
          {s.kind === 'campaign' ? 'Request from the sponsor portal' : s.kind === 'recording' ? 'In Record Spots' : 'Library audio'}
        </span>
      </div>
      {s.status_detail && <p className="text-sm text-gray-700">{s.status_detail}</p>}
      {s.kind === 'audio' && (
        <p className="text-sm text-gray-600">
          {s.plays_total ? `Played ${s.plays_total} times, first ${when(s.first_played_at)}, last ${when(s.last_played_at)}` : 'Never played'}
          {contracts.length > 0 && ` · ${contracts.length === 1 ? 'in 1 contract' : `in ${contracts.length} contracts`}: ${contracts.map((c) => `${c.station_name} (${CONTRACT_STATE_LABEL[c.state].toLowerCase()})`).join(', ')}`}
        </p>
      )}
      <div className="flex flex-wrap gap-2 pt-1">
        {canListen && (
          <button className="px-3 py-1 border rounded text-sm" onClick={() => listen.toggle(s.key, load)}>
            {listen.playing?.key === s.key ? 'Stop' : 'Listen'}
          </button>
        )}
        {s.script && (
          <button className="px-3 py-1 border rounded text-sm" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? 'Hide script' : 'Script'}
          </button>
        )}
        {s.kind === 'audio' && s.plays_total > 0 && (
          <button className="px-3 py-1 border rounded text-sm" onClick={() => onPlays(s.asset_id!)}>What played</button>
        )}
        {s.kind === 'recording' && <Link className="px-3 py-1 border rounded text-sm" to={`/admin/spot-recordings?sponsor=${account.client.id}`}>Open in Record Spots</Link>}
        {s.kind === 'campaign' && <Link className="px-3 py-1 border rounded text-sm" to={`/admin/campaigns/${s.campaign_id}`}>Open campaign</Link>}
      </div>
      {listen.failed === s.key && <p className="text-sm text-red-600">The audio could not be loaded.</p>}
      {open && s.script && <p className="text-sm whitespace-pre-wrap bg-gray-50 border rounded p-2">{s.script}</p>}
    </li>
  );
}

// ── contracts and their settings ────────────────────────────────────

function ContractsTab({ account }: { account: ClientAccount }) {
  const current = account.contracts.filter((c) => c.state !== 'ended');
  const past = account.contracts.filter((c) => c.state === 'ended');
  const listen = useListen();
  if (!account.contracts.length)
    return (
      <p className="p-6 bg-white rounded-lg shadow text-gray-500">
        No contracts yet. Add one with Book a sponsorship at the top of the page.
      </p>
    );
  return (
    <div className="space-y-4">
      {listen.player}
      <ContractTable title="Current contracts" rows={current} listen={listen} account={account} empty="No contract is running or waiting to start." />
      {past.length > 0 && <ContractTable title="Past contracts" rows={past} listen={listen} account={account} />}
    </div>
  );
}

function ContractTable({ title, rows, listen, account, empty }: {
  title: string; rows: ClientContract[]; listen: ReturnType<typeof useListen>; account: ClientAccount; empty?: string;
}) {
  return (
    <section className="bg-white rounded-lg shadow">
      <h2 className="px-4 py-2 border-b font-semibold">{title}</h2>
      {!rows.length ? (
        <p className="p-4 text-gray-500 text-sm">{empty}</p>
      ) : (
        <table className="phone-cards w-full text-sm" data-testid="contract-table">
          <thead className="bg-gray-50 text-left">
            <tr>
              <th className="px-3 py-2">Spot</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2 hidden md:table-cell">Station</th>
              <th className="px-3 py-2 hidden md:table-cell">When it airs</th>
              <th className="px-3 py-2 hidden md:table-cell">Dates</th>
              <th className="px-3 py-2 hidden md:table-cell">Plays</th>
              <th className="px-3 py-2"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((c) => <ContractLine key={c.id} c={c} listen={listen} account={account} />)}
          </tbody>
        </table>
      )}
    </section>
  );
}

function ContractLine({ c, listen, account }: { c: ClientContract; listen: ReturnType<typeof useListen>; account: ClientAccount }) {
  const queryClient = useQueryClient();
  const mayChange = useMayChange('/admin/spot-schedule');
  const [editingEnd, setEditingEnd] = useState(false);
  const [editing, setEditing] = useState(false);
  const [end, setEnd] = useState(c.end_date ?? '');
  const [error, setError] = useState('');
  useEffect(() => setEnd(c.end_date ?? ''), [c.end_date]);
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ['sponsor-account', account.client.id] });
    queryClient.invalidateQueries({ queryKey: ['sponsor-clients'] });
    queryClient.invalidateQueries({ queryKey: ['spot-schedules'] });
  };
  const mut = useMutation({
    mutationFn: (data: { is_active?: boolean; end_date?: string | null }) => updateSpotSchedule(c.id, data),
    onSuccess: () => { setEditingEnd(false); setError(''); done(); },
    onError: () => setError('Could not save. Try again.'),
  });
  return (
    <tr data-testid="contract-row">
      <td className="px-3 py-2">
        <div>{c.asset_title}</div>
        {c.name && <div className="text-xs text-gray-500 font-normal">{c.name}</div>}
        <div className="text-xs text-gray-700 font-normal mt-1" data-testid="contract-totals">{totalsLine(c, c.duration)}</div>
      </td>
      <td className="px-3 py-2" data-label="Status">
        <StatusPill status={c.state} label={CONTRACT_STATE_LABEL[c.state]} />
        {c.problem && <div className="text-xs text-red-700 mt-1" data-testid="contract-problem">{c.problem}, so it does not air.</div>}
      </td>
      <td className="px-3 py-2 hidden md:table-cell" data-label="Station">{c.station_name}</td>
      <td className="px-3 py-2 hidden md:table-cell" data-label="When it airs">{describeContract(c)}</td>
      <td className="px-3 py-2 hidden md:table-cell" data-label="Dates">
        {c.start_date ? day(c.start_date) : 'From the start'} to {c.end_date ? day(c.end_date) : 'no end date'}
        {editingEnd && (
          <form
            className="flex flex-wrap items-center gap-2 mt-1"
            onSubmit={(e) => { e.preventDefault(); mut.mutate({ end_date: end || null }); }}
          >
            <label className="text-xs">Last day <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className="px-2 py-1 border rounded" /></label>
            <button type="submit" className="px-2 py-1 border rounded text-xs" disabled={mut.isPending}>Save</button>
            <button type="button" className="px-2 py-1 border rounded text-xs" onClick={() => setEditingEnd(false)}>Cancel</button>
          </form>
        )}
        {error && <div className="text-xs text-red-600">{error}</div>}
      </td>
      <td className="px-3 py-2 hidden md:table-cell" data-label="Plays">
        {c.plays_in_contract} in this contract, {c.plays_7_days} in the last 7 days
        {c.last_played_at && <div className="text-xs text-gray-500">Last {when(c.last_played_at)}</div>}
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap gap-2">
          <button className="px-2 py-1 border rounded text-xs" onClick={() => listen.toggle(c.id, () => getAssetAudioUrl(c.asset_id))}>
            {listen.playing?.key === c.id ? 'Stop' : 'Listen'}
          </button>
          {mayChange && (
            <button className="px-3 py-1 rounded text-xs bg-blue-600 text-white font-semibold" data-testid="contract-edit" onClick={() => setEditing(true)}>
              Edit
            </button>
          )}
          {editing && <ContractEditor contract={c} sponsorId={account.client.id} onClose={() => setEditing(false)} />}
          {mayChange && c.state !== 'ended' && (
            <button className="px-2 py-1 border rounded text-xs" disabled={mut.isPending} onClick={() => mut.mutate({ is_active: !c.is_active })}>
              {c.is_active ? 'Pause' : 'Resume'}
            </button>
          )}
          {mayChange && !editingEnd && (
            <button className="px-2 py-1 border rounded text-xs" onClick={() => setEditingEnd(true)}>
              {c.state === 'ended' ? 'Extend' : 'Change end date'}
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

// ── requests: what the sponsor asked for in their portal (campaigns) ──

function RequestsTab({ clientId }: { clientId: string }) {
  const { data, isLoading, isError } = useCampaigns({ sponsorId: clientId });
  if (isLoading) return <Spinner />;
  if (isError) return <p className="p-4 text-red-700">Could not load the requests. Try again in a moment.</p>;
  return (
    <section className="bg-white rounded-lg shadow" data-testid="requests-tab">
      <div className="px-4 py-2 border-b flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Requests from the sponsor</h2>
        <Link to={`/admin/campaigns?sponsor=${clientId}`} className="text-sm text-blue-700 underline">New request or all requests</Link>
      </div>
      <p className="px-4 pt-2 text-xs text-gray-600">What the sponsor asked for in their portal: wording drafts and messages. Booking airtime is Book a sponsorship.</p>
      {!data?.length ? (
        <p className="p-4 text-gray-500 text-sm">No requests from this sponsor.</p>
      ) : (
        <ul className="divide-y">
          {data.map((r) => (
            <li key={r.id}>
              <Link to={`/admin/campaigns/${r.id}`} className="flex items-start justify-between gap-3 px-4 py-3 hover:bg-gray-50 min-h-[44px]" data-testid="request-row">
                <span className="min-w-0">
                  <span className="block font-medium break-words">{r.name}</span>
                  {r.description && <span className="block text-sm text-gray-600 line-clamp-1">{r.description}</span>}
                  <CampaignFacts c={r} className="mt-1 text-xs text-gray-500" />
                </span>
                <StatusBadge status={r.status} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ── play history (the as-run log / affidavit) ───────────────────────

const PAGE = 50;

function csvCell(v: unknown): string {
  const s = v == null ? '' : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function PlaysTab({ account }: { account: ClientAccount }) {
  const [params, setParams] = useSearchParams();
  const filters: PlayFilters = {
    station_id: params.get('station') || undefined,
    asset_id: params.get('spot') || undefined,
    date_from: params.get('from') || undefined,
    date_to: params.get('to') || undefined,
  };
  const page = Number(params.get('page') || 1);
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== 'page') next.delete('page');
    setParams(next, { replace: true });
  };
  const id = account.client.id;
  const { data, isLoading, isError } = useQuery({
    queryKey: ['sponsor-plays', id, filters, page],
    queryFn: () => getClientPlays(id, { ...filters, page, limit: PAGE }),
  });
  const [exporting, setExporting] = useState(false);

  const stations = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of account.contracts) if (c.station_id) m.set(c.station_id, c.station_name);
    for (const e of data?.entries ?? []) m.set(e.station_id, e.station_name);
    return [...m];
  }, [account.contracts, data]);
  const spots = account.spots.filter((s) => s.asset_id);

  const download = async () => {
    setExporting(true);
    try {
      const rows = [];
      for (let p = 1; p <= 20; p++) {
        const r = await getClientPlays(id, { ...filters, page: p, limit: 1000 });
        rows.push(...r.entries);
        if (rows.length >= r.total || !r.entries.length) break;
      }
      const lines = [
        ['Client', 'Date', 'Time (station clock)', 'Station', 'Spot', 'Seconds aired', 'Spot length', 'How it aired'].join(','),
        ...rows.map((e) =>
          [account.client.name, e.local_date, e.local_time, e.station_name, e.asset_title,
            e.aired_seconds != null ? Math.round(e.aired_seconds) : '', e.length_seconds != null ? Math.round(e.length_seconds) : '', e.how]
            .map(csvCell).join(','),
        ),
      ];
      const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `${account.client.name.replace(/[^\w-]+/g, '_')}-plays.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;
  return (
    <section className="bg-white rounded-lg shadow">
      <div className="px-4 py-3 border-b flex flex-wrap items-end gap-2">
        <label className="text-xs">Station
          <select className="block px-2 py-1 border rounded" value={filters.station_id ?? ''} onChange={(e) => set('station', e.target.value)}>
            <option value="">Every station</option>
            {stations.map(([sid, name]) => <option key={sid} value={sid}>{name}</option>)}
          </select>
        </label>
        <label className="text-xs min-w-0">Spot
          <select className="block max-w-full px-2 py-1 border rounded" value={filters.asset_id ?? ''} onChange={(e) => set('spot', e.target.value)}>
            <option value="">Every spot</option>
            {spots.map((s) => <option key={s.asset_id!} value={s.asset_id!}>{s.title}</option>)}
          </select>
        </label>
        <label className="text-xs">From<input type="date" className="block px-2 py-1 border rounded" value={filters.date_from ?? ''} onChange={(e) => set('from', e.target.value)} /></label>
        <label className="text-xs">To<input type="date" className="block px-2 py-1 border rounded" value={filters.date_to ?? ''} onChange={(e) => set('to', e.target.value)} /></label>
        <button className="px-3 py-1.5 border rounded text-sm ml-auto" onClick={download} disabled={exporting || !data?.total}>
          {exporting ? 'Preparing...' : 'Download (CSV)'}
        </button>
      </div>
      {isLoading ? (
        <div className="p-6"><Spinner /> Loading plays...</div>
      ) : isError ? (
        <p className="p-6 text-red-600">The play history could not be loaded.</p>
      ) : !data?.entries.length ? (
        <p className="p-6 text-gray-500">Nothing played{filters.station_id || filters.asset_id || filters.date_from || filters.date_to ? ' that matches' : ' yet'}.</p>
      ) : (
        <>
          <p className="px-4 pt-3 text-sm text-gray-600" data-testid="plays-total">{data.total} {data.total === 1 ? 'play' : 'plays'}. Times are each station's own clock.</p>
          <table className="w-full text-sm" data-testid="play-table">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="px-3 py-2">Date and time</th>
                <th className="px-3 py-2">Station and spot</th>
                <th className="px-3 py-2 hidden md:table-cell">Aired</th>
                <th className="px-3 py-2 hidden md:table-cell">How</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {data.entries.map((e) => (
                <tr key={e.id} data-testid="play-row">
                  <td className="px-3 py-2 align-top">
                    <div className="whitespace-nowrap">{day(e.local_date)}</div>
                    <div className="whitespace-nowrap text-gray-600">{e.local_time}</div>
                  </td>
                  <td className="px-3 py-2 align-top min-w-0">
                    <div className="break-words">{e.station_name}</div>
                    <div className="text-xs text-gray-600 break-words">
                      {e.asset_title}
                      {e.aired_seconds != null && e.length_seconds != null && e.aired_seconds + 2 < e.length_seconds && (
                        <span className="md:hidden text-red-700"> (cut short at {seconds(e.aired_seconds)})</span>
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2 hidden md:table-cell" data-label="Aired">
                    {e.aired_seconds != null ? seconds(e.aired_seconds) : 'Still on'}
                    {e.length_seconds != null && e.aired_seconds != null && e.aired_seconds + 2 < e.length_seconds && (
                      <span className="text-red-700"> (cut short, spot is {seconds(e.length_seconds)})</span>
                    )}
                  </td>
                  <td className="px-3 py-2 hidden md:table-cell" data-label="How">{e.how}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {pages > 1 && (
            <div className="flex flex-wrap items-center gap-2 px-4 py-3 border-t">
              <button className="px-3 py-1 border rounded text-sm" disabled={page <= 1} onClick={() => set('page', String(page - 1))}>Newer</button>
              <span className="text-sm">Page {page} of {pages}</span>
              <button className="px-3 py-1 border rounded text-sm" disabled={page >= pages} onClick={() => set('page', String(page + 1))}>Older</button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

// ── missed plays ────────────────────────────────────────────────────


function MissedTab({ loading, data }: { loading: boolean; data?: Awaited<ReturnType<typeof getClientMissedPlays>> }) {
  if (loading) return <div className="p-6"><Spinner /> Checking what was promised against what played...</div>;
  if (!data) return <p className="p-6 text-red-600">Missed plays could not be worked out.</p>;
  return (
    <section className="bg-white rounded-lg shadow">
      <p className="px-4 py-3 border-b text-sm text-gray-700">
        Last {data.days} days: {data.missed_past} promised {data.missed_past === 1 ? 'play' : 'plays'} did not air. Coming week: {data.missed_coming} have no room, {data.moved_coming} moved to a later time. The client sees the same in their portal.
      </p>
      {!data.rows.length ? (
        <p className="p-6 text-gray-500">Nothing missed. Every promised play aired.</p>
      ) : (
        <table className="phone-cards w-full text-sm" data-testid="missed-table">
          <thead className="bg-gray-50 text-left">
            <tr>
              <th className="px-3 py-2">Day</th>
              <th className="px-3 py-2">Time of day</th>
              <th className="px-3 py-2">Station</th>
              <th className="px-3 py-2 hidden md:table-cell">Spot</th>
              <th className="px-3 py-2">Promised / aired</th>
              <th className="px-3 py-2">What happened</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {data.rows.map((r, i) => (
              <tr key={i}>
                <td className="px-3 py-2">{day(r.date)}{r.when === 'coming' && ' (coming)'}</td>
                <td className="px-3 py-2" data-label="Time of day">{daypartText(r.daypart, true)}</td>
                <td className="px-3 py-2" data-label="Station">{r.station_name}</td>
                <td className="px-3 py-2 hidden md:table-cell" data-label="Spot">{r.asset_title}</td>
                <td className="px-3 py-2" data-label="Promised / aired">{r.promised} / {r.when === 'past' ? r.played : '-'}</td>
                <td className="px-3 py-2" data-label="What happened">{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

// ── details ─────────────────────────────────────────────────────────

function DetailsTab({ account }: { account: ClientAccount }) {
  const c = account.client;
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const mayChange = useMayChange();
  const start = {
    name: c.name, company_name: c.company_name ?? '', contact_name: c.contact_name ?? '',
    contact_phone: c.contact_phone ?? '', contact_email: c.contact_email ?? '', notes: account.notes ?? '',
  };
  const [form, setForm] = useState(start);
  const [msg, setMsg] = useState('');
  const changed = JSON.stringify(form) !== JSON.stringify(start);
  const save = useMutation({
    mutationFn: () =>
      updateSponsor(c.id, {
        name: form.name.trim(),
        company_name: form.company_name.trim() || null,
        contact_name: form.contact_name.trim() || null,
        contact_phone: form.contact_phone.trim() || null,
        contact_email: form.contact_email.trim() || null,
        notes: form.notes.trim() || null,
      }),
    onSuccess: () => {
      setMsg('Saved.');
      queryClient.invalidateQueries({ queryKey: ['sponsor-account', c.id] });
      queryClient.invalidateQueries({ queryKey: ['sponsor-clients'] });
      queryClient.invalidateQueries({ queryKey: ['sponsors'] });
    },
    onError: () => setMsg('Could not save. Try again.'),
  });
  const remove = useMutation({
    mutationFn: () => deleteSponsor(c.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sponsor-clients'] });
      queryClient.invalidateQueries({ queryKey: ['sponsors'] });
      navigate('/admin/sponsors');
    },
    onError: () => setMsg('This client has contracts or plays, so it is kept. End or pause its contracts instead.'),
  });
  const input = 'w-full min-w-0 px-3 py-2 border rounded-lg';
  const deletable = c.contracts_total === 0 && c.plays_total === 0 && account.spots.length === 0;
  return (
    <form
      className="bg-white rounded-lg shadow p-4 space-y-3"
      onSubmit={(e) => { e.preventDefault(); if (!form.name.trim()) return setMsg('The client needs a name.'); save.mutate(); }}
    >
      <fieldset disabled={!mayChange} className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <label className="block text-sm">Name on air (the business)<input className={input} value={form.name} onChange={(e) => { setForm({ ...form, name: e.target.value }); setMsg(''); }} /></label>
        <label className="block text-sm">Company (for billing)<input className={input} value={form.company_name} onChange={(e) => { setForm({ ...form, company_name: e.target.value }); setMsg(''); }} /></label>
        <label className="block text-sm">Contact person<input className={input} value={form.contact_name} onChange={(e) => { setForm({ ...form, contact_name: e.target.value }); setMsg(''); }} /></label>
        <label className="block text-sm">Phone<input className={input} type="tel" value={form.contact_phone} onChange={(e) => { setForm({ ...form, contact_phone: e.target.value }); setMsg(''); }} /></label>
        <label className="block text-sm">Email<input className={input} type="email" value={form.contact_email} onChange={(e) => { setForm({ ...form, contact_email: e.target.value }); setMsg(''); }} /></label>
        <label className="block text-sm md:col-span-3">Notes<textarea className={input} rows={3} value={form.notes} onChange={(e) => { setForm({ ...form, notes: e.target.value }); setMsg(''); }} /></label>
      </fieldset>
      <p className="text-sm text-gray-600">
        Client portal login: {c.has_portal_login ? 'yes, the client can sign in and see their plays' : 'none yet (add a user with the Sponsor role in Users)'}.
        {c.created_at && ` Client since ${when(c.created_at)}.`}
      </p>
      {msg && <p className="text-sm" role="status">{msg}</p>}
      {mayChange && (
        <div className="flex flex-wrap gap-2">
          <button type="submit" disabled={!changed || save.isPending} className="px-4 py-2 bg-green-600 text-white rounded-lg disabled:opacity-50">
            {save.isPending ? <Spinner /> : 'Save details'}
          </button>
          {deletable && (
            <button
              type="button"
              data-danger
              className="px-4 py-2 border border-red-300 text-red-700 rounded-lg"
              onClick={() => { if (window.confirm(`Delete ${c.name}? It has no contracts, spots or plays.`)) remove.mutate(); }}
            >
              Delete client
            </button>
          )}
        </div>
      )}
    </form>
  );
}
