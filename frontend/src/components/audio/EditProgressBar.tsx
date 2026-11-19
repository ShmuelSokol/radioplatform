import { useEffect, useRef, useState } from 'react';
import { getEditProgress, type EditProgress } from '../../api/assets';

/**
 * Progress of a long audio edit (AI Enhance, Remove All Found Silence) while the page
 * waits for it: a bar, the step it is on, the percent and the time so far.
 * Shmuel 2026-11-19: "can it show a progress bar?" The page makes up `progressId`,
 * sends it with the edit, and this asks the server how far it is every second.
 */
export default function EditProgressBar({ progressId, active, label }: {
  progressId: string | null;
  active: boolean;
  label: string;
}) {
  const [progress, setProgress] = useState<EditProgress | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const started = useRef(0);

  useEffect(() => {
    if (!active || !progressId) {
      setProgress(null);
      return;
    }
    started.current = Date.now();
    setElapsed(0);
    let stopped = false;
    const tick = async () => {
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
  }, [active, progressId]);

  if (!active) return null;
  const pct = Math.round(Math.min(1, Math.max(0, progress?.fraction ?? 0)) * 100);
  const mins = Math.floor(elapsed / 60);
  const secs = elapsed % 60;
  return (
    <div data-testid="edit-progress" className="w-full my-2" role="status" aria-live="polite">
      <div className="flex justify-between text-xs text-gray-600 mb-1 gap-2">
        <span className="truncate">{progress?.stage || label}</span>
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
        <div className="h-full bg-brand-600 transition-all duration-300" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
