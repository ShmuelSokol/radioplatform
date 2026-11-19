/**
 * The server stream keeps playing, and the screen tells the truth about sound
 * (Shmuel, 2026-11-19: "why did stream stop?"; the station was on air, his computer
 * played nothing, and the control room looked as if it did). Fake backend + a stand-in
 * Icecast mount (e2e/lib/stream-server.mjs), built site, Chromium.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4311 --strictPort &)
 *   BASE_URL=http://localhost:4311 node e2e/stream-keeps-playing.mjs     # ONLY=<part,...>
 *
 * Parts:
 *   meters    no click at all (a browser that wants a tap): the control room's meters show
 *             the engine's on-air levels pushed over the socket; dead air is flat; with no
 *             levels coming they say so; the Listen button says this computer is silent
 *   drops     Listen, then the connection is cut again and again, and the server is down
 *             for a while: the sound comes back by itself each time, the button says
 *             "Reconnecting…" meanwhile, and the tab never takes a new release while the
 *             stream is switched on (it tells the site socket it is playing)
 *   blocked   the browser refuses to play again after a drop: "Sound stopped: tap to
 *             resume", and one tap brings it back
 *   listen    the same drops and the tap on the public Listen page
 *   phone     the button's alert words fit on an iPhone 13 and an iPhone SE
 * Exits non-zero if a check fails.
 */
import { chromium, devices, checker, sleep, makeStation, adminContext, newPage } from './lib/fake-station.mjs';
import { startStreamServer } from './lib/stream-server.mjs';
import { allowEverywhere } from './lib/guard.mjs';

allowEverywhere(/:4323\/live-|ERR_CONNECTION|503/, 'the stream server is cut off and made to answer 503 on purpose');
allowEverywhere(/warning toast: Reconnecting…/, 'the Listen page says it is reconnecting while the test holds the stream server down');

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const STREAM_PORT = Number(process.env.STREAM_PORT || 4323);
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
const want = (p) => !ONLY.length || ONLY.includes(p);
const DESKTOP = { viewport: { width: 1440, height: 900 } };
const { check, done } = checker({});

// A real desktop browser before the first click: no audio context runs and no media
// plays until the person clicks somewhere (headless Chromium allows both by itself).
// After a click the page may play (Chrome's sticky activation).
function NO_CLICK_YET() {
  let clicked = false;
  const waiting = [];
  window.addEventListener('click', () => { clicked = true; waiting.splice(0).forEach((f) => f()); }, true);
  window.addEventListener('keydown', () => { clicked = true; waiting.splice(0).forEach((f) => f()); }, true);
  const Real = window.AudioContext;
  window.AudioContext = class extends Real {
    constructor(...a) { super(...a); if (!clicked) void super.suspend(); }
    resume() { return clicked ? super.resume() : new Promise((ok) => waiting.push(() => super.resume().then(ok))); }
  };
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...a) {
    if (!clicked) return Promise.reject(new DOMException('needs a click first', 'NotAllowedError'));
    return play.apply(this, a);
  };
}

const stream = await startStreamServer(STREAM_PORT);

function engineStation() {
  const station = makeStation();
  const base = station.nowPlaying;
  station.nowPlaying = (extra) => ({ ...base(extra), stream_url: stream.url(station.SID) });
  return station;
}

async function openRoom(browser, contextOpts, station, storage = {}, noClick = false) {
  const { ctx, np } = await adminContext(browser, contextOpts, station, { storage });
  if (noClick) await ctx.addInitScript(NO_CLICK_YET);
  const site = { reports: [] };
  await ctx.routeWebSocket(/\/ws\/site/, (ws) => {
    ws.send(JSON.stringify({ type: 'release', data: { mode: 'auto', push: null, server_time: new Date().toISOString() } }));
    ws.onMessage((m) => { try { const d = JSON.parse(String(m)); if (d.type === 'tab') site.reports.push({ at: Date.now(), ...d.data }); } catch { /* not ours */ } });
  });
  await ctx.routeWebSocket(/\/ws\/(live|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  return { ctx, np, page, errors, site };
}

const streamEl = (page) => page.evaluate(() => {
  const a = document.querySelector('[data-testid=station-stream]');
  return a ? { src: a.src, paused: a.paused, ended: a.ended, t: a.currentTime, state: a.dataset.streamState || '' } : null;
});
// The stream really gives sound: its time moves on
async function moving(page, ms = 1500) {
  const a = await streamEl(page);
  await sleep(ms);
  const b = await streamEl(page);
  return !!a && !!b && !b.paused && b.t > a.t + 0.4;
}
async function until(fn, timeout) {
  const end = Date.now() + timeout;
  for (;;) {
    if (await fn()) return true;
    if (Date.now() > end) return false;
    await sleep(250);
  }
}
const litSegments = (page) => page.locator('[data-testid=vu-meters]:visible [data-lit]').count();
const meterSource = (page) => page.locator('[data-testid=vu-meters]:visible').first().getAttribute('data-meter-source');
const listenButton = (page) => page.locator('[data-listen-button]:visible').first();

// ── meters: no click, the levels come from the engine ──
async function meters() {
  // A browser that wants a tap before any sound, like a real one (headless Chromium does not)
  const browser = await chromium.launch();
  for (const remembered of [false, true]) {
    const tag = `meters${remembered ? ' (listening remembered)' : ''}`;
    const station = engineStation();
    const { ctx, np, page, errors } = await openRoom(browser, DESKTOP, station, { 'kbr-cr-listen': remembered ? '1' : '0' }, true);
    await sleep(800);
    check(`${tag}: nothing lit before any level came`, (await litSegments(page)) === 0);
    const btn = await listenButton(page).innerText();
    check(`${tag}: the button says this computer plays nothing`, remembered ? /Sound stopped: tap to resume/.test(btn) : /Not playing here/.test(btn), btn);
    // The engine pushes its on-air level, ten values a second
    const send = (v) => np.raw({ type: 'levels', data: { l: Array(10).fill(v), r: Array(10).fill(v * 0.8), step: 0.1 } });
    send(0.3);
    await sleep(400);
    const lit = await litSegments(page);
    check(`${tag}: meters move with no click and no Listen`, lit > 20, `${lit} lit`);
    check(`${tag}: the meters say they show the air`, (await meterSource(page)) === 'air', await meterSource(page));
    const timer = setInterval(() => send(0.3), 1000);
    await sleep(2500);
    check(`${tag}: and keep moving while levels come`, (await litSegments(page)) > 20);
    clearInterval(timer);
    // Dead air: flat
    // (the meters fall like a hardware meter, about 20 dB a second)
    for (let i = 0; i < 4; i++) { send(0); await sleep(900); }
    const flat = await litSegments(page);
    check(`${tag}: dead air shows flat meters`, flat === 0, `${flat} lit`);
    // Nothing comes any more (engine gone): not 'air' after a few seconds
    await sleep(4500);
    check(`${tag}: no levels for a while: the meters no longer claim the air`, (await meterSource(page)) === 'none', await meterSource(page));
    check(`${tag}: still no sound started by itself`, (await streamEl(page))?.paused !== false);
    check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
  await browser.close();
}

// ── drops: the sound comes back by itself ──
async function drops() {
  const tag = 'drops';
  const browser = await chromium.launch();
  const station = engineStation();
  const { ctx, page, errors, site } = await openRoom(browser, DESKTOP, station);
  await listenButton(page).click();
  check(`${tag}: (set up) Listen plays the stream`, await until(() => moving(page, 800), 6000));
  for (let i = 1; i <= 3; i++) {
    const before = stream.stats.connects;
    stream.dropAll();
    const back = await until(async () => stream.stats.connects > before && await moving(page, 800), 12000);
    check(`${tag}: cut ${i}: the sound comes back by itself`, back, `connects ${before} -> ${stream.stats.connects}`);
  }
  // The server is down for 12 s (an engine restart)
  stream.failing = true;
  stream.dropAll();
  const shown = await until(async () => /Reconnecting/.test(await listenButton(page).innerText().catch(() => '')), 10000);
  check(`${tag}: server down: the button says Reconnecting…`, shown, await listenButton(page).innerText().catch(() => '(no button)'));
  await sleep(6000);
  const reports = site.reports.slice();
  check(`${tag}: while reconnecting the tab still tells the site it is playing (no release reload)`, reports.length > 0 && reports.at(-1).playing === true && !reports.slice(1).some((r) => r.playing === false), JSON.stringify(reports.map((r) => r.playing)));
  stream.failing = false;
  const back = await until(() => moving(page, 800), 20000);
  check(`${tag}: server back: the sound comes back by itself`, back);
  await sleep(500);
  check(`${tag}: and the button is the sound control again`, (await page.getByTestId('sound-control').count()) > 0);
  check(`${tag}: the built-in player never took over (one source of sound)`, (await page.evaluate(() => window.__outLevel())) < 0.01);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
  await browser.close();
}

// ── blocked: the browser refuses to start again ──
async function blocked() {
  const tag = 'blocked';
  const browser = await chromium.launch();
  const station = engineStation();
  const { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
  await listenButton(page).click();
  check(`${tag}: (set up) playing`, await until(() => moving(page, 800), 6000));
  // From now on play() is refused unless a click is under way (the permission was lost)
  await page.evaluate(() => {
    const real = HTMLMediaElement.prototype.play;
    let inClick = false;
    window.addEventListener('click', () => { inClick = true; setTimeout(() => { inClick = false; }, 0); }, true);
    HTMLMediaElement.prototype.play = function (...a) {
      if (!inClick && this.dataset.testid === 'station-stream') return Promise.reject(new DOMException('needs a tap', 'NotAllowedError'));
      return real.apply(this, a);
    };
  });
  // The phone takes the sound away (lock screen, a call, headphones out): the element is paused
  await page.evaluate(() => document.querySelector('[data-testid=station-stream]').pause());
  const asked = await until(async () => /Sound stopped: tap to resume/.test(await listenButton(page).innerText().catch(() => '')), 10000);
  check(`${tag}: sound taken away: "Sound stopped: tap to resume"`, asked, await listenButton(page).innerText().catch(() => '(no button)'));
  await listenButton(page).click();
  check(`${tag}: one tap brings the sound back`, await until(() => moving(page, 800), 8000));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
  await browser.close();
}

// ── listen: the public Listen page ──
async function listenPage() {
  const tag = 'listen page';
  const browser = await chromium.launch();
  const station = engineStation();
  const { ctx } = await adminContext(browser, DESKTOP, station);
  await ctx.routeWebSocket(/\/ws\/(site|live|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/listen/${station.SID}`);
  await page.getByRole('button', { name: 'Listen Live' }).click();
  check(`${tag}: (set up) Listen Live plays the stream`, await until(() => moving(page, 800), 6000));
  for (let i = 1; i <= 2; i++) {
    const before = stream.stats.connects;
    stream.dropAll();
    check(`${tag}: cut ${i}: the sound comes back by itself`, await until(async () => stream.stats.connects > before && await moving(page, 800), 12000));
  }
  stream.failing = true;
  stream.dropAll();
  check(`${tag}: server down: "Reconnecting…" shows`, await until(async () => (await page.getByTestId('listen-reconnecting').count()) > 0, 10000));
  stream.failing = false;
  check(`${tag}: server back: sound again and the note goes`, await until(async () => (await moving(page, 800)) && (await page.getByTestId('listen-reconnecting').count()) === 0, 20000));
  await page.evaluate(() => {
    const real = HTMLMediaElement.prototype.play;
    let inClick = false;
    window.addEventListener('click', () => { inClick = true; setTimeout(() => { inClick = false; }, 0); }, true);
    HTMLMediaElement.prototype.play = function (...a) {
      if (!inClick && this.dataset.testid === 'station-stream') return Promise.reject(new DOMException('needs a tap', 'NotAllowedError'));
      return real.apply(this, a);
    };
  });
  await page.evaluate(() => document.querySelector('[data-testid=station-stream]').pause());
  check(`${tag}: sound taken away: "Sound stopped: tap to resume"`, await until(async () => (await page.getByTestId('listen-resume').count()) > 0, 10000));
  await page.getByTestId('listen-resume').click();
  check(`${tag}: one tap brings it back`, await until(() => moving(page, 800), 8000));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
  await browser.close();
}

// ── phone: the alert words fit ──
async function phone() {
  const browser = await chromium.launch();
  for (const [name, dev] of [['iPhone 13', devices['iPhone 13']], ['iPhone SE', devices['iPhone SE']]]) {
    const tag = `phone ${name}`;
    for (const remembered of [false, true]) {
      const station = engineStation();
      const { ctx, page, errors } = await openRoom(browser, { ...dev }, station, { 'kbr-cr-listen': remembered ? '1' : '0' }, true);
      await sleep(600);
      const btn = listenButton(page);
      const text = await btn.innerText();
      const box = await btn.boundingBox();
      const vw = page.viewportSize().width;
      const fits = await btn.evaluate((el) => el.scrollWidth <= el.clientWidth + 1 && el.scrollHeight <= el.clientHeight + 2);
      check(`${tag}${remembered ? ' (remembered)' : ''}: the silent-here button shows its words`, (remembered ? /Tap to resume/ : /Silent here: Listen/).test(text), text);
      check(`${tag}${remembered ? ' (remembered)' : ''}: inside the screen, words not cut`, !!box && box.x >= 0 && box.x + box.width <= vw + 1 && fits, JSON.stringify(box));
      check(`${tag}: no horizontal page scroll`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
      await page.screenshot({ path: `/tmp/stream-${name.replace(/ /g, '-')}-${remembered ? 'remembered' : 'fresh'}.png` });
      check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
      await ctx.close();
    }
  }
  await browser.close();
}

try {
  if (want('meters')) await meters();
  if (want('drops')) await drops();
  if (want('blocked')) await blocked();
  if (want('listen')) await listenPage();
  if (want('phone')) await phone();
} finally {
  await stream.close();
}
done();
