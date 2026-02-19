import { Link } from 'react-router-dom';
import { useReviewQueues } from '../../hooks/useReviews';

const STATUS_COLORS: Record<string, string> = {
  open: 'bg-blue-100 text-blue-700',
  in_progress: 'bg-yellow-100 text-yellow-700',
  completed: 'bg-green-100 text-green-700',
  cancelled: 'bg-gray-100 text-gray-500',
};

export default function ReviewQueues() {
  const { data, isLoading } = useReviewQueues();

  if (isLoading) return <div className="text-center py-10">Loading...</div>;

  const queues = data?.queues ?? [];

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Review Queues</h1>
        <p className="text-sm text-gray-500">Select assets on the Assets page to create a queue</p>
      </div>

      {queues.length === 0 ? (
        <div className="bg-white border border-gray-200 rounded-lg p-10 text-center text-gray-500">
          No review queues yet. Go to <Link to="/admin/assets" className="text-brand-600 hover:underline">Assets</Link> to select assets and create a review queue.
        </div>
      ) : (
        <div className="space-y-3">
          {queues.map((queue) => {
            const progress = queue.total_items > 0
              ? Math.round((queue.reviewed_items / queue.total_items) * 100)
              : 0;

            return (
              <Link
                key={queue.id}
                to={`/admin/reviews/${queue.id}`}
                className="block bg-white border border-gray-200 rounded-lg p-4 hover:border-brand-300 transition"
              >
                <div className="flex items-center justify-between mb-2">
                  <h3 className="font-semibold">{queue.name}</h3>
                  <span className={`text-xs px-2 py-0.5 rounded-full ${STATUS_COLORS[queue.status] ?? 'bg-gray-100 text-gray-500'}`}>
                    {queue.status.replace('_', ' ')}
                  </span>
                </div>
                {queue.description && (
                  <p className="text-sm text-gray-500 mb-2">{queue.description}</p>
                )}
                <div className="flex items-center gap-4 text-sm text-gray-500">
                  <span>{queue.reviewed_items} / {queue.total_items} reviewed</span>
                  <div className="flex-1 bg-gray-200 rounded-full h-2 max-w-xs">
                    <div
                      className="bg-brand-600 h-2 rounded-full transition-all"
                      style={{ width: `${progress}%` }}
                    />
                  </div>
                  <span className="text-xs">{progress}%</span>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
