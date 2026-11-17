import { useEffect, useRef, useState } from 'react';

/**
 * KBR Studio's HS, Spots mode and Panic buttons (wiki/station-modes.md).
 * HS lit = timed items start on the second. Spots mode lit = sponsorships play.
 * Panic needs a second tap within 4 seconds, so a stray tap never cuts the air.
 */
export interface ModeProps {
  hardStart: boolean;
  spots: boolean;
  busy: boolean;
  canChange: boolean;
  onHardStart: () => void;
  onSpots: () => void;
  onPanic: () => void;
}

export default function ModeButtons({ p, stacked }: { p: ModeProps; stacked: boolean }) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const btn = `qn-btn ${stacked ? 'w-full h-8 text-sm' : 'flex-1 h-11 text-xs'}`;
  const panic = () => {
    if (!armed) {
      setArmed(true);
      timer.current = setTimeout(() => setArmed(false), 4000);
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    setArmed(false);
    p.onPanic();
  };
  return (
    <div className={stacked ? 'flex flex-col gap-1' : 'flex gap-1 p-1 bg-[#c0c0c0]'} role="group" aria-label="Station modes">
      <button type="button" data-testid="mode-hs" aria-pressed={p.hardStart} disabled={p.busy || !p.canChange} onClick={p.onHardStart}
        title={p.hardStart
          ? 'HS (hard start) is on: the time check and hard blocks start on the second, fading the song. Press to make them wait for the song to end.'
          : 'HS is off: timed items wait for the song on air to end. Press to start them on the second again.'}
        className={`${btn} ${p.hardStart ? '' : '!bg-amber-300'}`}>
        HS{stacked ? '' : p.hardStart ? ' on' : ' off'}
      </button>
      <button type="button" data-testid="mode-spots" aria-pressed={p.spots} disabled={p.busy || !p.canChange} onClick={p.onSpots}
        title={p.spots
          ? 'Spots mode is on: scheduled sponsorships play. Press to stop putting sponsorships on the air.'
          : 'Spots mode is off: no sponsorships play. Press to put them back.'}
        className={`${btn} ${p.spots ? '' : '!bg-amber-300'}`}>
        {stacked ? 'Spots mode' : 'Spots'}{p.spots ? '' : ' off'}
      </button>
      <button type="button" data-testid="panic" disabled={p.busy || !p.canChange} onClick={panic}
        title="Panic: cut whatever is on air right now (even a time check) and play the next item. Tap twice."
        className={`${btn} font-bold ${armed ? '!bg-red-600 !text-white' : '!text-red-700'}`}>
        {armed ? 'Tap again' : 'Panic'}
      </button>
    </div>
  );
}
