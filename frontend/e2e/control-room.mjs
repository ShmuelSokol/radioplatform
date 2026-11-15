/**
 * Browser check for the control room (/admin/dashboard) against a fake,
 * in-memory backend. No real station is touched.
 *
 *   cd frontend && npm run build && npx vite preview --port 4173 &
 *   node e2e/control-room.mjs            # optional: BASE_URL, SHOTS_DIR, UI_VERSION=v2
 *
 * Exits non-zero if any check fails.
 */
import fs from 'node:fs';
import { createRequire } from 'node:module';

// require() so a globally installed Playwright is found too (npx playwright / NODE_PATH).
const { chromium, devices } = createRequire(import.meta.url)('playwright');

const BASE = process.env.BASE_URL || 'http://localhost:4173';
const SHOTS = process.env.SHOTS_DIR || '';
const SID = 'st-1';
let failures = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
  if (!ok) failures++;
};

function makeBackend() {
  const assets = [];
  const types = ['music', 'jingle', 'spot', 'shiur', 'zmanim'];
  for (let i = 1; i <= 140; i++) {
    assets.push({ id: `m${i}`, title: i % 7 === 0 ? `Nigun Simcha ${i}` : `Song number ${i} with a fairly long descriptive title`, artist: `Artist ${i % 9}`, album: null, duration: 150 + (i % 90), asset_type: 'music', category: i % 3 ? 'lively' : 'slow' });
  }
  for (let i = 1; i <= 9; i++) assets.push({ id: `j${i}`, title: `Station ID ${i}`, artist: null, album: null, duration: 6 + i, asset_type: 'jingle', category: 'ids' });
  for (let i = 1; i <= 4; i++) assets.push({ id: `s${i}`, title: `Kol Berama sponsor business ${i}`, artist: null, album: null, duration: 30, asset_type: 'spot', category: null });
  const byId = Object.fromEntries(assets.map(a => [a.id, a]));
  let seq = 0;
  let queue = []; // {id, asset_id, position, status, started_at}
  for (let i = 1; i <= 60; i++) queue.push({ id: `q${++seq}`, asset_id: `m${i}`, position: i, status: i === 1 ? 'playing' : 'pending', started_at: i === 1 ? new Date(Date.now() - 20_000).toISOString() : null });
  const calls = [];
  const pending = () => queue.filter(e => e.status === 'pending').sort((a, b) => a.position - b.position);
  const playing = () => queue.find(e => e.status === 'playing');
  const view = () => {
    const list = [playing(), ...pending()].filter(Boolean);
    let cursor = Date.now();
    const entries = list.map(e => {
      const a = byId[e.asset_id];
      const est = e.status === 'playing' ? e.started_at : new Date(cursor).toISOString();
      cursor += a.duration * 1000;
      return { id: e.id, station_id: SID, asset_id: e.asset_id, position: e.position, status: e.status, estimated_start: est, source: 'manual', preempt_at: null, asset: { id: a.id, title: a.title, artist: a.artist, duration: a.duration, asset_type: a.asset_type, category: a.category } };
    });
    const p = entries.find(e => e.status === 'playing');
    return { entries, total: entries.length, total_all: entries.length, now_playing: p ? { ...p, started_at: playing().started_at } : null, queue_duration_seconds: 9000, total_queue_duration_seconds: 9000, preempt_fade_ms: 2000 };
  };
  const handle = (method, path, query, body) => {
    calls.push({ method, path, query, body });
    const q = `/stations/${SID}/queue`;
    if (path === '/auth/me') return { id: 'u1', email: 'operator@example.com', role: 'admin', is_active: true };
    if (path === '/stations') return { stations: [{ id: SID, name: 'Kol Berama', timezone: 'America/New_York', is_active: true, channels: [] }, { id: 'st-2', name: 'Second Feed', timezone: 'America/New_York', is_active: true, channels: [] }], total: 2 };
    if (path === '/asset-types') return types.map((name, i) => ({ id: `t${i}`, name }));
    if (path === '/categories') return [];
    if (path === '/alerts/unresolved-count') return { unresolved_count: 0 };
    if (path === '/alerts') return { alerts: [], total: 0 };
    if (path === '/live-shows') return { shows: [{ id: 'ls1', station_id: SID, title: 'Thursday Night Live', status: 'scheduled', scheduled_start: new Date(Date.now() + 3600e3).toISOString() }], total: 1 };
    if (path === '/schedules/timeline-preview') return { station_id: SID, at_time: new Date().toISOString(), is_blacked_out: false, active_block: { id: 'b', name: 'Afternoon music', schedule_name: 'Weekday', start_time: '12:00', end_time: '18:00', playback_mode: 'shuffle' }, current_blackout: null, next_blackout: null };
    if (path === '/assets') {
      let list = assets;
      if (query.asset_type) list = list.filter(a => a.asset_type === query.asset_type);
      if (query.search) { const s = query.search.toLowerCase(); list = list.filter(a => a.title.toLowerCase().includes(s) || (a.artist || '').toLowerCase().includes(s)); }
      const skip = +(query.skip || 0), limit = +(query.limit || 50);
      return { assets: list.slice(skip, skip + limit), total: list.length };
    }
    if (path.startsWith('/stations/st-2/')) return path.endsWith('/queue') ? { entries: [], total: 0, total_all: 0, now_playing: null, queue_duration_seconds: 0 } : {};
    if (path === q && method === 'GET') return view();
    if (path === `${q}/log`) return { logs: [{ id: 'l1', start_utc: new Date(Date.now() - 300e3).toISOString(), title: 'Earlier song', artist: 'Artist 2', asset_type: 'music', source: 'auto' }] };
    if (path === `${q}/last-played`) return { last_played: {} };
    if (path === `${q}/add`) {
      const pos = Math.max(0, ...pending().map(e => e.position)) + 1;
      const e = { id: `q${++seq}`, asset_id: body.asset_id, position: pos, status: 'pending' };
      queue.push(e);
      return { id: e.id, position: pos, status: 'pending' };
    }
    if (path === `${q}/play-next`) {
      pending().forEach(e => { e.position += 1; });
      const cur = playing();
      const e = { id: `q${++seq}`, asset_id: body.asset_id, position: cur ? cur.position + 1 : 1, status: 'pending' };
      queue.push(e);
      return { id: e.id, position: e.position };
    }
    if (path === `${q}/skip` || path === `${q}/start`) {
      const cur = playing();
      if (cur && path.endsWith('/start')) return { now_playing: cur.asset_id };
      if (cur) cur.status = 'skipped';
      const next = pending()[0];
      if (next) { next.status = 'playing'; next.started_at = new Date().toISOString(); }
      return { message: 'Skipped', now_playing: next ? next.asset_id : null };
    }
    if (path === `${q}/reorder-dnd`) {
      const e = queue.find(x => x.id === body.entry_id);
      const oldPos = e.position, newPos = body.new_position;
      for (const x of pending()) {
        if (x === e) continue;
        if (oldPos < newPos && x.position > oldPos && x.position <= newPos) x.position -= 1;
        if (oldPos > newPos && x.position >= newPos && x.position < oldPos) x.position += 1;
      }
      e.position = newPos;
      return { message: 'Reordered', warnings: [] };
    }
    if (method === 'DELETE' && path.startsWith(`${q}/`)) { queue = queue.filter(x => x.id !== path.split('/').pop()); return null; }
    if (path === `/stations/${SID}/now-playing` || path === `/now-playing/${SID}`) return {};
    return {};
  };
  return { handle, calls, order: () => view().entries.map(e => e.asset_id), nowId: () => playing()?.asset_id };
}

async function open(browser, contextOpts) {
  const backend = makeBackend();
  const ctx = await browser.newContext(contextOpts);
  await ctx.addInitScript((ui) => { localStorage.setItem('access_token', 'test'); localStorage.setItem('refresh_token', 'test'); if (ui) localStorage.setItem('ui_version', ui); }, process.env.UI_VERSION || '');
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, '');
    let body = null;
    try { body = req.postDataJSON(); } catch { /* none */ }
    const data = backend.handle(req.method(), path, Object.fromEntries(url.searchParams), body);
    await route.fulfill({ status: data === null ? 204 : 200, contentType: 'application/json', body: data === null ? '' : JSON.stringify(data) });
  });
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  return { page, backend, ctx, errors };
}
const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: `${SHOTS}/${name}.png` }); } };
const layout = (page) => page.evaluate(() => {
  const r = (sel) => { const el = document.querySelector(sel); if (!el) return null; const b = el.getBoundingClientRect(); return { top: Math.round(b.top), left: Math.round(b.left), right: Math.round(b.right), bottom: Math.round(b.bottom) }; };
  return { vw: innerWidth, vh: innerHeight, docH: document.documentElement.scrollHeight, docW: document.documentElement.scrollWidth, room: r('[data-testid=control-room]'), header: r('[data-testid=control-room] header'), nav: r('nav') };
});

async function desktop(browser, width, height) {
  const tag = `desktop ${width}x${height}`;
  const { page, backend, ctx, errors } = await open(browser, { viewport: { width, height } });
  let l = await layout(page);
  check(`${tag}: uses the full width`, l.room.left === 0 && l.room.right === l.vw, `${l.room.left}..${l.room.right} of ${l.vw}`);
  check(`${tag}: fills the height with no page scrollbars`, l.docH <= l.vh && l.docW <= l.vw && l.room.bottom === l.vh, `doc ${l.docW}x${l.docH}`);
  await page.getByTestId('queue-scroll').evaluate(el => { el.scrollTop = 1500; });
  await page.waitForTimeout(200);
  const scrolled = await page.getByTestId('queue-scroll').evaluate(el => el.scrollTop);
  const l2 = await layout(page);
  check(`${tag}: queue scrolls while the header stays put`, scrolled > 1000 && l2.header.top === l.header.top && l2.nav.top === 0, `scrollTop ${scrolled}, header top ${l2.header.top}`);
  await page.getByTestId('queue-scroll').evaluate(el => { el.scrollTop = 0; });
  await shot(page, `${width}-1-main`);

  // Search the library (server-side)
  await page.keyboard.press('/');
  await page.waitForFunction(() => document.activeElement?.getAttribute('type') === 'search');
  await page.keyboard.type('nigun');
  await page.waitForFunction(() => { const rows = document.querySelectorAll('[data-testid=library-row]'); return rows.length > 0 && [...rows].every(r => /nigun/i.test(r.textContent)); });
  check(`${tag}: "/" focuses search and results come from the server`, backend.calls.some(c => c.path === '/assets' && c.query.search === 'nigun'));

  // Insert at a point in the queue
  const before = backend.order();
  const target = page.getByTestId('queue-row').nth(4);
  const targetAsset = before[4];
  await target.getByTestId('insert-here').click();
  await page.getByTestId('insert-marker').waitFor();
  await page.getByTestId('insert-banner').waitFor();
  await shot(page, `${width}-2-insert-armed`);
  const firstResult = page.getByTestId('library-row').first();
  await firstResult.getByRole('button', { name: 'Insert' }).click();
  await page.getByTestId('toast').waitFor();
  const after = backend.order();
  check(`${tag}: song is inserted exactly at the marked point`, after[4] === 'm7' && after[5] === targetAsset && after.length === before.length + 1, `slot 4 = ${after[4]}, then ${after[5]}`);
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  check(`${tag}: Esc cancels insert mode`, await page.getByTestId('insert-marker').count() === 0);

  // Play next from the library
  await page.getByRole('searchbox').fill('Song number 100');
  await page.waitForFunction(() => { const rows = document.querySelectorAll('[data-testid=library-row]'); return rows.length === 1 && /Song number 100 /.test(rows[0].textContent); });
  await page.getByTestId('library-row').first().getByRole('button', { name: 'Next', exact: true }).click();
  await page.waitForTimeout(400);
  check(`${tag}: "Next" on a library song queues it right after the current item`, backend.order()[1] === 'm100');

  // Instant button, play-now mode
  const jingle = page.getByTestId('hot-button').filter({ hasText: 'Station ID 3' });
  await jingle.click();
  await page.waitForFunction(() => /Station ID 3/.test(document.querySelector('[data-testid=control-room] header')?.textContent || ''));
  check(`${tag}: instant button puts the jingle on air immediately`, backend.nowId() === 'j3');
  await shot(page, `${width}-3-jingle-on-air`);

  // Play-next mode
  await page.getByRole('radio', { name: 'Play next' }).click();
  await page.getByTestId('hot-button').filter({ hasText: 'Station ID 5' }).click();
  await page.waitForTimeout(400);
  check(`${tag}: in "Play next" mode the jingle waits for the current item`, backend.nowId() === 'j3' && backend.order()[1] === 'j5');

  // NEXT (button and keyboard)
  await page.getByRole('button', { name: /NEXT/ }).click();
  await page.waitForFunction(() => /Station ID 5/.test(document.querySelector('[data-testid=control-room] header')?.textContent || ''));
  await page.locator('body').click({ position: { x: 5, y: 300 } }).catch(() => {});
  await page.keyboard.press('n');
  await page.waitForTimeout(500);
  check(`${tag}: NEXT button and the N key both advance`, backend.nowId() === 'm100', `now ${backend.nowId()}`);

  // Pin a library item as an instant button
  await page.getByRole('searchbox').fill('Song number 50');
  await page.waitForFunction(() => { const rows = document.querySelectorAll('[data-testid=library-row]'); return rows.length === 1 && /Song number 50 /.test(rows[0].textContent); });
  await page.getByRole('button', { name: 'Add to instant buttons' }).click();
  await page.waitForTimeout(300);
  await shot(page, `${width}-5-pinned`);
  check(`${tag}: starred library item becomes an instant button`, await page.getByTestId('hot-button').filter({ hasText: 'Song number 50' }).count() === 1);

  // Other tabs render
  await page.getByRole('tab', { name: 'Go live' }).click();
  check(`${tag}: go-live panel shows the form and scheduled shows`, await page.getByRole('button', { name: /Go live/ }).count() > 0 && await page.getByText('Thursday Night Live').count() === 1);
  await shot(page, `${width}-4-go-live`);
  await page.getByRole('tab', { name: 'Played' }).click();
  check(`${tag}: played log shows`, await page.getByText('Earlier song').count() === 1);
  await page.getByRole('tab', { name: 'Schedule' }).click();
  check(`${tag}: schedule status shows`, await page.getByText('Afternoon music').count() === 1);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

async function phone(browser, deviceName) {
  const tag = deviceName;
  const { page, backend, ctx, errors } = await open(browser, { ...devices[deviceName] });
  const l = await layout(page);
  check(`${tag}: fits the screen with no sideways or page scrolling`, l.docW <= l.vw && l.docH <= l.vh && l.room.right === l.vw, `doc ${l.docW}x${l.docH} in ${l.vw}x${l.vh}`);
  const small = await page.evaluate(() => [...document.querySelectorAll('[data-testid=control-room] button')].filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && (r.height < 32 || r.width < 32); }).map(b => `${b.textContent.trim().slice(0, 12)}:${Math.round(b.getBoundingClientRect().width)}x${Math.round(b.getBoundingClientRect().height)}`));
  check(`${tag}: every button is big enough to tap`, small.length === 0, small.slice(0, 5).join(', '));
  await page.getByTestId('queue-scroll').evaluate(el => { el.scrollTop = 1200; });
  await page.waitForTimeout(200);
  const l2 = await layout(page);
  check(`${tag}: header stays frozen while the queue scrolls`, l2.header.top === l.header.top && l2.nav.top === 0);
  await page.getByTestId('queue-scroll').evaluate(el => { el.scrollTop = 0; });
  await shot(page, `${tag.replace(/\s+/g, '-')}-1-queue`);

  // Tap a row, then "Insert before"; lands in the library
  const before = backend.order();
  await page.getByTestId('queue-row').nth(3).tap();
  await page.getByTestId('row-actions').getByRole('button', { name: /Insert before/ }).tap();
  await page.getByTestId('insert-banner').waitFor();
  await page.getByRole('searchbox').fill('nigun');
  await page.waitForFunction(() => { const rows = document.querySelectorAll('[data-testid=library-row]'); return rows.length > 0 && [...rows].every(r => /nigun/i.test(r.textContent)); });
  await shot(page, `${tag.replace(/\s+/g, '-')}-2-insert-search`);
  await page.getByTestId('library-row').first().getByRole('button', { name: 'Insert' }).tap();
  await page.getByTestId('toast').waitFor();
  const after = backend.order();
  check(`${tag}: tap a queue item, search, insert at that point`, after[3] === 'm7' && after[4] === before[3], `slot 3 = ${after[3]}`);
  await page.getByRole('button', { name: 'Done' }).tap();

  // Instant buttons tab
  await page.getByRole('button', { name: 'Buttons' }).tap();
  await shot(page, `${tag.replace(/\s+/g, '-')}-3-buttons`);
  await page.getByTestId('hot-button').filter({ hasText: 'Station ID 2' }).tap();
  await page.waitForFunction(() => /Station ID 2/.test(document.querySelector('[data-testid=control-room] header')?.textContent || ''));
  check(`${tag}: instant button fires the jingle now`, backend.nowId() === 'j2');

  await page.getByRole('button', { name: /NEXT/ }).tap();
  await page.waitForTimeout(500);
  check(`${tag}: NEXT advances`, backend.nowId() !== 'j2');

  await page.getByRole('button', { name: 'Go live', exact: true }).tap();
  check(`${tag}: go-live tab opens`, await page.getByPlaceholder('Show name').count() === 1);
  await shot(page, `${tag.replace(/\s+/g, '-')}-4-go-live`);
  await page.getByRole('button', { name: 'More' }).tap();
  await page.getByText('Earlier song').waitFor({ timeout: 5000 }).catch(() => {});
  check(`${tag}: played log opens`, await page.getByText('Earlier song').count() === 1);
  const l3 = await layout(page);
  check(`${tag}: still no page overflow after using every tab`, l3.docW <= l3.vw && l3.docH <= l3.vh);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
await desktop(browser, 1440, 900);
await desktop(browser, 2560, 1440);
await desktop(browser, 1024, 768);
await phone(browser, 'iPhone 13');
await phone(browser, 'iPhone SE');
await phone(browser, 'iPad Mini');
await browser.close();
console.log(failures ? `\n${failures} check(s) failed` : '\nAll checks passed');
process.exit(failures ? 1 : 0);
