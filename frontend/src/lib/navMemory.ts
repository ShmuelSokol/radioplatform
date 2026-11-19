/**
 * "Back to where I was" for staff pages (Shmuel 2026-11-19: Back from a spot opened from
 * the dashboard goes back to the dashboard with the last thing he tapped highlighted).
 *
 * Kept per browser tab in sessionStorage, one record per entry of the tab's own history
 * (React Router numbers them: `history.state.idx`):
 *  - the page's address and heading (the Back button's words: "Back to Sponsors"),
 *  - how far down it was scrolled,
 *  - the row and the control last tapped on it (found again by id or by their words),
 *  - page state kept with `useNavState` (filters, search, open panel).
 * Pages whose thing was deleted are remembered as gone, and Back steps over them.
 * See wiki/back-navigation.md.
 */

const KEY = 'kbr_nav_v1';

/** How to find an element again when the page is drawn anew. */
export interface Sig {
  navId?: string;
  testid?: string;
  tag: string;
  text: string;
}

export interface NavEntry {
  path: string;      // pathname + search
  key: string;       // React Router location key
  label?: string;    // the page's heading when it was left
  scrollY?: number;
  row?: Sig;         // the row (or card) last tapped
  target?: Sig;      // the link or button last tapped
}

interface Store {
  entries: Record<string, NavEntry>;
  state: Record<string, unknown>;
  gone: string[];
}

let cache: Store | null = null;

function load(): Store {
  if (cache) return cache;
  try {
    const raw = sessionStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === 'object' && parsed.entries) {
      cache = { entries: parsed.entries || {}, state: parsed.state || {}, gone: parsed.gone || [] };
      return cache;
    }
  } catch { /* private mode or bad data */ }
  cache = { entries: {}, state: {}, gone: [] };
  return cache;
}

function save(): void {
  try { sessionStorage.setItem(KEY, JSON.stringify(load())); } catch { /* full or private mode */ }
}

/** Position of the current page in this tab's history (0 = the first page opened). */
export function currentIdx(): number {
  try {
    const idx = (window.history.state as { idx?: number } | null)?.idx;
    return typeof idx === 'number' ? idx : 0;
  } catch { return 0; }
}

export function entryAt(idx: number): NavEntry | undefined {
  return load().entries[String(idx)];
}

/** A new address became the current entry. PUSH forgets everything after it. */
export function recordArrival(idx: number, path: string, key: string, type: 'PUSH' | 'POP' | 'REPLACE'): NavEntry {
  const s = load();
  const prev = s.entries[String(idx)];
  if (type === 'PUSH') {
    for (const k of Object.keys(s.entries)) if (Number(k) >= idx) delete s.entries[k];
    for (const k of Object.keys(s.state)) {
      const i = Number(k.split('|')[0]);
      if (i > idx || (i === idx && !k.startsWith(`${idx}|${pathOnly(path)}|`))) delete s.state[k];
    }
  }
  // A REPLACE (a filter written into the address) keeps what was noted about the page.
  const keep = prev && pathOnly(prev.path) === pathOnly(path) && type !== 'PUSH';
  const entry: NavEntry = keep ? { ...prev, path, key } : { path, key };
  s.entries[String(idx)] = entry;
  // Keep the store small: nobody goes back 80 pages.
  const all = Object.keys(s.entries).map(Number).sort((a, b) => a - b);
  for (const k of all.slice(0, Math.max(0, all.length - 80))) delete s.entries[String(k)];
  save();
  return entry;
}

export function updateEntry(idx: number, patch: Partial<NavEntry>): void {
  const s = load();
  const e = s.entries[String(idx)];
  if (!e) return;
  Object.assign(e, patch);
  save();
}

export function pathOnly(path: string): string {
  const q = path.indexOf('?');
  return q < 0 ? path : path.slice(0, q);
}

// ── Gone pages ──────────────────────────────────────────────────────────────

const UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;

/** A delete went through on the server: pages about that thing no longer exist. */
export function markDeleted(apiUrl: string): void {
  // Only a delete of the thing itself (".../<id>"), not of something under it
  // (".../<id>/station-block" leaves the item in place).
  const m = apiUrl.split('?')[0].replace(/\/+$/, '').match(/\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/i);
  if (!m) return;
  const s = load();
  const last = m[1].toLowerCase();
  if (!s.gone.includes(last)) s.gone.push(last);
  if (s.gone.length > 200) s.gone = s.gone.slice(-200);
  save();
}

/** Is this page about something that was deleted in this tab? */
export function isGone(path: string): boolean {
  const s = load();
  if (!s.gone.length) return false;
  const ids = (pathOnly(path).match(UUID) || []).map((x) => x.toLowerCase());
  return ids.some((id) => s.gone.includes(id));
}

// ── Page state kept per history entry ───────────────────────────────────────

export function stateKey(idx: number, pathname: string, name: string): string {
  return `${idx}|${pathname}|${name}`;
}

export function readState<T>(key: string): { found: boolean; value?: T } {
  const s = load();
  return key in s.state ? { found: true, value: s.state[key] as T } : { found: false };
}

export function writeState(key: string, value: unknown): void {
  const s = load();
  if (value === undefined) delete s.state[key];
  else s.state[key] = value;
  save();
}

export function dropState(key: string): void {
  const s = load();
  if (key in s.state) { delete s.state[key]; save(); }
}

// ── Where Back goes ─────────────────────────────────────────────────────────

const NAMES: [RegExp, string][] = [
  [/^\/admin\/dashboard$/, 'Dashboard'],
  [/^\/admin\/sponsors$/, 'Sponsors'],
  [/^\/admin\/sponsors\/[^/]+$/, 'client'],
  [/^\/admin\/campaigns$/, 'Campaigns'],
  [/^\/admin\/campaigns\/[^/]+$/, 'campaign'],
  [/^\/admin\/spot-schedule$/, 'Spot Schedule'],
  [/^\/admin\/spot-recordings$/, 'Record Spots'],
  [/^\/admin\/assets$/, 'Library'],
  [/^\/admin\/assets\/upload$/, 'Upload'],
  [/^\/admin\/assets\/[^/]+$/, 'item'],
  [/^\/admin\/schedules$/, 'Schedules'],
  [/^\/admin\/schedules\/[^/]+\/blocks$/, 'schedule blocks'],
  [/^\/admin\/reviews$/, 'Reviews'],
  [/^\/admin\/live$/, 'Live Shows'],
  [/^\/admin\/databank$/, 'Databank'],
  [/^\/admin\/program-blocks$/, 'Program Blocks'],
  [/^\/admin\/schedule-check$/, 'Schedule Check'],
];

/** Words for a page when its heading was not noted ('' = not in this list). */
export function pageName(path: string): string {
  const p = pathOnly(path);
  for (const [re, name] of NAMES) if (re.test(p)) return name;
  return '';
}

/**
 * The natural page above this one, for a page opened straight from a link (no earlier
 * page in this tab). A page opened for one client goes back to that client.
 */
export function parentOf(path: string, home: string): string {
  const p = pathOnly(path);
  const params = new URLSearchParams(path.includes('?') ? path.slice(path.indexOf('?') + 1) : '');
  const sponsor = params.get('sponsor');
  if (sponsor && /^\/admin\/(spot-schedule|spot-recordings|campaigns)$/.test(p)) return `/admin/sponsors/${sponsor}`;
  const rules: [RegExp, string][] = [
    [/^\/admin\/sponsors\/[^/]+$/, '/admin/sponsors'],
    [/^\/admin\/campaigns\/[^/]+$/, '/admin/campaigns'],
    [/^\/admin\/assets\/.+$/, '/admin/assets'],
    [/^\/admin\/reviews\/.+$/, '/admin/reviews'],
    [/^\/admin\/schedules\/.+$/, '/admin/schedules'],
    [/^\/admin\/live\/.+$/, '/admin/live'],
    [/^\/admin\/(spot-schedule|spot-recordings|sponsorship-rules|campaigns)$/, '/admin/sponsors'],
  ];
  for (const [re, to] of rules) if (re.test(p)) return to;
  return home;
}

/**
 * Where Back goes from the current entry: the nearest earlier page in this tab that is a
 * different page (switching a tab or filter on one page is not "where I came from") and
 * was not deleted. `steps` is how far back (negative) or null for "no such page".
 */
export function backTarget(idx: number, path: string): { steps: number; entry: NavEntry } | null {
  const here = pathOnly(path);
  for (let i = idx - 1; i >= 0; i--) {
    const e = entryAt(i);
    if (!e) return null; // history from before this tab kept notes: do not guess
    if (pathOnly(e.path) === here) continue;
    if (isGone(e.path)) continue;
    return { steps: i - idx, entry: e };
  }
  return null;
}

// ── Finding the tapped row again ────────────────────────────────────────────

function words(el: Element): string {
  return (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120);
}

export function sigOf(el: Element): Sig {
  return {
    navId: el.getAttribute('data-nav-id') || undefined,
    testid: el.getAttribute('data-testid') || undefined,
    tag: el.tagName.toLowerCase(),
    text: words(el),
  };
}

/** The row, card or list line a tap was in (the "selection" to show on return). */
export function rowOf(el: Element): Element | null {
  return el.closest('[data-nav-id], tr, li, [role="row"], [data-testid$="-row"]');
}

/** The link or button that was tapped. */
export function targetOf(el: Element): Element | null {
  return el.closest('a, button, [role="button"], [role="tab"]');
}

function visible(el: Element): boolean {
  const r = (el as HTMLElement).getBoundingClientRect?.();
  return !!r && (r.width > 0 || r.height > 0);
}

/** Find an element again from its notes; null when it is not (yet) on the page. */
export function findSig(sig: Sig, root: ParentNode = document): Element | null {
  const esc = (v: string) => (window.CSS && CSS.escape ? CSS.escape(v) : v.replace(/"/g, '\\"'));
  if (sig.navId) {
    const all = Array.from(root.querySelectorAll(`[data-nav-id="${esc(sig.navId)}"]`)).filter(visible);
    if (all.length) return all[0];
    return null; // it has an id and is not here: do not guess by words
  }
  const pool = sig.testid
    ? Array.from(root.querySelectorAll(`[data-testid="${esc(sig.testid)}"]`))
    : Array.from(root.querySelectorAll(sig.tag));
  const shown = pool.filter(visible);
  if (!sig.text) return shown.length === 1 ? shown[0] : null;
  const exact = shown.find((el) => words(el) === sig.text);
  if (exact) return exact;
  // Rows carry counts and times that move; the first words (the name) stay.
  const head = sig.text.slice(0, 32);
  const near = shown.filter((el) => words(el).startsWith(head));
  if (near.length === 1) return near[0];
  // A named control that is the only one of its kind ("Sponsors" became "Sponsor page"
  // once the panel knew the sponsor) is the same control.
  return sig.testid && shown.length === 1 ? shown[0] : null;
}

/** The link to `path` on this page, and its row. Used after Back took a page's parent. */
export function linkTo(path: string): Element | null {
  const p = pathOnly(path);
  const links = Array.from(document.querySelectorAll('a[href]')).filter((a) => {
    const href = a.getAttribute('href') || '';
    return pathOnly(href) === p && visible(a) && !a.closest('[data-site-nav], [data-nav-back]');
  });
  return links[0] || null;
}

/**
 * Light up an element so the eye finds it: a bright flash, then a quiet yellow outline
 * that stays until another row is tapped, so the row behind a panel that opened again
 * (the control room's Sponsor panel on a phone) is still marked when the panel closes.
 */
export function flash(el: Element): void {
  el.classList.remove('nav-return-flash');
  // restart the animation if it is still running
  void (el as HTMLElement).offsetWidth;
  el.classList.add('nav-return-flash', 'nav-return-mark');
  el.setAttribute('data-nav-returned', '1');
  window.setTimeout(() => el.classList.remove('nav-return-flash'), 2600);
}

/** A new row was tapped: the old "you came from here" marks go. */
export function clearMarks(): void {
  document.querySelectorAll('.nav-return-mark').forEach((el) => {
    el.classList.remove('nav-return-mark', 'nav-return-flash');
    el.removeAttribute('data-nav-returned');
  });
}

/** The page's own heading, for "Back to <heading>". */
export function pageHeading(): string {
  const h = document.querySelector('main h1') || document.querySelector('main h2');
  const t = h ? words(h) : '';
  return t.length > 0 && t.length <= 48 ? t : '';
}

/** For tests: forget everything. */
export function resetNavMemory(): void {
  cache = { entries: {}, state: {}, gone: [] };
  save();
}
