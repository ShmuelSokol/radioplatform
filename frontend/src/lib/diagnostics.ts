/**
 * What the browser was doing, kept in memory for bug reports.
 *
 * Installed once at startup (main.tsx). Keeps the last few hundred console
 * lines, uncaught errors, API calls (with the X-Request-ID the server logs
 * them under), page changes and audio events, plus every audio element and
 * AudioContext the page made, so a report can say which players were running.
 * Nothing leaves the browser until someone presses "Report a bug".
 * See wiki/bug-reports.md.
 */
import type { QueryClient } from '@tanstack/react-query';

type Entry = Record<string, unknown> & { at: string };

const MAX_CONSOLE = 400;
const MAX_NETWORK = 300;
const MAX_CRUMBS = 300;

const consoleLog: Entry[] = [];
const network: Entry[] = [];
const breadcrumbs: Entry[] = [];
// Plain references, capped: old players drop out when new ones are made.
const mediaElements = new Set<HTMLMediaElement>();
const audioContexts = new Set<AudioContext>();
const MAX_TRACKED = 30;

function track<T>(set: Set<T>, item: T) {
  if (set.has(item)) return;
  set.add(item);
  if (set.size > MAX_TRACKED) set.delete(set.values().next().value as T);
}
let queryClientRef: QueryClient | null = null;
let installed = false;
const startedAt = new Date().toISOString();

function now() {
  return new Date().toISOString();
}

function push(list: Entry[], max: number, e: Omit<Entry, 'at'>) {
  list.push({ at: now(), ...e });
  if (list.length > max) list.splice(0, list.length - max);
}

function stringify(v: unknown): string {
  if (typeof v === 'string') return v;
  if (v instanceof Error) return `${v.name}: ${v.message}${v.stack ? '\n' + v.stack.split('\n').slice(0, 6).join('\n') : ''}`;
  try {
    return JSON.stringify(v, (_k, val) => (typeof val === 'string' && val.length > 500 ? val.slice(0, 500) + '…' : val));
  } catch {
    return String(v);
  }
}

/** Add an app event to the timeline ("switched to station X", "pressed Next"). */
export function addBreadcrumb(kind: string, detail?: Record<string, unknown>) {
  push(breadcrumbs, MAX_CRUMBS, { kind, ...detail });
}

/** Short random id the server logs the request under. */
export function newRequestId(): string {
  return 'c' + Math.random().toString(36).slice(2, 9);
}

export function recordRequest(e: { method?: string; url?: string; status?: number; ms?: number; rid?: string; error?: string }) {
  push(network, MAX_NETWORK, e);
}

export function setQueryClient(qc: QueryClient) {
  queryClientRef = qc;
}

export function installDiagnostics() {
  if (installed || typeof window === 'undefined') return;
  installed = true;

  for (const level of ['log', 'info', 'warn', 'error', 'debug'] as const) {
    const orig = console[level].bind(console);
    console[level] = (...args: unknown[]) => {
      try {
        push(consoleLog, MAX_CONSOLE, { level, msg: args.map(stringify).join(' ').slice(0, 2000) });
      } catch {
        /* never break logging */
      }
      orig(...args);
    };
  }

  window.addEventListener('error', (ev) => {
    push(consoleLog, MAX_CONSOLE, {
      level: 'error',
      msg: `Uncaught ${ev.message} at ${ev.filename}:${ev.lineno}:${ev.colno}${ev.error?.stack ? '\n' + String(ev.error.stack).slice(0, 1500) : ''}`,
    });
  });
  window.addEventListener('unhandledrejection', (ev) => {
    push(consoleLog, MAX_CONSOLE, { level: 'error', msg: `Unhandled rejection: ${stringify(ev.reason).slice(0, 1500)}` });
  });

  // Every audio/video element, including `new Audio()` ones that never join the page.
  try {
    const origPlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function (this: HTMLMediaElement) {
      try {
        track(mediaElements, this);
        addBreadcrumb('media.play', { src: shortSrc(this.currentSrc || this.src), at_s: round(this.currentTime) });
      } catch { /* ignore */ }
      return origPlay.call(this);
    };
    const origPause = HTMLMediaElement.prototype.pause;
    HTMLMediaElement.prototype.pause = function (this: HTMLMediaElement) {
      try { addBreadcrumb('media.pause', { src: shortSrc(this.currentSrc || this.src), at_s: round(this.currentTime) }); } catch { /* ignore */ }
      return origPause.call(this);
    };
  } catch { /* ignore */ }

  // Every AudioContext (the player mixes through one).
  try {
    const W = window as unknown as { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext };
    for (const key of ['AudioContext', 'webkitAudioContext'] as const) {
      const Orig = W[key];
      if (!Orig) continue;
      const Wrapped = class extends Orig {
        constructor(...args: ConstructorParameters<typeof AudioContext>) {
          super(...args);
          track(audioContexts, this as AudioContext);
          addBreadcrumb('audiocontext.new', { sampleRate: this.sampleRate });
        }
      };
      W[key] = Wrapped;
    }
  } catch { /* ignore */ }

  // Page changes in this single-page app
  let lastPath = location.pathname + location.search;
  addBreadcrumb('page', { path: lastPath });
  setInterval(() => {
    const p = location.pathname + location.search;
    if (p !== lastPath) {
      lastPath = p;
      addBreadcrumb('page', { path: p });
    }
  }, 1000);

  // Plain fetch calls (axios is recorded in api/client.ts with its request id)
  try {
    const origFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const t0 = performance.now();
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      const method = (init?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
      try {
        const res = await origFetch(input, init);
        recordRequest({ method, url: shortUrl(url), status: res.status, ms: Math.round(performance.now() - t0), via: 'fetch' } as never);
        return res;
      } catch (err) {
        recordRequest({ method, url: shortUrl(url), error: stringify(err).slice(0, 300), ms: Math.round(performance.now() - t0) });
        throw err;
      }
    };
  } catch { /* ignore */ }
}

function round(n: number) {
  return Math.round(n * 10) / 10;
}

function shortSrc(src: string): string {
  if (!src) return '';
  if (src.startsWith('blob:')) return 'blob:…';
  if (src.startsWith('data:')) return src.slice(0, 30) + '…';
  try {
    const u = new URL(src);
    return (u.host + u.pathname).slice(-160);
  } catch {
    return src.slice(-160);
  }
}

function shortUrl(url: string): string {
  try {
    const u = new URL(url, location.href);
    return (u.origin === location.origin ? '' : u.host) + u.pathname + u.search.slice(0, 120);
  } catch {
    return url.slice(0, 200);
  }
}

function mediaState() {
  const out: Record<string, unknown>[] = [];
  const seen = new Set<HTMLMediaElement>();
  const add = (el: HTMLMediaElement, inPage: boolean) => {
    if (seen.has(el)) return;
    seen.add(el);
    out.push({
      tag: el.tagName.toLowerCase(),
      in_page: inPage,
      src: shortSrc(el.currentSrc || el.src),
      paused: el.paused,
      ended: el.ended,
      muted: el.muted,
      volume: round(el.volume),
      current_s: round(el.currentTime),
      duration_s: Number.isFinite(el.duration) ? round(el.duration) : String(el.duration),
      ready_state: el.readyState,
      network_state: el.networkState,
      error: el.error ? `${el.error.code} ${el.error.message}` : null,
    });
  };
  document.querySelectorAll('audio,video').forEach((el) => add(el as HTMLMediaElement, true));
  mediaElements.forEach((el) => add(el, el.isConnected));
  return out;
}

function audioContextState() {
  const out: Record<string, unknown>[] = [];
  audioContexts.forEach((ctx) => {
    out.push({
      state: ctx.state,
      sampleRate: ctx.sampleRate,
      current_s: round(ctx.currentTime),
      base_latency: (ctx as AudioContext).baseLatency,
      destination_channels: ctx.destination.channelCount,
      destination_max_channels: ctx.destination.maxChannelCount,
      channel_interpretation: ctx.destination.channelInterpretation,
    });
  });
  return out;
}

/** React Query data that tells what the screen was showing: queue, now playing, stations. */
function queryState() {
  if (!queryClientRef) return [];
  const wanted = /queue|now|playing|station|played|schedule/i;
  return queryClientRef
    .getQueryCache()
    .getAll()
    .filter((q) => wanted.test(JSON.stringify(q.queryKey)))
    .slice(0, 25)
    .map((q) => ({
      key: q.queryKey,
      status: q.state.status,
      updated: q.state.dataUpdatedAt ? new Date(q.state.dataUpdatedAt).toISOString() : null,
      error: q.state.error ? stringify(q.state.error).slice(0, 300) : null,
      data: trimData(q.state.data),
    }));
}

function trimData(data: unknown, depth = 0): unknown {
  if (data == null || typeof data !== 'object') {
    return typeof data === 'string' && data.length > 300 ? data.slice(0, 300) + '…' : data;
  }
  if (depth > 4) return '…';
  if (Array.isArray(data)) {
    const items = data.slice(0, 30).map((x) => trimData(x, depth + 1));
    return data.length > 30 ? [...items, `… ${data.length - 30} more`] : items;
  }
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(data as Record<string, unknown>).slice(0, 40)) {
    out[k] = trimData(v, depth + 1);
  }
  return out;
}

/** Everything a bug report carries besides the screenshot and the user's words. */
export function collectContext(extra?: Record<string, unknown>) {
  let storage: Record<string, string> = {};
  try {
    storage = Object.fromEntries(
      Object.keys(localStorage)
        .filter((k) => !/token|password|secret|auth/i.test(k) && !(localStorage.getItem(k) || '').includes('eyJ'))
        .map((k) => [k, (localStorage.getItem(k) || '').slice(0, 300)]),
    );
  } catch { /* private mode */ }
  return {
    page_loaded_at: startedAt,
    reported_at: now(),
    url: location.href,
    viewport: `${window.innerWidth}x${window.innerHeight} @${window.devicePixelRatio}x`,
    screen: `${screen.width}x${screen.height}`,
    visibility: document.visibilityState,
    online: navigator.onLine,
    language: navigator.language,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    build: (document.querySelector('script[type="module"][src*="/assets/"]') as HTMLScriptElement | null)?.src?.split('/').pop() ?? null,
    audio: mediaState(),
    audio_contexts: audioContextState(),
    queries: queryState(),
    local_storage: storage,
    breadcrumbs: [...breadcrumbs],
    console: [...consoleLog],
    network: [...network],
    ...extra,
  };
}
