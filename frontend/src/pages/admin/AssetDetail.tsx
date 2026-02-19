import { useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useAssetDetail, useAssetAudioUrl } from '../../hooks/useAssets';
import WaveformPlayer, { type WaveformPlayerHandle } from '../../components/audio/WaveformPlayer';
import SilenceDetectionPanel from '../../components/audio/SilenceDetectionPanel';
import PreviewControls from '../../components/audio/PreviewControls';
import AssetHistory from '../../components/review/AssetHistory';
import CommentBox from '../../components/review/CommentBox';

function formatDuration(seconds: number | null): string {
  if (!seconds) return '--';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function AssetDetail() {
  const { assetId } = useParams<{ assetId: string }>();
  const { data: asset, isLoading } = useAssetDetail(assetId);
  const { data: audioUrl } = useAssetAudioUrl(assetId);
  const waveformRef = useRef<WaveformPlayerHandle>(null);

  if (isLoading) return <div className="text-center py-10">Loading...</div>;
  if (!asset) return <div className="text-center py-10 text-red-500">Asset not found</div>;

  return (
    <div className="max-w-5xl mx-auto">
      <div className="flex items-center gap-3 mb-6">
        <Link to="/admin/assets" className="text-brand-600 hover:text-brand-800 text-sm">&larr; Back to Assets</Link>
        <h1 className="text-2xl font-bold">{asset.title}</h1>
      </div>

      {/* Metadata */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 mb-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
          <div>
            <span className="text-gray-500">Artist</span>
            <p className="font-medium">{asset.artist ?? '--'}</p>
          </div>
          <div>
            <span className="text-gray-500">Album</span>
            <p className="font-medium">{asset.album ?? '--'}</p>
          </div>
          <div>
            <span className="text-gray-500">Type</span>
            <p className="font-medium">{asset.asset_type}</p>
          </div>
          <div>
            <span className="text-gray-500">Duration</span>
            <p className="font-medium">{formatDuration(asset.duration)}</p>
          </div>
          <div>
            <span className="text-gray-500">Category</span>
            <p className="font-medium">{asset.category ?? '--'}</p>
          </div>
          <div>
            <span className="text-gray-500">ID</span>
            <p className="font-medium text-xs font-mono text-gray-400">{asset.id}</p>
          </div>
        </div>
      </div>

      {/* Waveform */}
      {audioUrl && (
        <div className="mb-4">
          <WaveformPlayer ref={waveformRef} url={audioUrl} />
        </div>
      )}

      {/* Preview Controls + Silence Detection side by side */}
      {audioUrl && assetId && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
          <PreviewControls waveformRef={waveformRef} duration={asset.duration} />
          <SilenceDetectionPanel assetId={assetId} waveformRef={waveformRef} />
        </div>
      )}

      {/* Comments & History */}
      {assetId && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
          <CommentBox assetId={assetId} />
          <AssetHistory assetId={assetId} />
        </div>
      )}
    </div>
  );
}
