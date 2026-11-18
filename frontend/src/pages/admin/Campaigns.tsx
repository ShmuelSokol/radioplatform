import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useCampaigns, useCreateCampaign } from '../../hooks/useCampaigns';
import { useAuth } from '../../hooks/useAuth';
import { canAccess, canChange } from '../../auth/access';
import { listSponsors } from '../../api/sponsors';
import { CAMPAIGN_STATUSES } from '../../api/campaigns';
import Spinner from '../../components/Spinner';
import { CampaignFacts, StatusBadge, statusLabel } from '../../components/campaigns/CampaignParts';

const LIMIT = 500;
const input = 'w-full min-h-[44px] border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500';

/**
 * Sponsorship campaigns across every sponsor. Managers and admins create a
 * campaign for a sponsor; DJs and viewers see the list view only.
 */
export default function AdminCampaigns() {
  const { user } = useAuth();
  const mayChange = canChange(user?.role, '/admin/campaigns');
  const mayOpenClients = canAccess(user?.role, '/admin/sponsors');
  const [params, setParams] = useSearchParams();
  const sponsorFilter = params.get('sponsor') || '';
  const statusFilter = params.get('status') || '';
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true });
  };

  const all = useCampaigns({ limit: LIMIT });
  const filtered = useCampaigns({ sponsorId: sponsorFilter || undefined, limit: LIMIT });
  // The sponsor list is for managers (the API refuses other roles); others pick
  // from the sponsors that have campaigns.
  const sponsorsQ = useQuery({ queryKey: ['sponsors'], queryFn: listSponsors, enabled: mayChange });

  const sponsorOptions = useMemo(() => {
    const m = new Map<string, string>();
    for (const s of sponsorsQ.data ?? []) m.set(s.id, s.name);
    for (const c of all.data ?? []) if (!m.has(c.sponsor_id)) m.set(c.sponsor_id, c.sponsor_name || 'Unnamed sponsor');
    return [...m.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [sponsorsQ.data, all.data]);

  const campaigns = (filtered.data ?? []).filter(c => !statusFilter || c.status === statusFilter);
  const [showForm, setShowForm] = useState(false);

  return (
    <div className="w-full space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl sm:text-3xl font-bold text-gray-800">Sponsorship Campaigns</h1>
          <p className="text-sm text-gray-500">Every sponsor's campaigns: drafts, comments and status.</p>
        </div>
        {mayChange && (
          <button
            type="button"
            onClick={() => setShowForm(!showForm)}
            className="min-h-[44px] px-4 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm font-medium"
          >
            {showForm ? 'Cancel' : 'New Campaign'}
          </button>
        )}
      </div>

      {mayOpenClients && (
        <div className="bg-yellow-50 border border-yellow-300 rounded-lg p-3 text-sm flex flex-wrap items-center gap-x-3 gap-y-2" data-testid="clients-pointer">
          <span className="min-w-0">Campaigns are only the sponsor's requests and drafts. Every client with all their spots, contracts and what played is in <b>All Clients</b>.</span>
          <Link
            to={sponsorFilter ? `/admin/sponsors/${sponsorFilter}` : '/admin/sponsors'}
            className="inline-flex items-center min-h-[44px] px-3 rounded-lg bg-yellow-400 text-black font-semibold"
          >
            {sponsorFilter ? `Open ${sponsorOptions.find(([id]) => id === sponsorFilter)?.[1] ?? 'this client'}` : 'Open All Clients'}
          </Link>
        </div>
      )}

      {mayChange && showForm && (
        <CreateCampaignForm
          sponsors={sponsorsQ.data?.map(s => [s.id, s.name] as [string, string]) ?? []}
          sponsorsLoading={sponsorsQ.isLoading}
          defaultSponsor={sponsorFilter}
          onDone={() => setShowForm(false)}
        />
      )}

      <div className="flex flex-wrap gap-3">
        <label className="flex-1 min-w-[12rem] sm:flex-none sm:w-64">
          <span className="block text-xs font-medium text-gray-500 mb-1">Sponsor</span>
          <select aria-label="Filter by sponsor" value={sponsorFilter} onChange={e => setFilter('sponsor', e.target.value)} className={input}>
            <option value="">All sponsors</option>
            {sponsorOptions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </label>
        <label className="flex-1 min-w-[10rem] sm:flex-none sm:w-48">
          <span className="block text-xs font-medium text-gray-500 mb-1">Status</span>
          <select aria-label="Filter by status" value={statusFilter} onChange={e => setFilter('status', e.target.value)} className={input}>
            <option value="">Any status</option>
            {CAMPAIGN_STATUSES.map(s => <option key={s} value={s}>{statusLabel(s)}</option>)}
          </select>
        </label>
      </div>

      {filtered.isLoading ? (
        <div className="text-center py-10"><Spinner /></div>
      ) : filtered.isError ? (
        <div className="bg-red-50 border border-red-200 text-red-800 rounded-xl p-4">Could not load the campaigns. Try again in a moment.</div>
      ) : !campaigns.length ? (
        <div className="bg-white rounded-xl shadow p-8 text-center text-gray-500">
          {sponsorFilter || statusFilter ? 'No campaigns match these filters.' : 'No sponsorship campaigns yet.'}
        </div>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
          {campaigns.map(c => (
            <Link
              key={c.id}
              to={`/admin/campaigns/${c.id}`}
              data-testid="campaign-row"
              className="bg-white rounded-xl shadow p-4 hover:shadow-md transition flex items-start justify-between gap-3 min-h-[44px]"
            >
              <div className="min-w-0">
                <h3 className="font-semibold text-gray-800 break-words">{c.name}</h3>
                <p className="text-sm text-indigo-700">{c.sponsor_name || 'Unnamed sponsor'}</p>
                {c.description && <p className="text-sm text-gray-500 mt-1 line-clamp-1">{c.description}</p>}
                <CampaignFacts c={c} className="mt-2 text-xs text-gray-500" />
              </div>
              <StatusBadge status={c.status} />
            </Link>
          ))}
        </div>
      )}
      {(filtered.data?.length ?? 0) >= LIMIT && (
        <p className="text-xs text-gray-500">Showing the newest {LIMIT}. Pick a sponsor to see older ones.</p>
      )}
    </div>
  );
}

function CreateCampaignForm({ sponsors, sponsorsLoading, defaultSponsor, onDone }: {
  sponsors: [string, string][];
  sponsorsLoading: boolean;
  defaultSponsor: string;
  onDone: () => void;
}) {
  const create = useCreateCampaign();
  const [sponsorId, setSponsorId] = useState(defaultSponsor);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [budget, setBudget] = useState('');
  const [error, setError] = useState('');

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (start && end && end < start) { setError('The end date is before the start date.'); return; }
    const dollars = budget.trim() ? Number(budget) : undefined;
    if (dollars !== undefined && (!Number.isFinite(dollars) || dollars < 0)) { setError('The budget must be a positive amount.'); return; }
    try {
      await create.mutateAsync({
        sponsor_id: sponsorId,
        name: name.trim(),
        description: description.trim() || undefined,
        start_date: start || undefined,
        end_date: end || undefined,
        budget_cents: dollars !== undefined ? Math.round(dollars * 100) : undefined,
      });
      onDone();
    } catch (err: any) {
      const d = err?.response?.data?.detail;
      setError(typeof d === 'string' ? d : 'The campaign was not saved. Try again.');
    }
  };

  return (
    <form onSubmit={submit} data-testid="new-campaign-form" className="bg-white rounded-xl shadow p-4 sm:p-6 space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Sponsor</label>
          <select required value={sponsorId} onChange={e => setSponsorId(e.target.value)} className={input}>
            <option value="">{sponsorsLoading ? 'Loading sponsors...' : 'Choose a sponsor'}</option>
            {sponsors.map(([id, n]) => <option key={id} value={id}>{n}</option>)}
          </select>
          {!sponsorsLoading && !sponsors.length && (
            <p className="text-xs text-gray-500 mt-1">No sponsors yet. Add one under <Link to="/admin/sponsors" className="text-indigo-600 underline">Sponsors</Link>.</p>
          )}
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Campaign Name</label>
          <input required type="text" value={name} onChange={e => setName(e.target.value)} className={input} />
        </div>
      </div>
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
        <textarea value={description} onChange={e => setDescription(e.target.value)} rows={2}
          className="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500" />
      </div>
      <div className="grid gap-4 grid-cols-1 sm:grid-cols-3">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Start Date</label>
          <input type="date" value={start} onChange={e => setStart(e.target.value)} className={input} />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">End Date</label>
          <input type="date" value={end} min={start || undefined} onChange={e => setEnd(e.target.value)} className={input} />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Budget ($)</label>
          <input type="number" inputMode="decimal" min={0} step="0.01" value={budget} onChange={e => setBudget(e.target.value)} className={input} placeholder="Optional" />
        </div>
      </div>
      {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
      <button
        type="submit"
        disabled={create.isPending}
        className="min-h-[44px] px-4 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm disabled:opacity-50"
      >
        {create.isPending ? <><Spinner className="mr-2" />Creating...</> : 'Create Campaign'}
      </button>
    </form>
  );
}
