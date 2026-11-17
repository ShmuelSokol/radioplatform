/**
 * Release pickup timings (src/lib/appUpdate.ts) on the built site, with Playwright's
 * fake clock so minutes pass in moments. e2e/site-release/check.cjs runs the same
 * flows in real time against two real builds; this file pins the time limits:
 *
 * - a tab never reloads for a release twice within 2 minutes (a stale copy would loop);
 * - a playing tab included in a push reloads at the latest 5 minutes after the push,
 *   and the page plays on by itself only if the reload was under 90 s ago;
 * - a silent visible tab waits until the person has been idle 30 s, a hidden one
 *   reloads at once, audio heard in the last 2 minutes holds it, and a player that is
 *   switched on stops holding it after the tab was hidden 10+ minutes;
 * - a push "at" a time follows the server's clock, not the device's, and a push with
 *   no version named lapses after 1 hour;
 * - a version check made while offline is simply tried again, with no error.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4311 --strictPort &)
 *   BASE_URL=http://localhost:4311 node e2e/release-timing.mjs   # optional: ONLY=idle,hidden,grace,loop,push,resume,away,at,unnamed,offline
 */
import { chromium, checker, sleep, makeStation, routeApi, routeNowPlaying, AUDIO_PROBE } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const { check, done } = checker({});
const want = (n) => !process.env.ONLY || process.env.ONLY.split(',').includes(n);
const S = 1000;
const MIN = 60 * S;

function HIDDEN_SWITCH() {
  Object.defineProperty(Document.prototype, 'hidden', { configurable: true, get() { return !!window.__hidden; } });
  Object.defineProperty(Document.prototype, 'visibilityState', { configurable: true, get() { return window.__hidden ? 'hidden' : 'visible'; } });
  window.__setHidden = (h) => { window.__hidden = h; document.dispatchEvent(new Event('visibilitychange')); };
}

/**
 * One tab on `path` with a fake clock. `serverSkew`: the server's clock minus this
 * device's (sent as server_time with the policy). Returns helpers to set the policy,
 * publish a new version and count reloads.
 */
async function openTab(browser, path, { policy = { mode: 'auto', push: null }, serverSkew = 0, versionFails = 0 } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const station = makeStation();
  await ctx.addInitScript(AUDIO_PROBE);
  await ctx.addInitScript(HIDDEN_SWITCH);
  await routeApi(ctx, station);
  await routeNowPlaying(ctx, station);
  await ctx.routeWebSocket(/\/ws\/(live|listeners)/, () => {});
  const tab = { ctx, station, policy, published: null, versionFails, versionCalls: 0, sockets: [] };
  let page;
  const serverTime = async () => new Date((await page.evaluate(() => Date.now())) + serverSkew).toISOString();
  const send = async (ws) => ws.send(JSON.stringify({ type: 'release', data: { ...tab.policy, server_time: await serverTime() } }));
  await ctx.routeWebSocket(/\/ws\/site/, (ws) => { tab.sockets.push(ws); void send(ws); ws.onMessage(() => {}); });
  await ctx.route(/\/version\.json/, (route) => {
    tab.versionCalls++;
    if (tab.versionFails > 0) { tab.versionFails--; return route.abort('internetdisconnected'); }
    if (!tab.published) return route.fallback();
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ version: tab.published }) });
  });
  page = await ctx.newPage();
  tab.page = page;
  tab.errors = [];
  page.on('pageerror', (e) => tab.errors.push(String(e)));
  tab.loads = 0;
  page.on('load', () => { tab.loads++; });
  await page.clock.install();
  await page.goto(BASE + path);
  await page.waitForLoadState('networkidle').catch(() => {});
  tab.setPolicy = async (p) => { tab.policy = p; for (const ws of tab.sockets) await send(ws).catch(() => {}); };
  tab.serverNow = async () => (await page.evaluate(() => Date.now())) + serverSkew;
  /** Publish a new release and make the tab check now (as the 'online' event does). */
  tab.publish = async (v = 'v-next') => { tab.published = v; await page.evaluate(() => window.dispatchEvent(new Event('online'))); await sleep(300); };
  /** Let `ms` of fake time pass, a few seconds at a time, and give a reload time to start. */
  tab.pass = async (ms, step = 5 * S) => {
    const start = tab.loads;
    for (let left = ms; left > 0 && tab.loads === start; left -= step) {
      await page.clock.runFor(Math.min(step, left)).catch(() => {});
      await sleep(30);
    }
    await sleep(400);
  };
  tab.waitReload = async (before, ms = 4000) => { const s = Date.now(); while (tab.loads === before && Date.now() - s < ms) await sleep(50); return tab.loads > before; };
  tab.touch = () => page.mouse.click(5, 5);
  return tab;
}

const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
try {
  // V1-137: a silent visible tab waits for 30 s of idleness
  if (want('idle')) {
    const t = await openTab(browser, '/stations');
    await t.touch();
    await t.publish();
    await t.pass(25 * S);
    check('V1-137 visible silent tab: no reload while the person was active < 30 s ago', t.loads === 1, `loads=${t.loads}`);
    await t.pass(10 * S);
    check('V1-137 visible silent tab: reloads once idle 30 s', t.loads === 2, `loads=${t.loads}`);
    await t.ctx.close();
  }
  // V1-137: a hidden tab reloads at once
  if (want('hidden')) {
    const t = await openTab(browser, '/stations');
    await t.touch();
    await t.publish();
    const before = t.loads;
    await t.page.evaluate(() => window.__setHidden(true));
    check('V1-137 tab sent to the background reloads at once', await t.waitReload(before, 3000), `loads=${t.loads}`);
    await t.ctx.close();
  }
  // V1-137: audio heard in the last 2 minutes holds the reload
  if (want('grace')) {
    const t = await openTab(browser, '/stations');
    await t.publish();
    await t.page.evaluate(() => { const a = new Audio("/__tone/m1.wav"); a.muted = true; a.play().catch(() => {}); a.pause(); });
    await t.pass(110 * S);
    check('V1-137 audio stopped 110 s ago: no reload yet (2 min grace)', t.loads === 1, `loads=${t.loads}`);
    await t.pass(20 * S);
    check('V1-137 audio stopped 2 min ago: reloads', t.loads === 2, `loads=${t.loads}`);
    await t.ctx.close();
  }
  // V1-135: never two release reloads within 2 minutes (a stale copy would loop)
  if (want('loop')) {
    const t = await openTab(browser, '/stations');
    await t.publish();
    await t.page.evaluate(() => window.__setHidden(true));
    await t.waitReload(1, 3000);
    check('V1-135 first reload happens', t.loads === 2, `loads=${t.loads}`);
    // The new page still sees a different published version (stale copy): it must not reload again at once
    await t.page.evaluate(() => window.dispatchEvent(new Event('online')));
    await t.page.evaluate(() => window.__setHidden(true));
    await t.pass(100 * S);
    check('V1-135 reloaded tab does not reload again within 2 min', t.loads === 2, `loads=${t.loads}`);
    await t.pass(30 * S);
    await t.page.evaluate(() => window.__setHidden(true));
    await t.waitReload(2, 3000);
    check('V1-135 after 2 min it may reload again', t.loads === 3, `loads=${t.loads}`);
    await t.ctx.close();
  }
  // V1-136: a playing tab included in a push reloads at the latest 5 minutes after it
  if (want('push')) {
    const t = await openTab(browser, '/stations', { policy: { mode: 'manual', push: null } });
    await t.page.evaluate(() => { const a = new Audio("/__tone/m1.wav"); a.loop = true; window.__keep = a; a.play().catch(() => {}); }); await sleep(1500);
    const playing = await t.page.evaluate(() => !window.__keep.paused);
    t.published = 'v-next';
    const now = await t.serverNow();
    await t.setPolicy({ mode: 'manual', push: { id: 'p1', version: 'v-next', at: new Date(now).toISOString(), include_playing: true, pushed_at: new Date(now).toISOString(), pushed_by: 'admin' } });
    await sleep(500);
    await t.pass(4 * MIN + 40 * S);
    check('V1-136 playing tab (push includes playing) still playing at 4 min 40 s with no item change', playing && t.loads === 1, `playing=${playing} loads=${t.loads}`);
    await t.pass(40 * S);
    check('V1-136 playing tab reloads by 5 min after the push', t.loads === 2, `loads=${t.loads}`);
    const saved = await t.page.evaluate(() => sessionStorage.getItem('kbr_resume_playback'));
    check('V1-136 the reload saved "play on" for this screen', !!saved && JSON.parse(saved).path === '/stations', saved);
    await t.ctx.close();
  }
  // V1-136: "play on" is honoured only within 90 s of the reload
  if (want('resume')) for (const [age, expect] of [[80, true], [100, false]]) {
    const t2 = await openTabResume(browser, age);
    const on = await (async () => { for (let i = 0; i < 30; i++) { if (await t2.page.getByRole('button', { name: 'Stop', exact: true }).count()) return true; await sleep(100); } return false; })();
    check(`V1-136 Listen page after a release reload ${age} s ago ${expect ? 'plays on by itself' : 'waits for a press'}`, on === expect, `playing=${on}`);
    await t2.ctx.close();
  }
  // V1-137: a switched-on player holds updates, except after 10+ minutes hidden with nothing sounding
  if (want('away')) for (const [away, expect] of [[9 * MIN, false], [10 * MIN + 10 * S, true]]) {
    const t = await openTab(browser, '/listen/st-1');
    // (a fake clock holds animation frames, so no click() that waits for them)
    await t.page.getByRole('button', { name: 'Listen Live' }).dispatchEvent('click');
    await t.page.clock.runFor(1000);
    await t.page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ timeout: 5000 });
    await t.publish();
    await t.page.evaluate(() => window.__setHidden(true));
    await t.pass(5 * S);
    const heldHidden = t.loads === 1;
    // Screen locked: the browser stops the sound but the player stays switched on
    await t.page.evaluate(() => (window.__audios || []).forEach((a) => a.pause()));
   
    await t.pass(away, 60 * S);
   
    const before = t.loads;
    await t.page.evaluate(() => window.__setHidden(false));
    await t.waitReload(before, 2500);
    check(`V1-137 player switched on, hidden ${Math.round(away / S)} s: ${expect ? 'reloads on return' : 'no reload on return'}`, heldHidden && (t.loads > before) === expect, `heldHidden=${heldHidden} loads=${t.loads}`);
    await t.ctx.close();
  }
  // V1-138: a push at a set time follows the server's clock (device 10 min slow)
  if (want('at')) {
    const skew = 10 * MIN;
    const t = await openTab(browser, '/stations', { policy: { mode: 'manual', push: null }, serverSkew: skew });
    t.published = 'v-next';
    const at = (await t.serverNow()) + 30 * S;
    await t.setPolicy({ mode: 'manual', push: { id: 'p2', version: 'v-next', at: new Date(at).toISOString(), include_playing: false, pushed_at: new Date(at - 30 * S).toISOString(), pushed_by: 'admin' } });
    await sleep(300);
    await t.pass(20 * S);
    check('V1-138 push at server time +30 s: no reload at +20 s', t.loads === 1, `loads=${t.loads}`);
    await t.pass(20 * S);
    check('V1-138 push at server time +30 s: reloads by +40 s although the device clock is 10 min slow', t.loads === 2, `loads=${t.loads}`);
    await t.ctx.close();
  }
  // V1-138: a push with no version named lapses after 1 hour
  if (want('unnamed')) for (const [ago, expect] of [[50 * MIN, true], [70 * MIN, false]]) {
    const t = await openTab(browser, '/stations', { policy: { mode: 'manual', push: null } });
    t.published = 'v-next';
    const now = await t.serverNow();
    await t.setPolicy({ mode: 'manual', push: { id: `p-${ago}`, version: null, at: null, include_playing: false, pushed_at: new Date(now - ago).toISOString(), pushed_by: 'admin' } });
    await sleep(300);
    await t.pass(20 * S);
    check(`V1-138 unnamed push made ${ago / MIN} min ago: ${expect ? 'still taken' : 'lapsed, no reload'}`, (t.loads === 2) === expect, `loads=${t.loads}`);
    await t.ctx.close();
  }
  // V1-139: an offline version check is tried again a minute later, with no error
  if (want('offline')) {
    const t = await openTab(browser, '/stations', { versionFails: 1 });
    t.published = 'v-next';
    t.versionFails = 1;
    await t.page.evaluate(() => window.__setHidden(true));
    await t.page.evaluate(() => window.dispatchEvent(new Event('online')));
    await sleep(500);
    check('V1-139 offline check: no reload, no script error', t.loads === 1 && t.errors.length === 0, `loads=${t.loads} errors=${t.errors.join('|')}`);
    await t.pass(65 * S);
    check('V1-139 the next minute\'s check succeeds and the hidden tab reloads', t.loads === 2 && t.errors.length === 0, `loads=${t.loads} calls=${t.versionCalls}`);
    await t.ctx.close();
  }
} finally {
  await browser.close();
}
process.exit(done());

async function openTabResume(browser, ageS) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const station = makeStation();
  await ctx.addInitScript(AUDIO_PROBE);
  await ctx.addInitScript((age) => { sessionStorage.setItem('kbr_resume_playback', JSON.stringify({ path: '/listen/st-1', at: Date.now() - age * 1000 })); }, ageS);
  await routeApi(ctx, station);
  await routeNowPlaying(ctx, station);
  await ctx.routeWebSocket(/\/ws\/(live|listeners|site)/, () => {});
  const page = await ctx.newPage();
  await page.goto(`${BASE}/listen/${station.SID}`);
  await page.getByRole('button', { name: /Listen Live|Stop/ }).first().waitFor({ timeout: 5000 }).catch(() => {});
  return { ctx, page };
}
