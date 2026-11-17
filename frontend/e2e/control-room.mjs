/**
 * Browser check for the control room (/admin/dashboard) against a fake,
 * in-memory backend. No real station is touched.
 *
 *   cd frontend && npm run build && npx vite preview --port 4173 &
 *   node e2e/control-room.mjs            # optional: BASE_URL, SHOTS_DIR, UI_VERSION=v2
 *   API_DELAY_MS=300 node e2e/control-room.mjs   # slow fake server: also checks sends show up at once
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
const DELAY = Number(process.env.API_DELAY_MS || 0);
let failures = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
  if (!ok) failures++;
};

function makeBackend(queueSize = 60) {
  const assets = [];
  const types = ['music', 'jingle', 'spot', 'shiur', 'zmanim'];
  for (let i = 1; i <= 140; i++) {
    assets.push({ id: `m${i}`, title: i % 7 === 0 ? `Nigun Simcha ${i}` : `Song number ${i} with a fairly long descriptive title`, artist: `Artist ${i % 9}`, album: null, duration: 150 + (i % 90), asset_type: 'music', category: i % 3 ? 'lively' : 'slow', });
  }
  for (let i = 1; i <= 9; i++) assets.push({ id: `j${i}`, title: `Station ID ${i}`, artist: null, album: null, duration: 6 + i, asset_type: 'jingle', category: 'ids', intro_seconds: i === 3 ? 6 : null });
  for (let i = 1; i <= 4; i++) assets.push({ id: `s${i}`, title: `Kol Berama sponsor business ${i}`, artist: null, album: null, duration: 30, asset_type: 'spot', category: null });
  const byId = Object.fromEntries(assets.map(a => [a.id, a]));
  let seq = 0;
  let queue = []; // {id, asset_id, position, status, started_at}
  for (let i = 1; i <= queueSize; i++) queue.push({ id: `q${++seq}`, asset_id: `m${((i - 1) % 140) + 1}`, position: i, status: i === 1 ? 'playing' : 'pending', started_at: i === 1 ? new Date(Date.now() - 20_000).toISOString() : null });
  const calls = [];
  const fired = new Map();
  let loopId = null;
  const pending = () => queue.filter(e => e.status === 'pending').sort((a, b) => a.position - b.position);
  const playing = () => queue.find(e => e.status === 'playing');
  const view = (limit = Infinity) => {
    const list = [playing(), ...pending()].filter(Boolean);
    let cursor = Date.now();
    const entries = list.map(e => {
      const a = byId[e.asset_id];
      const est = e.status === 'playing' ? e.started_at : new Date(cursor).toISOString();
      cursor += a.duration * 1000;
      return { id: e.id, station_id: SID, asset_id: e.asset_id, position: e.position, status: e.status, estimated_start: est, source: 'manual', preempt_at: null, asset: { id: a.id, title: a.title, artist: a.artist, duration: a.duration, asset_type: a.asset_type, category: a.category, intro_seconds: a.intro_seconds ?? null } };
    });
    const p = entries.find(e => e.status === 'playing');
    return { loop_asset_id: loopId, entries: entries.slice(0, limit), total: Math.min(limit, entries.length), total_all: entries.length, now_playing: p ? { ...p, started_at: playing().started_at } : null, queue_duration_seconds: 9000, total_queue_duration_seconds: 9000, preempt_fade_ms: 2000 };
  };
  const handle = (method, path, query, body) => {
    calls.push({ method, path, query, body });
    const q = `/stations/${SID}/queue`;
    if (path === '/auth/me') return { id: 'u1', email: 'operator@example.com', role: 'admin', is_active: true };
    if (path === '/stations') return { stations: [{ id: SID, name: 'Kol Berama', timezone: 'America/New_York', is_active: true, channels: [] }, { id: 'st-2', name: 'Second Feed', timezone: 'America/New_York', is_active: true, channels: [] }, { id: 'st-down', name: 'Unreachable Feed', timezone: 'America/New_York', is_active: true, channels: [] }], total: 3 };
    if (path === '/asset-types') return types.map((name, i) => ({ id: `t${i}`, name }));
    if (path === '/categories') return [];
    if (/^\/assets\/[^/]+\/audio-url$/.test(path)) return { url: 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA=' };
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
    if (path === q && method === 'GET') return view(+(query.limit || Infinity));
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
    if (path === `${q}/fire-now`) {
      if (body.client_request_id && fired.has(body.client_request_id)) return { ...fired.get(body.client_request_id), duplicate: true };
      pending().forEach(e => { e.position += 1; });
      const cur = playing();
      const e = { id: `q${++seq}`, asset_id: body.asset_id, position: cur ? cur.position + 1 : 1, status: 'playing', started_at: new Date().toISOString() };
      if (cur) cur.status = 'skipped';
      queue.push(e);
      const done = { id: e.id, position: e.position, on_air: true, queue: view() };
      if (body.client_request_id) fired.set(body.client_request_id, done);
      return done;
    }
    if (path === `${q}/loop`) { loopId = body.on ? (playing()?.asset_id ?? null) : null; return { loop: !!loopId, asset_id: loopId, queue: view() }; }
    if (path === `${q}/mix-over`) return { message: 'Playing over', asset_id: body.asset_id, on_air: true };
    if (path === `${q}/insert`) {
      const anchor = body.after_entry_id ? queue.find(x => x.id === body.after_entry_id && x.status !== 'skipped') : null;
      const after = anchor ? anchor.position : (playing()?.position ?? 0);
      const ids = [...(body.asset_id ? [body.asset_id] : []), ...(body.asset_ids || [])];
      pending().forEach(x => { if (x.position > after) x.position += ids.length; });
      const made = ids.map((asset_id, i) => { const e = { id: `q${++seq}`, asset_id, position: after + 1 + i, status: 'pending' }; queue.push(e); return e.id; });
      return { ids: made, queue: view() };
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

async function open(browser, contextOpts, queueSize) {
  const backend = makeBackend(queueSize);
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
    if (path.startsWith('/stations/st-down/')) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"down"}' });
    const data = backend.handle(req.method(), path, Object.fromEntries(url.searchParams), body);
    if (DELAY) await new Promise(r => setTimeout(r, DELAY));
    await route.fulfill({ status: data === null ? 204 : 200, contentType: 'application/json', body: data === null ? '' : JSON.stringify(data) });
  });
  // Stand in for the now-playing push channel so a check can announce a track change.
  const sockets = [];
  await page.routeWebSocket(/\/ws\/now-playing\//, (ws) => { sockets.push(ws); });
  const push = (data) => sockets.forEach(ws => { try { ws.send(JSON.stringify({ type: 'now_playing', data })); } catch { /* closed */ } });
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  const pushRaw = (msg) => sockets.forEach(ws => { try { ws.send(JSON.stringify(msg)); } catch { /* closed */ } });
  return { page, backend, ctx, errors, push, pushRaw };
}
const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: `${SHOTS}/${name}.png` }); } };
const layout = (page) => page.evaluate(() => {
  const r = (sel) => { const el = document.querySelector(sel); if (!el) return null; const b = el.getBoundingClientRect(); return { top: Math.round(b.top), left: Math.round(b.left), right: Math.round(b.right), bottom: Math.round(b.bottom) }; };
  return { vw: innerWidth, vh: innerHeight, docH: document.documentElement.scrollHeight, docW: document.documentElement.scrollWidth, room: r('[data-testid=control-room]'), header: r('[data-testid=control-room] header'), nav: r('nav') };
});

async function desktop(browser, width, height) {
  const tag = `desktop ${width}x${height}`;
  const { page, backend, ctx, errors, push, pushRaw } = await open(browser, { viewport: { width, height } });
  let l = await layout(page);
  check(`${tag}: uses the full width`, l.room.left === 0 && l.room.right === l.vw, `${l.room.left}..${l.room.right} of ${l.vw}`);
  check(`${tag}: fills the height with no page scrollbars`, l.docH <= l.vh && l.docW <= l.vw && l.room.bottom === l.vh, `doc ${l.docW}x${l.docH}`);
  await page.getByTestId('queue-scroll').evaluate(el => { el.scrollTop = 1500; });
  await page.waitForTimeout(200);
  const scrolled = await page.getByTestId('queue-scroll').evaluate(el => el.scrollTop);
  const l2 = await layout(page);
  check(`${tag}: queue scrolls while the header stays put`, scrolled > 600 && l2.header.top === l.header.top && l2.nav.top === 0, `scrollTop ${scrolled}, header top ${l2.header.top}`);
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
  const shownMs = await page.evaluate(() => new Promise((resolve) => {
    const btn = [...document.querySelector('[data-testid=library-row]').querySelectorAll('button')].find(b => b.textContent.trim() === 'Next');
    const t0 = performance.now();
    btn.click();
    const look = () => {
      const second = document.querySelectorAll('[data-testid=queue-row]')[1];
      if (/Song number 100 /.test(second?.textContent || '')) resolve(Math.round(performance.now() - t0));
      else if (performance.now() - t0 > 5000) resolve(-1);
      else requestAnimationFrame(look);
    };
    requestAnimationFrame(look);
  }));
  if (DELAY >= 200) check(`${tag}: "Next" shows in the queue before the slow server answers`, shownMs >= 0 && shownMs < DELAY, `${shownMs} ms with a ${DELAY} ms server`);
  await page.waitForTimeout(400 + DELAY * 3);
  check(`${tag}: "Next" on a library song queues it right after the current item`, backend.order()[1] === 'm100');

  // Instant button, play-now mode
  const jingle = page.getByTestId('hot-button').filter({ hasText: 'Station ID 3' });
  await jingle.click();
  await page.waitForFunction(() => /Station ID 3/.test(document.querySelector('[data-testid=control-room] header')?.textContent || ''));
  check(`${tag}: instant button puts the jingle on air immediately`, backend.nowId() === 'j3');
  check(`${tag}: play now is one request`, backend.calls.filter(c => c.path.endsWith('/queue/fire-now')).length === 1 && !backend.calls.some(c => c.path.endsWith('/queue/skip')));
  const introText = await page.locator('[data-testid=intro-countdown]:visible').first().textContent().catch(() => '');
  check(`${tag}: intro countdown shows while the intro runs`, /(0:0[1-6]\s*Intro|Intro\s*0:0[1-6])/i.test(introText), introText);
  await shot(page, `${width}-3-jingle-on-air`);

  // Play-next mode
  await page.getByRole('radio', { name: 'Play next' }).click();
  await page.getByTestId('hot-button').filter({ hasText: 'Station ID 5' }).click();
  await page.waitForTimeout(400);
  check(`${tag}: in "Play next" mode the jingle waits for the current item`, backend.nowId() === 'j3' && backend.order()[1] === 'j5');

  // Mix over: the jingle plays on top, nothing on air or in the queue moves
  const mixBefore = { now: backend.nowId(), order: backend.order().join() };
  await page.getByRole('radio', { name: 'Mix over' }).click();
  await page.getByTestId('hot-button').filter({ hasText: 'Station ID 6' }).click();
  await page.waitForTimeout(300 + DELAY);
  const mixes = backend.calls.filter(c => c.path.endsWith('/queue/mix-over')).map(c => c.body.asset_id);
  check(`${tag}: Mix over plays a button on top and leaves the queue alone`, mixes.join() === 'j6' && backend.nowId() === mixBefore.now && backend.order().join() === mixBefore.order, mixes.join());
  await shot(page, `${width}-8-mix-over`);
  // The station's answer arrives on the now-playing socket; this browser's audio engine plays it
  await page.locator('button:visible', { hasText: 'Listen' }).first().click().catch(() => {});
  let mixFetched = false;
  await page.route('**/mix-test.wav', (route) => {
    mixFetched = true;
    const rate = 8000, n = 800, buf = Buffer.alloc(44 + n);
    buf.write('RIFF', 0); buf.writeUInt32LE(36 + n, 4); buf.write('WAVEfmt ', 8); buf.writeUInt32LE(16, 16);
    buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22); buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate, 28);
    buf.writeUInt16LE(1, 32); buf.writeUInt16LE(8, 34); buf.write('data', 36); buf.writeUInt32LE(n, 40); buf.fill(128, 44);
    return route.fulfill({ status: 200, contentType: 'audio/wav', body: buf });
  });
  pushRaw({ type: 'mix_over', data: { url: `${new URL(page.url()).origin}/mix-test.wav`, asset_id: 'j6', title: 'Station ID 6', replay_gain_db: 0 } });
  await page.waitForTimeout(800);
  check(`${tag}: a mix-over message from the station is played by this browser`, mixFetched);
  await page.getByRole('radio', { name: 'Play next' }).click();

  // NEXT (button and keyboard)
  await page.getByRole('button', { name: /Go next/ }).click();
  await page.waitForFunction(() => /Station ID 5/.test(document.querySelector('[data-testid=control-room] header')?.textContent || ''));
  await page.locator('body').click({ position: { x: 5, y: 300 } }).catch(() => {});
  await page.keyboard.press('n');
  await page.waitForTimeout(500);
  const skips = () => backend.calls.filter(c => c.path.endsWith('/queue/skip')).map(c => c.query.fade);
  check(`${tag}: NEXT and N cut with no fade`, skips().join() === 'hard,hard', skips().join());
  check(`${tag}: NEXT button and the N key both advance`, backend.nowId() === 'm100', `now ${backend.nowId()}`);

  // Loop
  const loopBtn = page.locator('[data-testid=loop]:visible').first();
  await loopBtn.click();
  await page.waitForFunction(() => [...document.querySelectorAll('[data-testid=loop]')].every(b => b.getAttribute('aria-pressed') === 'true'));
  await shot(page, `${width}-7-loop`);
  await loopBtn.click();
  await page.waitForFunction(() => [...document.querySelectorAll('[data-testid=loop]')].every(b => b.getAttribute('aria-pressed') === 'false'));
  const loops = backend.calls.filter(c => c.path.endsWith('/queue/loop')).map(c => c.body.on);
  check(`${tag}: Loop lights when on and switches off again`, loops.join() === 'true,false', loops.join());

  // Fade and Soft fade
  await page.locator('[data-testid=next-fade]:visible').first().click();
  await page.waitForTimeout(300 + DELAY);
  await page.locator('[data-testid=next-soft]:visible').first().click();
  await page.waitForTimeout(300 + DELAY);
  check(`${tag}: Fade and Soft fade ask for their own fade`, skips().slice(-2).join() === 'fade,soft', skips().join());

  // Pin a library item as an instant button
  await page.getByRole('searchbox').fill('Song number 50');
  await page.waitForFunction(() => { const rows = document.querySelectorAll('[data-testid=library-row]'); return rows.length === 1 && /Song number 50 /.test(rows[0].textContent); });
  await page.getByRole('button', { name: 'Add to instant buttons' }).click();
  await page.waitForTimeout(300);
  await shot(page, `${width}-5-pinned`);
  check(`${tag}: starred library item becomes an instant button`, await page.getByTestId('hot-button').filter({ hasText: 'Song number 50' }).count() === 1);

  // Cart pages and named sets, like the jingle cart operators know
  const pinned50 = () => page.getByTestId('hot-button').filter({ hasText: 'Song number 50' }).count();
  await page.getByTestId('cart-page').nth(1).click();
  check(`${tag}: cart page 2 starts empty`, await pinned50() === 0);
  await page.getByRole('button', { name: 'Add to instant buttons' }).click();
  check(`${tag}: a button can be put on page 2`, await pinned50() === 1);
  await page.getByTestId('cart-page').nth(2).click();
  check(`${tag}: page 3 is separate from page 2`, await pinned50() === 0);
  await page.getByTestId('cart-page').nth(0).click();
  check(`${tag}: page 1 keeps its button`, await pinned50() === 1);
  page.once('dialog', d => d.accept('Purim'));
  await page.getByTestId('cart').getByRole('button', { name: 'Edit', exact: true }).click();
  await page.getByRole('button', { name: 'New set' }).click();
  check(`${tag}: a new named set starts empty and is selected`, await pinned50() === 0 && await page.getByLabel('Button set').evaluate(el => el.selectedOptions[0].textContent) === 'Purim');
  await page.getByLabel('Button set').selectOption({ label: 'Default' });
  check(`${tag}: switching back to the first set restores its buttons`, await pinned50() === 1);
  await page.reload();
  await page.getByTestId('queue-row').first().waitFor();
  check(`${tag}: sets and pages survive a reload`, await pinned50() === 1 && await page.getByLabel('Button set').locator('option').count() === 2);
  const air = await page.getByTestId('air-time').nth(1).textContent();
  check(`${tag}: queue rows show air time to the second`, /^\d{1,2}:\d{2}:\d{2}\s*(AM|PM)/.test(air.trim()), air);

  // PFL: a button is heard off air and nothing reaches the station
  const pflBefore = { now: backend.nowId(), order: backend.order().join(), writes: backend.calls.filter(c => c.method !== 'GET').length };
  await page.getByTestId('pfl-toggle').click();
  await page.getByTestId('hot-button').filter({ hasText: 'Station ID 4' }).click();
  await page.waitForTimeout(300 + DELAY);
  await shot(page, `${width}-6-pfl`);
  check(`${tag}: in PFL a button asks for its audio and does not go on air`,
    backend.calls.some(c => c.path === '/assets/j4/audio-url')
      && backend.nowId() === pflBefore.now && backend.order().join() === pflBefore.order
      && backend.calls.filter(c => c.method !== 'GET').length === pflBefore.writes);
  await page.getByTestId('pfl-banner').getByRole('button', { name: 'Done' }).click();
  check(`${tag}: leaving PFL puts the buttons back on air duty`, await page.getByTestId('pfl-banner').count() === 0);

  // A pushed track change refreshes the queue at once, not at the next 10 second poll
  const track = (id, at) => ({ station_id: SID, asset_id: id, started_at: new Date(at).toISOString(), asset: { title: 'Pushed song' } });
  push(track('p1', Date.now() - 60000));
  await page.waitForTimeout(300 + DELAY);
  const queueGets = () => backend.calls.filter(c => c.method === 'GET' && c.path === `/stations/${SID}/queue`).length;
  const getsBefore = queueGets();
  const pushedAt = Date.now();
  push(track('p2', Date.now()));
  let refreshMs = -1;
  while (Date.now() - pushedAt < 3000) { if (queueGets() > getsBefore) { refreshMs = Date.now() - pushedAt; break; } await page.waitForTimeout(20); }
  check(`${tag}: a pushed track change refreshes the queue at once`, refreshMs >= 0 && refreshMs < 1500, `${refreshMs} ms`);

  // Other tabs render
  await page.getByRole('tab', { name: 'Go live' }).click();
  check(`${tag}: go-live panel shows the form and scheduled shows`, await page.getByRole('button', { name: /Go live/ }).count() > 0 && await page.getByText('Thursday Night Live').count() === 1);
  await shot(page, `${width}-4-go-live`);
  await page.getByRole('tab', { name: 'Played' }).click();
  await page.getByText('Earlier song').first().waitFor({ timeout: 5000 }).catch(() => {});
  check(`${tag}: played log shows`, await page.getByText('Earlier song').count() === 1);
  await page.getByRole('tab', { name: 'Schedule' }).click();
  await page.getByText('Afternoon music').first().waitFor({ timeout: 5000 }).catch(() => {});
  check(`${tag}: schedule status shows`, await page.getByText('Afternoon music').count() === 1);
  // Panic: a double-click is one tap, a Panic armed on one station never fires on another
  const panics = () => backend.calls.filter(c => c.method === 'POST' && c.path.endsWith('/queue/panic'));
  await page.getByTestId('panic').dblclick();
  await page.waitForTimeout(600);
  check(`${tag}: a double-click on Panic cuts nothing`, panics().length === 0 && (await page.getByTestId('panic').innerText()).includes('Tap again'));
  await page.getByLabel('Station').first().selectOption({ label: 'Second Feed' });
  await page.waitForTimeout(300);
  check(`${tag}: switching station disarms Panic`, (await page.getByTestId('panic').innerText()).trim() === 'Panic');
  await page.getByTestId('panic').click();
  await page.waitForTimeout(100);
  check(`${tag}: Panic armed on one station does not fire on the next`, panics().length === 0);
  await page.getByLabel('Station').first().selectOption({ label: 'Kol Berama' });
  await page.waitForTimeout(300);
  await page.getByTestId('panic').click();
  await page.waitForTimeout(500);
  await page.getByTestId('panic').click();
  await page.waitForTimeout(500);
  check(`${tag}: two separate taps send one Panic for this station`, panics().length === 1 && panics()[0].path === `/stations/${SID}/queue/panic`, panics().map(c => c.path).join(','));
  // A station whose queue cannot be loaded is not shown as stopped with an empty queue
  await page.getByLabel('Station').first().selectOption({ label: 'Unreachable Feed' });
  await page.getByText(/Can't reach the station|Loading the queue/).first().waitFor({ timeout: 5000 }).catch(() => {});
  check(`${tag}: an unreachable queue is not shown as stopped`, await page.getByText(/Can't reach the station|Loading the queue/).count() > 0 && await page.getByText('Stopped. Press Start to begin.').count() === 0 && await page.getByText('The queue is empty.', { exact: false }).count() === 0);
  await page.getByText("Can't reach the station").first().waitFor({ timeout: 20000 }).catch(() => {});
  check(`${tag}: then says it cannot reach the station`, await page.getByText("Can't reach the station", { exact: false }).count() > 0);
  await page.getByLabel('Station').first().selectOption({ label: 'Kol Berama' });

  // Drag bar between Up next and the lower pane: drag it down, Up next grows and stays after a reload
  const qh = () => page.getByTestId('queue-area').evaluate(el => el.getBoundingClientRect().height);
  const h0 = await qh();
  const bar = await page.getByTestId('split-handle').boundingBox();
  await page.mouse.move(bar.x + bar.width / 2, bar.y + bar.height / 2);
  await page.mouse.down();
  await page.mouse.move(bar.x + bar.width / 2, bar.y + 120, { steps: 6 });
  await page.mouse.up();
  const h1 = await qh();
  check(`${tag}: dragging the bar makes Up next taller`, h1 > h0 + 80, `${Math.round(h0)} -> ${Math.round(h1)}`);
  await page.reload();
  await page.getByTestId('split-handle').waitFor();
  await page.waitForTimeout(300);
  const h2 = await qh();
  check(`${tag}: the chosen size is kept after a reload`, Math.abs(h2 - h1) < 12, `${Math.round(h1)} vs ${Math.round(h2)}`);
  const sel = await page.getByLabel('Station').first().evaluate(el => getComputedStyle(el).backgroundColor);
  check(`${tag}: station dropdown is grey like the console`, sel === 'rgb(192, 192, 192)', sel);
  await page.getByTestId('split-handle').dblclick();
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// Phone held sideways (Shmuel, 2026-11-16: "Landscape mode on mobile is unusable": the
// menu, transport and tabs filled the ~300 px height and no queue row was visible).
async function phoneLandscape(browser, contextOpts, tag) {
  const { page, ctx, errors } = await open(browser, contextOpts);
  await page.getByTestId('queue-row').first().waitFor();
  await page.waitForTimeout(400);
  const m = await page.evaluate(() => {
    const vh = window.innerHeight, vw = window.innerWidth;
    const seen = (el) => { const r = el.getBoundingClientRect(); return r.height > 0 && r.top >= -1 && r.bottom <= vh + 1 && r.left >= -1 && r.right <= vw + 1; };
    const rows = [...document.querySelectorAll('[data-testid=queue-row]')].filter(seen).length;
    const next = [...document.querySelectorAll('[data-testid=control-room] button')].find(b => /Go next/.test(b.textContent));
    const remaining = document.querySelector('[data-testid=remaining]');
    const tabs = document.querySelector('[aria-label="Control room sections"]');
    return { rows, vh, vw, docW: document.documentElement.scrollWidth,
      next: !!next && seen(next), remaining: !!remaining && seen(remaining), tabs: !!tabs && seen(tabs) };
  });
  check(`${tag}: at least 4 queue rows visible`, m.rows >= 4, `${m.rows} rows in ${m.vw}x${m.vh}`);
  check(`${tag}: Go next, remaining time and the tabs are on screen with the queue`, m.next && m.remaining && m.tabs, JSON.stringify(m));
  check(`${tag}: no sideways scrolling`, m.docW <= m.vw, `doc ${m.docW} in ${m.vw}`);
  await page.evaluate(() => window.scrollTo(0, 0));
  const menu = await page.evaluate(() => { const n = document.querySelector('nav'); const r = n.getBoundingClientRect(); return r.top >= 0 && r.height > 0; });
  check(`${tag}: the site menu is reachable by scrolling up`, menu);
  await page.evaluate(() => document.querySelector('[data-testid=control-room]').scrollIntoView({ block: 'start' }));
  await shot(page, `${tag.replace(/\s+/g, '-')}`);
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
  await page.locator('[data-testid=queue-row][data-entry-id=q1]').waitFor(); // the list redraws its window after a scroll
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

  await page.getByRole('button', { name: /Go next/ }).tap();
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

// A queue far longer than a day: only the rows on screen may be drawn, and
// everything must still work at the far end of the list.
async function longQueue(browser, contextOpts, tag, rowH) {
  const N = 5000;
  const { page, backend, ctx, errors } = await open(browser, contextOpts, N);
  if (tag.startsWith('phone')) await page.getByRole('button', { name: 'Queue', exact: true }).tap().catch(() => {});
  const scroller = page.getByTestId('queue-scroll');
  const rows = () => page.getByTestId('queue-row').count();
  // Only the first hours come with each poll; the rest when the operator scrolls to the end.
  const queueGets = () => backend.calls.filter(c => c.method === 'GET' && c.path.endsWith('/queue')).map(c => +c.query.limit);
  check(`${tag}: long queue polls only the first rows`, queueGets().length > 0 && queueGets().every(l => l <= 200), `${queueGets()}`);
  await scroller.evaluate(el => { el.scrollTop = el.scrollHeight; });
  await page.waitForFunction(n => document.querySelector('[data-testid=queue-scroll]').scrollHeight >= n, N * rowH, { timeout: 5000 }).catch(() => {});
  check(`${tag}: scrolling to the end loads the rest`, queueGets().some(l => l >= N), `${queueGets()}`);
  await page.waitForTimeout(150);
  const drawn = await rows();
  check(`${tag}: long queue draws only the rows near the screen`, drawn > 5 && drawn < 120, `${drawn} of ${N}`);
  const h = await scroller.evaluate(el => el.scrollHeight);
  check(`${tag}: long queue scrollbar covers the whole queue`, h === N * rowH, `${h} vs ${N * rowH}`);
  // Jump to the middle, then the very end.
  await scroller.evaluate(el => { el.scrollTop = el.scrollHeight / 2; });
  await page.waitForTimeout(150);
  const mid = await scroller.evaluate(el => { const top = el.getBoundingClientRect().top; const r = [...el.querySelectorAll('[data-testid=queue-row]')].find(x => x.getBoundingClientRect().bottom > top + 1); return r?.dataset.entryId; });
  check(`${tag}: long queue shows the right rows mid-list`, Math.abs(Number(String(mid).slice(1)) - N / 2) <= 2, `first visible ${mid}`);
  await scroller.evaluate(el => { el.scrollTop = el.scrollHeight; });
  await page.waitForTimeout(150);
  const last = await scroller.evaluate(el => { const r = el.querySelectorAll('[data-testid=queue-row]'); return r[r.length - 1]?.dataset.entryId; });
  check(`${tag}: long queue reaches the last item`, last === `q${N}`, `last drawn ${last}`);
  check(`${tag}: long queue still draws few rows at the end`, (await rows()) < 120);
  // Insert before the last item, from the far end of the list.
  const lastRow = page.locator(`[data-testid=queue-row][data-entry-id=q${N}]`);
  await lastRow.getByTestId('insert-here').click();
  await page.getByTestId('insert-banner').waitFor();
  if (!tag.startsWith('phone')) { // on a phone the library takes over the screen
    const h2 = await scroller.evaluate(el => el.scrollHeight);
    check(`${tag}: insert marker keeps the list height exact`, h2 === N * rowH + 40, `${h2}`);
  }
  await page.getByTestId('library-row').first().getByRole('button', { name: 'Insert' }).click();
  await page.waitForTimeout(600);
  const order = backend.order();
  check(`${tag}: insert at the far end lands before the last item`, order.length === N + 1 && order[order.length - 2] === 'm1' && order[order.length - 1] === `m${((N - 1) % 140) + 1}`, `${order.slice(-3)}`);
  check(`${tag}: long queue has no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// Switching station: the first station's now-playing socket must stay closed.
// Its close handler used to reconnect to the old station a second later, so the
// control room got both stations and played both (heard live, 2026-11-16).
// Level meters move with the station without pressing Listen: any click starts the
// silent player, and the Listen button stays up (nothing is heard on this computer).
function toneWav(seconds = 600, rate = 8000) {
  const n = seconds * rate;
  const buf = Buffer.alloc(44 + n);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n, 4); buf.write('WAVE', 8);
  buf.write('fmt ', 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate, 28); buf.writeUInt16LE(1, 32); buf.writeUInt16LE(8, 34);
  buf.write('data', 36); buf.writeUInt32LE(n, 40);
  for (let i = 0; i < n; i++) buf[44 + i] = 128 + Math.round(100 * Math.sin(2 * Math.PI * 440 * i / rate));
  return buf;
}
async function metersWithoutListening(browser) {
  const tag = 'meters';
  const { page, ctx, errors } = await open(browser, { viewport: { width: 1440, height: 900 } });
  const tone = toneWav();
  await page.route('**/tone.wav', (route) => route.fulfill({ status: 200, contentType: 'audio/wav', body: tone }));
  await page.route(/\/assets\/[^/]+\/audio-url$/, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ url: `${new URL(page.url()).origin}/tone.wav` }) }));
  await page.reload();
  await page.getByTestId('queue-row').first().waitFor();
  const lit = () => page.locator('header .bg-cyan-400, header .bg-red-500').count();
  await page.waitForTimeout(1500);
  const before = await lit();
  await page.getByText('Up next', { exact: true }).first().click();
  let after = 0;
  for (let i = 0; i < 20 && after === 0; i++) { await page.waitForTimeout(300); after = await lit(); }
  check(`${tag}: meters move without pressing Listen`, after > 0, `${before} -> ${after} lit`);
  // The tone is a sine at 0.78 of full scale: RMS -5.1 dBFS, so 26 of 30
  // segments per side on the -40..0 dBFS scale (both sides together: 52)
  await page.waitForTimeout(1000);
  const steady = await lit();
  check(`${tag}: meter reads the real level (-5 dBFS tone)`, steady >= 48 && steady <= 54, `${steady} of 60 lit`);
  check(`${tag}: the Listen button is still offered (not heard here)`, await page.locator('button:visible', { hasText: 'Listen' }).count() > 0);
  // Listening here, then muted: the meters keep moving (mute only silences this computer)
  await page.locator('button:visible', { hasText: 'Listen' }).first().click();
  await page.waitForTimeout(500);
  await page.getByRole('button', { name: 'Mute' }).first().click();
  await page.waitForTimeout(800);
  const mutedLit = await lit();
  check(`${tag}: meters keep the same level while muted`, mutedLit >= 48 && mutedLit <= 54, `${mutedLit} lit`);
  // Reload while muted (mute is remembered): moving again after the first click
  await page.reload();
  await page.getByTestId('queue-row').first().waitFor();
  await page.getByText('Up next', { exact: true }).first().click();
  let reloadLit = 0;
  for (let i = 0; i < 20 && reloadLit < 48; i++) { await page.waitForTimeout(300); reloadLit = await lit(); }
  check(`${tag}: meters move after a reload while muted`, reloadLit >= 48, `${reloadLit} lit`);
  check(`${tag}: listening and mute are remembered after a reload`, await page.getByRole('button', { name: 'Unmute' }).count() > 0
    && await page.locator('button:visible', { hasText: 'Listen' }).count() === 0);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

async function stationSwitch(browser) {
  const tag = 'station switch';
  const backend = makeBackend();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(() => { localStorage.setItem('access_token', 'test'); localStorage.setItem('refresh_token', 'test'); localStorage.removeItem('radio_last_station'); });
  const page = await ctx.newPage();
  await page.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, '');
    let body = null;
    try { body = req.postDataJSON(); } catch { /* none */ }
    const data = backend.handle(req.method(), path, Object.fromEntries(url.searchParams), body);
    await route.fulfill({ status: data === null ? 204 : 200, contentType: 'application/json', body: data === null ? '' : JSON.stringify(data) });
  });
  const opened = [];
  await page.routeWebSocket(/\/ws\/now-playing\//, (ws) => { opened.push({ url: ws.url(), at: Date.now() }); });
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  await page.waitForTimeout(500);
  const switchedAt = Date.now();
  await page.getByLabel('Station').first().selectOption('st-2');
  await page.waitForTimeout(4000);
  const after = opened.filter(o => o.at >= switchedAt);
  const toOld = after.filter(o => o.url.endsWith(`/now-playing/${SID}`));
  check(`${tag}: no socket to the old station after the switch`, toOld.length === 0, after.map(o => o.url.split('/').pop()).join(', '));
  check(`${tag}: a socket to the new station`, after.some(o => o.url.endsWith('/now-playing/st-2')));
  await ctx.close();
}

// The pre-listen deck: seek anywhere in the item, markers, keys; never touches air
async function pflDeck(browser, contextOpts, tag) {
  const { page, ctx, errors, backend } = await open(browser, contextOpts);
  const tone = toneWav();
  // Like the real file storage, answer byte ranges so the browser can seek
  await page.route('**/tone.wav', (route) => {
    const m = /bytes=(\d+)-(\d*)/.exec(route.request().headers().range || '');
    if (!m) return route.fulfill({ status: 200, contentType: 'audio/wav', headers: { 'accept-ranges': 'bytes' }, body: tone });
    const start = +m[1];
    const end = m[2] ? Math.min(+m[2], tone.length - 1) : tone.length - 1;
    return route.fulfill({ status: 206, contentType: 'audio/wav', body: tone.subarray(start, end + 1),
      headers: { 'accept-ranges': 'bytes', 'content-range': `bytes ${start}-${end}/${tone.length}` } });
  });
  await page.route(/\/assets\/[^/]+\/audio-url$/, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ url: `${new URL(page.url()).origin}/tone.wav` }) }));
  await page.route(/\/api\/v1\/assets\/[^/?]+$/, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    id: 'a1', title: 'Pre-listen test song', artist: 'Test artist', duration: 600, intro_seconds: 12, asset_type: 'music', category: null,
    metadata_extra: { audio_analysis: { cue_in_seconds: 1.5, cross_start_seconds: 590, cue_out_seconds: 596, version: 2 } } }) }));
  const phone = !!contextOpts.isMobile;
  if (phone) await page.getByRole('button', { name: /Library/ }).first().click();
  await page.getByTestId('library-pfl').first().click();
  const deck = page.getByTestId('pfl-deck');
  await deck.waitFor({ timeout: 5000 });
  check(`${tag}: the deck opens with the title`, await deck.getByText('Pre-listen test song').count() > 0);
  const secs = async () => {
    const t = (await page.getByTestId('pfl-elapsed').textContent()) || '0:00.0';
    const [m, s] = t.split(':');
    return +m * 60 + parseFloat(s);
  };
  await page.waitForTimeout(1200);
  check(`${tag}: time runs while it plays`, (await secs()) > 0.3, `${await secs()} s`);
  const before = backend.calls.filter(c => c.method !== 'GET').length;
  // Tap or click half way along the wave: jumps to about 5:00
  const box = await page.getByTestId('pfl-scrub').boundingBox();
  if (phone) await page.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2);
  else await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForTimeout(300);
  const half = await secs();
  check(`${tag}: a tap half way jumps to the middle`, half > 280 && half < 320, `${half} s`);
  if (!phone) {
    // Drag from 25% to 75%
    await page.mouse.move(box.x + box.width * 0.25, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height / 2, { steps: 5 });
    await page.mouse.move(box.x + box.width * 0.75, box.y + box.height / 2, { steps: 5 });
    await page.mouse.up();
    await page.waitForTimeout(300);
    const dragged = await secs();
    check(`${tag}: dragging the playhead jumps there`, dragged > 430 && dragged < 470, `${dragged} s`);
    await page.getByTestId('pfl-scrub').focus();
    const k0 = await secs();
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(200);
    const k1 = await secs();
    check(`${tag}: arrow right moves 5 s on`, k1 - k0 > 4 && k1 - k0 < 7, `${k0} -> ${k1}`);
    await page.keyboard.press('n');
    await page.keyboard.press(' ');
    await page.waitForTimeout(400);
    const p0 = await secs();
    await page.waitForTimeout(700);
    check(`${tag}: Space pauses it`, Math.abs((await secs()) - p0) < 0.05, `${p0} -> ${await secs()}`);
    check(`${tag}: keys in the deck never reach the station (N did not skip)`, !backend.calls.some(c => c.path.endsWith('/skip')));
    // Waveform drawn from the file
    await page.waitForTimeout(800);
    const lit = await page.evaluate(() => {
      const c = document.querySelector('[data-testid=pfl-scrub] canvas');
      const g = c.getContext('2d'); const d = g.getImageData(0, 0, c.width, c.height).data;
      let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i + 1] > 200 && d[i] < 120) n++;
      return n;
    });
    check(`${tag}: the waveform is drawn`, lit > 500, `${lit} bright pixels`);
  }
  // Markers: Mix jumps to 5 s before the mix point
  check(`${tag}: intro, mix and end markers shown`, await deck.getByText('MIX', { exact: true }).count() > 0 && await deck.getByText('INTRO', { exact: true }).count() > 0);
  await deck.getByRole('button', { name: 'Mix', exact: true }).click();
  await page.waitForTimeout(300);
  const mix = await secs();
  check(`${tag}: Jump to Mix lands just before the mix`, mix > 583 && mix < 590, `${mix} s`);
  await deck.getByRole('button', { name: 'Intro end', exact: true }).click();
  await page.waitForTimeout(600);
  await shot(page, tag.replace(/ /g, '-'));
  check(`${tag}: nothing was sent to the station`, backend.calls.filter(c => c.method !== 'GET').length === before);
  await deck.getByRole('button', { name: 'Close pre-listen' }).click();
  await page.waitForTimeout(200);
  check(`${tag}: closing hides the deck`, await page.getByTestId('pfl-deck').count() === 0);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// A file the browser cannot play (old MP2 audio saved as .mp3) keeps the deck open with a clear reason.
async function pflUnplayable(browser, contextOpts, tag) {
  const { page, ctx, errors } = await open(browser, contextOpts);
  // MPEG-1 Layer II frame headers (FF FD) with nothing a browser can decode
  const bad = Buffer.alloc(64 * 1024);
  for (let i = 0; i < bad.length; i += 418) { bad[i] = 0xff; bad[i + 1] = 0xfd; bad[i + 2] = 0x94; bad[i + 3] = 0x44; }
  await page.route('**/bad.mp3', (route) => route.fulfill({ status: 200, contentType: 'audio/mpeg', body: bad }));
  await page.route(/\/assets\/[^/]+\/audio-url$/, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ url: `${new URL(page.url()).origin}/bad.mp3` }) }));
  await page.route(/\/api\/v1\/assets\/[^/?]+$/, (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    id: 'a1', title: 'Hiney Ma Tov', artist: 'Old file', duration: 240, intro_seconds: null, asset_type: 'music', category: null, metadata_extra: {} }) }));
  if (contextOpts.isMobile) await page.getByRole('button', { name: /Library/ }).first().click();
  await page.getByTestId('library-pfl').first().click();
  const msg = page.getByTestId('pfl-error');
  await msg.waitFor({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1000);
  check(`${tag}: the deck stays open`, await page.getByTestId('pfl-deck').count() === 1);
  check(`${tag}: it says the file can't play`, /Can't play this file/.test((await msg.textContent().catch(() => '')) || ''));
  check(`${tag}: play is greyed out`, await page.getByTestId('pfl-playpause').isDisabled());
  await shot(page, tag.replace(/ /g, '-'));
  await page.getByRole('button', { name: 'Close pre-listen' }).click();
  await page.waitForTimeout(200);
  check(`${tag}: close still works`, await page.getByTestId('pfl-deck').count() === 0);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// Playlist steps change on screen at once, while the save is still on its way.
async function playlistsInstant(browser) {
  const tag = 'playlists';
  const backend = makeBackend();
  const slot = (id, position, category) => ({ id, template_id: 'tpl-1', position, asset_type: 'music', category, created_at: '', updated_at: '' });
  let slots = [slot('s1', 0, 'Fast'), slot('s2', 1, 'Relax'), slot('s3', 2, 'Slow')];
  const SLOW = 3000; // the server takes 3 s to answer a change
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(() => { localStorage.setItem('access_token', 'test'); localStorage.setItem('refresh_token', 'test'); });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  await page.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, '');
    const json = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: data === null ? '' : JSON.stringify(data) });
    if (path === '/playlists' && req.method() === 'GET') {
      return json([{ id: 'tpl-1', name: 'Afternoon mix', description: null, station_id: null, is_active: true, slots, created_at: '', updated_at: '' }]);
    }
    if (path === '/playlists/asset-types') return json([{ asset_type: 'music', category: 'Fast' }, { asset_type: 'music', category: 'Relax' }]);
    const m = path.match(/^\/playlists\/slots\/(.+)$/);
    if (m && req.method() === 'DELETE') {
      await new Promise(r => setTimeout(r, SLOW));
      slots = slots.filter(x => x.id !== m[1]);
      return route.fulfill({ status: 204, body: '' });
    }
    if (m && req.method() === 'PATCH') {
      await new Promise(r => setTimeout(r, SLOW));
      const b = req.postDataJSON();
      slots = slots.map(x => (x.id === m[1] ? { ...x, ...b } : x));
      return json(slots.find(x => x.id === m[1]));
    }
    let body = null;
    try { body = req.postDataJSON(); } catch { /* none */ }
    const data = backend.handle(req.method(), path, Object.fromEntries(url.searchParams), body);
    return json(data, data === null ? 204 : 200);
  });
  await page.goto(`${BASE}/admin/playlists`);
  await page.getByText('Afternoon mix').first().click();
  await page.getByText('Relax').first().waitFor();
  const order = () => page.evaluate(() => [...document.querySelectorAll('span.text-indigo-600')].map(e => e.textContent));
  let t = Date.now();
  await page.getByRole('button', { name: 'Remove' }).first().click();
  await page.waitForFunction(() => ![...document.querySelectorAll('span.text-indigo-600')].some(e => e.textContent === 'Fast'), null, { timeout: 1000 }).catch(() => {});
  let took = Date.now() - t;
  check(`${tag}: removing a step shows at once`, !(await order()).includes('Fast') && took < 1000, `${took} ms, ${(await order()).join(',')}`);
  t = Date.now();
  await page.getByRole('button', { name: 'Move down' }).first().click();
  await page.waitForTimeout(300);
  took = Date.now() - t;
  check(`${tag}: moving a step shows at once`, (await order()).join(',') === 'Slow,Relax', `${took} ms, ${(await order()).join(',')}`);
  await page.waitForTimeout(SLOW + 1500);
  check(`${tag}: the saved order stays after the server answers`, (await order()).join(',') === 'Slow,Relax', (await order()).join(','));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
await stationSwitch(browser);
await metersWithoutListening(browser);
await playlistsInstant(browser);
await pflDeck(browser, { viewport: { width: 1440, height: 900 } }, 'pfl deck desktop');
await pflDeck(browser, { ...devices['iPhone 13'] }, 'pfl deck iPhone');
await pflUnplayable(browser, { viewport: { width: 1440, height: 900 } }, 'pfl unplayable desktop');
await pflUnplayable(browser, { ...devices['iPhone 13'] }, 'pfl unplayable iPhone');
if (process.env.ONLY === 'station-switch') { await browser.close(); process.exit(failures ? 1 : 0); }
await desktop(browser, 1440, 900);
await desktop(browser, 2560, 1440);
await desktop(browser, 1024, 768);
await phone(browser, 'iPhone 13');
await phone(browser, 'iPhone SE');
await phoneLandscape(browser, { ...devices['iPhone 13 landscape'] }, 'iPhone 13 landscape');
// iPhone Pro Max in Safari with the tab bar showing (Shmuel's screenshot): about 932 x 300
await phoneLandscape(browser, { viewport: { width: 932, height: 300 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 }, 'iPhone Pro Max landscape in Safari');
await phone(browser, 'iPad Mini');
await longQueue(browser, { viewport: { width: 1440, height: 900 } }, 'desktop long queue', 26);
await longQueue(browser, { ...devices['iPhone 13'] }, 'phone long queue', 66);
await browser.close();
console.log(failures ? `\n${failures} check(s) failed` : '\nAll checks passed');
process.exit(failures ? 1 : 0);
