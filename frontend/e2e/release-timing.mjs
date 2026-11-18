/**
 * Release pickup timings (src/lib/appUpdate.ts) on the built site, with Playwright's
 * fake clock so minutes pass in moments. e2e/site-release/check.cjs runs the same
 * flows in real time against two real builds; this file pins the time limits:
 *
 * - a tab never reloads for a release twice within 2 minutes (a stale copy would loop),
 *   also when the browser refuses sessionStorage; a guard time "in the future" (device
 *   clock set back) does not block releases;
 * - a playing tab included in a push reloads at the latest 5 minutes after the push,
 *   and the page plays on by itself only if the reload was under 90 s ago;
 * - a silent visible tab waits until the person has been idle 30 s, a hidden one
 *   reloads at once, audio heard in the last 2 minutes holds it, and a player that is
 *   switched on stops holding it after the tab was hidden 10+ minutes (but a focused
 *   form field still does);
 * - a push "at" a time follows the server's clock, not the device's (slow or fast),
 *   and a push with no version named lapses after 1 hour;
 * - a version check made while offline or mid-deploy (404, HTML) is simply tried
 *   again next minute, with no reload and no error;
 * - error screen (components/ErrorBoundary.tsx): a crashing screen shows "Something
 *   went wrong" + Reload Page and stays; a missing code file shows "The site was
 *   updated while this tab was open" and reloads by itself 0.3 s later.
 *
 * Scenarios run in parallel (each tab has its own fake clock), CONCURRENCY at a time.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4311 --strictPort &)
 *   BASE_URL=http://localhost:4311 node e2e/release-timing.mjs
 *   # optional: ONLY=idle,hidden,grace,loop,loop-nostorage,loop-clockback,push,resume,away,away-typing,at,at-fast,unnamed,offline,middeploy,crash,stale  CONCURRENCY=6
 */
import { chromium, checker, sleep, makeStation, routeApi, routeNowPlaying, AUDIO_PROBE } from './lib/fake-station.mjs';
import { allowEverywhere } from './lib/guard.mjs';

// Provoked on purpose; anything else the screen or console shows fails the run (e2e/lib/guard.mjs)
allowEverywhere(/e2e page crash|Failed to fetch dynamically imported module|The site was updated while this tab was open|not a valid JavaScript MIME type|Expected a JavaScript-or-Wasm module script|Something went wrong/, 'a page crash and a release under an open tab are staged to see the recovery screens');

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const CONCURRENCY = Number(process.env.CONCURRENCY || 6);
const { check, done } = checker({});
const want = (n) => !process.env.ONLY || process.env.ONLY.split(',').includes(n);
const S = 1000;
const MIN = 60 * S;

function HIDDEN_SWITCH() {
  Object.defineProperty(Document.prototype, 'hidden', { configurable: true, get() { return !!window.__hidden; } });
  Object.defineProperty(Document.prototype, 'visibilityState', { configurable: true, get() { return window.__hidden ? 'hidden' : 'visible'; } });
  window.__setHidden = (h) => { window.__hidden = h; document.dispatchEvent(new Event('visibilitychange')); };
}

/** Site data blocked: touching sessionStorage throws, as in Chrome with cookies blocked. */
function NO_SESSION_STORAGE() {
  Object.defineProperty(window, 'sessionStorage', { configurable: true, get() { throw new DOMException('The operation is insecure.', 'SecurityError'); } });
}

/**
 * One tab on `path` with a fake clock. `serverSkew`: the server's clock minus this
 * device's (sent as server_time with the policy). `versionFails`: how the next version
 * checks fail ('offline' | '404' | 'html'). Returns helpers to set the policy, publish
 * a new version and count reloads.
 */
async function openTab(browser, path, { policy = { mode: 'auto', push: null }, serverSkew = 0, versionFails = [], initScripts = [] } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const station = makeStation();
  await ctx.addInitScript(AUDIO_PROBE);
  await ctx.addInitScript(HIDDEN_SWITCH);
  for (const s of initScripts) await ctx.addInitScript(s);
  await routeApi(ctx, station);
  await routeNowPlaying(ctx, station);
  await ctx.routeWebSocket(/\/ws\/(live|listeners)/, () => {});
  const tab = { ctx, station, policy, published: null, versionFails: [...versionFails], versionCalls: 0, sockets: [] };
  let page;
  const serverTime = async () => new Date((await page.evaluate(() => Date.now())) + serverSkew).toISOString();
  const send = async (ws) => ws.send(JSON.stringify({ type: 'release', data: { ...tab.policy, server_time: await serverTime() } }));
  await ctx.routeWebSocket(/\/ws\/site/, (ws) => { tab.sockets.push(ws); send(ws).catch(() => {}); ws.onMessage(() => {}); });
  await ctx.route(/\/version\.json/, (route) => {
    tab.versionCalls++;
    const fail = tab.versionFails.shift();
    if (fail === 'offline') return route.abort('internetdisconnected');
    if (fail === '404') return route.fulfill({ status: 404, contentType: 'text/plain', body: 'Not found' });
    if (fail === 'html') return route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><html><body>deploying</body></html>' });
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
  /** Switch the Listen page's player on (a fake clock holds animation frames, so no click() that waits for them). */
  tab.listen = async () => {
    await page.getByRole('button', { name: 'Listen Live' }).dispatchEvent('click');
    await page.clock.runFor(1000);
    await page.getByRole('button', { name: 'Stop', exact: true }).waitFor({ timeout: 5000 });
  };
  return tab;
}

const scenarios = [];
const scenario = (name, fn) => { if (want(name)) scenarios.push({ name, fn }); };

// V1-137: a silent visible tab waits for 30 s of idleness
scenario('idle', async (browser) => {
  const t = await openTab(browser, '/stations');
  await t.touch();
  await t.publish();
  await t.pass(25 * S);
  check('V1-137 visible silent tab: no reload while the person was active < 30 s ago', t.loads === 1, `loads=${t.loads}`);
  await t.pass(10 * S);
  check('V1-137 visible silent tab: reloads once idle 30 s', t.loads === 2, `loads=${t.loads}`);
  await t.ctx.close();
});

// V1-137: a hidden tab reloads at once
scenario('hidden', async (browser) => {
  const t = await openTab(browser, '/stations');
  await t.touch();
  await t.publish();
  const before = t.loads;
  await t.page.evaluate(() => window.__setHidden(true));
  check('V1-137 tab sent to the background reloads at once', await t.waitReload(before, 3000), `loads=${t.loads}`);
  await t.ctx.close();
});

// V1-137: audio heard in the last 2 minutes holds the reload
scenario('grace', async (browser) => {
  const t = await openTab(browser, '/stations');
  await t.publish();
  await t.page.evaluate(() => { const a = new Audio('/__tone/m1.wav'); a.muted = true; a.play().catch(() => {}); a.pause(); });
  await t.pass(110 * S);
  check('V1-137 audio stopped 110 s ago: no reload yet (2 min grace)', t.loads === 1, `loads=${t.loads}`);
  await t.pass(20 * S);
  check('V1-137 audio stopped 2 min ago: reloads', t.loads === 2, `loads=${t.loads}`);
  await t.ctx.close();
});

// V1-135: never two release reloads within 2 minutes (a stale copy would loop)
async function loopCase(browser, label, opts) {
  const t = await openTab(browser, '/stations', opts);
  await t.publish();
  await t.page.evaluate(() => window.__setHidden(true));
  await t.waitReload(1, 3000);
  check(`V1-135${label} first reload happens`, t.loads === 2, `loads=${t.loads}`);
  // The new page still sees a different published version (stale copy): it must not reload again at once
  await t.page.evaluate(() => window.dispatchEvent(new Event('online')));
  await t.page.evaluate(() => window.__setHidden(true));
  await t.pass(100 * S);
  check(`V1-135${label} reloaded tab does not reload again within 2 min`, t.loads === 2, `loads=${t.loads}`);
  await t.pass(30 * S);
  await t.page.evaluate(() => window.__setHidden(true));
  await t.waitReload(2, 3000);
  check(`V1-135${label} after 2 min it may reload again`, t.loads === 3, `loads=${t.loads}`);
  await t.ctx.close();
}
scenario('loop', (browser) => loopCase(browser, '', {}));
scenario('loop-nostorage', (browser) => loopCase(browser, ' (sessionStorage blocked)', { initScripts: [NO_SESSION_STORAGE] }));

// V1-135: a guard time in the future (the device clock was set back) does not hold releases
scenario('loop-clockback', async (browser) => {
  const t = await openTab(browser, '/stations');
  await t.page.evaluate(() => sessionStorage.setItem('kbr_release_reload_at', String(Date.now() + 3 * 3600 * 1000)));
  await t.publish();
  await t.page.evaluate(() => window.__setHidden(true));
  check('V1-135 guard time 3 h ahead (clock set back): hidden tab still reloads', await t.waitReload(1, 3000), `loads=${t.loads}`);
  await t.ctx.close();
});

// V1-136: a playing tab included in a push reloads at the latest 5 minutes after it
scenario('push', async (browser) => {
  const t = await openTab(browser, '/stations', { policy: { mode: 'manual', push: null } });
  await t.page.evaluate(() => { const a = new Audio('/__tone/m1.wav'); a.loop = true; window.__keep = a; a.play().catch(() => {}); }); await sleep(1500);
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
});

// V1-136: "play on" is honoured only within 90 s of the reload
for (const [age, expect] of [[80, true], [100, false]]) {
  scenario('resume', async (browser) => {
    const t2 = await openTabResume(browser, age);
    const on = await (async () => { for (let i = 0; i < 30; i++) { if (await t2.page.getByRole('button', { name: 'Stop', exact: true }).count()) return true; await sleep(100); } return false; })();
    check(`V1-136 Listen page after a release reload ${age} s ago ${expect ? 'plays on by itself' : 'waits for a press'}`, on === expect, `playing=${on}`);
    await t2.ctx.close();
  });
}

// V1-137: a switched-on player holds updates, except after 10+ minutes hidden with nothing sounding
for (const [away, expect] of [[9 * MIN, false], [10 * MIN + 10 * S, true]]) {
  scenario('away', async (browser) => {
    const t = await openTab(browser, '/listen/st-1');
    await t.listen();
    await t.publish();
    await t.page.evaluate(() => window.__setHidden(true));
    await t.pass(5 * S);
    const heldHidden = t.loads === 1;
    // Screen locked: the browser stops the sound but the player stays switched on
    await t.page.evaluate(() => (window.__audios || []).forEach((a) => a.pause()));
    // A hidden tab's timers are throttled anyway: jump the clock (each due timer fires once)
    await t.page.clock.fastForward(away);
    await t.pass(2 * S);
    const before = t.loads;
    await t.page.evaluate(() => window.__setHidden(false));
    await t.waitReload(before, 2500);
    check(`V1-137 player switched on, hidden ${Math.round(away / S)} s: ${expect ? 'reloads on return' : 'no reload on return'}`, heldHidden && (t.loads > before) === expect, `heldHidden=${heldHidden} loads=${t.loads}`);
    await t.ctx.close();
  });
}

// V1-137: back after 10+ min away with a form field still focused: no reload (the text would be lost)
scenario('away-typing', async (browser) => {
  const t = await openTab(browser, '/stations');
  await t.page.evaluate(() => { const i = document.createElement('textarea'); i.id = 'draft'; document.body.appendChild(i); i.focus(); i.value = 'half-written message'; });
  await t.publish();
  await t.page.evaluate(() => window.__setHidden(true));
  await t.pass(5 * S);
  const heldHidden = t.loads === 1;
  await t.page.clock.fastForward(11 * MIN);
  await t.pass(2 * S);
  await t.page.evaluate(() => window.__setHidden(false));
  await t.waitReload(1, 2500);
  check('V1-137 hidden 11 min with a form field focused: no reload on return', heldHidden && t.loads === 1, `heldHidden=${heldHidden} loads=${t.loads}`);
  await t.ctx.close();
});

// V1-138: a push at a set time follows the server's clock, with the device clock 10 min slow or fast
for (const [name, skew, label] of [['at', 10 * MIN, 'slow'], ['at-fast', -10 * MIN, 'fast']]) {
  scenario(name, async (browser) => {
    const t = await openTab(browser, '/stations', { policy: { mode: 'manual', push: null }, serverSkew: skew });
    t.published = 'v-next';
    const at = (await t.serverNow()) + 30 * S;
    await t.setPolicy({ mode: 'manual', push: { id: 'p2', version: 'v-next', at: new Date(at).toISOString(), include_playing: false, pushed_at: new Date(at - 30 * S).toISOString(), pushed_by: 'admin' } });
    await sleep(300);
    await t.pass(20 * S);
    check(`V1-138 push at server time +30 s, device clock 10 min ${label}: no reload at +20 s`, t.loads === 1, `loads=${t.loads}`);
    await t.pass(20 * S);
    check(`V1-138 push at server time +30 s, device clock 10 min ${label}: reloads by +40 s`, t.loads === 2, `loads=${t.loads}`);
    await t.ctx.close();
  });
}

// V1-138: a push with no version named lapses after 1 hour
for (const [ago, expect] of [[50 * MIN, true], [70 * MIN, false]]) {
  scenario('unnamed', async (browser) => {
    const t = await openTab(browser, '/stations', { policy: { mode: 'manual', push: null } });
    t.published = 'v-next';
    const now = await t.serverNow();
    await t.setPolicy({ mode: 'manual', push: { id: `p-${ago}`, version: null, at: null, include_playing: false, pushed_at: new Date(now - ago).toISOString(), pushed_by: 'admin' } });
    await sleep(300);
    await t.pass(20 * S);
    check(`V1-138 unnamed push made ${ago / MIN} min ago: ${expect ? 'still taken' : 'lapsed, no reload'}`, (t.loads === 2) === expect, `loads=${t.loads}`);
    await t.ctx.close();
  });
}

// V1-139: an offline / mid-deploy version check is tried again a minute later, with no reload and no error
for (const [name, fail, label] of [['offline', 'offline', 'offline'], ['middeploy', '404', 'mid-deploy 404'], ['middeploy', 'html', 'mid-deploy HTML page']]) {
  scenario(name, async (browser) => {
    const t = await openTab(browser, '/stations');
    t.published = 'v-next';
    t.versionFails = [fail];
    const calls = t.versionCalls;
    await t.page.evaluate(() => window.__setHidden(true));
    await t.page.evaluate(() => window.dispatchEvent(new Event('online')));
    await sleep(500);
    const box = await t.page.locator('[data-testid=error-boundary]').count();
    check(`V1-139 ${label} check: no reload, no error`, t.versionCalls > calls && t.loads === 1 && t.errors.length === 0 && box === 0, `loads=${t.loads} calls=${t.versionCalls - calls} box=${box} errors=${t.errors.join('|')}`);
    await t.pass(65 * S);
    check(`V1-139 ${label}: the next minute's check succeeds and the hidden tab reloads`, t.loads === 2 && t.errors.length === 0, `loads=${t.loads} calls=${t.versionCalls}`);
    await t.ctx.close();
  });
}

// V2-97: a screen that crashes shows "Something went wrong" + its error + Reload Page, and stays
scenario('crash', async (browser) => {
  const t = await openTab(browser, '/stations');
  await t.page.route(/\/assets\/Archives-[^/]+\.js$/, (r) => r.fulfill({ contentType: 'text/javascript', body: 'throw new Error("e2e page crash");' }));
  await t.page.evaluate(() => { window.history.pushState({}, '', '/archives'); window.dispatchEvent(new PopStateEvent('popstate')); });
  const box = t.page.getByTestId('error-boundary');
  await box.waitFor({ timeout: 5000 }).catch(() => {});
  const text = (await box.count()) ? await box.innerText() : '';
  const btn = await box.getByRole('button', { name: 'Reload Page' }).count();
  await t.pass(5 * S);
  check('V2-97 page crash: "Something went wrong", the error text and Reload Page; no reload by itself', text.includes('Something went wrong') && text.includes('e2e page crash') && btn === 1 && t.loads === 1, `loads=${t.loads} text=${JSON.stringify(text)}`);
  await box.getByRole('button', { name: 'Reload Page' }).dispatchEvent('click');
  check('V2-97 Reload Page reloads', await t.waitReload(1, 3000), `loads=${t.loads}`);
  await t.ctx.close();
});

// V2-97: a missing code file after a release. With no audio heard lately the tab reloads
// at once (vite:preloadError); with audio heard in the last 2 min (the person just left
// Listen) the error box says the site was updated and reloads by itself 0.3 s later.
scenario('stale', async (browser) => {
  const t = await openTab(browser, '/stations');
  await t.page.route(/\/assets\/Archives-[^/]+\.js$/, (r) => r.fulfill({ status: 404, body: 'gone' }));
  await t.page.evaluate(() => { window.history.pushState({}, '', '/archives'); window.dispatchEvent(new PopStateEvent('popstate')); });
  check('V2-97 missing code file, no audio lately: reloads onto the new release at once', await t.waitReload(1, 3000), `loads=${t.loads}`);
  await t.ctx.close();
});
scenario('stale', async (browser) => {
  const t = await openTab(browser, '/stations');
  // Sound stopped a moment ago (leaving Listen): inside the 2-min grace, nothing sounding now
  await t.page.evaluate(() => { const a = new Audio('/__tone/m1.wav'); a.muted = true; a.play().catch(() => {}); a.pause(); });
  await t.page.route(/\/assets\/Archives-[^/]+\.js$/, (r) => r.fulfill({ status: 404, body: 'gone' }));
  // Stop the fake clock so only runFor moves it (otherwise it follows real time)
  await t.page.clock.pauseAt(await t.page.evaluate(() => Date.now() + 1000));
  await t.page.evaluate(() => { window.history.pushState({}, '', '/archives'); window.dispatchEvent(new PopStateEvent('popstate')); });
  const msg = t.page.getByText('The site was updated while this tab was open');
  const shown = await msg.waitFor({ timeout: 5000 }).then(() => true, () => false);
  await t.page.clock.runFor(200); await sleep(500);
  const early = t.loads;
  await t.page.clock.runFor(200);
  const reloaded = await t.waitReload(1, 3000);
  check('V2-97 missing code file just after audio: "The site was updated while this tab was open", reloads by itself after 0.3 s (not before)', shown && early === 1 && reloaded, `shown=${shown} loadsAt0.2s=${early} loads=${t.loads}`);
  await t.ctx.close();
});

// The real host answers a missing code file with the site's own page (200, text/html),
// not a 404, and Safari words the failure "'text/html' is not a valid JavaScript MIME
// type." (Shmuel's iPhone 2026-11-17: "Something went wrong" and a Reload button).
for (const [how, answer] of [
  ['the host answers with the site page (200 text/html)', (r) => r.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>KBR</title>' })],
  ["Safari's wording of that failure", (r) => r.fulfill({ status: 200, contentType: 'text/javascript', body: `throw new TypeError("'text/html' is not a valid JavaScript MIME type.");` })],
]) {
  scenario('stale', async (browser) => {
    const t = await openTab(browser, '/stations');
    await t.page.route(/\/assets\/Archives-[^/]+\.js$/, answer);
    await t.page.evaluate(() => { window.history.pushState({}, '', '/archives'); window.dispatchEvent(new PopStateEvent('popstate')); });
    const reloaded = await t.waitReload(1, 3000);
    const wrong = await t.page.getByText('Something went wrong').count();
    check(`F-ACCESS-17 missing code file, ${how}: reloads onto the new release by itself, never "Something went wrong"`, reloaded && wrong === 0, `loads=${t.loads} box=${wrong}`);
    await t.ctx.close();
  });
  scenario('stale', async (browser) => {
    const t = await openTab(browser, '/stations');
    // Sound stopped a moment ago: the error box shows first, and it must be the "site was updated" one
    await t.page.evaluate(() => { const a = new Audio('/__tone/m1.wav'); a.muted = true; a.play().catch(() => {}); a.pause(); });
    await t.page.route(/\/assets\/Archives-[^/]+\.js$/, answer);
    await t.page.clock.pauseAt(await t.page.evaluate(() => Date.now() + 1000));
    await t.page.evaluate(() => { window.history.pushState({}, '', '/archives'); window.dispatchEvent(new PopStateEvent('popstate')); });
    const shown = await t.page.getByText('The site was updated while this tab was open').waitFor({ timeout: 5000 }).then(() => true, () => false);
    const wrong = await t.page.getByText('Something went wrong').count();
    await t.page.clock.runFor(400);
    const reloaded = await t.waitReload(1, 3000);
    check(`F-ACCESS-17 missing code file just after audio, ${how}: "The site was updated", then reloads by itself`, shown && wrong === 0 && reloaded, `shown=${shown} box=${wrong} loads=${t.loads}`);
    await t.ctx.close();
  });
}

const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
try {
  const queue = [...scenarios];
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, async () => {
    while (queue.length) {
      const { name, fn } = queue.shift();
      try { await fn(browser); } catch (e) { check(`${name}: scenario ran to the end`, false, String(e).split('\n')[0]); }
    }
  }));
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
