import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { assetLabel, clockTimeSec, dayKey, weekdayShort } from './shared';
import type { QueueRow } from './QueuePanel';
import type { Origin } from './origin';

interface Props {
  row: QueueRow;
  origin: Origin;
  stationTz?: string;
  /** May the signed-in person open this page? Links they may not open are left out */
  canOpen: (path: string) => boolean;
  /** Open the song's own panel (category, do not play...) or, for a sponsorship, the Sponsor panel */
  onSong?: () => void;
  onClose: () => void;
}

/**
 * "Why is this in the queue?" for one queue row: the playlist and schedule block an
 * automatic song was drawn for, with the step it filled, or what else put it there,
 * and a link straight to the place to change it.
 */
export default function WhyPanel({ row, origin, stationTz, canOpen, onSong, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose]);

  const a = row.asset;
  let when = '';
  if (row.status === 'playing') when = 'On the air now';
  else if (row.estimated_start) {
    const d = new Date(row.estimated_start);
    const { time, ampm } = clockTimeSec(d, stationTz);
    const day = dayKey(d, stationTz) !== dayKey(Date.now(), stationTz) ? ` ${weekdayShort(d, stationTz)}` : '';
    when = `${row.preempt_at ? 'Plays at' : 'Expected at'} ${time} ${ampm}${day}`;
  }
  const links = origin.links.filter((l) => canOpen(l.to.split('?')[0]));
  const isSponsor = origin.kind === 'sponsorship';

  return (
    <div className="fixed inset-0 z-50 flex items-end lg:items-center justify-center bg-black/60"
      onClick={onClose} data-testid="why-panel-backdrop">
      <div role="dialog" aria-modal="true" aria-label={`Why it is in the queue: ${a?.title ?? ''}`} data-testid="why-panel"
        onClick={(e) => e.stopPropagation()}
        className="w-full lg:w-[480px] max-h-[92dvh] flex flex-col bg-[#c0c0c0] text-black border-2 border-white shadow-2xl select-text pb-[env(safe-area-inset-bottom)]">
        <div className="qn-title shrink-0 h-9 lg:h-7 flex items-center gap-2 pl-2 pr-1">
          <span className="font-bold text-sm truncate flex-1">Why it is in the queue</span>
          <button type="button" onClick={onClose} aria-label="Close" className="qn-btn w-9 h-7 lg:w-6 lg:h-5 text-xs">✕</button>
        </div>

        <div className="min-h-0 overflow-y-auto overscroll-contain p-3 space-y-3">
          <div className="bg-black qn-sunken px-2 py-1.5">
            <div className="text-[var(--qn-digit)] font-bold text-base leading-tight break-words" data-testid="why-song">{a ? assetLabel(a) : 'Unknown item'}</div>
            <div className="text-xs font-bold mt-0.5 text-[var(--qn-yellow)]">
              {[when, a?.category ? `Song type: ${a.category}` : a?.asset_type].filter(Boolean).join(' · ')}
            </div>
          </div>

          <div>
            <div className="text-xs font-bold">Playing from</div>
            <div className="text-base lg:text-sm font-bold" data-testid="why-title">{origin.title}</div>
            {origin.lines.map((l, i) => <p key={i} className="text-sm mt-1" data-testid="why-line">{l}</p>)}
          </div>

          {origin.steps && origin.playlist && (
            <div data-testid="why-steps">
              <div className="text-xs font-bold mb-0.5">Steps of “{origin.playlist.name}” (played in order, then repeated)</div>
              <ol className="bg-white border border-[#808080] max-h-48 overflow-y-auto text-sm">
                {origin.steps.map((s) => (
                  <li key={s.slot.id} data-testid={s.match ? 'why-step-match' : 'why-step'}
                    className={`flex gap-2 px-2 py-0.5 ${s.match ? 'bg-[#ffff80] font-bold' : ''}`}>
                    <span className="w-6 text-right tabular-nums text-gray-600">{s.n}</span>
                    <span>{s.slot.asset_type}</span>
                    <span className="text-gray-700">{s.slot.category || '(any)'}</span>
                    {s.match && <span className="ml-auto">← this song</span>}
                  </li>
                ))}
              </ol>
            </div>
          )}

          <div className="space-y-1.5">
            {links.map((l) => (
              <Link key={l.to} to={l.to} data-testid="why-link"
                className="qn-btn w-full min-h-11 lg:min-h-8 px-2 text-sm flex items-center justify-center text-center !no-underline !text-black">
                {l.label} →
              </Link>
            ))}
            {onSong && a && row.asset?.asset_type !== 'silence' && (
              <button type="button" onClick={onSong} data-testid="why-song-panel"
                className="qn-btn w-full min-h-11 lg:min-h-8 px-2 text-sm">
                {isSponsor ? 'Open the Sponsor panel' : 'Song options (category, do not play, details)'}
              </button>
            )}
            {origin.links.length > 0 && links.length === 0 && (
              <p className="text-xs" data-testid="why-no-access">A manager can open the page where this is set.</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
