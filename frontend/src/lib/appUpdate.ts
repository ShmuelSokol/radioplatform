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
 */

declare const __APP_VERSION__: string;

const CHECK_EVERY_MS = 60_000;
const APPLY_EVERY_MS = 5_000;
const IDLE_BEFORE_RELOAD_MS = 30_000;
const AUDIO_GRACE_MS = 120_000;

const media = new Set<HTMLMediaElement>();
const micStreams = new Set<MediaStream>();
const holds = new Set<symbol>();
let lastInput = Date.now();
let lastAudioAt = 0;
let pending = false;
let started = false;

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

async function check() {
  if (pending) return;
  const version = await publishedVersion();
  if (version && version !== __APP_VERSION__) pending = true;
}

function applyIfQuiet() {
  if (!pending || isBusy()) return;
  const idle = Date.now() - lastInput >= IDLE_BEFORE_RELOAD_MS;
  if (document.hidden || idle) window.location.reload();
}

/**
 * Called when the person moves to another screen. A screen change repaints
 * everything anyway, so it is a free moment to load the new release.
 * Returns true if a reload was started.
 */
export function applyUpdateOnNavigation(): boolean {
  if (!pending || isAudioActive()) return false;
  window.location.reload();
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
  window.setInterval(check, CHECK_EVERY_MS);
  window.setInterval(applyIfQuiet, APPLY_EVERY_MS);
  document.addEventListener('visibilitychange', () => { void check().then(applyIfQuiet); });
  window.addEventListener('online', () => { void check(); });

  // A screen whose code was replaced by a newer release fails to load in an
  // old tab. Reload to the new release instead of showing an error.
  window.addEventListener('vite:preloadError', (event) => {
    if (isAudioActive()) return;
    event.preventDefault();
    window.location.reload();
  });
}
