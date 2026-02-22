import { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAssets, useDeleteAsset } from '../../hooks/useAssets';
import AssetCategoryBadge from '../../components/AssetCategoryBadge';
import AssetSponsorBadge from '../../components/AssetSponsorBadge';
import { useCreateReviewQueue } from '../../hooks/useReviews';
import { useCategories } from '../../hooks/useCategories';
import { downloadAsset, getAssetAudioUrl } from '../../api/assets';
import type { Asset } from '../../types';
import Spinner from '../../components/Spinner';

const EXPORT_FORMATS = ['original', 'mp3', 'wav', 'flac', 'ogg', 'aac'] as const;
const PAGE_SIZE = 50;

/** Real uploads have file_path starting with "assets/"; seed data uses music/, spots/, etc. */
function hasRealAudio(filePath: string): boolean {
  return filePath.startsWith('assets/') || filePath.startsWith('http');
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return '--';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return '--';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState(value);
  useEffect(() => {
    const handler = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(handler);
  }, [value, delay]);
  return debouncedValue;
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

const ASSET_TYPES = ['music', 'shiur', 'spot', 'jingle', 'zmanim'] as const;

export default function Assets() {
  const { data: categories } = useCategories();
  const deleteMutation = useDeleteAsset();
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const createQueueMutation = useCreateReviewQueue();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Search & filter state
  const [searchInput, setSearchInput] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [page, setPage] = useState(0);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  // Debounce search for API calls
  const debouncedSearch = useDebounce(searchInput, 300);

  // Reset page when filters change
  useEffect(() => { setPage(0); }, [debouncedSearch, categoryFilter, typeFilter]);

  // Server-side filtered query
  const { data, isLoading, isFetching } = useAssets(
    page * PAGE_SIZE,
    PAGE_SIZE,
    debouncedSearch || undefined,
    typeFilter || undefined,
    categoryFilter || undefined,
  );

  // Suggestion query — fetch top 8 matches for autocomplete while typing
  const suggestSearch = useDebounce(searchInput, 150);
  const { data: suggestData } = useAssets(
    0,
    8,
    suggestSearch.length >= 2 ? suggestSearch : undefined,
    typeFilter || undefined,
    categoryFilter || undefined,
    suggestSearch.length >= 2 && showSuggestions,
  );
  const suggestions: Asset[] = suggestData?.assets ?? [];

  // Close suggestions on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setShowSuggestions(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const assets: Asset[] = data?.assets ?? [];
  const total: number = data?.total ?? 0;
  const totalPages = Math.ceil(total / PAGE_SIZE);
  const hasFilters = searchInput !== '' || categoryFilter !== '' || typeFilter !== '';

  const clearFilters = useCallback(() => {
    setSearchInput('');
    setCategoryFilter('');
    setTypeFilter('');
    setPage(0);
  }, []);

  const realAssets = useMemo(() => assets.filter((a: Asset) => hasRealAudio(a.file_path)), [assets]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAll = () => {
    if (selected.size === realAssets.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(realAssets.map((a) => a.id)));
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

  if (isLoading && !data) return <div className="text-center py-10">Loading...</div>;

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Library</h1>
        <Link
          to="/admin/assets/upload"
          className="bg-brand-600 hover:bg-brand-700 text-white px-4 py-2 rounded transition"
        >
          Upload Asset
        </Link>
      </div>

      {/* Search & filter bar */}
      <div className="bg-white border border-gray-200 rounded-lg p-4 mb-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {/* Search with autocomplete */}
          <div ref={searchRef} className="relative sm:col-span-1">
            <label className="block text-xs text-gray-500 mb-1">Search</label>
            <input
              type="text"
              value={searchInput}
              onChange={(e) => { setSearchInput(e.target.value); setShowSuggestions(true); }}
              onFocus={() => { if (searchInput.length >= 2) setShowSuggestions(true); }}
              placeholder="Search title, artist, album..."
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
            {isFetching && (
              <span className="absolute right-2 top-7 text-gray-400 text-xs">...</span>
            )}
            {/* Autocomplete dropdown */}
            {showSuggestions && searchInput.length >= 2 && suggestions.length > 0 && (
              <div className="absolute z-30 left-0 right-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg max-h-64 overflow-y-auto">
                {suggestions.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => {
                      setSearchInput(s.title);
                      setShowSuggestions(false);
                    }}
                    className="w-full text-left px-3 py-2 hover:bg-gray-50 border-b border-gray-100 last:border-0"
                  >
                    <div className="text-sm font-medium truncate">{s.title}</div>
                    <div className="text-xs text-gray-500 truncate">
                      {s.artist ?? 'Unknown artist'} {s.album ? `\u00B7 ${s.album}` : ''}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
          {/* Category filter */}
          <div>
            <label className="block text-xs text-gray-500 mb-1">Category</label>
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500 bg-white"
            >
              <option value="">All</option>
              {categories?.map((cat) => (
                <option key={cat.id} value={cat.name}>{cat.name}</option>
              ))}
            </select>
          </div>
          {/* Type filter */}
          <div>
            <label className="block text-xs text-gray-500 mb-1">Type</label>
            <select
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500 bg-white"
            >
              <option value="">All</option>
              {ASSET_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </div>
        </div>
        {/* Result count & clear */}
        <div className="mt-3 flex items-center gap-3">
          <span className="text-xs text-gray-500">
            {total} asset{total !== 1 ? 's' : ''} found
            {hasFilters ? '' : ' total'}
          </span>
          {hasFilters && (
            <button onClick={clearFilters} className="text-xs text-red-500 hover:text-red-700">
              Clear filters
            </button>
          )}
        </div>
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
      <div className="bg-white shadow rounded-lg overflow-hidden">
        <table className="w-full table-fixed divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              <th className="w-8 px-2 py-2 text-left">
                <input
                  type="checkbox"
                  checked={realAssets.length > 0 && selected.size === realAssets.length}
                  onChange={toggleAll}
                  className="rounded border-gray-300"
                />
              </th>
              <th className="w-10 px-1 py-2 text-center text-xs font-medium text-gray-500 uppercase">Audio</th>
              <th className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase" style={{ width: '22%' }}>Title</th>
              <th className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase" style={{ width: '14%' }}>Artist</th>
              <th className="px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase" style={{ width: '12%' }}>Album</th>
              <th className="w-20 px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Category</th>
              <th className="w-16 px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Type</th>
              <th className="w-14 px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Dur.</th>
              <th className="w-20 px-2 py-2 text-left text-xs font-medium text-gray-500 uppercase">Added</th>
              <th className="px-2 py-2 text-right text-xs font-medium text-gray-500 uppercase" style={{ width: '14%' }}>Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {assets.map((asset) => (
              <tr key={asset.id} className={selected.has(asset.id) ? 'bg-brand-50' : ''}>
                <td className="px-2 py-2">
                  <input
                    type="checkbox"
                    checked={selected.has(asset.id)}
                    onChange={() => toggleSelect(asset.id)}
                    disabled={!hasRealAudio(asset.file_path)}
                    className="rounded border-gray-300 disabled:opacity-30"
                    title={hasRealAudio(asset.file_path) ? '' : 'No audio file — cannot add to review queue'}
                  />
                </td>
                <td className="px-1 py-2 text-center">
                  {hasRealAudio(asset.file_path) ? (
                    <span className="inline-block w-2 h-2 rounded-full bg-green-500" title="Has audio file" />
                  ) : (
                    <span className="inline-block w-2 h-2 rounded-full bg-gray-300" title="No audio file (seed data)" />
                  )}
                </td>
                <td className="px-2 py-2 font-medium text-sm truncate" title={asset.title}>{asset.title}</td>
                <td className="px-2 py-2 text-sm text-gray-500 truncate" title={asset.artist ?? ''}>{asset.artist ?? '--'}</td>
                <td className="px-2 py-2 text-sm text-gray-500 truncate" title={asset.album ?? ''}>{asset.album ?? '--'}</td>
                <td className="px-2 py-2 text-sm truncate">
                  <AssetCategoryBadge assetId={asset.id} category={asset.category} />
                </td>
                <td className="px-2 py-2 text-xs text-gray-500 truncate">
                  {asset.asset_type}
                  {asset.asset_type === 'spot' && (
                    <span className="ml-1">
                      <AssetSponsorBadge assetId={asset.id} sponsorId={asset.sponsor_id} sponsorName={asset.sponsor_name} />
                    </span>
                  )}
                </td>
                <td className="px-2 py-2 text-xs text-gray-500">{formatDuration(asset.duration)}</td>
                <td className="px-2 py-2 text-xs text-gray-500">{formatDate(asset.created_at)}</td>
                <td className="px-2 py-2 text-right space-x-2">
                  <PlayButton assetId={asset.id} title={asset.title} audioRef={audioRef} playingId={playingId} setPlayingId={setPlayingId} />
                  <Link to={`/admin/assets/${asset.id}`} className="text-brand-600 hover:text-brand-800 text-xs">View</Link>
                  <DownloadButton assetId={asset.id} title={asset.title} />
                  <button
                    onClick={() => {
                      setDeletingId(asset.id);
                      deleteMutation.mutate(asset.id, {
                        onSettled: () => setDeletingId(null),
                      });
                    }}
                    disabled={deletingId === asset.id}
                    className="text-red-600 hover:text-red-800 text-xs disabled:opacity-50"
                  >
                    {deletingId === asset.id ? '...' : 'Del'}
                  </button>
                </td>
              </tr>
            ))}
            {assets.length === 0 && (
              <tr>
                <td colSpan={10} className="px-4 py-10 text-center text-gray-500">
                  {hasFilters ? 'No assets match the current filters' : 'No assets uploaded yet'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4 px-2">
          <span className="text-sm text-gray-500">
            Showing {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Previous
            </button>
            {/* Page numbers — show up to 7 pages around current */}
            {Array.from({ length: Math.min(7, totalPages) }, (_, i) => {
              let p: number;
              if (totalPages <= 7) {
                p = i;
              } else if (page < 4) {
                p = i;
              } else if (page > totalPages - 5) {
                p = totalPages - 7 + i;
              } else {
                p = page - 3 + i;
              }
              return (
                <button
                  key={p}
                  onClick={() => setPage(p)}
                  className={`px-3 py-1 text-sm border rounded ${
                    p === page
                      ? 'bg-brand-600 text-white border-brand-600'
                      : 'border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  {p + 1}
                </button>
              );
            })}
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
