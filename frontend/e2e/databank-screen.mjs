/**
 * Browser check of the Databank spot end-date edit and phone tap sizes against a fake
 * backend (no real station is touched): F-DATABANK-56 (clearing asks first),
 * F-DATABANK-60 (no station chosen: nothing sent), F-DATABANK-63 (44 px targets) and
 * the end-date and layout rows around them, at desktop 1366x768, iPhone 13 and SE,
 * landscape and tablets. The rest of the screen is in e2e/databank-ui.mjs; server
 * behaviour in backend/tests/test_databank_rows.py and test_databank_more.py.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4312 --strictPort &)
 *   BASE_URL=http://localhost:4312 node e2e/databank-screen.mjs   # optional: ONLY=<part,...>
 *
 * Exits non-zero if a check fails. Known site bugs are listed in KNOWN_BUGS (XFAIL).
 */
import {
  chromium, devices, checker, sleep, makeStation, adminContext, newPage,
} from './lib/fake-station.mjs';
import { allowEverywhere } from './lib/guard.mjs';

// Provoked on purpose; anything else the screen or console shows fails the run (e2e/lib/guard.mjs)
allowEverywhere(/The end date is before the schedule starts|Choose a station first/, 'wrong entries made to see each message');

const BASE = process.env.BASE_URL || 'http://localhost:4312';
const ONLY = (process.env.ONLY || '').split(',').map((s) => s.trim()).filter(Boolean);
const DESKTOP = { viewport: { width: 1366, height: 768 } };
const PHONE = { ...devices['iPhone 13'] }; // 390x844
const SE = { ...devices['iPhone SE'] };
const LANDSCAPE = { ...devices['iPhone 13 landscape'] };
const IPAD = { viewport: { width: 768, height: 1024 }, hasTouch: true, isMobile: true };
const WIDE_TABLET = { viewport: { width: 1024, height: 768 }, hasTouch: true };

const KNOWN_BUGS = {};
const { check, done } = checker(KNOWN_BUGS);

const STATIONS = [
  { id: 'st-1', name: "Kol B'ramah", timezone: 'America/New_York', is_active: true, channels: [] },
  { id: 'st-2', name: "Kol B'ramah Israel", timezone: 'Asia/Jerusalem', is_active: true, channels: [] },
];

/* ── Fake databank backend ─────────────────────────────── */

function makeDb(opts = {}) {
  const songs = [];
  const n = opts.songs ?? 450;
  for (let i = 1; i <= n; i++) {
    const p = String(i).padStart(3, '0');
    songs.push({
      id: `s${i}`, artist: `Artist ${p}`, title: `Title ${String(1000 - i)}`, type: i === n ? 'Rare type' : i % 2 ? 'lively' : 'slow',
      asset_type: 'music', vote: (i * 37) % 101, length: 120 + (i % 200), intro: i % 4 ? i % 30 : null,
      file: `song${p}.${i % 5 === 0 ? 'wav' : 'mp3'}`,
      last_played: i === 1 ? '2026-11-16T03:05:00Z' : i % 3 === 0 ? null : new Date(Date.UTC(2026, 10, 1) + i * 3600e3).toISOString(),
    });
  }
  const jingles = Array.from({ length: opts.jingles ?? 5 }, (_, i) => ({
    id: `j${i + 1}`, artist: null, title: `Station ID ${i + 1}`, type: i % 2 ? 'ids' : 'sweepers', asset_type: 'jingle',
    vote: 50, length: 6 + i, intro: null, file: `id${i + 1}.mp3`, last_played: null,
  }));
  const spots = opts.spots ?? [
    { id: 'sp1', title: 'Bagel King Breakfast', file: 'bagel.mp3', category: 'food', end_date: '2026-12-20', open_ended: false, schedules: 2, active: true, expired: false, length: 23 },
    { id: 'sp2', title: 'Fig Flip Pro', file: 'fig.wav', category: 'phone', end_date: '2026-01-05', open_ended: false, schedules: 1, active: true, expired: true, length: 34 },
    { id: 'sp3', title: 'Always on air', file: 'always.mp3', category: 'bank', end_date: null, open_ended: true, schedules: 1, active: true, expired: false, length: 15 },
    { id: 'sp4', title: 'Paused deal', file: 'paused.mp3', category: 'food', end_date: '2027-02-01', open_ended: false, schedules: 1, active: false, expired: false, length: 40 },
    { id: 'sp5', title: 'Unscheduled spot', file: 'loose.mp3', category: null, end_date: null, open_ended: false, schedules: 0, active: false, expired: false, length: 30 },
  ];
  const blocks = opts.blocks ?? [
    { id: 'b1', station_id: 'st-1', name: 'Weather Sponsors', is_active: true, days_of_week: '6,0,1,2,3,4', times: '08:00,07:00', timing_mode: 'hard', latest_minutes: null, items: [{}, {}, {}] },
    { id: 'b2', station_id: 'st-1', name: 'Afternoon PSA', is_active: false, days_of_week: '0,1,2,3,4,5,6', times: '07:00', timing_mode: 'soft', latest_minutes: 5, items: [{}] },
    { id: 'b3', station_id: 'st-1', name: 'Alpha news', is_active: true, days_of_week: '5', times: '07:00', timing_mode: 'soft', latest_minutes: null, items: [{}, {}] },
  ];
  const logs = opts.logs ?? {};
  const db = {
    songs, jingles, spots, blocks, logs, calls: [], role: opts.role || 'admin',
    refuse: {},        // path -> { status, detail } once
    totalOverride: null,
  };
  const like = (v, s) => (v ?? '').toString().toLowerCase().includes(s);
  db.handle = (method, path, q, body) => {
    if (path.startsWith('/databank') || path.startsWith('/program-blocks') || /^\/assets\/[^/]+$/.test(path) || /audio-url$/.test(path)) {
      db.calls.push({ method, path, query: q, body, at: Date.now() });
    }
    if (db.refuse[path]) { const r = db.refuse[path]; delete db.refuse[path]; return { __status: r.status, detail: r.detail }; }
    if (path === '/auth/me') return { id: 'u1', email: 'operator@example.com', role: db.role, is_active: true };
    if (path === '/databank/items') {
      let rows = q.asset_type === 'jingle' ? jingles : q.asset_type === 'music' || !q.asset_type ? songs : [];
      if (q.search) { const s = q.search.toLowerCase(); rows = rows.filter((r) => like(r.title, s) || like(r.artist, s) || like(r.type, s) || like(r.file, s)); }
      if (q.category) rows = rows.filter((r) => (r.type ?? '').toLowerCase() === q.category.toLowerCase());
      if (q.files === 'mpeg') rows = rows.filter((r) => /\.(mp3|mp2|mpg|mpeg)$/i.test(r.file));
      if (q.files === 'present') rows = rows.filter((r) => r.file && r.type !== 'do_not_play');
      const col = { artist: 'artist', title: 'title', type: 'type', vote: 'vote', length: 'length', intro: 'intro', file: 'file', last_played: 'last_played' }[q.sort] || 'artist';
      const desc = q.desc === 'true';
      const sorted = [...rows].sort((a, b) => {
        const x = a[col], y = b[col];
        if (x == null && y != null) return 1;
        if (y == null && x != null) return -1;
        const c = x < y ? -1 : x > y ? 1 : 0;
        return (desc ? -c : c) || (a.title < b.title ? -1 : a.title > b.title ? 1 : a.id < b.id ? -1 : 1);
      });
      const skip = +(q.skip || 0), limit = +(q.limit || 200);
      return { total: db.totalOverride ?? sorted.length, items: sorted.slice(skip, skip + limit) };
    }
    if (path === '/databank/types') {
      const rows = q.asset_type === 'jingle' ? jingles : songs;
      return { types: [...new Set(rows.map((r) => r.type).filter(Boolean))].sort() };
    }
    if (path === '/databank/spots') return { today: '2026-11-17', spots };
    const endDate = path.match(/^\/databank\/spots\/([^/]+)\/end-date$/);
    if (endDate && method === 'PATCH') {
      const s = spots.find((x) => x.id === endDate[1]);
      s.end_date = body.end_date; s.open_ended = !body.end_date; s.expired = !!body.end_date && body.end_date < '2026-11-17';
      return { updated: s.schedules, end_date: body.end_date };
    }
    if (path === '/databank/log') {
      if (logs.__error) return { __status: 500, detail: 'boom' };
      return { day: q.day, timezone: STATIONS.find((s) => s.id === q.station_id)?.timezone ?? 'UTC', logs: logs[q.day] ?? [] };
    }
    if (path === '/program-blocks') return blocks;
    const asset = path.match(/^\/assets\/([^/]+)$/);
    if (asset && method === 'PATCH') {
      const row = [...songs, ...jingles].find((r) => r.id === asset[1]);
      const sp = spots.find((r) => r.id === asset[1]);
      if (row) {
        if ('vote' in body) row.vote = body.vote;
        if ('title' in body) row.title = body.title;
        if ('artist' in body) row.artist = body.artist;
        if ('category' in body) row.type = body.category;
        if ('intro_seconds' in body) row.intro = body.intro_seconds;
      }
      if (sp && 'category' in body) sp.category = body.category;
      return { id: asset[1], ...body };
    }
    const audio = path.match(/^\/assets\/([^/]+)\/audio-url$/);
    if (audio) return { url: audio[1] === 'broken' || audio[1] === 's2' && opts.brokenS2 ? `${BASE}/__broken.mp3` : `/__tone/${audio[1]}.wav` };
    return undefined;
  };
  return db;
}

/** One play today (station time, New York) so the Log tab has a line. */
function todayLogs() {
  const day = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date());
  const at = new Date(Date.now() - 600e3).toISOString();
  return { [day]: [{ id: 'l1', asset_id: 'a1', title: 'Gadol Hashem', artist: 'Shalsheles', asset_type: 'music', file: 'gadol.mp3', start_utc: at, end_utc: null, source: 'scheduler' }] };
}

const browser = await chromium.launch();

async function open(contextOpts, { role = 'admin', path = '/admin/databank', storage = { radio_last_station: 'st-1' }, delay, db: dbOpts = {}, ctxExtra = {}, beforeGoto } = {}) {
  const station = makeStation({ stations: STATIONS });
  const db = makeDb({ role, ...dbOpts });
  const orig = station.handle;
  station.handle = (m, p, q, b) => { const r = db.handle(m, p, q, b); return r === undefined ? orig(m, p, q, b) : r; };
  const { ctx } = await adminContext(browser, { ...contextOpts, ...ctxExtra }, station, { storage, delay });
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  await ctx.route('**/__broken.mp3', (r) => r.fulfill({ status: 200, contentType: 'audio/mpeg', body: 'this is not audio' }));
  const { page, errors } = await newPage(ctx);
  page.on('dialog', (d) => { (page.__dialogs ||= []).push(d.message()); (page.__acceptDialogs ?? true) ? d.accept() : d.dismiss(); });
  if (beforeGoto) await beforeGoto(page);
  if (path) await page.goto(`${BASE}${path}`);
  return { ctx, page, db, station, errors };
}
const rows = (page) => page.locator('[data-testid="db-table"] [role="row"][data-row]');
const rowTexts = (page) => rows(page).allInnerTexts();
const status = async (page) => (await page.getByTestId('db-status').innerText()).replace(/\s+/g, ' ');
const head = (page, name) => page.locator('.qn-log-head button', { hasText: new RegExp(`^${name}( [▲▼])?$`) });
// (the Tot request, limit=1, is not a page of rows)
const lastCall = (db, re, method = 'GET') => [...db.calls].reverse().find((c) => c.method === method && re.test(c.path) && c.query.limit !== '1');
const writes = (db) => db.calls.filter((c) => c.method !== 'GET');
const waitRows = (page) => rows(page).first().waitFor({ timeout: 8000 });
const noticeText = (page) => page.locator('[role="status"]').innerText().catch(() => '');
const noSideScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
async function until(fn, timeout = 4000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) { try { if (await fn()) return true; } catch { /* not yet */ } await sleep(50); }
  return false;
}
const firstRowId = (page) => rows(page).first().getAttribute('data-row');

/* ── Parts ─────────────────────────────────────────────── */

const PARTS = {
  // F-DATABANK-16, 18, 20, 37, 40, 56, 60: spots
  async spots() {
    for (const [name, view] of [['desktop', DESKTOP], ['phone', PHONE]]) {
      const { ctx, page, db } = await open(view, { path: '/admin/databank?tab=spots' });
      await waitRows(page);
      const text = (id) => page.locator(`[data-row="${id}"]`).innerText();
      if (name === 'desktop') {
        check('F-DATABANK-16: end date d/m/yyyy, schedules count', /20\/12\/2026/.test(await text('sp1')) && /\b2\b/.test(await text('sp1')));
        check('F-DATABANK-16: "no end", "(off)", "not scheduled"', /no end/.test(await text('sp3')) && /1 \(off\)/.test(await text('sp4')) && /not scheduled/.test(await text('sp5')));
        // F-DATABANK-37: sorted and filtered in the page
        const order = async () => (await rows(page).evaluateAll((rs) => rs.map((r) => r.getAttribute('data-row')))).join(',');
        await head(page, 'End date').click(); // starts latest first, like Last played
        check('F-DATABANK-37: End date descending first: open-ended first, unscheduled last', await order() === 'sp3,sp4,sp1,sp2,sp5', await order());
        await head(page, 'End date').click();
        check('F-DATABANK-37: End date ascending: unscheduled first, open-ended last', await order() === 'sp5,sp2,sp1,sp4,sp3', await order());
        await head(page, 'Length').click();
        check('F-DATABANK-37: Length ascending', await order() === 'sp3,sp1,sp5,sp2,sp4', await order());
        await head(page, 'Length').click();
        check('F-DATABANK-37: Length descending', await order() === 'sp4,sp2,sp5,sp1,sp3', await order());
        await head(page, 'Category').click();
        check('F-DATABANK-37: Category ascending (none first)', (await order()).startsWith('sp5,sp3,'), await order());
        await head(page, 'File').click();
        check('F-DATABANK-37: File (title) ascending', await order() === 'sp3,sp1,sp2,sp4,sp5', await order());
        await head(page, 'File').click();
        check('F-DATABANK-37: File descending', await order() === 'sp5,sp4,sp2,sp1,sp3', await order());
        for (const [find, want] of [['fig.wav', 'sp2'], ['bank', 'sp3'], ['bagel king', 'sp1']]) {
          await page.getByTestId('db-find').fill(find);
          await until(async () => (await order()) === want);
          check(`F-DATABANK-37: Find "${find}" over title, file and category`, await order() === want, await order());
        }
        await page.getByRole('button', { name: 'ALL' }).click();
        await page.getByTestId('db-type').selectOption('food');
        await until(async () => (await rows(page).count()) === 2);
        check('F-DATABANK-37: Category filter', (await order()).split(',').sort().join(',') === 'sp1,sp4');
        await page.getByRole('button', { name: 'ALL' }).click();
        await page.getByTestId('db-mpeg').click();
        await until(async () => (await rows(page).count()) === 4);
        check('F-DATABANK-37: Mpeg leaves out the .wav', !(await order()).includes('sp2'));
        await page.getByRole('button', { name: 'ALL' }).click();
        await page.getByTestId('db-present').click();
        check('F-DATABANK-37: Prsnt keeps files that are there', (await rows(page).count()) === 5);
        await page.getByRole('button', { name: 'ALL' }).click();
      }
      // F-DATABANK-18: end-date edit (desktop with Enter, phone by leaving the box)
      await page.getByTestId('db-edit-mode').click();
      const endCell = page.locator('[data-row="sp2"]').getByTestId('spot-end');
      await endCell.click();
      await page.getByTestId('db-edit-input').fill('2027-03-15');
      const t0 = Date.now();
      if (name === 'phone') await page.locator('.qn-title').tap(); else await page.getByTestId('db-edit-input').press('Enter');
      await until(async () => (await noticeText(page)).includes('now runs until'));
      const w = lastCall(db, /end-date$/, 'PATCH');
      check(`F-DATABANK-18 ${name}: end date saved for the chosen station, notice in d/m/yyyy`, w?.body?.end_date === '2027-03-15' && w?.query?.station_id === 'st-1'
        && (await noticeText(page)) === 'Fig Flip Pro now runs until 15/3/2027.', `${await noticeText(page)} ${JSON.stringify(w?.query)}`);
      check(`F-DATABANK-26 ${name}: end-date save confirmed within 1 s`, Date.now() - t0 < 1000, `${Date.now() - t0} ms`);
      check(`F-DATABANK-18 ${name}: red clears`, await until(async () => (await page.locator('[data-row="sp2"][data-expired]').count()) === 0));
      // F-DATABANK-20: the page shows the server's words
      db.refuse['/databank/spots/sp1/end-date'] = { status: 422, detail: 'The end date is before the schedule starts' };
      await page.locator('[data-row="sp1"]').getByTestId('spot-end').click();
      await page.getByTestId('db-edit-input').fill('2026-01-01');
      if (name === 'phone') await page.locator('.qn-title').tap(); else await page.getByTestId('db-edit-input').press('Enter');
      await until(async () => (await noticeText(page)).includes('before the schedule'));
      check(`F-DATABANK-20 ${name}: the server's words are shown`, (await noticeText(page)) === 'The end date is before the schedule starts');
      // F-DATABANK-40 / 56: clearing asks first; dismiss sends nothing, accept makes it open-ended
      page.__acceptDialogs = false;
      page.__dialogs = [];
      let n0 = writes(db).length;
      await page.locator('[data-row="sp1"]').getByTestId('spot-end').click();
      await page.getByTestId('db-edit-input').fill('');
      if (name === 'phone') await page.locator('.qn-title').tap(); else await page.getByTestId('db-edit-input').press('Enter');
      await sleep(300);
      check(`F-DATABANK-56 ${name}: clearing the end date asks first; No sends nothing`, page.__dialogs.length === 1 && /no end/.test(page.__dialogs[0]) && writes(db).length === n0, page.__dialogs.join('|'));
      page.__acceptDialogs = true;
      n0 = writes(db).length;
      await page.locator('[data-row="sp1"]').getByTestId('spot-end').click();
      await page.getByTestId('db-edit-input').fill('');
      if (name === 'phone') await page.locator('.qn-title').tap(); else await page.getByTestId('db-edit-input').press('Enter');
      await until(async () => (await noticeText(page)).includes('no end date'));
      check(`F-DATABANK-40 ${name}: cleared end date: open-ended, notice "<spot> now has no end date."`, writes(db)[n0]?.body?.end_date === null
        && (await noticeText(page)) === 'Bagel King Breakfast now has no end date.' && /no end/.test(await text('sp1')));
      await ctx.close();
    }
    // F-DATABANK-60: no station chosen yet: the end date is not sent for every station
    let releaseStations;
    const gate = new Promise((r) => { releaseStations = r; });
    const { ctx, page, db } = await open(DESKTOP, { path: null, storage: { radio_last_station: null } });
    await ctx.route(/\/api\/v1\/stations(\?|$)/, async (route) => { await gate; await route.fallback(); });
    await page.goto(`${BASE}/admin/databank?tab=spots`);
    await waitRows(page);
    await page.getByTestId('db-edit-mode').click();
    await page.locator('[data-row="sp1"]').getByTestId('spot-end').click();
    await page.getByTestId('db-edit-input').fill('2027-05-01');
    await page.getByTestId('db-edit-input').press('Enter');
    await sleep(300);
    check('F-DATABANK-60: with no station chosen the end date is not sent (no every-station change)', !writes(db).some((c) => /end-date/.test(c.path)) && (await noticeText(page)).includes('Choose a station first'), await noticeText(page));
    releaseStations();
    await until(async () => (await page.getByLabel('Station').count()) === 1);
    await page.locator('[data-row="sp1"]').getByTestId('spot-end').click();
    await page.getByTestId('db-edit-input').fill('2027-05-01');
    await page.getByTestId('db-edit-input').press('Enter');
    await until(async () => !!lastCall(db, /end-date$/, 'PATCH'));
    check('F-DATABANK-60: once a station is chosen the edit names it', lastCall(db, /end-date$/, 'PATCH')?.query?.station_id === 'st-1');
    await ctx.close();
  },
  // F-DATABANK-24, 49, 50, 63: phone and tablet layout
  async layout() {
    for (const [name, view] of [['iPhone 13', PHONE], ['iPhone SE', SE]]) {
      const { ctx, page } = await open(view, { db: { logs: todayLogs() } });
      await waitRows(page);
      check(`F-DATABANK-24 ${name}: cards with a 🎧 each, no column header`, (await page.locator('.qn-log-head').count()) === 0 && (await page.locator('[data-row] button[aria-label^="Pre-listen"]').count()) === 200);
      const small = await page.evaluate(() => {
        const out = [];
        const sel = '[role=tablist] button, [data-testid=db-find], [data-testid=db-status] button, [data-testid=db-status] input, [data-testid=db-mpeg], [data-testid=db-present], [data-testid=db-type], [data-testid=db-edit-mode], [aria-label="Pre-listen to the selected item"], [data-row] button';
        for (const el of document.querySelectorAll(sel)) {
          const r = el.getBoundingClientRect();
          if (r.width && (r.height < 44 || r.width < 44)) out.push(`${el.getAttribute('aria-label') || el.textContent.trim().slice(0, 12)} ${Math.round(r.width)}x${Math.round(r.height)}`);
        }
        return out;
      });
      check(`F-DATABANK-24/63 ${name}: tabs, toolbar and status-bar buttons at least 44 px`, small.length === 0, small.join(', '));
      const fs = await page.getByTestId('db-find').evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
      check(`F-DATABANK-24 ${name}: Find in 16 px type`, fs >= 16, `${fs}px`);
      const stBox = await page.getByTestId('db-status').boundingBox();
      const pad = await page.getByTestId('db-status').getAttribute('class');
      check(`F-DATABANK-24 ${name}: status bar on screen and padded above the home bar`, stBox && stBox.y + stBox.height <= view.viewport.height + 1 && /safe-area-inset-bottom/.test(pad));
      for (const tab of ['Songs', 'Jingles', 'Spots', 'Blocks', 'Log']) {
        await page.getByRole('tab', { name: tab }).click();
        await sleep(400);
        check(`F-DATABANK-24 ${name}: no sideways scroll on ${tab}`, await noSideScroll(page));
      }
      await page.getByRole('tab', { name: 'Log' }).click();
      const line = page.locator('[data-testid="daylog-lines"] button').first();
      if (await until(() => line.isVisible(), 4000)) {
        const lh = await line.evaluate((el) => el.getBoundingClientRect().height);
        check(`F-DATABANK-63 ${name}: a day-log line is at least 44 px tall`, lh >= 44, `${lh}px`);
      } else check(`F-DATABANK-63 ${name}: a day-log line is at least 44 px tall`, false, 'no log line');
      await page.getByRole('tab', { name: 'Spots' }).click();
      await waitRows(page);
      await page.getByTestId('db-edit-mode').click();
      const eh = await page.locator('[data-row="sp1"]').getByTestId('spot-end').evaluate((el) => el.getBoundingClientRect().height);
      check(`F-DATABANK-63 ${name}: the end-date cell is at least 44 px tall`, eh >= 44, `${eh}px`);
      await ctx.close();
    }
    // F-DATABANK-49: phone held sideways
    const ls = await open(LANDSCAPE);
    await waitRows(ls.page);
    const tb = await ls.page.locator('[role=tablist]').boundingBox();
    const find = await ls.page.getByTestId('db-find').boundingBox();
    const stb = await ls.page.getByTestId('db-status').boundingBox();
    const tableBox = await ls.page.getByTestId('db-table').boundingBox();
    check('F-DATABANK-49: sideways: toolbar wraps (Find under the tabs)', find.y >= tb.y + tb.height - 1);
    check('F-DATABANK-49: sideways: table and status bar on screen, no sideways scroll',
      tableBox.height > 40 && stb.y + stb.height <= LANDSCAPE.viewport.height + 1 && await noSideScroll(ls.page), `table ${Math.round(tableBox.height)}px, status at ${Math.round(stb.y)}`);
    await ls.ctx.close();
    // F-DATABANK-50: tablets
    const ipad = await open(IPAD);
    await waitRows(ipad.page);
    check('F-DATABANK-50: iPad Mini portrait (768): cards, no sideways scroll', (await ipad.page.locator('.qn-log-head').count()) === 0 && await noSideScroll(ipad.page));
    await ipad.ctx.close();
    const wide = await open(WIDE_TABLET);
    await waitRows(wide.page);
    check('F-DATABANK-50: 1024 px wide: the desktop table takes over', (await wide.page.locator('.qn-log-head').count()) === 1 && await noSideScroll(wide.page));
    await wide.ctx.close();
  },

};

for (const [name, fn] of Object.entries(PARTS)) {
  if (ONLY.length && !ONLY.includes(name)) continue;
  console.log(`\n── ${name}`);
  try { await fn(); } catch (e) { check(`${name}: ran to the end`, false, String(e).split('\n')[0]); }
}
await browser.close();
process.exit(done());
