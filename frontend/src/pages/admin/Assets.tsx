import { useState, useMemo, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAssets, useDeleteAsset } from '../../hooks/useAssets';
import { useCreateReviewQueue } from '../../hooks/useReviews';
import { downloadAsset, getAssetAudioUrl } from '../../api/assets';
import Spinner from '../../components/Spinner';

const EXPORT_FORMATS = ['original', 'mp3', 'wav', 'flac', 'ogg', 'aac'] as const;

function formatDuration(seconds: number | null): string {
  if (!seconds) return '--';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function DownloadButton({ assetId, title }: { assetId: string; title: string }) {
  const [open, setOpen] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDownload = async (format: string) => {
    setOpen(false);
    setDownloading(true);
    setError(null);
    try {
      await downloadAsset(assetId, title, format);
    } catch (err: any) {
      const msg = err?.response?.data?.detail ?? err?.message ?? 'Download failed';
      setError(typeof msg === 'string' ? msg : 'Download failed');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <span className="relative inline-block">
      {error && (
        <span className="text-red-500 text-xs mr-1" title={error}>Error</span>
      )}
      <button
        onClick={() => { setOpen(!open); setError(null); }}
        disabled={downloading}
        className="text-blue-600 hover:text-blue-800 text-sm disabled:opacity-50"
      >
        {downloading ? <><Spinner className="mr-1" />Processing...</> : 'Download'}
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-1 w-32 bg-white border border-gray-200 rounded shadow-lg py-1">
            {EXPORT_FORMATS.map((fmt) => (
              <button
                key={fmt}
                onClick={() => handleDownload(fmt)}
                className="block w-full text-left px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100"
              >
                {fmt === 'original' ? 'Original' : fmt.toUpperCase()}
              </button>
            ))}
          </div>
        </>
      )}
    </span>
  );
}

function PlayButton({ assetId, title, audioRef, playingId, setPlayingId }: {
  assetId: string;
  title: string;
  audioRef: React.RefObject<HTMLAudioElement | null>;
  playingId: string | null;
  setPlayingId: (id: string | null) => void;
}) {
  const [loading, setLoading] = useState(false);
  const isPlaying = playingId === assetId;

  const handlePlay = async () => {
    if (!audioRef.current) return;

    if (isPlaying) {
      audioRef.current.pause();
      setPlayingId(null);
      return;
    }

    setLoading(true);
    try {
      const url = await getAssetAudioUrl(assetId);
      audioRef.current.src = url;
      audioRef.current.onended = () => setPlayingId(null);
      await audioRef.current.play();
      setPlayingId(assetId);
    } catch {
      setPlayingId(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <button
      onClick={handlePlay}
      disabled={loading}
      className={`text-sm ${isPlaying ? 'text-green-600 hover:text-green-800 font-medium' : 'text-emerald-600 hover:text-emerald-800'} disabled:opacity-50`}
      title={isPlaying ? `Stop ${title}` : `Play ${title}`}
    >
      {loading ? '...' : isPlaying ? 'Stop' : 'Play'}
    </button>
  );
}

interface Filters {
  title: string;
  artist: string;
  album: string;
  category: string;
  asset_type: string;
  durationMin: string;
  durationMax: string;
}

const EMPTY_FILTERS: Filters = {
  title: '',
  artist: '',
  album: '',
  category: '',
  asset_type: '',
  durationMin: '',
  durationMax: '',
};

export default function Assets() {
  const { data, isLoading } = useAssets();
  const deleteMutation = useDeleteAsset();
  const createQueueMutation = useCreateReviewQueue();
  const navigate = useNavigate();
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const setFilter = (key: keyof Filters, value: string) =>
    setFilters((prev) => ({ ...prev, [key]: value }));

  const hasFilters = Object.values(filters).some((v) => v !== '');

  const filtered = useMemo(() => {
    const assets = data?.assets ?? [];
    return assets.filter((a) => {
      if (filters.title && !a.title.toLowerCase().includes(filters.title.toLowerCase())) return false;
      if (filters.artist && !(a.artist ?? '').toLowerCase().includes(filters.artist.toLowerCase())) return false;
      if (filters.album && !(a.album ?? '').toLowerCase().includes(filters.album.toLowerCase())) return false;
      if (filters.category && !(a.category ?? '').toLowerCase().includes(filters.category.toLowerCase())) return false;
      if (filters.asset_type && !a.asset_type.toLowerCase().includes(filters.asset_type.toLowerCase())) return false;
      const min = filters.durationMin !== '' ? Number(filters.durationMin) * 60 : null;
      const max = filters.durationMax !== '' ? Number(filters.durationMax) * 60 : null;
      if (min !== null && (a.duration ?? 0) < min) return false;
      if (max !== null && (a.duration ?? 0) > max) return false;
      return true;
    });
  }, [data, filters]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === filtered.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filtered.map((a) => a.id)));
    }
  };

  const handleCreateQueue = () => {
    const ids = Array.from(selected);
    createQueueMutation.mutate(
      { name: `Review Queue - ${new Date().toLocaleDateString()}`, assetIds: ids },
      {
        onSuccess: (queue) => {
          setSelected(new Set());
          navigate(`/admin/reviews/${queue.id}`);
        },
      }
    );
  };

  if (isLoading) return <div className="text-center py-10">Loading...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Assets</h1>
        <Link
          to="/admin/assets/upload"
          className="bg-brand-600 hover:bg-brand-700 text-white px-4 py-2 rounded transition"
        >
          Upload Asset
        </Link>
      </div>

      {/* Filter bar */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 mb-4">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Title</label>
            <input
              type="text"
              value={filters.title}
              onChange={(e) => setFilter('title', e.target.value)}
              placeholder="Filter..."
              className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Artist</label>
            <input
              type="text"
              value={filters.artist}
              onChange={(e) => setFilter('artist', e.target.value)}
              placeholder="Filter..."
              className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Album</label>
            <input
              type="text"
              value={filters.album}
              onChange={(e) => setFilter('album', e.target.value)}
              placeholder="Filter..."
              className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Category</label>
            <input
              type="text"
              value={filters.category}
              onChange={(e) => setFilter('category', e.target.value)}
              placeholder="Filter..."
              className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Type</label>
            <input
              type="text"
              value={filters.asset_type}
              onChange={(e) => setFilter('asset_type', e.target.value)}
              placeholder="Filter..."
              className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Min (min)</label>
            <input
              type="number"
              min={0}
              value={filters.durationMin}
              onChange={(e) => setFilter('durationMin', e.target.value)}
              placeholder="0"
              className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Max (min)</label>
            <input
              type="number"
              min={0}
              value={filters.durationMax}
              onChange={(e) => setFilter('durationMax', e.target.value)}
              placeholder="..."
              className="w-full border border-gray-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
          </div>
        </div>
        {hasFilters && (
          <div className="mt-3 flex items-center gap-3">
            <span className="text-xs text-gray-500">{filtered.length} of {data?.assets.length ?? 0} assets</span>
            <button
              onClick={() => setFilters(EMPTY_FILTERS)}
              className="text-xs text-red-500 hover:text-red-700"
            >
              Clear filters
            </button>
          </div>
        )}
      </div>

      {/* Batch action bar */}
      {selected.size > 0 && (
        <div className="bg-brand-50 border border-brand-200 rounded-lg p-3 mb-4 flex items-center justify-between">
          <span className="text-sm font-medium text-brand-700">
            {selected.size} selected
          </span>
          <button
            onClick={handleCreateQueue}
            disabled={createQueueMutation.isPending}
            className="bg-brand-600 hover:bg-brand-700 text-white px-4 py-1.5 rounded text-sm transition disabled:opacity-50"
          >
            {createQueueMutation.isPending ? <><Spinner className="mr-2" />Processing...</> : 'Create Review Queue'}
          </button>
        </div>
      )}

      <audio ref={audioRef} hidden />
      <div className="bg-white shadow rounded-lg overflow-x-auto">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="px-3 py-3 text-left">
                <input
                  type="checkbox"
                  checked={filtered.length > 0 && selected.size === filtered.length}
                  onChange={toggleAll}
                  className="rounded border-gray-300"
                />
              </th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Title</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Artist</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Album</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Category</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
              <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Duration</th>
              <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {filtered.map((asset) => (
              <tr key={asset.id} className={selected.has(asset.id) ? 'bg-brand-50' : ''}>
                <td className="px-3 py-4">
                  <input
                    type="checkbox"
                    checked={selected.has(asset.id)}
                    onChange={() => toggleSelect(asset.id)}
                    className="rounded border-gray-300"
                  />
                </td>
                <td className="px-6 py-4 whitespace-nowrap font-medium">{asset.title}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{asset.artist ?? '--'}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{asset.album ?? '--'}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{asset.category ?? '--'}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{asset.asset_type}</td>
                <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{formatDuration(asset.duration)}</td>
                <td className="px-6 py-4 whitespace-nowrap text-right space-x-3">
                  <PlayButton assetId={asset.id} title={asset.title} audioRef={audioRef} playingId={playingId} setPlayingId={setPlayingId} />
                  <Link to={`/admin/assets/${asset.id}`} className="text-brand-600 hover:text-brand-800 text-sm">View</Link>
                  <DownloadButton assetId={asset.id} title={asset.title} />
                  <button
                    onClick={() => deleteMutation.mutate(asset.id)}
                    className="text-red-600 hover:text-red-800 text-sm"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={8} className="px-6 py-10 text-center text-gray-500">
                  {hasFilters ? 'No assets match the current filters' : 'No assets uploaded yet'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
