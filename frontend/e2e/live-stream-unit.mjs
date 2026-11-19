/**
 * Unit checks of src/lib/liveStream.ts (the engine stream player): which way a stream is
 * played (MSE, the HLS copy, the plain element), and the HLS start, fallback, stop and
 * refused-play rules. Runs the real module in Node with a fake <audio> element and a
 * fake clock: no browser, no network.
 *
 *   node frontend/e2e/live-stream-unit.mjs        (Node 22.18+ strips the TypeScript types)
 *
 * Checklist rows F-ENGINE2-14, -16, -17, -18, -26. Exits non-zero if a check fails.
 * KNOWN_BUGS lists checks expected to fail (XFAIL); a pass there prints XPASS and fails,
 * like pytest's xfail(strict=True).
 */
const KNOWN_BUGS = {
  'F-ENGINE2-26 HLS that stops advancing without an error is retried or falls back':
    'found 2026-11-19: after playing starts, a stalled HLS stream (segments no longer written) is never retried '
    + 'and never falls back to the plain stream; the listener sits in silence',
};

// ── fake clock ───────────────────────────────────────────────────────────────
let now = 0;
let timers = [];
let nextId = 1;
globalThis.setTimeout = (fn, ms) => { const id = nextId++; timers.push({ id, at: now + (ms || 0), fn }); return id; };
globalThis.clearTimeout = (id) => { timers = timers.filter((t) => t.id !== id); };
function advance(ms) {
  const end = now + ms;
  for (;;) {
    timers.sort((a, b) => a.at - b.at);
    const t = timers[0];
    if (!t || t.at > end) break;
    timers.shift();
    now = t.at;
    t.fn();
  }
  now = end;
}
const flush = () => new Promise((r) => setImmediate(r));

// ── fake browser ─────────────────────────────────────────────────────────────
class FakeAudio {
  constructor({ nativeHls = true, refuse = false } = {}) {
    this.nativeHls = nativeHls;
    this.refuse = refuse;
    this.listeners = {};
    this.srcs = [];
    this.plays = 0;
    this.loads = 0;
    this.paused = true;
    this.currentTime = 0;
    this._src = '';
  }
  set src(v) { this._src = v; this.srcs.push(v); }
  get src() { return this._src; }
  removeAttribute(name) { if (name === 'src') this._src = ''; }
  load() { this.loads += 1; this.currentTime = 0; }
  pause() { this.paused = true; }
  play() {
    this.plays += 1;
    if (this.refuse) return Promise.reject(new Error('NotAllowedError'));
    this.paused = false;
    return Promise.resolve();
  }
  canPlayType(t) { return t === 'application/vnd.apple.mpegurl' && this.nativeHls ? 'maybe' : ''; }
  addEventListener(ev, fn) { (this.listeners[ev] ||= []).push(fn); }
  removeEventListener(ev, fn) { this.listeners[ev] = (this.listeners[ev] || []).filter((f) => f !== fn); }
  emit(ev) { for (const fn of [...(this.listeners[ev] || [])]) fn(); }
}

function setMse(on) {
  globalThis.window = on
    ? { MediaSource: class { static isTypeSupported(t) { return t === 'audio/mpeg'; } addEventListener() {} } }
    : {};
  globalThis.MediaSource = globalThis.window.MediaSource;
}
URL.createObjectURL = () => 'blob:fake';
URL.revokeObjectURL = () => {};

const { hlsUrlFor, startLiveStream } = await import('../src/lib/liveStream.ts');

// ── checks ───────────────────────────────────────────────────────────────────
let failed = 0;
function check(name, ok, detail = '') {
  const known = KNOWN_BUGS[name];
  if (known) {
    if (ok) { failed += 1; console.log(`XPASS ${name} (listed as a known bug: remove it from KNOWN_BUGS)`); }
    else console.log(`XFAIL ${name}: ${known}`);
    return;
  }
  if (!ok) failed += 1;
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? `: ${detail}` : ''}`);
}

const ID = '0e0e0e0e-0000-4000-8000-00000000a001';
const LIVE = `https://stream.kbrlive.com/live-${ID}`;
const HLS = `https://stream.kbrlive.com/hls/${ID}/mp3.m3u8`;

function fresh(opts = {}) {
  now = 0; timers = [];
  setMse(!!opts.mse);
  const audio = new FakeAudio(opts);
  let fails = 0;
  const s = startLiveStream(audio, opts.url || LIVE, () => { fails += 1; });
  return { audio, s, fails: () => fails };
}

// F-ENGINE2-18: which addresses get an HLS copy, and who uses it
check('F-ENGINE2-18 hlsUrlFor maps <host>/live-<36-char id> to /hls/<id>/mp3.m3u8', hlsUrlFor(LIVE) === HLS, hlsUrlFor(LIVE));
check('F-ENGINE2-18 hlsUrlFor works over http too', hlsUrlFor(`http://127.0.0.1:8010/live-${ID}`) === `http://127.0.0.1:8010/hls/${ID}/mp3.m3u8`);
for (const bad of [
  'https://stream.kbrlive.com/live',
  `https://stream.kbrlive.com/live-${ID.slice(0, 35)}`,
  `https://stream.kbrlive.com/live-${ID}x`,
  `https://stream.kbrlive.com/radio/live-${ID}`,
  `https://stream.kbrlive.com/live-${ID}?x=1`,
  'https://stream.kbrlive.com/harness',
]) {
  check(`F-ENGINE2-18 no HLS address for ${bad}`, hlsUrlFor(bad) === null);
}
{
  const { audio, s } = fresh({ mse: true, nativeHls: true });
  check('F-ENGINE2-18 a browser with MSE for MP3 keeps MSE even if it could play HLS', s.mode === 'mse' && audio.src === 'blob:fake', `${s.mode} ${audio.src}`);
  s.stop();
}
{
  const { audio, s } = fresh({ nativeHls: true });
  check('F-ENGINE2-18 no MSE + native HLS (iPhone) plays the HLS copy', s.mode === 'hls' && audio.src === HLS, `${s.mode} ${audio.src}`);
  s.stop();
}
{
  const { audio, s } = fresh({ nativeHls: true, url: 'https://stream.kbrlive.com/live' });
  check('F-ENGINE2-18 any other stream address plays the plain stream', s.mode === 'plain' && audio.src === 'https://stream.kbrlive.com/live', `${s.mode} ${audio.src}`);
}
{
  const { audio, s } = fresh({ nativeHls: false });
  check('F-ENGINE2-18 no MSE and no native HLS plays the plain stream', s.mode === 'plain' && audio.src === LIVE, `${s.mode} ${audio.src}`);
}

// F-ENGINE2-14: falls back to the plain stream on an error, or when nothing is heard in 8 s
{
  const { audio } = fresh();
  await flush();
  audio.emit('error');
  await flush();
  check('F-ENGINE2-14 a missing or broken playlist (error) falls back to the plain stream', audio.src === LIVE && audio.srcs.join(' ') === `${HLS} ${LIVE}`, audio.srcs.join(' '));
}
{
  const { audio } = fresh();
  await flush();
  audio.paused = true; // loading, nothing heard yet
  advance(7900);
  const before = audio.src;
  advance(200);
  await flush();
  check('F-ENGINE2-14 nothing heard within 8 s falls back to the plain stream (not before)', before === HLS && audio.src === LIVE, `${before} -> ${audio.src}`);
}
{
  const { audio } = fresh();
  await flush();
  audio.emit('playing');
  audio.currentTime = 2;
  advance(20000);
  await flush();
  check('F-ENGINE2-14 an HLS stream that started within 8 s stays on HLS', audio.src === HLS, audio.srcs.join(' '));
  audio.emit('error');
  await flush();
  check('F-ENGINE2-14 an error later on (playlist gone) still falls back to the plain stream', audio.src === LIVE, audio.srcs.join(' '));
}

// F-ENGINE2-16: stop during the 8 s wait, stop after the fallback, Listen again
{
  const { audio, s } = fresh();
  await flush();
  advance(3000);
  s.stop();
  audio.paused = true;
  advance(10000);
  audio.emit('error');
  await flush();
  check('F-ENGINE2-16 Stop during the 8 s wait never starts the plain stream afterwards', audio.src === '' && !audio.srcs.includes(LIVE), audio.srcs.join(' '));
}
{
  const { audio, s } = fresh();
  await flush();
  audio.emit('error');
  await flush();
  s.stop();
  check('F-ENGINE2-16 Stop after the fallback stops the plain stream', audio.src === '' && audio.paused && audio.loads >= 1, `${audio.src} paused=${audio.paused}`);
  const again = startLiveStream(audio, LIVE, () => {});
  check('F-ENGINE2-16 Listen again after a stop loads the live HLS address afresh (rejoins live)', again.mode === 'hls' && audio.src === HLS && audio.currentTime === 0, `${again.mode} ${audio.src}`);
  again.stop();
}
{
  const { audio, s } = fresh();
  await flush();
  audio.emit('playing');
  audio.currentTime = 120;
  s.stop();
  check('F-ENGINE2-16 Stop on HLS drops the source and reloads, so nothing of the old position is kept', audio.src === '' && audio.loads === 1 && audio.currentTime === 0);
}

// F-ENGINE2-17: a refused play() fails at once, with no fallback afterwards
{
  const { audio, fails } = fresh({ refuse: true });
  await flush();
  check('F-ENGINE2-17 a refused play() reports failure at once', fails() === 1, `onFail called ${fails()}x`);
  audio.emit('error');
  advance(10000);
  await flush();
  check('F-ENGINE2-17 and does not try the plain stream afterwards', !audio.srcs.includes(LIVE) && audio.plays === 1 && fails() === 1, `${audio.srcs.join(' ')} plays=${audio.plays}`);
}

// F-ENGINE2-26: HLS that stops advancing without an error after it started
{
  const { audio } = fresh();
  await flush();
  audio.emit('playing');
  audio.currentTime = 5;
  // segments stop: the element waits and stalls, but raises no error
  audio.emit('waiting');
  audio.emit('stalled');
  advance(60000);
  await flush();
  const recovered = audio.srcs.length > 1 || audio.plays > 1;
  check('F-ENGINE2-26 HLS that stops advancing without an error is retried or falls back', recovered, audio.srcs.join(' '));
}

console.log(failed ? `\n${failed} check(s) failed` : '\nall checks passed');
process.exit(failed ? 1 : 0);
