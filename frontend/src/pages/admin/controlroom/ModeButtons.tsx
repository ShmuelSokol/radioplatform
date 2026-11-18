import { useEffect, useRef, useState } from 'react';

/**
 * KBR Studio's HS, Spots mode and Panic buttons (wiki/station-modes.md).
 * HS lit = timed items start on the second. Spots mode lit = sponsorships play.
 * Panic needs a second, separate tap within 4 seconds, so a stray tap never cuts the air:
 * a double-click (second tap under 0.4 s) does not count, and switching station disarms it.
 */
export interface ModeProps {
  stationId: string | null;
  hardStart: boolean;
  spots: boolean;
  busy: boolean;
  /** False until the server has said which modes are on: HS and Spots stay greyed */
  modesKnown?: boolean;
  canChange: boolean;
  onHardStart: () => void;
  onSpots: () => void;
  /** The station the Panic was armed on; the page cuts only if it is still the one shown */
  onPanic: (stationId: string) => void;
}

export const PANIC_WINDOW_MS = 4000;
export const PANIC_MIN_GAP_MS = 400;

export default function ModeButtons({ p, stacked, grid = false }: { p: ModeProps; stacked: boolean; grid?: boolean }) {
  const [armed, setArmed] = useState<{ station: string; at: number } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  // A Panic armed on one station must never fire on another
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    setArmed(null);
  }, [p.stationId]);
  // grid: cells of the phone-sideways button grid, after Loop (TransportButtons grid)
  const btn = grid ? 'qn-btn h-11 px-1 text-xs min-w-0' : `qn-btn ${stacked ? 'w-full h-8 [@media(max-height:1000px)]:h-7 text-sm' : 'flex-1 h-11 text-xs'}`;
  const panic = () => {
    const sid = p.stationId;
    if (!sid) return;
    const now = Date.now();
    if (!armed || armed.station !== sid) {
      if (timer.current) clearTimeout(timer.current);
      setArmed({ station: sid, at: now });
      timer.current = setTimeout(() => setArmed(null), PANIC_WINDOW_MS);
      return;
    }
    if (now - armed.at < PANIC_MIN_GAP_MS) return; // a double-click is one tap
    if (timer.current) clearTimeout(timer.current);
    setArmed(null);
    p.onPanic(sid);
  };
  const isArmed = !!armed && armed.station === p.stationId;
  return (
    <div className={grid ? 'contents' : stacked ? 'flex flex-col gap-1' : 'flex gap-1 p-1 bg-[#c0c0c0]'} role="group" aria-label="Station modes">
      <button type="button" data-testid="mode-hs" aria-pressed={p.hardStart} disabled={p.busy || !p.canChange || p.modesKnown === false} onClick={p.onHardStart}
        title={p.hardStart
          ? 'HS (hard start) is on: the time check and hard blocks start on the second, fading the song. Press to make them wait for the song to end.'
          : 'HS is off: timed items wait for the song on air to end. Press to start them on the second again.'}
        className={`${btn} ${grid ? 'order-5' : ''} ${p.hardStart ? '' : '!bg-amber-300'}`}>
        HS{stacked ? '' : p.hardStart ? ' on' : ' off'}
      </button>
      <button type="button" data-testid="mode-spots" aria-pressed={p.spots} disabled={p.busy || !p.canChange || p.modesKnown === false} onClick={p.onSpots}
        title={p.spots
          ? 'Spots mode is on: scheduled sponsorships play. Press to stop putting sponsorships on the air.'
          : 'Spots mode is off: no sponsorships play. Press to put them back.'}
        className={`${btn} ${grid ? 'order-6' : ''} ${p.spots ? '' : '!bg-amber-300'}`}>
        {stacked ? 'Spots mode' : 'Spots'}{p.spots ? '' : ' off'}
      </button>
      <button type="button" data-testid="panic" disabled={p.busy || !p.canChange} onClick={panic}
        title="Panic: cut whatever is on air right now (even a time check) and play the next item. Tap twice."
        className={`${btn} ${grid ? 'order-7' : ''} font-bold ${isArmed ? '!bg-red-600 !text-white' : '!text-red-700'}`}>
        {isArmed ? 'Tap again' : 'Panic'}
      </button>
    </div>
  );
}
