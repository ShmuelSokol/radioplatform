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
export type SendHow = 'now' | 'next' | 'end' | 'insert';
export type FireMode = 'now' | 'next';

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

/**
 * Operator-pinned instant buttons, per station. Stored in this browser only
 * for now (a phone and a desktop each keep their own set).
 */
export function useHotButtons(stationId: string | null) {
  const key = `kbr_hot_buttons_${stationId ?? 'none'}`;
  const [pins, setPins] = useState<CtlAsset[]>(() => readJson<CtlAsset[]>(key, []));
  useEffect(() => { setPins(readJson<CtlAsset[]>(key, [])); }, [key]);

  const toggle = useCallback((asset: CtlAsset) => {
    setPins(prev => {
      const next = prev.some(p => p.id === asset.id)
        ? prev.filter(p => p.id !== asset.id)
        : [...prev, {
            id: asset.id, title: asset.title, artist: asset.artist ?? null,
            duration: asset.duration ?? null, asset_type: asset.asset_type,
            category: asset.category ?? null,
          }];
      try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* private mode */ }
      return next;
    });
  }, [key]);

  return { pins, toggle };
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
