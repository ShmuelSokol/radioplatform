import { memo, useEffect, useRef, useState } from 'react';
import { useAssets } from '../../../hooks/useAssets';
import { useAssetTypes } from '../../../hooks/useAssetTypes';
import { getAssetAudioUrl } from '../../../api/assets';
import { fmtDur, typeStyle, useDebounced } from './shared';
import type { CtlAsset, SendFn } from './shared';

interface Props {
  /** Label of the queue item we are inserting before, or null when not inserting. */
  insertLabel: string | null;
  onCancelInsert: () => void;
  onSend: SendFn;
  busyId: string | null;
  pinnedIds: Set<string>;
  onTogglePin: (a: CtlAsset) => void;
  searchRef: React.RefObject<HTMLInputElement>;
  disabled: boolean;
}

const PAGE = 50;

function LibraryPanel(p: Props) {
  const [search, setSearch] = useState('');
  const [type, setType] = useState('all');
  const [limit, setLimit] = useState(PAGE);
  const debounced = useDebounced(search.trim(), 250);
  const { data: typesData } = useAssetTypes();
  const types = ['all', ...((typesData ?? []) as Array<{ name: string }>).map(t => t.name)];

  useEffect(() => { setLimit(PAGE); }, [debounced, type]);

  const { data, isFetching, isError, isPlaceholderData } = useAssets({
    skip: 0,
    limit,
    search: debounced || undefined,
    asset_type: type === 'all' ? undefined : type,
  });
  const assets = data?.assets ?? [];
  const total = data?.total ?? 0;
  // Results on screen belong to an older search until the new ones arrive.
  const stale = isPlaceholderData || search.trim() !== debounced;

  // Off-air preview ("cue"): plays in this browser only, never on the station.
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const stopPreview = () => {
    audioRef.current?.pause();
    audioRef.current = null;
    setPreviewId(null);
  };
  useEffect(() => () => { audioRef.current?.pause(); }, []);
  const togglePreview = async (id: string) => {
    if (previewId === id) { stopPreview(); return; }
    stopPreview();
    setPreviewId(id);
    try {
      const url = await getAssetAudioUrl(id);
      const a = new Audio(url);
      audioRef.current = a;
      a.onended = () => setPreviewId(cur => (cur === id ? null : cur));
      a.onerror = () => setPreviewId(cur => (cur === id ? null : cur));
      await a.play();
    } catch {
      setPreviewId(cur => (cur === id ? null : cur));
    }
  };

  const inserting = p.insertLabel !== null;

  return (
    <section className="flex flex-col min-h-0 h-full bg-[#0a0a28]" aria-label="Library">
      {inserting && (
        <div className="shrink-0 flex items-center gap-2 px-3 py-2 bg-cyan-500/20 border-b-2 border-cyan-400 text-cyan-50 text-sm" data-testid="insert-banner">
          <span className="flex-1 min-w-0">
            <span className="block font-bold">Pick a song to insert</span>
            <span className="block truncate text-xs text-cyan-200">before “{p.insertLabel}”</span>
          </span>
          <button type="button" onClick={p.onCancelInsert}
            className="h-9 px-3 rounded-md bg-white/10 hover:bg-white/20 text-xs font-bold shrink-0">
            Done
          </button>
        </div>
      )}

      <div className="shrink-0 p-2 border-b border-[#2a2a5e] space-y-2">
        <div className="relative">
          <input
            ref={p.searchRef}
            type="search"
            inputMode="search"
            enterKeyHint="search"
            autoComplete="off"
            placeholder="Search songs, artists, albums"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-11 lg:h-10 pl-9 pr-9 rounded-md bg-[#14143c] border border-[#3a3a7e] text-white text-base lg:text-sm placeholder-gray-500 focus:outline-none focus:border-cyan-400"
          />
          <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500 pointer-events-none" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="7" /><path strokeLinecap="round" d="M21 21l-4.3-4.3" />
          </svg>
          {search && (
            <button type="button" onClick={() => { setSearch(''); p.searchRef.current?.focus(); }}
              aria-label="Clear search"
              className="absolute right-1 top-1/2 -translate-y-1/2 w-8 h-8 rounded text-gray-400 hover:text-white">✕</button>
          )}
        </div>
        <div className="flex gap-1.5 overflow-x-auto -mx-2 px-2 pb-0.5" role="tablist" aria-label="Type">
          {types.map(t => (
            <button key={t} type="button" role="tab" aria-selected={type === t} onClick={() => setType(t)}
              className={`h-8 px-3 rounded-full text-xs font-semibold whitespace-nowrap shrink-0 border
                ${type === t ? 'bg-cyan-500 border-cyan-400 text-black' : 'bg-white/5 border-white/10 text-gray-300 hover:bg-white/10'}`}>
              {t === 'all' ? 'All' : t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain" data-testid="library-scroll">
        <div className="px-3 py-1 text-[11px] text-gray-500">
          {isError ? 'Could not load the library.' : `${total.toLocaleString()} ${total === 1 ? 'item' : 'items'}${isFetching ? ' · searching…' : ''}`}
        </div>
        <div className={stale ? 'opacity-40 pointer-events-none' : ''} aria-busy={stale}>
        {assets.map(asset => {
          const busy = p.busyId === asset.id;
          const pinned = p.pinnedIds.has(asset.id);
          return (
            <div key={asset.id} data-testid="library-row"
              className="flex items-center gap-2 px-2 min-h-[56px] lg:min-h-[48px] border-b border-[#15153a] hover:bg-[#12123a]">
              <button type="button" onClick={() => togglePreview(asset.id)}
                title="Preview here only (not on air)" aria-label={previewId === asset.id ? 'Stop preview' : 'Preview'}
                className={`w-9 h-9 shrink-0 rounded-full flex items-center justify-center text-sm
                  ${previewId === asset.id ? 'bg-white text-black' : 'bg-white/10 text-gray-200 hover:bg-white/20'}`}>
                {previewId === asset.id ? '■' : '▶'}
              </button>
              <div className="flex-1 min-w-0 leading-tight">
                <div className="truncate text-[15px] lg:text-sm font-medium text-gray-100">{asset.title}</div>
                <div className="truncate text-xs text-gray-400">
                  {asset.artist ? `${asset.artist} · ` : ''}
                  <span className={typeStyle(asset.asset_type).text}>{asset.asset_type}</span>
                  {' · '}{fmtDur(asset.duration)}
                </div>
              </div>
              <button type="button" onClick={() => p.onTogglePin(asset)}
                title={pinned ? 'Remove from instant buttons' : 'Add to instant buttons'}
                aria-label={pinned ? 'Remove from instant buttons' : 'Add to instant buttons'}
                aria-pressed={pinned}
                className={`w-9 h-9 shrink-0 rounded-md text-lg ${pinned ? 'text-yellow-300' : 'text-gray-600 hover:text-gray-300'}`}>
                {pinned ? '★' : '☆'}
              </button>
              {inserting ? (
                <button type="button" disabled={busy || p.disabled} onClick={() => p.onSend(asset, 'insert')}
                  className="h-10 px-3 shrink-0 rounded-md bg-cyan-500 hover:bg-cyan-400 text-black text-sm font-bold disabled:opacity-50">
                  {busy ? '…' : 'Insert'}
                </button>
              ) : (
                <>
                  <button type="button" disabled={busy || p.disabled} onClick={() => p.onSend(asset, 'next')}
                    title="Play right after the current item"
                    className="h-10 px-3 shrink-0 rounded-md bg-amber-500/90 hover:bg-amber-400 text-black text-sm font-bold disabled:opacity-50">
                    {busy ? '…' : 'Next'}
                  </button>
                  <button type="button" disabled={busy || p.disabled} onClick={() => p.onSend(asset, 'end')}
                    title="Add to the end of the queue" aria-label="Add to the end of the queue"
                    className="h-10 px-3 shrink-0 rounded-md bg-white/10 hover:bg-white/20 text-gray-100 text-sm font-semibold disabled:opacity-50">
                    + End
                  </button>
                </>
              )}
            </div>
          );
        })}
        </div>
        {!isFetching && assets.length === 0 && !isError && (
          <div className="text-center text-gray-500 py-10 px-4 text-sm">
            {debounced ? `Nothing matches “${debounced}”.` : 'The library is empty.'}
          </div>
        )}
        {assets.length < total && (
          <button type="button" onClick={() => setLimit(l => l + PAGE)} disabled={isFetching}
            className="w-full h-11 text-sm text-cyan-300 hover:bg-white/5 disabled:opacity-50">
            {isFetching ? 'Loading…' : `Show more (${(total - assets.length).toLocaleString()} more)`}
          </button>
        )}
      </div>
    </section>
  );
}

export default memo(LibraryPanel);
