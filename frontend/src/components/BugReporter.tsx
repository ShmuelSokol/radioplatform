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

export type Capture = { shot: string | null; how: string; tries: string[] };

const CAPTURE_TIMEOUT_MS = 8000;

function withTimeout<T>(p: Promise<T>, ms: number, what: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = window.setTimeout(() => reject(new Error(`${what} took over ${ms / 1000} s`)), ms);
    p.then((v) => { window.clearTimeout(t); resolve(v); }, (e) => { window.clearTimeout(t); reject(e); });
  });
}

/** A data URL that holds a real picture (Safari gives "data:," for a canvas it will not export). */
function usable(url: string | null | undefined): boolean {
  return !!url && url.startsWith('data:image/') && url.length > 1000;
}

function describe(e: unknown): string {
  if (e instanceof Error) return e.message || e.name;
  return String(e);
}

/** The page's own media and frames can't be drawn and only slow a picture down. */
const skipNode = (el: Element) =>
  el.hasAttribute?.(IGNORE_ATTR) || ['AUDIO', 'VIDEO', 'IFRAME', 'OBJECT', 'EMBED'].includes(el.tagName);

/** The colour behind the bottom of the screen, to fill any part the copy of the page leaves empty. */
function backdrop(): string {
  let el: Element | null = document.elementFromPoint(window.innerWidth / 2, window.innerHeight - 2);
  while (el) {
    const c = getComputedStyle(el).backgroundColor;
    if (c && c !== 'transparent' && !/rgba\(.*,\s*0\)$/.test(c)) return c;
    el = el.parentElement;
  }
  return '#ffffff';
}

/**
 * Picture of the screen as it is. Two ways, so one engine's quirk does not leave a report
 * without one: modern-screenshot first (the browser draws the page itself into an SVG
 * picture: truer than html2canvas, and it has Safari's workarounds; html2canvas alone came
 * back empty on Safari, iPhone and Mac: bug 8), then html2canvas (redraws the page in
 * script). Each try has a time limit, and why each failed goes with the report
 * (`client_context.screenshot`).
 */
export async function captureScreen(): Promise<Capture> {
  const tries: string[] = [];
  const scale = Math.min(window.devicePixelRatio || 1, 1.5, 1600 / window.innerWidth);
  const t0 = performance.now();
  // Tests turn a way off to check the other: window.__kbrShotSkip = ['html2canvas']
  const skip: string[] = (window as unknown as { __kbrShotSkip?: string[] }).__kbrShotSkip || [];
  try {
    if (skip.includes('modern-screenshot')) throw new Error('skipped by test');
    const { domToJpeg } = await import('modern-screenshot');
    const bg = backdrop();
    const url = await withTimeout(domToJpeg(document.documentElement, {
      width: window.innerWidth,
      height: window.innerHeight,
      scale,
      quality: 0.75,
      backgroundColor: bg,
      filter: (n) => !(n instanceof Element) || !skipNode(n),
      timeout: 4000,
      // Scrolled page: move the page up and left, not with a transform (that would carry the
      // fixed top bar away with it)
      style: { marginTop: `${-window.scrollY}px`, marginLeft: `${-window.scrollX}px` },
    }), CAPTURE_TIMEOUT_MS, 'modern-screenshot');
    if (usable(url)) return { shot: url, how: 'modern-screenshot', tries };
    tries.push(`modern-screenshot: the browser gave no picture (${String(url).slice(0, 20)})`);
  } catch (e) {
    tries.push(`modern-screenshot: ${describe(e)}`);
  }
  try {
    if (skip.includes('html2canvas')) throw new Error('skipped by test');
    const { default: html2canvas } = await import('html2canvas');
    const canvas = await withTimeout(html2canvas(document.body, {
      x: window.scrollX,
      y: window.scrollY,
      width: window.innerWidth,
      height: window.innerHeight,
      scale,
      useCORS: true,
      logging: false,
      backgroundColor: null,
      imageTimeout: 4000,
      ignoreElements: skipNode,
    }), CAPTURE_TIMEOUT_MS, 'html2canvas');
    const url = canvas.toDataURL('image/jpeg', 0.75);
    if (usable(url)) return { shot: url, how: 'html2canvas', tries };
    tries.push(`html2canvas: the browser gave no picture (${url.slice(0, 20)})`);
  } catch (e) {
    tries.push(`html2canvas: ${describe(e)}`);
  }
  console.warn('Bug report screenshot failed', tries, `${Math.round(performance.now() - t0)} ms`);
  return { shot: null, how: 'none', tries };
}

/** A picture the person picked (their own screenshot), made small enough to send. */
async function shrinkPicked(file: File): Promise<string | null> {
  const src = URL.createObjectURL(file);
  try {
    const img = new Image();
    await new Promise<void>((resolve, reject) => { img.onload = () => resolve(); img.onerror = () => reject(new Error('unreadable')); img.src = src; });
    const k = Math.min(1, 1600 / Math.max(img.naturalWidth, img.naturalHeight));
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(img.naturalWidth * k));
    c.height = Math.max(1, Math.round(img.naturalHeight * k));
    c.getContext('2d')?.drawImage(img, 0, 0, c.width, c.height);
    const url = c.toDataURL('image/jpeg', 0.8);
    return usable(url) ? url : null;
  } catch {
    return null;
  } finally {
    URL.revokeObjectURL(src);
  }
}

function currentStationId(): string | null {
  const m = location.pathname.match(/\/listen\/([^/?#]+)/);
  if (m) return m[1];
  const p = usePlayerStore.getState().stationId;
  if (p) return p;
  try { return localStorage.getItem('radio_last_station'); } catch { return null; }
}

/**
 * The bug button of the top bar on a phone: beside the bell on every staff page (and in
 * the sponsor portal's bar), 44 px, one tap from the same place everywhere. Where it is
 * on the page the floating button is not shown at phone size (it kept covering things).
 */
export function BugTopButton({ className = '' }: { className?: string }) {
  return (
    <button type="button" data-bug-top="" data-testid="bug-top" title="Report a bug" aria-label="Bug report"
      onClick={() => window.dispatchEvent(new CustomEvent(REPORT_BUG_EVENT))}
      className={`sm:hidden inline-flex items-center justify-center w-11 h-11 shrink-0 text-lg leading-none rounded hover:bg-white/10 ${className}`}>
      <span aria-hidden>🐞</span>
    </button>
  );
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
  const [picked, setPicked] = useState(false);
  const [pickError, setPickError] = useState(false);
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
  // The Library has controls on both edges of every row (checkbox left, View right): on a
  // phone the button would sit on one while scrolling. There it is in the menu ("Report a bug").
  const hideOnPhone = pathname === '/admin/assets';
  // A top bar that carries the bug button (BugTopButton) replaces the floating one on phones
  const [topButton, setTopButton] = useState(false);
  useEffect(() => {
    const look = () => setTopButton(!!document.querySelector('[data-bug-top]'));
    look();
    const t = window.setTimeout(look, 300); // a page that loads late brings its bar late
    return () => window.clearTimeout(t);
  }, [pathname]);
  const startRef = useRef<(delay?: number) => void>(() => {});

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
    const cap = await captureScreen();
    contextRef.current = { ...contextRef.current, screenshot: { how: cap.how, failed: cap.tries } };
    setShot(cap.shot);
    setPicked(false);
    setPhase('open');
  };

  startRef.current = (delay = 250) => {
    // Give a menu that triggered this a moment to close before the screenshot.
    if (phase === 'closed') setTimeout(start, delay);
  };
  useEffect(() => {
    const onEvent = (e: Event) => startRef.current((e as CustomEvent).detail?.delay);
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
    setPicked(false);
    setPickError(false);
  };
  const pick = async (file: File | undefined) => {
    if (!file) return;
    const url = await shrinkPicked(file);
    setPickError(!url);
    if (url) {
      setShot(url);
      setIncludeShot(true);
      setPicked(true);
      contextRef.current = { ...contextRef.current, screenshot: { ...(contextRef.current?.screenshot as object), picked_by_person: true } };
    }
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
          className={`fixed right-3 sm:right-auto sm:left-3 bottom-24 z-[60] h-11 w-11 sm:h-9 sm:w-9 rounded-full ${hideFloating ? 'hidden' : hideOnPhone || topButton ? 'hidden sm:flex' : 'flex'} bg-gray-800/80 text-gray-200 shadow-lg ring-1 ring-white/15 hover:bg-rose-600 hover:text-white transition-colors items-center justify-center text-base print:hidden`}
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
                        {picked ? 'Include this picture' : 'Include this screenshot'}
                      </label>
                      {includeShot && <img src={shot} alt="Screenshot of the page" className="mt-2 w-full max-h-48 object-cover object-top rounded-lg ring-1 ring-white/10" />}
                    </>
                  ) : (
                    <p className="text-xs text-amber-400" data-testid="bug-no-shot">
                      This browser would not let the site take a picture of the screen. You can add your own
                      (on an iPhone: side button and volume up together, then pick it below), or send the report without one.
                    </p>
                  )}
                  <label className="mt-2 inline-flex items-center gap-2 rounded-lg bg-white/10 px-3 py-2 text-sm text-gray-200 hover:bg-white/20 cursor-pointer">
                    <span aria-hidden>🖼️</span>
                    {shot ? 'Use a different picture' : 'Add a picture'}
                    <input type="file" accept="image/*" className="sr-only" data-testid="bug-pick"
                      onChange={(e) => { void pick(e.target.files?.[0]); e.target.value = ''; }} />
                  </label>
                  {pickError && <p className="mt-1 text-xs text-rose-400">That picture could not be read. Try another one.</p>}
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
