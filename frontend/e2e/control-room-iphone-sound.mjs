/**
 * Sound in the control room (/admin/dashboard) on an iPhone: where the play control
 * is, and how long a tap takes to give sound. Runs the built site against a fake
 * backend, in Chromium made to follow iPhone Safari's rule that sound may start only
 * inside a real tap (IOS_SOUND_POLICY in e2e/lib/fake-station.mjs). Found
 * 2026-11-18: Shmuel's iPhone stayed silent for about 3 minutes and he could not
 * see where to press play.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4311 --strictPort &)
 *   BASE_URL=http://localhost:4311 node e2e/control-room-iphone-sound.mjs   # optional: ONLY=<part,...>
 *
 * Exits non-zero if a check fails. Real iPhone Safari still needs a tap by hand.
 */
import {
  chromium, devices, checker, sleep, makeStation, adminContext, newPage, watchDecks, timeClick, timeUntil, IOS_SOUND_POLICY,
} from './lib/fake-station.mjs';
import { startStreamServer } from './lib/stream-server.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const STREAM_PORT = Number(process.env.STREAM_PORT || 4323);
const ONLY = process.env.ONLY || '';
const PHONE = { ...devices['iPhone 13'] };
const PHONE_LAND = { ...devices['iPhone 13 landscape'], screen: { width: 844, height: 390 } };
// First sound this long after the tap at most (ms)
const LIMIT = 2000;

const { check, done } = checker({});

async function openRoom(browser, contextOpts, station, opts = {}) {
  const { ctx, np } = await adminContext(browser, contextOpts, station, opts);
  await ctx.addInitScript(IOS_SOUND_POLICY);
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  return { ctx, np, page, errors };
}

// Sound really comes out: a deck is playing, its gain and the output are open, and the audio context runs
const HEARD = `() => window.__audible().length >= 1 && window.__outLevel() > 0.05 && [...window.__deckGain.values()][0].context.state === 'running'`;
const heard = (page) => page.evaluate(`(${HEARD})()`).catch(() => false);
const listenBtn = (page) => page.getByTestId('listen-here').locator('visible=true').first();

async function buttonChecks(page, tag) {
  const btn = listenBtn(page);
  const n = await page.getByTestId('listen-here').locator('visible=true').count();
  check(`${tag}: one Listen button is on screen`, n === 1, `${n} visible`);
  if (!n) return false;
  const text = (await btn.innerText()).trim();
  check(`${tag}: the button says Listen in words`, /listen/i.test(text), text);
  const box = await btn.boundingBox();
  check(`${tag}: the button is big enough to tap (44 px high, 120 px wide)`, box.height >= 44 && box.width >= 120, `${Math.round(box.width)}x${Math.round(box.height)}`);
  const vp = page.viewportSize();
  check(`${tag}: the button is on the first screen, not scrolled away`, box.y >= 0 && box.y + box.height <= vp.height && box.x >= 0 && box.x + box.width <= vp.width, `y ${Math.round(box.y)} of ${vp.height}`);
  return true;
}

async function oneSong(page, tag, ms = 3000) {
  // One item is heard, and its position only moves forward (no loop, no jump back)
  const samples = await watchDecks(page, ms, 100);
  const two = samples.filter((s) => s.decks.filter((d) => d.playing && d.gain > 0.05).length > 1).length;
  let back = 0; let last = null; let stuck = 0;
  for (const s of samples) {
    const d = s.decks.find((x) => x.playing && x.gain > 0.05);
    if (!d) continue;
    if (last && last.src === d.src) { if (d.t < last.t - 0.05) back++; if (d.t === last.t) stuck++; }
    last = d;
  }
  check(`${tag}: one item is heard at a time`, two === 0, `${two} of ${samples.length} samples had two`);
  check(`${tag}: the sound only moves forward (no loop, no jump back)`, back === 0 && stuck < samples.length / 3, `${back} jumps back, ${stuck} stuck`);
}

// ── A first visit: nothing remembered ─────────────────────────
async function fresh(browser, contextOpts, tag) {
  const station = makeStation();
  const { ctx, page, errors } = await openRoom(browser, contextOpts, station, { storage: { 'kbr-cr-listen': null, radio_muted: null } });
  await sleep(600);
  check(`${tag}: silent before any tap`, !(await heard(page)));
  if (await buttonChecks(page, tag)) {
    const ms = await timeClick(page, listenBtn(page), HEARD, 6000, 'tap');
    check(`${tag}: sound within ${LIMIT} ms of tapping Listen`, ms >= 0 && ms <= LIMIT, `${ms} ms`);
    const d = await page.evaluate(() => window.__audible()[0]);
    const live = (Date.now() - new Date(station.playing().started_at).getTime()) / 1000;
    check(`${tag}: it joins the item on air where the station is`, !!d && d.src === station.playing().asset_id && Math.abs(d.t - live) < 2.5, d ? `${d.src} at ${d.t}, station at ${live.toFixed(1)}` : 'nothing');
    await oneSong(page, tag);
    check(`${tag}: while listening the control says so in words`, /sound on|listening|mute/i.test(await page.getByTestId('sound-control').locator('visible=true').first().innerText().catch(() => '')));
    check(`${tag}: listening is remembered`, await page.evaluate(() => localStorage.getItem('kbr-cr-listen')) === '1');
  }
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Listening was on last time: the page comes back needing one tap ──
async function remembered(browser, contextOpts, tag, { muted = false, first = 'button' } = {}) {
  const station = makeStation();
  const { ctx, page, errors } = await openRoom(browser, contextOpts, station, { storage: { 'kbr-cr-listen': '1', radio_muted: muted ? 'true' : null } });
  await sleep(600);
  check(`${tag}: silent before any tap`, !(await heard(page)));
  check(`${tag}: the page does not claim sound is on while silent`, !/sound on/i.test(await page.locator('[data-testid=control-room] header').innerText()));
  if (await buttonChecks(page, tag)) {
    let ms;
    if (first === 'scroll') {
      // A scroll is not a tap: Safari allows no sound for it, and the tap after it must still work
      // (touch events made in the page: a real scroll here leaves Chromium swallowing the next tap)
      await page.getByTestId('queue-row').nth(2).evaluate((el) => {
        const r = el.getBoundingClientRect();
        const touch = (y) => new Touch({ identifier: 1, target: el, clientX: r.x + 60, clientY: y });
        const fire = (type, y, ended) => el.dispatchEvent(new TouchEvent(type, { bubbles: true, cancelable: true, touches: ended ? [] : [touch(y)], changedTouches: [touch(y)] }));
        fire('touchstart', r.y + 30); fire('touchmove', r.y - 20); fire('touchend', r.y - 20, true);
      });
      await sleep(500);
      check(`${tag}: a scroll starts no sound (as on an iPhone)`, !(await heard(page)));
      check(`${tag}: after the scroll the Listen button is still there`, await listenBtn(page).count() === 1);
      ms = await timeClick(page, listenBtn(page), HEARD, 6000, 'tap');
    } else if (first === 'elsewhere') {
      // A tap anywhere on the page (here: a queue row's title) turns the remembered sound back on
      ms = await timeClick(page, page.getByTestId('queue-row').nth(3), HEARD, 6000, 'tap');
    } else {
      ms = await timeClick(page, listenBtn(page), HEARD, 6000, 'tap');
    }
    check(`${tag}: sound within ${LIMIT} ms of the tap`, ms >= 0 && ms <= LIMIT, `${ms} ms`);
    if (muted) check(`${tag}: tapping Listen lifts an old Mute`, await page.evaluate(() => localStorage.getItem('radio_muted')) !== 'true');
    await oneSong(page, tag, 2000);
    await sleep(300);
    check(`${tag}: the Listen button is gone once sound is on`, await listenBtn(page).count() === 0);
  }
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── After the tap, later items play by themselves ────────────
async function nextItems(browser) {
  const tag = 'next items';
  const station = makeStation();
  const { ctx, page, errors } = await openRoom(browser, PHONE, station, { storage: { 'kbr-cr-listen': '1', radio_muted: null } });
  // The page sat silent for a while first, through a song change (as on 2026-11-18)
  await sleep(1500);
  station.goOnAir(station.pending()[0].asset_id);
  await sleep(5000);
  check(`${tag}: still silent after a song change with no tap`, !(await heard(page)));
  const ms = await timeClick(page, listenBtn(page), HEARD, 6000, 'tap');
  check(`${tag}: sound within ${LIMIT} ms of tapping Listen after a silent song change`, ms >= 0 && ms <= LIMIT, `${ms} ms`);
  let d = await page.evaluate(() => window.__audible()[0]);
  check(`${tag}: the item on air now is the one heard`, d?.src === station.playing().asset_id, `${d?.src} vs ${station.playing().asset_id}`);
  await oneSong(page, tag, 2500);
  // Two more changes with no tap: each is heard, one at a time
  for (let i = 1; i <= 2; i++) {
    await sleep(1500);
    const want = station.pending()[0].asset_id;
    station.goOnAir(want);
    const took = await timeUntil(page, `() => window.__audible().length === 1 && window.__audible()[0].src === '${want}'`, 6000);
    check(`${tag}: change ${i} is heard with no further tap`, took >= 0 && took <= 2500, `${took} ms`);
    await sleep(900);
    await oneSong(page, `${tag} (after change ${i})`, 2000);
  }
  // Go next from the phone
  const want = station.pending()[0].asset_id;
  const took = await timeClick(page, page.getByTestId('go-next').locator('visible=true').first(), `() => window.__audible().some((x) => x.src === '${want}')`, 6000, 'tap');
  check(`${tag}: Go next is heard within ${LIMIT} ms`, took >= 0 && took <= LIMIT, `${took} ms`);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Mute and back ────────────────────────────────────────────
async function mute(browser) {
  const tag = 'mute';
  const station = makeStation();
  const { ctx, page, errors } = await openRoom(browser, PHONE, station, { storage: { 'kbr-cr-listen': null, radio_muted: null } });
  await timeClick(page, listenBtn(page), HEARD, 6000, 'tap');
  const ctl = page.getByTestId('sound-control').locator('visible=true').first();
  const box = await ctl.boundingBox();
  check(`${tag}: the mute control is big enough to tap`, !!box && box.height >= 44 && box.width >= 44, box ? `${Math.round(box.width)}x${Math.round(box.height)}` : 'missing');
  await ctl.tap();
  await sleep(400);
  check(`${tag}: Mute silences this phone`, (await page.evaluate(() => window.__outLevel())) < 0.01);
  check(`${tag}: muted is said in words`, /muted|unmute/i.test(await ctl.innerText()));
  await ctl.tap();
  await sleep(400);
  check(`${tag}: a second tap brings the sound back`, await heard(page));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── A station on the server stream ───────────────────────────
async function serverStream(browser, stream) {
  const tag = 'server stream';
  for (const start of ['fresh', 'remembered']) {
    const station = makeStation();
    const base = station.nowPlaying;
    station.nowPlaying = (extra) => { const d = base(extra); return d && { ...d, stream_url: stream.url(station.SID) }; };
    const { ctx, page, errors } = await openRoom(browser, PHONE, station, { storage: { 'kbr-cr-listen': start === 'remembered' ? '1' : null, radio_muted: null } });
    await sleep(800);
    const streamEl = () => page.evaluate(() => { const a = document.querySelector('[data-testid=control-room] audio'); return { src: a.currentSrc || a.src, paused: a.paused, muted: a.muted, t: a.currentTime }; });
    const before = await streamEl();
    check(`${tag} (${start}): silent before any tap`, before.paused || before.t === 0, JSON.stringify(before));
    if (await buttonChecks(page, `${tag} (${start})`)) {
      const ms = await timeClick(page, listenBtn(page), `() => { const a = document.querySelector('[data-testid=control-room] audio'); return !a.paused && !a.muted && a.currentTime > 0.05; }`, 8000, 'tap');
      check(`${tag} (${start}): the stream is heard within ${LIMIT} ms of tapping Listen`, ms >= 0 && ms <= LIMIT, `${ms} ms`);
      await sleep(1500);
      const el = await streamEl();
      check(`${tag} (${start}): it is the server stream that plays, not the built-in player`, !el.paused && !el.muted && (await page.evaluate(() => window.__outLevel())) < 0.01, JSON.stringify(el));
      check(`${tag} (${start}): the Listen button is gone once sound is on`, await listenBtn(page).count() === 0);
    }
    check(`${tag} (${start}): no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

const run = (name) => !ONLY || ONLY.split(',').includes(name);
const browser = await chromium.launch();
const stream = await startStreamServer(STREAM_PORT);
try {
  if (run('fresh')) { await fresh(browser, PHONE, 'first visit iPhone'); await fresh(browser, PHONE_LAND, 'first visit iPhone sideways'); }
  if (run('remembered')) {
    await remembered(browser, PHONE, 'remembered iPhone');
    await remembered(browser, PHONE_LAND, 'remembered iPhone sideways');
    await remembered(browser, PHONE, 'remembered + muted', { muted: true });
    await remembered(browser, PHONE, 'remembered, scroll first', { first: 'scroll' });
    await remembered(browser, PHONE, 'remembered, tap elsewhere', { first: 'elsewhere' });
  }
  if (run('next')) await nextItems(browser);
  if (run('mute')) await mute(browser);
  if (run('stream')) await serverStream(browser, stream);
} finally {
  await browser.close();
  await stream.close?.();
}
process.exit(done());
