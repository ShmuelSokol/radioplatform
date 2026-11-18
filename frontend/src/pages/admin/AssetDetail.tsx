import { useRef, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useAssetDetail, useAssetAudioUrl, useUpdateAsset } from '../../hooks/useAssets';
import WaveformPlayer, { type WaveformPlayerHandle } from '../../components/audio/WaveformPlayer';
import SilenceDetectionPanel from '../../components/audio/SilenceDetectionPanel';
import PreviewControls from '../../components/audio/PreviewControls';
import EnhancePanel from '../../components/audio/EnhancePanel';
import AudienceDetectionPanel from '../../components/audio/AudienceDetectionPanel';
import AssetHistory from '../../components/review/AssetHistory';
import CommentBox from '../../components/review/CommentBox';
import type { SilenceRegion } from '../../types';
import { useAuth } from '../../hooks/useAuth';
import { veryQuietBy } from '../../lib/loudness';
import { isManager } from '../../auth/access';

function formatDuration(seconds: number | null): string {
  if (!seconds) return '--';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export default function AssetDetail() {
  const { assetId } = useParams<{ assetId: string }>();
  const queryClient = useQueryClient();
  const { data: asset, isLoading } = useAssetDetail(assetId);
  const { data: audioUrl } = useAssetAudioUrl(assetId);
  const updateMutation = useUpdateAsset();
  // Managers and admins change an item here (name, artist, album, vote, release date);
  // DJs and viewers see the same facts as plain text
  const { user } = useAuth();
  const canEdit = isManager(user?.role);
  const [saveError, setSaveError] = useState('');
  const saveText = (field: 'title' | 'artist' | 'album', label: string, was: string | null) => (e: React.FocusEvent<HTMLInputElement>) => {
    const value = e.target.value.trim();
    if (!assetId || value === (was ?? '')) return;
    if (field === 'title' && !value) { e.target.value = was ?? ''; setSaveError('The title cannot be empty.'); return; }
    setSaveError('');
    const input = e.target;
    updateMutation.mutate({ id: assetId, data: { [field]: value } }, {
      onError: () => { input.value = was ?? ''; setSaveError(`The ${label} was not saved. Try again.`); },
    });
  };
  const waveformRef = useRef<WaveformPlayerHandle>(null);
  const [silenceRegions, setSilenceRegions] = useState<SilenceRegion[]>([]);
  const [waveformKey, setWaveformKey] = useState(0);

  const handleRegionUpdate = useCallback((updated: { start: number; end: number; id: string }) => {
    const match = updated.id.match(/^silence-(\d+)$/);
    if (!match) return;
    const idx = parseInt(match[1], 10);
    setSilenceRegions((prev) => {
      const next = [...prev];
      if (next[idx]) {
        next[idx] = { start: updated.start, end: updated.end, duration: updated.end - updated.start };
      }
      return next;
    });
  }, []);

  const handleTrimComplete = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['asset-audio-url', assetId] });
    queryClient.invalidateQueries({ queryKey: ['asset', assetId] });
    setWaveformKey((k) => k + 1);
  }, [queryClient, assetId]);

  if (isLoading) return <div className="text-center py-10">Loading...</div>;
  if (!asset) return <div className="text-center py-10 text-red-500">Asset not found</div>;

  return (
    <div className="w-full">
      <div className="flex items-center gap-3 mb-6">
        <Link to="/admin/assets" className="text-brand-600 hover:text-brand-800 text-sm">&larr; Back to Assets</Link>
        <h1 className="text-2xl font-bold break-words min-w-0">{asset.title}</h1>
      </div>
      {veryQuietBy(asset.metadata_extra) !== null && (
        <p className="text-sm mb-3 px-3 py-2 rounded bg-amber-50 border border-amber-300 text-amber-900" data-testid="very-quiet">
          Very quiet recording. Even with the largest boost it plays about {veryQuietBy(asset.metadata_extra)} LU quieter than the rest of the station. The file has not been changed.
        </p>
      )}
      {saveError && <p role="alert" className="text-sm text-red-600 mb-3" data-testid="asset-save-error">{saveError}</p>}

      {/* Metadata */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 mb-4">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
          {canEdit && (
            <label className="col-span-2 sm:col-span-4 block">
              <span className="text-gray-500">Title</span>
              <input type="text" key={`title-${asset.title}`} defaultValue={asset.title} maxLength={500} dir="auto"
                onBlur={saveText('title', 'title', asset.title)}
                onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
                data-testid="asset-title"
                className="block w-full border border-gray-300 rounded px-2 py-1 text-sm mt-0.5 focus:outline-none focus:ring-1 focus:ring-brand-500" />
            </label>
          )}
          {canEdit ? (
            <label className="block">
              <span className="text-gray-500">Artist</span>
              <input type="text" key={`artist-${asset.artist ?? ''}`} defaultValue={asset.artist ?? ''} maxLength={255} dir="auto"
                onBlur={saveText('artist', 'artist', asset.artist)}
                onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
                data-testid="asset-artist"
                className="block w-full border border-gray-300 rounded px-2 py-1 text-sm mt-0.5 focus:outline-none focus:ring-1 focus:ring-brand-500" />
            </label>
          ) : (
            <div>
              <span className="text-gray-500">Artist</span>
              <p className="font-medium">{asset.artist || '--'}</p>
            </div>
          )}
          {canEdit ? (
            <label className="block">
              <span className="text-gray-500">Album</span>
              <input type="text" key={`album-${asset.album ?? ''}`} defaultValue={asset.album ?? ''} maxLength={255} dir="auto"
                onBlur={saveText('album', 'album', asset.album)}
                onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
                data-testid="asset-album"
                className="block w-full border border-gray-300 rounded px-2 py-1 text-sm mt-0.5 focus:outline-none focus:ring-1 focus:ring-brand-500" />
            </label>
          ) : (
            <div>
              <span className="text-gray-500">Album</span>
              <p className="font-medium">{asset.album || '--'}</p>
            </div>
          )}
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
            <span className="text-gray-500">Vote</span>
            {!canEdit ? <p className="font-medium">{asset.vote ?? 0}</p> : <input
              type="number"
              min={0}
              max={100}
              key={asset.vote ?? 0}
              defaultValue={asset.vote ?? 0}
              onBlur={(e) => {
                const vote = Math.max(0, Math.min(100, Math.round(Number(e.target.value) || 0)));
                if (!assetId || vote === (asset.vote ?? 0)) return;
                updateMutation.mutate({ id: assetId, data: { vote } });
              }}
              title="Playlist generation can be told to draw only songs with at least a certain vote."
              className="block w-24 border border-gray-300 rounded px-2 py-1 text-sm mt-0.5 focus:outline-none focus:ring-1 focus:ring-brand-500"
            />}
          </div>
          <div>
            <span className="text-gray-500">Last played</span>
            <p className="font-medium">
              {asset.last_played_at ? new Date(asset.last_played_at).toLocaleString() : 'Never'}
            </p>
          </div>
          <div>
            <span className="text-gray-500">Release Date</span>
            {!canEdit ? <p className="font-medium">{asset.release_date ?? '--'}</p> : <input
              type="date"
              value={asset.release_date ?? ''}
              onChange={(e) => {
                if (!assetId) return;
                updateMutation.mutate({ id: assetId, data: { release_date: e.target.value || null } });
              }}
              className="block w-full border border-gray-300 rounded px-2 py-1 text-sm mt-0.5 focus:outline-none focus:ring-1 focus:ring-brand-500"
            />}
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
          <WaveformPlayer
            key={waveformKey}
            ref={waveformRef}
            url={audioUrl}
            silenceRegions={silenceRegions}
            onRegionUpdate={handleRegionUpdate}
          />
        </div>
      )}

      {/* Preview Controls + Silence Detection side by side */}
      {audioUrl && assetId && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
          <PreviewControls waveformRef={waveformRef} duration={asset.duration} />
          <SilenceDetectionPanel
            assetId={assetId}
            waveformRef={waveformRef}
            onRegionsDetected={setSilenceRegions}
            onTrimComplete={handleTrimComplete}
          />
        </div>
      )}

      {/* Audio Enhancement + Audience Detection side by side */}
      {assetId && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
          <EnhancePanel assetId={assetId} onEnhanceComplete={handleTrimComplete} />
          <AudienceDetectionPanel assetId={assetId} waveformRef={waveformRef} />
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
