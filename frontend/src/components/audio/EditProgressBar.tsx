import { useEffect, useRef, useState } from 'react';
import { getEditProgress, type EditProgress } from '../../api/assets';

/**
 * Progress of a long audio edit (Smart Enhance, Remove All Found Silence) while the page
 * waits for it: a bar, the step it is on, the percent and the time so far.
 * Shmuel 2026-11-19: "can it show a progress bar?" The page makes up `progressId`,
 * sends it with the edit, and this asks the server how far it is every second.
 */
export default function EditProgressBar({ progressId, active, label, done = false }: {
  progressId: string | null;
  active: boolean;
  label: string;
  /** The edit has finished: hold "Done 100%" (the page keeps it up a moment so it is seen) */
  done?: boolean;
}) {
  const [progress, setProgress] = useState<EditProgress | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const started = useRef(0);

  useEffect(() => {
    if (!active || !progressId || done) {
      if (!active) setProgress(null);
      return;
    }
    started.current = Date.now();
    setElapsed(0);
    let stopped = false;
    const tick = async () => {
      if (stopped) return;
      setElapsed(Math.round((Date.now() - started.current) / 1000));
      try {
        const p = await getEditProgress(progressId);
        if (!stopped) setProgress(p);
      } catch {
        // a missed poll only means the bar waits a second longer
      }
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [active, progressId, done]);

  if (!active) return null;
  const pct = done ? 100 : Math.round(Math.min(1, Math.max(0, progress?.fraction ?? 0)) * 100);
  const stage = done ? 'Done' : progress?.stage || label;
  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  return (
    <div data-testid="edit-progress" className="w-full my-2" role="status" aria-live="polite">
      <div className="flex justify-between text-xs text-gray-600 mb-1 gap-2">
        <span className="truncate">{stage}</span>
        <span className="tabular-nums shrink-0" data-testid="edit-progress-pct">
          {pct}% · {mins}:{secs.toString().padStart(2, '0')}
        </span>
      </div>
      <div
        className="h-2.5 w-full bg-gray-200 rounded overflow-hidden"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div className={`h-full transition-all duration-300 ${done ? 'bg-green-600' : 'bg-brand-600'}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** How long "Done 100%" stays up after an edit finishes, before the page moves on. The PC
 *  saw the bar go from "Saving 92%" straight to gone on a 35 minute shiur (2026-11-19). */
export const DONE_HOLD_MS = 1500;
