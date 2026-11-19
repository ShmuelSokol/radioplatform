import { useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createSponsor, listClients, type ClientStatus, type ClientSummary } from '../../api/sponsors';
import Spinner from '../../components/Spinner';
import { useMayChange } from '../../hooks/useMayChange';
import { CLIENT_STATUS_LABEL, StatusPill, span, when } from '../../components/sponsors/ClientParts';
import BookSponsorship from '../../components/sponsors/BookSponsorship';

/**
 * Sponsors as a client list, the way radio traffic systems keep advertisers:
 * one line per client, whether they are on the air, since and until when, and
 * what they have going. Opening a client shows their spots, contracts and plays.
 */

const FILTERS: { key: 'all' | ClientStatus; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'running', label: 'On the air' },
  { key: 'upcoming', label: 'Starting soon' },
  { key: 'paused', label: 'Paused' },
  { key: 'ended', label: 'Ended' },
  { key: 'none', label: 'No contract yet' },
];
const ORDER: ClientStatus[] = ['running', 'upcoming', 'paused', 'ended', 'none'];
const input = 'w-full min-w-0 px-3 py-2 border rounded-lg';

const emptyForm = { name: '', company_name: '', contact_name: '', contact_phone: '', contact_email: '', notes: '' };

export default function Sponsors() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const mayChange = useMayChange();
  const [params, setParams] = useSearchParams();
  const filter = (params.get('show') as 'all' | ClientStatus) || 'all';
  const search = params.get('q') || '';
  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value && !(key === 'show' && value === 'all')) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  const { data: clients, isLoading, isError } = useQuery({ queryKey: ['sponsor-clients'], queryFn: listClients });
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState('');

  const createMut = useMutation({
    mutationFn: () =>
      createSponsor({
        name: form.name.trim(),
        company_name: form.company_name.trim() || null,
        contact_name: form.contact_name.trim() || null,
        contact_phone: form.contact_phone.trim() || null,
        contact_email: form.contact_email.trim() || null,
        notes: form.notes.trim() || null,
      }),
    onSuccess: (s) => {
      queryClient.invalidateQueries({ queryKey: ['sponsor-clients'] });
      queryClient.invalidateQueries({ queryKey: ['sponsors'] });
      setForm(emptyForm);
      setShowForm(false);
      navigate(`/admin/sponsors/${s.id}`);
    },
    onError: () => setError('Could not save the client. Check the name and try again.'),
  });

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: clients?.length ?? 0 };
    for (const x of clients ?? []) c[x.status] = (c[x.status] ?? 0) + 1;
    return c;
  }, [clients]);

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase();
    return (clients ?? [])
      .filter((c) => filter === 'all' || c.status === filter)
      .filter(
        (c) =>
          !q ||
          [c.name, c.company_name, c.contact_name, c.contact_phone, c.contact_email]
            .filter(Boolean)
            .some((v) => String(v).toLowerCase().includes(q)),
      )
      .sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status) || a.name.localeCompare(b.name));
  }, [clients, filter, search]);

  return (
    <div className="w-full space-y-4">
      {mayChange && params.get('book') && <BookSponsorship start={{ sponsorId: params.get('sponsor') || undefined }} onClose={() => setParam('book', '')} />}
      <div className="flex flex-wrap justify-between items-center gap-3">
        <div>
          <h1 className="text-3xl font-bold">Clients</h1>
          <p className="text-sm text-gray-600">Every client once: whether they are on the air, and since and until when. Open one for their spots, contracts and what played.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {mayChange && (
            <button onClick={() => setParam('book', '1')} className="px-4 py-2 bg-blue-600 text-white rounded-lg" data-testid="book-open">
              Book a sponsorship
            </button>
          )}
          <Link to="/admin/sponsorship-board" className="px-3 py-2 border rounded-lg text-sm">Sponsorship board</Link>
          <Link to="/admin/spot-schedule" className="px-3 py-2 border rounded-lg text-sm">All contracts</Link>
          <Link to="/admin/spot-recordings" className="px-3 py-2 border rounded-lg text-sm">Record Spots</Link>
          <Link to="/admin/campaigns" className="px-3 py-2 border rounded-lg text-sm">Sponsor requests</Link>
          {mayChange && (
            <button
              onClick={() => { setShowForm(!showForm); setError(''); }}
              className="px-3 py-2 border rounded-lg text-sm"
            >
              {showForm ? 'Cancel' : 'New client'}
            </button>
          )}
        </div>
      </div>

      {showForm && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!form.name.trim()) return setError('Give the client a name.');
            createMut.mutate();
          }}
          className="bg-white p-4 sm:p-6 rounded-lg shadow space-y-3"
          aria-label="New client"
        >
          <h2 className="text-lg font-semibold">New client</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <label className="block text-sm">Name on air (the business)<input className={input} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Pita Hut" /></label>
            <label className="block text-sm">Company (for billing)<input className={input} value={form.company_name} onChange={(e) => setForm({ ...form, company_name: e.target.value })} /></label>
            <label className="block text-sm">Contact person<input className={input} value={form.contact_name} onChange={(e) => setForm({ ...form, contact_name: e.target.value })} /></label>
            <label className="block text-sm">Phone<input className={input} type="tel" value={form.contact_phone} onChange={(e) => setForm({ ...form, contact_phone: e.target.value })} /></label>
            <label className="block text-sm">Email<input className={input} type="email" value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} /></label>
            <label className="block text-sm md:col-span-3">Notes<textarea className={input} rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
          </div>
          {error && <p className="text-sm text-red-600">{error}</p>}
          <button type="submit" disabled={createMut.isPending} className="px-4 py-2 bg-green-600 text-white rounded-lg disabled:opacity-50">
            {createMut.isPending ? <Spinner /> : 'Save client'}
          </button>
        </form>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setParam('show', f.key)}
            aria-pressed={filter === f.key}
            className={`px-3 py-1.5 rounded-lg border text-sm ${filter === f.key ? 'bg-blue-600 text-white' : 'bg-white'}`}
          >
            {f.label} ({counts[f.key] ?? 0})
          </button>
        ))}
        <input
          type="search"
          value={search}
          onChange={(e) => setParam('q', e.target.value)}
          placeholder="Find a client, contact or phone"
          aria-label="Find a client"
          className="flex-1 min-w-0 sm:min-w-[14rem] px-3 py-1.5 border rounded-lg"
        />
      </div>

      {isLoading ? (
        <div className="p-8"><Spinner /> Loading clients...</div>
      ) : isError ? (
        <p className="p-6 text-red-600">The client list could not be loaded. Try again in a moment.</p>
      ) : !shown.length ? (
        <p className="p-6 text-gray-500 bg-white rounded-lg shadow">
          {clients?.length ? 'No client matches.' : 'No clients yet. Add one with New client.'}
        </p>
      ) : (
        <div className="bg-white rounded-lg shadow">
          <table className="phone-cards w-full text-sm" data-testid="client-list">
            <thead className="bg-gray-50 text-left">
              <tr>
                <th className="px-3 py-2">Client</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2 hidden md:table-cell">Active</th>
                <th className="px-3 py-2 hidden lg:table-cell">Contracts</th>
                <th className="px-3 py-2 hidden lg:table-cell">Spots</th>
                <th className="px-3 py-2 hidden md:table-cell">Plays (7 days)</th>
                <th className="px-3 py-2 hidden xl:table-cell">Last played</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {shown.map((c) => <ClientLine key={c.id} c={c} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ClientLine({ c }: { c: ClientSummary }) {
  const navigate = useNavigate();
  const open = () => navigate(`/admin/sponsors/${c.id}`);
  const who = [c.company_name && c.company_name !== c.name ? c.company_name : null, c.contact_name, c.contact_phone]
    .filter(Boolean)
    .join(' · ');
  return (
    <tr className="hover:bg-blue-50 cursor-pointer" onClick={open} data-testid="client-row" data-nav-id={`client-${c.id}`}>
      <td className="px-3 py-2">
        <Link to={`/admin/sponsors/${c.id}`} className="font-semibold text-blue-700 hover:underline" onClick={(e) => e.stopPropagation()}>
          {c.name}
        </Link>
        {who && <div className="text-xs text-gray-500">{who}</div>}
      </td>
      <td className="px-3 py-2" data-label="Status">
        <StatusPill status={c.status} label={CLIENT_STATUS_LABEL[c.status]} />
      </td>
      <td className="px-3 py-2 hidden md:table-cell" data-label="Active">
        {span(c.active_from, c.active_until, c.open_ended) || <span className="text-gray-400">Never</span>}
      </td>
      <td className="px-3 py-2 hidden lg:table-cell" data-label="Contracts">
        {c.contracts_total ? `${c.contracts_running} running of ${c.contracts_total}` : <span className="text-gray-400">None</span>}
      </td>
      <td className="px-3 py-2 hidden lg:table-cell" data-label="Spots">
        {c.spots_on_air} on air{c.spots_in_progress ? `, ${c.spots_in_progress} in the works` : ''}
      </td>
      <td className="px-3 py-2 hidden md:table-cell" data-label="Plays (7 days)">
        {c.plays_7_days} <span className="text-gray-400">({c.plays_total} in all)</span>
      </td>
      <td className="px-3 py-2 hidden xl:table-cell" data-label="Last played">
        {when(c.last_played_at) || <span className="text-gray-400">Never</span>}
      </td>
    </tr>
  );
}
