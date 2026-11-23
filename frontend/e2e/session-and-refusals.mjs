/**
 * Sign-in renewal failures, one shared renewal for many 401s, and the "not allowed"
 * notice when the server refuses a station a login may not operate. Fake backend
 * (every /api/v1 call is answered here); nothing real is touched.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4173 --strictPort &)
 *   BASE_URL=http://localhost:4173 node e2e/session-and-refusals.mjs
 *
 * Checklist: V2-95 (renewal fails -> both tokens removed, /admin/login or /sponsor/login;
 * several 401s at once share one /auth/refresh), B-ROLE-22 and F-ACCESS-13 (a refused
 * station write shows the "not allowed" notice naming the login; nothing changes).
 * Exits non-zero if a check fails.
 */
import { chromium, checker, sleep, allowEverywhere } from './lib/fake-station.mjs';

// What this test provokes on purpose (anything else on screen is a finding).
allowEverywhere(/is not allowed to do that\. Nothing was changed/, 'the refused station write under test (B-ROLE-22)');
allowEverywhere(/Your sign-in has expired\. Please sign in again\./, 'V2-95 makes renewal fail on purpose: the sign-in page then says why (notice added after this suite)');

const BASE = process.env.BASE_URL || 'http://localhost:4173';
const { check, done } = checker({});

const STATIONS = [
  { id: 's-fm', name: "Kol B'ramah FM", type: 'internet', timezone: 'America/New_York', is_active: true, protected: true, automation_config: {} },
  { id: 's-web', name: "Kol B'ramah Web", type: 'internet', timezone: 'America/New_York', is_active: true, protected: false, automation_config: {} },
];
const user = (role, extra = {}) => ({ id: `u-${role}`, email: `${role}@t.test`, role, display_name: role, is_active: true, station_ids: null, ...extra });

function json(route, status, body) {
  return route.fulfill({ status, contentType: 'application/json', body: body === undefined ? '' : JSON.stringify(body) });
}

function generic(path) {
  if (path.startsWith('/stations')) return { stations: STATIONS, total: STATIONS.length };
  if (path.includes('count')) return { count: 0 };
  if (path.startsWith('/site-release')) return { mode: 'auto', push: null };
  return [];
}

/** A context with tokens in storage and the API answered by `handler(method, path, auth)`. */
async function context(browser, handler, tokens = { access: 'old-access', refresh: 'old-refresh' }) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  await ctx.addInitScript((t) => {
    if (sessionStorage.getItem('__seeded')) return;
    localStorage.setItem('access_token', t.access);
    localStorage.setItem('refresh_token', t.refresh);
    sessionStorage.setItem('__seeded', '1');
  }, tokens);
  const calls = [];
  await ctx.routeWebSocket(/\/ws\//, () => {});
  await ctx.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^.*\/api\/v1/, '');
    const auth = req.headers().authorization || '';
    calls.push({ method: req.method(), path, auth });
    const out = await handler(req.method(), path, auth, req);
    if (out === undefined) return json(route, 200, generic(path));
    return json(route, out.status, out.body);
  });
  return { ctx, calls };
}

async function renewalFails(browser, { start, login, tag }) {
  const { ctx, calls } = await context(browser, async (method, path) => {
    if (path === '/auth/refresh') return { status: 401, body: { detail: 'Invalid refresh token' } };
    return { status: 401, body: { detail: 'Not authenticated' } };
  });
  const page = await ctx.newPage();
  await page.goto(`${BASE}${start}`);
  for (let i = 0; i < 60 && new URL(page.url()).pathname !== login; i++) await sleep(100);
  await page.waitForLoadState('load').catch(() => {});
  await sleep(500); // the login page settles (it may load twice)
  const path = new URL(page.url()).pathname;
  check(`V2-95 ${tag}: expired renewal goes to ${login}`, path === login, path);
  let left = null;
  for (let i = 0; i < 5 && !left; i++) {
    left = await page.evaluate(() => [localStorage.getItem('access_token'), localStorage.getItem('refresh_token')]).catch(() => null);
    if (!left) await sleep(300);
  }
  left = left || ['?', '?'];
  check(`V2-95 ${tag}: both tokens removed`, left[0] === null && left[1] === null, JSON.stringify(left));
  check(`V2-95 ${tag}: renewal was tried`, calls.some((c) => c.path === '/auth/refresh'));
  await ctx.close();
}

async function sharedRenewal(browser) {
  let refreshes = 0;
  const { ctx, calls } = await context(browser, async (method, path, auth) => {
    if (path === '/auth/refresh') {
      refreshes++;
      await sleep(400); // slow enough that the page's other requests fail meanwhile
      return { status: 200, body: { access_token: 'new-access', refresh_token: 'new-refresh', token_type: 'bearer' } };
    }
    if (auth !== 'Bearer new-access') return { status: 401, body: { detail: 'expired' } };
    if (path === '/auth/me') return { status: 200, body: user('manager') };
    return undefined;
  });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/admin/stations`);
  await page.getByText("Kol B'ramah Web").first().waitFor({ timeout: 8000 }).catch(() => {});
  await sleep(800);
  const failed = calls.filter((c) => c.auth === 'Bearer old-access').length;
  check('V2-95 shared renewal: page loads after the renewal', await page.getByText("Kol B'ramah Web").first().isVisible().catch(() => false));
  check('V2-95 shared renewal: several requests failed with 401 together', failed >= 2, `${failed} requests with the old token`);
  check('V2-95 shared renewal: one /auth/refresh for all of them', refreshes === 1, `${refreshes} refresh calls`);
  check('V2-95 shared renewal: still signed in on the same page', new URL(page.url()).pathname === '/admin/stations');
  await ctx.close();
}

async function refusedStation(browser) {
  // A manager limited to the web station; the protected FM station is refused by the server
  const me = user('manager', { station_ids: ['s-web'] });
  const { ctx, calls } = await context(browser, async (method, path) => {
    if (path === '/auth/me') return { status: 200, body: me };
    if (method !== 'GET' && path.startsWith('/stations/s-fm')) {
      return { status: 403, body: { detail: 'Your login is not set up to operate this station' } };
    }
    return undefined;
  }, { access: 'a', refresh: 'r' });
  const page = await ctx.newPage();
  page.on('dialog', (d) => d.accept());
  await page.goto(`${BASE}/admin/stations`);
  const row = page.locator('tr', { hasText: "Kol B'ramah FM" });
  await row.waitFor({ timeout: 8000 });
  await row.getByRole('button', { name: 'Delete' }).click();
  const note = page.getByTestId('not-allowed');
  const shown = await note.waitFor({ timeout: 3000 }).then(() => true).catch(() => false);
  check('B-ROLE-22 refused station write shows the "not allowed" notice', shown);
  const text = shown ? await note.innerText() : '';
  check('B-ROLE-22 notice names the login and says nothing changed', /manager/.test(text) && /Nothing was changed/.test(text), text);
  check('F-ACCESS-13 the refused DELETE went to the server and was refused (403)',
    calls.some((c) => c.method === 'DELETE' && c.path === '/stations/s-fm'));
  check('F-ACCESS-13 station still listed after the refusal', await row.isVisible());
  // A read the server refuses (GET 403) does not show the notice
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await renewalFails(browser, { start: '/admin/stations', login: '/admin/login', tag: 'admin page' });
  await renewalFails(browser, { start: '/sponsor/dashboard', login: '/sponsor/login', tag: 'sponsor page' });
  await sharedRenewal(browser);
  await refusedStation(browser);
} finally {
  await browser.close();
}
process.exit(done());
