/**
 * The control room when the login has expired or the network drops, against the fake
 * station (e2e/lib/fake-station.mjs; nothing real is touched):
 *   V3-13  the access token has expired (open > 30 min): Next renews the login by itself
 *          and that press goes through, once
 *   V3-14  the renewal has expired too (open overnight): the operator is told to sign in
 *          again, and the press is not replayed later
 *   V3-19  the network drops: a press made meanwhile fails visibly and is not sent late;
 *          when the network is back the screen catches up without a reload
 *
 *   cd frontend && npm run build && (npx vite preview --port 4302 --strictPort &)
 *   BASE_URL=http://localhost:4302 node e2e/staff-sessions.mjs
 * Exits non-zero if a check fails (a KNOWN entry below is a bug kept out of the pass count).
 */
import { chromium, checker, sleep, makeStation, adminContext, newPage, allowEverywhere } from './lib/fake-station.mjs';

allowEverywhere(/net::ERR_INTERNET_DISCONNECTED|Network Error|did not go through|Could not|Can't reach|offline|connection/i, 'the network is cut on purpose (V3-19)');

const BASE = process.env.BASE_URL || 'http://localhost:4302';
const DESKTOP = { viewport: { width: 1440, height: 900 } };
const KNOWN = {
  'V3-14 the login page says the sign-in expired (sign in again)': 'found 2026-11-19: an expired renewal sends the operator to the plain login form with no word that the sign-in expired or that the press was not done',
};
const { check, done } = checker(KNOWN);

/** The control room, with the API behind a switch: `net.mode` = ok | expired | dead | down. */
async function openRoom(browser, station) {
  const { ctx, np } = await adminContext(browser, DESKTOP, station, { storage: { radio_last_station: null } });
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);
  const net = { mode: 'ok', seen: [], refreshes: 0 };
  await page.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^.*\/api\/v1/, '');
    const auth = req.headers().authorization || '';
    net.seen.push({ method: req.method(), path, auth, mode: net.mode, at: Date.now() });
    if (net.mode === 'down') return route.abort('internetdisconnected');
    if (path === '/auth/refresh') {
      net.refreshes++;
      if (net.mode === 'dead') return route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"Invalid refresh token"}' });
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ access_token: 'renewed', refresh_token: 'renewed-r', token_type: 'bearer' }) });
    }
    if (net.mode === 'expired' && auth !== 'Bearer renewed') {
      return route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"Token expired"}' });
    }
    if (net.mode === 'dead') return route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"Token expired"}' });
    return route.fallback();
  });
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  return { ctx, np, page, errors, net };
}
const skips = (st) => st.calls.filter((c) => c.method === 'POST' && c.path.endsWith('/queue/skip'));
const goNext = (page) => page.getByRole('button', { name: /Go next/ }).first();

async function expiredLogin(browser) {
  const station = makeStation();
  const { ctx, page, net } = await openRoom(browser, station);
  await sleep(5000); // past the "just changed" window
  net.mode = 'expired';
  const before = skips(station).length;
  const was = station.playing().asset_id;
  await goNext(page).click();
  await sleep(1500);
  const done = skips(station).length - before;
  check('V3-13 Next with an expired login renews it once', net.refreshes === 1, `${net.refreshes} renewals`);
  check('V3-13 that press goes through, once', done === 1 && station.playing().asset_id !== was, `${done} skips reached the station`);
  check('V3-13 still in the control room', new URL(page.url()).pathname === '/admin/dashboard', page.url());
  check('V3-13 the new login is kept', await page.evaluate(() => localStorage.getItem('access_token')) === 'renewed');
  await ctx.close();
}

async function deadLogin(browser) {
  const station = makeStation();
  const { ctx, page, net } = await openRoom(browser, station);
  await sleep(5000);
  net.mode = 'dead';
  const before = skips(station).length;
  await goNext(page).click();
  await page.waitForURL(/\/admin\/login/, { timeout: 5000 }).catch(() => {});
  check('V3-14 an expired renewal goes to the sign-in page', new URL(page.url()).pathname === '/admin/login', page.url());
  const text = await page.locator('body').innerText().catch(() => '');
  check('V3-14 the login page says the sign-in expired (sign in again)', /expired|sign in again|signed out/i.test(text), text.replace(/\s+/g, ' ').slice(0, 120));
  // Signing in again (the test's start-up script puts a login back) does not replay the press
  net.mode = 'ok';
  await sleep(500);
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  await sleep(2500);
  check('V3-14 the refused press is not replayed after signing in again', skips(station).length === before, `${skips(station).length - before} skips`);
  await ctx.close();
}

async function networkDrop(browser) {
  const station = makeStation();
  const { ctx, np, page, net } = await openRoom(browser, station);
  await sleep(5000);
  const before = skips(station).length;
  net.mode = 'down';
  np.dropAll();
  await goNext(page).click();
  await sleep(2500);
  const shown = await page.locator('[data-testid="toast"], [role="alert"], [role="status"]').allInnerTexts().catch(() => []);
  const visibleText = shown.join(' | ').replace(/\s+/g, ' ');
  check('V3-19 a press with the network down fails visibly', visibleText.length > 0 && !/^\s*$/.test(visibleText), visibleText || 'nothing shown');
  // Meanwhile the station moves on by itself (a 210 s song starts; nothing reaches the page)
  station.pushOnChange = false;
  const nxt = station.goOnAir('m60');
  await sleep(2000);
  // The screen catches up (socket reconnects or polling) without a reload
  // The new song is 210 s long, so its remaining time is far from the old song's
  const remaining = async () => { const t = await page.getByTestId('remaining').first().innerText().catch(() => ''); const [m, x] = t.split(':'); return +m * 60 + +x; };
  const want = () => station.byId[nxt.asset_id].duration - (Date.now() - new Date(nxt.started_at).getTime()) / 1000;
  const r0 = await remaining();
  check('V3-19 (before) the screen still shows the old song while offline', Math.abs(r0 - want()) > 20, `${r0} s left`);
  // Network back. The fake push channel cannot be cut, so a socket the page reopened while
  // "offline" is closed now: in real life no socket opens until the network is back
  net.mode = 'ok';
  np.dropAll();
  let caught = false;
  for (let i = 0; i < 200 && !caught; i++) { caught = Math.abs((await remaining()) - want()) <= 3; if (!caught) await sleep(100); }
  check('V3-19 the screen shows what is on air now, without a reload', caught, `${await remaining()} s left, want ${Math.round(want())}; sockets opened ${np.opened.length}; queue reads after back ${net.seen.filter((c) => c.mode === 'ok' && /queue/.test(c.path)).length}`);
  await sleep(3000);
  check('V3-19 the press made while offline is never sent late', skips(station).length === before, `${skips(station).length - before} skips`);
  check('V3-19 no reload happened', (await page.evaluate(() => performance.getEntriesByType('navigation').length)) === 1
    && net.seen.filter((c) => c.path === '/auth/me').length <= 2);
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await expiredLogin(browser);
  await deadLogin(browser);
  await networkDrop(browser);
} finally {
  await browser.close();
}
process.exit(done());
