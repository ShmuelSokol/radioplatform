import { useState, useMemo, useRef, useEffect, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAssets, useDeleteAsset, useBulkSetCategory } from '../../hooks/useAssets';
import AssetCategoryBadge from '../../components/AssetCategoryBadge';
import AssetSponsorBadge from '../../components/AssetSponsorBadge';
import { useCreateReviewQueue } from '../../hooks/useReviews';
import { useCategories } from '../../hooks/useCategories';
import { downloadAsset, getAssetAudioUrl } from '../../api/assets';
import type { Asset } from '../../types';
import Spinner from '../../components/Spinner';

const EXPORT_FORMATS = ['original', 'mp3', 'wav', 'flac', 'ogg', 'aac'] as const;
const PAGE_SIZE_OPTIONS = [50, 100, 200] as const;

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
  const bulkCategoryMutation = useBulkSetCategory();
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const createQueueMutation = useCreateReviewQueue();
  const navigate = useNavigate();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // Per-field search state
  const [titleSearch, setTitleSearch] = useState('');
  const [artistSearch, setArtistSearch] = useState('');
  const [albumSearch, setAlbumSearch] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [durationMin, setDurationMin] = useState('');
  const [durationMax, setDurationMax] = useState('');

  // Quick search (autocomplete across all fields)
  const [quickSearch, setQuickSearch] = useState('');
  const [showSuggestions, setShowSuggestions] = useState(false);
  const searchRef = useRef<HTMLDivElement>(null);

  // Pagination
  const [pageSize, setPageSize] = useState<number>(50);
  const [page, setPage] = useState(0);

  // Bulk category picker
  const [showBulkCategory, setShowBulkCategory] = useState(false);
  // Bulk download picker
  const [showBulkDownload, setShowBulkDownload] = useState(false);
  const [bulkDownloading, setBulkDownloading] = useState(false);

  // Debounce per-field searches
  const debouncedTitle = useDebounce(titleSearch, 300);
  const debouncedArtist = useDebounce(artistSearch, 300);
  const debouncedAlbum = useDebounce(albumSearch, 300);
  const debouncedDurMin = useDebounce(durationMin, 300);
  const debouncedDurMax = useDebounce(durationMax, 300);

  // Reset page when filters change
  useEffect(() => { setPage(0); }, [debouncedTitle, debouncedArtist, debouncedAlbum, categoryFilter, typeFilter, debouncedDurMin, debouncedDurMax]);

  // Server-side filtered query
  const durMinNum = debouncedDurMin ? parseFloat(debouncedDurMin) : undefined;
  const durMaxNum = debouncedDurMax ? parseFloat(debouncedDurMax) : undefined;

  const { data, isLoading, isFetching } = useAssets({
    skip: page * pageSize,
    limit: pageSize,
    title_search: debouncedTitle || undefined,
    artist_search: debouncedArtist || undefined,
    album_search: debouncedAlbum || undefined,
    asset_type: typeFilter || undefined,
    category: categoryFilter || undefined,
    duration_min: durMinNum,
    duration_max: durMaxNum,
  });

  // Suggestion query — fetch top 8 matches for quick search autocomplete
  const suggestSearch = useDebounce(quickSearch, 150);
  const { data: suggestData } = useAssets({
    skip: 0,
    limit: 8,
    search: suggestSearch.length >= 2 ? suggestSearch : undefined,
    asset_type: typeFilter || undefined,
    category: categoryFilter || undefined,
    enabled: suggestSearch.length >= 2 && showSuggestions,
  });
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
  const totalPages = Math.ceil(total / pageSize);
  const hasFilters = titleSearch !== '' || artistSearch !== '' || albumSearch !== '' || categoryFilter !== '' || typeFilter !== '' || durationMin !== '' || durationMax !== '';

  const clearFilters = useCallback(() => {
    setTitleSearch('');
    setArtistSearch('');
    setAlbumSearch('');
    setCategoryFilter('');
    setTypeFilter('');
    setDurationMin('');
    setDurationMax('');
    setQuickSearch('');
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

  const handleBulkCategory = (cat: string) => {
    const ids = Array.from(selected);
    bulkCategoryMutation.mutate(
      { assetIds: ids, category: cat },
      { onSuccess: () => { setSelected(new Set()); setShowBulkCategory(false); } },
    );
  };

  const handleBulkDownload = async (format: string) => {
    setShowBulkDownload(false);
    setBulkDownloading(true);
    const ids = Array.from(selected);
    for (const id of ids) {
      const asset = assets.find((a) => a.id === id);
      if (asset) {
        try {
          await downloadAsset(id, asset.title, format);
        } catch { /* skip failed */ }
        await new Promise((r) => setTimeout(r, 500));
      }
    }
    setBulkDownloading(false);
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
        {/* Row 1: Per-field search */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-3">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Title</label>
            <input
              type="text"
              value={titleSearch}
              onChange={(e) => setTitleSearch(e.target.value)}
              placeholder="Search by title..."
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Artist</label>
            <input
              type="text"
              value={artistSearch}
              onChange={(e) => setArtistSearch(e.target.value)}
              placeholder="Search by artist..."
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Album</label>
            <input
              type="text"
              value={albumSearch}
              onChange={(e) => setAlbumSearch(e.target.value)}
              placeholder="Search by album..."
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
        </div>
        {/* Row 2: Category, Type, Duration, Quick search */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
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
          <div>
            <label className="block text-xs text-gray-500 mb-1">Min Duration (s)</label>
            <input
              type="number"
              min={0}
              step={1}
              value={durationMin}
              onChange={(e) => setDurationMin(e.target.value)}
              placeholder="e.g. 60"
              className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Max Duration (s)</label>
            <input
              type="number"
              min={0}
              step={1}
              value={durationMax}
              onChange={(e) => setDurationMax(e.target.value)}
              placeholder="e.g. 300"
              className="w-full border border-gray-300 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
          </div>
          {/* Quick search with autocomplete */}
          <div ref={searchRef} className="relative">
            <label className="block text-xs text-gray-500 mb-1">Quick Search</label>
            <input
              type="text"
              value={quickSearch}
              onChange={(e) => { setQuickSearch(e.target.value); setShowSuggestions(true); }}
              onFocus={() => { if (quickSearch.length >= 2) setShowSuggestions(true); }}
              placeholder="Autocomplete..."
              className="w-full border border-gray-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
            {showSuggestions && quickSearch.length >= 2 && suggestions.length > 0 && (
              <div className="absolute z-30 left-0 right-0 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg max-h-64 overflow-y-auto">
                {suggestions.map((s) => (
                  <button
                    key={s.id}
                    onClick={() => {
                      setTitleSearch(s.title);
                      setQuickSearch('');
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
        </div>
        {/* Result count & clear */}
        <div className="mt-3 flex items-center gap-3">
          <span className="text-xs text-gray-500">
            {total} asset{total !== 1 ? 's' : ''} found
            {hasFilters ? '' : ' total'}
          </span>
          {isFetching && <span className="text-xs text-gray-400">...</span>}
          {hasFilters && (
            <button onClick={clearFilters} className="text-xs text-red-500 hover:text-red-700">
              Clear filters
            </button>
          )}
        </div>
      </div>

      {/* Batch action bar */}
      {selected.size > 0 && (
        <div className="bg-brand-50 border border-brand-200 rounded-lg p-3 mb-4 flex items-center gap-3 flex-wrap">
          <span className="text-sm font-medium text-brand-700">
            {selected.size} selected
          </span>
          <div className="flex gap-2 ml-auto flex-wrap">
            {/* Set Category */}
            <div className="relative">
              <button
                onClick={() => { setShowBulkCategory(!showBulkCategory); setShowBulkDownload(false); }}
                disabled={bulkCategoryMutation.isPending}
                className="bg-gray-600 hover:bg-gray-700 text-white px-3 py-1.5 rounded text-sm transition disabled:opacity-50"
              >
                {bulkCategoryMutation.isPending ? <><Spinner className="mr-1" />Updating...</> : 'Set Category'}
              </button>
              {showBulkCategory && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setShowBulkCategory(false)} />
                  <div className="absolute right-0 z-20 mt-1 w-40 bg-white border border-gray-200 rounded shadow-lg py-1 max-h-48 overflow-y-auto">
                    {categories?.map((cat) => (
                      <button
                        key={cat.id}
                        onClick={() => handleBulkCategory(cat.name)}
                        className="block w-full text-left px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100"
                      >
                        {cat.name}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* Download */}
            <div className="relative">
              <button
                onClick={() => { setShowBulkDownload(!showBulkDownload); setShowBulkCategory(false); }}
                disabled={bulkDownloading}
                className="bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded text-sm transition disabled:opacity-50"
              >
                {bulkDownloading ? <><Spinner className="mr-1" />Downloading...</> : 'Download'}
              </button>
              {showBulkDownload && (
                <>
                  <div className="fixed inset-0 z-10" onClick={() => setShowBulkDownload(false)} />
                  <div className="absolute right-0 z-20 mt-1 w-32 bg-white border border-gray-200 rounded shadow-lg py-1">
                    {(['mp3', 'wav', 'flac', 'ogg', 'aac'] as const).map((fmt) => (
                      <button
                        key={fmt}
                        onClick={() => handleBulkDownload(fmt)}
                        className="block w-full text-left px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100"
                      >
                        {fmt.toUpperCase()}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* Create Review Queue */}
            <button
              onClick={handleCreateQueue}
              disabled={createQueueMutation.isPending}
              className="bg-brand-600 hover:bg-brand-700 text-white px-3 py-1.5 rounded text-sm transition disabled:opacity-50"
            >
              {createQueueMutation.isPending ? <><Spinner className="mr-1" />Processing...</> : 'Create Review Queue'}
            </button>
          </div>
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
                    title={hasRealAudio(asset.file_path) ? '' : 'No audio file — cannot select'}
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

      {/* Pagination + Rows per page */}
      <div className="flex items-center justify-between mt-4 px-2">
        <div className="flex items-center gap-3">
          <span className="text-sm text-gray-500">
            {total > 0 ? `${page * pageSize + 1}–${Math.min((page + 1) * pageSize, total)} of ${total}` : '0 results'}
          </span>
          <div className="flex items-center gap-1">
            <label className="text-xs text-gray-500">Rows:</label>
            <select
              value={pageSize}
              onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}
              className="border border-gray-300 rounded px-1 py-0.5 text-sm bg-white focus:outline-none"
            >
              {PAGE_SIZE_OPTIONS.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </div>
        </div>
        {totalPages > 1 && (
          <div className="flex gap-2">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Previous
            </button>
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
        )}
      </div>
    </div>
  );
}
