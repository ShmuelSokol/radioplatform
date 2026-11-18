/**
 * Picks up a new release of the site without anyone refreshing by hand, and
 * without ever interrupting audio.
 *
 * Every build publishes /version.json. Each open tab checks it once a minute.
 * When the published version differs from the one the tab is running, the tab
 * reloads itself at the first moment that costs the person nothing: no audio
 * playing, no microphone live, nothing being typed, and either the tab is in
 * the background, the person has been idle, or they are moving to another
 * screen anyway. A listener in the middle of a song keeps listening on the
 * version they have and is updated when playback stops.
 *
 * A reload silences a listener for good (browsers refuse to start audio again
 * without a tap), so "audio playing" is read generously: a player that is
 * switched on holds updates even between items (holdUpdates), and any audio
 * heard in the last two minutes counts too, which covers the short gap at a
 * song change or before the hourly time and weather.
 *
 * Admins control this from Settings > Site Updates (backend /site-release).
 * Every tab keeps a WebSocket to /ws/site: it reports its release, screen and
 * whether it is playing, and hears the policy the moment an admin changes it:
 * - mode "auto": the behaviour above;
 * - mode "manual": no tab reloads on its own;
 * - a push: tabs take the named release at the set time (or now) without
 *   waiting for the person to go idle. Tabs playing audio wait until they stop,
 *   unless the push includes them: then they reload right after the next item
 *   change (or after 5 minutes of one long item) and play on from where the
 *   station is (takeResume). Browsers that refuse to start audio without a tap
 *   (Safari, iPhone) show a "tap to keep listening" prompt instead.
 */
import { noteServerTime, serverNow } from './serverClock';

declare const __APP_VERSION__: string;

const CHECK_EVERY_MS = 60_000;
const APPLY_EVERY_MS = 5_000;
const IDLE_BEFORE_RELOAD_MS = 30_000;
const AUDIO_GRACE_MS = 120_000;
// Hidden this long and silent: take the new release on return even if a player is switched on
const LONG_AWAY_MS = 10 * 60_000;

const media = new Set<HTMLMediaElement>();
const micStreams = new Set<MediaStream>();
const holds = new Set<symbol>();
let lastInput = Date.now();
let lastAudioAt = 0;
let pending = false;
let started = false;
let publishedNow: string | null = null;
let lastItemChangeAt = 0;

// ── Admin release policy (backend /site-release) ──
export interface ReleasePush {
  id: string;
  version: string | null;
  at: string | null;
  include_playing: boolean;
  pushed_at: string | null;
  pushed_by: string | null;
}
export interface ReleasePolicy {
  mode: 'auto' | 'manual';
  push: ReleasePush | null;
  server_time?: string;
}
let policy: ReleasePolicy = { mode: 'auto', push: null };
let pushDueSince = 0;
let waitingFor: string | null = null;
const policyListeners = new Set<(p: ReleasePolicy) => void>();

// A push for "whatever is published" (no version named) lasts this long
const UNNAMED_PUSH_LASTS_MS = 60 * 60_000;
// A playing tab included in a push waits at most this long for an item change
const PLAYING_PUSH_MAX_WAIT_MS = 5 * 60_000;
// An item change counts as "just now" for this long
const ITEM_CHANGE_WINDOW_MS = 8_000;
// Never reload for a release more often than this (a stale CDN copy would loop)
const MIN_RELOAD_GAP_MS = 2 * 60_000;
const RELOAD_KEY = 'kbr_release_reload_at';
const RESUME_KEY = 'kbr_resume_playback';
const RESUME_FRESH_MS = 90_000;

/** The release this tab runs. */
export const APP_VERSION: string = typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : 'dev';

export function currentPolicy(): ReleasePolicy {
  return policy;
}

/** Hear policy changes (the Site Updates screen). Returns the unsubscribe. */
export function onPolicy(fn: (p: ReleasePolicy) => void): () => void {
  policyListeners.add(fn);
  return () => { policyListeners.delete(fn); };
}

function setPolicy(next: ReleasePolicy) {
  if (next.server_time) {
    const t = new Date(next.server_time).getTime();
    if (isFinite(t)) noteServerTime(next.server_time);
  }
  const pushChanged = next.push?.id !== policy.push?.id;
  policy = { mode: next.mode === 'manual' ? 'manual' : 'auto', push: next.push ?? null };
  if (pushChanged) {
    pushDueSince = 0;
    if (policy.push) void check(true).then(applyIfQuiet);
  }
  for (const fn of policyListeners) fn(policy);
  reportTab();
}

/** The player started a new item. A push that includes playing tabs reloads now. */
export function noteItemChange() {
  lastItemChangeAt = Date.now();
}

/**
 * True once after a release reload that interrupted playback on this screen:
 * the page should switch its player back on by itself.
 */
export function takeResume(path: string = window.location.pathname): boolean {
  try {
    const raw = sessionStorage.getItem(RESUME_KEY);
    if (!raw) return false;
    const saved = JSON.parse(raw) as { path?: string; at?: number };
    if (saved.path !== path) return false;
    sessionStorage.removeItem(RESUME_KEY);
    return typeof saved.at === 'number' && Date.now() - saved.at < RESUME_FRESH_MS;
  } catch {
    return false;
  }
}

/** When this tab last reloaded for a release. sessionStorage normally; when the
 *  browser refuses it (cookies/site data blocked makes it throw) window.name, which
 *  also survives a reload of the same tab. Without the fallback a blocked storage
 *  turned the guard off and a stale CDN copy reloaded the tab every minute. */
const NAME_TAG = 'kbr_release_reload_at=';
function lastReloadAt(): number {
  try {
    return Number(sessionStorage.getItem(RELOAD_KEY) || 0);
  } catch {
    const m = window.name.match(/kbr_release_reload_at=(\d+)/);
    return m ? Number(m[1]) : 0;
  }
}
function noteReloadAt(at: number) {
  try {
    sessionStorage.setItem(RELOAD_KEY, String(at));
  } catch {
    window.name = NAME_TAG + at;
  }
}

function reloadedRecently(): boolean {
  const age = Date.now() - lastReloadAt();
  // A time "in the future" means the device clock was set back since: ignore it
  // rather than holding every release until the clock catches up.
  return age >= 0 && age < MIN_RELOAD_GAP_MS;
}

/** A screen's code file from an older release no longer exists on the server. Every
 *  merge publishes a new release, so a tab left open (the control room) hits this
 *  when it opens a screen it had not opened yet ("sometimes the Library gives an error"). */
export function isStaleCodeError(error: unknown): boolean {
  const text = error instanceof Error ? `${error.name} ${error.message}` : String(error);
  // The host answers a missing file with the site's own page (200, text/html), and
  // each browser words that differently. Safari: "'text/html' is not a valid
  // JavaScript MIME type." (Shmuel's iPhone, 2026-11-17: it was not in this list, so
  // he got "Something went wrong" and a Reload button instead of the new release.)
  return /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|ChunkLoadError|Loading (CSS )?chunk .* failed|Unable to preload CSS|is not a valid JavaScript MIME type|MIME type of ["']?text\/html|disallowed MIME type|Expected a JavaScript(-or-Wasm)? module script/i.test(text);
}

/** A screen's code could not load because a newer release replaced it (the old
 *  files are gone). Reload onto the new release now unless audio is sounding right
 *  now or a reload just happened. Returns true when a reload was started.
 *  Only sound playing NOW counts, not the grace after audio stopped: the person
 *  usually hits this by leaving the Listen page for another screen, which stops the
 *  player, so the grace would leave them on an error box for nothing. */
export function reloadForStaleCode(): boolean {
  if (holds.size > 0 || soundingNow() || reloadedRecently()) return false;
  reloadForRelease(false);
  return true;
}

function reloadForRelease(resume: boolean) {
  if (reloadedRecently()) return;
  noteReloadAt(Date.now());
  try {
    if (resume) sessionStorage.setItem(RESUME_KEY, JSON.stringify({ path: window.location.pathname, at: Date.now() }));
  } catch { /* private mode: reload anyway */ }
  window.location.reload();
}

/** Remember every audio/video element that is ever played, including ones never added to the page. */
function trackMedia() {
  const proto = HTMLMediaElement.prototype;
  const originalPlay = proto.play;
  proto.play = function (this: HTMLMediaElement) {
    media.add(this);
    lastAudioAt = Date.now();
    return originalPlay.call(this);
  };

  const devices = navigator.mediaDevices;
  if (devices?.getUserMedia) {
    const originalGetUserMedia = devices.getUserMedia.bind(devices);
    devices.getUserMedia = async (constraints) => {
      const stream = await originalGetUserMedia(constraints);
      micStreams.add(stream);
      return stream;
    };
  }
}

/**
 * Keep this tab on its current release while a player is switched on, even in
 * the gaps between items. Returns the function that lets updates go again.
 */
export function holdUpdates(): () => void {
  const key = Symbol('hold');
  holds.add(key);
  return () => {
    if (holds.delete(key)) lastAudioAt = Date.now();
  };
}

/** True while some player on this page holds updates (used by the browser checks). */
export function updatesHeld(): boolean {
  return holds.size > 0;
}

function soundingNow(): boolean {
  for (const el of media) {
    if (!el.paused && !el.ended) return true;
  }
  for (const stream of micStreams) {
    if (stream.getTracks().some((t) => t.readyState === 'live')) return true;
    micStreams.delete(stream);
  }
  return false;
}

export function isAudioActive(): boolean {
  if (holds.size > 0 || soundingNow()) {
    lastAudioAt = Date.now();
    return true;
  }
  return Date.now() - lastAudioAt < AUDIO_GRACE_MS;
}

function isTyping(): boolean {
  const el = document.activeElement as HTMLElement | null;
  if (!el) return false;
  return el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable;
}

/** True when reloading right now would take something away from the person. */
export function isBusy(): boolean {
  return isAudioActive() || isTyping();
}

export function updatePending(): boolean {
  return pending;
}

async function publishedVersion(): Promise<string | null> {
  try {
    const res = await fetch(`/version.json?t=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) return null;
    const body = await res.json();
    return typeof body?.version === 'string' ? body.version : null;
  } catch {
    return null; // offline or mid-deploy: try again next minute
  }
}

async function check(force = false) {
  if (pending && !force) return;
  const version = await publishedVersion();
  if (!version) return;
  publishedNow = version;
  if (version !== APP_VERSION) pending = true;
}

/** The push in force for this tab right now, if any. */
function activePush(): ReleasePush | null {
  const push = policy.push;
  if (!push) return null;
  const now = serverNow();
  const at = push.at ? new Date(push.at).getTime() : 0;
  if (now < at) return null;
  if (push.version) {
    // Only once that release is really being served: reloading earlier would
    // just load this same release again.
    if (push.version === APP_VERSION || publishedNow !== push.version) return null;
  } else {
    if (!publishedNow || publishedNow === APP_VERSION) return null;
    const from = Math.max(at, push.pushed_at ? new Date(push.pushed_at).getTime() : 0);
    if (now - from > UNNAMED_PUSH_LASTS_MS) return null;
  }
  return push;
}

function applyIfQuiet() {
  const push = activePush();
  if (push) {
    if (!pushDueSince) pushDueSince = Date.now();
    if (isTyping()) { setWaiting('typing'); return; }
    if (!isAudioActive()) { reloadForRelease(false); return; }
    if (!push.include_playing) { setWaiting('audio'); return; }
    const itemJustChanged = Date.now() - lastItemChangeAt < ITEM_CHANGE_WINDOW_MS;
    if (itemJustChanged || Date.now() - pushDueSince >= PLAYING_PUSH_MAX_WAIT_MS) {
      reloadForRelease(true);
      return;
    }
    setWaiting('next item');
    return;
  }
  if (policy.mode === 'manual') { setWaiting(pending ? 'admin push' : null); return; }
  if (!pending) { setWaiting(null); return; }
  if (isBusy()) { setWaiting(isTyping() ? 'typing' : 'audio'); return; }
  const idle = Date.now() - lastInput >= IDLE_BEFORE_RELOAD_MS;
  if (document.hidden || idle) reloadForRelease(false);
  else setWaiting('idle');
}

function setWaiting(next: string | null) {
  if (next === waitingFor) return;
  waitingFor = next;
  reportTab();
}

// ── Tab socket: report this tab, hear the policy ──
let socket: WebSocket | null = null;
let lastReport = '';
let socketRetry = 1_000;

function siteSocketUrl(): string {
  const wsBase = import.meta.env.VITE_WS_URL;
  const apiUrl = import.meta.env.VITE_API_URL || '/api/v1';
  if (wsBase) return `${wsBase}/api/v1/ws/site`;
  if (apiUrl.startsWith('http')) return apiUrl.replace(/^http/, 'ws') + '/ws/site';
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}${apiUrl}/ws/site`;
}

function clientId(): string {
  try {
    let id = sessionStorage.getItem('kbr_tab_id');
    if (!id) {
      id = Math.random().toString(36).slice(2, 10);
      sessionStorage.setItem('kbr_tab_id', id);
    }
    return id;
  } catch {
    return 'private';
  }
}

function browserName(): string {
  const ua = navigator.userAgent;
  const os = /iPhone|iPad|iPod/.test(ua) ? 'iPhone/iPad'
    : /Android/.test(ua) ? 'Android'
    : /Mac OS X/.test(ua) ? 'Mac'
    : /Windows/.test(ua) ? 'Windows'
    : /Linux/.test(ua) ? 'Linux' : 'Other';
  const name = /Edg\//.test(ua) ? 'Edge'
    : /Firefox\//.test(ua) ? 'Firefox'
    : /CriOS|Chrome\//.test(ua) ? 'Chrome'
    : /Safari\//.test(ua) ? 'Safari' : 'Browser';
  return `${name} on ${os}`;
}

/** The signed-in role, if any (shown to admins as who the tab belongs to). */
function userName(): string | null {
  try {
    const token = localStorage.getItem('access_token');
    if (!token) return null;
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return typeof payload?.role === 'string' ? payload.role : null;
  } catch {
    return null;
  }
}

function reportTab(force = false) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const data = {
    client_id: clientId(),
    version: APP_VERSION,
    page: window.location.pathname,
    playing: holds.size > 0 || soundingNow(),
    browser: browserName(),
    user: userName(),
    waiting: waitingFor,
  };
  const text = JSON.stringify({ type: 'tab', data });
  if (!force && text === lastReport) return;
  lastReport = text;
  try { socket.send(text); } catch { /* reconnects */ }
}

function connectSocket() {
  let ws: WebSocket;
  try { ws = new WebSocket(siteSocketUrl()); } catch { return; }
  socket = ws;
  ws.onopen = () => { socketRetry = 1_000; reportTab(true); };
  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg?.type === 'release' && msg.data) setPolicy(msg.data as ReleasePolicy);
    } catch { /* ignore */ }
  };
  ws.onclose = () => {
    if (socket === ws) socket = null;
    window.setTimeout(connectSocket, socketRetry);
    socketRetry = Math.min(socketRetry * 2, 60_000);
  };
  ws.onerror = () => { try { ws.close(); } catch { /* closed */ } };
}

/** Read the policy over plain HTTP (when the socket is down). */
async function fetchPolicy() {
  if (socket && socket.readyState === WebSocket.OPEN) return;
  try {
    const apiUrl = import.meta.env.VITE_API_URL || '/api/v1';
    const res = await fetch(`${apiUrl}/site-release`, { cache: 'no-store' });
    if (res.ok) setPolicy(await res.json());
  } catch { /* offline */ }
}

/**
 * Called when the person moves to another screen. A screen change repaints
 * everything anyway, so it is a free moment to load the new release.
 * Returns true if a reload was started.
 */
export function applyUpdateOnNavigation(): boolean {
  if (!pending || isAudioActive() || reloadedRecently()) return false;
  if (policy.mode === 'manual' && !activePush()) return false;
  reloadForRelease(false);
  return true;
}

export function startAppUpdates() {
  if (started) return;
  started = true;
  trackMedia();
  if (import.meta.env.DEV) return;

  for (const evt of ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const) {
    window.addEventListener(evt, () => { lastInput = Date.now(); }, { passive: true, capture: true });
  }
  connectSocket();
  void fetchPolicy();
  // A waiting push checks often so tabs move within seconds of the release being served
  window.setInterval(() => { void check(!!policy.push); }, CHECK_EVERY_MS);
  window.setInterval(() => { if (policy.push && !activePush()) void check(true); }, 15_000);
  window.setInterval(() => { void fetchPolicy(); }, CHECK_EVERY_MS);
  window.setInterval(applyIfQuiet, APPLY_EVERY_MS);
  window.setInterval(() => reportTab(true), 30_000);
  window.setInterval(() => reportTab(), 2_000);
  let hiddenAt = document.hidden ? Date.now() : 0;
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { hiddenAt = Date.now(); void check().then(applyIfQuiet); return; }
    const away = hiddenAt ? Date.now() - hiddenAt : 0;
    hiddenAt = 0;
    void check().then(() => {
      // Back after a long time away (screen locked for hours): the browser has
      // throttled this tab and nothing is actually sounding, so a switched-on
      // player alone does not hold the update. Reload onto the new release now,
      // unless a form field still has focus (a half-written message would be lost).
      if (pending && away >= LONG_AWAY_MS && !soundingNow() && !isTyping() && (policy.mode === 'auto' || activePush())) reloadForRelease(false);
      else applyIfQuiet();
    });
  });
  window.addEventListener('online', () => { void check(); });

  // A screen whose code was replaced by a newer release fails to load in an
  // old tab. Reload to the new release instead of showing an error.
  // Vite raises this for any failed lazy import, including a screen whose code throws
  // while loading: that is a real bug, not a release, so it must show the error box
  // (a reload would only hide it behind a reload). The import still fails, so the
  // error box says "The site was updated" while the reload starts.
  window.addEventListener('vite:preloadError', (event) => {
    if (!isStaleCodeError((event as Event & { payload?: unknown }).payload)) return;
    if (isAudioActive() || reloadedRecently()) return;
    reloadForRelease(false);
  });
}
