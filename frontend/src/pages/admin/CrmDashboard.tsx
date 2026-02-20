import { useState } from 'react';
import {
  useCrmMembers,
  useDeactivateMember,
  useSongRankings,
  useRafflesAdmin,
  useCreateRaffle,
  useDrawRaffle,
  useRaffleEntries,
} from '../../hooks/useCrm';

type Tab = 'members' | 'rankings' | 'raffles';

export default function CrmDashboard() {
  const [tab, setTab] = useState<Tab>('members');
  const [search, setSearch] = useState('');
  const [selectedRaffle, setSelectedRaffle] = useState<string | null>(null);

  // Raffle create form
  const [rfTitle, setRfTitle] = useState('');
  const [rfPrize, setRfPrize] = useState('');
  const [rfDesc, setRfDesc] = useState('');
  const [rfStart, setRfStart] = useState('');
  const [rfEnd, setRfEnd] = useState('');

  const { data: membersData, isLoading: membersLoading } = useCrmMembers(0, 100, search || undefined);
  const deactivateMember = useDeactivateMember();
  const { data: rankings, isLoading: rankingsLoading } = useSongRankings(0, 100);
  const { data: raffles, isLoading: rafflesLoading } = useRafflesAdmin(0, 100);
  const createRaffle = useCreateRaffle();
  const drawRaffle = useDrawRaffle();
  const { data: entries } = useRaffleEntries(selectedRaffle);

  const tabs: { key: Tab; label: string }[] = [
    { key: 'members', label: 'CRM Members' },
    { key: 'rankings', label: 'Song Rankings' },
    { key: 'raffles', label: 'Raffles' },
  ];

  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">Listener CRM</h1>

      {/* Tab bar */}
      <div className="flex gap-1 mb-6 border-b">
        {tabs.map(t => (
          <button key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition -mb-px ${
              tab === t.key
                ? 'border-brand-600 text-brand-700'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Members Tab ─────────────────────────────────── */}
      {tab === 'members' && (
        <div>
          <div className="mb-4">
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search by name..."
              className="border border-gray-300 rounded-lg px-3 py-2 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
            {membersData && (
              <span className="text-sm text-gray-500 ml-3">{membersData.total} members</span>
            )}
          </div>
          {membersLoading ? (
            <p className="text-gray-400">Loading...</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-left text-gray-600">
                    <th className="px-3 py-2">Name</th>
                    <th className="px-3 py-2">PIN</th>
                    <th className="px-3 py-2">Taste Profile</th>
                    <th className="px-3 py-2 text-center">Ratings</th>
                    <th className="px-3 py-2 text-center">Favorites</th>
                    <th className="px-3 py-2">Phone</th>
                    <th className="px-3 py-2">Email</th>
                    <th className="px-3 py-2">Joined</th>
                    <th className="px-3 py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {membersData?.members.map(m => (
                    <tr key={m.id} className="border-b hover:bg-gray-50">
                      <td className="px-3 py-2 font-medium">{m.name}</td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-500">{m.pin}</td>
                      <td className="px-3 py-2">
                        {m.taste_profile && (
                          <span className="inline-flex items-center gap-1 bg-brand-50 text-brand-700 px-2 py-0.5 rounded-full text-xs font-medium">
                            {m.taste_profile.label}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-center">{m.ratings_count}</td>
                      <td className="px-3 py-2 text-center">{m.favorites_count}</td>
                      <td className="px-3 py-2 text-gray-500">{m.phone || '-'}</td>
                      <td className="px-3 py-2 text-gray-500">{m.email || '-'}</td>
                      <td className="px-3 py-2 text-gray-400 text-xs">
                        {m.created_at ? new Date(m.created_at).toLocaleDateString() : '-'}
                      </td>
                      <td className="px-3 py-2">
                        <button
                          onClick={() => { if (confirm(`Deactivate ${m.name}?`)) deactivateMember.mutate(m.id); }}
                          className="text-xs text-red-500 hover:text-red-700 transition"
                        >
                          Deactivate
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {membersData?.members.length === 0 && (
                <p className="text-center text-gray-400 py-8">No members found</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Song Rankings Tab ───────────────────────────── */}
      {tab === 'rankings' && (
        <div>
          <p className="text-sm text-gray-500 mb-4">Songs ranked by listener ratings — useful for playlist curation decisions.</p>
          {rankingsLoading ? (
            <p className="text-gray-400">Loading...</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-gray-50 text-left text-gray-600">
                    <th className="px-3 py-2">#</th>
                    <th className="px-3 py-2">Title</th>
                    <th className="px-3 py-2">Artist</th>
                    <th className="px-3 py-2 text-center">Avg Rating</th>
                    <th className="px-3 py-2 text-center">Total Ratings</th>
                    <th className="px-3 py-2 text-center">Favorites</th>
                  </tr>
                </thead>
                <tbody>
                  {rankings?.map((r, i) => (
                    <tr key={r.asset_id} className="border-b hover:bg-gray-50">
                      <td className="px-3 py-2 text-gray-400">{i + 1}</td>
                      <td className="px-3 py-2 font-medium">{r.title}</td>
                      <td className="px-3 py-2 text-gray-500">{r.artist || '-'}</td>
                      <td className="px-3 py-2 text-center">
                        <span className="text-yellow-500">{'★'.repeat(Math.round(r.avg_rating))}</span>
                        <span className="text-gray-300">{'★'.repeat(5 - Math.round(r.avg_rating))}</span>
                        <span className="text-xs text-gray-400 ml-1">({r.avg_rating})</span>
                      </td>
                      <td className="px-3 py-2 text-center">{r.total_ratings}</td>
                      <td className="px-3 py-2 text-center text-red-400">{r.favorite_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {rankings?.length === 0 && (
                <p className="text-center text-gray-400 py-8">No ratings yet</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Raffles Tab ─────────────────────────────────── */}
      {tab === 'raffles' && (
        <div className="space-y-6">
          {/* Create raffle form */}
          <div className="bg-gray-50 rounded-lg p-4">
            <h3 className="font-medium mb-3">Create Raffle</h3>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Title *</label>
                <input type="text" value={rfTitle} onChange={e => setRfTitle(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Prize</label>
                <input type="text" value={rfPrize} onChange={e => setRfPrize(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
              </div>
              <div className="col-span-2">
                <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                <input type="text" value={rfDesc} onChange={e => setRfDesc(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Start *</label>
                <input type="datetime-local" value={rfStart} onChange={e => setRfStart(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">End *</label>
                <input type="datetime-local" value={rfEnd} onChange={e => setRfEnd(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500" />
              </div>
            </div>
            <button
              onClick={() => {
                if (!rfTitle.trim() || !rfStart || !rfEnd) return;
                createRaffle.mutate({
                  title: rfTitle.trim(),
                  prize: rfPrize.trim() || undefined,
                  description: rfDesc.trim() || undefined,
                  starts_at: new Date(rfStart).toISOString(),
                  ends_at: new Date(rfEnd).toISOString(),
                }, {
                  onSuccess: () => { setRfTitle(''); setRfPrize(''); setRfDesc(''); setRfStart(''); setRfEnd(''); },
                });
              }}
              disabled={createRaffle.isPending}
              className="mt-3 bg-brand-600 hover:bg-brand-700 text-white px-6 py-2 rounded-lg text-sm font-medium transition disabled:opacity-50"
            >
              {createRaffle.isPending ? 'Creating...' : 'Create Raffle'}
            </button>
          </div>

          {/* Raffle list */}
          {rafflesLoading ? (
            <p className="text-gray-400">Loading...</p>
          ) : (
            <div className="space-y-3">
              {raffles?.map(r => (
                <div key={r.id} className="bg-white shadow rounded-lg p-4">
                  <div className="flex items-start justify-between">
                    <div>
                      <h4 className="font-medium">{r.title}</h4>
                      {r.prize && <p className="text-sm text-gray-500">Prize: {r.prize}</p>}
                      {r.description && <p className="text-sm text-gray-400">{r.description}</p>}
                      <div className="flex items-center gap-4 mt-1 text-xs text-gray-400">
                        <span>{new Date(r.starts_at).toLocaleString()} — {new Date(r.ends_at).toLocaleString()}</span>
                        <span>{r.entry_count} entries</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        r.status === 'open' ? 'bg-green-100 text-green-700' :
                        r.status === 'drawn' ? 'bg-purple-100 text-purple-700' :
                        'bg-gray-100 text-gray-600'
                      }`}>
                        {r.status}
                      </span>
                      {r.status === 'open' && (
                        <button
                          onClick={() => { if (confirm('Draw a winner?')) drawRaffle.mutate(r.id); }}
                          disabled={drawRaffle.isPending}
                          className="bg-purple-600 hover:bg-purple-700 text-white px-3 py-1 rounded text-xs font-medium transition disabled:opacity-50"
                        >
                          Draw Winner
                        </button>
                      )}
                      <button
                        onClick={() => setSelectedRaffle(selectedRaffle === r.id ? null : r.id)}
                        className="text-xs text-brand-600 hover:text-brand-700"
                      >
                        {selectedRaffle === r.id ? 'Hide' : 'Entries'}
                      </button>
                    </div>
                  </div>
                  {r.winner_name && (
                    <div className="mt-2 bg-purple-50 border border-purple-200 rounded px-3 py-2 text-sm text-purple-700">
                      Winner: <strong>{r.winner_name}</strong>
                    </div>
                  )}
                  {selectedRaffle === r.id && entries && (
                    <div className="mt-3 border-t pt-3">
                      <p className="text-xs text-gray-500 mb-2">{entries.length} entries</p>
                      <div className="space-y-1">
                        {entries.map(e => (
                          <div key={e.id} className="flex items-center justify-between text-sm bg-gray-50 rounded px-3 py-1.5">
                            <span>{e.member_name}</span>
                            <span className="text-xs text-gray-400">
                              {e.created_at ? new Date(e.created_at).toLocaleString() : ''}
                            </span>
                          </div>
                        ))}
                        {entries.length === 0 && <p className="text-gray-400 text-sm">No entries yet</p>}
                      </div>
                    </div>
                  )}
                </div>
              ))}
              {raffles?.length === 0 && (
                <p className="text-center text-gray-400 py-8">No raffles yet</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
