/**
 * Browser check of the Databank screen (/admin/databank) against a fake backend
 * (no real station is touched): the F-DATABANK rows of docs/test-checklist.md that
 * are about the screen. Desktop (1366x768) and iPhone (390x844); some parts also at
 * iPhone SE, phone landscape and tablet sizes. Server behaviour is in
 * backend/tests/test_databank_rows.py; e2e/databank.mjs is the run against a real site.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4312 --strictPort &)
 *   BASE_URL=http://localhost:4312 node e2e/databank-screen.mjs   # optional: ONLY=<part,...>
 *
 * Exits non-zero if a check fails. Known site bugs are listed in KNOWN_BUGS (XFAIL).
 */
import {
  chromium, devices, checker, sleep, makeStation, adminContext, newPage, timeClick,
} from './lib/fake-station.mjs';

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
  // F-DATABANK-1: the DB buttons, Content > Databank, title bar follows the tab
  async opening() {
    const tag = 'F-DATABANK-1';
    let { ctx, page } = await open(DESKTOP, { path: '/admin/dashboard' });
    await page.getByTestId('cr-databank').click();
    await page.waitForURL(/\/admin\/databank$/);
    await waitRows(page);
    const title = page.locator('.qn-title');
    check(`${tag}: desktop DB button opens Songs.db with the station's name`, /KBR Database - \[Songs\.db\]/.test(await title.innerText()) && (await title.innerText()).includes("Kol B'ramah"));
    for (const [tab, file] of [['Jingles', 'Jingles.db'], ['Spots', 'Spots.db'], ['Blocks', 'Blocks.db'], ['Log', 'Log']]) {
      await page.getByRole('tab', { name: tab }).click();
      check(`${tag}: title bar file name follows the ${tab} tab`, (await title.innerText()).includes(`[${file}]`));
    }
    await page.goto(`${BASE}/admin/stations`);
    await page.getByRole('button', { name: 'Content' }).hover();
    await page.getByRole('link', { name: 'Databank' }).click();
    await page.waitForURL(/\/admin\/databank$/);
    check(`${tag}: Content > Databank opens the page`, await until(() => page.locator('.qn-title').isVisible()));
    await ctx.close();

    ({ ctx, page } = await open(PHONE, { path: '/admin/dashboard' }));
    await page.getByRole('button', { name: /More/ }).click();
    await page.getByTestId('cr-databank').click();
    await page.waitForURL(/\/admin\/databank\?tab=log$/);
    check(`${tag}: phone Played pane's Databank button opens the Log tab`, (await page.getByRole('tab', { name: 'Log' }).getAttribute('aria-selected')) === 'true' && (await page.locator('.qn-title').innerText()).includes('[Log]'));
    await ctx.close();
  },

  // F-DATABANK-2, 3, 9, 25 (sort, last played display, status bar, speed)
  async sorting() {
    const { ctx, page, db, errors } = await open(DESKTOP, {
      beforeGoto: (p) => p.evaluate(() => { window.__nav0 = performance.now(); }),
    });
    const t0 = Date.now();
    await waitRows(page);
    const firstRows = Date.now() - t0;
    check('F-DATABANK-25: first rows within 1.5 s of opening', firstRows < 1500, `${firstRows} ms`);
    // F-DATABANK-3: day-first date and HH:MM in the station's time zone (New York)
    const s1 = page.locator('[data-row="s1"]');
    check('F-DATABANK-3: last played shown d/m/yyyy and HH:MM in station time', /15\/11\/2026\s+22:05/.test(await s1.innerText()), (await s1.innerText()).replace(/\s+/g, ' '));
    // F-DATABANK-9: status bar
    let st = await status(page);
    check('F-DATABANK-9: Page 1 of 3, View and Tot', /Page 1 of 3/.test(st) && /View 450/.test(st) && /Tot 450/.test(st), st);
    check('F-DATABANK-9: ◀ greyed on the first page', await page.getByLabel('Previous page').isDisabled());
    await page.getByLabel('Next page').click();
    await until(async () => /Page 2 of 3/.test(await status(page)));
    await rows(page).first().click();
    check('F-DATABANK-9: Rec counts across pages (first row of page 2 = 201)', /Rec 201/.test(await status(page)), await status(page));
    await page.getByLabel('Next page').click();
    await until(async () => /Page 3 of 3/.test(await status(page)));
    check('F-DATABANK-9: ▶ greyed on the last page, 50 rows there', await page.getByLabel('Next page').isDisabled() && (await rows(page).count()) === 50);
    await page.getByTestId('db-find').fill('Artist 00');
    await until(async () => /View 9\b/.test(await status(page)));
    st = await status(page);
    check('F-DATABANK-9: View = found, Tot = whole table', /View 9\b/.test(st) && /Tot 450/.test(st) && /Page 1\b/.test(st), st);
    await page.getByRole('button', { name: 'ALL' }).click();
    await until(async () => /View 450/.test(await status(page)));

    // F-DATABANK-2: every column, both directions, ▲/▼, V and Last played start highest/newest, back to page 1
    const cols = [['Artist', 'artist', false], ['Title', 'title', false], ['Type', 'type', false], ['V', 'vote', true],
      ['Length', 'length', false], ['Intro', 'intro', false], ['File', 'file', false], ['Last played', 'last_played', true]];
    const sortTimes = [];
    for (const [label, key, startsDesc] of cols) {
      await page.getByLabel('Next page').click();
      await until(async () => /Page 2/.test(await status(page)));
      const ms = await timeClick(page, head(page, label), `() => document.querySelector('[data-testid=db-status]').textContent.includes('Page 1 of')`);
      if (label !== 'Artist') sortTimes.push(ms);
      let call = lastCall(db, /^\/databank\/items$/);
      const wantDesc = label === 'Artist' ? !false : startsDesc; // Artist was already the sort: a click flips it
      await until(async () => (await head(page, label).innerText()).includes(wantDesc ? '▼' : '▲'));
      call = lastCall(db, /^\/databank\/items$/);
      check(`F-DATABANK-2: ${label} sorts on the server (${wantDesc ? 'desc' : 'asc'}) with ${wantDesc ? '▼' : '▲'}, back to page 1`,
        call.query.sort === key && call.query.desc === String(wantDesc) && call.query.skip === '0' && (await head(page, label).innerText()).includes(wantDesc ? '▼' : '▲'),
        `${call.query.sort} desc=${call.query.desc} skip=${call.query.skip}`);
      const firstBefore = await firstRowId(page);
      await head(page, label).click();
      await until(async () => (await head(page, label).innerText()).includes(wantDesc ? '▲' : '▼') && (await firstRowId(page)) !== firstBefore);
      call = lastCall(db, /^\/databank\/items$/);
      // (a flip back to an order already loaded may come from the cache without a request)
      check(`F-DATABANK-2: second click on ${label} flips it`, (await head(page, label).innerText()).includes(wantDesc ? '▲' : '▼')
        && (await firstRowId(page)) !== firstBefore && call.query.sort === key);
    }
    // the rows really follow the order: V highest first
    await head(page, 'V').click(); // was last_played; V starts highest first
    await until(async () => (await head(page, 'V').innerText()).includes('▼'));
    await sleep(200);
    const votes = await rows(page).evaluateAll((rs) => rs.map((r) => Number(r.children[3].textContent)));
    check('F-DATABANK-2: rows drawn in V order, highest first', votes.length === 200 && votes.every((v, i) => i === 0 || votes[i - 1] >= v));
    check('F-DATABANK-25: each sort shows its rows within 500 ms', sortTimes.every((t) => t >= 0 && t < 500), sortTimes.join(','));
    check('sorting: no script errors', errors.length === 0, errors.join(' | '));
    await ctx.close();
  },

  // F-DATABANK-5, 6, 28, 25 (filters), 29, 43
  async filters() {
    const { ctx, page, db } = await open(DESKTOP);
    await waitRows(page);
    const typeSel = page.getByTestId('db-type');
    const opts = await typeSel.locator('option').allInnerTexts();
    check('F-DATABANK-6: "Every type" plus the types', opts[0] === 'Every type' && opts.includes('lively') && opts.includes('slow'), opts.join('|'));
    check('F-DATABANK-28: a type first seen on page 3 can be picked from page 1', opts.includes('Rare type'));
    let t = Date.now();
    await typeSel.selectOption('Rare type');
    await until(async () => (await page.getByTestId('db-view').innerText()) === 'View 1');
    const ms = Date.now() - t;
    check('F-DATABANK-6: type filter keeps only that type (server side)', lastCall(db, /^\/databank\/items$/).query.category === 'Rare type' && (await rows(page).count()) === 1);
    await typeSel.selectOption('lively');
    await page.getByTestId('db-find').fill('Artist 00');
    t = Date.now();
    await until(async () => (await page.getByTestId('db-view').innerText()) === 'View 5');
    const findMs = Date.now() - t;
    await page.getByTestId('db-mpeg').click();
    await until(async () => (await page.getByTestId('db-view').innerText()) === 'View 4');
    const q = lastCall(db, /^\/databank\/items$/).query;
    check('F-DATABANK-6: type works together with Find and Mpeg', q.category === 'lively' && q.search === 'Artist 00' && q.files === 'mpeg' && (await page.getByTestId('db-view').innerText()) === 'View 4');
    check('F-DATABANK-5: ALL is not pressed while something filters', (await page.getByRole('button', { name: 'ALL' }).getAttribute('aria-pressed')) === 'false');
    // F-DATABANK-29: tab change keeps Find and Mpeg, clears type, resets sort; Edit mode stays on
    await head(page, 'Length').click();
    await page.getByTestId('db-edit-mode').click();
    await page.getByRole('tab', { name: 'Jingles' }).click();
    await sleep(400);
    const jq = lastCall(db, /^\/databank\/items$/).query;
    check('F-DATABANK-29: Jingles by title, type cleared, Find and Mpeg kept, Edit mode on',
      jq.asset_type === 'jingle' && jq.sort === 'title' && !jq.category && jq.search === 'Artist 00' && jq.files === 'mpeg'
      && (await page.getByTestId('db-find').inputValue()) === 'Artist 00' && (await page.getByTestId('db-edit-mode').innerText()).includes('Editing'),
      JSON.stringify(jq));
    check('F-DATABANK-43: "Nothing matches." when Find finds nothing', await page.getByText('Nothing matches.').isVisible());
    await page.getByRole('tab', { name: 'Songs' }).click();
    await sleep(300);
    check('F-DATABANK-29: Songs by artist again, page 1', lastCall(db, /^\/databank\/items$/).query.sort === 'artist' && /Page 1\b/.test(await status(page)));
    // F-DATABANK-5: ALL clears Find, type, Mpeg/Prsnt and puts the cursor in Find
    await page.getByRole('button', { name: 'ALL' }).click();
    await until(async () => (await page.getByTestId('db-view').innerText()) === 'View 450');
    const focused = await page.evaluate(() => document.activeElement?.getAttribute('data-testid'));
    check('F-DATABANK-5: ALL cleared Find, type and Mpeg, cursor in Find, ALL pressed',
      (await page.getByTestId('db-find').inputValue()) === '' && (await typeSel.inputValue()) === '' && (await page.getByTestId('db-mpeg').getAttribute('aria-pressed')) === 'false'
      && focused === 'db-find' && (await page.getByRole('button', { name: 'ALL' }).getAttribute('aria-pressed')) === 'true');
    // F-DATABANK-25: Mpeg, Prsnt, page change, tab change within 500 ms
    const timings = {};
    timings.find = findMs;
    timings.type = ms;
    timings.prsnt = await timeClick(page, page.getByTestId('db-present'), `() => document.querySelector('[data-testid=db-present]').getAttribute('aria-pressed') === 'true' && !document.querySelector('[data-testid=db-table][aria-busy]')`);
    timings.page = await timeClick(page, page.getByLabel('Next page'), `() => document.querySelector('[data-testid=db-status]').textContent.includes('Page 2') && !document.querySelector('[data-testid=db-table][aria-busy]')`);
    timings.tab = await timeClick(page, page.getByRole('tab', { name: 'Spots' }), `() => document.querySelectorAll('[data-testid=db-table] [data-row]').length > 0 && document.querySelector('.qn-title').textContent.includes('Spots.db')`);
    check('F-DATABANK-25: Find, type, Prsnt, page and tab changes show rows within 500 ms', Object.values(timings).every((v) => v >= 0 && v < 500), JSON.stringify(timings));
    await ctx.close();
  },

  // F-DATABANK-8: go to record
  async gotoRecord() {
    for (const [name, view] of [['desktop', DESKTOP], ['phone', PHONE]]) {
      const { ctx, page } = await open(view);
      await waitRows(page);
      await page.getByTestId('db-rec').click();
      await page.getByTestId('db-goto').fill('450');
      await page.getByTestId('db-goto').press('Enter');
      await until(async () => /Rec 450/.test(await status(page)));
      const st = await status(page);
      const inView = await page.locator('[data-row="s450"]').evaluate((el) => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= window.innerHeight; }).catch(() => false);
      check(`F-DATABANK-8 ${name}: record 450 loads page 3, selected and in view`, /Page 3 of 3/.test(st) && /Rec 450/.test(st) && inView, st);
      for (const bad of ['0', '-3', '451']) {
        await page.getByTestId('db-rec').click();
        await page.getByTestId('db-goto').fill(bad);
        await page.getByTestId('db-goto').press('Enter');
        check(`F-DATABANK-8 ${name}: ${bad} says "There are 450 records here."`, (await noticeText(page)).includes('There are 450 records here.'));
      }
      await page.getByTestId('db-rec').click();
      await page.getByTestId('db-goto').fill('3');
      await page.getByTestId('db-goto').press('Escape');
      check(`F-DATABANK-8 ${name}: Esc cancels`, !(await page.getByTestId('db-goto').isVisible()) && /Rec 450/.test(await status(page)));
      await page.getByTestId('db-rec').click();
      await page.getByTestId('db-goto').fill('3');
      await page.getByTestId('db-find').click();
      check(`F-DATABANK-8 ${name}: leaving the box cancels`, !(await page.getByTestId('db-goto').isVisible()) && /Rec 450/.test(await status(page)));
      await ctx.close();
    }
  },

  // F-DATABANK-10, 32, 33, 48: pre-listen and keys
  async prelisten() {
    const { ctx, page, db, errors } = await open(DESKTOP, { db: { brokenS2: true } });
    await waitRows(page);
    const tool = page.getByLabel('Pre-listen to the selected item');
    check('F-DATABANK-10: toolbar 🎧 greyed until a row is selected', await tool.isDisabled());
    await page.locator('[data-row="s1"]').click();
    check('F-DATABANK-10: toolbar 🎧 on after selecting', !(await tool.isDisabled()));
    const before = writes(db).length;
    await tool.click();
    await until(async () => (await page.locator('[data-row="s1"]').getAttribute('class')).includes('outline-[#2bff4a]'));
    check('F-DATABANK-10: the row playing has a green outline', (await page.locator('[data-row="s1"]').getAttribute('class')).includes('outline-[#2bff4a]'));
    check('F-DATABANK-10: plays in the PFL deck', await page.getByTestId('pfl-deck').isVisible());
    await sleep(500);
    check('F-DATABANK-10: nothing sent to the station (no queue or now-playing change)', writes(db).length === before
      && !db.calls.some((c) => /queue|now-playing/.test(c.path) && c.method !== 'GET'));
    // double-click another row
    await page.locator('[data-row="s4"]').dblclick();
    await until(async () => lastCall(db, /audio-url$/)?.path === '/assets/s4/audio-url');
    check('F-DATABANK-10/33: double-click pre-listens (no Edit mode)', lastCall(db, /audio-url$/)?.path === '/assets/s4/audio-url');
    // F-DATABANK-32: arrows and Space
    await page.locator('[data-row="s5"]').click();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowUp');
    const sel = await page.locator('.qn-row-cur').getAttribute('data-row');
    check('F-DATABANK-32: ↑ ↓ move the selection', sel === 's6', sel);
    await page.keyboard.press('Space');
    await until(async () => lastCall(db, /audio-url$/)?.path === '/assets/s6/audio-url');
    check('F-DATABANK-32: Space pre-listens the selected item', lastCall(db, /audio-url$/)?.path === '/assets/s6/audio-url');
    for (let i = 0; i < 40; i++) await page.keyboard.press('ArrowDown');
    const kept = await page.locator('.qn-row-cur').evaluate((el) => { const r = el.getBoundingClientRect(); const t = document.querySelector('[data-testid=db-table]').getBoundingClientRect(); return r.top >= t.top && r.bottom <= t.bottom; });
    check('F-DATABANK-32: the selection stays in view', kept);
    // keys do nothing while editing or while the go-to box is open
    await page.getByTestId('db-edit-mode').click();
    await page.locator('[data-row="s7"] > span').nth(1).click();
    await page.keyboard.press('ArrowDown');
    check('F-DATABANK-32: arrows do nothing while a cell is edited', (await page.locator('.qn-row-cur').getAttribute('data-row')) === 's7' && await page.getByTestId('db-edit-input').isVisible());
    // F-DATABANK-33: turning Edit mode off closes the cell without saving
    await page.getByTestId('db-edit-input').fill('Not saved');
    const w0 = writes(db).length;
    await page.getByTestId('db-edit-mode').click();
    await sleep(300);
    check('F-DATABANK-33: Edit mode off closes the cell without saving', !(await page.getByTestId('db-edit-input').isVisible()) && writes(db).length === w0, `${writes(db).length - w0} writes`);
    await page.getByTestId('db-rec').click();
    const callsBefore = db.calls.length;
    await page.keyboard.press('Space');
    await page.keyboard.press('ArrowDown');
    check('F-DATABANK-32: keys do nothing while the go-to box is open', db.calls.length === callsBefore && await page.getByTestId('db-goto').isVisible());
    await page.keyboard.press('Escape');
    // Space on Blocks does nothing
    await page.getByRole('tab', { name: 'Blocks' }).click();
    await waitRows(page);
    await rows(page).first().click();
    const n0 = db.calls.filter((c) => /audio-url/.test(c.path)).length;
    await page.keyboard.press('Space');
    await sleep(300);
    check('F-DATABANK-32: Space does not pre-listen on Blocks', db.calls.filter((c) => /audio-url/.test(c.path)).length === n0);
    // F-DATABANK-48: a broken file shows the deck's error and the page keeps working
    await page.getByRole('tab', { name: 'Songs' }).click();
    await waitRows(page);
    await page.locator('[data-row="s2"]').dblclick();
    const shown = await until(async () => page.getByTestId('pfl-error').isVisible(), 6000);
    check('F-DATABANK-48: a broken file shows the PFL deck error', shown, shown ? await page.getByTestId('pfl-error').innerText() : '');
    await head(page, 'Title').click();
    await until(async () => lastCall(db, /^\/databank\/items$/).query.sort === 'title');
    check('F-DATABANK-48: the page keeps working after it', (await rows(page).count()) === 200 && errors.length === 0, errors.join(' | '));
    await ctx.close();

    // phone: 🎧 on each card
    const p = await open(PHONE);
    await waitRows(p.page);
    const cardBtn = p.page.getByRole('button', { name: /^Pre-listen Title 999$/ });
    await cardBtn.tap();
    await until(async () => lastCall(p.db, /audio-url$/)?.path === '/assets/s1/audio-url');
    check('F-DATABANK-10 phone: the 🎧 on a card pre-listens that item', lastCall(p.db, /audio-url$/)?.path === '/assets/s1/audio-url' && writes(p.db).length === 0);
    await p.ctx.close();
  },

  // F-DATABANK-11, 12, 26, 33, 34, 35, 51: editing
  async editing() {
    const { ctx, page, db } = await open(DESKTOP);
    await waitRows(page);
    const edit = page.getByTestId('db-edit-mode');
    check('F-DATABANK-11: "Edit mode" button', (await edit.innerText()).trim() === 'Edit mode');
    // without Edit mode a click only selects
    await page.locator('[data-row="s1"] > span').nth(1).click();
    check('F-DATABANK-33: without Edit mode a click only selects', !(await page.getByTestId('db-edit-input').isVisible()) && (await page.locator('.qn-row-cur').getAttribute('data-row')) === 's1');
    await edit.click();
    check('F-DATABANK-11: turns to "✎ Editing" with a yellow hint line', (await edit.innerText()).includes('✎ Editing') && await page.getByText(/Edit mode: click an artist, title, type, V or intro/).isVisible());
    const cellOf = (id, i) => page.locator(`[data-row="${id}"] > span`).nth(i);
    // sort by Length, which these edits do not change, so the edited rows stay on page 1
    await head(page, 'Length').click();
    await until(async () => lastCall(db, /^\/databank\/items$/).query.sort === 'length');
    const saveVia = async (id, i, value, key = 'Enter') => {
      await cellOf(id, i).click();
      await page.getByTestId('db-edit-input').fill(value);
      if (key === 'blur') await page.getByTestId('db-find').click(); else await page.getByTestId('db-edit-input').press(key);
    };
    // F-DATABANK-33: a click selects the row and opens only that cell
    await cellOf('s3', 3).click();
    check('F-DATABANK-33: click in Edit mode selects the row and opens only that cell', (await page.locator('.qn-row-cur').getAttribute('data-row')) === 's3' && (await page.getByTestId('db-edit-input').count()) === 1
      && (await cellOf('s3', 3).locator('input').count()) === 1);
    await page.getByTestId('db-edit-input').press('Escape');
    const fields = [[0, 'artist', 'New Artist', 'artist', 'New Artist'], [1, 'title', 'New Title', 'title', 'New Title'], [2, 'type', 'slow', 'category', 'slow'],
      [3, 'vote', '42', 'vote', 42], [5, 'intro', '8', 'intro_seconds', 8]];
    for (const [i, name, value, key, sent] of fields) {
      const n0 = writes(db).length;
      await saveVia('s3', i, value);
      const ok = await until(async () => (await noticeText(page)) === 'Saved.');
      const w = writes(db).slice(n0);
      check(`F-DATABANK-11: ${name} saved with Enter (PATCH /assets/{id})`, ok && w.length === 1 && w[0].path === '/assets/s3' && w[0].body[key] === sent, JSON.stringify(w.map((x) => x.body)));
    }
    await until(async () => (await page.locator('[data-row="s3"]').innerText()).includes('New Title'));
    check('F-DATABANK-11: the row shows the new values', /New Artist/.test(await page.locator('[data-row="s3"]').innerText()) && /\b42\b/.test(await page.locator('[data-row="s3"]').innerText()));
    // F-DATABANK-26: save confirmed within 1 s
    const t0 = Date.now();
    await cellOf('s5', 3).click();
    await page.getByTestId('db-edit-input').fill('56');
    await page.getByTestId('db-edit-input').press('Enter');
    await until(async () => (await noticeText(page)) === 'Saved.' && (await cellOf('s5', 3).innerText()).trim() === '56');
    check('F-DATABANK-26: Edit-mode save confirmed within 1 s', Date.now() - t0 < 1000, `${Date.now() - t0} ms`);
    // "Saved." goes after 5 s
    await sleep(5300);
    check('F-DATABANK-11: "Saved." shows for 5 s', (await noticeText(page)) === '');
    // Esc cancels, leaving unchanged sends nothing, leaving changed saves
    let n0 = writes(db).length;
    await saveVia('s6', 1, 'Escaped', 'Escape');
    await cellOf('s6', 1).click();
    await page.getByTestId('db-find').click();
    check('F-DATABANK-11: Esc cancels and leaving an unchanged box sends nothing', writes(db).length === n0 && !(await page.locator('[data-row="s6"]').innerText()).includes('Escaped'));
    await saveVia('s6', 1, 'Left box', 'blur');
    await until(async () => writes(db).length === n0 + 1);
    check('F-DATABANK-11: leaving a changed box saves', writes(db)[n0]?.body?.title === 'Left box');
    // F-DATABANK-12: checks before sending
    const refused = [['vote', 3, '101', 'V is a whole number from 0 to 100.'], ['vote', 3, '4.5', 'V is a whole number'], ['vote', 3, '', 'V is a whole number'],
      ['intro', 5, '601', 'Intro is seconds, 0 to 600.'], ['intro', 5, '-1', 'Intro is seconds'], ['title', 1, '   ', 'The title cannot be empty.']];
    for (const [name, i, value, msg] of refused) {
      await sleep(50);
      n0 = writes(db).length;
      await saveVia('s8', i, value);
      await sleep(150);
      const notice = page.locator('[role="status"]');
      check(`F-DATABANK-12: ${name} "${value}" refused with a red notice, nothing sent`, writes(db).length === n0 && (await noticeText(page)).includes(msg) && /bg-red/.test(await notice.getAttribute('class')), await noticeText(page));
    }
    for (const [name, i, key] of [['intro', 5, 'intro_seconds'], ['artist', 0, 'artist'], ['type', 2, 'category']]) {
      n0 = writes(db).length;
      await saveVia('s9', i, '');
      await until(async () => writes(db).length === n0 + 1);
      check(`F-DATABANK-12: ${name} left empty saves as none`, writes(db)[n0]?.body?.[key] === null, JSON.stringify(writes(db)[n0]?.body));
    }
    // F-DATABANK-35: refused by the server, and no connection
    db.refuse['/assets/s10'] = { status: 422, detail: 'That title is already used by another item.' };
    const oldTitle = (await cellOf('s10', 1).innerText()).trim();
    await saveVia('s10', 1, 'Clash');
    await until(async () => (await noticeText(page)).includes('already used'));
    check('F-DATABANK-35: a refused save shows the server\'s words and the old value', (await noticeText(page)) === 'That title is already used by another item.' && (await cellOf('s10', 1).innerText()).trim() === oldTitle);
    await ctx.route('**/api/v1/assets/s11', (r) => r.abort('connectionrefused'));
    const oldT11 = (await cellOf('s11', 1).innerText()).trim();
    await saveVia('s11', 1, 'Offline');
    await until(async () => (await noticeText(page)).includes('Could not save'));
    check('F-DATABANK-35: no connection says "Could not save that change." and keeps the old value', (await noticeText(page)) === 'Could not save that change.' && (await cellOf('s11', 1).innerText()).trim() === oldT11);
    // F-DATABANK-38: spot category edit
    await page.getByRole('tab', { name: 'Spots' }).click();
    await waitRows(page);
    n0 = writes(db).length;
    await page.locator('[data-row="sp1"] > span').nth(1).click();
    await page.getByTestId('db-edit-input').fill('bakery');
    await page.getByTestId('db-edit-input').press('Enter');
    await until(async () => writes(db).length === n0 + 1);
    check('F-DATABANK-38: spot category saves like a song\'s Type', writes(db)[n0]?.path === '/assets/sp1' && writes(db)[n0]?.body?.category === 'bakery');
    await ctx.close();

    // F-DATABANK-51: while a new sort loads, the old rows cannot be edited
    let slowNow = false;
    const slow = await open(DESKTOP, { delay: (p) => (p === '/databank/items' && slowNow ? 1500 : 0) });
    await waitRows(slow.page);
    const totalCalls = () => slow.db.calls.filter((c) => c.path === '/databank/items' && c.query.limit === '1').length;
    const tc0 = totalCalls();
    await slow.page.getByTestId('db-edit-mode').click();
    slowNow = true;
    await head(slow.page, 'Title').click();
    await sleep(150);
    await slow.page.locator('[data-row="s1"] > span').nth(1).click();
    check('F-DATABANK-51: old rows stay on screen but are not editable while the new sort loads',
      (await rows(slow.page).count()) > 0 && !(await slow.page.getByTestId('db-edit-input').isVisible()) && (await slow.page.getByTestId('db-table').getAttribute('aria-busy')) === 'true');
    slowNow = false;
    await until(async () => (await slow.page.getByTestId('db-table').getAttribute('aria-busy')) === null, 4000);
    check('F-DATABANK-51: Tot is cached (not asked again on a sort)', totalCalls() === tc0, `${totalCalls() - tc0} more`);
    await slow.ctx.close();

    // F-DATABANK-11 / 34: phone editing
    const ph = await open(PHONE);
    await waitRows(ph.page);
    await ph.page.getByTestId('db-edit-mode').tap();
    check('F-DATABANK-11 phone: hint says tap', await ph.page.getByText(/Edit mode: tap an artist/).isVisible());
    const card = ph.page.locator('[data-row="s1"]');
    await card.getByText('Title 999').tap();
    const input = ph.page.getByTestId('db-edit-input');
    const fs = await input.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    check('F-DATABANK-34: phone edit box text is 16 px or more (no iPhone zoom)', fs >= 16, `${fs}px`);
    await input.fill('Phone title');
    await input.press('Enter');
    await until(async () => writes(ph.db).some((w) => w.body?.title === 'Phone title'));
    check('F-DATABANK-11 phone: Title edited by tapping the card', writes(ph.db).some((w) => w.path === '/assets/s1' && w.body?.title === 'Phone title'));
    await card.getByText('lively').tap();
    await input.fill('slow');
    await input.press('Enter');
    await card.getByText(/^V /).tap();
    await input.fill('12');
    await input.press('Enter');
    await until(async () => writes(ph.db).length >= 3);
    check('F-DATABANK-34: Type and V can be tapped on a card', writes(ph.db).some((w) => w.body?.category === 'slow') && writes(ph.db).some((w) => w.body?.vote === 12));
    const n1 = writes(ph.db).length;
    const artistLine = card.locator('span.block.text-xs', { hasText: 'Artist 001' });
    const al = await artistLine.boundingBox();
    const hit = await ph.page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.textContent?.slice(0, 20), [al.x + 4, al.y + al.height / 2]);
    await ph.page.touchscreen.tap(al.x + 4, al.y + al.height / 2);
    // (Chrome's touch adjustment may send a tap beside the title to the title: that opens the Title box, never an Artist one)
    check('F-DATABANK-34: Artist (and Intro, not on the card) cannot be edited on a phone',
      !((await input.isVisible()) && (await input.inputValue()) === 'Artist 001') && writes(ph.db).length === n1,
      `${await input.isVisible() ? `box open: ${await input.inputValue()}` : ''} ${writes(ph.db).length - n1} writes, tapped "${hit}"`);
    await ph.ctx.close();
  },

  // F-DATABANK-13, 42: roles
  async roles() {
    for (const role of ['dj', 'viewer']) {
      const { ctx, page, db } = await open(DESKTOP, { role });
      await waitRows(page);
      check(`F-DATABANK-13 ${role}: page opens with the View only marker and no Edit mode`, await page.getByTestId('view-only').isVisible() && (await page.getByTestId('db-edit-mode').count()) === 0);
      await page.getByRole('tab', { name: 'Spots' }).click();
      await waitRows(page);
      await page.locator('[data-row="sp1"]').getByTestId('spot-end').click();
      check(`F-DATABANK-13 ${role}: no spot end-date edit`, !(await page.getByTestId('db-edit-input').isVisible()));
      await page.getByRole('tab', { name: 'Blocks' }).click();
      await sleep(400);
      check(`F-DATABANK-42 ${role}: "Blocks are for managers." and no request`, await page.getByText('Blocks are for managers.').isVisible() && !db.calls.some((c) => c.path === '/program-blocks'));
      await ctx.close();
    }
    for (const role of ['admin', 'manager']) {
      const { ctx, page } = await open(DESKTOP, { role });
      await waitRows(page);
      check(`F-DATABANK-13 ${role}: Edit mode offered, no View only marker`, (await page.getByTestId('db-edit-mode').count()) === 1 && (await page.getByTestId('view-only').count()) === 0);
      await ctx.close();
    }
    const sp = await open(DESKTOP, { role: 'sponsor' });
    await sleep(1200);
    check('F-DATABANK-13: a sponsor is sent away from the page', !sp.page.url().includes('/admin/databank') && (await sp.page.locator('[data-testid=databank]').count()) === 0, sp.page.url());
    await sp.ctx.close();
    const out = await open(DESKTOP, { storage: { access_token: null, refresh_token: null } });
    await out.ctx.addInitScript(() => { localStorage.removeItem('access_token'); localStorage.removeItem('refresh_token'); });
    await out.page.goto(`${BASE}/admin/databank`);
    await sleep(1200);
    check('F-DATABANK-13: signed out goes to the login page', /\/admin\/login/.test(out.page.url()), out.page.url());
    await out.ctx.close();
  },

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

  // F-DATABANK-21, 42 (empty), 43: blocks and empty states
  async blocks() {
    for (const [name, view] of [['desktop', DESKTOP], ['phone', PHONE]]) {
      const { ctx, page } = await open(view, { path: '/admin/databank?tab=blocks' });
      await waitRows(page);
      const ids = await rows(page).evaluateAll((rs) => rs.map((r) => r.getAttribute('data-row')));
      check(`F-DATABANK-21 ${name}: one row per clock time, by time then name`, ids.join(',') === 'b2-07:00,b3-07:00,b1-07:00,b1-08:00', ids.join(','));
      const wx = await page.locator('[data-row="b1-07:00"]').innerText();
      check(`F-DATABANK-21 ${name}: weekdays Sunday-first ("012345" Sunday to Friday), Recs, hard`, /012345/.test(wx) && /\b3\b/.test(wx) && /hard/.test(wx), wx.replace(/\s+/g, ' '));
      check(`F-DATABANK-21 ${name}: yellow dot (Flg) on hard blocks only`, (await page.locator('[data-row="b1-07:00"] [aria-label="Hard"]').count()) === 1 && (await page.locator('[data-row="b3-07:00"] [aria-label="Hard"]').count()) === 0);
      const psa = page.locator('[data-row="b2-07:00"]');
      check(`F-DATABANK-21 ${name}: "soft, N min", inactive greyed with (off)`, /soft, 5 min/.test(await psa.innerText()) && /\(off\)/.test(await psa.innerText()) && /opacity-50/.test(await psa.getAttribute('class')));
      check(`F-DATABANK-21 ${name}: plain soft; Friday = 6`, /soft\b/.test(await page.locator('[data-row="b3-07:00"]').innerText()) && /\b6\b/.test(await page.locator('[data-row="b3-07:00"]').innerText()));
      await page.getByTestId('db-find').fill('weather');
      await until(async () => (await rows(page).count()) === 2);
      check(`F-DATABANK-21 ${name}: Find filters by name`, (await rows(page).count()) === 2);
      check(`F-DATABANK-21 ${name}: "Change blocks" opens Program Blocks`, (await page.getByRole('link', { name: 'Change blocks' }).getAttribute('href')) === '/admin/program-blocks');
      await ctx.close();
    }
    const empty = await open(DESKTOP, { path: '/admin/databank?tab=blocks', db: { blocks: [], songs: 0, spots: [] } });
    check('F-DATABANK-42: no blocks: "No blocks on this station yet."', await until(async () => empty.page.getByText('No blocks on this station yet.').isVisible()));
    await empty.page.getByRole('tab', { name: 'Songs' }).click();
    check('F-DATABANK-43: "Nothing here yet." on an empty table', await until(async () => empty.page.getByText('Nothing here yet.').isVisible()));
    await empty.page.getByRole('tab', { name: 'Spots' }).click();
    check('F-DATABANK-43: "No spots in the library yet."', await until(async () => empty.page.getByText('No spots in the library yet.').isVisible()));
    await empty.ctx.close();
    const slow = await open(DESKTOP, { delay: (p) => (p === '/databank/items' ? 1500 : 0) });
    check('F-DATABANK-43: "Loading…" while the first rows come', await until(async () => slow.page.getByText('Loading…').isVisible(), 1200));
    await slow.ctx.close();
  },

  // F-DATABANK-22, 44, 45: the day log
  async log() {
    const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date());
    const t = (h, m, s, ms) => { const [y, mo, d] = today.split('-').map(Number); return new Date(Date.UTC(y, mo - 1, d, h + 5, m, s, ms)).toISOString(); };
    const logs = {
      [today]: [
        { id: 'l1', asset_id: 'a1', title: 'Gadol Hashem', artist: 'Shalsheles', asset_type: 'music', file: 'gadol.mp3', start_utc: t(1, 0, 0, 0), end_utc: t(1, 3, 0, 250), source: 'scheduler' },
        { id: 'l2', asset_id: 'a2', title: 'Bagel King', artist: null, asset_type: 'spot', file: 'bagel.mp3', start_utc: t(1, 3, 0, 250), end_utc: t(1, 3, 30, 0), source: 'scheduler' },
        { id: 'l3', asset_id: null, title: 'Unknown', artist: null, asset_type: null, file: '', start_utc: t(1, 4, 0, 0), end_utc: t(1, 5, 0, 0), source: 'scheduler' },
        { id: 'l4', asset_id: 'a1', title: 'On air now', artist: 'Avi', asset_type: 'music', file: 'onair.mp3', start_utc: t(1, 6, 0, 7), end_utc: null, source: 'scheduler' },
      ],
    };
    for (const [name, view] of [['desktop', DESKTOP], ['phone', PHONE]]) {
      const { ctx, page, db } = await open(view, { path: '/admin/databank?tab=log', db: { logs } });
      const lines = page.locator('[data-testid="daylog-lines"] button');
      await lines.first().waitFor({ timeout: 8000 });
      const texts = (await lines.allInnerTexts()).map((s) => s.replace(/\s+/g, ' ').trim());
      check(`F-DATABANK-22 ${name}: Started and Ended lines, HH:MM:SS.mmm station time, oldest first`, texts[0].startsWith('01:00:00.000') && /Started$/.test(texts[0]) && texts.length === 7, texts.join(' | '));
      check(`F-DATABANK-44 ${name}: Ended before Started on the same millisecond`, /^01:03:00\.250 Shalsheles - Gadol Hashem \[gadol\.mp3\] Ended$/.test(texts[1]) && /^01:03:00\.250 Bagel King \[bagel\.mp3\] Started$/.test(texts[2]), `${texts[1]} | ${texts[2]}`);
      check(`F-DATABANK-22 ${name}: a spot's end reads "Spot ended"`, /Spot ended$/.test(texts[3]));
      check(`F-DATABANK-44 ${name}: a deleted item reads Unknown`, /^01:04:00\.000 Unknown Started$/.test(texts[4]));
      check(`F-DATABANK-44 ${name}: an item still on air has a Started line only`, texts.filter((x) => x.includes('On air now')).length === 1 && /Avi - On air now \[onair\.mp3\] Started$/.test(texts[6]));
      await lines.nth(2).click();
      check(`F-DATABANK-44 ${name}: a tapped line is highlighted`, /bg-\[#000080\]/.test(await lines.nth(2).getAttribute('class')));
      check(`F-DATABANK-9 ${name}: the Log tab has no status bar`, (await page.getByTestId('db-status').count()) === 0);
      if (name === 'phone') {
        const h = await lines.first().evaluate((el) => el.getBoundingClientRect().height);
        check('F-DATABANK-63 phone: a day-log line is at least 44 px tall', h >= 44, `${h}px`);
        check('F-DATABANK-22 phone: days in a drop-down', await page.getByTestId('daylog-day').isVisible() && (await page.getByTestId('daylog-day').locator('option').count()) === 14);
        await page.getByTestId('daylog-day').selectOption({ index: 1 });
      } else {
        check('F-DATABANK-22 desktop: the last 14 days in a list on the left', (await page.locator('[data-testid="daylog-days"] button').count()) === 14);
        await page.locator('[data-testid="daylog-days"] button').nth(1).click();
      }
      check(`F-DATABANK-22 ${name}: "Nothing aired on <day>." for an empty day`, await until(async () => page.getByText(/^Nothing aired on .+\.$/).isVisible()));
      void db;
      await ctx.close();
    }
    // error and the 15 s refresh of today
    const err = await open(DESKTOP, { path: '/admin/databank?tab=log', db: { logs: { __error: true } } });
    check('F-DATABANK-22: "Can\'t load this day\'s log." on an error', await until(async () => err.page.getByText("Can't load this day's log.").isVisible(), 15000));
    await err.ctx.close();
    const r = await open(DESKTOP, { path: '/admin/databank?tab=log', db: { logs } });
    await r.page.locator('[data-testid="daylog-lines"] button').first().waitFor();
    const count = () => r.db.calls.filter((c) => c.path === '/databank/log' && c.query.day === today).length;
    const c0 = count();
    await sleep(16000);
    check('F-DATABANK-22: today refreshes every 15 s', count() - c0 >= 1, `${count() - c0} refreshes in 16 s`);
    await r.page.locator('[data-testid="daylog-days"] button').nth(1).click();
    const old = () => r.db.calls.filter((c) => c.path === '/databank/log' && c.query.day !== today).length;
    const o0 = old();
    await sleep(16000);
    check('F-DATABANK-22: an older day is not refreshed', old() === o0);
    await r.ctx.close();

    // F-DATABANK-45: station calendar, not the computer's; the page moves on at station midnight
    const cal = await open(DESKTOP, {
      path: null, ctxExtra: { timezoneId: 'Asia/Jerusalem' },
      beforeGoto: (p) => p.clock.install({ time: new Date('2026-11-17T04:59:00Z') }), // 23:59 New York, 06:59 Jerusalem
    });
    await cal.page.goto(`${BASE}/admin/databank?tab=log`);
    const first = () => cal.page.locator('[data-testid="daylog-days"] button').first().innerText();
    await cal.page.locator('[data-testid="daylog-days"] button').first().waitFor();
    check('F-DATABANK-45: a browser in Israel sees the New York station\'s day', await until(async () => (await first()).startsWith('Nov 16, 2026'), 4000), await first());
    await cal.page.clock.fastForward(120_000); // 00:01 New York
    check('F-DATABANK-45: a page left open over station midnight moves on to the new day', await until(async () => (await first()).startsWith('Nov 17, 2026'), 3000), await first());
    const lastDay = [...cal.db.calls].reverse().find((c) => c.path === '/databank/log');
    check('F-DATABANK-45: and shows the new day\'s log', lastDay?.query.day === '2026-11-17', lastDay?.query.day);
    await cal.ctx.close();
  },

  // F-DATABANK-30, 31, 47
  async stationAndAddress() {
    const { ctx, page, db } = await open(DESKTOP, { storage: { radio_last_station: 'st-2' } });
    await waitRows(page);
    const picker = page.getByLabel('Station');
    check('F-DATABANK-30: starts on the station last used in the control room', (await picker.inputValue()) === 'st-2' && lastCall(db, /^\/databank\/items$/).query.station_id === 'st-2');
    await rows(page).first().click();
    await picker.selectOption('st-1');
    await until(async () => lastCall(db, /^\/databank\/items$/).query.station_id === 'st-1');
    check('F-DATABANK-30: a change reloads Last played for that station and clears the selection', lastCall(db, /^\/databank\/items$/).query.station_id === 'st-1' && (await page.locator('.qn-row-cur').count()) === 0);
    await page.getByRole('tab', { name: 'Spots' }).click();
    await until(async () => lastCall(db, /^\/databank\/spots$/)?.query.station_id === 'st-1');
    await picker.selectOption('st-2');
    await until(async () => lastCall(db, /^\/databank\/spots$/)?.query.station_id === 'st-2');
    check('F-DATABANK-30: Spots end dates follow the station', lastCall(db, /^\/databank\/spots$/)?.query.station_id === 'st-2');
    await page.getByRole('tab', { name: 'Blocks' }).click();
    await until(async () => lastCall(db, /^\/program-blocks$/)?.query.station_id === 'st-2');
    check('F-DATABANK-30: Blocks follow the station', lastCall(db, /^\/program-blocks$/)?.query.station_id === 'st-2');
    await page.getByRole('tab', { name: 'Log' }).click();
    await until(async () => lastCall(db, /^\/databank\/log$/)?.query.station_id === 'st-2');
    check('F-DATABANK-30: Log follows the station', lastCall(db, /^\/databank\/log$/)?.query.station_id === 'st-2');
    // F-DATABANK-47: Control room goes back on the same station
    await page.getByRole('tab', { name: 'Songs' }).click();
    await page.getByRole('link', { name: 'Control room' }).click();
    await page.waitForURL(/\/admin\/dashboard/);
    await sleep(800);
    const stored = await page.evaluate(() => localStorage.getItem('radio_last_station'));
    check('F-DATABANK-47: "Control room" goes back to the control room on the same station', stored === 'st-2', stored);
    await ctx.close();

    const gone = await open(DESKTOP, { storage: { radio_last_station: 'st-deleted' } });
    await waitRows(gone.page);
    check('F-DATABANK-30: a remembered station that no longer exists falls back to the first', await until(async () => (await gone.page.getByLabel('Station').inputValue()) === 'st-1'));
    await gone.ctx.close();

    for (const [tab, label] of [['log', 'Log'], ['songs', 'Songs'], ['jingles', 'Jingles'], ['spots', 'Spots'], ['blocks', 'Blocks'], ['nonsense', 'Songs']]) {
      const o = await open(DESKTOP, { path: `/admin/databank?tab=${tab}` });
      await o.page.getByRole('tab', { name: label }).waitFor();
      check(`F-DATABANK-31: ?tab=${tab} opens ${label}`, (await o.page.getByRole('tab', { name: label }).getAttribute('aria-selected')) === 'true');
      await o.ctx.close();
    }

    const sp = await open(DESKTOP, { path: '/admin/databank?tab=spots' });
    await waitRows(sp.page);
    await sp.page.getByRole('link', { name: 'Spot schedule' }).click();
    await sp.page.waitForURL(/\/admin\/spot-schedule/);
    check('F-DATABANK-47: "Spot schedule" opens the Spot Schedule page', sp.page.url().endsWith('/admin/spot-schedule'));
    await sp.page.goto(`${BASE}/admin/databank`);
    await waitRows(sp.page);
    await sp.page.getByTestId('db-report-bug').click();
    check('F-DATABANK-47: 🐞 Report a bug opens the bug reporter', await until(async () => sp.page.getByRole('dialog', { name: 'Report a bug' }).isVisible(), 6000));
    await sp.ctx.close();
  },

  // F-DATABANK-24, 49, 50, 63: phone and tablet layout
  async layout() {
    for (const [name, view] of [['iPhone 13', PHONE], ['iPhone SE', SE]]) {
      const { ctx, page } = await open(view);
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
