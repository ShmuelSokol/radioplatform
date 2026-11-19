import { memo, useEffect, useRef, useState } from 'react';
import { useAssets } from '../../../hooks/useAssets';
import { useAssetTypes } from '../../../hooks/useAssetTypes';
import { fmtDur, useDebounced, useMediaQuery } from './shared';
import { ColumnHeads, useColumnWidths } from './columns';
import type { ColumnDef } from './columns';

// The library list's columns: drag a heading's right border, double-click it to fit
// (same as the queue above it; columns.tsx)
const COLS: ColumnDef[] = [
  { id: 'artist', label: 'Artist', width: 200, min: 60, shrink: true },
  { id: 'title', label: 'Title', width: 420, min: 120, shrink: true },
  { id: 'type', label: 'Song type', width: 96, min: 40, title: 'The song\'s category (its song type), or its kind when it has none' },
  { id: 'len', label: 'Length', width: 52, min: 36, align: 'right' },
];
import { useMayChange } from '../../../hooks/useMayChange';
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
  /** Item being pre-listened to (PFL), heard in this browser only. */
  pflId: string | null;
  onPfl: (id: string) => void;
}

const PAGE = 50;

function LibraryPanel(p: Props) {
  // A viewer searches and pre-listens; sending to the queue and the instant buttons are off
  const readOnly = !useMayChange();
  const isDesktop = useMediaQuery('(min-width: 1024px)');
  const listRef = useRef<HTMLDivElement>(null);
  const colApi = useColumnWidths('kbr-cr-library-cols', COLS);
  const col = (id: string) => colApi.cell(COLS.find(c => c.id === id) as ColumnDef);
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
    // Dated time and weather recordings (type spot or jingle) would air the wrong hour or day
    hide_generated: true,
  });
  const assets = data?.assets ?? [];
  const total = data?.total ?? 0;
  // Results on screen belong to an older search until the new ones arrive.
  const stale = isPlaceholderData || search.trim() !== debounced;

  const previewId = p.pflId;
  const togglePreview = p.onPfl;

  const inserting = p.insertLabel !== null;

  return (
    <section className="flex flex-col min-h-0 h-full qn-log" aria-label="Library">
      {inserting && (
        <div className="shrink-0 flex items-center gap-2 px-3 py-1.5 bg-[#00ffff] text-black text-sm" data-testid="insert-banner">
          <span className="flex-1 min-w-0">
            <span className="block font-bold">Pick a song to insert</span>
            <span className="block truncate text-xs font-normal">before “{p.insertLabel}”</span>
          </span>
          <button type="button" onClick={p.onCancelInsert}
            className="qn-btn h-8 px-3 text-xs shrink-0">
            Done
          </button>
        </div>
      )}

      <div className="cr-lib-head shrink-0 p-1.5 bg-[#c0c0c0] text-black font-normal border-b border-[#808080] flex flex-col lg:flex-row lg:items-center gap-1.5">
        <div className="cr-lib-search relative lg:w-72 xl:w-96 shrink-0">
          <input
            ref={p.searchRef}
            type="search"
            inputMode="search"
            enterKeyHint="search"
            autoComplete="off"
            placeholder="Search songs, artists, albums"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="qn-field w-full h-10 lg:h-7 pl-8 pr-8 text-base lg:text-sm placeholder-gray-500"
          />
          <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500 pointer-events-none" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="7" /><path strokeLinecap="round" d="M21 21l-4.3-4.3" />
          </svg>
          {search && (
            <button type="button" onClick={() => { setSearch(''); p.searchRef.current?.focus(); }}
              aria-label="Clear search"
              className="cr-tap absolute right-1 top-1/2 -translate-y-1/2 w-7 h-7 text-gray-600 hover:text-black">✕</button>
          )}
        </div>
        <div className="flex gap-1 overflow-x-auto min-w-0 pb-0.5" role="tablist" aria-label="Type">
          {types.map(t => (
            <button key={t} type="button" role="tab" aria-selected={type === t} onClick={() => setType(t)}
              className="qn-btn h-8 lg:h-6 px-2.5 text-xs whitespace-nowrap shrink-0">
              {t === 'all' ? 'All' : t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </div>
      </div>

      <div ref={listRef} className="flex-1 min-h-0 overflow-y-auto overscroll-contain" data-testid="library-scroll">
        <div className="px-2 py-0.5 text-[11px] font-normal text-gray-400">
          {isError ? 'Could not load the library.' : `${total.toLocaleString()} ${total === 1 ? 'item' : 'items'}${isFetching ? ' · searching…' : ''}`}
        </div>
        {assets.length > 0 && isDesktop && (
          <div role="row" className="flex h-[18px] items-stretch qn-log-head text-[11px] leading-[18px] px-1 gap-1.5 sticky top-0 z-10 select-none" data-testid="library-head">
            <span className="w-6 shrink-0 !shadow-none" aria-hidden="true" />
            <span className="flex-1 min-w-0 flex !shadow-none !p-0">
              <ColumnHeads cols={COLS} api={colApi} rowsRef={listRef} name="library" />
            </span>
            <span className="shrink-0 !shadow-none" style={{ width: 150 }} />
          </div>
        )}
        <div className={stale ? 'opacity-40 pointer-events-none' : ''} aria-busy={stale}>
        {assets.map(asset => {
          const busy = p.busyId === asset.id;
          const pinned = p.pinnedIds.has(asset.id);
          return (
            <div key={asset.id} data-testid="library-row"
              className="qn-row flex items-center gap-2 lg:gap-1.5 px-2 lg:px-1 min-h-[56px] lg:min-h-[28px] border-b border-[#222] lg:border-0">
              <button type="button" onClick={() => togglePreview(asset.id)}
                title="Pre-listen (PFL): only you hear it, not on air" aria-label={previewId === asset.id ? 'Stop pre-listen' : 'Pre-listen'}
                data-testid="library-pfl" aria-pressed={previewId === asset.id}
                className={`qn-btn w-9 h-9 lg:w-6 lg:h-6 shrink-0 flex items-center justify-center text-sm lg:text-[10px] ${previewId === asset.id ? '!bg-[#2bff4a]' : ''}`}>
                {previewId === asset.id ? '■' : '▶'}
              </button>
              <div className="flex-1 min-w-0 leading-tight lg:hidden">
                <div className="truncate text-[15px]">{asset.title}</div>
                <div className="truncate text-xs font-normal opacity-80">
                  {asset.artist ? `${asset.artist} · ` : ''}{asset.category || asset.asset_type}{' · '}{fmtDur(asset.duration)}
                </div>
              </div>
              {isDesktop && (
                <div className="flex flex-1 min-w-0 items-center text-[13px] leading-tight">
                  <span className="px-1 truncate" data-col="artist" style={col('artist')}>{asset.artist}</span>
                  <span className="px-1 truncate" data-col="title" style={col('title')}>{asset.title}</span>
                  <span className="px-1 truncate" data-col="type" style={col('type')} title={asset.asset_type}>{asset.category || asset.asset_type}</span>
                  <span className="px-1 text-right tabular-nums whitespace-nowrap overflow-hidden" data-col="len" style={col('len')}>{fmtDur(asset.duration)}</span>
                </div>
              )}
              {!readOnly && <button type="button" onClick={() => p.onTogglePin(asset)}
                title={pinned ? 'Remove from instant buttons' : 'Add to instant buttons'}
                aria-label={pinned ? 'Remove from instant buttons' : 'Add to instant buttons'}
                aria-pressed={pinned}
                className={`cr-tap w-9 h-9 lg:w-6 lg:h-6 shrink-0 text-lg lg:text-sm ${pinned ? 'text-[var(--qn-yellow)]' : 'text-gray-600 hover:text-gray-300'}`}>
                {pinned ? '★' : '☆'}
              </button>}
              {inserting ? (
                <button type="button" disabled={busy || p.disabled || readOnly} onClick={() => p.onSend(asset, 'insert')}
                  className="qn-btn h-10 lg:h-6 px-3 shrink-0 text-sm lg:text-xs">
                  {busy ? '…' : 'Insert'}
                </button>
              ) : (
                <>
                  <button type="button" disabled={busy || p.disabled || readOnly} onClick={() => p.onSend(asset, 'next')}
                    title="Play right after the current item"
                    className="qn-btn h-10 lg:h-6 px-3 shrink-0 text-sm lg:text-xs">
                    {busy ? '…' : 'Next'}
                  </button>
                  <button type="button" disabled={busy || p.disabled || readOnly} onClick={() => p.onSend(asset, 'end')}
                    title="Add to the end of the queue" aria-label="Add to the end of the queue"
                    className="qn-btn h-10 lg:h-6 px-3 shrink-0 text-sm lg:text-xs">
                    + End
                  </button>
                </>
              )}
            </div>
          );
        })}
        </div>
        {!isFetching && assets.length === 0 && !isError && (
          <div className="text-center py-10 px-4 text-sm font-normal">
            {debounced ? `Nothing matches “${debounced}”.` : 'The library is empty.'}
          </div>
        )}
        {assets.length < total && (
          <button type="button" onClick={() => setLimit(l => l + PAGE)} disabled={isFetching}
            className="w-full h-10 text-sm font-normal text-[#00ffff] hover:bg-[#111] disabled:opacity-50">
            {isFetching ? 'Loading…' : `Show more (${(total - assets.length).toLocaleString()} more)`}
          </button>
        )}
      </div>
    </section>
  );
}

export default memo(LibraryPanel);
