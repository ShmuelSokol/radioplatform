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
import { allowEverywhere, guardBrowser, guardVerdict } from './lib/guard.mjs';   // look at the screen: see lib/guard.mjs

const HERE = path.dirname(fileURLToPath(import.meta.url));

// What this suite provokes on purpose, or what only the local test stack does. Each needs its reason.
allowEverywhere(/Pick at least one day for a weekly block/, 'the schedule blocks page is asked to save a weekly block with no day');
allowEverywhere(/alert: Pick the city from the list under Location\./, 'F-STATIONS-2 presses Create Station with no city to see this message');
allowEverywhere(rec => /\b401\b/.test(`${rec.kind} ${rec.text}`) && /\/api\/v1\/(auth\/me|activity\/page-view)\b/.test(rec.text) && /\/admin\/stations/.test(rec.url || ''),
  'F-GLOBAL-12 plants a bad access token and checks the site renews it without signing out');
allowEverywhere(/(localhost:\d+|Failed to fetch )\/api\/v1\/assets\/[0-9a-f-]+\/download/,
  'local storage hands out a relative audio URL that the preview server answers itself; production hands out a storage URL');
// What the walk-through provokes on purpose. Each line: what may show, where, and which check causes it.
const provoked = (what, where, reason) => allowEverywhere(
  rec => what.test(`${rec.kind} ${rec.text}`) && (!where || where.test(rec.url || '')), reason);
provoked(/auth\/me.*ERR_CONNECTION_REFUSED|ERR_CONNECTION_REFUSED.*auth\/me/, /\/admin\/schedules/, 'F-ACCESS-4 cuts the account lookup to see "Try again"');
provoked(/\b403\b.*\/api\/v1\/schedules\b/, /\/admin\/schedules$/, 'the view-only checks press Create as a role that may not change schedules');
provoked(/alert:? The title cannot be empty/, /\/admin\/assets\//, 'F-ASSETDETAIL-9 leaves the title empty to see the refusal');
provoked(/\b400\b.*\/api\/v1\/schedules\b|Failed to create schedule: AxiosError|alert:? E2E refused this schedule/, null, 'F-SCHED: the server is made to refuse a schedule to see the message');
provoked(/\b(401|429)\b.*\/api\/v1\/auth\/login/, /\/(admin|sponsor)\/login/, 'sign-in checks type a wrong password and hit the sign-in limit');
provoked(/\b409\b.*\/api\/v1\/users\b/, /\/admin\/users/, 'F-USERS creates a user whose email is taken to see the message');
provoked(/example\.com\/p\.jpg/, /\/hosts/, 'the test DJ has a photo address that does not exist');
provoked(/alert:? (Pick at least one day for a weekly block\.|Give the block a name\.|Add at least one time of day\.)/, null, 'the block forms are sent empty to see each message');
provoked(/\b503\b.*weather-readouts|alert:? Could not (show the template preview|make the readout): [Ww]eather is not set up/, /\/admin\/studio/, 'no weather key in the test setup; the checks read these messages');
provoked(/\b400\b.*\/crm\/raffles\/[0-9a-f-]+\/draw/, /\/admin\/crm/, 'F-CRM draws a raffle with no entries');
provoked(/\b400\b.*\/billing\/invoices\/[0-9a-f-]+\/checkout/, /\/sponsor\/billing/, 'Stripe is off in the test setup; F-SPBILL-1 reads the refusal');
provoked(/e2e page crash/, null, 'F-GLOBAL: a page is made to crash to see the error page');
provoked(/\/assets\/(Sponsors|Archives)-[\w-]+\.js|The site was updated while this tab was open/, null, 'a release is published under the open tab to see the reload notice');
provoked(/\b401\b.*\/api\/v1\/auth\/refresh/, /\/admin\/stations/, 'the session checks use a sign-in that was ended');
provoked(/\b401\b.*\/api\/v1\/auth\/(me|refresh)/, /\/sponsor\/campaigns/, 'the sponsor session checks open the portal with an ended sign-in');
provoked(/\b429\b.*\/sponsorship\/copy-review/, /\/sponsor\/wording/, 'the wording check is pressed past its limit of 12 a minute');
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
  'V2-101 a11y-gaps': 'basic accessibility gaps (fields without labels, grey-on-dark text under 4.5:1, see the detail per page); totals printed after the a11y section',
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
    const at = (String(e?.stack || '').match(/admin-flows\.mjs:(\d+)/) || [])[1];
    return record(name, false, String(e?.message || e).split('\n')[0].slice(0, 240) + (at ? ` @line ${at}` : ''));
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
// Hold a long edit's answer for a moment after the server finished, so a person (and this
// check) sees the progress bar while it waits, and the bar reaching 100% and "Done"
async function holdEditAnswer(page, pathEnd, ms = 2500) {
  const glob = `${API}/assets/*/${pathEnd}*`;
  await page.route(glob, async r => {
    const resp = await r.fetch();
    await new Promise(res => setTimeout(res, ms));
    await r.fulfill({ response: resp });
  });
  return () => page.unroute(glob);
}
async function seeProgressBar(page, what) {
  const bar = page.getByTestId('edit-progress');
  await bar.waitFor({ timeout: 5000 });
  if (process.env.SHOT_DIR) {                                                  // for a person to look at
    await page.waitForTimeout(1200);
    await page.screenshot({ path: `${process.env.SHOT_DIR}/progress-${what.replace(/\W+/g, '-')}.png` });
  }
  assert(await bar.getByRole('progressbar').count() === 1, `${what}: no progressbar inside the progress box`);
  await until(async () => /^100%/.test((await page.getByTestId('edit-progress-pct').innerText().catch(() => '')).trim()), `${what}: the bar reaches 100%`, 8000);
  assert(/Done/.test(await bar.innerText()), `${what}: the bar does not say Done at the end: ${await bar.innerText()}`);
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

/** Polls fn until it returns something truthy (or throws after timeout). */
async function until(fn, what, timeout = 5000) {
  const t0 = Date.now();
  for (;;) {
    const v = await fn().catch(() => false);
    if (v) return v;
    if (Date.now() - t0 > timeout) throw new Error(`timed out waiting for ${what}`);
    await new Promise(r => setTimeout(r, 100));
  }
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
  for (const [i, t] of [['1', 'music'], ['2', 'music'], ['3', 'spot'], ['4', 'jingle']].entries()) {
    const f = makeTone(path.join(TMP, `fx${i}.mp3`), t[1] === 'spot' ? 30 : t[1] === 'jingle' ? 12 : 170 + i * 15, 300 + i * 120, '32k');
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
    [`/admin/schedules/${FX.schedule.id}/blocks`, ALL], ['/admin/holidays', MGR], ['/admin/sponsors', MGR], ['/admin/campaigns', ALL],
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
  manager: ['/admin/rules', '/admin/bug-reports'],   // managers read bug reports; only an admin closes them
  dj: ['/admin/assets', '/admin/schedules', '/admin/rules', '/admin/program-clock', '/admin/campaigns'],
  viewer: ['/admin/dashboard', '/admin/assets', '/admin/schedules', '/admin/rules', '/admin/program-clock', '/admin/campaigns'],
};
// Words a button that changes something starts with
const WRITE_WORDS = /^(\+ ?)?(new|add|create|edit|delete|remove|save|upload|update|approve|reject|publish|rename|import|resolve|generate|record|schedule a|duplicate|copy to|move|insert|go next|loop|fade|soft fade|go live|● go live|play now|play next|mix over|next|start|▶ start|end show|panic|generate)\b/i;
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
      const offeredWrites = [];
      for (const [p, roles] of adminRoutes()) {
        if (!roles.includes(role)) continue;
        if (p.includes('/live/') || p.includes('audio-enhance')) continue;
        await page.goto(`${BASE}${p}`);
        await page.waitForFunction(() => document.querySelector('main')?.innerText.trim().length > 0, null, { timeout: 8000 }).catch(() => {});
        const expect = VIEW_ONLY[role].some(v => p === v || p.startsWith(v + '/'));
        let has = false;
        try { await page.getByTestId('view-only').waitFor({ state: 'visible', timeout: expect ? 3000 : 400 }); has = true; } catch { has = false; }
        if (has !== expect) wrong.push(`${short(p)} ${expect ? 'missing' : 'shown wrongly'}`);
        // A view-only screen offers nothing that changes things: no New / Edit / Delete /
        // Save buttons beside the badge (live 2026-11-18: a viewer's schedule blocks page
        // had New Block, Edit and Delete)
        if (expect) {
          await page.waitForTimeout(400);
          const offered = await page.evaluate(() => {
            const seen = el => { const r = el.getBoundingClientRect(); const c = getComputedStyle(el); return r.width > 0 && r.height > 0 && c.visibility !== 'hidden'; };
            return [...document.querySelectorAll('main button:not([role="tab"]), main a, main [role="button"]')]
              .filter(el => seen(el) && !el.matches(':disabled') && el.getAttribute('aria-disabled') !== 'true')
              .flatMap(el => [el.innerText, el.getAttribute('aria-label')].map(t => (t || '').trim().replace(/\s+/g, ' ')))
              .filter(Boolean);
          });
          if (process.env.DUMP_BUTTONS) console.log(`BUTTONS ${role} ${short(p)}: ${[...new Set(offered)].join(' | ')}`);
          // "Save as Default" keeps the person's own pre-listen lengths, not the item
          const writes = [...new Set(offered.filter(t => WRITE_WORDS.test(t) && t !== 'Save as Default'))];
          if (writes.length) offeredWrites.push(`${short(p)}: ${writes.join(', ')}`);
        }
      }
      assert(!wrong.length, wrong.join('; '));
      assert(!offeredWrites.length, `view-only screens offer changes: ${offeredWrites.join('; ')}`);
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
    await step(`F-GLOBAL-1 ${view}: public nav links Stations / Schedule / DJs / Archives navigate`, async () => {
      await page.goto(`${BASE}/stations`);
      await page.locator("nav a", { hasText: "KBR Studio" }).waitFor();
      for (const [label, p] of [['Schedule', '/guide'], ['DJs', '/hosts'], ['Archives', '/archives'], ['Stations', '/stations']]) {
        const link = page.locator('nav').getByRole('link', { name: label, exact: true });
        if (view === 'iphone' && !(await link.first().isVisible())) {
          // Phones: the links must be reachable through a menu button.
          const menu = page.locator('nav button[aria-label="Menu"]');
          assert(await menu.isVisible().catch(() => false), `no way to reach "${label}" on a phone (links hidden, no menu)`);
          await menu.click();
        }
        await page.locator("nav").getByRole("link", { name: label, exact: true }).locator("visible=true").first().click();
        await waitPath(page, p);
        await page.locator("nav a", { hasText: "KBR Studio" }).waitFor();
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
    Sponsors: [['All Clients', '/admin/sponsors'], ['Spot Schedule', '/admin/spot-schedule'], ['Record Spots', '/admin/spot-recordings'], ['Campaigns', '/admin/campaigns'], ['Sponsorship Rules', '/admin/sponsorship-rules']],
    Content: [['Library', '/admin/assets'], ['Categories', '/admin/categories'], ['Types', '/admin/asset-types'], ['Playlists', '/admin/playlists'], ['Studio', '/admin/studio'], ['Reviews', '/admin/reviews']],
    Broadcast: [['Schedules', '/admin/schedules'], ['Program Blocks', '/admin/program-blocks'], ['Schedule Check', '/admin/schedule-check'], ['Rules', '/admin/rules'], ['Blackouts', '/admin/holidays'], ['Program Clock', '/admin/program-clock'], ['Live Shows', '/admin/live']],
    Audience: [['Listeners', '/admin/listeners'], ['Analytics', '/admin/analytics'], ['Requests', '/admin/requests'], ['Public Schedule', '/guide'], ['Archives', '/archives'], ['DJs', '/hosts']],
    Settings: [['Users', '/admin/users'], ['Activity', '/admin/activity'], ['Alerts', '/admin/alerts'], ['Bug Reports', '/admin/bug-reports'], ['Site Updates', '/admin/site-updates'], ['Manage Stations', '/admin/stations'], ['CRM', '/admin/crm']],
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
      const entries = [['Dashboard', '/admin/dashboard'], ['Library', '/admin/assets'], ['Schedules', '/admin/schedules'], ['Requests', '/admin/requests'], ['All Clients', '/admin/sponsors'], ['Manage Stations', '/admin/stations'], ['Stations', '/stations']];
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
        if (view === 'iphone') {
          // Every visible button and button-like link must sit fully inside the screen
          // (the Archives "+ New Archive" was cut off at the right edge), unless it lives
          // in its own sideways scroller.
          const cut = await page.evaluate((vw) => {
            const out = [];
            for (const el of document.querySelectorAll('main button, main a[class*="bg-"], main [role="button"]')) {
              const r = el.getBoundingClientRect();
              if (!r.width || !r.height || getComputedStyle(el).visibility === 'hidden') continue;
              let q = el.parentElement, scroller = false;
              while (q && q !== document.body) { const o = getComputedStyle(q).overflowX; if (o === 'auto' || o === 'scroll') { scroller = true; break; } q = q.parentElement; }
              if (scroller) continue;
              if (r.left < -1 || r.right > vw + 1) out.push(`"${(el.innerText || el.getAttribute('aria-label') || el.title || '').trim().slice(0, 24)}" ${Math.round(r.left)}..${Math.round(r.right)}`);
            }
            return out.slice(0, 3);
          }, devW);
          if (cut.length) bad.push(`${short(p)} buttons off screen: ${cut.join(', ')}`);
        }
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
        await page.locator('button[aria-label="Report a bug"]:visible, [data-testid="bug-top"]:visible').first().click();
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
      await rowOf(page, name).getByText(name, { exact: true }).first().click();   // the name, as a person taps it: the card's middle holds the Protected box on a phone
      const panel = page.getByTestId('station-panel');
      await panel.getByPlaceholder('Search for a city...').fill('Lakewood');
      await panel.getByRole('button', { name: /Lakewood, New Jersey/ }).click();
      const { ms } = await timedSave(page, 'save location', (m, p) => m === 'PUT' || m === 'PATCH' ? p.startsWith('/stations/') : false, () => panel.getByRole('button', { name: 'Save Location' }).click());
      await waitText(page, 'Current: 40.0821, -74.2097');
      await rowOf(page, name).filter({ hasText: 'America/New_York' }).waitFor();
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-STATIONS-6/7 ${view}: automation config (hourly ID, time, weather + interval, long items) saves fast and sticks after reload`, async () => {
      const panel = page.getByTestId('station-panel');
      const interval = panel.locator('input[type=number]:not([data-testid])');   // not the crossfade box
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
      await rowOf(page, name).getByText(name, { exact: true }).first().click();
      const p2 = page.getByTestId('station-panel');
      assert(await p2.getByTestId('timed-during-long-items').inputValue() === 'cut', 'long-items choice lost');
      assert(await p2.locator('input[type=number]:not([data-testid])').inputValue() === '45', 'interval lost');
      assert(await p2.getByText('Hourly Time Announcement').locator('input').isChecked(), 'hourly time lost');
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-STATIONS-8 ${view}: turning Hourly Time Announcement off is saved (queue effect needs storage, see backend tests)`, async () => {
      const panel = page.getByTestId('station-panel');
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
    await step('F-BLOCKS-5 all-day block with one song: after the next song the queue holds only that song', async () => {
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
        // The next song stays where it is (the player may already be loading it, and a
        // redraw must never leave the queue without one); everything after it is the block's.
        if (titles.length >= 4 && titles.slice(1).every(t => t === song.title)) return `queue switched after ${Date.now() - t0} ms`;
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

// ── Sponsorship campaigns (admin screen) ────────────────────────────────────
section('campaigns', async () => {
  const name = `E2E Staff Campaign ${RUN}`;
  const admin = await newPage('admin', 'desktop');
  const form = admin.getByTestId('new-campaign-form');
  const row = p => p.getByTestId('campaign-row').filter({ hasText: name }).first();
  await step('F-CAMPAIGNS-1 admin: create a campaign for a sponsor (sponsor, name, dates, budget) saves fast', async () => {
    await admin.goto(`${BASE}/admin/campaigns`);
    await admin.getByRole('button', { name: 'New Campaign' }).click();
    await form.getByRole('button', { name: 'Create Campaign' }).click();
    assert(await field(form, 'Sponsor').evaluate(el => el.validationMessage), 'no message when no sponsor is chosen');
    await field(form, 'Sponsor').selectOption(FX.sponsor.id);
    await field(form, 'Campaign Name').fill(name);
    await field(form, 'Description').fill('Made by the admin flows test');
    await field(form, 'Start Date').fill('2027-04-01');
    await field(form, 'End Date').fill('2027-04-14');
    await field(form, 'Budget ($)').fill('250');
    const { ms, resp } = await timedSave(admin, 'create campaign', (m, p) => m === 'POST' && p === '/campaigns',
      () => form.getByRole('button', { name: 'Create Campaign' }).click());
    const body = await resp.json();
    assert(body.sponsor_id === FX.sponsor.id && body.budget_cents === 25000, `saved ${JSON.stringify(body).slice(0, 160)}`);
    FX.campaign = body;
    const t = await row(admin).innerText();
    assert(t.includes(FX.sponsor.name) && t.includes('2027-04-01') && t.includes('$250.00') && t.includes('draft'), `row "${t.replace(/\s+/g, ' ')}"`);
    return `saved in ${ms} ms`;
  }, admin);
  await step('F-CAMPAIGNS-1 admin: sponsor filter; open it, Save Status (approved), Submit Draft, Send comment', async () => {
    await admin.getByLabel('Filter by sponsor').selectOption(FX.sponsor.id);
    await row(admin).waitFor();
    await admin.getByLabel('Filter by status').selectOption('completed');
    await row(admin).waitFor({ state: 'detached' });
    await admin.getByLabel('Filter by status').selectOption('');
    await row(admin).click();
    await waitPath(admin, `/admin/campaigns/${FX.campaign.id}`);
    await admin.getByLabel('Campaign status').selectOption('approved');
    await timedSave(admin, 'status', (m, p) => m === 'PATCH' && p.endsWith('/status'), () => admin.getByRole('button', { name: 'Save Status' }).click());
    await admin.getByTestId('campaign-status').filter({ hasText: 'approved' }).waitFor();
    await admin.getByRole('button', { name: 'New Draft' }).click();
    await admin.getByPlaceholder('Write the sponsorship script here...').fill(`E2E staff script ${RUN}`);
    await timedSave(admin, 'draft', (m, p) => m === 'POST' && p.endsWith('/drafts'), () => admin.getByRole('button', { name: 'Submit Draft' }).click());
    await admin.getByText(`E2E staff script ${RUN}`).waitFor();
    await admin.getByPlaceholder('Add a comment...').fill(`E2E staff comment ${RUN}`);
    await timedSave(admin, 'comment', (m, p) => m === 'POST' && p.endsWith('/comments'), () => admin.getByRole('button', { name: 'Send' }).click());
    await admin.getByText(`E2E staff comment ${RUN}`).waitFor();
  }, admin);
  await closePage(admin);

  for (const view of ['desktop', 'iphone']) {
    const dj = await newPage('dj', view);
    await step(`F-CAMPAIGNS-1 dj ${view}: sees the campaign, its draft and comment, View only, no buttons that change anything`, async () => {
      await dj.goto(`${BASE}/admin/campaigns`);
      await row(dj).waitFor();
      await dj.getByTestId('view-only').waitFor();
      assert(!(await dj.getByRole('button', { name: 'New Campaign' }).count()), 'New Campaign offered to a dj');
      await row(dj).click();
      await waitPath(dj, `/admin/campaigns/${FX.campaign.id}`);
      await dj.getByText(`E2E staff script ${RUN}`).waitFor();
      await dj.getByText(`E2E staff comment ${RUN}`).waitFor();
      await dj.getByTestId('campaign-status').filter({ hasText: 'approved' }).waitFor();
      await dj.getByTestId('view-only').waitFor();
      for (const b of ['Save Status', 'New Draft', 'Send']) {
        assert(!(await dj.getByRole('button', { name: b }).count()), `${b} offered to a dj`);
      }
      const w = await dj.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
      assert(w[0] <= w[1] + 1, `page ${w[0]} px wide in a ${w[1]} px window`);
    }, dj);
    await closePage(dj);
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
    await step('F-SPOTSCHED-1 time slots: no slot chosen is refused; Early morning + Late evening with plays per slot saves', async () => {
      await page.goto(`${BASE}/admin/spot-schedule`);
      await page.getByRole('button', { name: 'Schedule a spot' }).click();
      const form = page.locator('form');
      await field(form, 'Sponsor').selectOption(FX.sponsor.id);
      await form.getByPlaceholder("Type part of the spot's title").fill(spot.title.slice(0, 12));
      await form.locator('li', { hasText: spot.title }).getByRole('button', { name: spot.title }).click();
      for (let i = 0; i < 12 && await form.locator('.grid button.bg-blue-600').count(); i++) await form.locator('.grid button.bg-blue-600').first().click();
      await form.getByRole('button', { name: 'Save schedule' }).click();
      await form.getByText(/Choose at least one (time of day|day)/).waitFor({ timeout: 2000 });
      await form.getByRole('button', { name: /^Early morning/ }).click();
      await form.getByRole('button', { name: /^Late evening/ }).click();
      await field(form, 'Plays in each slot').fill('3');
      const { resp } = await timedSave(page, 'save daypart schedule', (m, p) => m === 'POST' && p.startsWith('/spot-schedules'), () => form.getByRole('button', { name: 'Save schedule' }).click());
      const sent = resp.request().postDataJSON();
      assert(sent.dayparts === 'early_morning,late_evening' && sent.plays_per_daypart === 3, `sent ${JSON.stringify(sent).slice(0, 200)}`);
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

// ── Record spots ────────────────────────────────────────────────────────────
section('spot-recordings', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    await serveAudio(page);
    const title = `E2E Spot Rec ${view} ${RUN}`;
    const cardR = () => page.locator('li', { hasText: title }).first();
    await step(`F-SPOTREC-1 ${view}: Save is off until sponsor, name and script are filled; create checks the wording`, async () => {
      await page.goto(`${BASE}/admin/spot-recordings`);
      const save = page.getByRole('button', { name: 'Save and check wording' });
      assert(await save.isDisabled(), 'Save enabled on an empty form');
      await page.locator('select', { has: page.locator('option', { hasText: 'Choose a sponsor' }) }).selectOption(FX.sponsor.id);
      await page.getByPlaceholder('Name for this spot').fill(title);
      await page.getByPlaceholder("Kol B'ramah sponsor ...").fill('Kol Bramah is sponsored by E2E Company, call 555 0100 today.');
      const { ms } = await timedResponse(page, (m, p) => m === 'POST' && p.startsWith('/spot-recordings'), () => save.click());
      await cardR().waitFor();
      await cardR().getByText(/Wording (fits the rules|needs changes)/).waitFor();
      return `created and checked in ${ms} ms`;
    }, page);
    await step(`F-SPOTREC-2 ${view}: Make the audio gives placeholder tones (no Approve); ▶ Listen plays`, async () => {
      await timedResponse(page, (m, p) => m === 'POST' && p.includes('/spot-recordings/'), () => cardR().getByRole('button', { name: 'Make the audio' }).click());
      await cardR().getByText('Placeholder tones').waitFor();
      assert(!(await cardR().getByRole('button', { name: 'Approve for air' }).count()), 'placeholder audio can be approved');
      await cardR().getByRole('button', { name: '▶ Listen' }).click();
      await cardR().locator('audio').waitFor({ state: 'attached' });
    }, page);
    await step(`F-SPOTREC-2 ${view}: Edit script / Cancel keeps text; Save and re-check updates it`, async () => {
      await cardR().getByRole('button', { name: 'Edit script' }).click();
      await cardR().locator('textarea').fill('changed then cancelled');
      await cardR().getByRole('button', { name: 'Cancel' }).click();
      await cardR().getByText('call 555 0100 today').waitFor();
      await cardR().getByRole('button', { name: 'Edit script' }).click();
      await cardR().locator('textarea').fill("Kol B'ramah is brought to you by E2E Company.");
      const { ms } = await timedResponse(page, (m, p) => (m === 'PUT' || m === 'PATCH') && p.includes('/spot-recordings/'), () => cardR().getByRole('button', { name: 'Save and re-check wording' }).click());
      await cardR().getByText('brought to you by E2E Company').first().waitFor();
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-SPOTREC-2/3 ${view}: Upload a recording, then Approve for air puts it in the library`, async () => {
      const f = makeTone(path.join(TMP, `rec-${view}.mp3`), 15, 660);
      await timedResponse(page, (m, p) => m === 'POST' && p.includes('/spot-recordings/'), () => cardR().locator('input[type=file]').setInputFiles(f));
      const approve = cardR().getByRole('button', { name: 'Approve for air' });
      await approve.waitFor();
      await timedResponse(page, (m, p) => m === 'POST' && p.includes('approve'), () => approve.click());
      await cardR().getByRole('link', { name: 'Schedule this spot' }).waitFor();
      const lib = await api('GET', `/assets?search=${encodeURIComponent(title)}&limit=5`, { token: USERS.admin.token }).catch(() => null);
      return lib ? `library has ${(lib.assets || []).length} match(es)` : 'approved';
    }, page);
    await step(`F-SPOTREC-3 ${view}: Reject a second spot, then Remove both`, async () => {
      const t2 = `${title} B`;
      await page.locator('select', { has: page.locator('option', { hasText: 'Choose a sponsor' }) }).selectOption(FX.sponsor.id);
      await page.getByPlaceholder('Name for this spot').fill(t2);
      await page.getByPlaceholder("Kol B'ramah sponsor ...").fill('Kol Bramah thanks E2E Company.');
      await page.getByRole('button', { name: 'Save and check wording' }).click();
      const c2 = page.locator('li', { hasText: t2 }).first();
      await c2.waitFor();
      await timedResponse(page, (m, p) => m === 'POST' && p.includes('reject'), () => c2.getByRole('button', { name: 'Reject' }).click());
      await c2.getByRole('button', { name: 'Reject' }).waitFor({ state: 'detached' });
      for (const c of [c2, cardR()]) {
        await timedSave(page, 'remove spot', (m, p) => m === 'DELETE' && p.includes('/spot-recordings/'), () => c.getByRole('button', { name: 'Remove' }).click());
        await c.waitFor({ state: 'detached' });
      }
    }, page);
    await closePage(page);
  }
});

// ── Program blocks ──────────────────────────────────────────────────────────
section('program-blocks', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    const name = `E2E PB ${view} ${RUN}`;
    const form = page.locator('form');
    const items = () => page.getByTestId('block-items').locator('li');
    await step(`F-PROGBLOCKS-1 ${view}: Save with no name / no items says what is missing`, async () => {
      await page.goto(`${BASE}/admin/program-blocks`);
      await page.getByLabel('Station').selectOption(FX.takeover.id);
      await page.getByRole('button', { name: 'New block' }).click();
      await form.getByRole('button', { name: 'Save block' }).click();
      await form.getByText('Give the block a name.').waitFor({ timeout: 2000 });
      await form.getByPlaceholder('Weather Sponsors').fill(name);
      await form.getByRole('button', { name: 'Save block' }).click();
      await form.getByText(/Add at least one (time of day|item)|Choose at least one day/).waitFor({ timeout: 2000 });
    }, page);
    await step(`F-PROGBLOCKS-1 ${view}: timing soft-with-limit, days, every hour Set / Clear all, add and remove times`, async () => {
      await form.locator('#block-timing').selectOption('soft-limit');
      await form.getByLabel('Minutes to wait at most').fill('4');
      for (const d of ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']) {
        const b = form.getByRole('button', { name: d, exact: true });
        if (await b.getAttribute('aria-pressed') !== 'true') await b.click();
      }
      await form.getByLabel('Minute of every hour').selectOption('30');
      await form.getByRole('button', { name: 'Set', exact: true }).click();
      assert(await form.getByRole('button', { name: /^Remove / }).count() === 24, 'every hour did not give 24 times');
      await form.getByRole('button', { name: 'Clear all' }).click();
      assert(await form.getByRole('button', { name: /^Remove / }).count() === 0, 'Clear all left times');
      FX.pbTime = stationClock('UTC', 20);
      for (const t of [FX.pbTime, '05:05']) {
        await form.getByLabel('Time to add').fill(t);
        await form.getByRole('button', { name: 'Add time', exact: true }).click();
      }
      await form.getByRole('button', { name: /^Remove 5:05/ }).click();
      assert(await form.getByRole('button', { name: /^Remove / }).count() === 1, 'remove time failed');
    }, page);
    await step(`F-PROGBLOCKS-2 ${view}: items: library search, type that takes turns, sponsor places, time, weather; ↑/↓; remove`, async () => {
      await form.locator('#block-search').fill(FX.assets[0].title.slice(0, 10));
      await form.getByRole('button', { name: new RegExp(FX.assets[0].title) }).click();
      await form.locator('#block-combo').selectOption({ index: 1 });
      await form.locator('#block-combo').locator('xpath=..').getByRole('button', { name: 'Add' }).click();
      await form.getByLabel('Number of sponsor places').fill('2');
      await form.getByRole('button', { name: 'Add sponsor places' }).click();
      await form.getByRole('button', { name: 'Add the time' }).click();
      await form.getByRole('button', { name: 'Add the weather' }).click();
      assert(await items().count() === 5, `items ${await items().count()}`);
      await form.getByRole('button', { name: 'Move Time announcement up' }).click();
      assert((await items().nth(2).innerText()).includes('Time announcement'), 'move up failed');
      await form.getByRole('button', { name: 'Move Time announcement down' }).click();
      assert((await items().nth(3).innerText()).includes('Time announcement'), 'move down failed');
      await form.getByRole('button', { name: 'Remove Weather' }).click();
      await form.getByRole('button', { name: 'Remove 2 sponsor places' }).click();
      assert(await items().count() === 3, 'remove item failed');
    }, page);
    const cardP = () => page.getByTestId('block-card').filter({ hasText: name });
    await step(`F-PROGBLOCKS-3 ${view}: Save block saves fast; card shows times, every day, timing`, async () => {
      const { ms, resp } = await timedSave(page, 'save program block', (m, p) => m === 'POST' && p.startsWith('/program-blocks'), () => form.getByRole('button', { name: 'Save block' }).click());
      const sent = resp.request().postDataJSON();
      assert(sent.timing_mode === 'soft' && sent.latest_minutes === 4 && sent.items.length === 3, `sent ${JSON.stringify(sent).slice(0, 250)}`);
      await cardP().waitFor();
      assert((await cardP().innerText()).includes('every day'), 'card text');
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-PROGBLOCKS-4 ${view}: next-24-hours preview lists the block at its time`, async () => {
      const sec = page.locator('section', { has: page.getByRole('heading', { name: 'Next 24 hours' }) });
      await sec.getByText(name).first().waitFor({ timeout: 5000 });
      const [hh, mm] = FX.pbTime.split(':').map(Number);
      await sec.getByText(new RegExp(`(^|\\D)0?(${hh}|${hh % 12 || 12}):${String(mm).padStart(2, '0')}`)).first().waitFor();
    }, page);
    if (view === 'desktop') {
      await step('F-PROGBLOCKS-5 the block is queued on the station at its clock time (source block)', async () => {
        const [h, m] = FX.pbTime.split(':').map(Number);
        const deadline = Date.now() + 10000;
        let seen = [];
        while (Date.now() < deadline) {
          const q = await api('GET', `/stations/${FX.takeover.id}/queue?limit=200`, { token: USERS.admin.token });
          const hit = q.entries.filter(e => e.source === 'block');
          seen = hit.map(e => e.preempt_at || e.estimated_start);
          const at = hit.find(e => { const d = new Date(e.preempt_at || e.estimated_start); return d.getUTCHours() === h && d.getUTCMinutes() === m; });
          if (at) return `queued for ${at.preempt_at || at.estimated_start}`;
          await new Promise(r => setTimeout(r, 500));
        }
        throw new Error(`no block entry at ${FX.pbTime} UTC (block entries: ${seen.slice(0, 3).join(', ') || 'none'})`);
      }, page);
    }
    await step(`F-PROGBLOCKS-3 ${view}: Edit → Save changes; Turn off / on; Delete`, async () => {
      await cardP().getByRole('button', { name: 'Edit' }).click();
      await page.getByRole('heading', { name: 'Edit block' }).waitFor();
      await form.locator('#block-timing').selectOption('hard');
      await timedSave(page, 'save changes', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/program-blocks/'), () => form.getByRole('button', { name: 'Save changes' }).click());
      await timedSave(page, 'turn off', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/program-blocks/'), () => cardP().getByRole('button', { name: 'Turn off' }).click());
      await cardP().getByText('Off', { exact: true }).waitFor();
      await timedSave(page, 'turn on', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/program-blocks/'), () => cardP().getByRole('button', { name: 'Turn on' }).click());
      await cardP().getByRole('button', { name: 'Turn off' }).waitFor();
      await timedSave(page, 'delete', (m, p) => m === 'DELETE' && p.startsWith('/program-blocks/'), () => cardP().getByRole('button', { name: 'Delete' }).click());
      await cardP().waitFor({ state: 'detached' });
    }, page);
    await closePage(page);
  }
});

// ── Schedule check ──────────────────────────────────────────────────────────
section('schedule-check', async () => {
  const T = USERS.admin.token;
  const st = await api('POST', '/stations', { token: T, body: { name: `E2E Check St ${RUN}`, timezone: 'America/New_York' } });
  const sched = await api('POST', '/schedules', { token: T, body: { name: `E2E Check Sched ${RUN}`, station_id: st.id } });
  const mk = (n, h) => api('POST', '/schedules/blocks', { token: T, body: { schedule_id: sched.id, name: n, recurrence_type: 'daily', start_time: `${h}:00:00`, end_time: `${h + 2}:00:00`, priority: 1, playback_mode: 'shuffle' } });
  const pairs = { desktop: [await mk(`E2E Morning A ${RUN}`, 10), await mk(`E2E Morning B ${RUN}`, 10)] };
  for (const view of ['desktop', 'iphone']) {
    // desktop settles its overlap, so the phone gets one of its own later in the day
    if (!pairs[view]) pairs[view] = [await mk(`E2E Noon A ${RUN}`, 14), await mk(`E2E Noon B ${RUN}`, 14)];
    const [a, b] = pairs[view];
    const page = await newPage('manager', view);
    await step(`F-SCHEDCHECK-1 ${view}: station select shows the overlap under "Needs a look"`, async () => {
      await page.goto(`${BASE}/admin/schedule-check`);
      await page.locator('select', { has: page.locator(`option[value="${st.id}"]`) }).selectOption(st.id);
      await page.getByRole('heading', { name: 'Needs a look' }).waitFor({ timeout: 8000 });
      await page.getByRole('button', { name: new RegExp(`(Keep|Play) "${a.name}"`) }).first().waitFor();
    }, page);
    if (view === 'desktop') {
      await step('F-SCHEDCHECK-2 settle a conflict: choosing one block makes it win and the item leaves "Needs a look"', async () => {
        const btn = page.getByRole('button', { name: `Play "${b.name}" instead` }).first();
        const target = (await btn.count()) ? btn : page.getByRole('button', { name: `Play "${a.name}" instead` }).first();
        const { ms } = await timedSave(page, 'settle', (m, p) => m === 'POST' && p.includes('look-ahead/settle'), () => target.click());
        await page.getByText('Nothing needs you').waitFor({ timeout: 5000 });
        return `settled in ${ms} ms`;
      }, page);
    }
    await closePage(page);
  }
  await api('DELETE', `/schedules/${sched.id}`, { token: T }).catch(() => {});
  await api('DELETE', `/stations/${st.id}`, { token: T }).catch(() => {});
});

// ── Sponsorship rules and copy review ───────────────────────────────────────
section('sponsorship-rules', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    const ta = page.locator('textarea').first();
    await step(`F-SPRULES-1 ${view}: Save is off until changed; Undo changes restores; Save rules saves a new version fast`, async () => {
      await page.goto(`${BASE}/admin/sponsorship-rules`);
      const save = page.getByRole('button', { name: 'Save rules' });
      await save.waitFor();
      assert(await save.isDisabled(), 'Save enabled with no change');
      const original = await ta.inputValue();
      await ta.fill(original + `\nE2E extra rule ${view} ${RUN}.`);
      await page.getByRole('button', { name: 'Undo changes' }).click();
      assert(await ta.inputValue() === original, 'Undo did not restore');
      await ta.fill('Too short');
      assert(await save.isDisabled(), 'Save allowed for a 9-letter rule text');
      await ta.fill(original + `\nE2E extra rule ${view} ${RUN}.`);
      const { ms } = await timedSave(page, 'save rules', (m, p) => m !== 'GET' && p.startsWith('/sponsorship/rules'), () => save.click());
      await page.getByText('Saved.').waitFor();
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-SPRULES-2 ${view}: Show earlier versions, Load into editor, Hide`, async () => {
      await page.getByRole('button', { name: /Show earlier versions/ }).click();
      const first = page.getByRole('button', { name: 'Load into editor' }).last(); // oldest: differs from the text in force
      await first.waitFor();
      await first.click();
      await page.getByRole('button', { name: 'Undo changes' }).waitFor();
      await page.getByRole('button', { name: 'Undo changes' }).click();
      await page.getByRole('button', { name: /Hide earlier versions/ }).click();
      await page.getByRole('button', { name: 'Load into editor' }).first().waitFor({ state: 'detached' });
    }, page);
    await step(`F-SPRULES-3 ${view}: copy review panel: business name + wording, Check my wording, result shown`, async () => {
      await page.getByPlaceholder('Burger Bar').fill('E2E Bakery');
      const words = page.getByPlaceholder('Write it the way you would say it. We will show you what needs to change.');
      await words.fill('E2E Bakery has the best and cheapest bread in town, guaranteed! Call now!');
      const { ms } = await timedResponse(page, (m, p) => m === 'POST' && p.startsWith('/sponsorship'), () => page.getByRole('button', { name: 'Check my wording' }).click());
      await page.getByText(/This wording is fine\.|Some wording needs to change\./).waitFor();
      const use = page.getByRole('button', { name: 'Use this wording' });
      if (await use.count()) { await use.click(); assert((await words.inputValue()).length > 3, 'suggestion not put in the box'); }
      return `checked in ${ms} ms${(await use.count()) ? ', suggestion used' : ''}`;
    }, page);
    await closePage(page);
  }
});

// ── Playlists ───────────────────────────────────────────────────────────────
section('playlists', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    const name = `E2E Playlist ${view} ${RUN}`;
    const head = () => page.locator('div.cursor-pointer', { hasText: name }).first();
    await step(`F-PLAYLISTS-1 ${view}: create (name, station, pattern) saves fast; station filter`, async () => {
      await page.goto(`${BASE}/admin/playlists`);
      await page.getByRole('button', { name: 'New playlist' }).click();
      await page.getByRole('button', { name: 'Create playlist' }).click();
      assert(await page.getByPlaceholder('Morning Rotation').evaluate(el => el.validationMessage), 'no message on empty name');
      await page.getByPlaceholder('Morning Rotation').fill(name);
      await field(page, 'Station (optional)').selectOption(FX.station.id);
      await page.getByPlaceholder('Fast → New Hit → Fast → Relax').fill('Fast → Slow');
      const { ms } = await timedSave(page, 'create playlist', (m, p) => m === 'POST' && p === '/playlists', () => page.getByRole('button', { name: 'Create playlist' }).click());
      await head().waitFor();
      assert((await head().innerText()).includes(`Station: ${FX.station.name}`), 'station not shown');
      const filter = page.locator('select', { has: page.locator('option', { hasText: 'All Stations' }) }).first();
      await filter.selectOption(FX.takeover.id);
      await head().waitFor({ state: 'detached' });
      await filter.selectOption(FX.station.id);
      await head().waitFor();
      return `saved in ${ms} ms`;
    }, page);
    await step(`F-PLAYLISTS-2 ${view}: add 3 steps, move up/down, remove one (each saves fast)`, async () => {
      if (!(await page.getByRole('button', { name: /\+ Add step|Done/ }).count())) await head().click();

      for (const kind of ['music', 'jingle', 'spot']) {
        if (await page.getByRole('button', { name: /\+ Add step/ }).count()) await page.getByRole('button', { name: /\+ Add step/ }).first().click();
        const kindSel = page.locator('select', { has: page.locator('option[value="jingle"]') }).first();
        await kindSel.selectOption(kind);
        await timedSave(page, 'add step', (m, p) => m === 'POST' && p.startsWith('/playlists/slots'), () => page.getByRole('button', { name: 'Add', exact: true }).first().click());
      }
      const rows = page.locator('div.bg-white.p-2.rounded.border').filter({ has: page.getByRole('button', { name: 'Remove' }) });
      await rows.nth(2).waitFor();
      const order = async () => (await rows.allInnerTexts()).map(t => (t.match(/music|jingle|spot/) || [''])[0]).join(',');
      assert(await order() === 'music,jingle,spot', `order ${await order()}`);
      await timedSave(page, 'move up', (m, p) => m !== 'GET' && p.startsWith('/playlists'), () => rows.nth(2).getByRole('button', { name: 'Move up' }).click());
      await page.waitForFunction(async () => true);
      await page.waitForTimeout(150);
      assert(await order() === 'music,spot,jingle', `after move up ${await order()}`);
      await timedSave(page, 'move down', (m, p) => m !== 'GET' && p.startsWith('/playlists'), () => rows.nth(0).getByRole('button', { name: 'Move down' }).click());
      await page.waitForTimeout(150);
      assert(await order() === 'spot,music,jingle', `after move down ${await order()}`);
      await timedSave(page, 'remove step', (m, p) => m === 'DELETE' && p.startsWith('/playlists/slots'), () => rows.nth(2).getByRole('button', { name: 'Remove' }).click());
      await page.waitForTimeout(150);
      assert(await order() === 'spot,music', `after remove ${await order()}`);
    }, page);
    if (view === 'desktop') {
      await step('F-PLAYLISTS-3 generation panel: settings change enables Save and disables Regenerate; Save keeps them', async () => {
        const panel = page.locator('main');
        await page.getByRole('heading', { name: 'Generate the playlist ahead' }).waitFor();
        const on = panel.getByLabel('Generate from sequences');
        const was = await on.isChecked();
        if (!was) await on.check();
        const hours = panel.getByLabel('Generate for (hours)');
        await hours.fill('5');
        const save = panel.getByRole('button', { name: 'Save', exact: true });
        assert(!(await save.isDisabled()), 'Save off after a change');
        const regen = panel.getByRole('button', { name: /Regenerate now/ });
        if (await regen.count()) assert(await regen.isDisabled(), 'Regenerate allowed with unsaved changes');
        const { ms } = await timedSave(page, 'save generation', (m, p) => m === 'PUT' && p.startsWith('/playlists/generation'), () => save.click());
        await page.reload();
        await page.getByText('Choose a station above').waitFor(); // the station choice is not kept on reload
        await page.locator('select', { has: page.locator('option', { hasText: 'All Stations' }) }).first().selectOption(FX.station.id);
        await page.getByRole('heading', { name: 'Generate the playlist ahead' }).waitFor();
        await until(async () => await panel.getByLabel('Generate for (hours)').inputValue() === '5', 'hours kept as 5 after reload');
        const regen2 = panel.getByRole('button', { name: /Regenerate now/ });
        await regen2.waitFor();
        const r = await timedResponse(page, (m, p) => m === 'POST' && p.startsWith('/playlists/generation/regenerate'), () => regen2.click());
        assert(r.status < 400, `regenerate ${r.status}`);
        // put generation back as it was (it changes how the station queue fills)
        await api('PUT', '/playlists/generation', { token: USERS.admin.token, body: { station_id: FX.station.id, enabled: was } }).catch(() => {});
        return `saved in ${ms} ms, regenerate ${r.ms} ms`;
      }, page);
    }
    await step(`F-PLAYLISTS-2 ${view}: Delete template (confirm)`, async () => {
      await timedSave(page, 'delete playlist', (m, p) => m === 'DELETE' && p.startsWith('/playlists/'), () => head().getByRole('button', { name: 'Delete' }).click());
      await head().waitFor({ state: 'detached' });
    }, page);
    await closePage(page);
  }
});

// ── Categories and asset types ──────────────────────────────────────────────
section('categories', async () => {
  for (const [id, url, ph, api1] of [['F-CATS-1', '/admin/categories', 'New category name...', '/categories'], ['F-CATS-2', '/admin/asset-types', 'New asset type name...', '/asset-types']]) {
    for (const view of ['desktop', 'iphone']) {
      const page = await newPage('manager', view);
      const name = `e2e-${view}-${RUN}`;
      await step(`${id} ${view}: Add is off while empty; Add by button and by Enter save fast`, async () => {
        await page.goto(`${BASE}${url}`);
        const input = page.getByPlaceholder(ph);
        assert(await page.getByRole('button', { name: 'Add', exact: true }).isDisabled(), 'Add enabled while empty');
        await input.fill(name);
        const { ms } = await timedSave(page, 'add', (m, p) => m === 'POST' && p.startsWith(api1), () => page.getByRole('button', { name: 'Add', exact: true }).click());
        await rowOf(page, name).waitFor();
        await input.fill(`${name}-enter`);
        await timedSave(page, 'add by Enter', (m, p) => m === 'POST' && p.startsWith(api1), () => input.press('Enter'));
        await rowOf(page, `${name}-enter`).waitFor();
        return `saved in ${ms} ms`;
      }, page);
      await step(`${id} ${view}: Rename: Esc cancels, Enter saves, Save button saves; Cancel`, async () => {
        await rowOf(page, `${name}-enter`).getByRole('button', { name: 'Rename' }).click();
        const box = rowOf(page, `${name}-enter`).locator('input').or(page.locator('tbody input')).first();
        await box.fill(`${name}-esc`);
        await box.press('Escape');
        await rowOf(page, `${name}-enter`).getByRole('button', { name: 'Rename' }).waitFor();
        await rowOf(page, `${name}-enter`).getByRole('button', { name: 'Rename' }).click();
        await page.locator('tbody input').first().fill(`${name}-renamed`);
        await timedSave(page, 'rename', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith(api1), () => page.locator('tbody input').first().press('Enter'));
        await rowOf(page, `${name}-renamed`).waitFor();
        await page.locator('tr').filter({ has: page.getByText(name, { exact: true }) }).getByRole('button', { name: 'Rename' }).click();
        await page.locator('tbody input').first().fill(`${name}-btn`);
        await timedSave(page, 'rename by Save', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith(api1), () => page.getByRole('button', { name: 'Save', exact: true }).click());
        await rowOf(page, `${name}-btn`).waitFor();
        await rowOf(page, `${name}-btn`).getByRole('button', { name: 'Rename' }).click();
        await page.getByRole('button', { name: 'Cancel', exact: true }).click();
        await rowOf(page, `${name}-btn`).getByRole('button', { name: 'Rename' }).waitFor();
      }, page);
      await step(`${id} ${view}: Delete (confirm)`, async () => {
        for (const n of [`${name}-btn`, `${name}-renamed`]) {
          await timedSave(page, 'delete', (m, p) => m === 'DELETE' && p.startsWith(api1), () => rowOf(page, n).getByRole('button', { name: 'Delete' }).click());
          await rowOf(page, n).waitFor({ state: 'detached' });
        }
      }, page);
      await closePage(page);
    }
  }
});

// ── Alerts ──────────────────────────────────────────────────────────────────
function seedAlerts(n, tag) {
  if (!process.env.E2E_DATABASE_URL) throw new Error('needs E2E_DATABASE_URL (the API cannot make alerts)');
  execFileSync('uv', ['run', 'python', path.join(HERE, 'admin-flows-seed.py')], {
    cwd: path.join(HERE, '..', '..', 'backend'),
    env: { ...process.env, DATABASE_URL: process.env.E2E_DATABASE_URL, E2E_ALERTS: String(n), E2E_ALERT_TAG: tag },
    stdio: 'pipe',
  });
}
section('alerts', async () => {
  const tag = `${RUN}a`;
  seedAlerts(105, tag); // more than one page (100 a page)
  const sel = (page, label) => field(page, label);
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('admin', view);
    const row = i => page.locator('tr', { has: page.getByText(`E2E alert ${tag} ${i}`, { exact: true }) });
    if (view === 'desktop') {
      await step('F-ALERTS-1 desktop: severity / type / status filters narrow the list; pager Older / Newer', async () => {
        await page.goto(`${BASE}/admin/alerts`);
        await page.getByTestId('alerts-list').waitFor();
        const pager = page.getByTestId('alerts-pager');
        await until(async () => /Page 1 of [2-9]/.test(await pager.innerText()), 'two pages of alerts');
        await pager.getByRole('button', { name: 'Older' }).click();
        await until(async () => /Page 2 of/.test(await pager.innerText()), 'page 2');
        await pager.getByRole('button', { name: 'Newer' }).click();
        await until(async () => /Page 1 of/.test(await pager.innerText()), 'back to page 1');
        await sel(page, 'Severity').selectOption('warning');
        await until(async () => {
          const sev = await page.locator('[data-testid=alerts-list] tbody tr td:nth-child(2)').allInnerTexts();
          return sev.length && sev.every(s => s.trim() === 'WARNING');
        }, 'only WARNING rows');
        await sel(page, 'Severity').selectOption('');
        await sel(page, 'Type').selectOption('system');
        await until(async () => {
          const t = await page.locator('[data-testid=alerts-list] tbody tr td:nth-child(3)').allInnerTexts();
          return t.length && t.every(s => s.trim() === 'system');
        }, 'only system rows');
        await sel(page, 'Status').selectOption('resolved');
        await until(async () => {
          const t = await page.locator('[data-testid=alerts-list] tbody tr td:nth-child(7)').allInnerTexts().catch(() => []);
          return (await page.getByText('No alerts found').count()) || (t.length && t.every(s => s.trim() === 'Resolved'));
        }, 'only resolved rows');
        await sel(page, 'Status').selectOption('');
        await sel(page, 'Type').selectOption('');
      }, page);
    } else {
      await page.goto(`${BASE}/admin/alerts`);
      await page.getByTestId('alerts-list').waitFor().catch(() => {});
      await sel(page, 'Status').selectOption(''); // opens on Unresolved; a resolved row would leave the list
    }
    await step(`F-ALERTS-2 ${view}: Resolve, Reopen, Delete (confirm) each save fast`, async () => {
      // the seeded alerts share one time, so which are on page 1 varies: take the first one shown
      const i = await until(async () => {
        const titles = await page.locator('[data-testid=alerts-list] tbody tr td:nth-child(4)').allInnerTexts();
        const t = titles.map(x => x.trim()).find(x => x.startsWith(`E2E alert ${tag} `));
        return t && t.split(' ').pop();
      }, 'a seeded alert on page 1', 8000);
      await timedSave(page, 'resolve', (m, p) => m === 'PATCH' && p.endsWith('/resolve'), () => row(i).getByRole('button', { name: 'Resolve' }).click());
      await row(i).getByText('Resolved', { exact: true }).waitFor();
      await timedSave(page, 'reopen', (m, p) => m === 'PATCH' && p.endsWith('/reopen'), () => row(i).getByRole('button', { name: 'Reopen' }).click());
      await row(i).getByText('Open', { exact: true }).waitFor();
      await timedSave(page, 'delete', (m, p) => m === 'DELETE' && p.startsWith('/alerts/'), () => row(i).getByRole('button', { name: 'Delete' }).click());
      await row(i).waitFor({ state: 'detached' });
    }, page);
    await closePage(page);
  }
  const page = await newPage('manager');
  await step('F-ALERTS-2/3 Resolve all (confirm) resolves every open alert and the bell count goes away', async () => {
    await page.goto(`${BASE}/admin/alerts`);
    const badge = page.locator('nav span.bg-red-600');
    await badge.waitFor();
    assert(!(await page.getByRole('button', { name: 'Delete' }).count()), 'a manager is offered Delete (admin only)');
    const { ms } = await timedSave(page, 'resolve all', (m, p) => m === 'POST' && p === '/alerts/resolve-all', () => page.getByTestId('resolve-all').click());
    await page.getByTestId('resolve-all').waitFor({ state: 'detached' });
    // A station can raise a new alert at any moment; the badge must at least drop below the 105 just seeded.
    await until(async () => !(await badge.count()) || (await badge.innerText()).trim() !== '9+', 'bell count to drop', 5000);
    return `resolved in ${ms} ms`;
  }, page);
  await closePage(page);
});

// ── Bug reports page ────────────────────────────────────────────────────────
section('bug-reports', async () => {
  const text = `E2E report for the list ${RUN}`;
  await api('POST', '/bug-reports', { token: USERS.viewer.token, body: { description: text, expected: 'E2E expected', page_url: `${BASE}/stations` } });
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage(view === 'desktop' ? 'admin' : 'manager', view);
    await step(`F-BUGS-1 ${view}: list shows the report; Details / Hide details${view === 'desktop' ? '; Close sets it closed' : '; no Close for a manager'}`, async () => {
      await page.goto(`${BASE}/admin/bug-reports`);
      const item = page.locator('li', { hasText: text });
      await item.waitFor();
      assert((await item.innerText()).includes('Expected: E2E expected'), 'expected text not shown');
      await item.getByRole('button', { name: 'Details' }).click();
      await item.locator('pre').waitFor();
      await item.getByRole('button', { name: 'Hide details' }).click();
      await item.locator('pre').waitFor({ state: 'detached' });
      if (view === 'desktop') {
        const { ms } = await timedSave(page, 'close', (m, p) => m === 'PATCH' && p.startsWith('/bug-reports/'), () => item.getByRole('button', { name: 'Close' }).click());
        await item.getByRole('button', { name: 'Close' }).waitFor({ state: 'detached' });
        return `closed in ${ms} ms`;
      }
      assert(!(await item.getByRole('button', { name: 'Close' }).count()), 'manager offered Close (admin only)');
    }, page);
    await closePage(page);
  }
});

// ── Site updates ────────────────────────────────────────────────────────────
section('site-updates', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('admin', view);
    const listener = await newPage(null, view);
    await step(`F-SITEUPD-3 ${view}: open tabs list shows another open tab (a listener on /stations) with version and playing`, async () => {
      await listener.goto(`${BASE}/stations`);
      await page.goto(`${BASE}/admin/site-updates`);
      const table = page.getByTestId('tabs-table');
      await until(async () => (await table.locator('tr', { hasText: 'Listener' }).count()) > 0, 'the listener tab in the list', 8000);
      const r = table.locator('tr', { hasText: 'Listener' }).first();
      const cells = await r.locator('td').allInnerTexts();
      assert(cells[3] && cells[3].trim() !== '', 'no version shown');
      assert(/Yes|No/.test(cells[4]), 'no playing column');
      assert(Number(await page.getByTestId('tab-count').innerText()) >= 2, 'tab count below 2');
      await listener.close();
      await until(async () => !(await table.locator('tr', { hasText: 'Listener' }).count()), 'closed tab leaves the list', 10000);
    }, page);
    await step(`F-SITEUPD-1/2 ${view}: mode buttons save; push at a later time shows the push and Cancel removes it`, async () => {
      const was = (await api('GET', '/site-release', {})).mode;
      const other = was === 'manual' ? 'auto' : 'manual';
      const { ms } = await timedSave(page, 'mode', (m, p) => m === 'PUT' && p.startsWith('/site-release'), () => page.getByTestId(`mode-${other}`).click());
      await until(async () => (await page.getByTestId(`mode-${other}`).getAttribute('class')).includes('bg-blue-600'), 'mode shown as chosen');
      await timedSave(page, 'mode back', (m, p) => m === 'PUT' && p.startsWith('/site-release'), () => page.getByTestId(`mode-${was}`).click());
      await page.locator('input[type=datetime-local]').fill(localInput(new Date(Date.now() + 86400e3)));
      await page.getByTestId('push-release').waitFor();
      await until(async () => /at that time/.test(await page.getByTestId('push-release').innerText()), 'push button for a later time');
      await timedSave(page, 'push later', (m, p) => m === 'POST' && p.startsWith('/site-release'), () => page.getByTestId('push-release').click());
      await page.getByTestId('active-push').getByText('Scheduled', { exact: false }).waitFor();
      await timedSave(page, 'cancel push', (m, p) => m === 'DELETE' && p.startsWith('/site-release'), () => page.getByTestId('cancel-push').click());
      await page.getByTestId('active-push').waitFor({ state: 'detached' });
      return `saved in ${ms} ms`;
    }, page);
    await closePage(listener);
    await closePage(page);
  }
});

// ── Activity ────────────────────────────────────────────────────────────────
section('activity', async () => {
  const name = `E2E Activity Station ${RUN}`;
  await api('POST', '/stations', { token: USERS.manager.token, body: { name, timezone: 'UTC' } });
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('admin', view);
    await step(`F-ACTIVITY-1 / V1-141 ${view}: tab and view are remembered after a reload`, async () => {
      await page.goto(`${BASE}/admin/activity`);
      await page.getByRole('tab', { name: 'Listeners' }).click();
      await page.getByTestId('listener-list').or(page.getByText(/No listening|No listeners|nobody/i)).first().waitFor();
      await page.reload();
      await until(async () => (await page.getByRole('tab', { name: 'Listeners' }).getAttribute('aria-selected')) === 'true', 'Listeners tab kept');
      await page.getByRole('tab', { name: 'Who did what' }).click();
      await page.getByRole('tab', { name: 'Everything in order' }).click();
      await page.reload();
      await until(async () => (await page.getByRole('tab', { name: 'Everything in order' }).getAttribute('aria-selected')) === 'true', '"Everything in order" kept');
      assert(await page.evaluate(() => localStorage.getItem('activity-tab') === 'staff' && localStorage.getItem('activity-view') === 'all'), 'not stored under activity-tab / activity-view');
    }, page);
    await step(`F-ACTIVITY-2 ${view}: search finds the manager's change; expand shows the detail; quick ranges set the dates`, async () => {
      const search = page.getByPlaceholder('Song, action, person, IP…');
      await search.fill(name);
      const r = page.getByTestId('activity-row').filter({ hasText: name }).first();
      await r.waitFor({ timeout: 10000 });
      await r.getByRole('button').first().click();
      await until(async () => (await r.innerText()).split('\n').length > 2, 'detail shown');
      await search.fill('');
      await page.getByRole('button', { name: '7 days', exact: true }).click();
      const dates = await page.locator('input[type=date]').evaluateAll(els => els.map(e => e.value));
      assert(dates[0] && dates[1] && dates[0] < dates[1], `7 days set ${dates}`);
      await page.getByRole('button', { name: 'Today', exact: true }).click();
      const d2 = await page.locator('input[type=date]').evaluateAll(els => els.map(e => e.value));
      assert(d2[0] === d2[1], `Today set ${d2}`);
      await page.getByRole('tab', { name: 'By person' }).click();
      const person = page.getByTestId('person-row').filter({ hasText: USERS.manager.email.split('@')[0] }).first()
        .or(page.getByTestId('person-row').first());
      await person.first().waitFor();
      await person.first().getByRole('button').first().click();
      await page.getByTestId('activity-list').first().waitFor();
    }, page);
    await step(`F-ACTIVITY-3 ${view}: Listeners: search, station, minimum minutes, dates`, async () => {
      await page.getByRole('tab', { name: 'Listeners' }).click();
      await page.getByPlaceholder('City, state, country, IP, device…').fill('nowhere-e2e');
      await page.getByText(/No listen|No sessions|Nobody|nothing/i).first().waitFor();
      await page.getByPlaceholder('City, state, country, IP, device…').fill('');
      const selects = page.locator('main select');
      for (let i = 0; i < await selects.count(); i++) {
        const opts = await selects.nth(i).locator('option').evaluateAll(o => o.map(x => x.value));
        if (opts.length > 1) await selects.nth(i).selectOption(opts[1]);
      }
      await page.getByText('Could not load').waitFor({ state: 'detached', timeout: 2000 }).catch(() => {});
      assert(!(await page.getByText('Could not load the listeners').count()), 'listeners failed to load with filters');
    }, page);
    await closePage(page);
  }
});

// ── Analytics ───────────────────────────────────────────────────────────────
section('analytics', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    await step(`F-ANALYTICS-1 ${view}: station and days selects reload the figures`, async () => {
      await page.goto(`${BASE}/admin/analytics`);
      await page.getByRole('heading').first().waitFor();
      const selects = page.locator('main select');
      await until(async () => (await selects.count()) >= 1, 'selects');
      const st = selects.filter({ has: page.locator(`option[value="${FX.station.id}"]`) }).first();
      const { status } = await timedResponse(page, (m, p) => m === 'GET' && p.startsWith('/analytics'), () => st.selectOption(FX.station.id));
      assert(status < 400, `analytics ${status}`);
      const days = selects.filter({ hasNot: page.locator(`option[value="${FX.station.id}"]`) }).first();
      if (await days.count()) {
        const opts = await days.locator('option').evaluateAll(o => o.map(x => x.value));
        const r = await timedResponse(page, (m, p) => m === 'GET' && p.startsWith('/analytics'), () => days.selectOption(opts[opts.length - 1]));
        assert(r.status < 400, `analytics ${r.status}`);
      }
      assert(!(await page.getByText(/Something went wrong|Could not load/).count()), 'error shown');
    }, page);
    await closePage(page);
  }
});

// ── Song requests (admin) ───────────────────────────────────────────────────
section('requests', async () => {
  const mk = t => api('POST', '/song-requests', { body: { station_id: FX.station.id, requester_name: 'E2E Listener', song_title: t, song_artist: 'Nobody Known' } });
  for (const view of ['desktop', 'iphone']) {
    const titles = ['A', 'B', 'C'].map(x => `E2E Request ${x} ${view} ${RUN}`);
    for (const t of titles) await mk(t);
    const page = await newPage('manager', view);
    const item = t => (view === 'desktop' ? page.locator('tr', { hasText: t }) : page.locator('div.md\\:hidden > div', { hasText: t })).first();
    await step(`F-REQUESTS-${view === 'desktop' ? 1 : 2} ${view}: Approve, Reject, Delete (confirm) save fast; status tabs filter`, async () => {
      await page.goto(`${BASE}/admin/requests`);
      await page.getByRole('button', { name: 'Pending', exact: true }).click();
      await item(titles[0]).waitFor();
      await timedSave(page, 'approve', (m, p) => m !== 'GET' && p.startsWith('/song-requests/'), () => item(titles[0]).getByRole('button', { name: 'Approve' }).click());
      await item(titles[0]).waitFor({ state: 'detached' }); // leaves the Pending tab
      await timedSave(page, 'reject', (m, p) => m !== 'GET' && p.startsWith('/song-requests/'), () => item(titles[1]).getByRole('button', { name: 'Reject' }).click());
      await item(titles[1]).waitFor({ state: 'detached' });
      await page.getByRole('button', { name: 'Approved', exact: true }).click();
      await item(titles[0]).waitFor();
      assert(!(await item(titles[1]).count()), 'rejected request on the Approved tab');
      await page.getByRole('button', { name: 'Rejected', exact: true }).click();
      await item(titles[1]).waitFor();
      await page.getByRole('button', { name: 'All', exact: true }).click();
      const { ms } = await timedSave(page, 'delete', (m, p) => m === 'DELETE' && p.startsWith('/song-requests/'), () => item(titles[2]).getByRole('button', { name: 'Delete' }).click());
      await item(titles[2]).waitFor({ state: 'detached' });
      await page.getByRole('button', { name: 'Most Requested Songs' }).click();
      await page.getByText(/No matched requests yet|req/).first().waitFor();
      return `deleted in ${ms} ms`;
    }, page);
    await closePage(page);
  }
});

// ── Show archives (admin) ───────────────────────────────────────────────────
section('admin-archives', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    const title = `E2E Archive ${view} ${RUN}`;
    const r = () => page.locator('tr', { hasText: title }).first();
    const dlg = page.locator('div.fixed.inset-0');
    await step(`F-AARCH-1 ${view}: New Archive: Create off until title/audio/station; × and Cancel close; create; Edit saves`, async () => {
      await page.goto(`${BASE}/admin/archives`);
      await page.getByRole('button', { name: '+ New Archive' }).click();
      await dlg.getByRole('button', { name: 'Close' }).click();
      await dlg.waitFor({ state: 'detached' });
      await page.getByRole('button', { name: '+ New Archive' }).click();
      await dlg.getByRole('button', { name: 'Cancel' }).click();
      await dlg.waitFor({ state: 'detached' });
      await page.getByRole('button', { name: '+ New Archive' }).click();
      const create = dlg.getByRole('button', { name: 'Create' });
      assert(await create.isDisabled(), 'Create allowed while empty');
      await field(dlg, 'Station').selectOption(FX.station.id);
      await dlg.getByPlaceholder('Show title').fill(title);
      assert(await create.isDisabled(), 'Create allowed without audio URL');
      await dlg.getByPlaceholder('https://example.com/audio.mp3').fill('https://example.test/e2e.mp3');
      await dlg.getByPlaceholder('Episode description...').fill('E2E description');
      await dlg.getByPlaceholder('Host name').fill('E2E Host');
      await dlg.getByPlaceholder('3600').fill('1800');
      const { ms } = await timedSave(page, 'create archive', (m, p) => m === 'POST' && p.startsWith('/archives'), () => create.click());
      await r().waitFor();
      await r().getByRole('button', { name: 'Edit' }).click();
      await dlg.getByPlaceholder('Show title').fill(`${title} edited`);
      await timedSave(page, 'update archive', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/archives/'), () => dlg.getByRole('button', { name: 'Update' }).click());
      await page.locator('tr', { hasText: `${title} edited` }).waitFor();
      return `created in ${ms} ms`;
    }, page);
    await step(`F-AARCH-2 ${view}: Draft / Published toggle saves; station filter; Delete (confirm)`, async () => {
      const toggle = r().getByRole('button', { name: /^(Draft|Published)$/ });
      const before = (await toggle.innerText()).trim();
      await timedSave(page, 'publish toggle', (m, p) => m !== 'GET' && p.startsWith('/archives/'), () => toggle.click());
      await until(async () => (await r().getByRole('button', { name: /^(Draft|Published)$/ }).innerText()).trim() !== before, 'toggle changed');
      const filter = page.locator('select', { has: page.locator('option', { hasText: 'All Stations' }) }).first();
      await filter.selectOption(FX.takeover.id);
      await r().waitFor({ state: 'detached' });
      await filter.selectOption(FX.station.id);
      await r().waitFor();
      const { ms } = await timedSave(page, 'delete archive', (m, p) => m === 'DELETE' && p.startsWith('/archives/'), () => r().getByRole('button', { name: 'Delete' }).click());
      await r().waitFor({ state: 'detached' });
      return `deleted in ${ms} ms`;
    }, page);
    await closePage(page);
  }
});

// ── Live shows, host console, call screener ─────────────────────────────────
section('live', async () => {
  const T = USERS.admin.token;
  // Its own station: going live silences the station's music.
  const st = await api('POST', '/stations', { token: T, body: { name: `E2E Live St ${RUN}`, timezone: 'UTC' } });
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('dj', view);
    const title = `E2E Show ${view} ${RUN}`;
    const cardL = (t = title) => page.locator('div.bg-\\[\\#12123a\\].rounded-lg', { hasText: t }).first();
    const dlg = page.locator('div.fixed.inset-0');
    await step(`F-LIVE-1 ${view}: New show: Create off until title+station; create; tabs; Edit saves; Delete (confirm)`, async () => {
      await page.goto(`${BASE}/admin/live`);
      await page.getByRole('button', { name: '+ New Show' }).click();
      assert(await dlg.getByRole('button', { name: 'Create' }).isDisabled(), 'Create allowed while empty');
      await dlg.getByPlaceholder('Show title').fill(title);
      await dlg.getByPlaceholder('Optional description').fill('E2E');
      await field(dlg, 'Station').selectOption(st.id);
      const start = new Date(Date.now() + 2 * 3600e3), end = new Date(Date.now() + 3 * 3600e3);
      await field(dlg, 'Start Time').fill(localInput(start));
      await field(dlg, 'End Time').fill(localInput(end));
      await dlg.getByLabel('Enable call-ins').check();
      const { ms } = await timedSave(page, 'create show', (m, p) => m === 'POST' && p === '/live-shows', () => dlg.getByRole('button', { name: 'Create' }).click());
      await cardL().waitFor();
      assert((await cardL().innerText()).includes('Calls ON'), 'calls not shown as on');
      await page.getByRole('button', { name: 'live', exact: true }).click();
      await cardL().waitFor({ state: 'detached' });
      await page.getByRole('button', { name: 'scheduled', exact: true }).click();
      await cardL().waitFor();
      await cardL().getByRole('button', { name: 'Edit' }).click();
      await dlg.getByPlaceholder('Show title').fill(`${title} edited`);
      await timedSave(page, 'edit show', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/live-shows/'), () => dlg.getByRole('button', { name: 'Save' }).click());
      await cardL(`${title} edited`).waitFor();
      // Delete is admin only on the server: a DJ must not be offered it
      assert(!(await cardL(`${title} edited`).getByRole('button', { name: 'Delete' }).count()), 'DJ offered Delete (server refuses it)');
      const adm = await newPage('admin', view);
      try {
        await adm.goto(`${BASE}/admin/live`);
        const c = adm.locator('div.bg-\\[\\#12123a\\].rounded-lg', { hasText: `${title} edited` }).first();
        await timedSave(adm, 'delete show', (m, p) => m === 'DELETE' && p.startsWith('/live-shows/'), () => c.getByRole('button', { name: 'Delete' }).click());
        await c.waitFor({ state: 'detached' });
      } finally { await closePage(adm); }
      return `created in ${ms} ms`;
    }, page);
    await closePage(page);
  }

  // F-LIVE-2, F-HOST-2/4, F-SCREENER-2: one show taken live, a web caller screened.
  {
    const page = await newPage('dj');
    const title = `E2E Live Now ${RUN}`;
    const show = await api('POST', '/live-shows', { token: T, body: { station_id: st.id, title, scheduled_start: new Date(Date.now() - 60e3).toISOString(), scheduled_end: new Date(Date.now() + 2 * 3600e3).toISOString(), calls_enabled: true } });
    const cardL = page.locator('div.bg-\\[\\#12123a\\].rounded-lg', { hasText: title }).first();
    await step('F-LIVE-2 Go Live saves fast; Host Console and Screener links open', async () => {
      await page.goto(`${BASE}/admin/live`);
      const { ms } = await timedSave(page, 'go live', (m, p) => m === 'POST' && p.startsWith(`/live-shows/${show.id}`), () => cardL.getByRole('button', { name: 'Go Live' }).click());
      await cardL.getByRole('link', { name: 'Screener' }).waitFor();
      await cardL.getByRole('link', { name: 'Screener' }).click();
      await waitPath(page, `/admin/live/${show.id}/screen`);
      await page.goBack();
      await cardL.getByRole('link', { name: 'Host Console' }).click();
      await waitPath(page, `/admin/live/${show.id}/host`);
      return `live in ${ms} ms`;
    }, page);
    await step('F-HOST-2 countdown to the hard stop shows about 2 hours and ticks down', async () => {
      const clock = page.locator('div.font-mono').filter({ hasText: /^\d\d:\d\d:\d\d$/ }).first();
      await until(async () => /^01:5\d:\d\d$/.test((await clock.innerText()).trim()), 'countdown near 01:59', 8000);
      const a = (await clock.innerText()).trim();
      await page.waitForTimeout(2100);
      const b = (await clock.innerText()).trim();
      assert(a !== b && b < a, `countdown not ticking (${a} → ${b})`);
      return `${a} → ${b}`;
    }, page);
    const screener = await newPage('dj');
    const call = await api('POST', `/call-in/${st.id}`, { body: { name: `E2E Caller ${RUN}`, note: 'hi' } });
    await step('F-SCREENER-2 edit caller name (Esc cancels, Enter saves), Approve, Put On Air, End Call; Reject', async () => {
      await screener.goto(`${BASE}/admin/live/${show.id}/screen`);
      await screener.getByText(`E2E Caller ${RUN}`).waitFor();
      await screener.getByRole('button', { name: 'edit', exact: true }).first().click();
      const box = screener.locator('main input, input').first();
      await box.fill('E2E Esc name');
      await box.press('Escape');
      await screener.getByText(`E2E Caller ${RUN}`).waitFor();
      await screener.getByRole('button', { name: 'edit', exact: true }).first().click();
      await box.fill(`E2E Renamed ${RUN}`);
      await timedSave(screener, 'rename caller', (m, p) => m !== 'GET' && p.includes(call.call_id), () => box.press('Enter'));
      await screener.getByText(`E2E Renamed ${RUN}`).waitFor();
      await timedSave(screener, 'approve', (m, p) => m !== 'GET' && p.includes(call.call_id), () => screener.getByRole('button', { name: 'Approve' }).first().click());
      await screener.getByText('Approved (1)').waitFor();
      await timedSave(screener, 'on air', (m, p) => m !== 'GET' && p.includes(call.call_id), () => screener.getByRole('button', { name: 'Put On Air' }).click());
      await screener.getByRole('button', { name: 'End Call' }).waitFor();
      // the host console sees the caller on air
      await page.getByText(`E2E Renamed ${RUN}`).first().waitFor({ timeout: 5000 });
      await timedSave(screener, 'end call', (m, p) => m !== 'GET' && p.includes(call.call_id), () => screener.getByRole('button', { name: 'End Call' }).click());
      await screener.getByRole('button', { name: 'End Call' }).waitFor({ state: 'detached' });
      const c2 = await api('POST', `/call-in/${st.id}`, { body: { name: `E2E Caller Two ${RUN}` } });
      await screener.getByText(`E2E Caller Two ${RUN}`).waitFor();
      await timedSave(screener, 'reject', (m, p) => m !== 'GET' && p.includes(c2.call_id), () => screener.getByRole('button', { name: 'Reject' }).first().click());
      await screener.getByText(`E2E Caller Two ${RUN}`).waitFor({ state: 'detached' });
    }, screener);
    await closePage(screener);
    await step('F-HOST-4 End Show (confirm) goes back to Live shows and the station plays music again', async () => {
      const { ms } = await timedSave(page, 'end show', (m, p) => m === 'POST' && p.startsWith(`/live-shows/${show.id}`), () => page.getByRole('button', { name: 'End Show' }).click());
      await waitPath(page, '/admin/live');
      const s = await api('GET', `/live-shows/${show.id}`, { token: T });
      assert(s.status === 'ended', `show ${s.status}`);
      return `ended in ${ms} ms`;
    }, page);
    await closePage(page);
  }

  // F-HOST-5 hard stop: the scheduler ends a show at its end time; the host console says so.
  {
    const page = await newPage('dj');
    // Started by the DJ: a DJ's console only gets show events for a show they host.
    const show = await api('POST', '/live-shows', { token: USERS.dj.token, body: { station_id: st.id, title: `E2E Hard Stop ${RUN}`, scheduled_start: new Date(Date.now() - 60e3).toISOString(), scheduled_end: new Date(Date.now() + 45e3).toISOString() } });
    await api('POST', `/live-shows/${show.id}/start`, { token: USERS.dj.token });
    await step('F-HOST-5 hard stop ends the show at its end time (host console shows SHOW ENDED) and the queue plays on', async () => {
      await page.goto(`${BASE}/admin/live/${show.id}/host`);
      await page.getByText('SHOW ENDED').waitFor({ timeout: 90_000 });
      const late = Date.now() - new Date(show.scheduled_end).getTime();
      assert(late < 20_000, `ended ${Math.round(late / 1000)} s after its end time`);
      const s = await api('GET', `/live-shows/${show.id}`, { token: T });
      assert(s.status === 'ended', `show ${s.status}`);
      await until(async () => {
        const q = await api('GET', `/stations/${st.id}/queue?limit=5`, { token: T });
        return (q.entries || []).some(e => e.status === 'playing');
      }, 'queue playing after the hard stop', 40_000);
      await page.getByRole('button', { name: 'Back to Live Shows' }).click();
      await waitPath(page, '/admin/live');
      return `ended ${Math.round(late / 1000)} s after its end time`;
    }, page);
    await closePage(page);
  }
});

// ── Library: assets, upload, asset detail, enhance, reviews ─────────────────
// Real audio for players. With local storage the backend hands out the relative
// /api/v1/assets/:id/download (behind a login; production hands out a public storage URL).
// Send those requests on to the real backend with the page's token.
async function proxyAudio(page, role) {
  await page.route(`${BASE}/api/v1/**`, async r => {
    const url = API + r.request().url().slice(`${BASE}/api/v1`.length);
    // A page closed while its audio was loading cancels the request: not an error
    try {
      const resp = await r.fetch({ url, headers: { ...r.request().headers(), authorization: `Bearer ${USERS[role].token}` } });
      await r.fulfill({ response: resp });
    } catch { await r.abort().catch(() => {}); }
  });
}
function makeSilenceTone(file) {
  // 3 s silence, 10 s tone, 3 s silence: something for silence detection and auto-clip to find
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', "aevalsrc='if(between(t,3,13),0.5*sin(440*2*PI*t),0)':s=44100:d=16",
    '-ac', '2', '-b:a', '128k', file]);
  return file;
}
function makeGapTone(file) {
  // 3 s silence, 4 s tone, 2 s silence, 4 s tone, 3 s silence (16 s): a gap in the middle
  // as well as at both ends, for Trim Out on one gap and Remove All Found Silence
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i',
    "aevalsrc='if(between(t,3,7)+between(t,9,13),0.5*sin(440*2*PI*t),0)':s=44100:d=16", '-ac', '2', '-b:a', '128k', file]);
  return file;
}
async function uploadFixture(title, { type = 'music', file } = {}) {
  const f = file || makeSilenceTone(path.join(TMP, `${title.replace(/\W+/g, '_')}.mp3`));
  const form = new FormData();
  form.append('file', new Blob([fs.readFileSync(f)], { type: 'audio/mpeg' }), path.basename(f));
  form.append('title', title);
  form.append('asset_type', type);
  form.append('artist', `E2E Library Artist ${RUN}`);
  return api('POST', '/assets/upload', { token: USERS.admin.token, form });
}
// Badge menus open in a floating layer on the page (components/FloatingMenu.tsx), outside the row
const menu = page => page.locator('[data-floating-menu]');
const libRow = (page, title) => page.locator('tbody tr', { has: page.locator(`td[title="${title}"]`) }).first();

section('assets', async () => {
  const cat = await api('POST', '/categories', { token: USERS.admin.token, body: { name: `e2e-cat-${RUN}` } });
  const throwA = await uploadFixture(`E2E Lib A ${RUN}`);
  const throwB = await uploadFixture(`E2E Lib B ${RUN}`);
  const spot = FX.assets[2];
  const page = await newPage('manager');
  await proxyAudio(page, 'manager');
  const titleBox = page.getByPlaceholder('Search title...');
  const titles = async () => (await page.locator('tbody tr td[title]:not([title=""])').evaluateAll(tds => tds.filter(td => td.cellIndex === 2).map(td => td.getAttribute('title'))));
  await step('F-ASSETS-1 column filters: title, artist, type, duration min/max; Clear filters', async () => {
    await page.goto(`${BASE}/admin/assets`);
    await page.getByRole('heading', { name: 'Library' }).waitFor();
    const countText = page.getByText(/^\d+ assets?$/).first();
    await countText.waitFor();
    const all = await countText.innerText();
    await titleBox.fill(`E2E Lib A ${RUN}`);
    await until(async () => { const t = await titles(); return t.length === 1 && t[0] === `E2E Lib A ${RUN}`; }, 'title filter to one row');
    await page.getByRole('button', { name: 'Clear filters' }).click();
    assert(await titleBox.inputValue() === '', 'title filter not cleared');
    await page.getByPlaceholder('Search artist...').fill(`E2E Library Artist ${RUN}`);
    await until(async () => { const t = await titles(); return t.length >= 2 && t.every(x => x.startsWith('E2E Lib')); }, 'artist filter');
    await page.getByRole('button', { name: 'Clear filters' }).click();
    const typeSel = page.locator('thead select', { has: page.locator('option[value="spot"]') }).first();
    await typeSel.selectOption('spot');
    await until(async () => {
      const types = await page.locator('tbody tr td:nth-child(7)').allInnerTexts();
      return types.length && types.every(t => t.trim().startsWith('spot'));
    }, 'type filter: only spots');
    await page.getByRole('button', { name: 'Clear filters' }).click();
    await page.getByPlaceholder('e.g. 3:00').fill('2:55');
    await page.getByPlaceholder('e.g. 6:30').fill('3:10');
    await until(async () => {
      const d = (await page.locator('tbody tr td:nth-child(8)').allInnerTexts()).map(x => x.trim()).filter(x => x !== '--');
      return d.length && d.every(x => { const [m, s] = x.split(':').map(Number); const t = m * 60 + s; return t >= 175 && t <= 190; });
    }, 'duration filter 2:55–3:10');
    await page.getByRole('button', { name: 'Clear filters' }).click();
    await page.getByRole('button', { name: 'Clear filters' }).waitFor({ state: 'detached' });
    // Filters reach the server 300 ms after typing stops: wait for the whole library again
    await until(async () => (await countText.innerText()) === all, `count back to ${all}`);
  }, page);
  await step('F-ASSETS-2 Rows select asks for that many; pager', async () => {
    const rowsSel = page.locator('select', { has: page.locator('option[value="200"]') }).first();
    const r = await timedResponse(page, (m, p, resp) => m === 'GET' && p === '/assets' && resp.url().includes('limit=100'), () => rowsSel.selectOption('100'));
    assert(r.status === 200, `list ${r.status}`);
    await rowsSel.selectOption('50');
    const next = page.getByRole('button', { name: 'Next' });
    const summary = page.getByText(/^\d+–\d+ of \d+$/).first();
    await summary.waitFor();
    // wait for the 50-row page (the 100-row answer may still be on screen)
    await until(async () => { const m = (await summary.innerText()).match(/^\d+–(\d+) of (\d+)/); return m && Number(m[1]) <= 50; }, '50 rows a page');
    const total = Number((await summary.innerText()).match(/of (\d+)/)[1]);
    if (total > 50) {
      await next.click();
      await until(async () => /^51–/.test((await summary.innerText()).trim()), 'page 2');
      await page.getByRole('button', { name: 'Previous' }).click();
      await until(async () => /^1–/.test((await summary.innerText()).trim()), 'page 1');
      return `${total} items, paged`;
    }
    assert(!(await next.count()) || await next.isDisabled(), `Next enabled on the only page (${await summary.innerText()})`);
    return `${total} items (one page)`;
  }, page);
  await step('F-ASSETS-3 row Play plays the real file, Stop stops it', async () => {
    await titleBox.fill(`E2E Lib A ${RUN}`);
    const row = libRow(page, `E2E Lib A ${RUN}`);
    await row.waitFor();
    await row.getByRole('button', { name: 'Play', exact: true }).click();
    await row.getByRole('button', { name: 'Stop', exact: true }).waitFor({ timeout: 8000 });
    const playing = await page.evaluate(() => [...document.querySelectorAll('audio')].some(a => !a.paused && a.currentTime > 0));
    await page.waitForTimeout(500);
    assert(playing || await page.evaluate(() => [...document.querySelectorAll('audio')].some(a => !a.paused)), 'audio element not playing');
    await row.getByRole('button', { name: 'Stop', exact: true }).click();
    await row.getByRole('button', { name: 'Play', exact: true }).waitFor();
  }, page);
  await step('F-ASSETS-4 row Download menu: a format downloads the file', async () => {
    const row = libRow(page, `E2E Lib A ${RUN}`);
    await row.getByRole('button', { name: 'Download' }).click();
    const [dl] = await Promise.all([page.waitForEvent('download', { timeout: 15000 }), row.getByRole('button', { name: 'Original' }).click()]);
    assert(/E2E Lib A .*\.mp3$/.test(dl.suggestedFilename()), `file name ${dl.suggestedFilename()}`);
    const p = await dl.path();
    assert(fs.statSync(p).size > 10000, 'downloaded file is tiny');
    return dl.suggestedFilename();
  }, page);
  await step('F-ASSETS-6 category badge sets and clears a category; sponsor badge search sets and clears a sponsor', async () => {
    const row = libRow(page, `E2E Lib A ${RUN}`);
    await row.getByTitle(/category/i).click();
    await timedSave(page, 'set category', (m, p) => (m === 'PATCH' || m === 'PUT') && p.startsWith('/assets/'), () => menu(page).getByRole('button', { name: cat.name }).click());
    await until(async () => (await row.getByTitle(/category/i).innerText()).includes(cat.name), 'badge shows the category');
    await row.getByTitle(/category/i).click();
    await timedSave(page, 'clear category', (m, p) => (m === 'PATCH' || m === 'PUT') && p.startsWith('/assets/'), () => menu(page).getByRole('button', { name: 'Clear category' }).click());
    await until(async () => !(await row.getByTitle(/category/i).innerText()).includes(cat.name), 'category cleared');
    // Another section may have linked this spot to the sponsor already: start without one
    await api('PATCH', `/assets/${spot.id}`, { token: USERS.admin.token, body: { sponsor_id: null } }).catch(() => {});
    await titleBox.fill(spot.title);
    const srow = libRow(page, spot.title);
    await srow.waitFor();
    await srow.getByTitle(/Set sponsor|change sponsor/i).click();
    await page.getByPlaceholder('Search sponsors...').fill(FX.sponsor.name.slice(0, 12));
    await timedSave(page, 'set sponsor', (m, p) => (m === 'PATCH' || m === 'PUT') && p.startsWith('/assets/'), () => menu(page).getByRole('button', { name: FX.sponsor.name }).click());
    await srow.getByTitle(/change sponsor/i).waitFor();
    await srow.getByTitle(/change sponsor/i).click();
    await timedSave(page, 'clear sponsor', (m, p) => (m === 'PATCH' || m === 'PUT') && p.startsWith('/assets/'), () => menu(page).getByRole('button', { name: 'Clear sponsor' }).click());
    await srow.getByTitle('Set sponsor').waitFor();
  }, page);
  await step('F-ASSETS-7 row checkboxes, select all on the page, "Select all N", "Clear, keep only this page"', async () => {
    await titleBox.fill('');
    await page.locator('tbody tr').nth(2).waitFor();
    const rowsSel = page.locator('select', { has: page.locator('option[value="200"]') }).first();
    await rowsSel.selectOption('50');
    const head = page.locator('thead input[type=checkbox]');
    await head.check();
    await page.getByText(/\d+ selected/).waitFor();
    const selectAll = page.getByRole('button', { name: /Select all \d+ results/ });
    if (await selectAll.count()) {
      await selectAll.click();
      await page.getByText(/All \d+ results selected/).waitFor();
      await page.getByRole('button', { name: 'Clear, keep only this page' }).click();
      await page.getByText(/\d+ selected/).waitFor();
    }
    await head.uncheck();
    await page.getByText(/\d+ selected/).waitFor({ state: 'detached' });
    await titleBox.fill(`E2E Lib A ${RUN}`);
    await libRow(page, `E2E Lib A ${RUN}`).locator('input[type=checkbox]').check();
    await page.getByText('1 selected').waitFor();
    return (await selectAll.count()) ? 'all-filtered banner checked' : 'one page only (no "Select all N")';
  }, page);
  await step('F-ASSETS-8 bulk: Set Category, Download, Auto-Clip Silence (dialog, Close), Create Review Queue', async () => {
    await titleBox.fill(`E2E Lib `);
    await until(async () => (await titles()).filter(t => t.endsWith(RUN)).length >= 2, 'both library fixtures listed');
    for (const t of [`E2E Lib A ${RUN}`, `E2E Lib B ${RUN}`]) await libRow(page, t).locator('input[type=checkbox]').check();
    await page.getByText('2 selected').waitFor();
    await page.getByRole('button', { name: 'Set Category' }).click();
    await timedSave(page, 'bulk category', (m, p) => m !== 'GET' && p.startsWith('/assets'), () => page.getByRole('button', { name: cat.name, exact: true }).last().click());
    await until(async () => (await libRow(page, `E2E Lib B ${RUN}`).getByTitle(/category/i).innerText()).includes(cat.name), 'bulk category shown');
    for (const t of [`E2E Lib A ${RUN}`, `E2E Lib B ${RUN}`]) await libRow(page, t).locator('input[type=checkbox]').check();
    await page.getByRole('button', { name: 'Download', exact: true }).first().click();
    const downloads = [];
    page.on('download', d => downloads.push(d.suggestedFilename()));
    await page.getByRole('button', { name: 'MP3', exact: true }).first().click();
    await until(async () => downloads.length >= 2, 'two downloads', 20000);
    await page.getByRole('button', { name: 'Auto-Clip Silence' }).click();
    await page.getByText(/Complete!|Failed/).waitFor({ timeout: 30000 });
    assert(await page.getByText('Complete!').count(), 'auto-clip job failed');
    await page.getByRole('button', { name: 'Close' }).click();
    await page.getByText('Complete!').waitFor({ state: 'detached' });
    for (const t of [`E2E Lib A ${RUN}`, `E2E Lib B ${RUN}`]) { const c = libRow(page, t).locator('input[type=checkbox]'); if (!(await c.isChecked())) await c.check(); }
    await page.getByRole('button', { name: 'Create Review Queue' }).click();
    await page.waitForURL(/\/admin\/reviews\/[0-9a-f-]{36}$/);
    return `downloads: ${downloads.join(', ')}`;
  }, page);
  await step('F-ASSETS-5 View opens the item page; Del removes an item', async () => {
    await page.goto(`${BASE}/admin/assets`);
    await titleBox.fill(`E2E Lib B ${RUN}`);
    await libRow(page, `E2E Lib B ${RUN}`).getByRole('link', { name: 'View' }).click();
    await waitPath(page, `/admin/assets/${throwB.id}`);
    await page.goBack();
    await titleBox.fill(`E2E Lib B ${RUN}`);
    const { ms } = await timedSave(page, 'delete', (m, p) => m === 'DELETE' && p.startsWith('/assets/'), () => libRow(page, `E2E Lib B ${RUN}`).getByRole('button', { name: 'Del' }).click());
    await libRow(page, `E2E Lib B ${RUN}`).waitFor({ state: 'detached' });
    return `deleted in ${ms} ms (no confirm asked)`;
  }, page);
  await closePage(page);
  void throwA;
  for (const view of ['desktop', 'iphone']) {
    const dj = await newPage('dj', view);
    await step(`F-ASSETS-9 ${view}: a DJ gets no Upload link and no Del; a manager does`, async () => {
      await dj.goto(`${BASE}/admin/assets`);
      await dj.getByRole('heading', { name: 'Library' }).waitFor();
      await dj.locator('tbody tr').first().waitFor();
      assert(!(await dj.getByRole('link', { name: /Upload/ }).count()) && !(await dj.getByRole('button', { name: /Upload/ }).count()), 'DJ sees Upload');
      assert(!(await dj.getByRole('button', { name: 'Del' }).count()), 'DJ sees Del');
    }, dj);
    await closePage(dj);
  }
  {
    const m = await newPage('manager', 'iphone');
    await step('F-ASSETS-9 iphone: manager sees the Upload link', async () => {
      await m.goto(`${BASE}/admin/assets`);
      await m.getByRole('link', { name: /Upload/ }).or(m.getByRole('button', { name: /Upload/ })).first().waitFor();
    }, m);
    await closePage(m);
  }
});

section('upload', async () => {
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    await proxyAudio(page, 'manager');
    const a = makeTone(path.join(TMP, `up-a-${view}.mp3`), 20, 600);
    const b = makeTone(path.join(TMP, `up-b-${view}.mp3`), 20, 700);
    // S-10: a 5 MB file (about 5 min at 128 kbit/s)
    const big = makeTone(path.join(TMP, `up-big-${view}.mp3`), 330, 500, '128k');
    const input = page.locator('input[type=file]');
    await step(`F-UPLOAD-1/3 ${view}: pick files (list shows each), drop a file, Remove one, Clear All`, async () => {
      await page.goto(`${BASE}/admin/assets/upload`);
      await input.setInputFiles([a, b]);
      await page.getByText('Files (2)').waitFor();
      // drop a file on the drop zone
      const buf = fs.readFileSync(a).toString('base64');
      const dt = await page.evaluateHandle(async (b64) => {
        const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
        const d = new DataTransfer(); d.items.add(new File([bytes], 'dropped.mp3', { type: 'audio/mpeg' })); return d;
      }, buf);
      const zone = page.locator('[class*="border-dashed"]').first();
      await zone.dispatchEvent('dragover', { dataTransfer: dt });
      await zone.dispatchEvent('drop', { dataTransfer: dt });
      await page.getByText('Files (3)').waitFor();
      await page.getByTitle('Remove').last().click();
      await page.getByText('Files (2)').waitFor();
      await page.getByRole('button', { name: 'Clear All' }).click();
      await page.getByText(/Files \(/).waitFor({ state: 'detached' });
    }, page);
    await step(`F-UPLOAD-2/4 + S-10 ${view}: type, category, format, artist, album; Upload All shows progress at once; 5 MB file playable in the library within 10 s`, async () => {
      const title = `E2E Upload ${view} ${RUN}`;
      await input.setInputFiles([big]);
      await page.locator('input[value^="up-big"]').fill(title);
      await field(page, 'Artist').fill('E2E Upload Artist');
      await field(page, 'Album').fill('E2E Album');
      await field(page, 'Type').selectOption('music');
      const catOpts = await field(page, 'Category').locator('option').evaluateAll(o => o.map(x => x.value).filter(Boolean));
      if (catOpts.length) await field(page, 'Category').selectOption(catOpts[0]);
      await field(page, 'Convert To').selectOption({ index: 0 });
      const size = fs.statSync(big).size;
      const t0 = Date.now();
      const respP = page.waitForResponse(r => r.url().includes('/assets/upload') && r.request().method() === 'POST', { timeout: 60000 });
      await page.getByRole('button', { name: /Upload All/ }).click();
      await page.getByText(/Uploading 1 of 1/).waitFor({ timeout: LIMIT.progressShown });
      const progressMs = Date.now() - t0;
      const resp = await respP;
      assert(resp.status() < 300, `upload ${resp.status()} ${(await resp.text()).slice(0, 150)}`);
      const tEnd = Date.now();
      const created = await resp.json();
      await page.getByRole('link', { name: 'View Assets' }).waitFor();
      // playable: listed in the library and its audio loads and plays
      await page.getByRole('link', { name: 'View Assets' }).click();
      await waitPath(page, '/admin/assets');
      await page.getByPlaceholder('Search title...').fill(title);
      const row = libRow(page, title);
      await row.waitFor();
      await row.getByRole('button', { name: 'Play', exact: true }).click();
      await row.getByRole('button', { name: 'Stop', exact: true }).waitFor({ timeout: LIMIT.uploadPlayable });
      const playableMs = Date.now() - tEnd;
      assertFast(playableMs, LIMIT.uploadPlayable, 'upload end to playable');
      await row.getByRole('button', { name: 'Stop', exact: true }).click();
      const got = await api('GET', `/assets/${created.id}`, { token: USERS.admin.token });
      assert(got.artist === 'E2E Upload Artist' && got.album === 'E2E Album', `artist/album not saved: ${got.artist} / ${got.album}`);
      return `${(size / 1048576).toFixed(1)} MB, progress after ${progressMs} ms, playable ${playableMs} ms after upload end`;
    }, page);
    await closePage(page);
  }
});

section('asset-detail', async () => {
  const asset = await uploadFixture(`E2E Detail ${RUN}`, { file: makeGapTone(path.join(TMP, `detail_${RUN}.mp3`)) });
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    await proxyAudio(page, 'manager');
    const url = `${BASE}/admin/assets/${asset.id}`;
    await step(`F-ASSETDETAIL-1 ${view}: metadata shown; vote saves on blur; release date saves`, async () => {
      await page.goto(url);
      await page.getByText(`E2E Detail ${RUN}`).first().waitFor();
      const vote = page.locator('input[type=number]').first();
      await vote.fill(view === 'desktop' ? '77' : '150');
      await timedSave(page, 'vote', (m, p) => (m === 'PATCH' || m === 'PUT') && p === `/assets/${asset.id}`, () => vote.blur());
      const want = view === 'desktop' ? 77 : 100; // 150 is held to the 0-100 range
      await until(async () => (await api('GET', `/assets/${asset.id}`, { token: USERS.admin.token })).vote === want, `vote ${want} stored`);
      await timedSave(page, 'release date', (m, p) => (m === 'PATCH' || m === 'PUT') && p === `/assets/${asset.id}`, () => page.locator('input[type=date]').first().fill(view === 'desktop' ? '2024-05-01' : '2023-02-03'));
      const day = view === 'desktop' ? '2024-05-01' : '2023-02-03';
      await until(async () => (await api('GET', `/assets/${asset.id}`, { token: USERS.admin.token })).release_date?.startsWith(day), 'release date stored');
    }, page);
    await step(`F-ASSETDETAIL-2/3 ${view}: waveform loads; Play/Pause, Stop, speed, seek; Play first/last N s; Save as default`, async () => {
      await page.getByText('Loading waveform...').waitFor({ state: 'detached', timeout: 10000 });
      const time = page.getByText(/\d+:\d\d \/ \d+:\d\d/).first();
      await page.getByRole('button', { name: 'Play', exact: true }).first().click();
      await page.getByRole('button', { name: 'Pause', exact: true }).first().waitFor();
      await until(async () => !/^0:00 \//.test((await time.innerText()).trim()), 'play position moving', 5000);
      await page.getByRole('button', { name: 'Pause', exact: true }).first().click();
      await page.getByRole('button', { name: 'Stop', exact: true }).first().click();
      await until(async () => /^0:00 \//.test((await time.innerText()).trim()), 'Stop back to 0:00');
      const speed = page.getByRole('button', { name: /^1\.5x$|^2x$/ }).first();
      if (await speed.count()) await speed.click();
      // seek: click the middle of the waveform (there is no seek slider; Zoom is the slider)
      const wave = page.locator('xpath=//div[contains(@class,"gap-x-4")][.//button[normalize-space()="Stop"]]/preceding-sibling::div[1]').first();
      const box = await wave.boundingBox();
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
      await until(async () => !/^0:00 \//.test((await time.innerText()).trim()), 'click on the waveform moved the position');
      await page.locator('input[type=range]').first().fill('50'); // zoom
      await page.getByRole('button', { name: /Play First \d+s/ }).click();
      await page.getByRole('button', { name: /Play Last \d+s/ }).click();
      await page.getByRole('button', { name: 'Stop', exact: true }).first().click();
      await timedSave(page, 'save preview default', (m, p) => m !== 'GET', () => page.getByRole('button', { name: 'Save as Default' }).click());
    }, page);
    if (view === 'desktop') {
      await step('F-ASSETDETAIL-4 silence: Detect finds regions; Play / Deselect; Trim Out on a middle gap removes only that gap, then Undo Trim; Auto-Clip then Restore Original', async () => {
        const lengthNow = async () => (await api('GET', `/assets/${asset.id}`, { token: USERS.admin.token })).duration;
        await timedResponse(page, (m, p) => m === 'POST' && p.endsWith('/detect-silence'), () => page.getByRole('button', { name: 'Detect Silence' }).click());
        await page.getByText('3 silence region(s) found').waitFor({ timeout: 10000 });
        const region = page.locator('button', { hasText: /^#2/ }).first();
        await region.click();
        await page.getByRole('button', { name: 'Play Region' }).click();
        await page.getByRole('button', { name: 'Deselect' }).click();
        await page.getByRole('button', { name: 'Play Region' }).waitFor({ state: 'detached' });
        await region.click();
        await page.getByRole('button', { name: 'Trim Out' }).click();
        await page.getByText('Confirm Region Trim').waitFor();
        const r = await timedResponse(page, (m, p) => m === 'POST' && p.endsWith('/remove-silence'), () => page.getByRole('button', { name: 'Trim', exact: true }).click());
        assert(r.status < 300, `trim out ${r.status}`);
        await page.getByText('Trim applied — review the result').waitFor();
        const cut = await lengthNow();
        // The 2 s gap goes and nothing else (it used to cut off everything after the gap: 7 s left)
        assert(Math.abs(cut - 14) < 0.5, `Trim Out on the middle gap left ${cut} s of 16 s (expected about 14)`);
        await page.getByRole('button', { name: 'Undo Trim' }).click();
        await page.getByRole('button', { name: 'Undo Trim' }).waitFor({ state: 'detached', timeout: 10000 });
        await until(async () => (await lengthNow()) > 15, 'Undo Trim brought back the 16 s original');
        await page.getByRole('button', { name: 'Detect Silence' }).click(); // auto-clip is offered after a detection
        await page.getByRole('button', { name: 'Auto-Clip Start & End' }).click();
        await page.getByText('Confirm Auto-Clip').waitFor();
        await page.getByRole('button', { name: 'Trim', exact: true }).click();
        await page.getByRole('button', { name: 'Save', exact: true }).first().waitFor({ timeout: 15000 });
        await page.getByRole('button', { name: 'Save', exact: true }).first().click();
        await page.getByText('Trim saved.').waitFor();
        const after = await lengthNow();
        assert(after < 14, `auto-clip left ${after} s of a 16 s file with 6 s of silence at the ends`);
        await page.getByRole('button', { name: 'Restore Original' }).click();
        await until(async () => (await lengthNow()) > 15, 'original restored');
        return `trim out middle gap → ${cut.toFixed(1)} s; auto-clip → ${after.toFixed(1)} s, restored`;
      }, page);
      await step('F-ASSETDETAIL-4b Remove All Found Silence: count on the button, confirm says how much and that the original is kept; Cancel changes nothing; Remove All takes out all 3 gaps; Save; Restore Original', async () => {
        const lengthNow = async () => (await api('GET', `/assets/${asset.id}`, { token: USERS.admin.token })).duration;
        await page.getByText('Original restored!').waitFor({ timeout: 10000 }).catch(() => {});
        await timedResponse(page, (m, p) => m === 'POST' && p.endsWith('/detect-silence'), () => page.getByRole('button', { name: 'Detect Silence' }).click());
        const all = page.getByRole('button', { name: 'Remove All Found Silence (3)' });
        await all.waitFor({ timeout: 10000 });
        await all.click();
        const dialog = page.getByRole('dialog');
        await dialog.getByText('Remove all found silence?').waitFor();
        if (process.env.SHOT_DIR) {                                              // for a person to look at
          await page.screenshot({ path: `${process.env.SHOT_DIR}/remove-all-desktop.png` });
          const size = page.viewportSize();
          await page.setViewportSize({ width: 390, height: 844 });
          await page.screenshot({ path: `${process.env.SHOT_DIR}/remove-all-iphone.png` });
          await page.setViewportSize(size);
        }
        const words = await dialog.innerText();
        assert(/all 3 silent parts/.test(words) && /8\.0 s in total/.test(words) && /middle/.test(words) && /original/i.test(words),
          `the confirm does not say what goes and that the original is kept: ${words}`);
        await dialog.getByRole('button', { name: 'Cancel' }).click();
        await dialog.waitFor({ state: 'detached' });
        assert(Math.abs((await lengthNow()) - 16) < 0.5, 'Cancel changed the item');
        await all.click();
        await page.keyboard.press('Escape');                                   // Esc closes it too
        await page.getByRole('dialog').waitFor({ state: 'detached' });
        await all.click();
        const release = await holdEditAnswer(page, 'remove-silence');
        let r;
        try {
          [r] = await Promise.all([
            timedResponse(page, (m, p) => m === 'POST' && p.endsWith('/remove-silence'),
              () => page.getByRole('dialog').getByRole('button', { name: 'Remove All', exact: true }).click()),
            (async () => {
              await page.getByTestId('edit-progress').waitFor({ timeout: 5000 });
              assert(await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).isDisabled(), 'Cancel is offered while the silence is being removed');
              await page.keyboard.press('Escape');                              // Esc does not hide a running edit
              assert(await page.getByRole('dialog').count() === 1, 'Esc closed the box while the silence was being removed');
              await seeProgressBar(page, 'Remove All');
            })(),
          ]);
        } finally { await release(); }
        assert(r.status < 300, `remove all ${r.status}`);
        await page.getByTestId('edit-progress').waitFor({ state: 'detached' });
        await page.getByText('Trim applied — review the result').waitFor({ timeout: 15000 });
        const left = await lengthNow();
        assert(Math.abs(left - 8) < 0.5, `Remove All left ${left} s (expected the 8 s of tone)`);
        await page.getByRole('button', { name: 'Save', exact: true }).first().click();
        await page.getByText('Trim saved.').waitFor();
        await page.getByRole('button', { name: 'Detect Silence' }).click();     // nothing left to find
        await until(async () => (await page.getByRole('button', { name: /Remove All Found Silence/ }).count()) === 0
          && (await page.getByText(/silence region\(s\) found/).count()) === 0, 'a new scan finds no silence', 10000);
        await page.getByRole('button', { name: 'Restore Original' }).click();
        await until(async () => (await lengthNow()) > 15, 'Restore Original brought back the 16 s file');
        return `16 s → ${left.toFixed(1)} s, restored`;
      }, page);
      await step('F-ASSETDETAIL-5 enhance panel: preset, Preview, Apply, Undo Enhancement; full editor link', async () => {
        await page.getByRole('button', { name: 'Broadcast Polish' }).first().click();
        const pv = await timedResponse(page, (m, p) => m === 'POST' && p.endsWith('/enhance-preview'), () => page.getByRole('button', { name: /Preview/ }).first().click());
        assert(pv.status < 300, `preview ${pv.status}`);
        const ap = await timedResponse(page, (m, p) => m === 'POST' && p.endsWith('/enhance'), () => page.getByRole('button', { name: 'Apply', exact: true }).click());
        assert(ap.status < 300, `apply ${ap.status}`);
        await page.getByRole('button', { name: 'Undo Enhancement' }).click();
        await page.getByRole('button', { name: 'Undo Enhancement' }).waitFor({ state: 'detached', timeout: 15000 });
        await page.getByRole('link', { name: /Full Editor/ }).click();
        await waitPath(page, `/admin/audio-enhance/${asset.id}`);
        await page.goBack();
      }, page);
      await step('F-ASSETDETAIL-6 audience detection: Detect Questions runs and lists segments (or says none)', async () => {
        const r = await timedResponse(page, (m, p) => m === 'POST' && /audience|question/.test(p), () => page.getByRole('button', { name: 'Detect Questions' }).click());
        assert(r.status < 300, `detect ${r.status}`);
        await page.getByRole('button', { name: 'Detect Questions' }).waitFor();
      }, page);
    }
    await step(`F-ASSETDETAIL-7 ${view}: a comment posts and shows in the history`, async () => {
      const text = `E2E comment ${view} ${RUN}`;
      await page.getByPlaceholder('Write a comment...').fill(text);
      await timedSave(page, 'comment', (m, p) => m === 'POST', () => page.getByRole('button', { name: 'Post Comment' }).click());
      await page.getByText(text).first().waitFor();
    }, page);
    await step(`F-ASSETDETAIL-9 ${view}: title, artist and album can be changed here; an empty title is refused`, async () => {
      const patch = (m, p) => m === 'PATCH' && p === `/assets/${asset.id}`;
      const stored = async () => api('GET', `/assets/${asset.id}`, { token: USERS.admin.token });
      const title = page.getByTestId('asset-title');
      await title.fill(`E2E Detail ${RUN} renamed ${view}`);
      await timedSave(page, 'title', patch, () => title.press('Enter'));
      await until(async () => (await stored()).title === `E2E Detail ${RUN} renamed ${view}`, 'title stored');
      await page.getByRole('heading', { name: `E2E Detail ${RUN} renamed ${view}` }).waitFor();
      const artist = page.getByTestId('asset-artist');
      await artist.fill(`Artist ${view}`);
      await timedSave(page, 'artist', patch, () => artist.blur());
      const album = page.getByTestId('asset-album');
      await album.fill(`Album ${view}`);
      await timedSave(page, 'album', patch, () => album.blur());
      await until(async () => { const a = await stored(); return a.artist === `Artist ${view}` && a.album === `Album ${view}`; }, 'artist and album stored');
      await page.getByTestId('asset-title').fill('  ');
      await page.getByTestId('asset-title').blur();
      await page.getByTestId('asset-save-error').filter({ hasText: 'The title cannot be empty.' }).waitFor();
      assert((await stored()).title === `E2E Detail ${RUN} renamed ${view}`, 'an empty title was stored');
      // back to the name the other steps look for
      await page.getByTestId('asset-title').fill(`E2E Detail ${RUN}`);
      await timedSave(page, 'title back', patch, () => page.getByTestId('asset-title').press('Enter'));
      await until(async () => (await stored()).title === `E2E Detail ${RUN}`, 'title restored');
    }, page);
    await step(`F-ASSETDETAIL-8 ${view}: Back to Assets`, async () => {
      await page.getByRole('link', { name: /Back to Assets/ }).click();
      await waitPath(page, '/admin/assets');
    }, page);
    await closePage(page);
  }
  // A DJ or viewer reads the same page with nothing to change on it
  for (const role of ['dj', 'viewer']) {
    const page = await newPage(role);
    await proxyAudio(page, role);
    await step(`F-ASSETDETAIL-10 ${role}: the item page shows the facts as text, with no field or button that changes the item`, async () => {
      await page.goto(`${BASE}/admin/assets/${asset.id}`);
      await page.getByRole('heading', { name: `E2E Detail ${RUN}` }).waitFor();
      await page.getByText('Loading waveform...').waitFor({ state: 'detached', timeout: 10000 }).catch(() => {});
      const main = page.locator('main');
      // (the pre-listen lengths are the person's own setting and stay)
      assert(await main.locator('[data-testid=asset-title], [data-testid=asset-artist], [data-testid=asset-album], input[type=date], input[title*="vote"]').count() === 0, 'an editable field of the item is shown');
      for (const name of ['AI Auto-Enhance', 'Restore Original', 'Apply', 'Trim Out', 'Auto-Clip Start & End'])
        assert(await main.getByRole('button', { name, exact: true }).count() === 0, `"${name}" is offered`);
      await main.getByRole('button', { name: 'Detect Silence' }).click();
      await main.getByText(/silence region\(s\) found/).waitFor({ timeout: 10000 });
      for (const name of [/Remove All Found Silence/, /^Auto-Clip/, /^Trim Out$/])
        assert(await main.getByRole('button', { name, exact: true }).count() === 0, `"${name}" is offered`);
      assert(await main.getByRole('link', { name: /Full Editor/ }).count() === 0, '"Full Editor" is offered');
      await main.getByRole('button', { name: 'Detect Silence' }).waitFor();   // looking is still allowed
    }, page);
    await closePage(page);
  }
});

section('enhance', async () => {
  const asset = await uploadFixture(`E2E Enhance ${RUN}`);
  const page = await newPage('manager');
  await proxyAudio(page, 'manager');
  await step('F-ENHANCE-1 presets and Custom Filters: toggles show their sliders', async () => {
    await page.goto(`${BASE}/admin/audio-enhance/${asset.id}`);
    await page.getByRole('button', { name: 'Custom Filters' }).first().click();
    const sec = page.locator('div.border.rounded-lg.p-3', { hasText: 'High-Pass Filter' }).last();
    await sec.locator('label.relative').click();
    await sec.locator('input[type=range]').first().waitFor();
    await sec.locator('label.relative').click();
    await sec.locator('input[type=range]').waitFor({ state: 'detached' });
    await page.getByRole('button', { name: 'Warm Up' }).first().click();
    await page.getByText('Preset: Warm Up').waitFor();
  }, page);
  await step('F-ENHANCE-2 Play Original / preview enhanced', async () => {
    await page.getByRole('button', { name: 'Play Original' }).click();
    const pv = await timedResponse(page, (m, p) => m === 'POST' && p.endsWith('/enhance-preview'), () => page.getByRole('button', { name: 'Preview Enhanced 15s' }).click());
    assert(pv.status < 300, `preview ${pv.status}`);
  }, page);
  await step('F-ENHANCE-3 Apply Enhancement, Undo Enhancement, AI Auto-Enhance, Save; back link', async () => {
    const ap = await timedResponse(page, (m, p) => m === 'POST' && p.endsWith('/enhance'), () => page.getByRole('button', { name: 'Apply Enhancement' }).click());
    assert(ap.status < 300, `apply ${ap.status}`);
    await page.getByRole('button', { name: 'Undo Enhancement' }).click();
    await page.getByRole('button', { name: 'Undo Enhancement' }).waitFor({ state: 'detached', timeout: 15000 });
    const release = await holdEditAnswer(page, 'auto-enhance');
    let ai;
    try {
      [ai] = await Promise.all([
        timedResponse(page, (m, p) => m === 'POST' && /auto-enhance/.test(p), () => page.getByRole('button', { name: /AI Auto-Enhance/ }).click()),
        seeProgressBar(page, 'AI Auto-Enhance'),
      ]);
    } finally { await release(); }
    assert(ai.status < 300, `auto-enhance ${ai.status} ${(await ai.resp.text()).slice(0, 120)}`);
    await page.getByTestId('edit-progress').waitFor({ state: 'detached' });
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await page.getByText('Enhancement saved.').waitFor();
    await page.getByRole('link', { name: /Back to Asset/ }).click();
    await waitPath(page, `/admin/assets/${asset.id}`);
  }, page);
  await closePage(page);
});

section('reviews', async () => {
  const items = [];
  for (const x of ['1', '2', '3', '4']) items.push(await uploadFixture(`E2E Review Item ${x} ${RUN}`));
  const q = await api('POST', '/reviews/queues', { token: USERS.admin.token, body: { name: `E2E Flow ${RUN}`, asset_ids: items.map(a => a.id) } });
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('manager', view);
    await proxyAudio(page, 'manager');
    if (view === 'desktop') {
      await step('F-REVIEWS-1 queue list links to the flow', async () => {
        await page.goto(`${BASE}/admin/reviews`);
        await page.getByRole('link', { name: new RegExp(`E2E Flow ${RUN}`) }).click();
        await waitPath(page, `/admin/reviews/${q.id}`);
      }, page);
    } else {
      await page.goto(`${BASE}/admin/reviews/${q.id}`);
    }
    const status = async () => (await page.locator('span.rounded-full', { hasText: /^(pending|approved|rejected|flagged|skipped)$/ }).first().innerText()).trim();
    const counter = page.getByText(/Reviewing \d+ of \d+/);
    await step(`F-REVIEWS-3 ${view}: Next / Previous and the item list move between items`, async () => {
      await counter.waitFor();
      assert(await page.getByRole('button', { name: /Previous/ }).first().isDisabled(), 'Previous allowed on the first item');
      await page.getByRole('button', { name: /Next/ }).first().click();
      await page.getByText('Reviewing 2 of 4').waitFor();
      await page.getByRole('button', { name: /Previous/ }).first().click();
      await page.getByText('Reviewing 1 of 4').waitFor();
      await page.locator('button', { hasText: `E2E Review Item 3 ${RUN}` }).click();
      await page.getByText('Reviewing 3 of 4').waitFor();
      await page.locator('button', { hasText: `E2E Review Item 1 ${RUN}` }).click();
      await page.getByText('Reviewing 1 of 4').waitFor();
    }, page);
    if (view === 'desktop') {
      await step('V1-140 keyboard A/R/F/S do nothing while typing in the notes; work outside it', async () => {
        const notes = page.getByPlaceholder('Add notes about this asset...');
        await notes.click();
        await page.keyboard.type('a rafs');
        await page.waitForTimeout(400);
        assert(await counter.innerText() === 'Reviewing 1 of 4', `typing moved the flow: ${await counter.innerText()}`);
        assert(await notes.inputValue() === 'a rafs', 'keys not typed into the notes');
        await notes.fill('E2E note by keyboard');
        await page.locator('h1, h2').first().click();
        const { ms } = await timedSave(page, 'A key', (m, p) => m !== 'GET' && p.includes('/reviews'), () => page.keyboard.press('a'));
        await page.getByText('Reviewing 2 of 4').waitFor();
        await timedSave(page, 'S key', (m, p) => m !== 'GET' && p.includes('/reviews'), () => page.keyboard.press('s'));
        await page.getByText('Reviewing 3 of 4').waitFor();
        const it = await api('GET', `/reviews/queues/${q.id}/items`, { token: USERS.admin.token }).catch(() => null);
        const list = it?.items || it || [];
        const first = Array.isArray(list) ? list.find(i => i.asset_id === items[0].id) : null;
        if (first) assert(first.status === 'approved' && (first.notes || '').includes('E2E note by keyboard'), `item 1 ${first.status} notes "${first.notes}"`);
        return `A saved in ${ms} ms`;
      }, page);
    }
    await step(`F-REVIEWS-2 ${view}: Approve / Reject / Flag / Skip buttons with notes save and advance`, async () => {
      for (const [btn, st] of [['Reject (R)', 'rejected'], ['Flag (F)', 'flagged']]) {
        await page.locator('button', { hasText: `E2E Review Item ${btn.startsWith('R') ? 3 : 4} ${RUN}` }).click();
        await page.getByPlaceholder('Add notes about this asset...').fill(`E2E ${st} note`);
        const n = btn.startsWith('R') ? 3 : 4;
        await timedSave(page, btn, (m, p) => m !== 'GET' && p.includes('/reviews'), () => page.getByRole('button', { name: btn }).click());
        await until(async () => (await page.locator('button', { hasText: `E2E Review Item ${n} ${RUN}` }).innerText()).includes(st), `item ${n} listed as ${st}`);
        if (n === 3) await until(async () => !/Reviewing 3 of/.test(await counter.innerText()), 'Reject advances');
      }
      await page.locator('button', { hasText: `E2E Review Item 2 ${RUN}` }).click();
      await timedSave(page, 'Skip', (m, p) => m !== 'GET' && p.includes('/reviews'), () => page.getByRole('button', { name: 'Skip (S)' }).click());
      await page.locator('button', { hasText: `E2E Review Item 1 ${RUN}` }).click();
      await timedSave(page, 'Approve', (m, p) => m !== 'GET' && p.includes('/reviews'), () => page.getByRole('button', { name: 'Approve (A)' }).click());
      await page.locator('button', { hasText: `E2E Review Item 3 ${RUN}` }).click();
      assert(await status() === 'rejected', `item 3 shows ${await status()}`);
    }, page);
    await step(`F-REVIEWS-4 ${view}: change the tempo/category in the flow; waveform, silence, enhance, comments panels present`, async () => {
      const sel = page.locator('select', { has: page.locator('option', { hasText: 'Uncategorized' }) }).first();
      const opts = await sel.locator('option').evaluateAll(o => o.map(x => x.value).filter(Boolean));
      if (opts.length) {
        await timedSave(page, 'category in flow', (m, p) => (m === 'PATCH' || m === 'PUT') && p.startsWith('/assets/'), () => sel.selectOption(opts[0]));
      }
      for (const t of ['Detect Silence', 'Detect Questions', 'Post Comment']) await page.getByRole('button', { name: t }).first().waitFor();
      await page.getByText('Loading waveform...').waitFor({ state: 'detached', timeout: 10000 });
      return opts.length ? `set ${opts[0]}` : 'no categories to pick';
    }, page);
    await closePage(page);
  }
});

// ── Recording studio (fake microphone) ──────────────────────────────────────
section('studio', async () => {
  // A browser of its own with Chrome's fake microphone (a steady tone) and no permission prompt
  const fake = guardBrowser(await chromium.launch({ args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] }));
  try {
    for (const view of ['desktop', 'iphone']) {
      const ctx = await fake.newContext({ ...VIEWS[view], permissions: ['microphone'] });
      const u = USERS.manager; // a DJ may open the studio but not save (see F-STUDIO-1 dj below)
      await ctx.addInitScript(([a, r]) => { localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r); }, [u.token, u.refresh]);
      const page = await ctx.newPage();
      page.setDefaultTimeout(8000);
      page.on('dialog', d => d.accept().catch(() => {}));
      const rec = page.locator('section', { hasText: 'General Recorder' });
      const title = `E2E Studio ${view} ${RUN}`;
      await step(`F-STUDIO-1${view === 'iphone' ? '/4' : ''} ${view}: Record / Pause / Resume / Stop; Discard; record again and Save to Library (title, type, category)`, async () => {
        await page.goto(`${BASE}/admin/studio`);
        await rec.getByRole('button', { name: 'Start recording' }).click();
        await rec.getByRole('button', { name: 'Pause' }).click();
        await rec.getByText('(paused)').waitFor();
        await rec.getByRole('button', { name: 'Resume' }).click();
        await page.waitForTimeout(1500);
        await rec.getByRole('button', { name: 'Stop' }).click();
        await rec.getByRole('button', { name: 'Discard' }).click();
        await rec.getByRole('button', { name: 'Start recording' }).click();
        await page.waitForTimeout(1200);
        await rec.getByRole('button', { name: 'Cancel' }).click();
        await rec.getByRole('button', { name: 'Start recording' }).click();
        await page.waitForTimeout(2000);
        await rec.getByRole('button', { name: 'Stop' }).click();
        const save = rec.getByRole('button', { name: 'Save to Library' });
        assert(await save.isDisabled(), 'Save allowed with no title');
        await rec.getByPlaceholder('Title *').fill(title);
        await rec.locator('select').first().selectOption('jingle');
        await rec.getByPlaceholder('Category (optional)').fill('e2e');
        const r = await timedResponse(page, (m, p) => m === 'POST' && p.startsWith('/assets/upload'), () => save.click());
        assert(r.status < 300, `save ${r.status}`);
        const a = await r.resp.json();
        // V2-100 (fixed): the take is stored as MP3, not an uncompressed WAV
        assert(/\.mp3$/i.test(a.file_path || ''), `stored as ${a.file_path}`);
        assert(a.asset_type === 'jingle', `type ${a.asset_type}`);
        return `saved ${a.duration ? a.duration.toFixed(1) + ' s ' : ''}${a.file_path.split('.').pop()}`;
      }, page);
      if (view === 'desktop') {
        await step('V2-100 leaving the studio while recording asks first (the take lives only in this tab)', async () => {
          await rec.getByRole('button', { name: 'Start recording' }).click();
          await page.waitForTimeout(800);
          let asked = false;
          page.removeAllListeners('dialog');
          page.on('dialog', d => { if (d.type() === 'beforeunload') asked = true; d.dismiss().catch(() => {}); });
          await page.reload({ timeout: 3000 }).catch(() => {});
          await page.waitForTimeout(500);
          page.removeAllListeners('dialog');
          page.on('dialog', d => d.accept().catch(() => {}));
          assert(asked, 'no warning before the take is thrown away');
        }, page);
        await page.goto(`${BASE}/admin/studio`);
        await step('F-STUDIO-2 weather readouts: station + date, Generate Readout, Template Config toggle', async () => {
          const sec = page.locator('section', { hasText: 'Weather Readouts' });
          await sec.locator('select').first().selectOption(FX.station.id);
          await sec.getByRole('button', { name: 'Template Config' }).click();
          await sec.getByRole('button', { name: 'Hide Template' }).click();
          const r = await timedResponse(page, (m, p, r) => m === 'POST' && p.includes('readout') && r.status() !== 307, () => sec.getByRole('button', { name: 'Generate Readout' }).click());
          if (r.status >= 400) return `generate answered ${r.status} (the weather service cannot be reached from this test setup)`;
          await sec.getByRole('button', { name: 'Regenerate' }).first().waitFor();
          const n = await sec.getByRole('button', { name: 'Regenerate' }).count();
          await sec.getByRole('button', { name: /^Record$|^Re-record$/ }).first().click();
          await sec.getByRole('button', { name: 'Close Recorder' }).click();
          await timedResponse(page, (m, p) => m !== 'GET' && p.includes('readout'), () => sec.getByRole('button', { name: 'Regenerate' }).first().click());
          await timedSave(page, 'delete readout', (m, p) => m === 'DELETE', () => sec.getByRole('button', { name: 'Delete' }).first().click());
          await until(async () => (await sec.getByRole('button', { name: 'Regenerate' }).count()) === n - 1, 'readout removed');
          return 'generated, recorder opened and closed, regenerated, deleted';
        }, page);
        await step('F-STUDIO-3 audio mixer: pick bed and voice-over by search, Mix & Save to Library', async () => {
          const sec = page.locator('section', { hasText: 'Audio Mixer' });
          const searches = sec.getByPlaceholder('Search assets...');
          const lists = sec.locator('select[size]');
          await searches.nth(0).fill(FX.assets[0].title);
          await lists.nth(0).selectOption(FX.assets[0].id);
          await searches.nth(1).fill(FX.assets[3].title);
          await lists.nth(1).selectOption(FX.assets[3].id);
          await sec.getByRole('button', { name: /Show .* Waveform/ }).first().click();
          await sec.getByPlaceholder('Title *').fill(`E2E Mix ${RUN}`);
          const r = await timedResponse(page, (m, p) => m === 'POST' && /mix/.test(p), () => sec.getByRole('button', { name: 'Mix & Save to Library' }).click());
          assert(r.status < 300, `mix ${r.status} ${(await r.resp.text()).slice(0, 150)}`);
          return `mixed in ${r.ms} ms`;
        }, page);
      }
      await ctx.close();
    }
    {
      const ctx = await fake.newContext({ ...VIEWS.desktop, permissions: ['microphone'] });
      await ctx.addInitScript(([a, r]) => { localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r); }, [USERS.dj.token, USERS.dj.refresh]);
      const page = await ctx.newPage();
      page.setDefaultTimeout(8000);
      await step('F-STUDIO-1 dj: a DJ (who may open the studio) can save a recording to the library', async () => {
        await page.goto(`${BASE}/admin/studio`);
        const rec = page.locator('section', { hasText: 'General Recorder' });
        await rec.getByRole('button', { name: 'Start recording' }).click();
        await page.waitForTimeout(1200);
        await rec.getByRole('button', { name: 'Stop' }).click();
        await rec.getByPlaceholder('Title *').fill(`E2E DJ take ${RUN}`);
        const r = await timedResponse(page, (m, p) => m === 'POST' && p.startsWith('/assets/upload'), () => rec.getByRole('button', { name: 'Save to Library' }).click());
        assert(r.status < 300, `save refused ${r.status} ${(await r.resp.text()).slice(0, 100)}`);
      }, page);
      await ctx.close();
    }
  } finally { await fake.close(); }
});

// ── CRM ─────────────────────────────────────────────────────────────────────
section('crm', async () => {
  for (const view of ['desktop', 'iphone']) {
    const member = await api('POST', '/crm/register', { body: { name: `E2E Member ${view} ${RUN}` } });
    const page = await newPage('manager', view);
    await step(`F-CRM-1 ${view}: members tab: search finds the member; Deactivate (confirm)`, async () => {
      await page.goto(`${BASE}/admin/crm`);
      await page.getByPlaceholder('Search by name...').fill(`E2E Member ${view} ${RUN}`);
      const row = page.locator('tr', { hasText: `E2E Member ${view} ${RUN}` }).first();
      await row.waitFor();
      await timedSave(page, 'deactivate', (m, p) => m !== 'GET' && p.includes(member.id), () => row.getByRole('button', { name: 'Deactivate' }).click());
      await until(async () => !(await row.getByRole('button', { name: 'Deactivate' }).count()) || !(await row.count()), 'member deactivated');
    }, page);
    await step(`F-CRM-2 ${view}: song rankings tab loads`, async () => {
      await page.getByRole('button', { name: 'Song Rankings' }).click();
      await page.waitForTimeout(300);
      assert(!(await page.getByText(/Something went wrong|Could not load|Failed/).count()), 'error shown');
    }, page);
    await step(`F-CRM-3 ${view}: create a raffle (title, dates), Entries, Draw Winner (confirm)`, async () => {
      await page.getByRole('button', { name: 'Raffles' }).click();
      const t = `E2E Raffle ${view} ${RUN}`;
      await field(page, 'Title *').fill(t);
      await field(page, 'Prize').fill('A mug');
      await field(page, 'Start *').fill(localInput(new Date(Date.now() - 3600e3)));
      await field(page, 'End *').fill(localInput(new Date(Date.now() + 86400e3)));
      await timedSave(page, 'create raffle', (m, p) => m === 'POST' && p.includes('raffle'), () => page.getByRole('button', { name: 'Create Raffle' }).click());
      const r = page.locator('div', { has: page.getByText(t, { exact: true }) }).filter({ has: page.getByRole('button', { name: 'Draw Winner' }) }).last();
      await r.waitFor();
      await r.getByRole('button', { name: 'Entries' }).click();
      await r.getByRole('button', { name: 'Hide' }).click();
      const d = await timedResponse(page, (m, p) => m === 'POST' && p.includes('draw'), () => r.getByRole('button', { name: 'Draw Winner' }).click());
      return `draw answered ${d.status} (no entries)`;
    }, page);
    await closePage(page);
  }
});

// ── Program clock ───────────────────────────────────────────────────────────
section('program-clock', async () => {
  // Rules are admin-only on the server (as on the Rules screen), so rule changes run as admin
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('admin', view);
    const name = `E2E Clock Rule ${view} ${RUN}`;
    await step(`F-CLOCK-1 ${view}: station select, day buttons, 24 hours listed; clicking an hour opens the panel`, async () => {
      await page.goto(`${BASE}/admin/program-clock`);
      await page.locator('select', { has: page.locator(`option[value="${FX.station.id}"]`) }).first().selectOption(FX.station.id);
      await page.getByRole('button', { name: 'Tue', exact: true }).click();
      await until(async () => (await page.getByText(/No rule — default fill|· p\d+/).count()) >= 24, '24 hour rows');
      await page.getByText(/No rule — default fill|· p\d+/).nth(9).click();
      await page.getByRole('heading', { name: /New Rule|Edit Rule/ }).waitFor();
    }, page);
    await step(`F-CLOCK-3 ${view}: New Rule (name, type, asset type, hours, days, priority) creates; Edit updates; Delete (confirm); Cancel`, async () => {
      await page.getByRole('button', { name: '+ New Rule' }).click();
      await field(page, 'Name').fill(name);
      await field(page, 'Hour Start').fill('3');
      await field(page, 'Hour End').fill('4');
      const { ms } = await timedSave(page, 'create rule', (m, p) => m === 'POST' && p.startsWith('/rules'), () => page.getByRole('button', { name: 'Create Rule' }).click());
      await page.getByText(name).first().waitFor();
      await page.getByText(name).first().click();
      await field(page, 'Priority').fill('7');
      await timedSave(page, 'update rule', (m, p) => (m === 'PUT' || m === 'PATCH') && p.startsWith('/rules/'), () => page.getByRole('button', { name: 'Update Rule' }).click());
      await page.getByText(name).first().click();
      await timedSave(page, 'delete rule', (m, p) => m === 'DELETE' && p.startsWith('/rules/'), () => page.getByRole('button', { name: 'Delete' }).click());
      await page.getByText(name).first().waitFor({ state: 'detached' });
      await page.getByRole('button', { name: '+ New Rule' }).click();
      await page.getByRole('button', { name: 'Cancel' }).click();
      return `created in ${ms} ms`;
    }, page);
    if (view === 'desktop') {
      await step('F-CLOCK-2 ⚙ Automation: an ad minute toggles and is saved to the station', async () => {
        await page.getByRole('button', { name: '⚙ Automation' }).click();
        const m = page.getByRole('button', { name: ':45', exact: true });
        const on = (await m.getAttribute('class')).includes('bg-orange-700');
        await timedSave(page, 'ad minute', (mm, p) => mm !== 'GET' && p.startsWith('/stations'), () => m.click());
        await until(async () => (await m.getAttribute('class')).includes('bg-orange-700') !== on, 'minute toggled');
        await timedSave(page, 'ad minute back', (mm, p) => mm !== 'GET' && p.startsWith('/stations'), () => m.click());
      }, page);
    }
    await closePage(page);
  }
  for (const role of ['manager', 'dj', 'viewer']) {
    const page = await newPage(role);
    await step(`F-CLOCK-4 ${role}: ${role === 'manager' ? 'no New Rule (rules are admin-only); the clock still shows' : 'program clock is view only (no New Rule)'}`, async () => {
      await page.goto(`${BASE}/admin/program-clock`);
      if (role === 'manager') await page.getByRole('button', { name: 'Tue', exact: true }).waitFor();
      else await page.getByTestId('view-only').waitFor();
      assert(!(await page.getByRole('button', { name: '+ New Rule' }).count()) || await page.getByRole('button', { name: '+ New Rule' }).isDisabled(), 'New Rule offered');
    }, page);
    await closePage(page);
  }
});

// ── Public pages ────────────────────────────────────────────────────────────
section('public', async () => {
  const T = USERS.admin.token;
  // A station in Jerusalem with blocks around "now" there, for the guide's clock checks.
  const jz = 'Asia/Jerusalem';
  const gst = await api('POST', '/stations', { token: T, body: { name: `E2E Guide St ${RUN}`, timezone: jz, latitude: 31.77, longitude: 35.21 } });
  const gsc = await api('POST', '/schedules', { token: T, body: { name: `E2E Guide ${RUN}`, station_id: gst.id } });
  const hm = d => new Intl.DateTimeFormat('en-GB', { timeZone: jz, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(d);
  const now = new Date();
  const s = hm(new Date(now.getTime() - 30 * 60e3)), e = hm(new Date(now.getTime() + 30 * 60e3));
  const block = (name, start, end, extra = {}) => api('POST', '/schedules/blocks', { token: T, body: { schedule_id: gsc.id, name, recurrence_type: 'daily', start_time: `${start}:00`, end_time: `${end}:00`, priority: 1, playback_mode: 'shuffle', ...extra } });
  // The block on now outranks the sunset one, and the sunset one ends before midnight: it used to
  // end at 13:00, so it ran from sunset through the night to 1 PM and covered the "On Now" block
  // (the guide was right to show it; the test data was wrong).
  await block(`E2E On Now ${RUN}`, s, e, { priority: 9 });
  await block(`E2E Sunset Show ${RUN}`, '12:00', '23:59', { start_sun_event: 'sunset', start_sun_offset: 0 });
  await block(`E2E Overlap A ${RUN}`, '05:00', '06:00', { priority: 5 });
  await block(`E2E Overlap B ${RUN}`, '05:00', '06:00', { priority: 1 });
  // A public host with no display name (V2-127)
  const hostEmail = `e2e-secret-login-${RUN}@example.test`;
  const host = await api('POST', '/users', { token: T, body: { email: hostEmail, password: `pw-${RUN}-${crypto.randomBytes(4).toString('hex')}`, role: 'dj' } });
  await api('PUT', `/users/${host.id}`, { token: T, body: { is_public: true, bio: 'E2E bio' } }).catch(() => api('PATCH', `/users/${host.id}`, { token: T, body: { is_public: true, bio: 'E2E bio' } }));
  // A published and a draft archive
  const pub = await api('POST', '/archives', { token: T, body: { station_id: FX.station.id, title: `E2E Public Archive ${RUN}`, audio_url: `${API}/assets/${FX.assets[0].id}/download`, is_published: true } });
  const draft = await api('POST', '/archives', { token: T, body: { station_id: FX.station.id, title: `E2E Draft Archive ${RUN}`, audio_url: 'https://example.test/x.mp3' } });
  await api('PATCH', `/archives/${draft.id}`, { token: T, body: { is_published: false } });

  for (const view of ['desktop', 'iphone']) {
    const page = await newPage(null, view);
    await step(`F-STLIST-1 ${view}: active stations listed; a card opens /listen/:id`, async () => {
      await page.goto(`${BASE}/stations`);
      const cardL = page.getByRole('link', { name: new RegExp(FX.station.name) });
      await cardL.waitFor();
      await cardL.click();
      await waitPath(page, `/listen/${FX.station.id}`);
    }, page);
    await step(`F-STLIST-2 ${view}: loading then list (no error), empty state text exists for no stations`, async () => {
      await page.route(`${API}/stations**`, r => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ stations: [], total: 0 }) }));
      await page.goto(`${BASE}/stations`);
      await page.getByText(/No stations|no active stations|No live stations/i).first().waitFor();
      await page.unroute(`${API}/stations**`);
    }, page);
    await step(`F-GUIDE-1/2 + V2-104 ${view}: station + date load the EPG; "today" and "Now" follow the station's clock (Jerusalem) not the viewer's`, async () => {
      await page.goto(`${BASE}/guide`);
      const sel = page.locator('select', { has: page.locator(`option[value="${gst.id}"]`) }).first();
      const r = await timedResponse(page, (m, p) => m === 'GET' && p === `/schedules/epg/${gst.id}`, () => sel.selectOption(gst.id));
      const want = new Intl.DateTimeFormat('en-CA', { timeZone: jz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
      await until(async () => (await page.locator('input[type=date]').inputValue()) === want, `date ${want} (station's today)`);
      const card = page.locator('div.rounded-lg', { hasText: `E2E On Now ${RUN}` }).last();
      await card.waitFor();
      await until(async () => (await card.innerText()).includes('Now'), '"Now" on the block running in Jerusalem');
      // a different day: no "Now"
      await page.locator('input[type=date]').fill('2031-01-01');
      await page.getByText(`E2E On Now ${RUN}`).waitFor();
      assert(!(await page.getByText('Now', { exact: true }).count()), '"Now" shown on another day');
      void r;
    }, page);
    await step(`V2-104 ${view}: EPG with no date answers the station's date, not the server's`, async () => {
      const d = await api('GET', `/schedules/epg/${gst.id}`);
      const want = new Intl.DateTimeFormat('en-CA', { timeZone: jz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
      assert(d.date === want, `EPG default date ${d.date}, station date ${want}`);
    }, page);
    await step(`V2-105 ${view}: a sunset block shows today's sunset time, not its stored placeholder`, async () => {
      await page.locator('input[type=date]').fill(new Intl.DateTimeFormat('en-CA', { timeZone: jz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()));
      const c = page.locator('div.rounded-lg', { hasText: `E2E Sunset Show ${RUN}` }).last();
      await c.waitFor();
      assert(!(await c.innerText()).includes('12:00 PM'), 'shows the stored 12:00 PM placeholder');
    }, page);
    await step(`V2-105 ${view}: of two overlapping blocks only the one that airs (higher priority) is listed`, async () => {
      await page.getByText(`E2E Overlap A ${RUN}`).waitFor();
      assert(!(await page.getByText(`E2E Overlap B ${RUN}`).count()), 'the block that loses is listed too');
    }, page);
    await step(`F-GUIDE-3 ${view}: no sideways scroll`, async () => {
      const w = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
      assert(w[0] <= w[1] + 1, `page ${w[0]} px wide in a ${w[1]} px window`);
    }, page);
    await step(`F-HOSTS-1 + V2-127 ${view}: public host card with bio; a host without a display name does not show their login name`, async () => {
      await page.goto(`${BASE}/hosts`);
      const c = page.locator('div', { hasText: 'E2E bio' }).last();
      await c.waitFor();
      assert(!(await page.getByText(`e2e-secret-login-${RUN}`).count()), 'login name (e-mail before @) shown publicly');
    }, page);
    await step(`F-PARCH-1/2 ${view}: only published archives; station filter; play overlay plays the audio`, async () => {
      await page.route(/\/assets\/[^/]+\/download/, async r => {
        try {
          const resp = await r.fetch({ headers: { ...r.request().headers(), authorization: `Bearer ${USERS.admin.token}` } });
          await r.fulfill({ response: resp });
        } catch { await r.abort().catch(() => {}); }
      });
      // The page plays through `new Audio()` (never in the document), so watch play() itself
      await page.addInitScript(() => {
        const play = HTMLMediaElement.prototype.play;
        HTMLMediaElement.prototype.play = function (...args) {
          const p = play.apply(this, args);
          p.then(() => { window.__e2eAudio = true; }, e => { window.__e2eAudioErr = String(e); });
          return p;
        };
      });
      await page.goto(`${BASE}/archives`);
      const cardA = page.locator('div', { has: page.getByRole('heading', { name: `E2E Public Archive ${RUN}` }) }).last();
      await cardA.waitFor();
      assert(!(await page.getByText(`E2E Draft Archive ${RUN}`).count()), 'a draft is shown to the public');
      const filter = page.locator('select', { has: page.locator(`option[value="${FX.station.id}"]`) }).first();
      // Any other station: the archive belongs to FX.station only
      const other = await filter.locator('option').evaluateAll((os, keep) => os.map(o => o.value).find(v => v && v !== keep), FX.station.id);
      assert(other, 'only one station in the filter');
      await filter.selectOption(other);
      await page.getByRole('heading', { name: `E2E Public Archive ${RUN}` }).waitFor({ state: 'detached' });
      await filter.selectOption(FX.station.id);
      await page.getByRole('heading', { name: `E2E Public Archive ${RUN}` }).waitFor();
      await page.getByRole('button', { name: `Play E2E Public Archive ${RUN}` }).click({ force: true });
      await until(async () => page.evaluate(() => { if (window.__e2eAudioErr) throw new Error(window.__e2eAudioErr); return !!window.__e2eAudio; }), 'archive audio playing', 8000);
      await page.getByRole('button', { name: `Pause E2E Public Archive ${RUN}` }).waitFor();
    }, page);
    await closePage(page);
  }
  void pub;
});

// ── Sponsor portal ──────────────────────────────────────────────────────────
section('sponsor', async () => {
  const T = USERS.admin.token;
  await api('PATCH', `/assets/${FX.assets[2].id}`, { token: T, body: { sponsor_id: FX.sponsor.id } }).catch(() => {});
  const inv = await api('POST', '/billing/invoices', { token: T, body: { sponsor_id: FX.sponsor.id, amount_cents: 12345, description: `E2E Invoice ${RUN}` } });
  for (const view of ['desktop', 'iphone']) {
    // F-SPLOGIN-1: real sign-in through the form
    const ctx = await browser.newContext(VIEWS[view]);
    const page = await ctx.newPage();
    page.setDefaultTimeout(8000);
    page.on('dialog', d => d.accept().catch(() => {}));
    await step(`F-SPLOGIN-1 ${view}: sign in lands on /sponsor/dashboard; "Signing in..." while waiting`, async () => {
      await page.goto(`${BASE}/sponsor/login`);
      await page.locator('input[type=email], input[type=text]').first().fill(USERS.sponsor.email);
      await page.locator('input[type=password]').fill(USERS.sponsor.password);
      await loginSlot();
      await page.route(`${API}/auth/login`, async r => { await new Promise(x => setTimeout(x, 400)); await r.continue(); });
      await page.getByRole('button', { name: 'Sign In' }).click();
      await page.getByText('Signing in...').waitFor({ timeout: 1000 });
      await waitPath(page, '/sponsor/dashboard', 8000);
      await page.unroute(`${API}/auth/login`);
    }, page);
    await step(`F-SPLAYOUT-1 ${view}: nav Dashboard / Campaigns / Check Wording / Billing; ${view === 'iphone' ? 'F-SPLAYOUT-2 no sideways scroll' : 'all reachable'}`, async () => {
      for (const [label, p] of [['Campaigns', '/sponsor/campaigns'], ['Check Wording', '/sponsor/wording'], ['Billing', '/sponsor/billing'], ['Dashboard', '/sponsor/dashboard']]) {
        await page.locator('nav').getByRole('link', { name: label }).click();
        await waitPath(page, p);
        const w = await page.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
        assert(w[0] <= w[1] + 1, `${p}: page ${w[0]} px wide in a ${w[1]} px window`);
      }
    }, page);
    await step(`F-SPDASH-1 ${view}: stats, my spots (the linked spot), play history, upcoming`, async () => {
      await page.getByRole('heading', { name: 'My Spots' }).waitFor();
      await page.getByText(FX.assets[2].title).first().waitFor();
      await page.getByRole('heading', { name: 'Play History' }).waitFor();
      await page.getByText('Next Scheduled').waitFor();
    }, page);
    await step(`F-SPCAMP-1/2 ${view}: New Campaign → Create Campaign; detail: Submit Draft (versioned), comment Send`, async () => {
      const name = `E2E Campaign ${view} ${RUN}`;
      await page.locator('nav').getByRole('link', { name: 'Campaigns' }).click();
      await page.getByRole('button', { name: 'New Campaign' }).click();
      await field(page, 'Campaign Name').fill(name);
      await field(page, 'Description').fill('E2E');
      const { ms } = await timedSave(page, 'create campaign', (m, p) => m === 'POST' && p.startsWith('/campaigns'), () => page.getByRole('button', { name: 'Create Campaign' }).click());
      await page.getByRole('link', { name: new RegExp(name) }).click();
      await page.waitForURL(/\/sponsor\/campaigns\/[0-9a-f-]{36}$/);
      for (const v of [1, 2]) {
        await page.getByRole('button', { name: 'New Draft' }).click();
        await page.getByPlaceholder('Write the sponsorship script here...').fill(`E2E script v${v}`);
        await page.getByPlaceholder('Any notes for the team...').fill('note');
        await timedSave(page, 'draft', (m, p) => m === 'POST' && p.includes('/drafts'), () => page.getByRole('button', { name: 'Submit Draft' }).click());
        await page.getByText(`E2E script v${v}`).waitFor();
      }
      await page.getByText(/v2|Version 2|#2/).first().waitFor();
      await page.getByPlaceholder('Add a comment...').fill(`E2E comment ${RUN}`);
      await timedSave(page, 'comment', (m, p) => m === 'POST' && p.includes('/comments'), () => page.getByRole('button', { name: 'Send' }).click());
      await page.getByText(`E2E comment ${RUN}`).waitFor();
      return `created in ${ms} ms`;
    }, page);
    await step(`F-SPBILL-1 ${view}: invoice listed with summary; Pay Now asks for a checkout; ?payment=success / cancelled messages`, async () => {
      await page.locator('nav').getByRole('link', { name: 'Billing' }).click();
      const row = page.locator('tr', { hasText: `E2E Invoice ${RUN}` });
      await row.waitFor();
      assert((await row.innerText()).includes('123.45'), 'amount not shown');
      const r = await timedResponse(page, (m, p) => m === 'POST' && p.includes(`/invoices/${inv.id}/checkout`), () => row.getByRole('button', { name: 'Pay Now' }).click());
      await page.goto(`${BASE}/sponsor/billing?payment=success`);
      await page.getByText('Payment successful!').waitFor();
      await page.goto(`${BASE}/sponsor/billing?payment=cancelled`);
      await page.getByText('Payment was cancelled').waitFor();
      return `checkout answered ${r.status}${r.status >= 400 ? ' (Stripe is off in this test setup)' : ''}`;
    }, page);
    await step(`F-SPWORD-1 ${view}: wording check shows what must change and why; Use this wording`, async () => {
      await page.goto(`${BASE}/sponsor/wording`);
      await page.getByPlaceholder('Burger Bar').fill('E2E Bakery');
      const box = page.getByPlaceholder('Write it the way you would say it. We will show you what needs to change.');
      await box.fill('E2E Bakery: the best and cheapest bread in town, guaranteed! Call now for 50% off!');
      const r = await timedResponse(page, (m, p) => m === 'POST' && p === '/sponsorship/copy-review', () => page.getByRole('button', { name: 'Check my wording' }).click());
      assert(r.status < 300, `review ${r.status}`);
      await page.getByText(/Some wording needs to change\.|This wording is fine\./).waitFor();
      const use = page.getByRole('button', { name: 'Use this wording' });
      if (await use.count()) { await use.click(); assert(!(await box.inputValue()).includes('cheapest'), 'suggestion not put in'); }
    }, page);
    await step(`F-GLOBAL-5 ${view}: sponsor Logout returns to /sponsor/login and clears the tokens`, async () => {
      await page.locator('nav').getByRole('button', { name: 'Logout' }).click();
      await waitPath(page, '/sponsor/login');
      assert(!(await page.evaluate(() => localStorage.getItem('access_token'))), 'token kept');
    }, page);
    await ctx.close();
  }
});

// ── Release pickup on screens (F-GLOBAL-15/21/22), with a fake clock ────────
section('release', async () => {
  const current = (await (await fetch(`${BASE}/version.json?t=${Date.now()}`)).json()).version;
  const site = await api('GET', '/site-release');
  if (site.mode !== 'auto') await api('PUT', '/site-release', { token: USERS.admin.token, body: { mode: 'auto' } });
  async function releasePage(path) {
    const ctx = await browser.newContext(VIEWS.desktop);
    const page = await ctx.newPage();
    page.setDefaultTimeout(8000);
    let next = false;
    await page.route(/\/version\.json/, r => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ version: next ? `e2e-next-${RUN}` : current }) }));
    await page.clock.install();
    let loads = 0;
    page.on('load', () => { loads++; });
    await page.goto(`${BASE}${path}`);
    await page.waitForLoadState('networkidle').catch(() => {});
    return { page, ctx, publish: () => { next = true; }, loads: () => loads };
  }
  {
    const { page, ctx, publish, loads } = await releasePage('/stations');
    await step('F-GLOBAL-15 auto mode: an idle tab with no audio reloads onto the new release', async () => {
      const before = loads();
      publish();
      await page.clock.runFor(70_000); // the minute check sees the new id
      await page.clock.runFor(35_000); // 30 s with no input
      await until(async () => loads() > before, 'reload', 10000);
    }, page);
    await ctx.close();
  }
  {
    const { page, ctx, publish, loads } = await releasePage('/admin/login');
    await step('F-GLOBAL-22 a tab never reloads while the person is typing (field focused)', async () => {
      const before = loads();
      await page.locator('input').first().click();
      await page.keyboard.type('someone');
      publish();
      await page.clock.runFor(70_000);
      await page.clock.runFor(200_000);
      await page.waitForTimeout(1000);
      assert(loads() === before, 'reloaded while typing');
      assert(await page.locator('input').first().inputValue() === 'someone', 'typed text lost');
    }, page);
    await ctx.close();
  }
  {
    const { page, ctx, publish, loads } = await releasePage('/stations');
    await step('F-GLOBAL-21 a waiting release is picked up when the person moves to another screen', async () => {
      const before = loads();
      publish();
      // keep the tab "busy" (input every few seconds) so the idle rule cannot fire first
      // (a key press counts as input; a bare mouse move does not)
      for (let i = 0; i < 14; i++) { await page.keyboard.press('Shift'); await page.clock.runFor(5_000); }
      assert(loads() === before, 'reloaded while in use');
      await page.getByRole('link', { name: /Schedule$/ }).first().click();
      await until(async () => loads() > before, 'reload on navigation', 10000);
    }, page);
    await ctx.close();
  }
  // Manual mode: idle tabs stay; an admin's "push now" moves them at once
  await api('PUT', '/site-release', { token: USERS.admin.token, body: { mode: 'manual' } });
  try {
    const { page, ctx, publish, loads } = await releasePage('/stations');
    await page.clock.runFor(2_000); // the tab reads the manual policy
    let before = loads();
    await step('F-GLOBAL-16 manual mode: an idle tab with a new release waiting does not reload on its own', async () => {
      publish();
      await page.clock.runFor(70_000);
      await page.clock.runFor(60_000); // well past the 30 s idle rule
      await page.waitForTimeout(1000);
      assert(loads() === before, 'reloaded without a push');
    }, page);
    before = loads();
    await step('F-GLOBAL-17 manual mode: "push now" reloads the silent tab at once', async () => {
      await api('POST', '/site-release/push', { token: USERS.admin.token, body: {} });
      for (let i = 0; i < 20 && loads() === before; i++) { await page.clock.runFor(1_000); await page.waitForTimeout(250); }
      assert(loads() > before, 'no reload within 20 s of the push');
    }, page);
    await ctx.close();
  } finally {
    await api('DELETE', '/site-release/push', { token: USERS.admin.token }).catch(() => {});
    await api('PUT', '/site-release', { token: USERS.admin.token, body: { mode: 'auto' } }).catch(() => {});
  }
});

// ── Errors, sessions, diagnostics, accessibility (V2-95..101, V1-144/145, F-ACCESS-15) ──
section('errors', async () => {
  {
    const page = await newPage('manager');
    await step('F-ACCESS-15 / V2-97 a page that throws shows "Something went wrong" with the error and Reload Page; another route clears it', async () => {
      await page.route(/\/assets\/Holidays-[^/]+\.js$/, r => r.fulfill({ contentType: 'text/javascript', body: 'throw new Error("e2e page crash");' }));
      await page.goto(`${BASE}/admin/holidays`);
      const box = page.getByTestId('error-boundary');
      await box.waitFor();
      assert((await box.innerText()).includes('Something went wrong') && (await box.innerText()).includes('e2e page crash'), 'error text not shown');
      await box.getByRole('button', { name: 'Reload Page' }).waitFor();
      await page.locator('nav').getByRole('link', { name: 'Stations', exact: true }).first().click().catch(async () => { await page.goto(`${BASE}/admin/stations`); });
      await waitPath(page, '/admin/stations');
      await page.getByRole('heading', { name: 'Manage Stations' }).waitFor();
      assert(!(await box.count()), 'error box stayed after moving on');
      await page.unroute(/\/assets\/Holidays-[^/]+\.js$/);
    }, page);
    await closePage(page);
  }
  {
    // Its own tab: a load failure above may already have used the tab's one reload per 2 minutes
    const page = await newPage('manager');
    await step('F-ACCESS-16 / V2-97 a screen whose code file is gone after a release shows "site was updated" and reloads by itself', async () => {
      let loads = 0;
      page.on('load', () => { loads++; });
      await page.route(/\/assets\/Sponsors-[^/]+\.js$/, r => r.fulfill({ status: 404, body: 'gone' }));
      await page.goto(`${BASE}/admin/stations`);
      await page.getByRole('heading', { name: 'Manage Stations' }).waitFor();
      loads = 0;
      await page.evaluate(() => { window.history.pushState({}, '', '/admin/sponsors'); window.dispatchEvent(new PopStateEvent('popstate')); });
      await page.getByText('The site was updated while this tab was open').waitFor();
      await until(async () => loads > 0, 'reload after 0.3 s', 5000);
      await page.unroute(/\/assets\/Sponsors-[^/]+\.js$/);
    }, page);
    await closePage(page);
  }
  for (const view of ['desktop', 'iphone']) {
    const page = await newPage('admin', view);
    await step(`F-LISTENERS-1/2 ${view}: a listener joining shows in Active Now and Active Stations within seconds (push, not the 30 s poll), leaving takes it off; history and region day buttons ask for those days`, async () => {
      await page.goto(`${BASE}/admin/listeners`);
      const card = page.getByText('Active Now', { exact: true }).locator('..');
      await card.waitFor();
      const num = async () => Number(((await card.innerText()).match(/\n\s*(\d+)/) || [])[1]);
      await until(async () => Number.isFinite(await num()), 'Active Now number');
      await page.waitForTimeout(1500); // the push socket connects
      const n0 = await num();
      const key = `e2e-listener-${view}-${RUN}`;
      const t0 = Date.now();
      await api('POST', '/listeners/heartbeat', { body: { station_id: FX.station.id, session_key: key } });
      await until(async () => (await num()) === n0 + 1, `Active Now ${n0} → ${n0 + 1}`, 10000);
      const joinMs = Date.now() - t0;
      assert(joinMs < 10000, `took ${joinMs} ms`);
      await page.getByText(FX.station.name).first().waitFor();
      await api('POST', '/listeners/disconnect', { body: { station_id: FX.station.id, session_key: key } });
      await until(async () => (await num()) === n0, `Active Now back to ${n0}`, 10000);
      for (const [heading, d] of [['Listening History', 7], ['Listening History', 90], ['Listener Regions', 30]]) {
        const sec = page.locator('section', { has: page.getByRole('heading', { name: heading }) });
        const path = heading === 'Listener Regions' ? '/listeners/regions' : '/listeners/history';
        const r = await timedResponse(page, (m, p, resp) => m === 'GET' && p === path && resp.url().includes(`days=${d}`), () => sec.getByRole('button', { name: `${d}d`, exact: true }).click());
        assert(r.status === 200, `${path} days=${d} answered ${r.status}`);
      }
      return `shown ${joinMs} ms after joining`;
    }, page);
    if (view === 'desktop') {
      await step('F-GLOBAL-9 app-shell route (control room) fills the window with no page scrollbar', async () => {
        await page.goto(`${BASE}/admin/dashboard`);
        await page.waitForFunction(defaultReady, null, { timeout: 10000 }).catch(() => {});
        await page.waitForTimeout(800);
        const m = await page.evaluate(() => [document.documentElement.scrollHeight, innerHeight, document.documentElement.scrollWidth, innerWidth]);
        assert(m[0] <= m[1] + 1 && m[2] <= m[3] + 1, `page ${m[2]}x${m[0]} in a ${m[3]}x${m[1]} window`);
      }, page);
    }
    await closePage(page);
  }
  {
    // A listener's old tab: the Archives code file is gone once, the reload gets the new one
    const page = await newPage(null);
    await step('F-PARCH-3 public Archives after a release replaced its code file: reloads and opens with no error box', async () => {
      let gone = 1, loads = 0;
      page.on('load', () => { loads++; });
      await page.route(/\/assets\/Archives-[^/]+\.js$/, r => (gone-- > 0 ? r.fulfill({ status: 404, body: 'gone' }) : r.continue()));
      await page.goto(`${BASE}/stations`);
      await page.getByRole('link', { name: /Archives/ }).first().waitFor();
      loads = 0;
      await page.getByRole('link', { name: /Archives/ }).first().click();
      await page.getByRole('heading', { name: 'Show Archives' }).waitFor({ timeout: 10000 });
      assert(loads > 0, 'no reload');
      assert(!(await page.getByTestId('error-boundary').count()), 'error box shown');
      await page.unroute(/\/assets\/Archives-[^/]+\.js$/);
    }, page);
    await closePage(page);
  }
  // V2-95 renewal fails: both tokens removed, back to the right login
  for (const [where, login] of [['/admin/stations', '/admin/login'], ['/sponsor/campaigns', '/sponsor/login']]) {
    const ctx = await browser.newContext(VIEWS.desktop);
    await ctx.addInitScript(() => {
      if (!sessionStorage.getItem('e2e-once')) {
        sessionStorage.setItem('e2e-once', '1');
        localStorage.setItem('access_token', 'bad.access.token');
        localStorage.setItem('refresh_token', 'bad.refresh.token');
      }
    });
    const page = await ctx.newPage();
    const refreshes = [];
    page.on('request', r => { if (r.url().endsWith('/auth/refresh')) refreshes.push(r.url()); });
    await step(`V2-95 ${short(where)}: failed renewal removes both tokens and goes to ${login}`, async () => {
      await page.goto(`${BASE}${where}`);
      await waitPath(page, login, 8000);
      const t = await page.evaluate(() => [localStorage.getItem('access_token'), localStorage.getItem('refresh_token')]);
      assert(!t[0] && !t[1], `tokens left: ${t}`);
      return `${refreshes.length} refresh call(s) for the failing requests (one per request, not shared)`;
    }, page);
    await ctx.close();
  }
  // V1-144 / V2-96 two devices
  {
    const a = await loginApi(USERS.viewer.email, USERS.viewer.password);
    const b = await loginApi(USERS.viewer.email, USERS.viewer.password);
    const ctxA = await browser.newContext(VIEWS.desktop), ctxB = await browser.newContext(VIEWS.iphone);
    for (const [c, t] of [[ctxA, a], [ctxB, b]]) await c.addInitScript(([x, y]) => { if (!localStorage.getItem('e2e-set')) { localStorage.setItem('e2e-set', '1'); localStorage.setItem('access_token', x); localStorage.setItem('refresh_token', y); } }, [t.access_token, t.refresh_token]);
    const pa = await ctxA.newPage(), pb = await ctxB.newPage();
    await step('V1-144 same user on two devices: logging out on one leaves the other signed in', async () => {
      await pa.goto(`${BASE}/admin/schedules`); await pb.goto(`${BASE}/admin/schedules`);
      await pa.getByRole('button', { name: 'Logout' }).click();
      await waitPath(pa, '/admin/login');
      await pb.reload();
      await pb.getByRole('heading').first().waitFor();
      assert(await pathOf(pb) === '/admin/schedules', `other device sent to ${await pathOf(pb)}`);
      const r = await api('POST', '/auth/refresh', { body: { refresh_token: b.refresh_token }, raw: true });
      assert(r.status === 200, `other device's renewal refused ${r.status}`);
    }, pb);
    await step('V2-96 deactivating the user ends their other sessions at once', async () => {
      await api('PATCH', `/users/${USERS.viewer.id}`, { token: USERS.admin.token, body: { is_active: false } }).catch(() => api('PUT', `/users/${USERS.viewer.id}`, { token: USERS.admin.token, body: { is_active: false } }));
      try {
        const r = await api('GET', '/auth/me', { token: b.access_token, raw: true });
        assert(r.status === 401 || r.status === 403, `deactivated user still served (${r.status})`);
        const rr = await api('POST', '/auth/refresh', { body: { refresh_token: b.refresh_token }, raw: true });
        assert(rr.status >= 400, `deactivated user's renewal accepted (${rr.status})`);
      } finally {
        await api('PATCH', `/users/${USERS.viewer.id}`, { token: USERS.admin.token, body: { is_active: true } }).catch(() => api('PUT', `/users/${USERS.viewer.id}`, { token: USERS.admin.token, body: { is_active: true } }));
      }
      return 'a password change does not end other sessions (by design per V2-96; only deactivation does)';
    }, pb);
    await ctxA.close(); await ctxB.close();
  }
  // V2-98 / V2-99 request ids and the bug report payload
  {
    const page = await newPage('manager');
    await step('V2-98 every API call carries an X-Request-ID; the bug report leaves out tokens', async () => {
      const seen = [];
      page.on('request', r => { if (r.url().startsWith(API)) seen.push([r.url().slice(API.length).split('?')[0], r.headers()['x-request-id'] || '']); });
      await page.goto(`${BASE}/admin/stations`);
      await page.getByRole('heading', { name: 'Manage Stations' }).waitFor();
      await page.evaluate(() => localStorage.setItem('my_secret_password', 'e2e-not-a-real-password'));
      // The id is added by the axios client. A few calls are plain fetch() and go without
      // one (the release policy in lib/appUpdate.ts, now-playing polls): reported, not failed.
      const PLAIN_FETCH = ['/site-release', '/now-playing/'];
      const without = seen.filter(x => !x[1]).map(x => x[0]);
      const bad = without.filter(u => !PLAIN_FETCH.some(f => u.startsWith(f)));
      assert(seen.length > 2 && !bad.length, `${bad.length} of ${seen.length} axios calls without X-Request-ID: ${[...new Set(bad)].join(', ')}`);
      const plainNote = without.length ? `; plain fetch without id: ${[...new Set(without)].join(', ')}` : '';
      await page.locator('button[aria-label="Report a bug"]:visible, [data-testid="bug-top"]:visible').first().click();
      const dlg = page.getByRole('dialog', { name: 'Report a bug' });
      await dlg.locator('#bug-what').fill(`E2E diagnostics ${RUN}`);
      const { resp } = await timedResponse(page, (m, p) => m === 'POST' && p === '/bug-reports', () => dlg.getByRole('button', { name: 'Send report' }).click());
      const body = resp.request().postData() || '';
      const tk = await page.evaluate(() => localStorage.getItem('access_token'));
      assert(!body.includes(tk) && !/eyJ[a-zA-Z0-9_-]{10,}\./.test(body), 'a token is in the report');
      assert(!body.includes('e2e-not-a-real-password'), 'a password-like storage key is in the report');
      const j = JSON.parse(body);
      // V2-99 screenshot: JPEG of the visible window, at most 1600 px wide
      if (j.screenshot) {
        assert(j.screenshot.startsWith('data:image/jpeg'), 'screenshot is not a JPEG');
        const buf = Buffer.from(j.screenshot.split(',')[1], 'base64');
        let w = 0, h = 0;
        for (let i = 2; i < buf.length - 9;) {
          if (buf[i] !== 0xff) { i++; continue; }
          const m = buf[i + 1];
          if (m >= 0xc0 && m <= 0xc3) { h = buf.readUInt16BE(i + 5); w = buf.readUInt16BE(i + 7); break; }
          i += 2 + buf.readUInt16BE(i + 2);
        }
        const vp = page.viewportSize();
        assert(w > 0 && w <= 1600, `screenshot ${w} px wide`);
        assert(Math.abs(w / h - vp.width / vp.height) < 0.05, `screenshot ${w}x${h} is not the visible window (${vp.width}x${vp.height})`);
        return `screenshot ${w}x${h} JPEG${plainNote}`;
      }
      return `no screenshot taken${plainNote}`;
    }, page);
    await closePage(page);
  }
  // V1-145 copy review rate limit (12 a minute): the page says to try again
  {
    const page = await newPage('sponsor');
    await step('V1-145 wording check after 12 checks in a minute: the page says to try again in a minute', async () => {
      let limited = false;
      for (let i = 0; i < 14 && !limited; i++) {
        const r = await api('POST', '/sponsorship/copy-review', { token: USERS.sponsor.token, body: { script_text: 'E2E rate limit check text', sponsor_name: 'E2E' }, raw: true });
        limited = r.status === 429;
      }
      assert(limited, 'no 429 after 14 checks');
      await page.goto(`${BASE}/sponsor/wording`);
      await page.getByPlaceholder('Write it the way you would say it. We will show you what needs to change.').fill('E2E Bakery has bread.');
      await page.getByRole('button', { name: 'Check my wording' }).click();
      await page.getByText('The check could not be run. Please try again in a minute.').waitFor();
    }, page);
    await closePage(page);
  }
  {
    await step('V1-145 song requests: the 11th in a minute from one address is refused (429)', async () => {
      let n = 0, limited = false;
      for (; n < 12 && !limited; n++) {
        const r = await api('POST', '/song-requests', { body: { station_id: FX.station.id, requester_name: 'E2E', song_title: `E2E rate ${n}` }, raw: true });
        limited = r.status === 429;
      }
      assert(limited && n <= 11, `limited=${limited} after ${n}`);
      return `refused at request ${n}`;
    });
  }
});

// ── V2-101 accessibility basics on every page ───────────────────────────────
function a11yScan() {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const text = el => (el.innerText || el.textContent || '').trim();
  const nameOf = el => (el.getAttribute('aria-label') || '').trim()
    || (el.getAttribute('aria-labelledby') ? (document.getElementById(el.getAttribute('aria-labelledby'))?.textContent || '').trim() : '')
    || text(el) || (el.getAttribute('title') || '').trim() || [...el.querySelectorAll('img[alt],svg[aria-label]')].map(i => i.getAttribute('alt') || i.getAttribute('aria-label')).join('').trim();
  const out = { unnamed: [], unlabeled: [], lowContrast: [], lang: document.documentElement.lang };
  for (const b of document.querySelectorAll('button, [role=button], a[href]')) {
    if (b.closest('[data-bug-reporter]') && !vis(b)) continue;
    if (vis(b) && !nameOf(b)) out.unnamed.push(b.outerHTML.slice(0, 70));
  }
  for (const f of document.querySelectorAll('input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea')) {
    if (!vis(f) && !f.closest('label')) continue;
    const lab = (f.labels && [...f.labels].map(l => l.textContent).join('').trim()) || f.getAttribute('aria-label') || f.getAttribute('aria-labelledby') || f.getAttribute('title') || f.getAttribute('placeholder');
    if (!lab) out.unlabeled.push(f.outerHTML.slice(0, 70));
  }
  const rgb = c => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const lum = ({ r, g, b }) => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor); if (c && c.a > 0.5) return c; } return { r: 255, g: 255, b: 255, a: 1 }; };
  const root = document.querySelector('main') || document.body;
  let checked = 0;
  for (const el of root.querySelectorAll('p, span, td, th, li, h1, h2, h3, label, a, button, div')) {
    if (!vis(el)) continue;
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (!own) continue;
    const s = getComputedStyle(el);
    // Gradient text (transparent fill over a painted background) cannot be measured this way
    if (s.webkitTextFillColor && rgb(s.webkitTextFillColor)?.a === 0) continue;
    if ((s.backgroundClip || s.webkitBackgroundClip) === 'text') continue;
    const fg = rgb(s.color); if (!fg) continue;
    if (el.closest('[disabled], .disabled\\:opacity-50:disabled') || el.matches(':disabled')) continue;
    const bg = bgOf(el);
    const L1 = lum(fg), L2 = lum(bg);
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const size = parseFloat(s.fontSize), bold = Number(s.fontWeight) >= 700;
    const need = (size >= 24 || (bold && size >= 18.66)) ? 3 : 4.5;
    checked++;
    if (ratio < need) out.lowContrast.push(`${ratio.toFixed(1)}:1 "${[...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').slice(0, 30)}"`);
  }
  out.checked = checked;
  return out;
}
// Pages the first scan found gaps on (listed under KNOWN_BUGS 'V2-101 a11y-gaps'); every
// other page must stay clean. Remove a page here once it scans clean.
const A11Y_GAP_PAGES = new Set([
  '/admin/dashboard', '/admin/assets', '/admin/assets/upload', '/admin/assets/:id', '/admin/reviews/:id',
  '/admin/schedules', '/admin/holidays',
  '/admin/spot-schedule', '/admin/spot-recordings', '/admin/schedule-check', '/admin/sponsorship-rules',
  '/admin/playlists', '/admin/alerts', '/admin/site-updates',
  '/admin/analytics', '/admin/live', '/admin/live/:id/host',
  '/admin/archives', '/admin/audio-enhance/:id', '/admin/studio', '/admin/listeners',
  '/admin/program-clock', '/guide', '/listen/:id',
]);
section('a11y', async () => {
  const jobs = [
    ...adminRoutes().map(([p]) => ['admin', p]),
    ...SPONSOR_ROUTES.map(p => ['sponsor', p]),
    ...PUBLIC_ROUTES.map(p => [null, p]),
    [null, '/admin/login'], [null, '/sponsor/login'], [null, `/listen/${FX.station.id}`], [null, `/call/${FX.station.id}`],
  ];
  const pages = {};
  const summary = { unnamed: 0, unlabeled: 0, lowContrast: 0, clean: 0 };
  for (const [role, p] of jobs) {
    const k = role || 'anon';
    if (!pages[k]) pages[k] = await newPage(role);
    const page = pages[k];
    const known = A11Y_GAP_PAGES.has(short(p));
    await step(`V2-101 a11y${known ? '-gaps' : ''} ${short(p)}: buttons named, fields labelled, main text contrast AA, lang="en"`, async () => {
      await page.goto(`${BASE}${p}`);
      await page.waitForFunction(defaultReady, null, { timeout: 10000 }).catch(() => {});
      await page.waitForTimeout(400);
      const r = await page.evaluate(a11yScan);
      const probs = [];
      if (r.lang !== 'en') probs.push(`lang="${r.lang}"`);
      if (r.unnamed.length) probs.push(`${r.unnamed.length} unnamed button/link (${r.unnamed[0]})`);
      if (r.unlabeled.length) probs.push(`${r.unlabeled.length} unlabelled field (${r.unlabeled[0]})`);
      if (r.lowContrast.length) probs.push(`${r.lowContrast.length}/${r.checked} low-contrast text (${r.lowContrast.slice(0, 2).join(', ')})`);
      summary.unnamed += r.unnamed.length; summary.unlabeled += r.unlabeled.length; summary.lowContrast += r.lowContrast.length;
      if (!probs.length) summary.clean++;
      assert(!probs.length, probs.join('; '));
      return `clean (${r.checked} text items)`;
    }, page);
  }
  for (const pg of Object.values(pages)) await closePage(pg);
  // Esc closes modals; the "not allowed" notice is announced
  const page = await newPage('manager');
  await step('V2-101 Esc closes the New Archive, New Show and bug report dialogs', async () => {
    const fails = [];
    await page.goto(`${BASE}/admin/archives`);
    await page.getByRole('button', { name: '+ New Archive' }).click();
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    if (await page.getByRole('heading', { name: 'New Archive' }).count()) fails.push('New Archive');
    await page.goto(`${BASE}/admin/live`);
    await page.getByRole('button', { name: '+ New Show' }).click();
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    if (await page.getByRole('heading', { name: 'Create Live Show' }).count()) fails.push('New Show');
    await page.locator('button[aria-label="Report a bug"]:visible, [data-testid="bug-top"]:visible').first().click();
    await page.getByRole('dialog', { name: 'Report a bug' }).waitFor({ timeout: 10000 });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    if (await page.getByRole('dialog', { name: 'Report a bug' }).count()) fails.push('bug report');
    assert(!fails.length, `Esc does not close: ${fails.join(', ')}`);
  }, page);
  await closePage(page);
  console.log(`  a11y totals: ${summary.clean}/${jobs.length} pages clean; ${summary.unnamed} unnamed buttons/links, ${summary.unlabeled} unlabelled fields, ${summary.lowContrast} low-contrast text items`);
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
  browser = guardBrowser(await chromium.launch());
  for (const s of SECTIONS) {
    if (ONLY.length && !ONLY.includes(s.name)) continue;
    console.log(`\n── ${s.name} ──`);
    try { await s.fn(); } catch (e) { record(`${s.name}: section crashed`, false, String(e?.stack || e).slice(0, 300)); }
  }
  await browser.close();
  const n = (st) => results.filter(r => r.status === st).length;
  console.log(`\n${n('PASS')} passed, ${n('KNOWN-BUG')} known bugs, ${n('FAIL')} failed, ${n('FIXED?')} known bugs now passing (${results.length} checks)`);
  fs.rmSync(TMP, { recursive: true, force: true });
  const looked = guardVerdict();   // always printed, also when checks failed
  process.exit(n('FAIL') || n('FIXED?') || looked ? 1 : 0);
}
main().catch(e => { console.error(e); process.exit(2); });
