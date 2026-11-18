import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { daypartText, getSponsorItem, skipSponsorshipPlay, updateSpotSchedule } from '../../../api/spotSchedules';
import type { SponsorItemContract, SponsorItemPlay } from '../../../api/spotSchedules';
import { assetLabel, fmtDur } from './shared';
import type { SongTarget } from './SongPanel';

interface Props {
  target: SongTarget;
  stationId: string;
  /** Skip / put back one play (everyone who runs the control room) */
  canSkip: boolean;
  /** Pause / resume a contract (managers and admins) */
  canManage: boolean;
  /** May this person open a page? Links to pages they cannot open are left out */
  canOpen: (path: string) => boolean;
  /** Open the item's own options (category, do not play, details) instead */
  onItemOptions: () => void;
  onClose: () => void;
  onToast: (text: string, type: 'ok' | 'warn' | 'error') => void;
}

const STATE_TEXT: Record<SponsorItemContract['state'], { text: string; cls: string }> = {
  running: { text: 'RUNNING', cls: 'bg-green-700 text-white' },
  paused: { text: 'PAUSED', cls: 'bg-amber-500 text-black' },
  ended: { text: 'ENDED', cls: 'bg-[#808080] text-white' },
  not_started: { text: 'NOT STARTED', cls: 'bg-blue-700 text-white' },
};

function errText(e: unknown, fallback: string): string {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof d === 'string' ? d : fallback;
}

function fmtDate(iso: string | null): string {
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function playsText(c: SponsorItemContract): string {
  if (c.fixed_times.length) return `At ${c.fixed_times.join(', ')}`;
  const parts = c.dayparts.map(d => daypartText(d, true)).join(', ');
  return `${c.plays_per_daypart} ${c.plays_per_daypart === 1 ? 'play' : 'plays'} in each: ${parts || 'no times of day'}`;
}

/**
 * The control room's panel for a sponsorship spot (tapped on air or in the queue):
 * whose spot it is, the contract(s) behind it, today's and coming plays on this
 * station with Skip / Put back, recent missed plays, Pause / Resume of a contract,
 * and links into the sponsorship pages. The item's own options (category, do not
 * play, details) are one button away.
 */
export default function SponsorPanel({ target, stationId, canSkip, canManage, canOpen, onItemOptions, onClose, onToast }: Props) {
  const qc = useQueryClient();
  const assetId = target.asset.id;
  const key = ['sponsor-item', assetId, stationId];
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: key,
    queryFn: () => getSponsorItem(assetId, stationId),
    refetchInterval: 30_000,
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [showAllComing, setShowAllComing] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose]);

  const run = async (what: string, fn: () => Promise<string>, failed: string) => {
    if (busy) return;
    setBusy(what);
    try {
      onToast(await fn(), 'ok');
      qc.invalidateQueries({ queryKey: ['sponsor-item', assetId] });
      qc.invalidateQueries({ queryKey: ['queue', stationId] });
      qc.invalidateQueries({ queryKey: ['spot-schedules'] });
      qc.invalidateQueries({ queryKey: ['spot-plan'] });
    } catch (e) {
      onToast(errText(e, failed), 'error');
      refetch();
    } finally {
      setBusy(null);
    }
  };

  const skip = (p: SponsorItemPlay, restore: boolean) => run(`play-${p.entry_id}`, async () => {
    await skipSponsorshipPlay(stationId, p.entry_id, restore);
    const when = `${p.local_day === 'Today' ? '' : `${p.local_day} `}${p.local_time ?? ''}`.trim();
    return restore
      ? `The ${when} play is back in the queue.`
      : `The ${when} play is skipped. It counts as a missed play for the sponsor; Put back undoes it.`;
  }, 'That did not go through. Try again.');

  const pause = (c: SponsorItemContract) => run(`contract-${c.id}`, async () => {
    await updateSpotSchedule(c.id, { is_active: !c.is_active });
    const name = c.name || c.sponsor?.name || 'The contract';
    return c.is_active
      ? `${name} is paused: its coming plays leave the queue on ${c.station_name === 'Every station' ? 'every station' : c.station_name}.`
      : `${name} is running again: its plays are back in the queue.`;
  }, 'The contract was not changed. Try again.');

  const sponsor = data?.contracts.find(c => c.sponsor)?.sponsor ?? data?.owner ?? null;
  const contractsHere = data?.contracts.filter(c => c.on_this_station) ?? [];
  const contractsElsewhere = data?.contracts.filter(c => !c.on_this_station) ?? [];
  const coming = data?.upcoming ?? [];
  const comingShown = showAllComing ? coming : coming.slice(0, 5);
  const missedPast = (data?.missed ?? []).filter(m => m.when === 'past');
  const missedComing = (data?.missed ?? []).filter(m => m.when === 'coming');
  const linkCls = 'qn-btn w-full min-h-11 lg:min-h-8 px-2 text-sm flex items-center justify-center text-center !no-underline !text-black';
  const head = 'text-xs font-bold';

  const playRow = (p: SponsorItemPlay) => (
    <li key={p.entry_id} className="flex items-center gap-2 py-1 border-b border-[#a0a0a0] last:border-b-0" data-testid="sponsor-play">
      <span className="w-[5.5rem] shrink-0 font-mono text-sm tabular-nums">
        {p.local_day && p.local_day !== 'Today' && <span className="block text-[10px] leading-none">{p.local_day}</span>}
        {p.local_time ?? 'Next'}
      </span>
      <span className={`flex-1 min-w-0 text-xs font-bold truncate ${
        p.status === 'played' ? 'text-green-800'
          : p.status === 'playing' ? 'text-red-700'
          : p.status === 'skipped' || p.status === 'cancelled' ? 'text-[#606060] line-through'
          : ''}`} data-testid="sponsor-play-status">
        {p.status_text}{p.timing_mode === 'hard' && p.status === 'pending' ? ' · on the second' : ''}
      </span>
      {canSkip && p.can_skip && (
        <button type="button" onClick={() => skip(p, false)} disabled={!!busy} data-testid="sponsor-skip"
          className="qn-btn shrink-0 min-h-10 lg:min-h-7 px-3 text-xs font-bold !text-red-700">
          {busy === `play-${p.entry_id}` ? '…' : 'Skip'}
        </button>
      )}
      {canSkip && p.can_restore && (
        <button type="button" onClick={() => skip(p, true)} disabled={!!busy} data-testid="sponsor-restore"
          className="qn-btn shrink-0 min-h-10 lg:min-h-7 px-3 text-xs font-bold">
          {busy === `play-${p.entry_id}` ? '…' : 'Put back'}
        </button>
      )}
    </li>
  );

  const contractCard = (c: SponsorItemContract) => {
    const st = STATE_TEXT[c.state];
    return (
      <li key={c.id} className="bg-white qn-sunken p-2 space-y-1" data-testid="sponsor-contract">
        <div className="flex items-start gap-2">
          <span className="flex-1 min-w-0 font-bold text-sm break-words">{c.name || c.sponsor?.name || 'Sponsorship'}</span>
          <span className={`shrink-0 text-[10px] font-bold px-1.5 py-px ${st.cls}`} data-testid="sponsor-contract-state">{st.text}</span>
        </div>
        <div className="text-xs">{c.station_name} · {c.days_text}</div>
        <div className="text-xs">{playsText(c)}{c.timing_mode ? ` · ${c.timing_mode === 'hard' ? 'on the second' : 'after the song'}` : ''}</div>
        <div className="text-xs" data-testid="sponsor-contract-dates">
          {c.start_date || c.end_date
            ? `${c.start_date ? `From ${fmtDate(c.start_date)}` : 'Running'}${c.end_date ? ` until ${fmtDate(c.end_date)}` : ', no end date'}`
            : 'No start or end date'}
        </div>
        {canManage && c.state !== 'ended' && (
          <button type="button" onClick={() => pause(c)} disabled={!!busy} data-testid="sponsor-pause"
            className="qn-btn w-full min-h-10 lg:min-h-7 text-xs font-bold">
            {busy === `contract-${c.id}` ? 'Working…' : c.is_active ? 'Pause this contract' : 'Resume this contract'}
          </button>
        )}
        {canOpen('/admin/spot-schedule') && (
          <Link to={`/admin/spot-schedule?contract=${c.id}${c.station_id ? `&station=${c.station_id}` : ''}`} data-testid="sponsor-contract-link"
            className="block text-xs underline !text-blue-800">Change it on Spot Schedule →</Link>
        )}
      </li>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end lg:items-center justify-center bg-black/60"
      onClick={onClose} data-testid="sponsor-panel-backdrop">
      <div role="dialog" aria-modal="true" aria-label={`Sponsorship: ${target.asset.title}`} data-testid="sponsor-panel"
        onClick={(e) => e.stopPropagation()}
        className="w-full lg:w-[480px] max-h-[92dvh] flex flex-col bg-[#c0c0c0] text-black border-2 border-white shadow-2xl select-text pb-[env(safe-area-inset-bottom)]">
        <div className="qn-title shrink-0 h-9 lg:h-7 flex items-center gap-2 pl-2 pr-1">
          <span className="font-bold text-sm truncate flex-1">Sponsorship</span>
          {target.onAir && <span className="text-[10px] font-bold bg-red-600 text-white px-1.5 py-px" data-testid="sponsor-on-air">ON AIR</span>}
          <button type="button" onClick={onClose} aria-label="Close" className="qn-btn w-9 h-7 lg:w-6 lg:h-5 text-xs">✕</button>
        </div>

        <div className="min-h-0 overflow-y-auto overscroll-contain p-3 space-y-3">
          <div className="bg-black qn-sunken px-2 py-1.5">
            <div className="text-[var(--qn-digit)] font-bold text-base leading-tight break-words" data-testid="sponsor-spot-name">
              {assetLabel(data?.asset ?? target.asset)}
            </div>
            <div className="text-xs font-bold mt-0.5 text-[var(--qn-yellow)]" data-testid="sponsor-name">
              {sponsor
                ? `Sponsor: ${sponsor.name}${sponsor.company_name && sponsor.company_name !== sponsor.name ? ` (${sponsor.company_name})` : ''}`
                : isLoading ? 'Loading…' : 'No sponsor is linked to this recording'}
            </div>
            {data && (
              <div className="text-[11px] text-white mt-0.5" data-testid="sponsor-summary">
                {fmtDur(data.asset.duration)} · aired {data.aired_today}× today on {data.station.name}
              </div>
            )}
          </div>

          {sponsor && (sponsor.contact_phone || sponsor.contact_email) && (
            <div className="text-xs" data-testid="sponsor-contact">
              {sponsor.contact_phone && <a href={`tel:${sponsor.contact_phone}`} className="underline !text-blue-800 mr-3">{sponsor.contact_phone}</a>}
              {sponsor.contact_email && <a href={`mailto:${sponsor.contact_email}`} className="underline !text-blue-800 break-all">{sponsor.contact_email}</a>}
            </div>
          )}

          {isError && (
            <div className="text-sm" data-testid="sponsor-error">
              The sponsorship details could not be loaded.{' '}
              <button type="button" onClick={() => refetch()} className="qn-btn px-2 min-h-8 text-xs">Try again</button>
            </div>
          )}

          {data && (
            <>
              <section className="space-y-1" data-testid="sponsor-contracts">
                <div className={head}>Contract{contractsHere.length === 1 ? '' : 's'} on this station</div>
                {contractsHere.length ? (
                  <ul className="space-y-2">{contractsHere.map(contractCard)}</ul>
                ) : (
                  <p className="text-xs" data-testid="sponsor-no-contract">
                    No contract schedules this recording on {data.station.name}. It airs only when someone puts it in the queue.
                  </p>
                )}
                {contractsElsewhere.length > 0 && (
                  <details className="text-xs" data-testid="sponsor-elsewhere">
                    <summary className="cursor-pointer font-bold py-1">On other stations ({contractsElsewhere.length})</summary>
                    <ul className="space-y-2 mt-1">{contractsElsewhere.map(contractCard)}</ul>
                  </details>
                )}
              </section>

              <section className="border-t border-[#808080] pt-2" data-testid="sponsor-today">
                <div className={head}>Today on {data.station.name}</div>
                {data.today.length ? (
                  <ul>{data.today.map(playRow)}</ul>
                ) : (
                  <p className="text-xs">No plays today.</p>
                )}
              </section>

              <section className="border-t border-[#808080] pt-2" data-testid="sponsor-coming">
                <div className={head}>Coming days</div>
                {coming.length ? (
                  <>
                    <ul>{comingShown.map(playRow)}</ul>
                    {coming.length > comingShown.length && (
                      <button type="button" onClick={() => setShowAllComing(true)} data-testid="sponsor-more"
                        className="qn-btn w-full min-h-10 lg:min-h-7 text-xs mt-1">Show {coming.length - comingShown.length} more</button>
                    )}
                  </>
                ) : (
                  <p className="text-xs">Nothing in the queue after today.</p>
                )}
                {canSkip && (
                  <p className="text-[11px] mt-1">Skip takes one play out (it counts as missed for the sponsor). A play due within 2 minutes cannot be skipped here: press Next when it airs.</p>
                )}
              </section>

              <section className="border-t border-[#808080] pt-2" data-testid="sponsor-missed">
                <div className={head}>Missed plays (last 7 days)</div>
                {missedPast.length ? (
                  <ul className="text-xs">
                    {missedPast.map(m => (
                      <li key={`${m.local_date}-${m.daypart}-${m.local_time ?? ''}`} className="py-0.5" data-testid="sponsor-missed-row">
                        {m.local_time
                          ? <><b>{m.day}</b>, {m.local_time}: did not air</>
                          : <><b>{m.day}</b>, {daypartText(m.daypart).split(' (')[0].toLowerCase()}: {m.played} of {m.promised} aired</>}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs" data-testid="sponsor-missed-none">{contractsHere.length ? 'None. Every promised play aired.' : 'No contract here, so nothing is promised.'}</p>
                )}
                {sponsor && canOpen('/admin/sponsors') && (missedPast.length > 0 || missedComing.length > 0) && (
                  <Link to={`/admin/sponsors/${sponsor.id}?tab=missed`} className="text-xs underline text-blue-800" data-testid="link-sponsor-missed">All of this sponsor's missed plays →</Link>
                )}
                {missedComing.length > 0 && (
                  <ul className="text-xs mt-1 text-red-800" data-testid="sponsor-no-room">
                    {missedComing.map(m => (
                      <li key={`c-${m.local_date}-${m.daypart}`} className="py-0.5">
                        <b>{m.day}</b>, {daypartText(m.daypart).split(' (')[0].toLowerCase()}: {m.note || `${m.missed} with no room`}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          )}

          <section className="border-t border-[#808080] pt-2 grid grid-cols-2 gap-2" data-testid="sponsor-links">
            {canOpen('/admin/spot-schedule') && (
              <Link to={`/admin/spot-schedule?station=${stationId}`} className={linkCls} data-testid="link-spot-schedule">Spot Schedule</Link>
            )}
            {canOpen('/admin/sponsors') && (
              <Link to={sponsor ? `/admin/sponsors/${sponsor.id}?tab=contracts` : '/admin/sponsors'} className={linkCls} data-testid="link-sponsors">{sponsor ? 'Sponsor page' : 'Sponsors'}</Link>
            )}
            {canOpen('/admin/campaigns') && (
              <Link to={sponsor ? `/admin/campaigns?sponsor=${sponsor.id}` : '/admin/campaigns'} className={linkCls} data-testid="link-campaigns">Campaigns</Link>
            )}
            {canOpen('/admin/schedule-check') && (
              <Link to="/admin/schedule-check" className={linkCls} data-testid="link-schedule-check">Schedule Check</Link>
            )}
            <button type="button" onClick={onItemOptions} className={`${linkCls} col-span-2`} data-testid="sponsor-item-options">
              Recording options (category, do not play, title) →
            </button>
          </section>
        </div>
      </div>
    </div>
  );
}
