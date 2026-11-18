import { useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import apiClient from '../api/client';
import { usePlayerStore } from '../stores/playerStore';
import { addBreadcrumb, collectContext } from '../lib/diagnostics';
import { useEscape } from '../hooks/useEscape';

/**
 * "Report a bug" button on every page. Takes a screenshot of the screen as it
 * is (before the form opens), then sends it with what the browser saw: player
 * and audio state, queue and now-playing data on screen, console, API calls.
 * The server adds its own log. See wiki/bug-reports.md.
 */

// Station changes go on the timeline that a report carries.
usePlayerStore.subscribe((s, prev) => {
  if (s.stationId !== prev.stationId || s.isPlaying !== prev.isPlaying) {
    addBreadcrumb('player', { station: s.stationId, name: s.stationName, playing: s.isPlaying });
  }
});

const IGNORE_ATTR = 'data-bug-reporter';

/** Any part of the app can open the reporter (e.g. the phone menu) with this event. */
export const REPORT_BUG_EVENT = 'kbr:report-bug';

async function captureScreen(): Promise<string | null> {
  try {
    const { default: html2canvas } = await import('html2canvas');
    const scale = Math.min(window.devicePixelRatio || 1, 1.5, 1600 / window.innerWidth);
    const canvas = await html2canvas(document.body, {
      x: window.scrollX,
      y: window.scrollY,
      width: window.innerWidth,
      height: window.innerHeight,
      scale,
      useCORS: true,
      logging: false,
      backgroundColor: null,
      ignoreElements: (el) => el.hasAttribute?.(IGNORE_ATTR),
    });
    return canvas.toDataURL('image/jpeg', 0.75);
  } catch (e) {
    console.warn('Bug report screenshot failed', e);
    return null;
  }
}

function currentStationId(): string | null {
  const m = location.pathname.match(/\/listen\/([^/?#]+)/);
  if (m) return m[1];
  const p = usePlayerStore.getState().stationId;
  if (p) return p;
  try { return localStorage.getItem('radio_last_station'); } catch { return null; }
}

type Phase = 'closed' | 'capturing' | 'open' | 'sending' | 'sent';

export default function BugReporter() {
  const [phase, setPhase] = useState<Phase>('closed');
  const [shot, setShot] = useState<string | null>(null);
  const [includeShot, setIncludeShot] = useState(true);
  const [description, setDescription] = useState('');
  const [expected, setExpected] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [number, setNumber] = useState<number | null>(null);
  const [shotDropped, setShotDropped] = useState(false);
  const contextRef = useRef<Record<string, unknown> | null>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (phase === 'open') textRef.current?.focus();
  }, [phase]);

  const { pathname } = useLocation();
  // The control room fills the window to its edges: the floating button would cover
  // a queue row's insert button on a phone and the cart's buttons on a computer. There
  // it lives in the menu (phone) and in the status bar along the bottom (computer).
  const hideFloating = pathname === '/admin/dashboard' || pathname === '/admin/databank';
  const startRef = useRef<() => void>(() => {});

  const start = async () => {
    setPhase('capturing');
    setError(null);
    // Freeze what was happening at the moment of the click, before the form changes anything.
    const player = usePlayerStore.getState();
    contextRef.current = collectContext({
      station_id: currentStationId(),
      player: {
        stationId: player.stationId,
        stationName: player.stationName,
        isPlaying: player.isPlaying,
        currentTrack: player.currentTrack,
      },
    });
    setShot(await captureScreen());
    setPhase('open');
  };

  startRef.current = () => {
    // Give a menu that triggered this a moment to close before the screenshot.
    if (phase === 'closed') setTimeout(start, 250);
  };
  useEffect(() => {
    const onEvent = () => startRef.current();
    window.addEventListener(REPORT_BUG_EVENT, onEvent);
    return () => window.removeEventListener(REPORT_BUG_EVENT, onEvent);
  }, []);

  const close = () => {
    setPhase('closed');
    setDescription('');
    setExpected('');
    setShot(null);
    setIncludeShot(true);
    setError(null);
  };
  useEscape(phase === 'open' || phase === 'sent', close);

  const send = async () => {
    if (!description.trim()) {
      setError('Please say what went wrong.');
      return;
    }
    setPhase('sending');
    setError(null);
    try {
      const res = await apiClient.post('/bug-reports', {
        description: description.trim(),
        expected: expected.trim() || null,
        page_url: location.href,
        station_id: currentStationId(),
        screenshot: includeShot ? shot : null,
        context: contextRef.current,
      });
      setNumber(res.data.number);
      setShotDropped(res.data.screenshot_saved === false);
      setPhase('sent');
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      setError(err.response?.data?.detail || err.message || 'Could not send the report.');
      setPhase('open');
    }
  };

  return (
    <div {...{ [IGNORE_ATTR]: '' }}>
      {phase === 'closed' && (
        <button
          type="button"
          onClick={start}
          title="Report a bug"
          aria-label="Report a bug"
          // Phone: right edge, where cards have no text (on the left it covered their labels and the Library checkboxes); 44 px there for a finger
          className={`fixed right-3 sm:right-auto sm:left-3 bottom-24 z-[60] h-11 w-11 sm:h-9 sm:w-9 rounded-full ${hideFloating ? 'hidden' : 'flex'} bg-gray-800/80 text-gray-200 shadow-lg ring-1 ring-white/15 hover:bg-rose-600 hover:text-white transition-colors items-center justify-center text-base print:hidden`}
        >
          <span aria-hidden>🐞</span>
        </button>
      )}
      {phase === 'capturing' && (
        <div className="fixed right-3 sm:right-auto sm:left-3 bottom-24 z-[60] rounded-full bg-gray-800 px-3 py-2 text-xs text-gray-200 shadow-lg">
          Capturing the screen…
        </div>
      )}
      {(phase === 'open' || phase === 'sending' || phase === 'sent') && (
        <div className="fixed inset-0 z-[70] flex items-end sm:items-center justify-center bg-black/60 p-0 sm:p-4" onClick={phase === 'sending' ? undefined : close}>
          <div
            role="dialog"
            aria-label="Report a bug"
            className="w-full sm:max-w-lg max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl bg-gray-900 text-gray-100 shadow-2xl ring-1 ring-white/10 p-4 sm:p-5"
            onClick={(e) => e.stopPropagation()}
          >
            {phase === 'sent' ? (
              <div className="text-center py-6">
                <div className="text-3xl mb-2">✅</div>
                <h2 className="text-lg font-semibold">Thanks, bug {number} was sent</h2>
                <p className="mt-2 text-sm text-gray-400">
                  {shotDropped
                    ? 'The screenshot could not be kept (too big or unreadable). What the player was doing went with it.'
                    : 'The screenshot and what the player was doing went with it.'}
                </p>
                <button type="button" onClick={close} className="mt-5 rounded-lg bg-white/10 px-4 py-2 text-sm hover:bg-white/20">
                  Close
                </button>
              </div>
            ) : (
              <>
                <div className="flex items-center justify-between mb-3">
                  <h2 className="text-lg font-semibold">Report a bug</h2>
                  <button type="button" onClick={close} className="text-gray-400 hover:text-white text-xl leading-none" aria-label="Close">×</button>
                </div>
                <label className="block text-sm text-gray-300 mb-1" htmlFor="bug-what">What went wrong?</label>
                <textarea
                  id="bug-what"
                  ref={textRef}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={4}
                  maxLength={5000}
                  placeholder="For example: I switched stations and both kept playing."
                  className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-rose-500"
                />
                <label className="block text-sm text-gray-300 mt-3 mb-1" htmlFor="bug-expected">What did you expect? <span className="text-gray-500">(optional)</span></label>
                <textarea
                  id="bug-expected"
                  value={expected}
                  onChange={(e) => setExpected(e.target.value)}
                  rows={2}
                  maxLength={3000}
                  className="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-rose-500"
                />
                <div className="mt-3">
                  {shot ? (
                    <>
                      <label className="flex items-center gap-2 text-sm text-gray-300">
                        <input type="checkbox" checked={includeShot} onChange={(e) => setIncludeShot(e.target.checked)} />
                        Include this screenshot
                      </label>
                      {includeShot && <img src={shot} alt="Screenshot of the page" className="mt-2 w-full max-h-48 object-cover object-top rounded-lg ring-1 ring-white/10" />}
                    </>
                  ) : (
                    <p className="text-xs text-amber-400">The screenshot could not be taken. You can still send the report.</p>
                  )}
                </div>
                {error && <p className="mt-3 text-sm text-rose-400">{error}</p>}
                <div className="mt-4 flex justify-end gap-2">
                  <button type="button" onClick={close} className="rounded-lg px-4 py-2 text-sm text-gray-300 hover:bg-white/10">Cancel</button>
                  <button
                    type="button"
                    onClick={send}
                    disabled={phase === 'sending'}
                    className="rounded-lg bg-rose-600 px-4 py-2 text-sm font-semibold text-white hover:bg-rose-500 disabled:opacity-60"
                  >
                    {phase === 'sending' ? 'Sending…' : 'Send report'}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
