import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getStationBlock, setDoNotPlay, setStationBlock, updateAsset } from '../../../api/assets';
import { useAssetDetail } from '../../../hooks/useAssets';
import { useCategories } from '../../../hooks/useCategories';
import { useAssetTypes } from '../../../hooks/useAssetTypes';
import { assetLabel, isSponsorSpot } from './shared';
import AlsoInCategories from '../../../components/AlsoInCategories';
import type { CtlAsset } from './shared';

/** The song a quick-actions panel is open for, and where it was opened from. */
export interface SongTarget {
  asset: CtlAsset;
  onAir: boolean;
  /** Queue row source ("ad_slot" = a planned sponsorship play) */
  source?: string | null;
  /** Show the item's own options even for a sponsorship spot */
  itemOptions?: boolean;
  /** Known to be a sponsorship play (a row of the Sponsors list), whatever its source */
  sponsorship?: boolean;
  /** The queue row it was opened from */
  entryId?: string;
}

/** A sponsorship spot opens the Sponsor panel instead of the song options. */
export function isSponsorship(t: SongTarget): boolean {
  return !!t.sponsorship || isSponsorSpot(t.source, t.asset);
}

interface Props {
  target: SongTarget;
  stationId: string | null;
  /** The station whose control room this is, named on the "Not on this station" choice */
  stationName?: string | null;
  /** Managers and admins change things; everyone else only looks */
  canEdit: boolean;
  /** Opened from a queue row: where it plays from, and a way to see why it is there */
  why?: { short: string; open: () => void };
  onClose: () => void;
  onToast: (text: string, type: 'ok' | 'warn' | 'error') => void;
}

const DNP = 'do_not_play';
const isDnp = (c: string | null | undefined) => (c ?? '').trim().toLowerCase() === DNP;

function errText(e: unknown, fallback: string): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof d === 'string' ? d : fallback;
}

/**
 * Quick actions on one song, opened from the song on air or a queue row: change its
 * category, stop it from playing again (or allow it again), fix its details, or go
 * to its Library page. Two ways to stop a song, each with its own undo: "Not on this
 * station" keeps it off the station whose control room this is and no other; "Do not
 * play this song again" takes it off every station (the library is shared). Nothing
 * here touches the audio on air; both remove the song's waiting copies on the server.
 */
export default function SongPanel({ target, stationId, stationName, canEdit, why, onClose, onToast }: Props) {
  const qc = useQueryClient();
  const id = target.asset.id;
  const { data: full } = useAssetDetail(id);
  const { data: categories } = useCategories();
  const { data: types } = useAssetTypes();
  const [busy, setBusy] = useState<string | null>(null);

  const category = full ? full.category : target.asset.category ?? null;
  const blocked = isDnp(category);
  const here = stationName || 'this station';
  const { data: offHere } = useQuery({
    queryKey: ['asset-station-block', id, stationId],
    queryFn: () => getStationBlock(id, stationId as string),
    enabled: !!stationId,
  });
  const blockedHere = offHere === true;
  const [title, setTitle] = useState(target.asset.title);
  const [artist, setArtist] = useState(target.asset.artist ?? '');
  const [type, setType] = useState(target.asset.asset_type ?? 'music');
  const [vote, setVote] = useState('0');
  // Fill the form once the full record arrives (the queue row has no rating)
  const loadedId = full?.id;
  useEffect(() => {
    if (!full) return;
    setTitle(full.title);
    setArtist(full.artist ?? '');
    setType(full.asset_type);
    setVote(String(full.vote ?? 0));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadedId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose]);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['asset', id] });
    qc.invalidateQueries({ queryKey: ['assets'] });
    qc.invalidateQueries({ queryKey: ['asset-station-block', id] });
    if (stationId) {
      qc.invalidateQueries({ queryKey: ['queue', stationId] });
      qc.invalidateQueries({ queryKey: ['now-playing', stationId] });
    }
  };

  const run = async (what: string, fn: () => Promise<string>, failed: string) => {
    if (busy) return;
    setBusy(what);
    try {
      onToast(await fn(), 'ok');
      refresh();
    } catch (e) {
      onToast(errText(e, failed), 'error');
    } finally {
      setBusy(null);
    }
  };

  const changeCategory = (value: string) => run('category', async () => {
    await updateAsset(id, { category: value === '' ? null : value });
    return value === '' ? 'Category cleared' : `Category is now “${value}”`;
  }, 'The category was not changed. Try again.');

  const toggleDnp = () => run('dnp', async () => {
    const res = await setDoNotPlay(id, !blocked);
    if (!res.do_not_play) return `Allowed again${res.category ? `, back in “${res.category}”` : ''}`;
    const gone = res.removed_from_queue;
    const left = res.still_to_play;
    return `Will not play again on any station. ${gone === 0 ? 'No waiting copies in the queue' : `${gone} waiting ${gone === 1 ? 'copy' : 'copies'} removed`}`
      + (left > 0 ? `; ${left} already on air, loaded or timed will still play.` : '.');
  }, 'That did not go through. Try again.');

  const toggleHere = () => run('here', async () => {
    const res = await setStationBlock(id, stationId as string, !blockedHere);
    const name = res.station_name || here;
    if (!res.blocked) return `Allowed on ${name} again`;
    const gone = res.removed_from_queue;
    const left = res.still_to_play;
    return `Will not play on ${name}. Other stations are not changed. ${gone === 0 ? 'No waiting copies in this queue' : `${gone} waiting ${gone === 1 ? 'copy' : 'copies'} removed`}`
      + (left > 0 ? `; ${left} already on air, loaded or timed will still play.` : '.');
  }, 'That did not go through. Try again.');

  const voteNum = Number(vote);
  const voteOk = vote.trim() !== '' && Number.isInteger(voteNum) && voteNum >= 0 && voteNum <= 100;
  const changed = !!full && (title.trim() !== full.title || artist.trim() !== (full.artist ?? '')
    || type !== full.asset_type || voteNum !== (full.vote ?? 0));
  const save = () => run('save', async () => {
    await updateAsset(id, { title: title.trim(), artist: artist.trim() || null, asset_type: type, vote: voteNum });
    return 'Song details saved';
  }, 'The details were not saved. Try again.');

  const names = (categories ?? []).map(c => c.name);
  const options = category && !names.includes(category) ? [category, ...names] : names;
  const typeNames = (types ?? []).map(t => t.name);
  const typeOptions = typeNames.includes(type) ? typeNames : [type, ...typeNames];
  const label = 'block text-xs font-bold mb-0.5';
  const field = 'qn-field w-full h-11 lg:h-8 px-2 text-base lg:text-sm';

  return (
    <div className="fixed inset-0 z-50 flex items-end lg:items-center justify-center bg-black/60"
      onClick={onClose} data-testid="song-panel-backdrop">
      <div role="dialog" aria-modal="true" aria-label={`Song actions: ${target.asset.title}`} data-testid="song-panel"
        onClick={(e) => e.stopPropagation()}
        className="w-full lg:w-[440px] max-h-[92dvh] flex flex-col bg-[#c0c0c0] text-black border-2 border-white shadow-2xl select-text pb-[env(safe-area-inset-bottom)]">
        <div className="qn-title shrink-0 h-9 lg:h-7 flex items-center gap-2 pl-2 pr-1">
          <span className="font-bold text-sm truncate flex-1">Song</span>
          {target.onAir && <span className="text-[10px] font-bold bg-red-600 text-white px-1.5 py-px" data-testid="song-on-air">ON AIR</span>}
          <button type="button" onClick={onClose} aria-label="Close" className="qn-btn w-9 h-7 lg:w-6 lg:h-5 text-xs">✕</button>
        </div>

        <div className="min-h-0 overflow-y-auto overscroll-contain p-3 space-y-3">
          <div className="bg-black qn-sunken px-2 py-1.5">
            <div className="text-[var(--qn-digit)] font-bold text-base leading-tight break-words" data-testid="song-name">{assetLabel(full ?? target.asset)}</div>
            <div className={`text-xs font-bold mt-0.5 ${blocked ? 'text-red-400' : 'text-[var(--qn-yellow)]'}`} data-testid="song-state">
              {blocked ? 'DO NOT PLAY: never picked automatically on any station' : `${category || 'No category'} · ${full?.asset_type ?? target.asset.asset_type ?? ''}`}
            </div>
            {blockedHere && !blocked && (
              <div className="text-xs font-bold mt-0.5 text-red-400" data-testid="song-state-here">NOT ON THIS STATION: {here} never picks it</div>
            )}
          </div>

          {why && (
            <button type="button" onClick={why.open} data-testid="song-why"
              className="qn-btn w-full min-h-11 lg:min-h-8 px-2 flex items-center gap-1.5 text-left text-sm">
              <span className="shrink-0">Playing from:</span>
              <span className="flex-1 min-w-0 truncate font-bold">{why.short}</span>
              <span className="shrink-0 underline">Why ›</span>
            </button>
          )}

          {!canEdit && (
            <p className="text-sm" data-testid="song-view-only">View only. A manager can change this song.</p>
          )}

          <label className="block">
            <span className={label}>Category</span>
            <select value={category ?? ''} disabled={!canEdit || !!busy} data-testid="song-category"
              onChange={(e) => changeCategory(e.target.value)} className={field}>
              <option value="">No category</option>
              {options.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <span className="block text-[11px] mt-0.5">Saved as soon as you pick. Copies already in the queue stay where they are.</span>
          </label>

          <AlsoInCategories assetId={id} mainCategory={category} canEdit={canEdit} compact
            onSaved={(text) => onToast(text, 'ok')} />

          <div className="border-t border-[#808080] pt-2 space-y-2" data-testid="song-stop">
            <div className="text-xs font-bold">Stop this song from playing</div>
            {stationId && (
              <div>
                <button type="button" onClick={toggleHere} disabled={!canEdit || !!busy || offHere === undefined} data-testid="song-here"
                  aria-pressed={blockedHere}
                  className={`qn-btn w-full min-h-12 lg:min-h-10 px-2 py-1 leading-tight ${blockedHere ? '' : '!text-red-700'}`}>
                  <span className="block text-base lg:text-sm font-bold">
                    {busy === 'here' ? 'Working…' : blockedHere ? '✓ Allow on this station again' : '⛔ Not on this station'}
                  </span>
                  <span className="block text-[11px] !text-black truncate" data-testid="song-here-scope">Only {here}</span>
                </button>
                <span className="block text-[11px] mt-0.5" data-testid="song-here-help">
                  {blockedHere
                    ? `${here} may pick it again. Nothing else changes.`
                    : `${target.onAir ? 'It finishes playing now (press Next to cut it). ' : ''}Its waiting copies leave this queue and ${here} stops picking it. The other stations keep playing it.`}
                </span>
              </div>
            )}
            <div>
              <button type="button" onClick={toggleDnp} disabled={!canEdit || !!busy} data-testid="song-dnp"
                aria-pressed={blocked}
                className={`qn-btn w-full min-h-12 lg:min-h-10 px-2 py-1 leading-tight ${blocked ? '' : '!text-red-700'}`}>
                <span className="block text-base lg:text-sm font-bold">
                  {busy === 'dnp' ? 'Working…' : blocked ? '✓ Allow it to play again' : '⛔ Do not play this song again'}
                </span>
                <span className="block text-[11px] !text-black" data-testid="song-dnp-scope">Every station</span>
              </button>
              <span className="block text-[11px] mt-0.5" data-testid="song-dnp-help">
                {blocked
                  ? `Puts the song back in the category it had before, on every station.${blockedHere ? ` It stays off ${here} until you allow it there too.` : ''}`
                  : target.onAir
                    ? 'It finishes playing now (press Next to cut it). Its waiting copies leave the queue on every station.'
                    : 'Its waiting copies leave the queue on every station. Nothing on air is interrupted.'}
              </span>
            </div>
          </div>

          <fieldset disabled={!canEdit || !!busy} className="space-y-2 border-t border-[#808080] pt-2">
            <legend className="sr-only">Song details</legend>
            <label className="block">
              <span className={label}>Title</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} data-testid="song-title" className={field} />
            </label>
            <label className="block">
              <span className={label}>Artist</span>
              <input value={artist} onChange={(e) => setArtist(e.target.value)} data-testid="song-artist" className={field} />
            </label>
            <div className="flex gap-2">
              <label className="block flex-1 min-w-0">
                <span className={label}>Type</span>
                <select value={type} onChange={(e) => setType(e.target.value)} data-testid="song-type" className={field}>
                  {typeOptions.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              <label className="block w-24 shrink-0">
                <span className={label} title="Staff rating, 0 to 100. Playlists can ask for a minimum.">Rating</span>
                <input value={vote} onChange={(e) => setVote(e.target.value)} inputMode="numeric" data-testid="song-vote" className={field} />
              </label>
            </div>
            {!voteOk && <p className="text-xs text-red-700 font-bold" data-testid="song-vote-error">Rating is a whole number from 0 to 100.</p>}
            <button type="button" onClick={save} data-testid="song-save"
              disabled={!changed || !voteOk || !title.trim()}
              className="qn-btn w-full h-11 lg:h-8 text-sm font-bold">
              {busy === 'save' ? 'Saving…' : 'Save details'}
            </button>
          </fieldset>

          <Link to={`/admin/assets/${id}`} data-testid="song-library"
            className="qn-btn w-full h-11 lg:h-8 text-sm flex items-center justify-center !no-underline !text-black">
            Open in the Library (trim, sound, history) →
          </Link>
        </div>
      </div>
    </div>
  );
}
