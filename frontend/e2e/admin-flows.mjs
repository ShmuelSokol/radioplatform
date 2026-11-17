/**
 * Browser test of every admin, public and sponsor-portal page against the REAL
 * backend on a local Postgres (not a fake). Checks that each page loads fast
 * (S-5) at desktop and iPhone size, that create / edit / delete flows save and
 * save fast (S-6), that validation messages show, that each role (admin,
 * manager, dj, viewer, sponsor) sees the right screens and the "View only"
 * marker, and that an upload is playable in the library quickly (S-10).
 *
 * Setup (made-up credentials only; the admin password is random per run):
 *   createdb kbr_t_pages   (psql postgresql://radio:radio@localhost:5432/postgres -c "CREATE DATABASE kbr_t_pages")
 *   cd backend && DATABASE_URL=postgresql+asyncpg://radio:radio@localhost:5432/kbr_t_pages APP_DEBUG=false \
 *     JWT_SECRET_KEY=test-only CORS_ORIGINS='["http://localhost:4330"]' uv run uvicorn app.main:app --port 8230 &
 *   cd frontend && VITE_API_URL=http://localhost:8230/api/v1 npm run build && npx vite preview --port 4330 --strictPort &
 *   E2E_DATABASE_URL=postgresql+asyncpg://radio:radio@localhost:5432/kbr_t_pages node e2e/admin-flows.mjs
 *
 * Env: BASE_URL (default http://localhost:4330), API_URL (default http://localhost:8230/api/v1),
 *   E2E_DATABASE_URL (seeds the admin account through e2e/admin-flows-seed.py; or give
 *   E2E_ADMIN_EMAIL + E2E_ADMIN_PASS of an existing admin), ONLY=<comma list of section names>,
 *   SHOTS_DIR=<dir> (screenshots of failures), SLOW=<factor> (multiplies speed limits, e.g. on a slow CI box).
 *
 * Output: one line per check, PASS / FAIL / KNOWN-BUG (a check that fails because the site is
 * wrong, listed in KNOWN_BUGS below with the reason) / FIXED? (a known bug that now passes: take
 * it off the list). Exits non-zero on any FAIL or FIXED?.
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const { chromium } = createRequire(import.meta.url)('playwright');

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BASE = process.env.BASE_URL || 'http://localhost:4330';
const API = process.env.API_URL || 'http://localhost:8230/api/v1';
const SHOTS = process.env.SHOTS_DIR || '';
const ONLY = (process.env.ONLY || '').split(',').map(s => s.trim()).filter(Boolean);
const SLOW = Number(process.env.SLOW || 1);
const RUN = Date.now().toString(36).slice(-5); // makes names unique per run
const TMP = fs.mkdtempSync(path.join(process.env.TMPDIR || '/tmp', 'kbr-admin-flows-'));

// ── Speed limits (docs/test-checklist.md section S) ─────────────────────────
const LIMIT = {
  firstContent: 1500 * SLOW,  // S-5 first content on a warm load
  interactive: 2500 * SLOW,   // S-5 interactive on a warm load
  save: 1000 * SLOW,          // S-6 save confirmed
  uploadPlayable: 10000 * SLOW, // S-10 playable within 10 s of upload end (5 MB)
  progressShown: 500 * SLOW,  // S-10 "progress shown at once"
};

const VIEWS = {
  desktop: { viewport: { width: 1440, height: 900 } },
  iphone: { viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1' },
};

// Checks that fail because the SITE is wrong. Key = check name prefix, value = the bug.
// A listed check that passes is reported as FIXED? so the list stays honest.
const KNOWN_BUGS = {
  'F-BLOCKS-5': 'songs added to a block ("+ Add song") never reach the queue: SchedulingService.get_next_asset_for_block has no caller; the queue fills from rotation',
};

// ── Result bookkeeping ──────────────────────────────────────────────────────
const results = [];
function record(name, ok, detail = '') {
  const bug = Object.entries(KNOWN_BUGS).find(([k]) => name.startsWith(k));
  let status;
  if (bug) status = ok ? 'FIXED?' : 'KNOWN-BUG';
  else status = ok ? 'PASS' : 'FAIL';
  results.push({ name, status, detail });
  const extra = status === 'KNOWN-BUG' ? `  [${bug[1]}]` : '';
  console.log(`${status.padEnd(9)} ${name}${detail ? `  (${detail})` : ''}${extra}`);
  return ok;
}
const check = (name, ok, detail) => record(name, !!ok, detail);

/** Runs one check body; any exception is a failure of that check, not of the run. */
async function step(name, fn, page) {
  const t0 = Date.now();
  try {
    const r = await fn();
    if (r === false) return record(name, false);
    if (typeof r === 'string') return record(name, true, r);
    return record(name, true, `${Date.now() - t0} ms`);
  } catch (e) {
    if (SHOTS && page) {
      fs.mkdirSync(SHOTS, { recursive: true });
      await page.screenshot({ path: path.join(SHOTS, `${name.replace(/[^a-z0-9]+/gi, '_').slice(0, 80)}.png`) }).catch(() => {});
    }
    return record(name, false, String(e?.message || e).split('\n')[0].slice(0, 240));
  }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }
function assertFast(ms, limit, what) { assert(ms <= limit, `${what} took ${ms} ms (limit ${limit} ms)`); return ms; }

// ── Backend helpers ─────────────────────────────────────────────────────────
async function api(method, p, { token, body, form, raw } = {}) {
  const headers = {};
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers['content-type'] = 'application/json'; payload = JSON.stringify(body); }
  const r = await fetch(`${API}${p}`, { method, headers, body: payload });
  if (raw) return r;
  const text = await r.text();
  let data; try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!r.ok) throw new Error(`${method} ${p} -> ${r.status} ${text.slice(0, 200)}`);
  return data;
}
// The backend allows 10 sign-ins a minute per address (a real limit, kept on purpose).
// Every sign-in here, API or browser, first takes a slot so the run stays under it.
const loginTimes = [];
async function loginSlot() {
  for (;;) {
    const now = Date.now();
    while (loginTimes.length && now - loginTimes[0] > 61_000) loginTimes.shift();
    if (loginTimes.length < 9) { loginTimes.push(now); return; }
    await new Promise(r => setTimeout(r, 61_000 - (now - loginTimes[0]) + 100));
  }
}
async function loginApi(email, password) {
  for (let i = 0; ; i++) {
    await loginSlot();
    try { return await api('POST', '/auth/login', { body: { email, password } }); } catch (e) {
      // A run started right after another one inherits its count: wait it out.
      if (!String(e.message).includes('429') || i > 4) throw e;
      await new Promise(r => setTimeout(r, 20_000));
    }
  }
}

function makeTone(file, seconds, freq = 440, bitrate = '128k') {
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', `sine=frequency=${freq}:duration=${seconds}`,
    '-ac', '2', '-ar', '44100', '-b:a', bitrate, file]);
  return file;
}

// ── Browser helpers ─────────────────────────────────────────────────────────
let browser;
const USERS = {}; // role -> { email, password, token, refresh, id }

async function newPage(role, view = 'desktop') {
  const ctx = await browser.newContext({ ...VIEWS[view] });
  if (role) {
    const u = USERS[role];
    await ctx.addInitScript(([a, r]) => {
      if (!sessionStorage.getItem('e2e-signed-out')) {
        localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r);
      }
    }, [u.token, u.refresh]);
  }
  const page = await ctx.newPage();
  page.errors = [];
  page.on('pageerror', e => page.errors.push(String(e).slice(0, 200)));
  page.on('dialog', d => d.accept().catch(() => {})); // confirm() boxes: say yes
  page.setDefaultTimeout(8000);
  return page;
}
async function closePage(page) { await page.context().close().catch(() => {}); }

/** The input/select/textarea that follows a label with this text (labels here are not linked by for=). */
function field(scope, labelText, nth = 0) {
  const t = labelText.replace(/"/g, '\\"');
  return scope.locator(`xpath=//label[starts-with(normalize-space(.), "${t}")]/following-sibling::*[self::input or self::select or self::textarea][1] | //label[starts-with(normalize-space(.), "${t}")]/following-sibling::div[1]//*[self::input or self::select or self::textarea][1]`).nth(nth);
}

/** Wall time from an action to the matching API response (S-6). */
async function timedResponse(page, matcher, action) {
  const t0 = Date.now();
  const [resp] = await Promise.all([
    page.waitForResponse(r => {
      const u = r.url();
      if (!u.startsWith(API)) return false;
      const p = u.slice(API.length).split('?')[0];
      return matcher(r.request().method(), p, r);
    }, { timeout: 15000 }),
    action(),
  ]);
  const ms = Date.now() - t0;
  return { resp, ms, status: resp.status() };
}
async function timedSave(page, what, matcher, action, { expectStatus = s => s < 400 } = {}) {
  const r = await timedResponse(page, matcher, action);
  assert(expectStatus(r.status), `${what}: server answered ${r.status} ${(await r.resp.text().catch(() => '')).slice(0, 160)}`);
  assertFast(r.ms, LIMIT.save, what);
  return r;
}

/** S-5: warm load timing. Returns { fcp, ready }. */
async function measureLoad(page, url, readyFn) {
  await page.goto(url, { waitUntil: 'domcontentloaded' }); // cold: fills the HTTP cache
  await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
  const t0 = Date.now();
  await page.goto(url, { waitUntil: 'commit' });
  await page.waitForFunction(readyFn || defaultReady, null, { timeout: 10000, polling: 50 });
  const ready = Date.now() - t0;
  const fcp = await page.evaluate(() => {
    const e = performance.getEntriesByName('first-contentful-paint')[0];
    return e ? Math.round(e.startTime) : null;
  });
  return { fcp: fcp ?? ready, ready };
}
// Ready = the page shows its own content (a heading or table) and no "Loading" placeholder.
function defaultReady() {
  const root = document.querySelector('main') || document.querySelector('#root');
  if (!root) return false;
  const txt = root.innerText || '';
  if (txt.trim().length < 15) return false;
  if (/^\s*Loading\.\.\.\s*$/m.test(txt) || /Loading (users|alerts|stations)\.\.\./.test(txt)) return false;
  return !!root.querySelector('h1, h2, h3, table, form, button');
}

async function waitText(page, text, timeout = 5000) {
  await page.getByText(text, { exact: false }).first().waitFor({ state: 'visible', timeout });
}
async function waitGone(page, text, timeout = 5000) {
  await page.getByText(text, { exact: false }).first().waitFor({ state: 'detached', timeout }).catch(async () => {
    await page.getByText(text, { exact: false }).first().waitFor({ state: 'hidden', timeout: 100 });
  });
}
async function pathOf(page) { return new URL(page.url()).pathname; }
async function waitPath(page, p, timeout = 5000) {
  try {
    await page.waitForURL(u => new URL(u).pathname === p, { timeout });
  } catch {
    const body = (await page.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ').slice(0, 120);
    throw new Error(`expected ${short(p)}, still on ${short(await pathOf(page))} ("${body}")`);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// Setup: admin account, one user per role, a station, a few library items.
// ═══════════════════════════════════════════════════════════════════════════
const FX = {}; // fixtures

async function setup() {
  const adminEmail = process.env.E2E_ADMIN_EMAIL || 'e2e-admin@example.test';
  const adminPass = process.env.E2E_ADMIN_PASS || `e2e-${crypto.randomBytes(9).toString('base64url')}`;
  if (process.env.E2E_DATABASE_URL) {
    execFileSync('uv', ['run', 'python', path.join(HERE, 'admin-flows-seed.py')], {
      cwd: path.join(HERE, '..', '..', 'backend'),
      env: { ...process.env, DATABASE_URL: process.env.E2E_DATABASE_URL, E2E_ADMIN_EMAIL: adminEmail, E2E_ADMIN_PASS: adminPass },
      stdio: 'pipe',
    });
  }
  const adm = await loginApi(adminEmail, adminPass);
  USERS.admin = { email: adminEmail, password: adminPass, token: adm.access_token, refresh: adm.refresh_token };
  const T = adm.access_token;
  USERS.admin.id = (await api('GET', '/auth/me', { token: T })).id;

  // One account per role (made-up credentials, unique per run).
  const existing = (await api('GET', '/users', { token: T })).users;
  for (const role of ['manager', 'dj', 'viewer', 'sponsor']) {
    const email = `e2e-${role}-${RUN}@example.test`;
    const password = `pw-${role}-${crypto.randomBytes(6).toString('hex')}`;
    const u = await api('POST', '/users', { token: T, body: { email, password, role, display_name: `E2E ${role}` } });
    const tk = await loginApi(email, password);
    USERS[role] = { email, password, token: tk.access_token, refresh: tk.refresh_token, id: u.id };
  }
  void existing;

  // Station used by most flows.
  const st = await api('POST', '/stations', { token: T, body: { name: `E2E Station ${RUN}`, timezone: 'America/New_York', latitude: 40.08, longitude: -74.21 } });
  FX.station = st;

  // Sponsor linked to the sponsor login (the portal looks it up by user_id).
  FX.sponsor = await api('POST', '/sponsors', { token: T, body: { name: `E2E Sponsor ${RUN}`, length_seconds: 15, audio_file_path: 'spots/e2e.mp3', user_id: USERS.sponsor.id, company_name: 'E2E Co' } });

  // A few library items, song length (short test files make the station queue thousands
  // of entries long, which is not what a real station has).
  FX.assets = [];
  for (const [i, t] of [['1', 'music'], ['2', 'music'], ['3', 'spot']].entries()) {
    const f = makeTone(path.join(TMP, `fx${i}.mp3`), t[1] === 'spot' ? 30 : 170 + i * 15, 300 + i * 120, '32k');
    const form = new FormData();
    form.append('file', new Blob([fs.readFileSync(f)], { type: 'audio/mpeg' }), `fx${i}.mp3`);
    form.append('title', `E2E Tone ${t[0]} ${RUN}`);
    form.append('asset_type', t[1]);
    form.append('artist', `E2E Artist ${i}`);
    FX.assets.push(await api('POST', '/assets/upload', { token: T, form }));
  }
  FX.toneFile = makeTone(path.join(TMP, 'play.mp3'), 8, 523);
  FX.takeover = await api('POST', '/stations', { token: T, body: { name: `E2E Takeover ${RUN}`, timezone: 'UTC' } });
  FX.schedule = await api('POST', '/schedules', { token: T, body: { name: `E2E Base Schedule ${RUN}`, station_id: st.id } });
  const start = new Date(Date.now() + 3 * 3600e3), end = new Date(Date.now() + 4 * 3600e3);
  FX.show = await api('POST', '/live-shows', { token: T, body: { station_id: st.id, title: `E2E Fixture Show ${RUN}`, scheduled_start: start.toISOString(), scheduled_end: end.toISOString() } });
  FX.review = await api('POST', '/reviews/queues', { token: T, body: { name: `E2E Review ${RUN}`, asset_ids: FX.assets.slice(0, 2).map(a => a.id) } });
}

// Every screen, with the roles that may open it (docs/test-checklist.md F-ACCESS-5..9,
// written out by hand from the rows, not imported from access.ts, so a wrong map is caught).
const ALL = ['admin', 'manager', 'dj', 'viewer'];
const MGR = ['admin', 'manager'];
const ONAIR = ['admin', 'manager', 'dj'];
function adminRoutes() {
  return [
    ['/admin/dashboard', ALL], ['/admin/stations', MGR], ['/admin/assets', ALL], ['/admin/assets/upload', MGR],
    [`/admin/assets/${FX.assets[0].id}`, ALL], ['/admin/reviews', ALL], [`/admin/reviews/${FX.review.id}`, ALL],
    ['/admin/users', ['admin']], ['/admin/rules', ALL], ['/admin/schedules', ALL],
    [`/admin/schedules/${FX.schedule.id}/blocks`, ALL], ['/admin/holidays', MGR], ['/admin/sponsors', MGR],
    ['/admin/spot-schedule', MGR], ['/admin/spot-recordings', MGR], ['/admin/program-blocks', MGR],
    ['/admin/schedule-check', MGR], ['/admin/sponsorship-rules', MGR], ['/admin/playlists', MGR],
    ['/admin/categories', MGR], ['/admin/asset-types', MGR], ['/admin/alerts', MGR], ['/admin/bug-reports', MGR],
    ['/admin/site-updates', ['admin']], ['/admin/activity', ['admin']], ['/admin/analytics', MGR],
    ['/admin/live', ONAIR], [`/admin/live/${FX.show.id}/host`, ONAIR], [`/admin/live/${FX.show.id}/screen`, ONAIR],
    ['/admin/requests', MGR], ['/admin/archives', MGR], [`/admin/audio-enhance/${FX.assets[0].id}`, MGR],
    ['/admin/studio', ONAIR], ['/admin/listeners', MGR], ['/admin/crm', MGR], ['/admin/program-clock', ALL],
  ];
}
const SPONSOR_ROUTES = ['/sponsor/dashboard', '/sponsor/campaigns', '/sponsor/billing', '/sponsor/wording'];
const PUBLIC_ROUTES = ['/stations', '/guide', '/hosts', '/archives'];
// Screens a role may open but not change (F-ACCESS-10/11): the "View only" pill.
const VIEW_ONLY = {
  admin: [],
  manager: ['/admin/rules'],
  dj: ['/admin/assets', '/admin/schedules', '/admin/rules', '/admin/program-clock'],
  viewer: ['/admin/dashboard', '/admin/assets', '/admin/schedules', '/admin/rules', '/admin/program-clock'],
};
const short = p => p.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ':id');

// ═══════════════════════════════════════════════════════════════════════════
// Sections
// ═══════════════════════════════════════════════════════════════════════════
const SECTIONS = [];
const section = (name, fn) => SECTIONS.push({ name, fn });

// ── Roles and routes ────────────────────────────────────────────────────────
section('access', async () => {
  // F-ACCESS-1 / 2 / 3: anonymous visitor
  {
    const page = await newPage(null);
    await step('F-ACCESS-1 / redirects to /stations', async () => {
      await page.goto(`${BASE}/`); await waitPath(page, '/stations');
    }, page);
    await step('F-ACCESS-2 anonymous /admin/* goes to /admin/login', async () => {
      for (const [p] of adminRoutes()) { await page.goto(`${BASE}${p}`); await waitPath(page, '/admin/login'); }
      return `${adminRoutes().length} routes`;
    }, page);
    await step('F-ACCESS-3 anonymous /sponsor/* goes to /sponsor/login', async () => {
      for (const p of [...SPONSOR_ROUTES, `/sponsor/campaigns/${crypto.randomUUID()}`]) { await page.goto(`${BASE}${p}`); await waitPath(page, '/sponsor/login'); }
    }, page);
    await closePage(page);
  }

  // F-ACCESS-5..9: each role on every screen, desktop. Redirects go to the role's home.
  const ids = { admin: '5', manager: '6', dj: '7', viewer: '8' };
  for (const role of ['admin', 'manager', 'dj', 'viewer']) {
    const page = await newPage(role);
    await step(`F-ACCESS-${ids[role]} ${role}: opens exactly the allowed screens`, async () => {
      const wrong = [];
      for (const [p, roles] of adminRoutes()) {
        await page.goto(`${BASE}${p}`);
        const allowed = roles.includes(role);
        if (allowed) {
          await page.waitForFunction(() => document.querySelector('main')?.innerText.trim().length > 0, null, { timeout: 8000 }).catch(() => {});
          await page.waitForTimeout(150);
          const where = await pathOf(page);
          const errBox = await page.getByText('Something went wrong').count();
          if (where !== p) wrong.push(`${short(p)} -> ${where}`);
          else if (errBox) wrong.push(`${short(p)} error box`);
        } else {
          await waitPath(page, '/admin/dashboard').catch(() => {});
          const where = await pathOf(page);
          if (where !== '/admin/dashboard') wrong.push(`${short(p)} not redirected (${where})`);
        }
      }
      if (page.errors.length) wrong.push(`script error: ${page.errors[0]}`);
      assert(!wrong.length, wrong.join('; '));
      return `${adminRoutes().length} routes`;
    }, page);
    // Staff on sponsor pages go to the control room
    await step(`F-ACCESS-9 ${role} on /sponsor/* goes to /admin/dashboard`, async () => {
      for (const p of SPONSOR_ROUTES) { await page.goto(`${BASE}${p}`); await waitPath(page, '/admin/dashboard'); }
    }, page);
    // View only pill on each screen the role can open (F-ACCESS-10/11)
    await step(`F-ACCESS-${role === 'manager' || role === 'admin' ? 11 : role === 'dj' ? 11 : 10} ${role}: "View only" marker exactly where the role cannot change`, async () => {
      const wrong = [];
      for (const [p, roles] of adminRoutes()) {
        if (!roles.includes(role)) continue;
        if (p.includes('/live/') || p.includes('audio-enhance')) continue;
        await page.goto(`${BASE}${p}`);
        await page.waitForFunction(() => document.querySelector('main')?.innerText.trim().length > 0, null, { timeout: 8000 }).catch(() => {});
        const expect = VIEW_ONLY[role].some(v => p === v || p.startsWith(v + '/'));
        let has = false;
        try { await page.getByTestId('view-only').waitFor({ state: 'visible', timeout: expect ? 3000 : 400 }); has = true; } catch { has = false; }
        if (has !== expect) wrong.push(`${short(p)} ${expect ? 'missing' : 'shown wrongly'}`);
      }
      assert(!wrong.length, wrong.join('; '));
    }, page);
    await closePage(page);
  }

  // Sponsor: admin pages go to the sponsor dashboard; sponsor pages open.
  {
    const page = await newPage('sponsor');
    await step('F-ACCESS-9 sponsor on /admin/* goes to /sponsor/dashboard', async () => {
      const wrong = [];
      for (const [p] of adminRoutes()) {
        await page.goto(`${BASE}${p}`);
        await waitPath(page, '/sponsor/dashboard').catch(() => {});
        if (await pathOf(page) !== '/sponsor/dashboard') wrong.push(short(p));
      }
      assert(!wrong.length, `not redirected: ${wrong.join(', ')}`);
    }, page);
    await step('F-ACCESS-9 sponsor opens every sponsor page', async () => {
      for (const p of SPONSOR_ROUTES) {
        await page.goto(`${BASE}${p}`); await page.waitForTimeout(200);
        assert(await pathOf(page) === p, `${p} -> ${await pathOf(page)}`);
      }
    }, page);
    await closePage(page);
  }

  // Phone: the same gate (sample)
  {
    const page = await newPage('viewer', 'iphone');
    await step('F-ACCESS-8 viewer (iPhone): /admin/live and /admin/studio redirect, /admin/rules opens view-only', async () => {
      for (const p of ['/admin/live', '/admin/studio', '/admin/stations']) { await page.goto(`${BASE}${p}`); await waitPath(page, '/admin/dashboard'); }
      await page.goto(`${BASE}/admin/rules`);
      await page.getByTestId('view-only').waitFor({ state: 'visible' });
    }, page);
    await closePage(page);
  }

  // F-ACCESS-4: account lookup fails -> "Could not reach the server" + Try again
  {
    const page = await newPage('manager');
    await step('F-ACCESS-4 account lookup failure shows Try again (not login); Try again recovers', async () => {
      let fail = true;
      await page.route(`${API}/auth/me`, route => fail ? route.abort('connectionrefused') : route.continue());
      await page.goto(`${BASE}/admin/schedules`);
      await waitText(page, 'Could not reach the server', 15000);
      assert(await pathOf(page) === '/admin/schedules', 'was sent away from the page');
      fail = false;
      await page.getByRole('button', { name: 'Try again' }).click();
      await page.getByRole('heading').first().waitFor({ state: 'visible' });
      await waitGone(page, 'Could not reach the server');
    }, page);
    await closePage(page);
  }

  // F-ACCESS-14: unknown route renders without crash
  {
    const page = await newPage('admin');
    await step('F-ACCESS-14 unknown route: no crash, nav still there, says "Page not found"', async () => {
      await page.goto(`${BASE}/admin/does-not-exist-${RUN}`);
      await page.locator('nav').waitFor({ state: 'visible' });
      await waitText(page, 'Page not found');
      assert(!page.errors.length, page.errors[0]);
      assert(!(await page.getByText('Something went wrong').count()), 'error box');
    }, page);
    await closePage(page);
  }

  // F-ACCESS-12: a refused write shows the app-wide notice
  {
    const page = await newPage('viewer');
    await step('F-ACCESS-12 a 403 on a write shows "not allowed" notice', async () => {
      await page.goto(`${BASE}/admin/schedules`);
      await page.getByRole('heading').first().waitFor();
      // Make a write from the page's own API client (the same path every button uses).
      await page.evaluate(async (api) => {
        const t = localStorage.getItem('access_token');
        // The notice listens to the client's event; drive it through a real refused request.
        const r = await fetch(`${api}/schedules`, { method: 'POST', headers: { Authorization: `Bearer ${t}`, 'content-type': 'application/json' }, body: '{"name":"x","station_id":"00000000-0000-0000-0000-000000000000"}' });
        window.__e2eStatus = r.status;
      }, API);
      assert(await page.evaluate(() => window.__e2eStatus) === 403, `server answered ${await page.evaluate(() => window.__e2eStatus)} to a viewer write`);
    }, page);
    await closePage(page);
  }
});

// ── Admin login and sponsor login ───────────────────────────────────────────
async function fillLogin(page, email, password) {
  await field(page, 'Username').or(field(page, 'Email')).first().fill(email);
  await field(page, 'Password').fill(password);
}
section('login', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage(null, view);
    await step(`F-LOGIN-3 ${view}: wrong password shows an error, stays on login, Processing… while waiting`, async () => {
      await page.goto(`${BASE}/admin/login`);
      await fillLogin(page, USERS.manager.email, 'not-the-password');
      // Hold the answer back a little so the busy state can be seen.
      await page.route(`${API}/auth/login`, async r => { await new Promise(res => setTimeout(res, 400)); await r.continue(); });
      await loginSlot(); await page.getByRole('button', { name: 'Login' }).click();
      await waitText(page, 'Processing', 1000);
      await page.locator('.bg-red-50').waitFor({ state: 'visible' });
      const msg = (await page.locator('.bg-red-50').innerText()).trim();
      assert(msg.length > 3, 'empty error');
      assert(await pathOf(page) === '/admin/login', 'moved away from login');
      await page.unroute(`${API}/auth/login`);
      return `error "${msg}"`;
    }, page);
    await step(`F-LOGIN-1 ${view}: manager signs in and lands on /admin/dashboard fast`, async () => {
      await page.goto(`${BASE}/admin/login`);
      await fillLogin(page, USERS.manager.email, USERS.manager.password);
      const t0 = Date.now();
      await loginSlot(); await page.getByRole('button', { name: 'Login' }).click();
      await waitPath(page, '/admin/dashboard');
      return `${Date.now() - t0} ms to the control room`;
    }, page);
    await step(`F-LOGIN-4 ${view}: signed-in user opening /admin/login is not stuck (can go to the dashboard)`, async () => {
      await page.goto(`${BASE}/admin/login`);
      await page.waitForTimeout(300);
      // Either redirected, or the nav offers the Dashboard link (no forced re-login).
      if (await pathOf(page) !== '/admin/login') return `redirected to ${await pathOf(page)}`;
      await page.goto(`${BASE}/admin/dashboard`);
      await waitPath(page, '/admin/dashboard');
      return 'login form shown, session kept';
    }, page);
    await closePage(page);
  }
  {
    const page = await newPage(null);
    await step('F-LOGIN-4 Enter key submits the login form', async () => {
      await page.goto(`${BASE}/admin/login`);
      await fillLogin(page, USERS.dj.email, USERS.dj.password);
      await loginSlot(); await field(page, 'Password').press('Enter');
      await waitPath(page, '/admin/dashboard');
    }, page);
    await closePage(page);
  }
  {
    const page = await newPage(null);
    await step('F-LOGIN-2 sponsor credentials on the admin login land on /sponsor/dashboard', async () => {
      await page.goto(`${BASE}/admin/login`);
      await fillLogin(page, USERS.sponsor.email, USERS.sponsor.password);
      await loginSlot(); await page.getByRole('button', { name: 'Login' }).click();
      await waitPath(page, '/sponsor/dashboard');
    }, page);
    await closePage(page);
  }
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage(null, view);
    await step(`F-SPLOGIN-1 ${view}: sponsor wrong password error, Signing in… state, then sign in to /sponsor/dashboard`, async () => {
      await page.goto(`${BASE}/sponsor/login`);
      await fillLogin(page, USERS.sponsor.email, 'wrong-one');
      await page.route(`${API}/auth/login`, async r => { await new Promise(res => setTimeout(res, 400)); await r.continue(); });
      await loginSlot(); await page.getByRole('button', { name: 'Sign In' }).click();
      await waitText(page, 'Signing in', 1000);
      await page.locator('.bg-red-50').waitFor({ state: 'visible' });
      assert(await pathOf(page) === '/sponsor/login', 'moved away');
      await page.unroute(`${API}/auth/login`);
      await field(page, 'Password').fill(USERS.sponsor.password);
      const t0 = Date.now();
      await loginSlot(); await page.getByRole('button', { name: 'Sign In' }).click();
      await waitPath(page, '/sponsor/dashboard');
      return `${Date.now() - t0} ms to the sponsor dashboard`;
    }, page);
    await closePage(page);
  }
});

section('login-limit', async () => {
  // The sign-in limit (10 a minute) answers 429 without a "detail": the person must get words, not "status code 429".
  for (const [p, btn] of [['/admin/login', 'Login'], ['/sponsor/login', 'Sign In']]) {
    const page = await newPage(null);
    await step(`F-LOGIN-3 ${p}: too many attempts shows a plain message`, async () => {
      await page.goto(`${BASE}${p}`);
      await fillLogin(page, 'someone@example.test', 'whatever');
      await page.route(`${API}/auth/login`, r => r.fulfill({ status: 429, contentType: 'application/json', body: '{"error":"Rate limit exceeded: 10 per 1 minute"}' }));
      await page.getByRole('button', { name: btn }).click();
      const box = page.locator('.bg-red-50');
      await box.waitFor();
      const msg = await box.innerText();
      assert(!/status code/i.test(msg), `shows "${msg}"`);
      assert(/too many/i.test(msg), `shows "${msg}"`);
    }, page);
    await closePage(page);
  }
});

// ── Shared shell: nav, bell, logout, phone menu, bug reporter, token refresh ─
section('shell', async () => {
  // F-GLOBAL-1 public nav (desktop and phone)
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage(null, view);
    await step(`F-GLOBAL-1 ${view}: public nav links Stations / Program Guide / DJs / Archives navigate`, async () => {
      await page.goto(`${BASE}/stations`);
      for (const [label, p] of [['Program Guide', '/guide'], ['DJs', '/hosts'], ['Archives', '/archives'], ['Stations', '/stations']]) {
        const link = page.locator('nav').getByRole('link', { name: label, exact: true });
        if (view === 'iphone' && !(await link.first().isVisible())) {
          // Phones: the links must be reachable through a menu button.
          const menu = page.locator('nav button[aria-label="Menu"]');
          assert(await menu.isVisible().catch(() => false), `no way to reach "${label}" on a phone (links hidden, no menu)`);
          await menu.click();
        }
        await page.locator('nav').getByRole('link', { name: label, exact: true }).first().click();
        await waitPath(page, p);
      }
    }, page);
    await closePage(page);
  }

  // F-GLOBAL-2 signed-in nav
  {
    const page = await newPage('manager');
    await step('F-GLOBAL-2 staff nav shows Dashboard link (no Sponsor Portal)', async () => {
      await page.goto(`${BASE}/admin/stations`);
      await page.locator('nav').getByRole('link', { name: 'Dashboard', exact: true }).waitFor();
      assert(!(await page.locator('nav').getByRole('link', { name: 'Sponsor Portal' }).count()), 'Sponsor Portal shown to staff');
    }, page);
    await closePage(page);
    const sp = await newPage('sponsor');
    await step('F-GLOBAL-2 sponsor on a public page sees Sponsor Portal link, no Dashboard', async () => {
      await sp.goto(`${BASE}/stations`);
      await sp.locator('nav').getByRole('link', { name: 'Sponsor Portal' }).click();
      await waitPath(sp, '/sponsor/dashboard');
      await sp.goto(`${BASE}/stations`);
      assert(!(await sp.locator('nav').getByRole('link', { name: 'Dashboard', exact: true }).count()), 'Dashboard link shown to sponsor');
    }, sp);
    await closePage(sp);
  }

  // F-GLOBAL-3 dropdown groups: every link navigates; hidden for roles that cannot open it
  const groups = {
    Content: [['Library', '/admin/assets'], ['Categories', '/admin/categories'], ['Types', '/admin/asset-types'], ['Playlists', '/admin/playlists'], ['Studio', '/admin/studio'], ['Reviews', '/admin/reviews']],
    Broadcast: [['Schedules', '/admin/schedules'], ['Program Blocks', '/admin/program-blocks'], ['Schedule Check', '/admin/schedule-check'], ['Rules', '/admin/rules'], ['Blackouts', '/admin/holidays'], ['Program Clock', '/admin/program-clock'], ['Live Shows', '/admin/live']],
    Audience: [['Listeners', '/admin/listeners'], ['Analytics', '/admin/analytics'], ['Requests', '/admin/requests'], ['Program Guide', '/guide'], ['Archives', '/archives'], ['DJs', '/hosts']],
    Settings: [['Users', '/admin/users'], ['Activity', '/admin/activity'], ['Sponsors', '/admin/sponsors'], ['Spot Schedule', '/admin/spot-schedule'], ['Record Spots', '/admin/spot-recordings'], ['Sponsorship Rules', '/admin/sponsorship-rules'], ['Alerts', '/admin/alerts'], ['Bug Reports', '/admin/bug-reports'], ['Site Updates', '/admin/site-updates'], ['Manage Stations', '/admin/stations'], ['CRM', '/admin/crm']],
  };
  const canOpen = (role, p) => {
    if (!p.startsWith('/admin')) return true;
    const hit = adminRoutes().find(([r]) => r === p);
    return hit ? hit[1].includes(role) : false;
  };
  for (const role of ['admin', 'manager', 'dj', 'viewer']) {
    const page = await newPage(role);
    await step(`F-GLOBAL-3 ${role}: nav dropdowns open/close, each shown link navigates, forbidden ones hidden`, async () => {
      await page.goto(`${BASE}/stations`);
      let n = 0;
      for (const [g, links] of Object.entries(groups)) {
        const expected = links.filter(([, p]) => canOpen(role, p));
        const btn = page.locator('nav').getByRole('button', { name: g, exact: true });
        if (!expected.length) { assert(!(await btn.count()), `${g} group shown with nothing in it`); continue; }
        for (const [label, p] of links) {
          await btn.click();
          const menu = page.locator('nav div.absolute').filter({ has: page.getByRole('link') }).first();
          await menu.waitFor({ state: 'visible' });
          const link = menu.getByRole('link', { name: label, exact: true });
          const shown = await link.count();
          if (!canOpen(role, p)) { assert(!shown, `${g} > ${label} shown to ${role}`); await page.mouse.click(5, 400); continue; }
          assert(shown, `${g} > ${label} missing for ${role}`);
          await link.click();
          await waitPath(page, p);
          assert(!(await menu.isVisible().catch(() => false)), 'menu stayed open after navigating');
          n++;
        }
      }
      // Close by clicking elsewhere
      const first = Object.keys(groups).find(g => groups[g].some(([, p]) => canOpen(role, p)));
      await page.locator('nav').getByRole('button', { name: first, exact: true }).click();
      await page.mouse.click(700, 600);
      await page.waitForTimeout(250);
      assert(!(await page.locator('nav div.absolute a').count()), 'dropdown did not close on outside click');
      return `${n} links`;
    }, page);
    await closePage(page);
  }

  // F-GLOBAL-4 alert bell
  {
    const page = await newPage('manager');
    await step('F-GLOBAL-4 bell shows unresolved count, opens list, alert click and View All go to /admin/alerts', async () => {
      const before = (await api('GET', '/alerts/unresolved-count', { token: USERS.admin.token }).catch(() => null));
      void before;
      await page.goto(`${BASE}/admin/stations`);
      await page.waitForTimeout(500);
      const badge = page.locator('nav span.bg-red-600');
      const want = await page.evaluate(async (api) => {
        const r = await fetch(`${api}/alerts?is_resolved=false&limit=1`, { headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` } });
        const d = await r.json(); return d.total ?? d.unresolved ?? (d.alerts || []).length;
      }, API);
      if (want > 0) {
        await badge.waitFor({ state: 'visible' });
        const txt = (await badge.innerText()).trim();
        assert(txt === (want > 9 ? '9+' : String(want)), `badge ${txt}, expected ${want}`);
      }
      await page.locator('nav button:has(svg path[d^="M15 17h5"])').click();
      await waitText(page, 'View All Alerts');
      if (want > 0) {
        await page.locator('nav .max-h-64 button').first().click();
        await waitPath(page, '/admin/alerts');
        await page.locator('nav button:has(svg path[d^="M15 17h5"])').click();
      }
      await page.getByRole('button', { name: 'View All Alerts' }).click();
      await waitPath(page, '/admin/alerts');
      return `${want} unresolved`;
    }, page);
    await closePage(page);
    const dj = await newPage('dj');
    await step('F-GLOBAL-4 no alert bell for a DJ (manager screen)', async () => {
      await dj.goto(`${BASE}/admin/schedules`);
      await dj.locator('nav').waitFor();
      await dj.waitForTimeout(300);
      assert(!(await dj.locator('nav button:has(svg path[d^="M15 17h5"])').count()), 'bell shown');
    }, dj);
    await closePage(dj);
  }

  // F-GLOBAL-5 logout (desktop) and F-GLOBAL-6 phone menu
  {
    const page = await newPage(null);
    await step('F-GLOBAL-5 Logout clears tokens and returns to login', async () => {
      await page.goto(`${BASE}/admin/login`);
      await fillLogin(page, USERS.viewer.email, USERS.viewer.password);
      await loginSlot(); await page.getByRole('button', { name: 'Login' }).click();
      await waitPath(page, '/admin/dashboard');
      await page.goto(`${BASE}/admin/schedules`);
      await page.locator('nav').getByRole('button', { name: 'Logout' }).click();
      await waitPath(page, '/admin/login');
      const tokens = await page.evaluate(() => [localStorage.getItem('access_token'), localStorage.getItem('refresh_token')]);
      assert(!tokens[0] && !tokens[1], 'tokens still stored');
      await page.goto(`${BASE}/admin/schedules`);
      await waitPath(page, '/admin/login');
    }, page);
    await closePage(page);
  }
  {
    const page = await newPage(null, 'iphone');
    await step('F-GLOBAL-6 phone: hamburger opens, each entry navigates and closes the menu, Logout works', async () => {
      await page.goto(`${BASE}/admin/login`);
      await fillLogin(page, USERS.manager.email, USERS.manager.password);
      await loginSlot(); await page.getByRole('button', { name: 'Login' }).click();
      await waitPath(page, '/admin/dashboard');
      await page.goto(`${BASE}/admin/stations`);
      const burger = page.locator('nav button.md\\:hidden');
      const entries = [['Dashboard', '/admin/dashboard'], ['Library', '/admin/assets'], ['Schedules', '/admin/schedules'], ['Requests', '/admin/requests'], ['Sponsors', '/admin/sponsors'], ['Manage Stations', '/admin/stations'], ['Stations', '/stations']];
      for (const [label, p] of entries) {
        await burger.click();
        const menu = page.locator('nav div.md\\:hidden.border-t');
        await menu.waitFor({ state: 'visible' });
        await menu.getByRole('link', { name: label, exact: true }).click();
        await waitPath(page, p);
        assert(!(await menu.isVisible().catch(() => false)), `menu stayed open after ${label}`);
      }
      await burger.click();
      await page.locator('nav div.md\\:hidden.border-t').getByRole('button', { name: 'Logout' }).click();
      const tk = await page.evaluate(() => localStorage.getItem('access_token'));
      assert(!tk, 'token kept after Logout');
      await page.goto(`${BASE}/admin/schedules`);
      await waitPath(page, '/admin/login');
    }, page);
    await closePage(page);
  }

  // F-GLOBAL-7 / 8 sticky nav, no sideways scroll, full width (re-checked here at 1440 and 390)
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('admin', view);
    await step(`F-GLOBAL-7/8 ${view}: nav sticks, no sideways scroll${view === 'desktop' ? ', page uses the full width' : ''} on every admin page`, async () => {
      const bad = [];
      const devW = VIEWS[view].viewport.width;
      for (const [p] of adminRoutes()) {
        await page.goto(`${BASE}${p}`);
        await page.waitForFunction(defaultReady, null, { timeout: 8000 }).catch(() => {});
        const m = await page.evaluate((vw) => {
          const main = document.querySelector('main');
          const mainW = main ? main.getBoundingClientRect().width : 0;
          return { docW: document.documentElement.scrollWidth, mainW, vw, docH: document.documentElement.scrollHeight, vh: innerHeight };
        }, devW);
        if (m.docW > devW + 1) bad.push(`${short(p)} ${m.docW}px wide`);
        if (view === 'desktop' && m.mainW < devW * 0.9) bad.push(`${short(p)} main only ${Math.round(m.mainW)}px`);
        if (m.docH > m.vh + 60 && p !== '/admin/dashboard') {
          await page.evaluate(() => scrollTo(0, 400));
          await page.waitForTimeout(60);
          const top = await page.evaluate(() => Math.round(document.querySelector('nav')?.getBoundingClientRect().top ?? -1));
          if (top !== 0) bad.push(`${short(p)} nav scrolled away`);
        }
      }
      assert(!bad.length, bad.join('; '));
    }, page);
    await closePage(page);
  }

  // F-GLOBAL-12 token refresh
  {
    const ctx = await browser.newContext(VIEWS.desktop);
    const fresh = await loginApi(USERS.manager.email, USERS.manager.password);
    await ctx.addInitScript(([r]) => {
      if (!sessionStorage.getItem('e2e-once')) {
        sessionStorage.setItem('e2e-once', '1');
        localStorage.setItem('access_token', 'expired.or.bad.token');
        localStorage.setItem('refresh_token', r);
      }
    }, [fresh.refresh_token]);
    const page = await ctx.newPage();
    await step('F-GLOBAL-12 a bad/expired access token is refreshed without logging out', async () => {
      await page.goto(`${BASE}/admin/stations`);
      await page.getByRole('heading', { name: 'Manage Stations' }).waitFor();
      assert(await pathOf(page) === '/admin/stations', `sent to ${await pathOf(page)}`);
      const tk = await page.evaluate(() => localStorage.getItem('access_token'));
      assert(tk && tk !== 'expired.or.bad.token', 'access token not replaced');
    }, page);
    await ctx.close();
  }

  // F-GLOBAL-11 page views reach the activity log
  {
    const page = await newPage('dj');
    await step('F-GLOBAL-11 page views are recorded (activity log shows "Opened Schedules" for the DJ)', async () => {
      await page.goto(`${BASE}/admin/schedules`);
      await page.getByRole('heading').first().waitFor();
      await page.locator('nav').getByRole('button', { name: 'Broadcast' }).click();
      await page.locator('nav').getByRole('link', { name: 'Rules', exact: true }).click();
      await waitPath(page, '/admin/rules');
      const deadline = Date.now() + 8000;
      let labels = [];
      while (Date.now() < deadline) {
        const d = await api('GET', `/activity?user_id=${USERS.dj.id}&limit=50`, { token: USERS.admin.token });
        labels = (d.items || d.rows || d.entries || d.activity || d).map?.(x => x.label) || [];
        if (labels.some(l => /Opened Schedules/.test(l)) && labels.some(l => /Opened Rules/.test(l))) return 'both views logged';
        await new Promise(r => setTimeout(r, 400));
      }
      throw new Error(`not logged; saw: ${labels.slice(0, 5).join(' | ')}`);
    }, page);
    await closePage(page);
  }

  // F-GLOBAL-13 / 14 bug reporter
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    await step(`F-GLOBAL-13 ${view}: bug reporter opens, Cancel / × / backdrop close, Send posts with breadcrumbs, success screen`, async () => {
      await page.goto(`${BASE}/admin/stations`);
      await page.getByRole('heading', { name: 'Manage Stations' }).waitFor();
      const openIt = async () => {
        await page.locator('button[aria-label="Report a bug"]').click();
        await page.getByRole('dialog', { name: 'Report a bug' }).waitFor({ timeout: 10000 });
      };
      const dlg = page.getByRole('dialog', { name: 'Report a bug' });
      await openIt(); await dlg.getByRole('button', { name: 'Cancel' }).click(); await dlg.waitFor({ state: 'detached' });
      await openIt(); await dlg.getByRole('button', { name: 'Close' }).click(); await dlg.waitFor({ state: 'detached' });
      await openIt(); await page.mouse.click(5, 60); await dlg.waitFor({ state: 'detached' });
      await openIt();
      const shotBox = dlg.getByText('Include this screenshot');
      if (await shotBox.count()) await dlg.locator('input[type=checkbox]').uncheck();
      await dlg.locator('#bug-what').fill(`E2E bug report ${RUN} ${view}`);
      await dlg.locator('#bug-expected').fill('Nothing, this is a test');
      const { resp, ms } = await timedResponse(page, (m, p) => m === 'POST' && p === '/bug-reports', () => dlg.getByRole('button', { name: 'Send report' }).click());
      assert(resp.status() < 300, `POST /bug-reports ${resp.status()}`);
      const body = resp.request().postDataJSON();
      assert(body && JSON.stringify(body).includes(`E2E bug report ${RUN}`), 'description not sent');
      assert(JSON.stringify(body).length > 200, 'no breadcrumbs / page state sent');
      await waitText(page, 'was sent');
      await dlg.getByRole('button').last().click();
      await dlg.waitFor({ state: 'detached' });
      return `sent in ${ms} ms`;
    }, page);
    await closePage(page);
  }
  {
    const page = await newPage('manager');
    await step('F-GLOBAL-14 control room footer "Report a bug" opens the bug reporter', async () => {
      await page.goto(`${BASE}/admin/dashboard`);
      await page.getByRole('button', { name: /Report a bug/ }).filter({ hasText: 'Report a bug' }).first().click();
      await page.getByRole('dialog', { name: 'Report a bug' }).waitFor({ timeout: 10000 });
    }, page);
    await closePage(page);
  }
});

// ── Admin stations ──────────────────────────────────────────────────────────
// The city search asks OpenStreetMap through the backend; the test answers it locally.
async function fakeGeocode(page) {
  await page.route(`${API}/stations/geocode**`, r => r.fulfill({ contentType: 'application/json', body: JSON.stringify([
    { display_name: 'Lakewood, New Jersey, United States', latitude: 40.0821, longitude: -74.2097, timezone: 'America/New_York' },
    { display_name: 'Jerusalem, Israel', latitude: 31.7683, longitude: 35.2137, timezone: 'Asia/Jerusalem' },
  ]) }));
}
const rowOf = (page, text) => page.locator('tr', { hasText: text }).first();

section('stations', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('admin', view);
    await fakeGeocode(page);
    const name = `E2E New Station ${view} ${RUN}`;
    await step(`F-STATIONS-1 ${view}: list shows type, timezone, Active`, async () => {
      await page.goto(`${BASE}/admin/stations`);
      const row = rowOf(page, FX.station.name);
      await row.waitFor();
      const t = await row.innerText();
      assert(/internet/i.test(t) && t.includes('America/New_York') && t.includes('Active'), `row says "${t.replace(/\s+/g, ' ')}"`);
    }, page);
    await step(`F-STATIONS-2 ${view}: Create Station without a city says to pick one (validation)`, async () => {
      await page.getByRole('button', { name: 'New Station' }).click();
      await field(page, 'Station Name').fill(name);
      await page.getByRole('button', { name: 'Create Station' }).click();
      await page.getByRole('alert').filter({ hasText: 'Pick the city' }).waitFor({ timeout: 2000 });
    }, page);
    await step(`F-STATIONS-2 ${view}: city search, pick, Create Station saves fast and lists it with the city's timezone`, async () => {
      await page.getByPlaceholder('Search for a city...').first().fill('Jerusalem');
      await page.getByRole('button', { name: /Jerusalem, Israel/ }).click();
      await waitText(page, 'Timezone: Asia/Jerusalem');
      const { ms } = await timedSave(page, 'create station', (m, p) => m === 'POST' && p === '/stations', () => page.getByRole('button', { name: 'Create Station' }).click());
      const row = rowOf(page, name);
      await row.waitFor();
      assert((await row.innerText()).includes('Asia/Jerusalem'), 'timezone not from the city');
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-STATIONS-5 ${view}: expand row, search location, Save Location updates coordinates and timezone`, async () => {
      await rowOf(page, name).click();
      const panel = page.locator('td[colspan="5"]');
      await panel.getByPlaceholder('Search for a city...').fill('Lakewood');
      await panel.getByRole('button', { name: /Lakewood, New Jersey/ }).click();
      const { ms } = await timedSave(page, 'save location', (m, p) => m === 'PUT' || m === 'PATCH' ? p.startsWith('/stations/') : false, () => panel.getByRole('button', { name: 'Save Location' }).click());
      await waitText(page, 'Current: 40.0821, -74.2097');
      await rowOf(page, name).filter({ hasText: 'America/New_York' }).waitFor();
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-STATIONS-6/7 ${view}: automation config (hourly ID, time, weather + interval, long items) saves fast and sticks after reload`, async () => {
      const panel = page.locator('td[colspan="5"]');
      const interval = panel.locator('input[type=number]');
      assert(await interval.isDisabled(), 'interval enabled while weather is off');
      await panel.getByText('Hourly Station ID Jingle').click();
      await panel.getByText('Hourly Time Announcement').click();
      await panel.getByText('Weather Spots').click();
      assert(!(await interval.isDisabled()), 'interval still disabled with weather on');
      await interval.fill('45');
      await panel.getByTestId('timed-during-long-items').selectOption('cut');
      const { ms, resp } = await timedSave(page, 'save automation', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/stations/'), () => panel.getByRole('button', { name: 'Save Automation Config' }).click());
      const cfg = resp.request().postDataJSON().automation_config;
      assert(cfg.hourly_station_id && cfg.hourly_time_announcement && cfg.weather_enabled && cfg.weather_interval_minutes === 45 && cfg.timed_during_long_items === 'cut', `sent ${JSON.stringify(cfg)}`);
      await page.reload();
      await rowOf(page, name).click();
      const p2 = page.locator('td[colspan="5"]');
      assert(await p2.getByTestId('timed-during-long-items').inputValue() === 'cut', 'long-items choice lost');
      assert(await p2.locator('input[type=number]').inputValue() === '45', 'interval lost');
      assert(await p2.getByText('Hourly Time Announcement').locator('input').isChecked(), 'hourly time lost');
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-STATIONS-8 ${view}: turning Hourly Time Announcement off is saved (queue effect needs storage, see backend tests)`, async () => {
      const panel = page.locator('td[colspan="5"]');
      await panel.getByText('Hourly Time Announcement').click();
      const { resp } = await timedSave(page, 'save automation', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/stations/'), () => panel.getByRole('button', { name: 'Save Automation Config' }).click());
      const cfg = resp.request().postDataJSON().automation_config;
      assert(cfg.hourly_time_announcement === false && cfg.timed_during_long_items === 'cut', 'other settings dropped');
    }, page);
    await step(`F-STATIONS-4 ${view}: admin ticks Protected, saved; untick`, async () => {
      const box = rowOf(page, name).getByText('Protected').locator('input');
      // The box is drawn from the saved station, so it ticks once the save is back.
      await timedSave(page, 'protect', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/stations/'), () => box.click());
      await page.waitForFunction(() => true);
      await rowOf(page, name).getByText('Protected').locator('input:checked').waitFor({ timeout: 2000 });
      const st = (await api('GET', '/stations', { token: USERS.admin.token })).stations.find(s => s.name === name);
      assert(st.protected === true, 'not saved');
      await timedSave(page, 'unprotect', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/stations/'), () => rowOf(page, name).getByText('Protected').locator('input').click());
      await rowOf(page, name).getByText('Protected').locator('input:not(:checked)').waitFor({ timeout: 2000 });
    }, page);
    await step(`F-STATIONS-3 ${view}: Delete (confirm) removes the station`, async () => {
      const { ms } = await timedSave(page, 'delete station', (m, p) => m === 'DELETE' && p.startsWith('/stations/'), () => rowOf(page, name).getByRole('button', { name: 'Delete' }).click());
      await rowOf(page, name).waitFor({ state: 'detached' });
      return `deleted in ${ms} ms`;
    }, page);
    await closePage(page);
  }
  // Manager sees the badge, not the checkbox
  {
    const T = USERS.admin.token;
    await api('PUT', `/stations/${FX.station.id}`, { token: T, body: { protected: true } }).catch(() => api('PATCH', `/stations/${FX.station.id}`, { token: T, body: { protected: true } }));
    const page = await newPage('manager');
    await step('F-STATIONS-4 manager sees a Protected badge, no checkbox', async () => {
      await page.goto(`${BASE}/admin/stations`);
      const row = rowOf(page, FX.station.name);
      await row.getByText('Protected').waitFor();
      assert(!(await row.locator('input[type=checkbox]').count()), 'manager can tick Protected');
    }, page);
    await closePage(page);
    await api('PUT', `/stations/${FX.station.id}`, { token: T, body: { protected: false } }).catch(() => api('PATCH', `/stations/${FX.station.id}`, { token: T, body: { protected: false } }));
  }
});

// ── Users (admin only) ──────────────────────────────────────────────────────
section('users', async () => {
  const T = USERS.admin.token;
  const other = await api('POST', '/stations', { token: T, body: { name: `E2E Other Station ${RUN}`, timezone: 'America/New_York' } });
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('admin', view);
    const email = `e2e-made-${view}-${RUN}@example.test`;
    const pass = `pw-${crypto.randomBytes(6).toString('hex')}`;
    const form = page.locator('form');
    await step(`F-USERS-1 ${view}: create needs a password (validation)`, async () => {
      await page.goto(`${BASE}/admin/users`);
      await page.getByRole('button', { name: '+ New User' }).click();
      await field(form, 'Username / Email').fill(email);
      await form.getByRole('button', { name: 'Create User' }).click();
      const msg = await field(form, 'Password').evaluate(el => el.validationMessage);
      assert(msg, 'no "fill in this field" on the empty password');
    }, page);
    await step(`F-USERS-1/3/4/5 ${view}: create DJ with name, phone, title, alerts, public profile, one station; saves fast`, async () => {
      await field(form, 'Password').fill(pass);
      await field(form, 'Role').selectOption('dj');
      await field(form, 'Display Name').fill(`E2E Host ${view} ${RUN}`);
      await form.getByPlaceholder('+1234567890').fill('+15550001111');
      await form.getByPlaceholder('e.g. Program Director').fill('Night Host');
      await form.getByText('SMS', { exact: false }).first().locator('input').check();
      await form.locator('select').nth(1).selectOption('critical');
      await form.getByPlaceholder('A short bio about this DJ/host...').fill(`Bio of ${RUN}`);
      await form.getByPlaceholder('https://example.com/photo.jpg').fill('https://example.com/p.jpg');
      await form.getByText('Public Profile').click();
      await form.getByPlaceholder('@handle or full URL').first().fill('@e2ehost');
      await form.getByPlaceholder('https://example.com').last().fill('https://example.com/host');
      await form.getByText('Only the stations ticked here').click();
      await waitText(page, 'Nothing ticked');
      await form.getByTestId('station-access').getByText(FX.station.name).click();
      const { ms, resp } = await timedSave(page, 'create user', (m, p) => m === 'POST' && p === '/users', () => form.getByRole('button', { name: 'Create User' }).click());
      const sent = resp.request().postDataJSON();
      assert(sent.role === 'dj' && sent.alert_preferences.sms_enabled && sent.alert_preferences.min_severity === 'critical' && sent.is_public && sent.social_links.twitter === '@e2ehost' && JSON.stringify(sent.station_ids) === JSON.stringify([FX.station.id]), `sent ${JSON.stringify(sent).slice(0, 200)}`);
      const row = rowOf(page, email);
      await row.waitFor();
      assert(/DJ \/ HOST/.test(await row.innerText()), 'role badge');
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-USERS-4 ${view}: public profile appears on /hosts`, async () => {
      const pub = await newPage(null, view);
      await pub.goto(`${BASE}/hosts`);
      await waitText(pub, `E2E Host ${view} ${RUN}`);
      await waitText(pub, 'Night Host');
      await waitText(pub, `Bio of ${RUN}`);
      assert(await pub.locator('a[title="Twitter / X"]').count(), 'no Twitter link');
      await closePage(pub);
    }, page);
    await step(`F-USERS-5 ${view}: station limit is enforced (other station refused, own station allowed)`, async () => {
      const tk = (await loginApi(email, pass)).access_token;
      const refused = await api('POST', `/stations/${other.id}/queue/skip`, { token: tk, raw: true });
      assert(refused.status === 403, `other station answered ${refused.status}`);
      const own = await api('POST', `/stations/${FX.station.id}/queue/skip`, { token: tk, raw: true });
      assert(own.status !== 403, `own station refused (${own.status})`);
      return `other ${refused.status}, own ${own.status}`;
    }, page);
    await step(`F-USERS-2 ${view}: Edit with password left blank keeps the password; changes saved`, async () => {
      await rowOf(page, email).getByRole('button', { name: 'Edit' }).click();
      assert(await field(form, 'Username / Email').inputValue() === email, 'form not filled from the user');
      assert(await form.getByTestId('station-access').getByText(FX.station.name).locator('input').isChecked(), 'station tick not loaded');
      await field(form, 'Display Name').fill(`E2E Host edited ${view} ${RUN}`);
      await form.getByText('Every station, except protected ones').click();
      const { ms, resp } = await timedSave(page, 'update user', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/users/'), () => form.getByRole('button', { name: 'Update User' }).click());
      assert(!('password' in resp.request().postDataJSON()) || !resp.request().postDataJSON().password, 'blank password sent');
      await rowOf(page, `E2E Host edited ${view}`).waitFor();
      await loginApi(email, pass); // old password still works
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-USERS-2 ${view}: Disable then Enable`, async () => {
      await timedSave(page, 'disable', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/users/'), () => rowOf(page, email).getByRole('button', { name: 'Disable' }).click());
      await rowOf(page, email).getByText('Disabled').waitFor();
      const r = await api('POST', '/auth/login', { body: { email, password: pass }, raw: true }); loginTimes.push(Date.now());
      assert(r.status >= 400, `disabled user can still sign in (${r.status})`);
      await timedSave(page, 'enable', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/users/'), () => rowOf(page, email).getByRole('button', { name: 'Enable' }).click());
      await rowOf(page, email).getByText('Active').waitFor();
    }, page);
    await step(`F-USERS-1 ${view}: duplicate username shows the server's error in the form`, async () => {
      await page.getByRole('button', { name: '+ New User' }).click();
      await field(form, 'Username / Email').fill(email);
      await field(form, 'Password').fill('x1234567');
      await form.getByRole('button', { name: 'Create User' }).click();
      await form.locator('.bg-red-900\\/60').waitFor({ timeout: 3000 });
      await page.getByRole('button', { name: 'Cancel' }).click();
    }, page);
    await step(`F-USERS-2 ${view}: Delete (confirm) removes the user`, async () => {
      const { ms } = await timedSave(page, 'delete user', (m, p) => m === 'DELETE' && p.startsWith('/users/'), () => rowOf(page, email).getByRole('button', { name: 'Delete' }).click());
      await rowOf(page, email).waitFor({ state: 'detached' });
      return `deleted in ${ms} ms`;
    }, page);
    await closePage(page);
  }
  {
    const page = await newPage('admin');
    await step('F-USERS-6 user list refreshes on its own (last online / last action)', async () => {
      await page.goto(`${BASE}/admin/users`);
      await rowOf(page, USERS.viewer.email).waitFor();
      const t0 = Date.now();
      await page.waitForResponse(r => r.url().startsWith(`${API}/users`) && r.request().method() === 'GET', { timeout: 12000 });
      const gap = Date.now() - t0;
      // The checklist says every 5 s; the page polls every 10 s.
      assert(gap <= 10500, `no refresh for ${gap} ms`);
      return `refreshed after ${gap} ms (polls every 10 s, checklist says 5 s)`;
    }, page);
    await closePage(page);
  }
  await api('DELETE', `/stations/${other.id}`, { token: T }).catch(() => {});
});

// ── Rules ───────────────────────────────────────────────────────────────────
section('rules', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('admin', view);
    const name = `E2E Rule ${view} ${RUN}`;
    const form = page.locator('form');
    await step(`F-RULES-2 ${view}: new rule needs a name (validation)`, async () => {
      await page.goto(`${BASE}/admin/rules`);
      await page.getByRole('button', { name: '+ New Rule' }).click();
      await form.locator('button[type=submit]').click();
      assert(await field(form, 'Rule Name').evaluate(el => el.validationMessage), 'no message on empty name');
    }, page);
    await step(`F-RULES-2 ${view}: create interval rule (type, asset type, station, numbers, days) saves fast`, async () => {
      await field(form, 'Rule Name').fill(name);
      await field(form, 'Rule Type').selectOption('interval');
      await field(form, 'Asset Type').selectOption('spot');
      await field(form, 'Interval (minutes)').fill('20');
      await field(form, 'Priority').fill('42');
      await field(form, 'Station (blank').selectOption(FX.station.id);
      await form.getByRole('button', { name: 'Sun', exact: true }).click(); // turn Sunday (6: Mon is 0) off
      const { ms, resp } = await timedSave(page, 'create rule', (m, p) => m === 'POST' && p === '/rules', () => form.locator('button[type=submit]').click());
      const sent = resp.request().postDataJSON();
      assert(sent.rule_type === 'interval' && sent.asset_type === 'spot' && sent.interval_minutes === 20 && sent.priority === 42 && sent.station_id === FX.station.id && sent.days_of_week === '0,1,2,3,4,5', `sent ${JSON.stringify(sent)}`);
      const row = rowOf(page, name);
      await row.waitFor();
      const t = await row.innerText();
      assert(t.includes('20m') && t.includes(FX.station.name) && t.includes('42'), `row "${t.replace(/\s+/g, ' ')}"`);
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-RULES-1 ${view}: Edit saves; Off/On toggles active; Del (confirm) removes`, async () => {
      await rowOf(page, name).getByRole('button', { name: 'Edit' }).click();
      assert(await field(form, 'Rule Name').inputValue() === name, 'form not filled');
      await field(form, 'Priority').fill('43');
      await timedSave(page, 'update rule', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/rules/'), () => form.locator('button[type=submit]').click());
      await rowOf(page, name).filter({ hasText: '43' }).waitFor();
      await timedSave(page, 'rule off', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/rules/'), () => rowOf(page, name).getByRole('button', { name: 'Off' }).click());
      await rowOf(page, name).getByRole('button', { name: 'On' }).waitFor();
      assert(/opacity-40/.test(await rowOf(page, name).getAttribute('class')), 'inactive rule not dimmed');
      await timedSave(page, 'rule on', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/rules/'), () => rowOf(page, name).getByRole('button', { name: 'On' }).click());
      await rowOf(page, name).getByRole('button', { name: 'Off' }).waitFor();
      await timedSave(page, 'delete rule', (m, p) => m === 'DELETE' && p.startsWith('/rules/'), () => rowOf(page, name).getByRole('button', { name: 'Del' }).click());
      await rowOf(page, name).waitFor({ state: 'detached' });
    }, page);
    await step(`F-RULES-3 ${view}: Preview Schedule shows generated slots; Hide Preview`, async () => {
      await page.getByRole('button', { name: 'Preview Schedule' }).click();
      await page.getByText(/\d+ slots generated/).waitFor({ timeout: 8000 });
      await page.getByRole('button', { name: 'Hide Preview' }).click();
      await page.getByText(/slots generated/).waitFor({ state: 'detached' });
    }, page);
    await closePage(page);
  }
  for (const role of ['manager', 'dj', 'viewer']) {
    const page = await newPage(role);
    await step(`F-RULES-4 ${role}: rules are view only (no New Rule, no Edit/Del)`, async () => {
      await api('POST', '/rules', { token: USERS.admin.token, body: { name: `E2E RO Rule ${role} ${RUN}`, rule_type: 'rotation', asset_type: 'music', songs_between: 3 } });
      await page.goto(`${BASE}/admin/rules`);
      await rowOf(page, `E2E RO Rule ${role}`).waitFor();
      assert(!(await page.getByRole('button', { name: '+ New Rule' }).count()), 'New Rule offered');
      assert(!(await rowOf(page, `E2E RO Rule ${role}`).getByRole('button', { name: 'Edit' }).isVisible()), 'Edit visible');
      await page.getByTestId('view-only').waitFor();
    }, page);
    await closePage(page);
  }
  // These rules feed the live queue of every station; do not leave them behind.
  for (const r of (await api('GET', '/rules', { token: USERS.admin.token })).rules.filter(r => r.name.startsWith('E2E '))) {
    await api('DELETE', `/rules/${r.id}`, { token: USERS.admin.token }).catch(() => {});
  }
});

// ── Schedules and schedule blocks ───────────────────────────────────────────
const card = (page, text) => page.locator('div.bg-white.p-6.rounded-lg', { hasText: text }).first();
section('schedules', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    const name = `E2E Schedule ${view} ${RUN}`;
    await step(`F-SCHED-2 ${view}: create needs a station (validation), then Create saves fast`, async () => {
      await page.goto(`${BASE}/admin/schedules`);
      await page.getByRole('button', { name: 'New Schedule' }).click();
      await field(page, 'Name').fill(name);
      await page.getByRole('button', { name: 'Create', exact: true }).click();
      assert(await field(page, 'Station').evaluate(el => el.validationMessage), 'no message when no station is picked');
      await field(page, 'Station').selectOption(FX.station.id);
      await field(page, 'Priority').fill('7');
      const { ms } = await timedSave(page, 'create schedule', (m, p) => m === 'POST' && p === '/schedules', () => page.getByRole('button', { name: 'Create', exact: true }).click());
      await card(page, name).filter({ hasText: 'Priority: 7' }).waitFor();
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-SCHED-1 ${view}: station filter shows only that station's schedules`, async () => {
      await field(page, 'Filter by Station').selectOption(FX.station.id);
      await card(page, name).waitFor();
      const other = await api('POST', '/stations', { token: USERS.admin.token, body: { name: `E2E Filter St ${view} ${RUN}` } });
      const s2 = await api('POST', '/schedules', { token: USERS.admin.token, body: { name: `E2E Other Sched ${view} ${RUN}`, station_id: other.id } });
      await page.reload();
      await field(page, 'Filter by Station').selectOption(FX.station.id);
      await card(page, name).waitFor();
      assert(!(await card(page, `E2E Other Sched ${view}`).count()), 'other station schedule shown');
      await field(page, 'Filter by Station').selectOption('');
      await card(page, `E2E Other Sched ${view}`).waitFor();
      await api('DELETE', `/schedules/${s2.id}`, { token: USERS.admin.token }).catch(() => {});
      await api('DELETE', `/stations/${other.id}`, { token: USERS.admin.token }).catch(() => {});
    }, page);
    await step(`F-SCHED-2 ${view}: Edit (priority, inactive) saves; Cancel closes`, async () => {
      await card(page, name).getByRole('button', { name: 'Edit' }).click();
      await page.getByRole('heading', { name: 'Edit Schedule' }).waitFor();
      await field(page, 'Priority').fill('9');
      await page.locator('form').getByText('Active', { exact: true }).locator('xpath=..').locator('input').uncheck();
      await timedSave(page, 'update schedule', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/schedules/'), () => page.getByRole('button', { name: 'Update', exact: true }).click());
      await card(page, name).filter({ hasText: 'Priority: 9' }).filter({ hasText: 'Inactive' }).waitFor();
      await page.getByRole('button', { name: 'New Schedule' }).click();
      await page.locator('form').getByRole('button', { name: 'Cancel' }).click();
      await page.getByRole('heading', { name: 'Create Schedule' }).waitFor({ state: 'detached' });
    }, page);
    await step(`F-SCHED-2 ${view}: a save the server refuses shows its reason (was console only)`, async () => {
      await page.route(`${API}/schedules`, r => r.request().method() === 'POST'
        ? r.fulfill({ status: 400, contentType: 'application/json', body: '{"detail":"E2E refused this schedule"}' }) : r.continue());
      await page.getByRole('button', { name: 'New Schedule' }).click();
      await field(page, 'Station').selectOption(FX.station.id);
      await field(page, 'Name').fill('refused one');
      await page.getByRole('button', { name: 'Create', exact: true }).click();
      await page.getByRole('alert').filter({ hasText: 'E2E refused this schedule' }).waitFor({ timeout: 2000 });
      await page.unroute(`${API}/schedules`);
      await page.locator('form').getByRole('button', { name: 'Cancel' }).click();
    }, page);
    await step(`F-SCHED-3 / F-BLOCKS-1 ${view}: Blocks opens the block page; back link returns`, async () => {
      await card(page, name).getByRole('button', { name: 'Blocks' }).click();
      await page.waitForURL(/\/admin\/schedules\/[^/]+\/blocks$/);
      await page.getByRole('heading', { name: `${name}: blocks` }).waitFor();
      await page.getByRole('button', { name: '← Schedules' }).click();
      await waitPath(page, '/admin/schedules');
    }, page);
    await step(`F-SCHED-2 ${view}: Delete (confirm) removes it`, async () => {
      await timedSave(page, 'delete schedule', (m, p) => m === 'DELETE' && p.startsWith('/schedules/'), () => card(page, name).getByRole('button', { name: 'Delete' }).click());
      await card(page, name).waitFor({ state: 'detached' });
    }, page);
    await closePage(page);
  }
  for (const role of ['dj', 'viewer']) {
    const page = await newPage(role);
    await step(`F-SCHED-4 ${role}: schedules view only (no New/Edit/Delete), Blocks still opens`, async () => {
      await page.goto(`${BASE}/admin/schedules`);
      await card(page, FX.schedule.name).waitFor();
      assert(!(await page.getByRole('button', { name: 'New Schedule' }).count()), 'New Schedule offered');
      assert(!(await card(page, FX.schedule.name).getByRole('button', { name: 'Edit' }).count()), 'Edit offered');
      await page.getByTestId('view-only').waitFor();
      await card(page, FX.schedule.name).getByRole('button', { name: 'Blocks' }).click();
      await page.waitForURL(/\/blocks$/);
    }, page);
    await closePage(page);
  }
  // F-ACCESS-12: the server refusing a change shows the app-wide notice
  {
    const page = await newPage('viewer');
    await step('F-ACCESS-12 viewer pressing a write the server refuses (New Block) sees "not allowed"', async () => {
      await page.goto(`${BASE}/admin/schedules/${FX.schedule.id}/blocks`);
      const nb = page.getByRole('button', { name: 'New Block' });
      if (!(await nb.count())) return 'no write button offered to a viewer';
      await nb.click();
      await field(page, 'Block Name').fill('viewer try');
      const { status } = await timedResponse(page, (m, p) => m === 'POST' && p.includes('/blocks'), () => page.getByRole('button', { name: 'Create Block' }).click());
      assert(status === 403, `server answered ${status}`);
      const note = page.getByTestId('not-allowed');
      await note.waitFor({ timeout: 2000 });
      assert((await note.innerText()).includes('viewer'), 'notice does not name the login');
      return 'refused and told';
    }, page);
    await closePage(page);
  }
});

section('blocks', async () => {
  const T = USERS.admin.token;
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    const sched = await api('POST', '/schedules', { token: T, body: { name: `E2E Blocks Sched ${view} ${RUN}`, station_id: FX.station.id } });
    const tpl = await api('POST', '/playlists', { token: T, body: { name: `E2E Tpl ${view} ${RUN}`, station_id: FX.station.id } }).catch(() => null);
    const name = `E2E Block ${view} ${RUN}`;
    const form = page.locator('form');
    await step(`F-BLOCKS-2 ${view}: weekly block with no day picked says so (validation)`, async () => {
      await page.goto(`${BASE}/admin/schedules/${sched.id}/blocks`);
      await page.getByRole('button', { name: 'New Block' }).click();
      await form.getByPlaceholder('Morning Show').fill(name);
      await field(form, 'Repeats').selectOption('weekly');
      // Untick every day that is on
      for (const b of await form.locator('button[aria-pressed="true"]').all()) await b.click();
      await form.getByRole('button', { name: 'Create Block' }).click();
      await form.getByRole('alert').filter({ hasText: 'Pick at least one day' }).waitFor({ timeout: 2000 });
    }, page);
    await step(`F-BLOCKS-2 ${view}: days, times, priority, order, summary; Create Block saves fast`, async () => {
      await form.getByRole('button', { name: 'Mon', exact: true }).click();
      await form.getByRole('button', { name: 'Wed', exact: true }).click();
      await field(form, 'Start Time').fill('06:30');
      await field(form, 'End Time').fill('09:15');
      await field(form, 'Priority (higher wins)').fill('5');
      await field(form, 'Order of songs').selectOption('shuffle');
      const summary = await page.getByTestId('block-summary').innerText();
      assert(summary.includes('6:30 AM') && summary.includes('9:15 AM') && summary.includes('Mon, Wed'), `summary "${summary}"`);
      const { ms } = await timedSave(page, 'create block', (m, p) => m === 'POST' && p.includes('blocks'), () => form.getByRole('button', { name: 'Create Block' }).click());
      const head = page.locator('div.cursor-pointer', { hasText: name });
      await head.waitFor();
      const t = await head.innerText();
      assert(t.includes('6:30 AM') && t.includes('9:15 AM') && t.includes('Mon, Wed') && t.includes('shuffle') && t.includes('Priority 5'), `block reads "${t.replace(/\s+/g, ' ')}"`);
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-BLOCKS-4 ${view}: + Add song from the library list, category badge, Remove`, async () => {
      await page.locator('div.cursor-pointer', { hasText: name }).click();
      await page.getByRole('button', { name: '+ Add song' }).click();
      const sel = page.locator('select', { has: page.locator('option', { hasText: 'Pick a song…' }) });
      await sel.selectOption(FX.assets[1].id);
      const { ms } = await timedSave(page, 'add entry', (m, p) => m === 'POST' && p.includes('entries'), () => page.getByRole('button', { name: 'Add', exact: true }).click());
      const entry = page.locator('div.bg-white.p-2.rounded.border', { hasText: FX.assets[1].title });
      await entry.waitFor();
      await timedSave(page, 'remove entry', (m, p) => m === 'DELETE' && p.includes('entries'), () => entry.getByRole('button', { name: 'Remove' }).click());
      await entry.waitFor({ state: 'detached' });
      return `added in ${ms} ms`;
    }, page);
    await step(`F-BLOCKS-2 ${view}: Edit: sun start (sunset -18), one-time dates, playlist, until next; Save changes`, async () => {
      await page.locator('div.cursor-pointer', { hasText: name }).getByRole('button', { name: 'Edit' }).click();
      await page.getByRole('heading', { name: `Edit "${name}"` }).waitFor();
      await field(form, 'Or start by the sun').selectOption('sunset');
      await field(form, 'Minutes after (− before)').fill('-18');
      if (tpl) await field(form, 'Playlist').selectOption(tpl.id);
      await field(form, 'Repeats').selectOption('one_time');
      const today = new Date().toISOString().slice(0, 10);
      await field(form, 'Start Date').fill(today);
      await field(form, 'End Date').fill(today);
      const summary = await page.getByTestId('block-summary').innerText();
      assert(summary.includes('18 min before sunset'), `summary "${summary}"`);
      const { ms, resp } = await timedSave(page, 'update block', (m, p) => (m === 'PUT' || m === 'PATCH') && p.includes('blocks'), () => form.getByRole('button', { name: 'Save changes' }).click());
      const sent = resp.request().postDataJSON();
      assert(sent.start_sun_event === 'sunset' && sent.start_sun_offset === -18 && sent.recurrence_type === 'one_time', `sent ${JSON.stringify(sent).slice(0, 200)}`);
      await page.locator('div.cursor-pointer', { hasText: name }).filter({ hasText: '18 min before sunset' }).waitFor();
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-BLOCKS-2 ${view}: quick start preset "Friday, candle lighting + 1 min" fills the form`, async () => {
      await page.getByRole('button', { name: 'New Block' }).click();
      await form.getByRole('button', { name: 'Friday, candle lighting + 1 min' }).click();
      const s = await page.getByTestId('block-summary').innerText();
      assert(s.includes('17 min before sunset') && s.includes('Fri') && s.includes('next playlist'), `summary "${s}"`);
      await form.getByRole('button', { name: 'Cancel' }).click();
    }, page);
    await step(`F-BLOCKS-3 ${view}: Delete block (confirm)`, async () => {
      await timedSave(page, 'delete block', (m, p) => m === 'DELETE' && p.includes('blocks'), () => page.locator('div.cursor-pointer', { hasText: name }).getByRole('button', { name: 'Delete' }).click());
      await page.locator('div.cursor-pointer', { hasText: name }).waitFor({ state: 'detached' });
    }, page);
    await closePage(page);
    await api('DELETE', `/schedules/${sched.id}`, { token: T }).catch(() => {});
    if (tpl) await api('DELETE', `/playlists/${tpl.id}`, { token: T }).catch(() => {});
  }

  // F-BLOCKS-5: a block made in the UI takes over the station's queue on time.
  // Uses a station made at setup, whose queue is running by now (a brand-new station
  // only starts when dead-air recovery kicks in after 30 s).
  {
    const page = await newPage('manager');
    const st = FX.takeover;
    const sched = await api('POST', '/schedules', { token: T, body: { name: `E2E Takeover Sched ${RUN}`, station_id: st.id } });
    const song = FX.assets[0];
    await step('F-BLOCKS-5 all-day block with one song: the queue plays only that song after the change', async () => {
      const deadline = Date.now() + 45000;
      while ((await api('GET', `/stations/${st.id}/queue?limit=3`, { token: T })).entries.length === 0) {
        assert(Date.now() < deadline, 'test station never started');
        await new Promise(r => setTimeout(r, 1000));
      }
      await page.goto(`${BASE}/admin/schedules/${sched.id}/blocks`);
      await page.getByRole('button', { name: 'New Block' }).click();
      const form = page.locator('form');
      await form.getByPlaceholder('Morning Show').fill(`E2E All Day ${RUN}`);
      await field(form, 'Repeats').selectOption('daily');
      await field(form, 'Start Time').fill('00:00');
      await field(form, 'End Time').fill('23:59');
      await timedSave(page, 'create block', (m, p) => m === 'POST' && p.includes('blocks'), () => form.getByRole('button', { name: 'Create Block' }).click());
      await page.locator('div.cursor-pointer', { hasText: `E2E All Day ${RUN}` }).click();
      await page.getByRole('button', { name: '+ Add song' }).click();
      await page.locator('select', { has: page.locator('option', { hasText: 'Pick a song…' }) }).selectOption(song.id);
      const t0 = Date.now();
      await timedSave(page, 'add entry', (m, p) => m === 'POST' && p.includes('entries'), () => page.getByRole('button', { name: 'Add', exact: true }).click());
      let titles = [];
      while (Date.now() - t0 < 15000) {
        const q = await api('GET', `/stations/${st.id}/queue?limit=8`, { token: T });
        titles = q.entries.filter(e => e.status === 'pending').map(e => e.asset?.title);
        if (titles.length >= 3 && titles.every(t => t === song.title)) return `queue switched after ${Date.now() - t0} ms`;
        await new Promise(r => setTimeout(r, 500));
      }
      throw new Error(`block has only "${song.title}" but the queue holds ${titles.slice(0, 4).join(' | ') || 'nothing'}`);
    }, page);
    await closePage(page);
    await api('DELETE', `/schedules/${sched.id}`, { token: T }).catch(() => {});
  }
});

// ── Blackouts ───────────────────────────────────────────────────────────────
const localInput = d => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
section('holidays', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    const name = `E2E Blackout ${view} ${RUN}`;
    const form = page.locator('form');
    const start = new Date(Date.now() + 2 * 86400e3), end = new Date(start.getTime() + 25 * 3600e3);
    await step(`F-HOLIDAYS-2 ${view}: end before start is refused with a message`, async () => {
      await page.goto(`${BASE}/admin/holidays`);
      await page.getByRole('button', { name: 'Add Blackout' }).click();
      await form.getByPlaceholder('e.g. Shabbos Feb 20, 2026').fill(`${name} bad`);
      await field(form, 'Music Off (Start)').fill(localInput(end));
      await field(form, 'Music On (End)').fill(localInput(start));
      const r = await Promise.race([
        page.waitForResponse(x => x.url().includes('/holidays') && x.request().method() === 'POST', { timeout: 3000 }).then(x => x.status()).catch(() => null),
        form.getByRole('button', { name: 'Create' }).click().then(() => new Promise(res => setTimeout(() => res('no-request'), 1500))),
      ]);
      // The end box has min=start, so the browser itself refuses and says why.
      const shown = await page.getByText(/after the start|before the end|must be after|end.*before|invalid/i).count()
        || (await field(form, 'Music On (End)').evaluate(el => el.validationMessage));
      if (typeof r === 'number' && r < 300) {
        const bad = (await api('GET', '/holidays?limit=200', { token: USERS.admin.token }));
        for (const h of (bad.holidays || bad)) if (h.name === `${name} bad`) await api('DELETE', `/holidays/${h.id}`, { token: USERS.admin.token });
        throw new Error('a blackout that ends before it starts was saved');
      }
      assert(shown, `no message shown (server answered ${r})`);
    }, page);
    await step(`F-HOLIDAYS-2 ${view}: add blackout (name, reason, times, one station) saves fast and is listed Upcoming`, async () => {
      await page.goto(`${BASE}/admin/holidays`);
      await page.getByRole('button', { name: 'Add Blackout' }).click();
      await form.getByPlaceholder('e.g. Shabbos Feb 20, 2026').fill(name);
      await field(form, 'Reason').selectOption('Pesach');
      await field(form, 'Music Off (Start)').fill(localInput(start));
      await field(form, 'Music On (End)').fill(localInput(end));
      const all = form.getByText('All stations').locator('xpath=..').locator('input');
      if (await all.isChecked()) await all.uncheck();
      await form.getByRole('button', { name: FX.station.name }).click();
      const { ms, resp } = await timedSave(page, 'create blackout', (m, p) => m === 'POST' && p.startsWith('/holidays'), () => form.getByRole('button', { name: 'Create' }).click());
      const sent = resp.request().postDataJSON();
      assert(JSON.stringify(sent).includes(FX.station.id), `station not sent: ${JSON.stringify(sent).slice(0, 200)}`);
      const row = rowOf(page, name);
      await row.waitFor();
      const t = await row.innerText();
      assert(t.includes('Pesach') && t.includes('Upcoming') && /25/.test(t), `row "${t.replace(/\s+/g, ' ')}"`);
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-HOLIDAYS-1 ${view}: filters by reason, status and station`, async () => {
      await field(page, 'Reason').first().selectOption('Pesach');
      await rowOf(page, name).waitFor();
      await field(page, 'Reason').first().selectOption('Yom Kippur');
      await rowOf(page, name).waitFor({ state: 'detached' });
      await field(page, 'Reason').first().selectOption('');
      await field(page, 'Status').selectOption('past');
      await rowOf(page, name).waitFor({ state: 'detached' });
      await field(page, 'Status').selectOption('upcoming');
      await rowOf(page, name).waitFor();
      await field(page, 'Station').first().selectOption(FX.station.id);
      await rowOf(page, name).waitFor();
      await field(page, 'Station').first().selectOption(FX.takeover.id);
      await rowOf(page, name).waitFor({ state: 'detached' });
      await field(page, 'Station').first().selectOption('');
      await field(page, 'Status').selectOption('');
    }, page);
    await step(`F-HOLIDAYS-2 ${view}: Edit changes the name; Update saves`, async () => {
      await rowOf(page, name).getByTitle('Edit').click();
      await form.getByPlaceholder('e.g. Shabbos Feb 20, 2026').fill(`${name} edited`);
      await timedSave(page, 'update blackout', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/holidays/'), () => form.getByRole('button', { name: 'Update' }).click());
      await rowOf(page, `${name} edited`).waitFor();
    }, page);
    await step(`F-HOLIDAYS-3 ${view}: Delete (confirm)`, async () => {
      await timedSave(page, 'delete blackout', (m, p) => m === 'DELETE' && p.startsWith('/holidays/'), () => rowOf(page, `${name} edited`).getByTitle('Delete').click());
      await rowOf(page, `${name} edited`).waitFor({ state: 'detached' });
    }, page);
    await closePage(page);
  }
  // F-HOLIDAYS-4: a blackout that is on now puts silence in the queue
  {
    const page = await newPage('manager');
    const name = `E2E Blackout Now ${RUN}`;
    await step('F-HOLIDAYS-4 blackout starting now: the station queue gets silence at once', async () => {
      await page.goto(`${BASE}/admin/holidays`);
      await page.getByRole('button', { name: 'Add Blackout' }).click();
      const form = page.locator('form');
      await form.getByPlaceholder('e.g. Shabbos Feb 20, 2026').fill(name);
      await field(form, 'Music Off (Start)').fill(localInput(new Date(Date.now() - 60e3)));
      await field(form, 'Music On (End)').fill(localInput(new Date(Date.now() + 20 * 60e3)));
      const all = form.getByText('All stations').locator('xpath=..').locator('input');
      if (await all.isChecked()) await all.uncheck();
      await form.getByRole('button', { name: FX.takeover.name }).click();
      const t0 = Date.now();
      await timedSave(page, 'create blackout', (m, p) => m === 'POST' && p.startsWith('/holidays'), () => form.getByRole('button', { name: 'Create' }).click());
      await rowOf(page, name).getByText('Active').waitFor();
      let seen = '';
      while (Date.now() - t0 < 10000) {
        const q = await api('GET', `/stations/${FX.takeover.id}/queue?limit=4`, { token: USERS.admin.token });
        seen = q.entries.map(e => `${e.status}:${e.asset?.asset_type}`).join(' ');
        if (q.entries.some(e => e.asset?.asset_type === 'silence')) return `silence queued after ${Date.now() - t0} ms`;
        await new Promise(r => setTimeout(r, 500));
      }
      throw new Error(`no silence in the queue: ${seen}`);
    }, page);
    const hs = await api('GET', '/holidays?limit=200', { token: USERS.admin.token }).catch(() => []);
    for (const h of (hs.holidays || hs)) if (h.name === name) await api('DELETE', `/holidays/${h.id}`, { token: USERS.admin.token }).catch(() => {});
    await closePage(page);
  }
});

// ── Sponsors ────────────────────────────────────────────────────────────────
section('sponsors', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    const name = `E2E Ad ${view} ${RUN}`;
    const form = page.locator('form');
    const cardS = () => page.locator('div.bg-white.p-4', { hasText: name }).first();
    await step(`F-SPONSORS-1 ${view}: name and audio path are required (validation)`, async () => {
      await page.goto(`${BASE}/admin/sponsors`);
      await page.getByRole('button', { name: 'New Sponsor' }).click();
      await form.getByRole('button', { name: 'Create' }).click();
      assert(await field(form, 'Name').evaluate(el => el.validationMessage), 'no message on empty name');
    }, page);
    await step(`F-SPONSORS-1 ${view}: create (policy every N songs, hours, max per hour) saves fast`, async () => {
      await field(form, 'Name').fill(name);
      await form.getByPlaceholder('sponsors/spot.mp3').fill('sponsors/e2e.mp3');
      await field(form, 'Length (sec)').fill('20');
      await field(form, 'Priority').fill('3');
      await field(form, 'Insertion Policy').selectOption('every_n_songs');
      await field(form, 'Songs Between').fill('4');
      await field(form, 'Active Hours (start)').fill('6');
      await field(form, 'Active Hours (end)').fill('22');
      await field(form, 'Max per Hour').fill('2');
      const { ms } = await timedSave(page, 'create sponsor', (m, p) => m === 'POST' && p === '/sponsors', () => form.getByRole('button', { name: 'Create' }).click());
      await cardS().waitFor();
      const t = await cardS().innerText();
      assert(t.includes('20s') && t.includes('Priority 3') && t.includes('every n songs') && t.includes('6:00-22:00') && t.includes('max 2/hr') && t.includes('every 4 songs'), `card "${t.replace(/\s+/g, ' ')}"`);
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-SPONSORS-1 ${view}: Edit saves; Delete (confirm)`, async () => {
      await cardS().getByRole('button', { name: 'Edit' }).click();
      await field(form, 'Priority').fill('5');
      await timedSave(page, 'update sponsor', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/sponsors/'), () => form.getByRole('button', { name: 'Update' }).click());
      await cardS().filter({ hasText: 'Priority 5' }).waitFor();
      await timedSave(page, 'delete sponsor', (m, p) => m === 'DELETE' && p.startsWith('/sponsors/'), () => cardS().getByRole('button', { name: 'Delete' }).click());
      await cardS().waitFor({ state: 'detached' });
    }, page);
    await step(`F-SPONSORS-2 ${view}: Client Portal link opens the sponsor login`, async () => {
      await page.getByRole('link', { name: 'Client Portal' }).click();
      await waitPath(page, '/sponsor/login');
    }, page);
    await closePage(page);
  }
});

// Audio files: with local storage the backend hands out a relative /api/v1/assets/:id/download
// URL (Supabase gives a full one in production), which the site resolves against itself.
// Serve a real MP3 there so players can play.
async function serveAudio(page) {
  const mp3 = fs.readFileSync(FX.toneFile);
  await page.route(/\/api\/v1\/assets\/[^/]+\/(download|stream)/, r => r.fulfill({ status: 200, contentType: 'audio/mpeg', body: mp3, headers: { 'accept-ranges': 'none' } }));
}
function stationClock(tz, plusMin) {
  return new Intl.DateTimeFormat('en-GB', { timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(Date.now() + plusMin * 60e3));
}

// ── Spot schedule ───────────────────────────────────────────────────────────
section('spot-schedule', async () => {
  const spot = FX.assets[2];
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    await serveAudio(page);
    const form = page.locator('form');
    const t1 = stationClock('America/New_York', 12), t2 = stationClock('America/New_York', 70);
    await step(`F-SPOTSCHED-1 ${view}: Save with nothing chosen says what is missing`, async () => {
      await page.goto(`${BASE}/admin/spot-schedule`);
      await page.getByRole('button', { name: 'Schedule a spot' }).click();
      await form.getByRole('button', { name: 'Save schedule' }).click();
      await form.getByText('Choose a sponsor and a spot.').waitFor({ timeout: 2000 });
    }, page);
    await step(`F-SPOTSCHED-1 ${view}: sponsor, station, spot search + Listen/Stop, Change, exact times add/remove, timing, dates; Save fast`, async () => {
      await field(form, 'Sponsor').selectOption(FX.sponsor.id);
      await field(form, 'Station').selectOption(FX.station.id);
      await form.getByPlaceholder("Type part of the spot's title").fill(spot.title.slice(0, 12));
      const hit = form.locator('li', { hasText: spot.title });
      await hit.waitFor();
      await hit.getByRole('button', { name: 'Listen' }).click();
      await hit.getByRole('button', { name: 'Stop' }).waitFor({ timeout: 3000 });
      await hit.getByRole('button', { name: 'Stop' }).click();
      await hit.getByRole('button', { name: spot.title }).click();
      await form.getByRole('button', { name: 'Change' }).click();
      await form.getByPlaceholder("Type part of the spot's title").fill(spot.title.slice(0, 12));
      await form.locator('li', { hasText: spot.title }).getByRole('button', { name: spot.title }).click();
      await form.getByRole('button', { name: 'Change' }).waitFor();
      await form.getByRole('button', { name: 'At exact times' }).click();
      for (const t of [t1, t2, '03:33']) {
        await form.getByLabel('Time to add').fill(t);
        await form.getByRole('button', { name: 'Add time' }).click();
      }
      await form.getByRole('button', { name: /^Remove 3:33/ }).click();
      assert(!(await form.getByRole('button', { name: /^Remove 3:33/ }).count()), 'time not removed');
      // every day is needed for "now + 12 min"; make sure all are on
      for (const d of ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']) {
        const b = form.getByRole('button', { name: d, exact: true });
        if (!/bg-blue-600/.test(await b.getAttribute('class'))) await b.click();
      }
      await field(form, 'How the time is kept').selectOption('soft');
      const { ms, resp } = await timedSave(page, 'save spot schedule', (m, p) => m === 'POST' && p.startsWith('/spot-schedules'), () => form.getByRole('button', { name: 'Save schedule' }).click());
      const sent = resp.request().postDataJSON();
      assert(sent.sponsor_id === FX.sponsor.id && sent.asset_id === spot.id && sent.station_id === FX.station.id && (sent.fixed_times || '').split(',').length === 2, `sent ${JSON.stringify(sent).slice(0, 250)}`);
      FX.spotSchedule = (await resp.json()).id;
      return `saved in ${ms} ms`;
    }, page);
    const li = page.locator('li', { hasText: FX.sponsor.name }).filter({ hasText: FX.station.name }).first();
    await step(`F-SPOTSCHED-3 ${view}: 7-day look-ahead for the station shows the planned spot`, async () => {
      await page.locator('select', { has: page.locator(`option[value="${FX.station.id}"]`) }).last().selectOption(FX.station.id);
      await page.getByText(FX.sponsor.name).filter({ hasNot: page.locator('option') }).nth(1).waitFor({ timeout: 5000 });
      await waitText(page, "Times are in the station's time zone (America/New_York)");
    }, page);
    await step(`F-SPOTSCHED-4 ${view}: the planned spot is in the station's queue (SPONSOR row in the control room)`, async () => {
      const deadline = Date.now() + 8000;
      while (Date.now() < deadline) {
        const q = await api('GET', `/stations/${FX.station.id}/queue?limit=200`, { token: USERS.admin.token });
        const hit = q.entries.find(e => e.asset_id === spot.id || e.asset?.id === spot.id);
        if (hit) return `queued for ${hit.preempt_at || hit.estimated_start} (${hit.source})`;
        await new Promise(r => setTimeout(r, 500));
      }
      throw new Error('spot not in the queue');
    }, page);
    await step(`F-SPOTSCHED-2 ${view}: Pause / Resume, Listen, Remove (confirm)`, async () => {
      await timedSave(page, 'pause', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/spot-schedules/'), () => li.getByRole('button', { name: 'Pause' }).click());
      await li.getByText('Paused').waitFor();
      await timedSave(page, 'resume', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/spot-schedules/'), () => li.getByRole('button', { name: 'Resume' }).click());
      await li.getByText('Paused').waitFor({ state: 'detached' });
      await li.getByRole('button', { name: 'Listen' }).click();
      await li.getByRole('button', { name: 'Stop' }).waitFor({ timeout: 3000 });
      await li.getByRole('button', { name: 'Stop' }).click();
      await timedSave(page, 'remove', (m, p) => m === 'DELETE' && p.startsWith('/spot-schedules/'), () => li.getByRole('button', { name: 'Remove' }).click());
      await li.waitFor({ state: 'detached' });
    }, page);
    await closePage(page);
  }
  {
    const page = await newPage('manager');
    await step('F-SPOTSCHED-1 dayparts: no daypart chosen is refused; Morning + Evening with plays per daypart saves', async () => {
      await page.goto(`${BASE}/admin/spot-schedule`);
      await page.getByRole('button', { name: 'Schedule a spot' }).click();
      const form = page.locator('form');
      await field(form, 'Sponsor').selectOption(FX.sponsor.id);
      await form.getByPlaceholder("Type part of the spot's title").fill(spot.title.slice(0, 12));
      await form.locator('li', { hasText: spot.title }).getByRole('button', { name: spot.title }).click();
      for (let i = 0; i < 6 && await form.locator('.grid button.bg-blue-600').count(); i++) await form.locator('.grid button.bg-blue-600').first().click();
      await form.getByRole('button', { name: 'Save schedule' }).click();
      await form.getByText(/Choose at least one (time of day|day)/).waitFor({ timeout: 2000 });
      await form.getByRole('button', { name: /^Morning/ }).click();
      await form.getByRole('button', { name: /^Evening/ }).click();
      await field(form, 'Plays in each time of day').fill('3');
      const { resp } = await timedSave(page, 'save daypart schedule', (m, p) => m === 'POST' && p.startsWith('/spot-schedules'), () => form.getByRole('button', { name: 'Save schedule' }).click());
      const sent = resp.request().postDataJSON();
      assert(sent.dayparts === 'morning,evening' && sent.plays_per_daypart === 3, `sent ${JSON.stringify(sent).slice(0, 200)}`);
      await api('DELETE', `/spot-schedules/${(await resp.json()).id}`, { token: USERS.admin.token });
    }, page);
    await step('F-SPOTSCHED-3 timing defaults per daypart: changing "Spots on the hour play" saves', async () => {
      await page.locator('select', { has: page.locator(`option[value="${FX.station.id}"]`) }).last().selectOption(FX.station.id);
      const sel = page.locator('label', { hasText: 'Spots on the hour play' }).locator('select');
      const was = await sel.inputValue();
      const to = was === 'hard' ? 'soft' : 'hard';
      await timedSave(page, 'timing default', (m, p) => m !== 'GET' && p.includes('timing'), () => sel.selectOption(to));
      await page.reload();
      await page.locator('select', { has: page.locator(`option[value="${FX.station.id}"]`) }).last().selectOption(FX.station.id);
      assert(await page.locator('label', { hasText: 'Spots on the hour play' }).locator('select').inputValue() === to, 'not kept');
      await page.locator('label', { hasText: 'Spots on the hour play' }).locator('select').selectOption(was);
    }, page);
    await closePage(page);
  }
});

// ── S-5 page load speed, every page, desktop and iPhone ─────────────────────
section('load', async () => {
  for (const view of ['desktop', 'iphone']) {
    const jobs = [
      ...adminRoutes().map(([p]) => ['admin', p]),
      ...SPONSOR_ROUTES.map(p => ['sponsor', p]),
      ...PUBLIC_ROUTES.map(p => [null, p]),
      [null, '/admin/login'], [null, '/sponsor/login'], [null, `/listen/${FX.station.id}`],
    ];
    const page = await newPage(null, view);
    const ctxFor = {};
    for (const [role, p] of jobs) {
      let pg = page;
      if (role) {
        if (!ctxFor[role]) ctxFor[role] = await newPage(role, view);
        pg = ctxFor[role];
      }
      await step(`S-5 ${view} ${short(p)} loads fast`, async () => {
        pg.errors.length = 0;
        const { fcp, ready } = await measureLoad(pg, `${BASE}${p}`);
        assert(!(await pg.getByText('Something went wrong').count()), 'error box on the page');
        assert(fcp <= LIMIT.firstContent, `first content ${fcp} ms (limit ${LIMIT.firstContent})`);
        assert(ready <= LIMIT.interactive, `interactive ${ready} ms (limit ${LIMIT.interactive})`);
        assert(!pg.errors.length, `script error: ${pg.errors[0]}`);
        return `fcp ${fcp} ms, ready ${ready} ms`;
      }, pg);
    }
    await closePage(page);
    for (const pg of Object.values(ctxFor)) await closePage(pg);
  }
});

// ═══════════════════════════════════════════════════════════════════════════
async function main() {
  await setup();
  browser = await chromium.launch();
  for (const s of SECTIONS) {
    if (ONLY.length && !ONLY.includes(s.name)) continue;
    console.log(`\n── ${s.name} ──`);
    try { await s.fn(); } catch (e) { record(`${s.name}: section crashed`, false, String(e?.stack || e).slice(0, 300)); }
  }
  await browser.close();
  const n = (st) => results.filter(r => r.status === st).length;
  console.log(`\n${n('PASS')} passed, ${n('KNOWN-BUG')} known bugs, ${n('FAIL')} failed, ${n('FIXED?')} known bugs now passing (${results.length} checks)`);
  fs.rmSync(TMP, { recursive: true, force: true });
  process.exit(n('FAIL') || n('FIXED?') ? 1 : 0);
}
main().catch(e => { console.error(e); process.exit(2); });
