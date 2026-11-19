import { holdUpdates } from './appUpdate';
import { startLiveStream, STREAM_STATE_EVENT, streamStateOf, type LiveStream } from './liveStream';

/**
 * One player for a station's server stream, shared by the whole tab (the Listen page
 * and the control room both play through it).
 *
 * It lives outside React, so leaving the page that started it does not end it: the
 * next page takes it over (adoptStationStream) and the sound goes on with no gap and
 * no new tap. An iPhone allows sound only from a tap, and only on the element that
 * tap started; a new page with its own element was silent until tapped again
 * (Shmuel, 2026-11-18: "When in guide music stops. And when navigating back to
 * dashboard music doesn't continue").
 *
 * A page that goes away calls releaseStationStream: the stream stops a few seconds
 * later unless another page has taken it over by then.
 */

// Long enough for a late-loading page to arrive on a slow phone
export const RELEASE_GRACE_MS = 6000;

interface Running {
  stationId: string;
  url: string;
  stream: LiveStream;
}

let element: HTMLAudioElement | null = null;
let current: Running | null = null;
let releaseTimer: ReturnType<typeof setTimeout> | null = null;

// A new site release never reloads the tab while its stream is switched on, even while
// it reconnects after a drop (no audio element is playing then, so the "audio playing"
// rule alone let a reload in and the tab came back silent). Not held while the stream
// waits for a tap: the browser needs one after a reload anyway.
let releaseHold: (() => void) | null = null;
function syncHold() {
  const want = !!current && !!element && streamStateOf(element) !== 'blocked' && streamStateOf(element) !== 'stopped';
  if (want && !releaseHold) releaseHold = holdUpdates();
  if (!want && releaseHold) { releaseHold(); releaseHold = null; }
}

/** The tab's stream element (made once, kept in the page body). */
export function stationStreamElement(): HTMLAudioElement {
  if (!element) {
    element = document.createElement('audio');
    element.setAttribute('playsinline', '');
    element.setAttribute('aria-hidden', 'true');
    element.dataset.testid = 'station-stream';
    element.style.display = 'none';
    element.addEventListener(STREAM_STATE_EVENT, syncHold);
    document.body.appendChild(element);
  }
  return element;
}

function cancelRelease() {
  if (releaseTimer) clearTimeout(releaseTimer);
  releaseTimer = null;
}

/** Play `url` for a station, replacing whatever the tab streamed. Call inside a tap
 *  the first time (an iPhone needs it); later calls may come from anywhere. */
export function playStationStream(stationId: string, url: string, onFail: () => void): LiveStream {
  cancelRelease();
  current?.stream.stop();
  const holder: { stream: LiveStream | null } = { stream: null };
  const inner = startLiveStream(stationStreamElement(), url, () => {
    if (current && current.stream === holder.stream) current = null;
    syncHold();
    onFail();
  });
  const stream: LiveStream = {
    mode: inner.mode,
    // A page's old handle never stops the stream another page has started since
    stop: () => {
      if (current?.stream !== stream) return;
      cancelRelease();
      current = null;
      inner.stop();
      syncHold();
    },
  };
  holder.stream = stream;
  current = { stationId, url, stream };
  syncHold();
  return stream;
}

/** The stream the tab plays now (optionally: for this station), if it is playing. */
export function runningStationStream(stationId?: string | null): Running | null {
  if (!current || !element || element.paused) return null;
  if (stationId && current.stationId !== stationId) return null;
  return current;
}

/** A page takes over the running stream of its station: the pending stop is called off. */
export function adoptStationStream(stationId: string | null | undefined): Running | null {
  const run = stationId ? runningStationStream(stationId) : null;
  if (run) cancelRelease();
  return run;
}

/** The page that played `stream` goes away: stop it soon unless another page adopts it. */
export function releaseStationStream(stream: LiveStream | null | undefined, graceMs = RELEASE_GRACE_MS) {
  if (!stream || current?.stream !== stream) return;
  cancelRelease();
  releaseTimer = setTimeout(() => {
    releaseTimer = null;
    if (current?.stream === stream) stream.stop();
  }, graceMs);
}

/** Stop the tab's stream now, whoever started it. */
export function stopStationStream() {
  cancelRelease();
  current?.stream.stop();
  current = null;
  syncHold();
}

/** The stream element really gives sound now. */
export function stationStreamSounding(): boolean {
  return !!element && !!current && !element.paused && element.readyState >= 3;
}
