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
    const streamEl = () => page.evaluate(() => { const a = document.querySelector('[data-testid=station-stream]'); return { src: a.currentSrc || a.src, paused: a.paused, muted: a.muted, t: a.currentTime }; });
    const before = await streamEl();
    check(`${tag} (${start}): silent before any tap`, before.paused || before.t === 0, JSON.stringify(before));
    if (await buttonChecks(page, `${tag} (${start})`)) {
      const ms = await timeClick(page, listenBtn(page), `() => { const a = document.querySelector('[data-testid=station-stream]'); return !a.paused && !a.muted && a.currentTime > 0.05; }`, 8000, 'tap');
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

// ── Server stream: Mute, a stall, and a stream that does not start ──
// (the PC's walk-through 2026-11-18: after Listen on and off on Jerusalem the header
// stayed on "Starting…")
async function streamStarting(browser, stream, contextOpts, tag) {
  const mk = () => { const station = makeStation(); const base = station.nowPlaying; station.nowPlaying = (extra) => { const d = base(extra); return d && { ...d, stream_url: stream.url(station.SID) }; }; return station; };
  const el = (page) => page.evaluate(() => { const a = document.querySelector('[data-testid=station-stream]'); return { paused: a.paused, muted: a.muted, t: a.currentTime }; });
  const vis = (page, id) => page.getByTestId(id).locator('visible=true').count();
  const PLAYS = `() => { const a = document.querySelector('[data-testid=station-stream]'); return !a.paused && !a.muted && a.currentTime > 0.05; }`;
  const press = (page, loc) => (contextOpts.hasTouch ? loc.tap() : loc.click());
  {
    // Listen, Mute, then the stream stalls and comes back while muted, then Unmute
    const { ctx, page, errors } = await openRoom(browser, contextOpts, mk(), { storage: { 'kbr-cr-listen': null, radio_muted: null } });
    await timeClick(page, listenBtn(page), PLAYS, 8000, contextOpts.hasTouch ? 'tap' : 'click');
    await sleep(800);
    const ctl = page.getByTestId('sound-control').locator('visible=true').first();
    await press(page, ctl);
    await sleep(300);
    check(`${tag}: Mute keeps the mute control (not "Starting…", not Listen)`, await vis(page, 'sound-control') === 1 && await vis(page, 'listen-starting') === 0 && await vis(page, 'listen-here') === 0);
    stream.stall(2500);
    let wrong = 0;
    for (let i = 0; i < 12; i++) { await sleep(500); if (await vis(page, 'listen-starting')) wrong++; }
    check(`${tag}: a stall while muted never puts "Starting…" in the header`, wrong === 0 && await vis(page, 'sound-control') === 1, `${wrong} of 12 looks`);
    await press(page, page.getByTestId('sound-control').locator('visible=true').first());
    const back = await timeUntil(page, PLAYS, 4000);
    check(`${tag}: Unmute brings the stream back, the header says so`, back >= 0 && await vis(page, 'sound-control') === 1 && await vis(page, 'listen-starting') === 0, `${back} ms ${JSON.stringify(await el(page))}`);
    // on, off, on a few times quickly
    for (let i = 0; i < 6; i++) { await press(page, page.getByTestId('sound-control').locator('visible=true').first()); await sleep(150); }
    await sleep(1500);
    check(`${tag}: Mute and Unmute six times in a row ends on the mute control, sound on`, await vis(page, 'sound-control') === 1 && await vis(page, 'listen-starting') === 0 && !(await el(page)).muted && !(await el(page)).paused, JSON.stringify(await el(page)));
    check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
  {
    // The stream gives no data: "Starting…" may show for a while, never for good
    // (the stream request is held, unanswered, until `open` is called)
    let open; const held = new Promise((ok) => { open = ok; });
    let holding = true;
    const { ctx, page, errors } = await openRoom(browser, contextOpts, mk(), { storage: { 'kbr-cr-listen': null, radio_muted: null } });
    await ctx.route(/\/live-st-1$/, async (route) => { if (holding) await held; await route.continue().catch(() => {}); });
    await press(page, listenBtn(page));
    await sleep(1000);
    const early = await vis(page, 'listen-starting');
    await sleep(8500);
    check(`${tag}: a stream that does not start shows "Starting…" at first, and Listen again within 10 s`, early === 1 && await vis(page, 'listen-starting') === 0 && await vis(page, 'listen-here') === 1, `starting at 1 s: ${early}`);
    const asked0 = stream.stats.connects; let asks = 0;
    page.on('request', (r) => { if (/\/live-st-1$/.test(r.url())) asks++; });
    await press(page, listenBtn(page));
    await sleep(600);
    check(`${tag}: tapping Listen again asks for the stream afresh and says "Starting…"`, asks >= 1 && await vis(page, 'listen-starting') === 1, `${asks} new requests`);
    holding = false; open();
    const ms = await timeUntil(page, PLAYS, 8000);
    check(`${tag}: once the stream answers it is heard`, ms >= 0 && ms <= 3000 && stream.stats.connects > asked0, `${ms} ms`);
    await sleep(500);
    check(`${tag}: then the header shows the mute control`, await vis(page, 'sound-control') === 1 && await vis(page, 'listen-starting') === 0);
    check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── Leaving the control room and coming back (Shmuel's iPhone, 2026-11-18) ──
// "Dj page stops the audio", "When in guide music stops. And when navigating back to
// dashboard music doesn't continue". The sound must carry on through the menu, the
// back button and the way back, with no new tap and no restart.
function travelStation(stream) {
  const station = makeStation({
    extra: (method, path) => {
      if (path === '/users/public/hosts') return { hosts: [{ id: 'h1', display_name: 'Reb Moshe', title: 'Morning host', bio: null, photo_url: null, social_links: {} }], total: 1 };
      if (path === '/archives' && method === 'GET') return { archives: [], total: 0 };
      if (/^\/schedules\/epg\//.test(path)) return { station_id: station.SID, date: new Date().toISOString().slice(0, 10), blocks: [] };
      return undefined;
    },
  });
  if (stream) {
    const base = station.nowPlaying;
    station.nowPlaying = (extra) => { const d = base(extra); return d && { ...d, stream_url: stream.url(station.SID) }; };
  }
  return station;
}

async function tapMenu(page, label) {
  const nav = page.locator('nav').first();
  // A phone taps; a desktop clicks (its menu groups open on hover and close when a tap moves away)
  const press = (loc) => ((page.viewportSize()?.width ?? 0) < 768 ? loc.tap() : loc.click());
  const link = () => nav.getByRole('link', { name: label, exact: true }).locator('visible=true').first();
  if (!(await link().count())) {
    const burger = nav.getByRole('button', { name: /menu/i }).locator('visible=true').first();
    if (await burger.count()) await press(burger);
    await sleep(150);
  }
  if (!(await link().count())) {
    for (const group of ['Audience', 'Content', 'Broadcast']) {
      const g = nav.getByRole('button', { name: new RegExp(`^\\s*${group}`) }).locator('visible=true').first();
      if (await g.count()) { await press(g); await sleep(150); }
      if (await link().count()) break;
    }
  }
  await press(link());
}

const STREAM_EL = `(() => { const a = document.querySelector('[data-testid=station-stream]'); return a ? { paused: a.paused, muted: a.muted, t: a.currentTime, src: a.currentSrc || a.src } : null; })()`;
/** Sample the stream element every 100 ms while `during` runs and `ms` after it. */
async function streamWhile(page, ms, during) {
  const samples = [];
  let running = true;
  const sampler = (async () => { while (running) { samples.push(await page.evaluate(STREAM_EL).catch(() => null)); await sleep(100); } })();
  await during();
  await sleep(ms);
  running = false;
  await sampler;
  const silent = samples.filter((x) => !x || x.paused || x.muted).length;
  let restarts = 0;
  for (let i = 1; i < samples.length; i++) if (samples[i] && samples[i - 1] && samples[i].t < samples[i - 1].t - 0.3) restarts++;
  return { silent, restarts, n: samples.length };
}

async function travel(browser, stream, contextOpts, tag) {
  // Server stream (Main, Jerusalem)
  {
    const station = travelStation(stream);
    const { ctx, page, errors } = await openRoom(browser, contextOpts, station, { storage: { 'kbr-cr-listen': null, radio_muted: null } });
    await sleep(600);
    await listenBtn(page).tap();
    await page.waitForFunction(`(() => { const a = document.querySelector('[data-testid=station-stream]'); return a && !a.paused && a.currentTime > 0.3; })()`, null, { timeout: 8000 }).catch(() => {});
    const bar = page.getByTestId('player-bar');
    for (const [label, path] of [['Program Guide', '/guide'], ['DJs', '/hosts'], ['Archives', '/archives'], ['Requests', '/admin/requests']]) {
      const heard = await streamWhile(page, 1500, () => tapMenu(page, label));
      check(`${tag} stream: tapping "${label}" keeps the station on (no gap, no restart) with the bar`,
        new URL(page.url()).pathname === path && heard.silent === 0 && heard.restarts === 0 && (await bar.count()) === 1, JSON.stringify(heard));
    }
    {
      const heard = await streamWhile(page, 2000, () => tapMenu(page, 'Dashboard'));
      check(`${tag} stream: back to the Dashboard by the menu, the sound carries on with no tap`, new URL(page.url()).pathname === '/admin/dashboard' && heard.silent === 0 && heard.restarts === 0, JSON.stringify(heard));
      check(`${tag} stream: the control room shows the sound as on (no Listen button)`, await listenBtn(page).count() === 0);
      check(`${tag} stream: no bar on the control room (it has its own sound controls)`, (await bar.count()) === 0);
    }
    {
      const heard = await streamWhile(page, 1500, () => page.goBack());
      check(`${tag} stream: the back button to a listener page keeps it on`, heard.silent === 0 && heard.restarts === 0 && (await bar.count()) === 1, JSON.stringify(heard));
      const fwd = await streamWhile(page, 1500, () => page.goForward());
      check(`${tag} stream: forward to the Dashboard keeps it on`, fwd.silent === 0 && fwd.restarts === 0 && await listenBtn(page).count() === 0, JSON.stringify(fwd));
    }
    await tapMenu(page, 'DJs');
    await sleep(800);
    await bar.getByRole('button', { name: 'Stop' }).tap();
    await sleep(600);
    const after = await page.evaluate(STREAM_EL);
    check(`${tag} stream: Stop in the bar silences it`, !after || after.paused, JSON.stringify(after));
    await tapMenu(page, 'Dashboard');
    await page.getByTestId('queue-row').first().waitFor();
    await sleep(800);
    const back = await page.evaluate(STREAM_EL);
    check(`${tag} stream: after Stop the Dashboard stays quiet and offers Listen`, (!back || back.paused) && await listenBtn(page).count() === 1, JSON.stringify(back));
    check(`${tag} stream: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
  // The built-in player (All Day Stories): an iPhone cannot carry it to a new page
  // without a tap, so the bar offers one
  {
    const station = travelStation(null);
    const { ctx, page, errors } = await openRoom(browser, contextOpts, station, { storage: { 'kbr-cr-listen': null, radio_muted: null } });
    await sleep(600);
    await listenBtn(page).tap();
    await page.waitForFunction(HEARD, null, { timeout: 8000 }).catch(() => {});
    await tapMenu(page, 'Program Guide');
    const bar = page.getByTestId('player-bar');
    await bar.waitFor({ timeout: 5000 }).catch(() => {});
    const tapBtn = page.getByTestId('player-bar-tap');
    await tapBtn.waitFor({ timeout: 6000 }).catch(() => {});
    check(`${tag} built-in: on the guide the bar shows the station and offers "Tap to listen"`, (await bar.count()) === 1 && (await tapBtn.count()) === 1);
    if (await tapBtn.count()) {
      await tapBtn.tap();
      await page.waitForFunction(() => window.__audible && window.__audible().length > 0, null, { timeout: 6000 }).catch(() => {});
      await sleep(500);
      check(`${tag} built-in: one tap on the bar gives the sound`, (await page.evaluate(() => window.__audible().length)) >= 1 && (await tapBtn.count()) === 0);
      const samples = await watchDecks(page, 1500, 100);
      const silent = samples.filter((x) => !x.decks.some((d) => d.playing && d.gain > 0.05)).length;
      await tapMenu(page, 'DJs');
      const s2 = await watchDecks(page, 1500, 100);
      const silent2 = s2.filter((x) => !x.decks.some((d) => d.playing && d.gain > 0.05)).length;
      check(`${tag} built-in: then the guide and DJs keep it on`, silent === 0 && silent2 === 0, `${silent} / ${silent2} silent samples`);
    }
    check(`${tag} built-in: no script errors`, errors.length === 0, errors.join(' | '));
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
  if (run('travel')) { await travel(browser, stream, PHONE, 'travel iPhone'); await travel(browser, stream, { viewport: { width: 1366, height: 900 }, hasTouch: true }, 'travel desktop'); }
  if (run('starting')) { await streamStarting(browser, stream, PHONE, 'stream starting iPhone'); await streamStarting(browser, stream, { viewport: { width: 1366, height: 900 } }, 'stream starting desktop'); }
} finally {
  await browser.close();
  await stream.close?.();
}
process.exit(done());
