import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useCampaigns, useCreateCampaign } from '../../hooks/useCampaigns';
import Spinner from '../../components/Spinner';

const statusColors: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-700',
  in_review: 'bg-yellow-100 text-yellow-700',
  approved: 'bg-green-100 text-green-700',
  active: 'bg-blue-100 text-blue-700',
  paused: 'bg-orange-100 text-orange-700',
  completed: 'bg-purple-100 text-purple-700',
};

export default function SponsorCampaigns() {
  const { data: campaigns, isLoading } = useCampaigns();
  const createMutation = useCreateCampaign();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    await createMutation.mutateAsync({ name, description: description || undefined });
    setName('');
    setDescription('');
    setShowForm(false);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-800">Campaigns</h1>
        <button
          onClick={() => setShowForm(!showForm)}
          className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm font-medium"
        >
          {showForm ? 'Cancel' : 'New Campaign'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} className="bg-white rounded-xl shadow p-6 space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Campaign Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500"
              rows={3}
            />
          </div>
          <button
            type="submit"
            disabled={createMutation.isPending}
            className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm disabled:opacity-50"
          >
            {createMutation.isPending ? <><Spinner className="mr-2" />Creating...</> : 'Create Campaign'}
          </button>
        </form>
      )}

      {isLoading ? (
        <div className="text-center py-10"><Spinner /></div>
      ) : !campaigns?.length ? (
        <div className="bg-white rounded-xl shadow p-8 text-center text-gray-400">
          No campaigns yet. Create your first campaign to get started.
        </div>
      ) : (
        <div className="grid gap-4">
          {campaigns.map((c) => (
            <Link
              key={c.id}
              to={`/sponsor/campaigns/${c.id}`}
              className="bg-white rounded-xl shadow p-5 hover:shadow-md transition flex items-center justify-between"
            >
              <div>
                <h3 className="font-semibold text-gray-800">{c.name}</h3>
                {c.description && (
                  <p className="text-sm text-gray-500 mt-1 line-clamp-1">{c.description}</p>
                )}
                <div className="flex gap-3 mt-2 text-xs text-gray-400">
                  {c.start_date && <span>Start: {c.start_date}</span>}
                  {c.end_date && <span>End: {c.end_date}</span>}
                  {c.budget_cents != null && (
                    <span>Budget: ${(c.budget_cents / 100).toFixed(2)}</span>
                  )}
                </div>
              </div>
              <span className={`px-3 py-1 rounded-full text-xs font-medium ${statusColors[c.status] || 'bg-gray-100 text-gray-600'}`}>
                {c.status.replace('_', ' ')}
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
