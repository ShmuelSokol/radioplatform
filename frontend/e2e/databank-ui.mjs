/**
 * Databank (KBR Studio's database window) against the built site and a FAKE backend
 * (e2e/lib/fake-station.mjs plus a fake /databank API here), so every row of the
 * Databank section of docs/test-checklist.md can be driven without a real station.
 *
 *   npm run build && (npx vite preview --port 4181 &)
 *   BASE_URL=http://localhost:4181 node e2e/databank-ui.mjs      # optional: ONLY=<part,...>
 *
 * Parts: open, songs, edit, pfl, keys, spots, blocks, log, logstation, roles, station, phone, speed.
 */
import {
  chromium, devices, checker, makeStation, adminContext, newPage, sleep, timeClick, timeUntil,
} from './lib/fake-station.mjs';
import { allowEverywhere } from './lib/guard.mjs';

// Provoked on purpose; anything else the screen or console shows fails the run (e2e/lib/guard.mjs)
allowEverywhere(/There are \d+ records here|V is a whole number|Intro is seconds|The title cannot be empty|You may not change this item|is not allowed to do that|Could not save that change|The end date is before the schedule starts/, 'wrong values and refused saves are typed in to see each message');
allowEverywhere(/Can't play this file/, 'an unplayable file is pre-listened to see the message');

const BASE = process.env.BASE_URL || 'http://localhost:4181';
const ONLY = process.env.ONLY || '';
const run = (name) => !ONLY || ONLY.split(',').includes(name);
const { check, done } = checker();

const DESKTOP = { viewport: { width: 1600, height: 900 } };
const IPHONE13 = { ...devices['iPhone 13'], deviceScaleFactor: 1 };
const IPHONE_SE = { ...devices['iPhone SE'], deviceScaleFactor: 1 };
const IPHONE_LAND = { ...devices['iPhone 13 landscape'], deviceScaleFactor: 1 };
const IPAD_MINI = { ...devices['iPad Mini'], deviceScaleFactor: 1 };

/* ── Fake /databank API ─────────────────────────────────────────── */

const pad = (n) => String(n).padStart(2, '0');
const isoDay = (d) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
const nyDay = (d = new Date()) => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit' }).format(d);
const addDays = (day, n) => { const [y, m, d] = day.split('-').map(Number); return isoDay(new Date(Date.UTC(y, m - 1, d + n))); };

function makeDb() {
  const songs = [];
  const cats = ['1 oldie', '1a old hit fast', '2 slow', 'Lively'];
  for (let i = 1; i <= 450; i++) {
    songs.push({
      id: `m${i}`, asset_type: 'music', artist: `Artist ${String(i % 37).padStart(2, '0')}`,
      title: `Song ${String(i).padStart(3, '0')}`, type: i === 420 ? 'rare type' : cats[i % 4],
      vote: (i * 7) % 101, length: 120 + (i % 200), intro: i % 5 ? (i % 30) : null,
      file: i === 3 ? 'three.wav' : i === 4 ? '' : `song${i}.mp3${i === 5 ? '?token=1' : ''}`,
      last_played: i % 3 ? new Date(Date.UTC(2026, 10, 1 + (i % 15), 3, 30)).toISOString() : null,
    });
  }
  songs.find((s) => s.id === 'm6').type = 'do_not_play';
  songs.find((s) => s.id === 'm7').artist = 'Shalsheles';
  songs.find((s) => s.id === 'm8').artist = null;
  songs.find((s) => s.id === 'm420').artist = 'Zz Last';
  // Last played 03:30 UTC Nov 16 = 22:30 on Nov 15 in New York
  songs.find((s) => s.id === 'm1').last_played = '2026-11-16T03:30:00+00:00';
  const jingles = Array.from({ length: 6 }, (_, i) => ({
    id: `j${i + 1}`, asset_type: 'jingle', artist: null, title: `Station ID ${i + 1}`, type: i % 2 ? 'ids' : 'sweepers',
    vote: 50, length: 5 + i, intro: null, file: `id${i + 1}.mp3`, last_played: null,
  }));
  const today = nyDay();
  const spots = [
    { id: 's1', title: 'Bagel King Breakfast', file: 'bagel.mp3', category: 'food', end_date: addDays(today, 20), open_ended: false, schedules: 2, active: true, expired: false, length: 23 },
    { id: 's2', title: 'Fig Flip Pro', file: 'fig.mp3', category: 'phone', end_date: addDays(today, -5), open_ended: false, schedules: 1, active: true, expired: true, length: 34 },
    { id: 's3', title: 'Always On Insurance', file: 'ins.wav', category: 'finance', end_date: null, open_ended: true, schedules: 1, active: true, expired: false, length: 30 },
    { id: 's4', title: 'Unscheduled spot', file: 'loose.mp3', category: 'food', end_date: null, open_ended: false, schedules: 0, active: false, expired: false, length: 15 },
    { id: 's5', title: 'Paused Pizza', file: 'pizza.mp3', category: 'food', end_date: addDays(today, 3), open_ended: false, schedules: 1, active: false, expired: false, length: 28 },
  ];
  const blocks = [
    { id: 'b1', station_id: 'st-1', name: 'Weather Sponsors', is_active: true, days_of_week: '6,0,1,2,3,4', times: '08:00,07:00', timing_mode: 'hard', latest_minutes: null, items: [{}, {}, {}] },
    { id: 'b2', station_id: 'st-1', name: 'Afternoon Mix', is_active: true, days_of_week: '0,1,2,3,4,5,6', times: '07:00', timing_mode: 'soft', latest_minutes: 5, items: [{}] },
    { id: 'b3', station_id: 'st-1', name: 'Old Block', is_active: false, days_of_week: '5', times: '12:00', timing_mode: 'soft', latest_minutes: null, items: [] },
  ];
  return { songs, jingles, spots, blocks, today, patches: [], endDates: [], logReqs: [], typeReqs: 0 };
}

const SORT_KEYS = { artist: 'artist', title: 'title', type: 'type', vote: 'vote', length: 'length', intro: 'intro', file: 'file', last_played: 'last_played' };

function listItems(db, q) {
  let rows = (q.asset_type === 'jingle' ? db.jingles : db.songs).slice();
  if (q.search) { const s = q.search.toLowerCase(); rows = rows.filter((r) => [r.artist, r.title, r.type, r.file].some((x) => (x || '').toLowerCase().includes(s))); }
  if (q.category) rows = rows.filter((r) => (r.type || '').toLowerCase() === q.category.toLowerCase());
  if (q.files === 'mpeg') rows = rows.filter((r) => /\.(mp3|mp2|mpg|mpeg)(\?.*)?$/i.test(r.file));
  if (q.files === 'present') rows = rows.filter((r) => r.file && (r.type || '').toLowerCase() !== 'do_not_play');
  const key = SORT_KEYS[q.sort] || 'artist';
  const desc = q.desc === 'true';
  rows.sort((a, b) => {
    const x = a[key], y = b[key];
    if (x == null && y != null) return 1;
    if (y == null && x != null) return -1;
    let c = x == null ? 0 : x < y ? -1 : x > y ? 1 : 0;
    if (desc) c = -c;
    return c || (a.title < b.title ? -1 : a.title > b.title ? 1 : 0) || (a.id < b.id ? -1 : 1);
  });
  const skip = +(q.skip || 0), limit = +(q.limit || 200);
  return { total: rows.length, items: rows.slice(skip, skip + limit) };
}

function dayLog(db, q) {
  db.logReqs.push({ ...q, at: Date.now() });
  const today = db.today;
  if (q.day === addDays(today, -3)) return { __status: 500, detail: 'boom' };
  if (q.day === addDays(today, -2)) return { day: q.day, timezone: 'America/New_York', logs: [] };
  if (q.day === addDays(today, -1)) {
    return { day: q.day, timezone: 'America/New_York', logs: [
      { id: 'y1', asset_id: 'm2', title: 'Song 002', artist: 'Artist 02', asset_type: 'music', file: 'song2.mp3', start_utc: `${addDays(today, -1)}T15:00:00+00:00`, end_utc: `${addDays(today, -1)}T15:03:00+00:00`, source: 'scheduler' },
    ] };
  }
  // Today (or any later "today" after midnight)
  const T = (h, m, s, ms = '') => `${q.day}T${pad(h)}:${pad(m)}:${pad(s)}${ms}+00:00`;
  return { day: q.day, timezone: 'America/New_York', logs: [
    // A ends in the same millisecond B starts, B's start a few microseconds EARLIER: still Ended first
    { id: 'a', asset_id: 'm1', title: 'Song 001', artist: 'Artist 01', asset_type: 'music', file: 'song1.mp3', start_utc: T(14, 0, 0), end_utc: T(14, 3, 31, '.672900'), source: 'scheduler' },
    { id: 'b', asset_id: 's1', title: 'Bagel King Breakfast', artist: null, asset_type: 'spot', file: 'bagel.mp3', start_utc: T(14, 3, 31, '.672100'), end_utc: T(14, 3, 54, '.500000'), source: 'ad' },
    { id: 'c', asset_id: null, title: 'Unknown', artist: null, asset_type: null, file: '', start_utc: T(14, 3, 54, '.500000'), end_utc: T(14, 6, 0), source: 'scheduler' },
    { id: 'd', asset_id: 'm7', title: 'Song 007', artist: 'Shalsheles', asset_type: 'music', file: 'song7.mp3', start_utc: T(14, 6, 0), end_utc: null, source: 'scheduler' },
  ] };
}

function databankExtra(db, role = () => 'admin') {
  return (method, path, query, body) => {
    if (path === '/auth/me') return { id: 'u1', email: 'operator@example.com', role: role(), is_active: true, station_ids: null };
    if (path === '/databank/items') return listItems(db, query);
    if (path === '/databank/types') {
      db.typeReqs++;
      const rows = query.asset_type === 'jingle' ? db.jingles : db.songs;
      return { types: [...new Set(rows.map((r) => r.type).filter(Boolean))].sort((a, b) => a.localeCompare(b)) };
    }
    if (path === '/databank/spots') return { today: db.today, spots: db.spots };
    if (path === '/databank/log') return dayLog(db, query);
    if (path === '/program-blocks') return db.blocks;
    const pe = /^\/databank\/spots\/([^/]+)\/end-date$/.exec(path);
    if (pe && method === 'PATCH') {
      db.endDates.push({ id: pe[1], body, query });
      const s = db.spots.find((x) => x.id === pe[1]);
      if (body.end_date && body.end_date < '2026-01-01') return { __status: 422, detail: 'The end date is before the schedule starts' };
      s.end_date = body.end_date; s.open_ended = !body.end_date; s.expired = !!body.end_date && body.end_date < db.today;
      return { updated: s.schedules, end_date: body.end_date };
    }
    const pa = /^\/assets\/([^/]+)$/.exec(path);
    if (pa && method === 'PATCH') {
      db.patches.push({ id: pa[1], body, at: Date.now() });
      if (body.title === 'REFUSE') return { __status: 422, detail: [{ loc: ['body', 'title'], msg: 'Value error, The title cannot be empty', type: 'value_error' }] };
      if (body.title === 'NOPE') return { __status: 403, detail: 'You may not change this item' };
      const it = [...db.songs, ...db.jingles, ...db.spots].find((x) => x.id === pa[1]);
      if ('title' in body) it.title = body.title;
      if ('artist' in body) it.artist = body.artist;
      if ('category' in body) { if ('type' in it) it.type = body.category; else it.category = body.category; }
      if ('vote' in body) it.vote = body.vote;
      if ('intro_seconds' in body) it.intro = body.intro_seconds;
      return { id: it.id, title: it.title };
    }
    return undefined;
  };
}

async function open(browser, opts, { role, path = '/admin/databank', storage = {}, db = makeDb(), delay } = {}) {
  let r = role || 'admin';
  const st = makeStation({ extra: databankExtra(db, () => r) });
  const { ctx } = await adminContext(browser, opts, st, { storage: { radio_last_station: 'st-1', ...storage }, delay });
  const { page, errors } = await newPage(ctx);
  page.on('console', (m) => { if (m.type() === 'error' && /Objects are not valid|Uncaught/.test(m.text())) errors.push(m.text().slice(0, 200)); });
  await page.goto(`${BASE}${path}`);
  return { ctx, page, errors, db, st, setRole: (x) => { r = x; } };
}

const rows = (page) => page.locator('[data-testid="db-table"] [role="row"][data-row]');
const rowIds = (page) => rows(page).evaluateAll((rs) => rs.map((r) => r.getAttribute('data-row')));
const num = async (page, id) => Number((await page.getByTestId(id).innerText()).replace(/\D/g, ''));
const head = (page, label) => page.locator('.qn-log-head button', { hasText: new RegExp(`^${label}( [▲▼])?$`) });
const waitView = async (page, n, timeout = 3000) => {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) { if (await num(page, 'db-view') === n) return true; await sleep(40); }
  return false;
};
const waitIds = async (page, pred, timeout = 3000) => {
  const t0 = Date.now();
  let ids = await rowIds(page);
  while (!pred(ids) && Date.now() - t0 < timeout) { await sleep(40); ids = await rowIds(page); }
  return ids;
};
const noSideScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });

/* ── F-DATABANK-1 / 31 / 47: getting there and back ─────────────── */
if (run('open')) {
  const { ctx, page, errors } = await open(browser, DESKTOP, { path: '/admin/dashboard' });
  await page.getByTestId('cr-databank').click();
  await page.waitForURL(/\/admin\/databank$/);
  await rows(page).first().waitFor();
  check('F-DATABANK-1 desktop DB button opens the Databank on Songs', true);
  check('F-DATABANK-1 title bar names the file and the station',
    (await page.locator('.qn-title').innerText()).replace(/\s+/g, ' ').includes('KBR Database - [Songs.db]')
    && (await page.locator('.qn-title').innerText()).includes('Kol Berama'));
  await page.getByRole('tab', { name: 'Spots' }).click();
  check('F-DATABANK-1 file name follows the tab', (await page.locator('.qn-title').innerText()).includes('[Spots.db]'));
  // Spot schedule link (Spots tab), bug reporter, Control room
  await page.getByRole('link', { name: 'Spot schedule' }).click();
  await page.waitForURL(/\/admin\/spot-schedule/);
  check('F-DATABANK-47 "Spot schedule" opens the Spot Schedule page', true);
  await page.goBack();
  await rows(page).first().waitFor();
  await page.getByTestId('db-report-bug').click();
  check('F-DATABANK-47 🐞 opens the bug reporter', await page.getByRole('dialog', { name: 'Report a bug' }).waitFor({ timeout: 8000 }).then(() => true).catch(() => false));
  await page.keyboard.press('Escape');
  await page.goto(`${BASE}/admin/databank`);
  await rows(page).first().waitFor();
  await page.getByRole('link', { name: 'Control room' }).click();
  await page.waitForURL(/\/admin\/dashboard/);
  check('F-DATABANK-47 "Control room" goes back to the control room (same station)', await page.evaluate(() => localStorage.getItem('radio_last_station')) === 'st-1');
  // Content > Databank in the menu of an ordinary page
  await page.goto(`${BASE}/admin/stations`);
  const content = page.getByRole('button', { name: /^Content/ }).first();
  await content.waitFor({ timeout: 8000 }).catch(() => {});
  await content.hover().catch(() => {});
  await page.locator('a[href="/admin/databank"]:visible').first().waitFor({ timeout: 3000 }).catch(() => {});
  const link = page.locator('a[href="/admin/databank"]:visible').first();
  const reached = await link.click({ timeout: 3000 }).then(() => page.waitForURL(/\/admin\/databank/, { timeout: 3000 })).then(() => true).catch(() => false);
  check('F-DATABANK-1 Content > Databank opens it', reached);
  // ?tab=
  for (const [tab, file] of [['log', 'Log'], ['jingles', 'Jingles.db'], ['spots', 'Spots.db'], ['blocks', 'Blocks.db'], ['nonsense', 'Songs.db']]) {
    await page.goto(`${BASE}/admin/databank?tab=${tab}`);
    await page.locator('.qn-title').waitFor();
    check(`F-DATABANK-31 ?tab=${tab} opens ${file}`, (await page.locator('.qn-title').innerText()).includes(`[${file}]`));
  }
  check('F-DATABANK-1 desktop: no script errors', errors.length === 0, errors.join(' | '));
  await ctx.close();

  // Phone: the Played pane's Databank button opens the Log tab
  const ph = await open(browser, IPHONE13, { path: '/admin/dashboard' });
  const played = ph.page.getByRole('button', { name: /More$/ }).first();
  await played.click({ timeout: 8000 }).catch(() => {});
  const btn = ph.page.getByTestId('cr-databank');
  const ok = await btn.click({ timeout: 5000 }).then(() => ph.page.waitForURL(/\/admin\/databank\?tab=log/, { timeout: 5000 })).then(() => true).catch(() => false);
  check('F-DATABANK-1 phone Played pane "Databank" opens the Log tab', ok && (await ph.page.locator('.qn-title').innerText()).includes('[Log]'));
  await ph.ctx.close();
}

/* ── Songs: sort, Find, ALL, type, Mpeg/Prsnt, 123, status bar, tabs ── */
if (run('songs')) {
  const { ctx, page, errors, st, db } = await open(browser, DESKTOP);
  await rows(page).first().waitFor();
  const itemCalls = () => st.calls.filter((c) => c.path === '/databank/items' && c.query.limit !== '1');
  const lastQ = () => itemCalls().at(-1)?.query ?? {};

  // F-DATABANK-2: every column, both directions, on the server
  const cols = [['Artist', 'artist', false], ['Title', 'title', false], ['Type', 'type', false], ['V', 'vote', true],
    ['Length', 'length', false], ['Intro', 'intro', false], ['File', 'file', false], ['Last played', 'last_played', true]];
  for (const [label, key, startsDesc] of cols) {
    // Artist is the starting sort: check it as it opens, then flip it
    if (lastQ().sort !== key) await head(page, label).click();
    await waitIds(page, () => lastQ().sort === key);
    await sleep(150);
    const q1 = lastQ();
    const arrow1 = await head(page, label).innerText();
    const expect1 = listItems(db, { ...q1 }).items.slice(0, 5).map((r) => r.id);
    const ids1 = (await rowIds(page)).slice(0, 5);
    await head(page, label).click();
    await waitIds(page, () => lastQ().desc !== q1.desc);
    await sleep(150);
    const q2 = lastQ();
    const arrow2 = await head(page, label).innerText();
    const ids2 = (await rowIds(page)).slice(0, 5);
    const expect2 = listItems(db, { ...q2 }).items.slice(0, 5).map((r) => r.id);
    check(`F-DATABANK-2 ${label}: first click ${startsDesc ? 'highest/newest' : 'A-Z'} first, server sorted, ${startsDesc ? '▼' : '▲'}`,
      q1.desc === String(startsDesc) && JSON.stringify(ids1) === JSON.stringify(expect1) && arrow1.endsWith(startsDesc ? '▼' : '▲'), `${q1.desc} ${arrow1}`);
    check(`F-DATABANK-2 ${label}: second click flips it`,
      q2.desc === String(!startsDesc) && JSON.stringify(ids2) === JSON.stringify(expect2) && arrow2.endsWith(startsDesc ? '▲' : '▼'));
  }
  // back to page 1 after a sort
  await head(page, 'Title').click();
  await page.getByRole('button', { name: 'Next page' }).click();
  await waitIds(page, () => lastQ().skip === '200');
  await head(page, 'Title').click();
  await waitIds(page, () => lastQ().skip === '0');
  check('F-DATABANK-2 a new sort goes back to page 1', (await page.getByTestId('db-status').innerText()).includes('Page 1 of 3'));

  // F-DATABANK-3: Last played day-first in station time, never-played last both ways
  await head(page, 'Title').click(); // A-Z: m1 = Song 001 first
  await waitIds(page, (ids) => ids[0] === 'm1');
  const r1 = await rows(page).first().locator(':scope > span').allInnerTexts();
  check('F-DATABANK-3 last played shown day-first in the station\'s time zone (22:30 on 15/11 New York)', r1[7] === '15/11/2026' && r1[8] === '22:30', `${r1[7]} ${r1[8]}`);
  await head(page, 'Last played').click();
  await waitIds(page, () => lastQ().sort === 'last_played');
  await head(page, 'Last played').click();
  await waitIds(page, () => lastQ().desc === 'false');
  check('F-DATABANK-3 last played is per station (station_id sent)', lastQ().station_id === 'st-1');

  // F-DATABANK-4: Find after 300 ms, View shows the count, page 1
  await page.getByRole('button', { name: 'Next page' }).click();
  await waitIds(page, () => lastQ().skip === '200');
  const before = itemCalls().length;
  await page.getByTestId('db-find').pressSequentially('shalsheles', { delay: 30 });
  await sleep(100);
  const midTyping = itemCalls().slice(before).filter((c) => c.query.search).length;
  await waitIds(page, (ids) => ids.length === 1 && ids[0] === 'm7');
  check('F-DATABANK-4 Find waits for typing to stop (one request), case-insensitive, page 1',
    midTyping === 0 && itemCalls().slice(before).filter((c) => c.query.search).length === 1 && lastQ().skip === '0',
    `${midTyping} ${JSON.stringify(itemCalls().slice(before).map((c) => [c.query.search, c.query.skip]))}`);
  check('F-DATABANK-4 View shows the number found', await num(page, 'db-view') === 1);

  // F-DATABANK-6 type filter (with types over the whole table, F-DATABANK-28)
  await page.getByTestId('db-find').fill('');
  await head(page, 'Artist').click();
  await waitIds(page, () => !lastQ().search);
  const opts = await page.getByTestId('db-type').locator('option').allInnerTexts();
  const onScreenTypes = new Set(listItems(db, { sort: 'artist' }).items.map((r) => r.type));
  check('F-DATABANK-28 type list offers a type that only shows past page 1', opts.includes('rare type') && !onScreenTypes.has('rare type'), opts.join(','));
  check('F-DATABANK-6 type list starts with "Every type"', opts[0] === 'Every type');
  await page.getByTestId('db-type').selectOption('Lively');
  await waitIds(page, () => lastQ().category === 'Lively');
  await page.getByTestId('db-find').fill('artist 0');
  await page.getByTestId('db-mpeg').click();
  await waitIds(page, () => lastQ().files === 'mpeg' && lastQ().search === 'artist 0');
  const exp = listItems(db, { sort: lastQ().sort, desc: lastQ().desc, category: 'Lively', search: 'artist 0', files: 'mpeg' });
  check('F-DATABANK-6 type filter on the server, together with Find and Mpeg', await num(page, 'db-view') === exp.total && exp.total > 0, `${exp.total}`);
  check('F-DATABANK-5 ALL not pressed while something filters', await page.getByRole('button', { name: 'ALL' }).getAttribute('aria-pressed') === 'false');

  // F-DATABANK-7 Mpeg / Prsnt release each other; second press back to all
  await page.getByTestId('db-present').click();
  check('F-DATABANK-7 pressing Prsnt releases Mpeg', await page.getByTestId('db-mpeg').getAttribute('aria-pressed') === 'false' && await page.getByTestId('db-present').getAttribute('aria-pressed') === 'true');
  await page.getByTestId('db-present').click();
  check('F-DATABANK-7 a second press goes back to all', await page.getByTestId('db-present').getAttribute('aria-pressed') === 'false');

  // F-DATABANK-5 ALL clears Find, type and Mpeg/Prsnt and focuses Find
  await page.getByTestId('db-mpeg').click();
  await page.getByRole('button', { name: 'ALL' }).click();
  await waitIds(page, () => !lastQ().files && !lastQ().category && !lastQ().search);
  check('F-DATABANK-5 ALL clears Find, type and Mpeg/Prsnt, cursor in Find, shown pressed',
    await page.getByTestId('db-find').inputValue() === '' && await page.getByTestId('db-type').inputValue() === ''
    && await page.getByTestId('db-mpeg').getAttribute('aria-pressed') === 'false'
    && await page.evaluate(() => document.activeElement?.getAttribute('data-testid')) === 'db-find'
    && await page.getByRole('button', { name: 'ALL' }).getAttribute('aria-pressed') === 'true');

  // F-DATABANK-9 status bar
  const status = await page.getByTestId('db-status').innerText();
  check('F-DATABANK-9 Page 1 of 3, View 450, Tot 450', /Page 1 of 3/.test(status) && await num(page, 'db-view') === 450 && await num(page, 'db-tot') === 450, status.replace(/\s+/g, ' '));
  check('F-DATABANK-9 ◀ greyed on page 1', await page.getByRole('button', { name: 'Previous page' }).isDisabled());
  await page.getByRole('button', { name: 'Next page' }).click();
  await page.getByRole('button', { name: 'Next page' }).click();
  await waitIds(page, () => lastQ().skip === '400');
  check('F-DATABANK-9 ▶ moves 200 rows and is greyed on the last page', await page.getByRole('button', { name: 'Next page' }).isDisabled() && (await rowIds(page)).length === 50);
  await rows(page).nth(2).click();
  check('F-DATABANK-9 Rec counts across pages (403)', (await page.getByTestId('db-rec').innerText()).includes('Rec 403'));
  await page.getByTestId('db-find').fill('song 00');
  await waitIds(page, () => lastQ().search === 'song 00');
  check('F-DATABANK-9 Tot is the whole table while View is the found', await waitView(page, 9) && await num(page, 'db-tot') === 450, `${await num(page, 'db-view')}`);
  await page.getByRole('button', { name: 'ALL' }).click();
  await waitIds(page, () => !lastQ().search);
  await waitView(page, 450);

  // F-DATABANK-8 123: other page, errors, Esc, leaving the box
  await page.getByTestId('db-rec').click();
  await page.getByTestId('db-goto').fill('250');
  await page.getByTestId('db-goto').press('Enter');
  const want = listItems(db, { sort: 'artist', desc: 'false', skip: '200' }).items[49].id;
  await page.locator(`[data-row="${want}"].qn-row-cur`).waitFor({ timeout: 3000 }).catch(() => {});
  await sleep(300);
  const selVisible = await page.locator(`[data-row="${want}"]`).evaluate((el) => {
    const r = el.getBoundingClientRect(); const box = el.closest('[data-testid="db-table"]').getBoundingClientRect();
    return el.classList.contains('qn-row-cur') && r.top >= box.top && r.bottom <= box.bottom;
  }).catch(() => false);
  check('F-DATABANK-8 123 to record 250 loads page 2, selects it and scrolls it into view',
    (await page.getByTestId('db-rec').innerText()).includes('Rec 250') && selVisible);
  for (const bad of ['0', '-3', '451']) {
    await page.getByTestId('db-rec').click();
    await page.getByTestId('db-goto').fill(bad);
    await page.getByTestId('db-goto').press('Enter');
    check(`F-DATABANK-8 record ${bad} says "There are 450 records here."`, await page.getByRole('status').filter({ hasText: 'There are 450 records here.' }).isVisible());
  }
  await page.getByTestId('db-rec').click();
  await page.getByTestId('db-goto').fill('7');
  await page.getByTestId('db-goto').press('Escape');
  check('F-DATABANK-8 Esc cancels', await page.getByTestId('db-goto').count() === 0 && (await page.getByTestId('db-rec').innerText()).includes('Rec 250'));
  await page.getByTestId('db-rec').click();
  await page.getByTestId('db-goto').fill('7');
  await page.getByTestId('db-find').click();
  check('F-DATABANK-8 leaving the box cancels', await page.getByTestId('db-goto').count() === 0 && (await page.getByTestId('db-rec').innerText()).includes('Rec 250'));

  // F-DATABANK-43 empty states
  await page.getByTestId('db-find').fill('zzzz-nothing');
  await page.getByText('Nothing matches.').waitFor({ timeout: 3000 }).catch(() => {});
  check('F-DATABANK-43 "Nothing matches." for a Find with no rows', await page.getByText('Nothing matches.').isVisible());
  await page.getByRole('button', { name: 'ALL' }).click();

  // F-DATABANK-29 changing tab: clears page/selection/type, resets sort, keeps Find and Mpeg; Edit mode stays
  await head(page, 'V').click();
  await page.getByRole('button', { name: 'Next page' }).click();
  await waitIds(page, () => lastQ().skip === '200');
  await rows(page).first().click();
  await page.getByRole('tab', { name: 'Jingles' }).click();
  await waitIds(page, () => lastQ().asset_type === 'jingle');
  check('F-DATABANK-29 tab change: back to page 1, selection cleared', lastQ().skip === '0' && (await page.getByTestId('db-rec').innerText()).includes('Rec 0'));
  await page.getByRole('tab', { name: 'Songs' }).click();
  await waitIds(page, () => lastQ().asset_type === 'music');
  await page.getByTestId('db-type').selectOption('Lively');
  await rows(page).first().click();
  await page.getByTestId('db-find').fill('station');
  await page.getByTestId('db-mpeg').click();
  await page.getByTestId('db-edit-mode').click();
  await page.getByRole('tab', { name: 'Jingles' }).click();
  await waitIds(page, () => lastQ().asset_type === 'jingle');
  await sleep(200);
  const jq = lastQ();
  check('F-DATABANK-29 tab change: page 1, no type, sorted by title, Find and Mpeg kept',
    jq.skip === '0' && !jq.category && jq.sort === 'title' && jq.desc === 'false' && jq.search === 'station' && jq.files === 'mpeg', JSON.stringify(jq));
  check('F-DATABANK-29 selection cleared, Edit mode still on',
    (await page.getByTestId('db-rec').innerText()).includes('Rec 0') && (await page.getByTestId('db-edit-mode').innerText()).includes('Editing'));
  // F-DATABANK-14 jingle columns
  const jh = await page.locator('.qn-log-head').innerText();
  check('F-DATABANK-14 jingle columns File, Group, Length, Stored as, Last played, At (no Artist, V, Intro)',
    ['File', 'Group', 'Length', 'Stored as', 'Last played', 'At'].every((c) => jh.includes(c)) && !/Artist|\bV\b|Intro/.test(jh), jh.replace(/\s+/g, ' '));
  check('F-DATABANK-6 jingle type list says "Every group"', (await page.getByTestId('db-type').locator('option').first().innerText()) === 'Every group');
  await page.getByRole('tab', { name: 'Songs' }).click();
  await waitIds(page, () => lastQ().asset_type === 'music');
  check('F-DATABANK-29 back on Songs sorted by artist', lastQ().sort === 'artist');

  check('songs: no script errors', errors.length === 0, errors.join(' | '));
  await ctx.close();
}

/* ── Edit mode ─────────────────────────────────────────────────── */
if (run('edit')) {
  const { ctx, page, errors, db } = await open(browser, DESKTOP);
  await rows(page).first().waitFor();
  const cellOf = (id, i) => page.locator(`[data-row="${id}"] > span`).nth(i);
  const editAndEnter = async (id, i, value) => {
    await cellOf(id, i).click();
    await page.getByTestId('db-edit-input').fill(value);
    await page.getByTestId('db-edit-input').press('Enter');
  };
  // F-DATABANK-33 without Edit mode a click only selects
  const first = (await rowIds(page))[0];
  await cellOf(first, 1).click();
  check('F-DATABANK-33 without Edit mode a click only selects', await page.getByTestId('db-edit-input').count() === 0 && await page.locator(`[data-row="${first}"].qn-row-cur`).count() === 1);
  // Sorted by file, so edits do not move the row away
  await head(page, 'File').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="db-table"] [data-row]')?.getAttribute('data-row') === 'm4');
  await page.getByTestId('db-edit-mode').click();
  check('F-DATABANK-11 "Edit mode" turns to "✎ Editing" with the yellow hint',
    (await page.getByTestId('db-edit-mode').innerText()).includes('✎ Editing') && await page.getByText(/Edit mode: click an artist, title, type, V or intro/).isVisible());
  const id = (await rowIds(page))[3];
  await cellOf(id, 1).click();
  check('F-DATABANK-33 a click in Edit mode selects the row and opens only that cell',
    await page.getByTestId('db-edit-input').count() === 1 && await page.locator(`[data-row="${id}"].qn-row-cur`).count() === 1);
  await page.getByTestId('db-edit-input').press('Escape');

  const fields = [[0, 'artist', 'New Artist', 'artist', 'New Artist'], [1, 'title', 'New Title', 'title', 'New Title'],
    [2, 'type', 'Lively', 'category', 'Lively'], [3, 'vote', '42', 'vote', 42], [5, 'intro', '12.5', 'intro_seconds', 12.5]];
  for (const [i, name, value, key, sent] of fields) {
    const n = db.patches.length;
    const t0 = Date.now();
    await editAndEnter(id, i, value);
    await page.getByRole('status').filter({ hasText: 'Saved.' }).waitFor({ timeout: 3000 }).catch(() => {});
    const took = Date.now() - t0;
    const p = db.patches.slice(n);
    await sleep(250);
    const shown = (await cellOf(id, i).innerText()).trim();
    check(`F-DATABANK-11 ${name}: Enter saves once (PATCH /assets/{id}) and the row shows the new value`,
      p.length === 1 && p[0].id === id && p[0].body[key] === sent && (name === 'intro' ? shown === '0:13' || shown === '0:12' : shown === String(value)), `${p.length} ${JSON.stringify(p[0]?.body)} "${shown}"`);
    if (name === 'vote') check('F-DATABANK-26 an Edit-mode save is confirmed within 1 s', took < 1000, `${took} ms`);
  }
  check('F-DATABANK-11 "Saved." shows for 5 s', await page.getByRole('status').filter({ hasText: 'Saved.' }).isVisible());
  await sleep(5300);
  check('F-DATABANK-11 ... then goes', await page.getByRole('status').filter({ hasText: 'Saved.' }).count() === 0);

  // Esc cancels; leaving the box saves only when changed
  let n = db.patches.length;
  await cellOf(id, 1).click();
  await page.getByTestId('db-edit-input').fill('Escaped');
  await page.getByTestId('db-edit-input').press('Escape');
  check('F-DATABANK-11 Esc cancels (nothing sent)', db.patches.length === n && await page.getByTestId('db-edit-input').count() === 0);
  await cellOf(id, 1).click();
  await page.getByTestId('db-find').click();
  check('F-DATABANK-11 leaving an unchanged box sends nothing', db.patches.length === n);
  await cellOf(id, 1).click();
  await page.getByTestId('db-edit-input').fill('Blurred title');
  await page.getByTestId('db-find').click();
  await sleep(300);
  check('F-DATABANK-11 leaving a changed box saves it', db.patches.length === n + 1 && db.patches.at(-1).body.title === 'Blurred title');

  // F-DATABANK-12 checks before sending
  const refused = [[3, '101', 'V is a whole number from 0 to 100.'], [3, '4.5', 'V is a whole number from 0 to 100.'], [3, '', 'V is a whole number from 0 to 100.'],
    [5, '601', 'Intro is seconds, 0 to 600.'], [5, '-1', 'Intro is seconds, 0 to 600.'], [1, '   ', 'The title cannot be empty.']];
  for (const [i, value, words] of refused) {
    n = db.patches.length;
    await editAndEnter(id, i, value);
    const red = page.getByRole('status').filter({ hasText: words });
    check(`F-DATABANK-12 "${value}" refused with a red notice, nothing sent`, db.patches.length === n && await red.isVisible() && (await red.getAttribute('class')).includes('bg-red'));
  }
  // F-DATABANK-55 V emptied: refused, not saved as 0, and the cell keeps its value
  {
    const vBefore = (await cellOf(id, 3).innerText()).trim();
    const n55 = db.patches.length;
    await editAndEnter(id, 3, '');
    await sleep(250);
    if (await page.getByTestId('db-edit-input').count()) await page.getByTestId('db-edit-input').press('Escape');
    check('F-DATABANK-55 V emptied + Enter: refused, nothing sent (no V = 0), the cell keeps its value',
      db.patches.length === n55 && (await cellOf(id, 3).innerText()).trim() === vBefore && vBefore !== '0', `${db.patches.length - n55} sent, "${vBefore}"`);
  }
  n = db.patches.length;
  await editAndEnter(id, 5, '');
  await sleep(200);
  await editAndEnter(id, 0, '');
  await sleep(200);
  await editAndEnter(id, 2, '');
  await sleep(200);
  const sent = db.patches.slice(n).map((p) => p.body);
  check('F-DATABANK-12 empty Intro clears it; empty Artist / Type save as none',
    sent.length === 3 && sent[0].intro_seconds === null && sent[1].artist === null && sent[2].category === null, JSON.stringify(sent));

  // F-DATABANK-35 refused by the server / server unreachable
  const before = (await cellOf(id, 1).innerText()).trim();
  await editAndEnter(id, 1, 'REFUSE');
  await sleep(300);
  check('F-DATABANK-35 a 422 shows the server\'s words (not a crash) and the cell keeps the old value',
    await page.getByRole('status').filter({ hasText: 'The title cannot be empty' }).isVisible() && (await cellOf(id, 1).innerText()).trim() === before);
  await editAndEnter(id, 1, 'NOPE');
  await sleep(300);
  check('F-DATABANK-35 a 403 shows the server\'s words', await page.getByRole('status').filter({ hasText: 'You may not change this item' }).isVisible());
  await page.route('**/api/v1/assets/*', (r) => (r.request().method() === 'PATCH' ? r.abort('internetdisconnected') : r.fallback()));
  await editAndEnter(id, 1, 'Offline title');
  await sleep(300);
  check('F-DATABANK-35 no connection: "Could not save that change." and the old value',
    await page.getByRole('status').filter({ hasText: 'Could not save that change.' }).isVisible() && (await cellOf(id, 1).innerText()).trim() === before);
  await page.unroute('**/api/v1/assets/*');

  // F-DATABANK-33 turning Edit mode off closes an open cell without saving
  n = db.patches.length;
  await cellOf(id, 1).click();
  await page.getByTestId('db-edit-input').fill('Not to be saved');
  await page.getByTestId('db-edit-mode').click();
  await sleep(300);
  check('F-DATABANK-33 turning Edit mode off closes the cell without saving', db.patches.length === n && await page.getByTestId('db-edit-input').count() === 0, `${db.patches.length - n} sent`);
  await cellOf(id, 1).click();
  check('F-DATABANK-33 Edit mode off: a click does not open a cell', await page.getByTestId('db-edit-input').count() === 0);

  // F-DATABANK-51 old rows while a new sort loads are not editable as if new
  await page.getByTestId('db-edit-mode').click();
  await page.route('**/api/v1/databank/items*', async (r) => { await sleep(1500); await r.fallback(); });
  await head(page, 'Title').click();
  await sleep(200);
  const busy = await page.locator('[role="table"][aria-busy="true"]').count();
  const oldId = (await rowIds(page))[0];
  await cellOf(oldId, 1).click();
  check('F-DATABANK-51 while a new sort loads the old rows are marked busy and cannot be edited', busy === 1 && await page.getByTestId('db-edit-input').count() === 0);
  await page.unroute('**/api/v1/databank/items*');

  check('edit: no script errors', errors.length === 0, errors.join(' | '));
  await ctx.close();
}

/* ── Pre-listen and keyboard ───────────────────────────────────── */
if (run('pfl')) {
  const { ctx, page, errors, st } = await open(browser, DESKTOP);
  await rows(page).first().waitFor();
  const pflBtn = page.getByRole('button', { name: 'Pre-listen to the selected item' });
  check('F-DATABANK-10 toolbar 🎧 greyed until a row is selected', await pflBtn.isDisabled());
  const queueCalls = () => st.calls.filter((c) => c.method !== 'GET' && c.path.startsWith('/stations/')).length;
  const q0 = queueCalls();
  const np0 = JSON.stringify(st.nowPlaying().asset_id);
  const ids = await rowIds(page);
  await rows(page).nth(1).dblclick();
  await page.getByTestId('pfl-deck').waitFor({ timeout: 3000 }).catch(() => {});
  await sleep(500);
  check('F-DATABANK-10 double-click pre-listens in the PFL deck, row outlined green',
    await page.getByTestId('pfl-deck').isVisible() && await page.locator(`[data-row="${ids[1]}"]`).evaluate((el) => el.className.includes('outline-[#2bff4a]')));
  check('F-DATABANK-10 nothing changes on air (no queue / control requests, same now-playing)', queueCalls() === q0 && JSON.stringify(st.nowPlaying().asset_id) === np0);
  // F-DATABANK-32 keys
  await page.getByTestId('db-table').focus();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowDown');
  check('F-DATABANK-32 ↓ moves the selection', (await page.getByTestId('db-rec').innerText()).includes('Rec 4'));
  await page.keyboard.press('ArrowUp');
  check('F-DATABANK-32 ↑ moves it back', (await page.getByTestId('db-rec').innerText()).includes('Rec 3'));
  for (let i = 0; i < 60; i++) await page.keyboard.press('ArrowDown');
  const inView = await page.locator('.qn-row-cur').evaluate((el) => {
    const r = el.getBoundingClientRect(); const box = el.closest('[data-testid="db-table"]').getBoundingClientRect();
    return r.top >= box.top - 1 && r.bottom <= box.bottom + 1;
  });
  check('F-DATABANK-32 the selection is kept in view', inView);
  await page.keyboard.press(' ');
  await sleep(400);
  const selId = await page.locator('.qn-row-cur').getAttribute('data-row');
  check('F-DATABANK-32 Space pre-listens the selected item', await page.locator(`[data-row="${selId}"]`).evaluate((el) => el.className.includes('outline-[#2bff4a]')));
  await page.getByTestId('db-edit-mode').click();
  await page.locator(`[data-row="${selId}"] > span`).nth(1).click();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press(' ');
  check('F-DATABANK-32 keys do nothing while a cell is being edited', await page.locator('.qn-row-cur').getAttribute('data-row') === selId && (await page.getByTestId('db-edit-input').inputValue()).endsWith(' '));
  await page.getByTestId('db-edit-input').press('Escape');
  await page.getByTestId('db-edit-mode').click();
  await page.getByTestId('db-rec').click();
  await page.keyboard.press('ArrowDown');
  check('F-DATABANK-32 keys do nothing while the go-to box is open', await page.locator('.qn-row-cur').getAttribute('data-row') === selId);
  await page.keyboard.press('Escape');
  // F-DATABANK-48 broken file
  await page.route('**/__tone/**', (r) => r.fulfill({ status: 200, contentType: 'audio/wav', body: 'not audio at all' }));
  await rows(page).nth(5).dblclick();
  await page.getByTestId('pfl-error').waitFor({ timeout: 6000 }).catch(() => {});
  check('F-DATABANK-48 a broken file shows the PFL deck\'s error', await page.getByTestId('pfl-error').isVisible());
  await rows(page).nth(6).click();
  check('F-DATABANK-48 the page keeps working', (await page.getByTestId('db-rec').innerText()).includes('Rec 7'));
  await page.unroute('**/__tone/**');
  // Space on Blocks does not pre-listen
  await page.getByRole('tab', { name: 'Blocks' }).click();
  await rows(page).first().click();
  await page.keyboard.press(' ');
  check('F-DATABANK-32 Space does nothing on Blocks', !(await page.locator('.qn-row-cur').evaluate((el) => el.className.includes('outline-[#2bff4a]'))));
  check('pfl: no script errors', errors.length === 0, errors.join(' | '));
  await ctx.close();
}

/* ── F-DATABANK-54: keys typed in fields and buttons are theirs, not the table's ── */
if (run('keys')) {
  const { ctx, page, errors } = await open(browser, DESKTOP);
  await rows(page).first().waitFor();
  const pflOn = async () => (await page.getByTestId('pfl-deck').isVisible().catch(() => false))
    || (await page.locator('[data-testid="db-table"] [class*="outline-[#2bff4a]"]').count()) > 0;
  await rows(page).nth(2).click();
  const rec = await page.getByTestId('db-rec').innerText();
  // A space typed in Find, with a row selected
  await page.getByTestId('db-find').click();
  await page.keyboard.type('Mordechai Ben David', { delay: 5 });
  await sleep(400);
  check('F-DATABANK-54 a space typed in Find is typed ("Mordechai Ben David"), no pre-listen',
    await page.getByTestId('db-find').inputValue() === 'Mordechai Ben David' && !(await pflOn()), await page.getByTestId('db-find').inputValue());
  await page.getByTestId('db-find').fill('');
  await waitView(page, 450);
  await rows(page).nth(2).click();
  // Arrows in the type drop-down move the choice, not the row selection
  const typeSel = page.getByTestId('db-type');
  await typeSel.focus();
  const v0 = await typeSel.inputValue();
  await page.keyboard.press('ArrowDown');
  await sleep(300);
  check('F-DATABANK-54 ↓ in the type drop-down moves the choice, not the row selection',
    await typeSel.inputValue() !== v0 && (await page.getByTestId('db-rec').innerText()) !== 'Rec 4', `${v0} -> ${await typeSel.inputValue()}, ${await page.getByTestId('db-rec').innerText()}`);
  await typeSel.selectOption({ index: 0 });
  await waitView(page, 450);
  // Space on a focused toolbar button presses that button, it does not pre-listen
  await rows(page).nth(2).click();
  await page.getByTestId('db-edit-mode').focus();
  await page.keyboard.press(' ');
  await sleep(300);
  check('F-DATABANK-54 Space on a focused toolbar button presses it (Edit mode on), no pre-listen',
    (await page.getByTestId('db-edit-mode').innerText()).includes('Editing') && !(await pflOn()));
  await page.keyboard.press('ArrowDown');
  check('F-DATABANK-54 ↓ on a focused toolbar button does not move the row selection', (await page.getByTestId('db-rec').innerText()) === rec, await page.getByTestId('db-rec').innerText());
  await page.keyboard.press(' ');
  check('F-DATABANK-54 ... and Space again turns Edit mode off', !(await page.getByTestId('db-edit-mode').innerText()).includes('Editing'));
  // Log tab: arrows and Space do not select or pre-listen hidden song rows
  await page.getByRole('tab', { name: 'Log' }).click();
  await page.locator('[data-testid="daylog-lines"] button').first().waitFor({ timeout: 5000 });
  await page.getByTestId('daylog-lines').evaluate((el) => { el.tabIndex = -1; el.focus(); });
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press(' ');
  await sleep(500);
  check('F-DATABANK-54 on the Log tab ↓ and Space pre-listen nothing', !(await page.getByTestId('pfl-deck').isVisible().catch(() => false)));
  await page.locator('[data-testid="daylog-lines"] button').nth(1).focus();
  await page.keyboard.press(' ');
  await sleep(300);
  check('F-DATABANK-54 Space on a log line highlights that line only (no pre-listen)',
    (await page.locator('[data-testid="daylog-lines"] button').nth(1).getAttribute('class')).includes('bg-[#000080]') && !(await page.getByTestId('pfl-deck').isVisible().catch(() => false)));
  check('keys: no script errors', errors.length === 0, errors.join(' | '));
  await ctx.close();
}

/* ── Spots ─────────────────────────────────────────────────────── */
if (run('spots')) {
  const { ctx, page, errors, db } = await open(browser, DESKTOP, { path: '/admin/databank?tab=spots' });
  await rows(page).first().waitFor();
  const text = async (id) => (await page.locator(`[data-row="${id}"]`).innerText()).replace(/\s+/g, ' ');
  check('F-DATABANK-16 open-ended spot reads "no end"', (await text('s3')).includes('no end'));
  check('F-DATABANK-16 unscheduled spot reads "not scheduled"', (await text('s4')).includes('not scheduled'));
  check('F-DATABANK-16 schedules count, "(off)" when none is active', (await text('s1')).endsWith('2') && (await text('s5')).includes('1 (off)'));
  const color = await page.locator('[data-row="s2"]').evaluate((el) => getComputedStyle(el).color);
  check('F-DATABANK-17 expired spot red (#ff4040)', color === 'rgb(255, 64, 64)', color);
  check('F-DATABANK-17 open-ended and unscheduled never red', (await page.locator('[data-expired]').count()) === 1);
  await page.locator('[data-row="s2"]').click();
  const selColor = await page.locator('[data-row="s2"]').evaluate((el) => getComputedStyle(el).color);
  check('F-DATABANK-17 not red while selected', selColor !== 'rgb(255, 64, 64)', selColor);

  // F-DATABANK-37 sort and filter in the page
  const order = async () => rowIds(page);
  await head(page, 'End date').click();
  check('F-DATABANK-37 End date: first click latest first (open-ended top, unscheduled last)', JSON.stringify(await order()) === JSON.stringify(['s3', 's1', 's5', 's2', 's4']), (await order()).join());
  await head(page, 'End date').click();
  check('F-DATABANK-37 End date ascending: unscheduled first, open-ended last', JSON.stringify(await order()) === JSON.stringify(['s4', 's2', 's5', 's1', 's3']), (await order()).join());
  await head(page, 'Length').click();
  check('F-DATABANK-37 Length', JSON.stringify(await order()) === JSON.stringify(['s4', 's1', 's5', 's3', 's2']));
  await head(page, 'Category').click();
  check('F-DATABANK-37 Category', (await order())[0] === 's3' && (await order()).at(-1) === 's2');
  await head(page, 'File').click();
  await head(page, 'File').click();
  check('F-DATABANK-37 File (title) both ways', (await order())[0] === 's4' && (await order()).at(-1) === 's3');
  await page.getByTestId('db-find').fill('pizza');
  await sleep(450);
  check('F-DATABANK-37 Find over title, file and category', JSON.stringify(await order()) === '["s5"]');
  await page.getByTestId('db-find').fill('FOOD');
  await sleep(450);
  check('F-DATABANK-37 Find matches the category', (await order()).length === 3);
  await page.getByRole('button', { name: 'ALL' }).click();
  await sleep(400);
  await page.getByTestId('db-type').selectOption('finance');
  check('F-DATABANK-37 Category filter', JSON.stringify(await order()) === '["s3"]');
  await page.getByRole('button', { name: 'ALL' }).click();
  await page.getByTestId('db-mpeg').click();
  check('F-DATABANK-37 Mpeg leaves out the .wav spot', (await order()).length === 4 && !(await order()).includes('s3'));
  await page.getByTestId('db-type').selectOption('finance');
  check('F-DATABANK-43 Spots with a filter and no rows say "Nothing matches."', await page.getByText('Nothing matches.').isVisible());
  await page.getByRole('button', { name: 'ALL' }).click();

  // F-DATABANK-18 end date edit, F-DATABANK-40 clearing it, F-DATABANK-20 server words
  await page.getByTestId('db-edit-mode').click();
  check('F-DATABANK-18 hint mentions the end date moves every schedule', await page.getByText('A new end date moves every schedule of that spot.').isVisible());
  await page.locator('[data-row="s2"] [data-testid="spot-end"]').click();
  const newEnd = addDays(db.today, 30);
  await page.getByTestId('db-edit-input').fill(newEnd);
  const t0 = Date.now();
  await page.getByTestId('db-edit-input').press('Enter');
  const [y, m, d] = newEnd.split('-').map(Number);
  await page.getByRole('status').filter({ hasText: `Fig Flip Pro now runs until ${d}/${m}/${y}.` }).waitFor({ timeout: 3000 }).catch(() => {});
  check('F-DATABANK-18 notice "<spot> now runs until d/m/yyyy", sent with the station', await page.getByRole('status').filter({ hasText: `now runs until ${d}/${m}/${y}` }).isVisible() && db.endDates.at(-1).query.station_id === 'st-1');
  await sleep(300);
  check('F-DATABANK-18 the red clears', await page.locator('[data-row="s2"][data-expired]').count() === 0, `${Date.now() - t0} ms`);
  await page.locator('[data-row="s1"] [data-testid="spot-end"]').click();
  await page.getByTestId('db-edit-input').fill('');
  // clearing asks first (F-DATABANK-56, checked in e2e/databank-screen.mjs): say yes
  page.once('dialog', (dlg) => dlg.accept());
  await page.getByTestId('db-edit-input').press('Enter');
  await page.getByRole('status').filter({ hasText: 'Bagel King Breakfast now has no end date.' }).waitFor({ timeout: 3000 }).catch(() => {});
  check('F-DATABANK-40 clearing the end date: "<spot> now has no end date." and "no end"',
    db.endDates.at(-1).body.end_date === null && await page.getByRole('status').filter({ hasText: 'now has no end date.' }).isVisible() && (await text('s1')).includes('no end'));
  await page.locator('[data-row="s5"] [data-testid="spot-end"]').click();
  await page.getByTestId('db-edit-input').fill('2025-06-01');
  await page.getByTestId('db-edit-input').press('Enter');
  await page.getByRole('status').filter({ hasText: 'The end date is before the schedule starts' }).waitFor({ timeout: 3000 }).catch(() => {});
  check('F-DATABANK-20 the page shows the server\'s words', await page.getByRole('status').filter({ hasText: 'The end date is before the schedule starts' }).isVisible());
  const n = db.endDates.length;
  await page.locator('[data-row="s4"] [data-testid="spot-end"]').click();
  check('F-DATABANK-18 an unscheduled spot\'s end date cannot be opened', await page.getByTestId('db-edit-input').count() === 0 && db.endDates.length === n);
  // F-DATABANK-38 category edit
  const p = db.patches.length;
  await page.locator('[data-row="s3"] > span').nth(1).click();
  await page.getByTestId('db-edit-input').fill('insurance');
  await page.getByTestId('db-edit-input').press('Enter');
  await sleep(300);
  check('F-DATABANK-38 spot Category edit saves the category', db.patches.length === p + 1 && db.patches.at(-1).body.category === 'insurance' && (await text('s3')).includes('insurance'));
  check('spots: no script errors', errors.length === 0, errors.join(' | '));
  await ctx.close();

  // F-DATABANK-43 no spots at all
  const empty = makeDb(); empty.spots = [];
  const e = await open(browser, DESKTOP, { path: '/admin/databank?tab=spots', db: empty });
  await e.page.getByText('No spots in the library yet.').waitFor({ timeout: 4000 }).catch(() => {});
  check('F-DATABANK-43 "No spots in the library yet."', await e.page.getByText('No spots in the library yet.').isVisible());
  await e.page.getByTestId('db-find').fill('x');
  await sleep(400);
  check('F-DATABANK-43 ... even with a Find typed', await e.page.getByText('No spots in the library yet.').isVisible());
  await e.ctx.close();
}

/* ── Blocks ────────────────────────────────────────────────────── */
if (run('blocks')) {
  const { ctx, page, errors } = await open(browser, DESKTOP, { path: '/admin/databank?tab=blocks' });
  await rows(page).first().waitFor();
  const lines = (await rows(page).allInnerTexts()).map((t) => t.replace(/\s+/g, ' ').trim());
  check('F-DATABANK-21 one row per clock time, ordered by time then name',
    JSON.stringify(lines.map((l) => l.split(' ').slice(0, 2).join(' '))) === JSON.stringify(['07:00 Afternoon', '07:00 Weather', '08:00 Weather', '12:00 Old']), lines.join(' | '));
  check('F-DATABANK-21 week days Sunday-first ("012345" = Sunday to Friday)', lines[1].includes('012345') && lines[0].includes('0123456') && lines[3].includes(' 6 '), lines[1]);
  check('F-DATABANK-21 Recs = number of items', lines[1].includes(' 3 ') && lines[0].includes(' 1 '));
  check('F-DATABANK-21 timing hard / soft / soft, N min', lines[1].endsWith('hard') && lines[0].endsWith('soft, 5 min') && lines[3].endsWith('soft'));
  const flg = await rows(page).nth(1).locator('[aria-label="Hard"]').count() + await rows(page).nth(0).locator('[aria-label="Hard"]').count();
  check('F-DATABANK-21 yellow dot only on the hard block', flg === 1);
  check('F-DATABANK-21 inactive block greyed with "(off)"', lines[3].includes('Old Block (off)') && (await rows(page).nth(3).getAttribute('class')).includes('opacity-50'));
  await page.getByTestId('db-find').fill('weather');
  await sleep(400);
  check('F-DATABANK-21 Find filters by name', (await rows(page).count()) === 2);
  await page.getByRole('link', { name: 'Change blocks' }).click();
  await page.waitForURL(/\/admin\/program-blocks/);
  check('F-DATABANK-21 "Change blocks" opens Program Blocks', true);
  check('blocks: no script errors', errors.length === 0, errors.join(' | '));
  await ctx.close();

  const none = makeDb(); none.blocks = [];
  const e = await open(browser, DESKTOP, { path: '/admin/databank?tab=blocks', db: none });
  await e.page.getByText('No blocks on this station yet.').waitFor({ timeout: 4000 }).catch(() => {});
  check('F-DATABANK-42 no blocks: "No blocks on this station yet."', await e.page.getByText('No blocks on this station yet.').isVisible());
  await e.ctx.close();
  for (const role of ['dj', 'viewer']) {
    const v = await open(browser, DESKTOP, { path: '/admin/databank?tab=blocks', role });
    await v.page.getByText('Blocks are for managers.').waitFor({ timeout: 4000 }).catch(() => {});
    check(`F-DATABANK-42 ${role}: "Blocks are for managers." and no request`, await v.page.getByText('Blocks are for managers.').isVisible() && !v.st.calls.some((c) => c.path === '/program-blocks'));
    await v.ctx.close();
  }
}

/* ── Day log ───────────────────────────────────────────────────── */
if (run('log')) {
  const { ctx, page, errors, db } = await open(browser, DESKTOP, { path: '/admin/databank?tab=log' });
  const lines = page.locator('[data-testid="daylog-lines"] button');
  await lines.first().waitFor({ timeout: 5000 });
  check('F-DATABANK-9 the Log tab has no status bar', await page.getByTestId('db-status').count() === 0);
  const days = await page.locator('[data-testid="daylog-days"] button').allInnerTexts();
  check('F-DATABANK-22 the last 14 days in a list, today first', days.length === 14);
  const t = (await lines.allInnerTexts()).map((s) => s.replace(/\s+/g, ' ').trim());
  check('F-DATABANK-22 every play a Started and an Ended line, HH:MM:SS.mmm in station time, oldest first',
    t[0].startsWith('09:00:00.000 Artist 01 - Song 001') && t[0].endsWith('Started') && t.every((l) => /^\d\d:\d\d:\d\d\.\d{3} /.test(l)), t[0]);
  check('F-DATABANK-44 same millisecond: Ended before Started', t[1].includes('Song 001') && t[1].endsWith('Ended') && t[2].includes('Bagel King') && t[2].endsWith('Started'), `${t[1]} / ${t[2]}`);
  check('F-DATABANK-22 a spot\'s end reads "Spot ended"', t[3].includes('Bagel King') && t[3].endsWith('Spot ended'));
  check('F-DATABANK-44 a deleted item reads "Unknown"', t.some((l) => l.includes('Unknown Started')));
  check('F-DATABANK-44 the name is "artist - title [file]"', t[0].includes('Artist 01 - Song 001 [song1.mp3]'));
  const song7 = t.filter((l) => l.includes('Song 007'));
  check('F-DATABANK-44 the item on air has a Started line only', song7.length === 1 && song7[0].endsWith('Started'));
  await lines.nth(2).click();
  check('F-DATABANK-44 a tapped line is highlighted', (await lines.nth(2).getAttribute('class')).includes('bg-[#000080]'));
  // 15 s refresh of today
  const n0 = db.logReqs.filter((r) => r.day === db.today).length;
  await sleep(15800);
  check('F-DATABANK-22 today refreshes every 15 s', db.logReqs.filter((r) => r.day === db.today).length > n0);
  await page.locator('[data-testid="daylog-days"] button').nth(2).click();
  await page.getByText(/^Nothing aired on /).waitFor({ timeout: 4000 }).catch(() => {});
  check('F-DATABANK-22 empty day: "Nothing aired on <day>."', await page.getByText(/^Nothing aired on .+\.$/).isVisible());
  await page.locator('[data-testid="daylog-days"] button').nth(3).click();
  await page.getByText("Can't load this day's log.").waitFor({ timeout: 8000 }).catch(() => {});
  check('F-DATABANK-22 error: "Can\'t load this day\'s log."', await page.getByText("Can't load this day's log.").isVisible());
  const n1 = db.logReqs.length;
  await page.locator('[data-testid="daylog-days"] button').nth(1).click();
  await sleep(16000);
  check('F-DATABANK-22 an older day is not refreshed', db.logReqs.filter((r) => r.day === addDays(db.today, -1)).length <= 2 && db.logReqs.length - n1 <= 2);
  check('log: no script errors', errors.length === 0, errors.join(' | '));
  await ctx.close();

  // F-DATABANK-45 station calendar, not the computer's; rolls over at station midnight
  {
    // 23:59:40 in New York (06:59:40 next day in Jerusalem, where this "computer" is)
    const nyNow = new Date();
    const local = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York', hour12: false, year: 'numeric', month: '2-digit', day: '2-digit' }).format(nyNow);
    const offsetH = -(new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' })).getTime() - new Date(new Date().toLocaleString('en-US', { timeZone: 'UTC' })).getTime()) / 3600e3;
    const fakeNow = Date.UTC(...local.split('-').map((v, i) => (i === 1 ? Number(v) - 1 : Number(v))), 23 + offsetH, 59, 40);
    const db2 = makeDb();
    db2.today = local;
    const st = makeStation({ extra: databankExtra(db2) });
    const { ctx: c3 } = await adminContext(browser, { ...DESKTOP, timezoneId: 'Asia/Jerusalem' }, st, { storage: { radio_last_station: 'st-1' } });
    await c3.addInitScript((ms) => {
      const Real = Date; const shift = ms - Real.now();
      class Fake extends Real { constructor(...a) { if (a.length === 0) super(Real.now() + shift); else super(...a); } static now() { return Real.now() + shift; } }
      window.Date = Fake;
    }, fakeNow);
    const { page: p3 } = await newPage(c3);
    await p3.goto(`${BASE}/admin/databank?tab=log`);
    await p3.locator('[data-testid="daylog-days"] button').first().waitFor();
    const first = await p3.locator('[data-testid="daylog-days"] button').first().innerText();
    const [yy, mm, dd] = local.split('-').map(Number);
    const label = new Date(Date.UTC(yy, mm - 1, dd)).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
    check('F-DATABANK-45 a browser in Israel sees the New York station\'s days', first.startsWith(label), `${first} vs ${label}`);
    // wait past station midnight (20 s + the 30 s check)
    await sleep(52000);
    const after = await p3.locator('[data-testid="daylog-days"] button').first().innerText();
    const sel = await p3.locator('[data-testid="daylog-days"] li[aria-selected="true"]').innerText();
    const nextLabel = new Date(Date.UTC(yy, mm - 1, dd + 1)).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
    check('F-DATABANK-45 left open over station midnight: the list moves on to the new today and shows it', after.startsWith(nextLabel) && sel.startsWith(nextLabel), `${after} / ${sel}`);
    await c3.close();
  }
}

/* ── F-DATABANK-62: Log tab after a station change ─────────────── */
if (run('logstation')) {
  // 20:00 in New York = 03:00 next day in Jerusalem (st-2): the 14 days differ by one
  const nyNow = new Date();
  const local = nyDay(nyNow);
  const offsetH = -(new Date(nyNow.toLocaleString('en-US', { timeZone: 'America/New_York' })).getTime() - new Date(nyNow.toLocaleString('en-US', { timeZone: 'UTC' })).getTime()) / 3600e3;
  const [yy, mm, dd] = local.split('-').map(Number);
  const fakeNow = Date.UTC(yy, mm - 1, dd, 20 + offsetH, 0, 0);
  for (const [name, opts] of [['desktop', DESKTOP], ['iPhone 13', IPHONE13]]) {
    const db = makeDb();
    db.today = local;
    const st = makeStation({ extra: databankExtra(db) });
    const { ctx } = await adminContext(browser, opts, st, { storage: { radio_last_station: 'st-1' } });
    await ctx.addInitScript((ms) => {
      const Real = Date; const shift = ms - Real.now();
      class Fake extends Real { constructor(...a) { if (a.length === 0) super(Real.now() + shift); else super(...a); } static now() { return Real.now() + shift; } }
      window.Date = Fake;
    }, fakeNow);
    const { page, errors } = await newPage(ctx);
    await page.goto(`${BASE}/admin/databank?tab=log`);
    const lines = page.locator('[data-testid="daylog-lines"] button');
    await lines.first().waitFor({ timeout: 8000 });
    const oldest = addDays(local, -13);
    if (name === 'desktop') await page.locator('[data-testid="daylog-days"] button').nth(13).click();
    else await page.getByTestId('daylog-day').selectOption(oldest);
    await sleep(500);
    await lines.nth(2).click();
    check(`F-DATABANK-62 ${name}: a line highlighted on the oldest day of station 1`, (await lines.nth(2).getAttribute('class')).includes('bg-[#000080]'));
    await page.getByRole('combobox', { name: 'Station' }).selectOption('st-2');
    await sleep(600);
    const jmToday = addDays(local, 1);
    const asked = db.logReqs.filter((r) => r.station_id === 'st-2');
    check(`F-DATABANK-62 ${name}: a day outside station 2's list falls back to its today`, asked.length > 0 && asked.every((r) => r.day === jmToday), JSON.stringify(asked.map((r) => r.day)));
    if (name === 'desktop') {
      const sel = await page.locator('[data-testid="daylog-days"] li[aria-selected="true"]').count();
      const first = await page.locator('[data-testid="daylog-days"] li').first().getAttribute('aria-selected');
      check(`F-DATABANK-62 ${name}: station 2's today is shown as chosen`, sel === 1 && first === 'true');
    } else {
      const v = await page.getByTestId('daylog-day').inputValue();
      check(`F-DATABANK-62 ${name}: the day drop-down is not blank (shows station 2's today)`, v === jmToday, v);
    }
    const hl = await lines.evaluateAll((bs) => bs.filter((b) => b.className.includes('bg-[#000080]')).length);
    check(`F-DATABANK-62 ${name}: no line of the new station is highlighted`, hl === 0, `${hl}`);
    const n0 = db.logReqs.filter((r) => r.station_id === 'st-2' && r.day === jmToday).length;
    await sleep(15800);
    check(`F-DATABANK-62 ${name}: station 2's today keeps refreshing every 15 s`, db.logReqs.filter((r) => r.station_id === 'st-2' && r.day === jmToday).length > n0);
    // Back to station 1: today, not the old day brought back
    await page.getByRole('combobox', { name: 'Station' }).selectOption('st-1');
    await sleep(600);
    const backDay = name === 'desktop'
      ? await page.locator('[data-testid="daylog-days"] li[aria-selected="true"]').innerText()
      : await page.getByTestId('daylog-day').inputValue();
    const todayLabel = new Date(Date.UTC(yy, mm - 1, dd)).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
    check(`F-DATABANK-62 ${name}: back on station 1 it shows today (the dropped day does not come back)`, name === 'desktop' ? backDay.startsWith(todayLabel) : backDay === local, backDay);
    check(`F-DATABANK-62 ${name}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

/* ── Roles ─────────────────────────────────────────────────────── */
if (run('roles')) {
  for (const role of ['admin', 'manager', 'dj', 'viewer']) {
    const { ctx, page, errors } = await open(browser, DESKTOP, { role });
    await rows(page).first().waitFor({ timeout: 8000 }).catch(() => {});
    const opened = await rows(page).count() > 0;
    const edit = await page.getByTestId('db-edit-mode').count();
    const viewOnly = await page.getByText('View only', { exact: true }).isVisible().catch(() => false);
    const mgr = role === 'admin' || role === 'manager';
    check(`F-DATABANK-13 ${role}: opens the page; Edit mode ${mgr ? 'offered' : 'hidden'}; ${mgr ? 'no' : ''} View only marker`,
      opened && (edit === 1) === mgr && viewOnly === !mgr, `edit=${edit} viewOnly=${viewOnly}`);
    if (!mgr) {
      await page.getByRole('tab', { name: 'Spots' }).click();
      await rows(page).first().waitFor();
      await page.locator('[data-row="s2"] [data-testid="spot-end"]').click();
      check(`F-DATABANK-13 ${role}: the spot end date cannot be edited`, await page.getByTestId('db-edit-input').count() === 0);
    }
    check(`F-DATABANK-13 ${role}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
  const sp = await open(browser, DESKTOP, { role: 'sponsor' });
  await sleep(1500);
  check('F-DATABANK-13 sponsor: sent away from the page', !sp.page.url().includes('/admin/databank') && !sp.st.calls.some((c) => c.path.startsWith('/databank')), sp.page.url());
  await sp.ctx.close();
  const anon = await browser.newContext(DESKTOP);
  const ap = await anon.newPage();
  await ap.goto(`${BASE}/admin/databank`);
  await sleep(1500);
  check('F-DATABANK-13 signed out: sent to sign in', /login/.test(ap.url()), ap.url());
  await anon.close();
}

/* ── Station picker ────────────────────────────────────────────── */
if (run('station')) {
  const { ctx, page, errors, st } = await open(browser, DESKTOP, { storage: { radio_last_station: 'st-2' } });
  await rows(page).first().waitFor();
  const picker = page.getByRole('combobox', { name: 'Station' });
  check('F-DATABANK-30 starts on the station last used in the control room', await picker.inputValue() === 'st-2'
    && st.calls.some((c) => c.path === '/databank/items' && c.query.station_id === 'st-2'));
  await rows(page).nth(2).click();
  await picker.selectOption('st-1');
  await sleep(500);
  check('F-DATABANK-30 a change reloads Last played for that station and clears the selection',
    st.calls.filter((c) => c.path === '/databank/items').at(-1).query.station_id === 'st-1' && (await page.getByTestId('db-rec').innerText()).includes('Rec 0'));
  await page.getByRole('tab', { name: 'Spots' }).click();
  await picker.selectOption('st-2');
  await sleep(400);
  check('F-DATABANK-30 Spots follow the station', st.calls.filter((c) => c.path === '/databank/spots').at(-1).query.station_id === 'st-2');
  await page.getByRole('tab', { name: 'Blocks' }).click();
  await sleep(300);
  check('F-DATABANK-30 Blocks follow the station', st.calls.filter((c) => c.path === '/program-blocks').at(-1).query.station_id === 'st-2');
  await page.getByRole('tab', { name: 'Log' }).click();
  await sleep(400);
  check('F-DATABANK-30 Log follows the station', st.calls.filter((c) => c.path === '/databank/log').at(-1).query.station_id === 'st-2');
  check('F-DATABANK-30 title bar shows the station', (await page.locator('.qn-title').innerText()).includes('Second Feed'));
  // Opening the control room from here lands on the station picked here
  await page.getByRole('link', { name: 'Control room' }).click();
  await page.waitForURL(/\/admin\/dashboard/);
  const crPicker = page.getByRole('combobox', { name: 'Station' }).first();
  await crPicker.waitFor();
  await sleep(300);
  check('F-DATABANK-64 the control room opens on the station picked in the Databank', await crPicker.inputValue() === 'st-2');
  await page.goto(`${BASE}/admin/databank`);
  await rows(page).first().waitFor();
  await page.getByRole('combobox', { name: 'Station' }).selectOption('st-1');
  await sleep(200);
  await page.goto(`${BASE}/admin/dashboard`);
  await crPicker.waitFor();
  await sleep(300);
  check('F-DATABANK-64 also when the control room is opened from the menu', await crPicker.inputValue() === 'st-1');
  await ctx.close();
  const gone = await open(browser, DESKTOP, { storage: { radio_last_station: 'deleted-station' } });
  await rows(gone.page).first().waitFor();
  await sleep(300);
  check('F-DATABANK-30 a remembered station that no longer exists falls back to the first',
    await gone.page.getByRole('combobox', { name: 'Station' }).inputValue() === 'st-1');
  check('station: no script errors', errors.length === 0 && gone.errors.length === 0, [...errors, ...gone.errors].join(' | '));
  await gone.ctx.close();
}

/* ── Phone, landscape, tablet ──────────────────────────────────── */
if (run('phone')) {
  for (const [name, opts] of [['iPhone 13', IPHONE13], ['iPhone SE', IPHONE_SE]]) {
    const { ctx, page, errors, db } = await open(browser, opts);
    await rows(page).first().waitFor();
    check(`F-DATABANK-24 ${name}: cards with a 🎧 on each`, await rows(page).first().getByRole('button', { name: /^Pre-listen/ }).count() === 1);
    const small = await page.evaluate(() => {
      const sel = '[role="tab"], [data-testid="databank"] .qn-btn, [data-testid="db-status"] button, [data-testid="db-find"], [data-testid="db-type"]';
      return [...document.querySelectorAll(sel)].filter((el) => el.offsetParent && el.getBoundingClientRect().height < 43.5)
        .map((el) => `${el.textContent.trim().slice(0, 12) || el.getAttribute('aria-label')}:${Math.round(el.getBoundingClientRect().height)}`);
    });
    check(`F-DATABANK-24 ${name}: tabs, toolbar and status-bar buttons at least 44 px`, small.length === 0, small.join(', '));
    const clipped = await page.evaluate(() => [...document.querySelectorAll('[role="tab"]')].filter((el) => { const r = el.getBoundingClientRect(); return r.right > window.innerWidth + 0.5 || el.scrollWidth > el.clientWidth + 1; }).map((el) => el.textContent));
    check(`F-DATABANK-24 ${name}: every tab fully on screen`, clipped.length === 0, clipped.join(','));
    const fs = await page.getByTestId('db-find').evaluate((el) => getComputedStyle(el).fontSize);
    check(`F-DATABANK-24 ${name}: Find in 16 px type`, fs === '16px', fs);
    for (const tab of ['Songs', 'Jingles', 'Spots', 'Blocks', 'Log']) {
      await page.getByRole('tab', { name: tab }).click();
      await sleep(400);
      check(`F-DATABANK-24 ${name}: no sideways scroll on ${tab}`, await noSideScroll(page));
    }
    await page.getByRole('tab', { name: 'Songs' }).click();
    await rows(page).first().waitFor();
    const bar = await page.getByTestId('db-status').boundingBox();
    check(`F-DATABANK-24 ${name}: status bar on screen at the bottom`, bar && bar.y + bar.height <= opts.viewport.height + 1 && bar.y > opts.viewport.height / 2);
    // F-DATABANK-34 phone editing: Title, Type, V tap; Artist / Intro not; 16 px edit box
    await page.getByTestId('db-edit-mode').tap();
    const card = rows(page).nth(6);
    const cid = await card.getAttribute('data-row');
    await card.locator('span.text-\\[15px\\]').tap();
    const box = page.getByTestId('db-edit-input');
    const efs = await box.evaluate((el) => getComputedStyle(el).fontSize).catch(() => '');
    check(`F-DATABANK-34 ${name}: tapping the title opens a 16 px edit box (no zoom)`, efs === '16px', efs);
    // The row is brought to the top of the list (iOS then only has to pan the toolbar away)
    const vis = await box.evaluate((el) => { const r = el.getBoundingClientRect(); const list = el.closest('[data-testid="db-table"]').getBoundingClientRect(); return r.top >= list.top && r.top - list.top < 60; }).catch(() => false);
    const where = await box.evaluate((el) => `${Math.round(el.getBoundingClientRect().top)}-${Math.round(el.getBoundingClientRect().bottom)} of ${window.innerHeight}`).catch(() => '');
    check(`F-DATABANK-34 ${name}: the edit box sits where the keyboard will not hide it`, vis, where);
    await box.fill('Phone title');
    await box.press('Enter');
    await sleep(300);
    check(`F-DATABANK-11 ${name}: phone edit saves`, db.patches.at(-1)?.id === cid && db.patches.at(-1).body.title === 'Phone title');
    await card.getByText(/^V \d+/).tap();
    check(`F-DATABANK-34 ${name}: V can be tapped`, await page.getByTestId('db-edit-input').count() === 1);
    await page.getByTestId('db-edit-input').press('Escape');
    await card.locator('span.text-xs > span').first().tap();
    check(`F-DATABANK-34 ${name}: Type can be tapped`, await page.getByTestId('db-edit-input').count() === 1);
    await page.getByTestId('db-edit-input').press('Escape');
    // F-DATABANK-18 phone end date
    await page.getByRole('tab', { name: 'Spots' }).click();
    await rows(page).first().waitFor();
    await page.locator('[data-row="s2"] [data-testid="spot-end"]').tap();
    await page.getByTestId('db-edit-input').fill(addDays(db.today, 10));
    await page.getByTestId('db-edit-input').press('Enter');
    await page.getByRole('status').filter({ hasText: 'now runs until' }).waitFor({ timeout: 3000 }).catch(() => {});
    await sleep(300);
    check(`F-DATABANK-18 ${name}: end date changed on a phone, red clears`, await page.locator('[data-row="s2"][data-expired]').count() === 0 && db.endDates.length === 1);
    // Log on a phone: drop-down
    await page.getByRole('tab', { name: 'Log' }).click();
    check(`F-DATABANK-22 ${name}: days in a drop-down`, await page.getByTestId('daylog-day').locator('option').count() === 14);
    check(`F-DATABANK-24 ${name}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
  for (const [name, opts, row] of [['iPhone landscape', IPHONE_LAND, 'F-DATABANK-49'], ['iPad Mini', IPAD_MINI, 'F-DATABANK-50'], ['1024 px', { viewport: { width: 1024, height: 768 } }, 'F-DATABANK-50']]) {
    const { ctx, page, errors } = await open(browser, opts);
    await rows(page).first().waitFor();
    for (const tab of ['Songs', 'Spots', 'Blocks', 'Log']) {
      await page.getByRole('tab', { name: tab }).click();
      await sleep(300);
      check(`${row} ${name}: no sideways scroll on ${tab}`, await noSideScroll(page));
    }
    await page.getByRole('tab', { name: 'Songs' }).click();
    await rows(page).first().waitFor();
    const bar = await page.getByTestId('db-status').boundingBox();
    const table = await page.getByTestId('db-table').boundingBox();
    check(`${row} ${name}: table and status bar usable (table ${Math.round(table?.height ?? 0)} px tall)`, bar && bar.y + bar.height <= opts.viewport.height + 1 && table && table.height >= 80);
    if (name === '1024 px') check(`${row} 1024 px: the desktop table takes over`, await page.locator('.qn-log-head').count() === 1);
    if (name === 'iPad Mini') check(`${row} iPad Mini portrait: cards`, await page.locator('.qn-log-head').count() === 0);
    check(`${row} ${name}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

/* ── Speed (front-end share; the fake server answers in 50 ms) ── */
if (run('speed')) {
  const t0 = Date.now();
  const { ctx, page } = await open(browser, DESKTOP, { delay: () => 50 });
  await rows(page).first().waitFor();
  const first = Date.now() - t0;
  check('F-DATABANK-25 first rows within 1.5 s of opening', first < 1500, `${first} ms`);
  const st = async (what, act, cond) => {
    const ms = await timeClick(page, act, cond);
    check(`F-DATABANK-25 ${what} shows its rows within 500 ms`, ms >= 0 && ms < 500, `${ms} ms`);
  };
  await st('a sort', head(page, 'Title'), () => document.querySelector('[data-testid="db-table"] [data-row]')?.getAttribute('data-row') === 'm1' && !document.querySelector('[aria-busy="true"]'));
  await st('a page change', page.getByRole('button', { name: 'Next page' }), () => document.querySelector('[data-testid="db-status"]')?.textContent.includes('Page 2') && !document.querySelector('[aria-busy="true"]'));
  await st('Mpeg', page.getByTestId('db-mpeg'), () => /View 4\d\d/.test(document.querySelector('[data-testid="db-view"]')?.textContent) && !document.querySelector('[aria-busy="true"]'));
  await st('a tab change', page.getByRole('tab', { name: 'Jingles' }), () => document.querySelector('[data-testid="db-table"] [data-row^="j"]'));
  await page.getByRole('tab', { name: 'Songs' }).click();
  await rows(page).first().waitFor();
  await page.getByTestId('db-find').fill('song 1');
  const ms = await timeUntil(page, () => /View 1\d\d\b/.test(document.querySelector('[data-testid="db-view"]')?.textContent || '') && !document.querySelector('[aria-busy="true"]'));
  check('F-DATABANK-25 Find shows its rows within 500 ms (300 ms wait included)', ms >= 0 && ms < 500, `${ms} ms`);
  await page.getByTestId('db-type').selectOption('Lively');
  const ms2 = await timeUntil(page, () => !document.querySelector('[aria-busy="true"]') && document.querySelectorAll('[data-testid="db-table"] [data-row]').length > 0);
  check('F-DATABANK-25 type filter shows its rows within 500 ms', ms2 >= 0 && ms2 < 500, `${ms2} ms`);
  await ctx.close();
}

await browser.close();
process.exit(done());
