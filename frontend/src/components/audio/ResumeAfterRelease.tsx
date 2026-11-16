import { useEffect, useRef, useState } from 'react';
import { takeResume } from '../../lib/appUpdate';

/**
 * After an admin-pushed release reloaded this tab mid-playback, switch the
 * player back on by itself. Browsers that refuse to start sound without a tap
 * (Safari, iPhone) get a full-screen "tap to keep listening" instead.
 *
 * `start` switches the player on; `soundRunning` says sound really comes out.
 */
export function useResumeAfterRelease(start: () => unknown, soundRunning: boolean) {
  const [asking, setAsking] = useState(false);
  const [resuming, setResuming] = useState(false);
  const startRef = useRef(start);
  startRef.current = start;

  useEffect(() => {
    if (!takeResume()) return;
    setResuming(true);
    try { void Promise.resolve(startRef.current()).catch(() => {}); } catch { /* refused */ }
  }, []);

  useEffect(() => {
    if (!resuming) return;
    if (soundRunning) { setAsking(false); setResuming(false); return; }
    const timer = window.setTimeout(() => setAsking(true), 1500);
    return () => window.clearTimeout(timer);
  }, [resuming, soundRunning]);

  const tap = () => {
    setAsking(false);
    setResuming(false);
    void Promise.resolve(startRef.current()).catch(() => {});
  };

  return { asking, tap };
}

export function ResumePrompt({ onTap }: { onTap: () => void }) {
  return (
    <button
      type="button"
      onClick={onTap}
      data-testid="resume-after-release"
      className="fixed inset-0 z-[1000] flex flex-col items-center justify-center gap-3 bg-black/80 text-white text-center px-6"
    >
      <span className="text-5xl" aria-hidden>▶</span>
      <span className="text-2xl font-semibold">Tap to keep listening</span>
      <span className="text-sm text-gray-300 max-w-sm">
        The site was just updated. Your browser needs one tap to turn the sound back on.
      </span>
    </button>
  );
}
