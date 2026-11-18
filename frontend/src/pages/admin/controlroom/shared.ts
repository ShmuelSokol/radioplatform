import { useEffect, useState, useCallback } from 'react';

export interface CtlAsset {
  id: string;
  title: string;
  artist?: string | null;
  duration?: number | null;
  asset_type?: string;
  category?: string | null;
}

/** How an asset is sent to air. */
export type SendHow = 'now' | 'next' | 'end' | 'insert' | 'mix';
/** What a tap on a cart button does. 'mix' plays it over the item on air, which carries on. */
export type FireMode = 'now' | 'next' | 'mix';

export type SendFn = (asset: CtlAsset, how: SendHow) => void;

export function fmtDur(sec: number | null | undefined): string {
  if (!sec || sec <= 0) return '0:00';
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, '0')}`;
}

export function fmtHMS(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

export function isValidTz(tz: string): boolean {
  try { Intl.DateTimeFormat(undefined, { timeZone: tz }); return true; } catch { return false; }
}

export function fmtAgo(iso: string | null | undefined): string {
  if (!iso) return '';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function assetLabel(a: { title: string; artist?: string | null } | null | undefined): string {
  if (!a) return '';
  // Title first, so a narrow screen cuts off the artist rather than the title.
  return a.artist ? `${a.title} — ${a.artist}` : a.title;
}

/** Text + chip colours per asset type. Unknown types fall back to gray. */
export const TYPE_STYLE: Record<string, { text: string; chip: string; button: string }> = {
  music: { text: 'text-cyan-300', chip: 'bg-cyan-900/40 text-cyan-200', button: 'bg-cyan-900/40 border-cyan-700 text-cyan-100 hover:bg-cyan-800/60' },
  spot: { text: 'text-orange-300', chip: 'bg-orange-900/40 text-orange-200', button: 'bg-orange-900/40 border-orange-700 text-orange-100 hover:bg-orange-800/60' },
  shiur: { text: 'text-purple-300', chip: 'bg-purple-900/40 text-purple-200', button: 'bg-purple-900/40 border-purple-700 text-purple-100 hover:bg-purple-800/60' },
  jingle: { text: 'text-yellow-200', chip: 'bg-yellow-900/40 text-yellow-100', button: 'bg-yellow-900/40 border-yellow-700 text-yellow-100 hover:bg-yellow-800/60' },
  zmanim: { text: 'text-green-300', chip: 'bg-green-900/40 text-green-200', button: 'bg-green-900/40 border-green-700 text-green-100 hover:bg-green-800/60' },
  silence: { text: 'text-red-400', chip: 'bg-red-900/40 text-red-200', button: 'bg-red-900/40 border-red-700 text-red-100 hover:bg-red-800/60' },
};
const FALLBACK_STYLE = { text: 'text-gray-400', chip: 'bg-white/10 text-gray-300', button: 'bg-white/5 border-white/20 text-gray-100 hover:bg-white/10' };
export const typeStyle = (t: string | null | undefined) => (t && TYPE_STYLE[t]) || FALLBACK_STYLE;

/** Phone held sideways: wider than tall, at most 500 px tall, under the desktop width.
 * The page decides (usePhoneLandscape) and sets html.cr-land; classic.css follows that
 * class, so there is one query only. */
// Up to 720 px tall, not only phones: a car screen (Tesla browser) or small tablet held
// wide gets the queue at full height beside the transport (Shmuel, 2026-11-18: only 3
// songs showed in the car with the transport stacked above the queue).
export const PHONE_LANDSCAPE = '(orientation: landscape) and (max-height: 720px) and (max-width: 1023.98px)';

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(query).matches
      : false,
  );
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setMatches(mq.matches);
    onChange();
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [query]);
  return matches;
}

const isTypingField = (el: Element | null): boolean => {
  if (!el) return false;
  if (el.tagName === 'TEXTAREA' || (el as HTMLElement).isContentEditable) return true;
  return el.tagName === 'INPUT' && !/^(button|checkbox|radio|range|submit|reset|file|color|image)$/i.test((el as HTMLInputElement).type);
};
const screenUpright = (): boolean => {
  try { return !!window.screen?.orientation?.type?.startsWith('portrait'); } catch { return false; }
};

/**
 * True when the control room should use its sideways layout (PHONE_LANDSCAPE).
 * Android shrinks the window when the on-screen keyboard opens, so a phone held upright
 * can look wider than tall while typing; with the screen upright and a text field in
 * use, the layout stays as it is. (iPhone does not resize the window for the keyboard.)
 */
export function usePhoneLandscape(): boolean {
  const wide = useMediaQuery(PHONE_LANDSCAPE);
  const [typingUpright, setTypingUpright] = useState(false);
  useEffect(() => {
    const check = () => setTypingUpright(isTypingField(document.activeElement) && screenUpright());
    const later = () => setTimeout(check, 0);
    check();
    document.addEventListener('focusin', check);
    document.addEventListener('focusout', later);
    window.addEventListener('resize', check);
    const so = window.screen?.orientation;
    so?.addEventListener?.('change', check);
    return () => {
      document.removeEventListener('focusin', check);
      document.removeEventListener('focusout', later);
      window.removeEventListener('resize', check);
      so?.removeEventListener?.('change', check);
    };
  }, []);
  return wide && !typingUpright;
}

export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch { return fallback; }
}

/** A value remembered in this browser. */
export function useStored<T>(key: string, fallback: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => readJson(key, fallback));
  const set = useCallback((v: T) => {
    setValue(v);
    try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* private mode */ }
  }, [key]);
  return [value, set];
}

export const CART_PAGES = 10;

/** A named set of instant buttons: ten pages, like the cart operators know. */
export interface CartSet { name: string; pages: CtlAsset[][] }
interface CartState { sets: CartSet[]; set: number; page: number }

const emptyPages = (): CtlAsset[][] => Array.from({ length: CART_PAGES }, () => []);
const DEFAULT_CART: CartState = { sets: [{ name: 'Default', pages: emptyPages() }], set: 0, page: 0 };

function readCart(key: string): CartState {
  const raw = readJson<unknown>(key, null);
  // Older builds stored one flat list of buttons: keep it as page 1 of the first set.
  if (Array.isArray(raw)) {
    const pages = emptyPages();
    pages[0] = raw as CtlAsset[];
    return { sets: [{ name: 'Default', pages }], set: 0, page: 0 };
  }
  const c = raw as Partial<CartState> | null;
  if (!c || !Array.isArray(c.sets) || c.sets.length === 0) return DEFAULT_CART;
  const sets = c.sets.map(s => ({
    name: String(s?.name || 'Set'),
    pages: emptyPages().map((_, i) => (Array.isArray(s?.pages?.[i]) ? s.pages[i] : [])),
  }));
  const set = Math.min(Math.max(0, Number(c.set) || 0), sets.length - 1);
  const page = Math.min(Math.max(0, Number(c.page) || 0), CART_PAGES - 1);
  return { sets, set, page };
}

/**
 * Operator-pinned instant buttons, per station: named sets of ten pages each.
 * Stored in this browser only for now (a phone and a desktop each keep their own).
 */
export function useHotButtons(stationId: string | null) {
  const key = `kbr_hot_buttons_${stationId ?? 'none'}`;
  const [cart, setCart] = useState<CartState>(() => readCart(key));
  useEffect(() => { setCart(readCart(key)); }, [key]);

  const update = useCallback((fn: (c: CartState) => CartState) => {
    setCart(prev => {
      const next = fn(prev);
      try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* private mode */ }
      return next;
    });
  }, [key]);

  /** Add the asset to the page on screen, or take it off if it is already there. */
  const toggle = useCallback((asset: CtlAsset) => update(c => {
    const cur = c.sets[c.set].pages[c.page];
    const nextPage = cur.some(p => p.id === asset.id)
      ? cur.filter(p => p.id !== asset.id)
      : [...cur, {
          id: asset.id, title: asset.title, artist: asset.artist ?? null,
          duration: asset.duration ?? null, asset_type: asset.asset_type,
          category: asset.category ?? null,
        }];
    const sets = c.sets.map((s, si) => si !== c.set ? s : { ...s, pages: s.pages.map((pg, pi) => (pi === c.page ? nextPage : pg)) });
    return { ...c, sets };
  }), [update]);

  const selectPage = useCallback((page: number) => update(c => ({ ...c, page })), [update]);
  const selectSet = useCallback((set: number) => update(c => ({ ...c, set, page: 0 })), [update]);
  const addSet = useCallback((name: string) => update(c => ({ sets: [...c.sets, { name, pages: emptyPages() }], set: c.sets.length, page: 0 })), [update]);
  const renameSet = useCallback((name: string) => update(c => ({ ...c, sets: c.sets.map((s, i) => (i === c.set ? { ...s, name } : s)) })), [update]);
  const deleteSet = useCallback(() => update(c => {
    if (c.sets.length <= 1) return c;
    return { sets: c.sets.filter((_, i) => i !== c.set), set: 0, page: 0 };
  }), [update]);

  const pins = cart.sets[cart.set].pages[cart.page];
  return { pins, toggle, cart, selectPage, selectSet, addSet, renameSet, deleteSet };
}

/** "3:05:09" and "PM" in the station's time zone, for the running log. */
export function clockTimeSec(d: Date | string | number, tz?: string): { time: string; ampm: string } {
  const parts = cachedFmt('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true, ...tzOpt(tz) }).formatToParts(new Date(d));
  const get = (t: string) => parts.find(x => x.type === t)?.value ?? '';
  return { time: `${get('hour')}:${get('minute')}:${get('second')}`, ampm: get('dayPeriod') };
}

// Date formatters are slow to build (very slow in Safari) and the queue asks
// for thousands of them, so keep one per time zone and shape.
const fmtCache = new Map<string, Intl.DateTimeFormat>();
function cachedFmt(locale: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = locale + JSON.stringify(opts);
  let f = fmtCache.get(key);
  if (!f) { f = new Intl.DateTimeFormat(locale, opts); fmtCache.set(key, f); }
  return f;
}
const tzOpt = (tz?: string) => (tz ? { timeZone: tz } : {});

/** YYYY-MM-DD in the station's time zone. */
export function dayKey(d: Date | string | number, tz?: string): string {
  return cachedFmt('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', ...tzOpt(tz) }).format(new Date(d));
}
/** "3:05 PM" in the station's time zone. */
export function clockTime(d: Date | string | number, tz?: string): string {
  return cachedFmt('en-US', { hour: 'numeric', minute: '2-digit', hour12: true, ...tzOpt(tz) }).format(new Date(d));
}
/** "Mon" in the station's time zone. */
export function weekdayShort(d: Date | string | number, tz?: string): string {
  return cachedFmt('en-US', { weekday: 'short', ...tzOpt(tz) }).format(new Date(d));
}
