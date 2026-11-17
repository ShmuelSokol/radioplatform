import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useCampaign, useUpdateCampaignStatus } from '../../hooks/useCampaigns';
import { useAuth } from '../../hooks/useAuth';
import { canChange } from '../../auth/access';
import { CAMPAIGN_STATUSES } from '../../api/campaigns';
import Spinner from '../../components/Spinner';
import {
  CampaignComments, CampaignDrafts, CampaignFacts, StatusBadge, statusLabel,
} from '../../components/campaigns/CampaignParts';

/** One sponsorship campaign for staff: drafts, comments, and (managers) its status. */
export default function AdminCampaignDetail() {
  const { id = '' } = useParams<{ id: string }>();
  const { user } = useAuth();
  const mayChange = canChange(user?.role, '/admin/campaigns');
  const { data: campaign, isLoading, isError } = useCampaign(id);
  const setStatus = useUpdateCampaignStatus();
  const [status, setStatusChoice] = useState('');
  const [saved, setSaved] = useState(false);

  const current = campaign?.status;
  useEffect(() => { if (current) setStatusChoice(current); }, [current]);

  if (isLoading) return <div className="text-center py-10"><Spinner /></div>;
  if (isError || !campaign) {
    return (
      <div className="w-full text-center py-10 text-gray-500">
        <p>Campaign not found.</p>
        <Link to="/admin/campaigns" className="inline-flex items-center min-h-[44px] text-indigo-600 underline">Back to campaigns</Link>
      </div>
    );
  }

  const saveStatus = async () => {
    setSaved(false);
    await setStatus.mutateAsync({ id: campaign.id, status });
    setSaved(true);
  };

  return (
    <div className="w-full space-y-4">
      <Link
        to={campaign.sponsor_id ? `/admin/campaigns?sponsor=${campaign.sponsor_id}` : '/admin/campaigns'}
        className="inline-flex items-center min-h-[44px] text-sm text-indigo-600 hover:text-indigo-800"
      >
        &larr; All campaigns{campaign.sponsor_name ? ` for ${campaign.sponsor_name}` : ''}
      </Link>

      <div className="bg-white rounded-xl shadow p-4 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-2xl font-bold text-gray-800 break-words">{campaign.name}</h1>
            <p className="text-indigo-700">{campaign.sponsor_name || 'Unnamed sponsor'}</p>
            {campaign.description && <p className="text-gray-500 mt-1 whitespace-pre-wrap">{campaign.description}</p>}
          </div>
          <StatusBadge status={campaign.status} />
        </div>
        <CampaignFacts c={campaign} className="mt-3 text-sm text-gray-500" />

        {mayChange && (
          <div className="mt-4 pt-4 border-t flex flex-wrap items-end gap-3">
            <label className="flex-1 min-w-[10rem] sm:flex-none sm:w-56">
              <span className="block text-sm font-medium text-gray-700 mb-1">Status</span>
              <select
                aria-label="Campaign status"
                value={status}
                onChange={e => { setStatusChoice(e.target.value); setSaved(false); }}
                className="w-full min-h-[44px] border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500"
              >
                {CAMPAIGN_STATUSES.map(s => <option key={s} value={s}>{statusLabel(s)}</option>)}
              </select>
            </label>
            <button
              type="button"
              onClick={saveStatus}
              disabled={setStatus.isPending || status === campaign.status}
              className="min-h-[44px] px-4 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition text-sm disabled:opacity-50"
            >
              {setStatus.isPending ? <><Spinner className="mr-2" />Saving...</> : 'Save Status'}
            </button>
            {saved && !setStatus.isPending && <span className="text-sm text-green-700">Status saved.</span>}
            {setStatus.isError && <span role="alert" className="text-sm text-red-700">The status was not saved.</span>}
          </div>
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-2 items-start">
        <CampaignDrafts campaignId={campaign.id} canChange={mayChange} />
        <CampaignComments campaignId={campaign.id} canChange={mayChange} />
      </div>
    </div>
  );
}
