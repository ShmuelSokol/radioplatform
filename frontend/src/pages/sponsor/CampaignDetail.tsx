import { useParams } from 'react-router-dom';
import { useCampaign } from '../../hooks/useCampaigns';
import Spinner from '../../components/Spinner';
import { CampaignComments, CampaignDrafts, CampaignFacts, StatusBadge } from '../../components/campaigns/CampaignParts';

export default function CampaignDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: campaign, isLoading } = useCampaign(id!);

  if (isLoading) return <div className="text-center py-10"><Spinner /></div>;
  if (!campaign) return <div className="text-center py-10 text-gray-400">Campaign not found</div>;

  return (
    <div className="space-y-6">
      {/* Campaign Header */}
      <div className="bg-white rounded-xl shadow p-6">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-2xl font-bold text-gray-800">{campaign.name}</h1>
            {campaign.description && (
              <p className="text-gray-500 mt-1">{campaign.description}</p>
            )}
          </div>
          <StatusBadge status={campaign.status} />
        </div>
        <CampaignFacts c={campaign} className="mt-4 text-sm text-gray-500" />
      </div>

      <CampaignDrafts campaignId={campaign.id} canChange />
      <CampaignComments campaignId={campaign.id} canChange />
    </div>
  );
}
