import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useReviewQueue, useQueueItems, useUpdateReviewItem } from '../../hooks/useReviews';
import { useAssetAudioUrl } from '../../hooks/useAssets';
import ErrorBoundary from '../../components/ErrorBoundary';
import Spinner from '../../components/Spinner';
import AssetHistory from '../../components/review/AssetHistory';
import CommentBox from '../../components/review/CommentBox';

// Lazy-load WaveformPlayer to isolate wavesurfer.js errors
import { lazy, Suspense } from 'react';
const WaveformPlayer = lazy(() => import('../../components/audio/WaveformPlayer'));
type WaveformPlayerHandle = import('../../components/audio/WaveformPlayer').WaveformPlayerHandle;

const STATUS_COLORS: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-600',
  in_review: 'bg-yellow-100 text-yellow-700',
  approved: 'bg-green-100 text-green-700',
  rejected: 'bg-red-100 text-red-700',
  flagged: 'bg-orange-100 text-orange-700',
  skipped: 'bg-blue-100 text-blue-700',
};

function formatDuration(seconds: number | null): string {
  if (!seconds) return '--';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function ReviewFlow() {
  const { queueId } = useParams<{ queueId: string }>();
  const { data: queue } = useReviewQueue(queueId);
  const { data: itemsData, refetch: refetchItems } = useQueueItems(queueId);
  const updateMutation = useUpdateReviewItem();
  const [currentIndex, setCurrentIndex] = useState(0);
  const [notes, setNotes] = useState('');
  const waveformRef = useRef<WaveformPlayerHandle>(null);

  const items = itemsData?.items ?? [];
  const currentItem = items[currentIndex];
  const currentAsset = currentItem?.asset;
  const { data: audioUrl, isLoading: audioUrlLoading } = useAssetAudioUrl(currentAsset?.id);

  const advanceToNext = useCallback(() => {
    if (currentIndex < items.length - 1) {
      setCurrentIndex((i) => i + 1);
      setNotes('');
    }
  }, [currentIndex, items.length]);

  const handleAction = useCallback(
    (status: string) => {
      if (!currentItem) return;
      updateMutation.mutate(
        {
          itemId: currentItem.id,
          data: { status, notes: notes || undefined, version: currentItem.version },
        },
        {
          onSuccess: () => {
            refetchItems();
            advanceToNext();
          },
        }
      );
    },
    [currentItem, notes, updateMutation, refetchItems, advanceToNext]
  );

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Don't trigger when typing in inputs
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      switch (e.key.toLowerCase()) {
        case 'a':
          handleAction('approved');
          break;
        case 'r':
          handleAction('rejected');
          break;
        case 'f':
          handleAction('flagged');
          break;
        case 's':
          handleAction('skipped');
          break;
        case ' ':
          e.preventDefault();
          waveformRef.current?.getWaveSurfer()?.playPause();
          break;
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [handleAction]);

  if (!queue) return <div className="text-center py-10">Loading...</div>;

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <Link to="/admin/reviews" className="text-brand-600 hover:text-brand-800 text-sm">&larr; Queues</Link>
          <h1 className="text-xl font-bold">{queue.name}</h1>
        </div>
        <div className="text-sm text-gray-500">
          Reviewing {currentIndex + 1} of {items.length}
        </div>
      </div>

      {/* Progress bar */}
      <div className="bg-gray-200 rounded-full h-2 mb-6">
        <div
          className="bg-brand-600 h-2 rounded-full transition-all"
          style={{ width: `${items.length > 0 ? ((currentIndex + 1) / items.length) * 100 : 0}%` }}
        />
      </div>

      {currentItem && currentAsset ? (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Main content */}
          <div className="lg:col-span-2 space-y-4">
            {/* Asset metadata */}
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                <div>
                  <span className="text-gray-500">Title</span>
                  <p className="font-medium">{currentAsset.title}</p>
                </div>
                <div>
                  <span className="text-gray-500">Artist</span>
                  <p className="font-medium">{currentAsset.artist ?? '--'}</p>
                </div>
                <div>
                  <span className="text-gray-500">Duration</span>
                  <p className="font-medium">{formatDuration(currentAsset.duration)}</p>
                </div>
                <div>
                  <span className="text-gray-500">Status</span>
                  <span className={`inline-block text-xs px-2 py-0.5 rounded-full ${STATUS_COLORS[currentItem.status] ?? ''}`}>
                    {currentItem.status}
                  </span>
                </div>
              </div>
            </div>

            {/* Waveform */}
            {audioUrlLoading ? (
              <div className="bg-white border border-gray-200 rounded-lg p-4 text-center text-gray-400">
                <Spinner className="mr-2" />Loading audio...
              </div>
            ) : audioUrl ? (
              <ErrorBoundary key={currentAsset.id} fallback={<div className="bg-white border border-gray-200 rounded-lg p-4 text-center text-gray-500">Waveform failed to load — try refreshing the page</div>}>
                <Suspense fallback={<div className="bg-white border border-gray-200 rounded-lg p-4 text-center text-gray-400">Loading waveform...</div>}>
                  <WaveformPlayer ref={waveformRef} url={audioUrl} />
                </Suspense>
              </ErrorBoundary>
            ) : (
              <div className="bg-white border border-gray-200 rounded-lg p-4 text-center text-amber-600 text-sm">
                No audio file available for this asset
              </div>
            )}

            {/* Notes */}
            <div className="bg-white border border-gray-200 rounded-lg p-4">
              <label className="block text-sm font-medium text-gray-700 mb-2">Notes</label>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Add notes about this asset..."
                className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
                rows={3}
              />
            </div>

            {/* Action buttons */}
            <div className="flex items-center gap-3">
              <button
                onClick={() => handleAction('approved')}
                disabled={updateMutation.isPending}
                className="bg-green-600 hover:bg-green-700 text-white px-6 py-2 rounded text-sm transition disabled:opacity-50"
              >
                {updateMutation.isPending ? <><Spinner className="mr-2" />Processing...</> : 'Approve (A)'}
              </button>
              <button
                onClick={() => handleAction('rejected')}
                disabled={updateMutation.isPending}
                className="bg-red-600 hover:bg-red-700 text-white px-6 py-2 rounded text-sm transition disabled:opacity-50"
              >
                {updateMutation.isPending ? <><Spinner className="mr-2" />Processing...</> : 'Reject (R)'}
              </button>
              <button
                onClick={() => handleAction('flagged')}
                disabled={updateMutation.isPending}
                className="bg-orange-500 hover:bg-orange-600 text-white px-6 py-2 rounded text-sm transition disabled:opacity-50"
              >
                {updateMutation.isPending ? <><Spinner className="mr-2" />Processing...</> : 'Flag (F)'}
              </button>
              <button
                onClick={() => handleAction('skipped')}
                disabled={updateMutation.isPending}
                className="bg-blue-500 hover:bg-blue-600 text-white px-6 py-2 rounded text-sm transition disabled:opacity-50"
              >
                {updateMutation.isPending ? <><Spinner className="mr-2" />Processing...</> : 'Skip (S)'}
              </button>
              <span className="text-xs text-gray-400 ml-auto">Space = Play/Pause</span>
            </div>

            {/* Navigation */}
            <div className="flex items-center gap-3">
              <button
                onClick={() => { setCurrentIndex((i) => Math.max(0, i - 1)); setNotes(''); }}
                disabled={currentIndex === 0}
                className="text-sm text-gray-500 hover:text-gray-700 disabled:opacity-30"
              >
                &larr; Previous
              </button>
              <div className="flex-1" />
              <button
                onClick={() => advanceToNext()}
                disabled={currentIndex >= items.length - 1}
                className="text-sm text-gray-500 hover:text-gray-700 disabled:opacity-30"
              >
                Next &rarr;
              </button>
            </div>
          </div>

          {/* Sidebar */}
          <div className="space-y-4">
            {/* Item list */}
            <div className="bg-white border border-gray-200 rounded-lg p-3 max-h-64 overflow-y-auto">
              <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">Queue Items</h3>
              {items.map((item, i) => (
                <button
                  key={item.id}
                  onClick={() => { setCurrentIndex(i); setNotes(''); }}
                  className={`block w-full text-left px-2 py-1.5 text-sm rounded mb-0.5 ${
                    i === currentIndex ? 'bg-brand-100 text-brand-700' : 'hover:bg-gray-50'
                  }`}
                >
                  <span className="font-mono text-xs text-gray-400 mr-2">#{item.position}</span>
                  <span className="truncate">{item.asset?.title ?? 'Unknown'}</span>
                  <span className={`float-right text-xs px-1.5 py-0.5 rounded-full ${STATUS_COLORS[item.status] ?? ''}`}>
                    {item.status}
                  </span>
                </button>
              ))}
            </div>

            {/* Comments */}
            <CommentBox assetId={currentAsset.id} />

            {/* History */}
            <AssetHistory assetId={currentAsset.id} />
          </div>
        </div>
      ) : (
        <div className="bg-white border border-gray-200 rounded-lg p-10 text-center text-gray-500">
          {items.length === 0 ? 'This queue has no items.' : 'All items have been reviewed!'}
        </div>
      )}
    </div>
  );
}
