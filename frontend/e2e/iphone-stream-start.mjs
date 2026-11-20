/**
 * iPhone: the station's stream starts, plays a second, then stops (Shmuel, 2026-11-20
 * 10:40 AM ET on his iPhone: "it played for like a second, and then the audio stopped.
 * And then I had to hit like mute and then listen ... for the audio to like refresh").
 * The sound must come back by itself, with no tap.
 *
 * Chromium dressed as an iPhone: Safari's tap rule (IOS_SOUND_POLICY: play() only in a
 * tap, or on an element a tap once started), no MediaSource and native HLS (so the site
 * takes the iPhone's HLS path; the stand-in mount answers the HLS address with the MP3
 * stream), fake backend, stand-in Icecast (e2e/lib/stream-server.mjs).
 *
 *   cd frontend && npm run build && (npx vite preview --port 4311 --strictPort &)
 *   BASE_URL=http://localhost:4311 node e2e/iphone-stream-start.mjs      # ONLY=<part,...>
 *
 * Each part runs on the public Listen page and in the control room:
 *   taken      a second after the stream starts, something other than the page pauses it
 *              (iPhone Safari does this when it hands the sound to another player of the
 *              page, or briefly takes the audio session): it plays again by itself
 *   empty      a second after the start the station says "nothing on air" for a moment,
 *              then the item again: the stream plays on (it was stopped and restarted
 *              outside the tap)
 *   later      the same pause after a minute of steady sound (lock screen, headphones out):
 *              not fought, "Sound stopped: tap to resume" as before, one tap brings it back
 *   again      taken again and again: after a few tries it stops trying and asks for a tap
 *   stop       Stop is Stop: nothing starts again by itself
 * Exits non-zero if a check fails.
 */
import { chromium, devices, checker, sleep, makeStation, adminContext, newPage, IOS_SOUND_POLICY } from './lib/fake-station.mjs';
import { startStreamServer } from './lib/stream-server.mjs';
import { allowEverywhere } from './lib/guard.mjs';

allowEverywhere(/warning toast: (Reconnecting…|Sound stopped)/, 'the page says the stream stopped while the test takes the sound away');

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const STREAM_PORT = Number(process.env.STREAM_PORT || 4327);
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
const want = (p) => !ONLY.length || ONLY.includes(p);
const PAGES = (process.env.PAGES || 'listen,room').split(',');
const { check, done } = checker({});

// iPhone Safari has no MediaSource for MP3 and plays HLS itself
function IPHONE_MEDIA() {
  window.MediaSource = undefined;
  window.ManagedMediaSource = undefined;
  const can = HTMLMediaElement.prototype.canPlayType;
  HTMLMediaElement.prototype.canPlayType = function (type) { return /mpegurl/i.test(type) ? 'maybe' : can.call(this, type); };
  // Chromium has its own HLS player for addresses ending in .m3u8 and waits there for a
  // playlist; the stand-in mount answers with the MP3 stream, so it gets another ending
  const d = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src');
  Object.defineProperty(HTMLMediaElement.prototype, 'src', {
    configurable: true, enumerable: d.enumerable, get() { return d.get.call(this); },
    set(v) { d.set.call(this, typeof v === 'string' ? v.replace(/\/mp3\.m3u8$/, '/mp3.hls') : v); },
  });
}

const stream = await startStreamServer(STREAM_PORT);
stream.hlsBurst = 15;
const browser = await chromium.launch();

function engineStation() {
  const station = makeStation();
  const base = station.nowPlaying;
  // The stand-in mount's address has the engine's shape (/live-<uuid>), so the page
  // finds the HLS copy beside it (/hls/<uuid>/mp3.m3u8)
  const sid = '0b5e7c2a-1d4f-4e8b-9a6c-3f2d1e0c9b8a';
  station.nowPlaying = (extra) => ({ ...base(extra), stream_url: `${stream.origin}/live-${sid}` });
  return station;
}

const streamEl = (page) => page.evaluate(() => {
  const a = document.querySelector('[data-testid=station-stream]');
  return a ? { src: a.src, paused: a.paused, t: a.currentTime, state: a.dataset.streamState || '' } : null;
});
async function moving(page, ms = 800) {
  const a = await streamEl(page);
  await sleep(ms);
  const b = await streamEl(page);
  return !!a && !!b && !b.paused && b.t > a.t + 0.3;
}
async function until(fn, timeout) {
  const end = Date.now() + timeout;
  for (;;) {
    if (await fn()) return true;
    if (Date.now() > end) return false;
    await sleep(200);
  }
}
/** Pause the stream element as the phone would (not through the page). */
const takeAway = (page) => page.evaluate(() => document.querySelector('[data-testid=station-stream]').pause());
const refusedPlays = (page) => page.evaluate(() => window.__iosPolicy?.refusedPlays ?? 0);

async function open(where, station) {
  const { ctx, np } = await adminContext(browser, { ...devices['iPhone 13'] }, station, { storage: { 'kbr-cr-listen': '0' } });
  await ctx.addInitScript(IPHONE_MEDIA);
  await ctx.addInitScript(IOS_SOUND_POLICY);
  await ctx.routeWebSocket(/\/ws\/(site|live|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);
  let listen, resume;
  if (where === 'listen') {
    await page.goto(`${BASE}/listen/${station.SID}`);
    listen = page.getByRole('button', { name: 'Listen Live' });
    resume = () => page.getByTestId('listen-resume');
  } else {
    await page.goto(`${BASE}/admin/dashboard`);
    await page.getByTestId('queue-row').first().waitFor();
    listen = page.locator('[data-listen-button]:visible').first();
    resume = () => page.locator('[data-listen-button]:visible', { hasText: /tap to resume/i });
  }
  await listen.waitFor();
  // A real tap (touchend + click), as on the phone
  await listen.tap();
  const started = await until(() => moving(page), 8000);
  const src = (await streamEl(page))?.src ?? '';
  return { ctx, np, page, errors, listen, resume, started, src };
}

async function close(o) { await o.ctx.close(); }

const name = (where) => (where === 'listen' ? 'Listen page' : 'control room');

for (const where of PAGES) {
  const tag = `${name(where)} (iPhone)`;

  if (want('taken')) {
    const o = await open(where, engineStation());
    check(`${tag}: (set up) a tap on Listen plays the HLS copy`, o.started && /\/hls\//.test(o.src), o.src);
    await sleep(1000);
    await takeAway(o.page);
    const back = await until(() => moving(o.page), 6000);
    const st = await streamEl(o.page);
    check(`${tag}: taken: a second after the start something pauses the stream: it plays again by itself within 6 s, no tap`, back, JSON.stringify(st));
    check(`${tag}: taken: no "tap to resume" left on screen`, (await o.resume().count()) === 0);
    check(`${tag}: taken: no script errors`, o.errors.length === 0, o.errors.join(' | '));
    await close(o);
  }

  if (want('empty')) {
    const station = engineStation();
    const o = await open(where, station);
    check(`${tag}: (set up) empty: playing`, o.started);
    const connects = stream.stats.connects;
    await sleep(1000);
    o.np.raw({ type: 'now_playing', data: null });
    await sleep(1500);
    o.np.raw({ type: 'now_playing', data: station.nowPlaying() });
    // Sampled through the gap: it never stopped
    let stops = 0;
    for (let i = 0; i < 8; i++) { const s = await streamEl(o.page); if (!s || s.paused || !s.src) stops++; await sleep(250); }
    check(`${tag}: empty: "nothing on air" for a moment does not stop the stream`, stops === 0 && await moving(o.page), `${stops} stopped samples`);
    check(`${tag}: empty: the stream was not asked for again`, stream.stats.connects === connects, `${stream.stats.connects - connects} new requests`);
    check(`${tag}: empty: no play() refused`, (await refusedPlays(o.page)) === 0, await refusedPlays(o.page));
    await close(o);
  }

  if (want('later')) {
    const o = await open(where, engineStation());
    check(`${tag}: (set up) later: playing`, o.started);
    await sleep(31_000);
    await takeAway(o.page);
    await sleep(3000);
    const st = await streamEl(o.page);
    check(`${tag}: later: a pause after steady sound is left alone (headphones out, lock screen)`, !!st && st.paused, JSON.stringify(st));
    check(`${tag}: later: "Sound stopped: tap to resume" shows`, await until(async () => (await o.resume().count()) > 0, 8000));
    await o.resume().first().tap();
    check(`${tag}: later: one tap brings it back`, await until(() => moving(o.page), 6000));
    await close(o);
  }

  if (want('again')) {
    const o = await open(where, engineStation());
    check(`${tag}: (set up) again: playing`, o.started);
    let n = 0;
    for (; n < 8; n++) {
      await sleep(600);
      if (!(await until(async () => !(await streamEl(o.page))?.paused, 4000))) break;
      await takeAway(o.page);
    }
    check(`${tag}: again: after a few pauses in a row it stops trying`, n < 8, `${n} pauses answered`);
    check(`${tag}: again: and asks for a tap`, await until(async () => (await o.resume().count()) > 0, 8000));
    await close(o);
  }

  if (want('stop')) {
    const o = await open(where, engineStation());
    check(`${tag}: (set up) stop: playing`, o.started);
    if (where === 'listen') await o.page.getByRole('button', { name: 'Stop' }).tap();
    else await o.page.locator('[data-testid=sound-control]:visible').first().tap(); // mute
    await sleep(3000);
    const st = await streamEl(o.page);
    const silent = where === 'listen' ? (!st || st.paused) : await o.page.evaluate(() => { const a = document.querySelector('[data-testid=station-stream]'); return !a || a.paused || a.muted; });
    check(`${tag}: stop: ${where === 'listen' ? 'Stop' : 'Mute'} stays silent, nothing starts by itself`, silent, JSON.stringify(st));
    await close(o);
  }
}

await browser.close();
await stream.close();
done();
