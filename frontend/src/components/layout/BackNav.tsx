import { useEffect, useLayoutEffect, useRef } from 'react';
import { useLocation, useNavigate, useNavigationType } from 'react-router-dom';
import { useAuth } from '../../hooks/useAuth';
import { homeFor } from '../../auth/access';
import { pageTitle } from './PageViewTracker';
import {
  backTarget, clearMarks, currentIdx, findSig, flash, isGone, linkTo, pageHeading, pageName,
  parentOf, pathOnly, recordArrival, rowOf, sigOf, targetOf, updateEntry,
  type NavEntry,
} from '../../lib/navMemory';

/**
 * Back that returns to where you were (see lib/navMemory.ts and wiki/back-navigation.md).
 *
 * NavTracker (once, in Layout) notes on every staff page what was tapped and how far down
 * it was, and on coming back (the Back bar, the browser's back, an iPhone swipe) scrolls
 * there again and lights up the row and the control last tapped. BackBar is the visible
 * "‹ Back to …" line under the menu on every staff page.
 */

const staffPage = (p: string) => p.startsWith('/admin') && p !== '/admin/login';

/** Pages with no Back line: the control room is home. */
const NO_BAR = new Set(['/admin/dashboard', '/admin/login']);

function label(path: string): string {
  return pageName(path) || pageTitle(pathOnly(path));
}

export function NavTracker() {
  const location = useLocation();
  const type = useNavigationType();
  const navigate = useNavigate();
  const lastIdx = useRef<number | null>(null);
  const lastPath = useRef<string | null>(null);

  // Note taps on the page (capture: before a row's own handler moves to another page).
  useEffect(() => {
    const onTap = (e: Event) => {
      if (!staffPage(window.location.pathname)) return;
      const el = e.target instanceof Element ? e.target : null;
      if (!el || el.closest('[data-site-nav], [data-nav-back], [data-bug-trigger]')) return;
      const row = rowOf(el);
      const target = targetOf(el);
      if (!row && !target) return;
      if (row) clearMarks();
      const patch: Partial<NavEntry> = { scrollY: Math.round(window.scrollY) };
      if (row) patch.row = sigOf(row);
      if (target) patch.target = sigOf(target);
      const heading = pageHeading();
      if (heading) patch.label = heading;
      updateEntry(currentIdx(), patch);
    };
    let pending = 0;
    const onScroll = () => {
      if (pending) return;
      pending = window.requestAnimationFrame(() => {
        pending = 0;
        if (staffPage(window.location.pathname)) updateEntry(currentIdx(), { scrollY: Math.round(window.scrollY) });
      });
    };
    document.addEventListener('click', onTap, true);
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      document.removeEventListener('click', onTap, true);
      window.removeEventListener('scroll', onScroll);
      if (pending) window.cancelAnimationFrame(pending);
    };
  }, []);

  // Our own scroll keeping on staff pages; the browser's would fight it.
  useEffect(() => {
    try { if ('scrollRestoration' in window.history) window.history.scrollRestoration = 'manual'; } catch { /* old browsers */ }
  }, []);

  useLayoutEffect(() => {
    const idx = currentIdx();
    const path = location.pathname + location.search;
    const before = lastIdx.current;
    const pathBefore = lastPath.current;
    lastIdx.current = idx;
    lastPath.current = location.pathname;
    const entry = recordArrival(idx, path, location.key, type);
    if (!staffPage(location.pathname)) return;

    // Came back (browser back) onto a page whose thing was deleted: step over it.
    if (type === 'POP' && isGone(path)) {
      if (before !== null && before < idx) navigate(1);
      else if (idx > 0) navigate(-1);
      else navigate(parentOf(path, '/admin/dashboard'), { replace: true });
      return;
    }

    if (type === 'PUSH' && pathBefore !== null && pathBefore !== location.pathname) {
      // A new page starts at the top (a long list scrolled down used to open the next
      // page half way down). A filter or tab on the same page keeps its place.
      window.scrollTo(0, 0);
    }

    const navFrom = (location.state as { navFrom?: string } | null)?.navFrom;
    if (type !== 'POP' && !navFrom) return;
    return restore(type === 'POP' ? entry : null, navFrom);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key]);

  return null;
}

/**
 * Put the page back as it was: scroll, then find the row and control last tapped and
 * light them up. Pages draw their lists late (data arrives), so this keeps looking for
 * a few seconds and stops as soon as the person scrolls or taps themselves.
 */
function restore(entry: NavEntry | null, navFrom?: string): () => void {
  const started = Date.now();
  let stopped = false;
  let scrolled = !entry?.scrollY;
  const wantRow = !!entry?.row && !navFrom;
  const wantTarget = !!navFrom || !!entry?.target;
  let lit = !wantRow && !wantTarget;
  let rowEl: Element | null = null;
  let targetEl: Element | null = null;
  const bringIntoView = (el: Element) => {
    if (!scrolled) return;
    const r = el.getBoundingClientRect();
    if (r.top < 48 || r.bottom > window.innerHeight) el.scrollIntoView({ block: 'center' });
  };
  const stop = () => { stopped = true; };
  const userMoved = () => stop();
  window.addEventListener('wheel', userMoved, { passive: true, once: true });
  window.addEventListener('touchmove', userMoved, { passive: true, once: true });
  window.addEventListener('keydown', userMoved, { once: true });

  const tick = () => {
    if (stopped) return;
    const age = Date.now() - started;
    if (!scrolled && entry?.scrollY) {
      const room = document.documentElement.scrollHeight - window.innerHeight;
      if (room >= entry.scrollY - 2 || age > 3000) {
        window.scrollTo(0, Math.min(entry.scrollY, Math.max(0, room)));
        scrolled = true;
      }
    }
    if (!lit) {
      // The row and the control can appear at different times (a panel draws after its
      // data arrives), so each is looked for until found.
      if (wantRow && !rowEl) {
        rowEl = navFrom ? null : findSig(entry!.row!, document);
        if (rowEl) { flash(rowEl); bringIntoView(rowEl); }
      }
      if (wantTarget && !targetEl) {
        targetEl = navFrom ? linkTo(navFrom) : findSig(entry!.target!, document);
        if (targetEl) {
          const itsRow = rowOf(targetEl);
          if (navFrom && itsRow) { rowEl = itsRow; flash(itsRow); bringIntoView(itsRow); }
          else if (!(rowEl && rowEl.contains(targetEl))) { flash(targetEl); if (!rowEl) bringIntoView(targetEl); }
        }
      }
      lit = (!wantRow || !!rowEl) && (!wantTarget || !!targetEl);
    }
    if ((scrolled && lit) || age > 5000) { cleanup(); return; }
    timer = window.setTimeout(tick, 120);
  };
  let timer = window.setTimeout(tick, 0);
  function cleanup() {
    window.clearTimeout(timer);
    window.removeEventListener('wheel', userMoved);
    window.removeEventListener('touchmove', userMoved);
    window.removeEventListener('keydown', userMoved);
  }
  return () => { stop(); cleanup(); };
}

/** "‹ Back to …" under the menu on every staff page except the control room. */
export function BackBar() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();
  if (!staffPage(location.pathname) || NO_BAR.has(location.pathname)) return null;

  const idx = currentIdx();
  const path = location.pathname + location.search;
  const back = backTarget(idx, path);
  const home = homeFor(user?.role);
  const up = back ? null : parentOf(path, home);
  const words = back ? (back.entry.label || label(back.entry.path)) : label(up as string);

  const go = () => {
    // Note where this page was, so Forward (or coming back again) finds it too.
    updateEntry(idx, { scrollY: Math.round(window.scrollY) });
    if (back) navigate(back.steps);
    else navigate(up as string, { replace: true, state: { navFrom: path } });
  };

  return (
    // Sticky under the menu (h-12): on a long iPhone page Back is always one tap away.
    <div data-nav-back className="nav-back-bar sticky top-12 z-40 px-4 sm:px-6 lg:px-8 border-b border-[#2a2a5e] bg-[#10103a]">
      <button type="button" onClick={go} data-testid="nav-back"
        className="inline-flex items-center gap-1.5 min-h-[40px] sm:min-h-[34px] pr-3 text-sm font-semibold text-cyan-300 hover:text-white"
        aria-label={`Back to ${words}`}>
        <span aria-hidden="true" className="text-lg leading-none">‹</span>
        <span className="truncate max-w-[70vw]">Back to {words}</span>
      </button>
    </div>
  );
}
