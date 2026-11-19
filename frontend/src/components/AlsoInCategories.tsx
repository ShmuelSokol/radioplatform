import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getAssetCategories, setAssetExtraCategories } from '../api/categories';
import { useCategories } from '../hooks/useCategories';

/**
 * A song in several categories: its extra categories ("Also in"), which playlist steps
 * draw from as well as its main one, and the holiday lists it is on. Used on the Library
 * item page and in the control room's Song panel (see wiki/song-categories.md).
 */
export default function AlsoInCategories({ assetId, mainCategory, canEdit, compact = false, onSaved }: {
  assetId: string;
  mainCategory: string | null;
  canEdit: boolean;
  compact?: boolean;
  onSaved?: (text: string) => void;
}) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ['asset-categories', assetId], queryFn: () => getAssetCategories(assetId) });
  const { data: categories } = useCategories();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const extra = data?.extra_categories ?? [];
  const main = (mainCategory ?? '').toLowerCase();
  const choices = (categories ?? []).map(c => c.name)
    .filter(n => n.toLowerCase() !== main && !extra.includes(n.toLowerCase()));

  const save = async (next: string[], text: string) => {
    setBusy(true);
    setError(null);
    try {
      const res = await setAssetExtraCategories(assetId, next);
      qc.setQueryData(['asset-categories', assetId], res);
      qc.invalidateQueries({ queryKey: ['categories'] });
      onSaved?.(text);
    } catch {
      setError('Not saved. Try again.');
    } finally {
      setBusy(false);
    }
  };

  const chip = compact ? 'text-sm lg:text-xs px-2 py-1 lg:py-0.5' : 'text-sm px-2 py-0.5';
  return (
    <div data-testid="also-in">
      <span className={compact ? 'block text-xs font-bold mb-0.5' : 'text-gray-500'}>Also in</span>
      <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
        {extra.length === 0 && <span className="text-sm text-gray-600" data-testid="also-in-none">No other category</span>}
        {extra.map(c => (
          <span key={c} className={`inline-flex items-center gap-1 rounded border border-gray-400 bg-white ${chip}`} data-testid="also-in-chip">
            {c}
            {canEdit && (
              <button type="button" disabled={busy} aria-label={`Take out of ${c}`}
                onClick={() => save(extra.filter(x => x !== c), `No longer also in “${c}”`)}
                className="min-w-6 min-h-6 leading-none text-gray-700 hover:text-red-700">✕</button>
            )}
          </span>
        ))}
        {canEdit && choices.length > 0 && (
          <select value="" disabled={busy} data-testid="also-in-add" aria-label="Add a category"
            onChange={(e) => { const v = e.target.value; if (v) save([...extra, v], `Also in “${v}” now`); }}
            className={compact ? 'qn-field h-11 lg:h-7 px-1 text-base lg:text-xs' : 'border border-gray-300 rounded px-2 py-1 text-sm'}>
            <option value="">+ Add category</option>
            {choices.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        )}
      </div>
      <span className="block text-[11px] mt-0.5 text-gray-700">
        Playlist steps for any of its categories can play it. Its main category decides where it sits in the Library.
      </span>
      {error && <span className="block text-xs text-red-700 mt-0.5">{error}</span>}
      {(data?.holiday_lists ?? []).length > 0 && (
        <p className="text-xs mt-1" data-testid="also-in-holidays">
          On holiday lists:{' '}
          {data!.holiday_lists.map((h, i) => (
            <span key={h.holiday + (h.station_name ?? '')}>
              {i > 0 && ', '}
              <Link to={`/admin/holiday-music?holiday=${h.holiday}`} className="underline">{h.name}</Link>
              {h.station_name ? ` (${h.station_name} only)` : ''}{h.all_year ? ' (also all year)' : ' (only then)'}
            </span>
          ))}
        </p>
      )}
    </div>
  );
}
