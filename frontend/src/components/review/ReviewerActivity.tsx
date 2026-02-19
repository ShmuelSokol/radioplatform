import { useQueueActivity } from '../../hooks/useReviews';

interface ReviewerActivityProps {
  queueId: string;
}

export default function ReviewerActivity({ queueId }: ReviewerActivityProps) {
  const { data: actions, isLoading } = useQueueActivity(queueId);

  if (isLoading) return <div className="text-xs text-gray-400">Loading activity...</div>;

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-3">
      <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">Recent Activity</h3>
      {!actions || actions.length === 0 ? (
        <p className="text-xs text-gray-400">No activity yet</p>
      ) : (
        <div className="space-y-1.5 max-h-48 overflow-y-auto">
          {actions.map((action) => (
            <div key={action.id} className="text-xs text-gray-600 flex items-start gap-1.5">
              <span className="font-medium text-gray-800">{action.user_email ?? 'User'}</span>
              <span className="capitalize">{action.action_type}</span>
              {action.comment && <span className="text-gray-400 truncate">- {action.comment}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
