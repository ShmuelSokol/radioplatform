import { useAssetHistory } from '../../hooks/useReviews';

const ACTION_COLORS: Record<string, string> = {
  approved: 'text-green-600 bg-green-50 border-green-200',
  rejected: 'text-red-600 bg-red-50 border-red-200',
  flagged: 'text-orange-600 bg-orange-50 border-orange-200',
  comment: 'text-blue-600 bg-blue-50 border-blue-200',
  trim: 'text-purple-600 bg-purple-50 border-purple-200',
  reassign: 'text-gray-600 bg-gray-50 border-gray-200',
};

interface AssetHistoryProps {
  assetId: string;
}

export default function AssetHistory({ assetId }: AssetHistoryProps) {
  const { data: actions, isLoading } = useAssetHistory(assetId);

  if (isLoading) return <div className="text-xs text-gray-400">Loading history...</div>;

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3">
      <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">History</h3>
      {!actions || actions.length === 0 ? (
        <p className="text-xs text-gray-400">No activity yet</p>
      ) : (
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {actions.map((action) => {
            const colors = ACTION_COLORS[action.action_type] ?? 'text-gray-600 bg-gray-50 border-gray-200';
            return (
              <div key={action.id} className={`border rounded p-2 text-xs ${colors}`}>
                <div className="flex items-center justify-between mb-0.5">
                  <span className="font-medium capitalize">{action.action_type}</span>
                  <span className="text-gray-400">
                    {action.created_at ? new Date(action.created_at).toLocaleString() : ''}
                  </span>
                </div>
                {action.user_email && (
                  <p className="text-gray-500">{action.user_email}</p>
                )}
                {action.comment && (
                  <p className="mt-1">{action.comment}</p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
