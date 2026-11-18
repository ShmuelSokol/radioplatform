/**
 * Admin screens against a fake backend (lib/fake-station.mjs), desktop and iPhone:
 * the song request "Match" box, the review flow's keys and its "changed by someone
 * else" notice, the Activity page's live updates, what a bug report carries
 * (diagnostics and screenshot), the Databank day log after a station change, and
 * the global player bar.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4312 --strictPort &)
 *   BASE_URL=http://localhost:4312 node e2e/admin-misc.mjs   # optional: ONLY=match,review,activity,diag,daylog,player
 *
 * Rows: F-MATCH-1/2/10/15/16/17/24, V1-140, V2-25, V2-48 (live part), V2-98, V2-99,
 * F-DATABANK-62, F-GLOBAL-10.
 */
import { chromium, checker, sleep, makeStation, adminContext, newPage, routeSiteSocket } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4312';
const want = (n) => !process.env.ONLY || process.env.ONLY.split(',').includes(n);
const KNOWN = {
  'F-GLOBAL-10': 'nothing in the site starts the global player bar: no page calls playerStore.play(), so the bar (volume, Stop) never shows. Owner: wire it to a "play" button or remove it?',
};
const { check, done } = checker(KNOWN);
const DESKTOP = { viewport: { width: 1440, height: 900 } };
const IPHONE = {
  viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true,
  userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
};

async function safe(name, fn) {
  try { await fn(); } catch (e) { check(name, false, String(e).split('\n')[0].slice(0, 300)); }
}

const browser = await chromium.launch();

// ── Song request Match box ──────────────────────────────────────────────────
const LIBRARY = [
  ['One Day', 'Matisyahu'], ['One Day', 'Shlomo Simcha'], ['Slow Nigun', 'Abie'], ['Slowly', 'MBD'],
  ...Array.from({ length: 12 }, (_, i) => [`One More ${i}`, `Singer ${i}`]),
].map(([title, artist], i) => ({ id: `lib${i}`, title, artist, duration: 200, asset_type: 'music' }));

function requestsBackend() {
  const reqs = [
    { id: 'r1', song_title: 'One Day', song_artist: 'Matisyahu', status: 'pending' },
    { id: 'r2', song_title: 'Slow Nigun', song_artist: null, status: 'approved' },
    { id: 'r3', song_title: 'Queued one', status: 'queued' },
    { id: 'r4', song_title: 'Played one', status: 'played' },
    { id: 'r5', song_title: 'Rejected one', status: 'rejected' },
  ].map((r, i) => ({ station_id: 'st-1', station_name: 'Kol Berama', requester_name: `Listener ${i}`, requester_message: null,
    asset_id: null, matched_asset_title: null, matched_asset_artist: null, created_at: new Date(Date.now() - i * 60e3).toISOString(), ...r }));
  const st = { reqs, searches: [], patches: [], lists: 0, failSearch: false, slow: {} };
  st.extra = (method, path, query, body) => {
    if (path === '/song-requests' && method === 'GET') {
      st.lists++;
      const list = reqs.filter((r) => !query.status || r.status === query.status);
      return { requests: list, total: list.length };
    }
    if (path === '/song-requests/analytics/top-requested') return { top_requested: [] };
    const m = path.match(/^\/song-requests\/([^/]+)$/);
    if (m && method === 'PATCH') {
      st.patches.push({ id: m[1], body, at: Date.now() });
      const r = reqs.find((x) => x.id === m[1]);
      if (!r) return { __status: 404, detail: 'Song request not found' };
      if (body.asset_id) {
        const a = LIBRARY.find((x) => x.id === body.asset_id);
        Object.assign(r, { asset_id: a.id, matched_asset_title: a.title, matched_asset_artist: a.artist });
        if (r.status === 'approved') r.status = 'queued';
      }
      if (body.status) r.status = body.status === 'approved' && r.asset_id ? 'queued' : body.status;
      return r;
    }
    if (path === '/assets' && method === 'GET' && query.search !== undefined) {
      st.searches.push({ q: query.search, type: query.asset_type, limit: query.limit, at: Date.now() });
      if (st.failSearch) return { __status: 500, detail: 'boom' };
      if (query.search === 'Sl') return { assets: [{ id: 'stale', title: 'Stale Result', artist: 'Old', asset_type: 'music' }], total: 1 };
      const q = query.search.toLowerCase();
      const hits = LIBRARY.filter((a) => `${a.title} ${a.artist}`.toLowerCase().includes(q)).slice(0, Number(query.limit || 50));
      return { assets: hits, total: hits.length };
    }
    return undefined;
  };
  st.delay = (path, method) => {
    if (path === '/assets' && method === 'GET') return st.slow.search ?? 40;
    if (/^\/song-requests\/[^/]+$/.test(path) && method === 'PATCH') return st.slow.patch ?? 30;
    return 0;
  };
  return st;
}

async function openRequests(opts) {
  const be = requestsBackend();
  const station = makeStation({ extra: be.extra });
  const { ctx } = await adminContext(browser, opts, station, { delay: (p, m) => be.delay(p, m) });
  await routeSiteSocket(ctx);
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/requests`);
  await page.getByText('Listener 0').filter({ visible: true }).first().waitFor({ timeout: 15000 });
  return { be, ctx, page, errors };
}

if (want('match')) {
  for (const [view, opts] of [['desktop', DESKTOP], ['iphone', IPHONE]]) {
    const { be, ctx, page, errors } = await openRequests(opts);
    const buttons = page.locator('[data-testid="match-to-song"]:visible');
    await safe(`F-MATCH-1 ${view}`, async () => {
      const n = await buttons.count();
      const rows = await page.evaluate(() => [...document.querySelectorAll('[data-testid="match-to-song"]')]
        .filter((b) => b.offsetParent).map((b) => (b.closest('tr, .space-y-2 > div')?.textContent || '').match(/Listener \d/)?.[0]));
      check(`F-MATCH-1 ${view}: Match only on the pending and the approved request`, n === 2 && rows.join() === 'Listener 0,Listener 1', `${n}: ${rows.join()}`);
    });
    await safe(`F-MATCH-24 ${view}`, async () => {
      const b = await buttons.first().boundingBox();
      if (view === 'iphone') check('F-MATCH-24 iphone: Match is at least 44 px tall and wide', b.height >= 44 && b.width >= 44, `${Math.round(b.width)}x${Math.round(b.height)}`);
    });
    await safe(`F-MATCH-2 ${view}`, async () => {
      await buttons.first().click();
      const box = page.getByTestId('match-box').filter({ visible: true });
      const input = box.locator('input');
      await input.waitFor();
      const sel = await input.evaluate((el) => [el.value, el.selectionStart, el.selectionEnd, document.activeElement === el]);
      check(`F-MATCH-2 ${view}: box opens with the request's title (F-MATCH-21) focused and selected`, sel[0] === 'One Day' && sel[1] === 0 && sel[2] === 7 && sel[3], JSON.stringify(sel));
      await sleep(400);
      const first = be.searches.at(-1);
      check(`F-MATCH-2 ${view}: searches the music library for up to 8`, first && first.q === 'One Day' && first.type === 'music' && first.limit === '8', JSON.stringify(first));
      be.searches.length = 0;
      await input.fill('');
      await input.pressSequentially('O', { delay: 0 });
      await sleep(350);
      check(`F-MATCH-2 ${view}: one letter does not search`, be.searches.length === 0, `${be.searches.length} searches`);
      be.slow.search = 400;
      const typedAt = Date.now();
      await input.evaluate((el) => el.addEventListener('input', () => { window.__lastKey = Date.now(); }));
      await input.pressSequentially('ne', { delay: 30 });
      const lastKey = await page.evaluate(() => window.__lastKey);
      await box.getByText('Searching…').waitFor({ timeout: 2000 });
      await box.locator('button', { hasText: '—' }).first().waitFor({ timeout: 3000 });
      const s = be.searches;
      check(`F-MATCH-2 ${view}: one search, 200 ms after typing stops`, s.length === 1 && s[0].q === 'One' && s[0].at - lastKey >= 180, `${s.length} searches, ${s[0] ? s[0].at - lastKey : '?'} ms after the last key (typing took ${lastKey - typedAt} ms)`);
      const items = await box.locator('button', { hasText: '—' }).allInnerTexts();
      check(`F-MATCH-2 ${view}: at most 8 songs listed as "title — artist"`, items.length === 8 && items[0].includes('One Day — Matisyahu'), `${items.length}: ${items[0]}`);
      be.slow.search = 40;
      await input.fill('zzz');
      await box.getByText('No songs found').waitFor({ timeout: 2000 });
      check(`F-MATCH-2 ${view}: "No songs found" when nothing comes back`, true);
      // F-MATCH-15 failed search, newest search only
      be.failSearch = true;
      await input.fill('Matisyahu');
      await box.getByText('No songs found').waitFor({ timeout: 2000 });
      check(`F-MATCH-15 ${view}: a failed search shows "No songs found"`, !(await page.getByRole('alert').count()) || !(await page.getByRole('alert').innerText()).includes('boom'));
      be.failSearch = false;
      be.slow.search = 700;
      await input.fill('Sl');
      await sleep(260);   // the "Sl" search is now running (slow, and with a stale answer)
      be.slow.search = 30;
      await input.fill('Slow');
      await sleep(1100);
      const shown = await box.locator('button', { hasText: '—' }).allInnerTexts();
      check(`F-MATCH-15 ${view}: typing fast shows only the newest search's results`, shown.length === 2 && !shown.join().includes('Stale'), shown.join(' | '));
      // F-MATCH-17 phone sizes
      if (view === 'iphone') {
        const bb = await box.locator('div.absolute').boundingBox();
        const fs = await input.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
        const rowH = await box.locator('button', { hasText: '—' }).first().boundingBox();
        const x = await box.getByRole('button', { name: 'Close search' }).boundingBox();
        check('F-MATCH-17 iphone: the box stays on screen', bb.x >= 0 && bb.x + bb.width <= 390, `${Math.round(bb.x)}..${Math.round(bb.x + bb.width)}`);
        check('F-MATCH-17 iphone: the field text is 16 px (no zoom on focus)', fs >= 16, `${fs}px`);
        check('F-MATCH-17 iphone: each result is at least 44 px tall', rowH.height >= 44, `${Math.round(rowH.height)} px`);
        check('F-MATCH-24 iphone: the ✕ is at least 44 px', x.height >= 44 && x.width >= 44, `${Math.round(x.width)}x${Math.round(x.height)}`);
      }
      // F-MATCH-15: Esc and ✕ close without a change
      await input.press('Escape');
      await box.waitFor({ state: 'detached', timeout: 2000 });
      await buttons.first().click();
      await page.getByTestId('match-box').filter({ visible: true }).getByRole('button', { name: 'Close search' }).click();
      await page.getByTestId('match-box').filter({ visible: true }).waitFor({ state: 'detached', timeout: 2000 });
      check(`F-MATCH-15 ${view}: Esc and ✕ close the box, nothing saved`, be.patches.length === 0, `${be.patches.length} saves`);
    });
    await safe(`F-MATCH-10/16 ${view}`, async () => {
      be.slow.search = 60;
      await buttons.first().click();
      const box = page.getByTestId('match-box').filter({ visible: true });
      const input = box.locator('input');
      await input.fill('');
      await input.pressSequentially('Matis', { delay: 20 });
      const stopped = Date.now();
      await box.locator('button', { hasText: 'Matisyahu' }).first().waitFor({ timeout: 3000 });
      const shownMs = Date.now() - stopped;
      check(`F-MATCH-10 ${view}: results on screen within 500 ms of typing stopping`, shownMs <= 500, `${shownMs} ms`);
      be.slow.patch = 500;
      const lists0 = be.lists;
      const t0 = Date.now();
      await box.locator('button', { hasText: 'Matisyahu' }).first().click();
      await sleep(150);
      const greyed = await page.evaluate(() => [...document.querySelectorAll('[data-testid="match-to-song"]')].filter((b) => b.offsetParent).map((b) => b.disabled));
      check(`F-MATCH-16 ${view}: while the save runs every Match button is greyed`, greyed.length > 0 && greyed.every(Boolean), JSON.stringify(greyed));
      for (let i = 0; i < 100 && be.lists === lists0; i++) await sleep(20);
      await page.waitForFunction(() => [...document.querySelectorAll('[data-testid="match-to-song"]')].filter((b) => b.offsetParent).every((b) => !b.disabled), null, { timeout: 3000 });
      const confirmMs = Date.now() - t0 - 500;
      check(`F-MATCH-10 ${view}: a pick is confirmed within 1 s (plus the fake 0.5 s server wait)`, confirmMs <= 1000, `${confirmMs} ms after the save answered`);
      check(`F-MATCH-16 ${view}: the list refreshes after the save`, be.lists > lists0, `${be.lists - lists0} reloads`);
      be.slow.patch = 30;
      await page.getByRole('button', { name: 'Pending', exact: false }).first().click();
      await sleep(400);
      const pendingBefore = await page.getByText('Listener 0').filter({ visible: true }).count();
      // Approve the matched pending request: it moves to the Queued tab
      const approve = page.locator('button:visible', { hasText: /^Approve$/ }).first();
      await approve.click();
      await sleep(600);
      const pendingAfter = await page.getByText('Listener 0').filter({ visible: true }).count();
      await page.getByRole('button', { name: 'Queued', exact: false }).first().click();
      await page.getByText('Listener 0').filter({ visible: true }).first().waitFor({ timeout: 3000 });
      check(`F-MATCH-16 ${view}: the request moves to its new status tab`, pendingBefore === 1 && pendingAfter === 0, `pending ${pendingBefore} -> ${pendingAfter}`);
    });
    check(`F-MATCH ${view}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── Review flow: keys and the "changed by someone else" notice ──────────────
if (want('review')) {
  const items = [1, 2, 3].map((n) => ({
    id: `ri${n}`, queue_id: 'q1', asset_id: `m${n}`, position: n, status: 'pending', assigned_to: null, notes: null, version: 1,
    asset: { id: `m${n}`, title: `Review Song ${n}`, artist: 'Artist', duration: 180, asset_type: 'music', category: 'lively', metadata_extra: {} },
  }));
  const patches = [];
  let conflictNext = 0;
  const extra = (method, path, query, body) => {
    if (path === '/reviews/queues/q1') return { id: 'q1', name: 'E2E queue', status: 'in_progress', total_items: 3, reviewed_items: 0 };
    if (path === '/reviews/queues/q1/items') return { items, total: items.length };
    if (/^\/reviews\/assets\/[^/]+\/history$/.test(path) || path === '/reviews/queues/q1/activity') return [];
    const m = path.match(/^\/reviews\/items\/([^/]+)$/);
    if (m && method === 'PATCH') {
      patches.push({ id: m[1], body });
      const it = items.find((x) => x.id === m[1]);
      if (conflictNext > 0) {
        conflictNext--;
        Object.assign(it, { status: 'rejected', version: it.version + 1 });   // the other reviewer's decision
        return { __status: 409, detail: 'Item was modified by another user' };
      }
      if (body.version !== it.version) return { __status: 409, detail: 'Item was modified by another user' };
      Object.assign(it, { status: body.status ?? it.status, notes: body.notes ?? it.notes, version: it.version + 1 });
      return it;
    }
    return undefined;
  };
  const station = makeStation({ extra });
  const { ctx } = await adminContext(browser, DESKTOP, station);
  await routeSiteSocket(ctx);
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/reviews/q1`);
  const counter = page.getByText(/Reviewing \d of 3/);
  await safe('V2-25', async () => {
    await page.getByText('Reviewing 1 of 3').waitFor({ timeout: 15000 });
    conflictNext = 1;
    await page.locator('h1').first().click();
    await page.keyboard.press('a');
    const alert = page.getByTestId('review-save-error');
    await alert.waitFor({ timeout: 3000 });
    const text = await alert.innerText();
    check('V2-25 a stale save tells the reviewer someone else changed the item, and does not advance',
      /someone else changed this item/i.test(text) && (await counter.innerText()) === 'Reviewing 1 of 3' && (await alert.getAttribute('role')) === 'alert', text);
    await page.getByText('rejected', { exact: true }).first().waitFor({ timeout: 3000 });
    check('V2-25 the item shows the other reviewer\'s decision', true);
    await page.getByRole('button', { name: 'Approve (A)' }).click();
    await page.getByText('Reviewing 2 of 3').waitFor({ timeout: 3000 });
    check('V2-25 pressing again saves (with the new version) and moves on; the notice goes',
      patches.at(-1).body.version === 2 && patches.at(-1).body.status === 'approved' && !(await alert.count()), JSON.stringify(patches.at(-1).body));
  });
  await safe('V1-140', async () => {
    const n0 = patches.length;
    const tempo = page.locator('select').first();
    await tempo.focus();
    await page.keyboard.press('a');
    await page.keyboard.press('r');
    await sleep(400);
    check('V1-140 letters typed in a drop-down (Tempo) do not approve or reject', patches.length === n0 && (await counter.innerText()) === 'Reviewing 2 of 3', `${patches.length - n0} saves`);
    await page.getByPlaceholder('Add notes about this asset...').click();
    await page.keyboard.type('fsar');
    await sleep(300);
    check('V1-140 letters typed in the notes do nothing', patches.length === n0);
    await page.locator('h1').first().click();
    await page.keyboard.press('Control+r').catch(() => {});
    await sleep(300);
    check('V1-140 Ctrl+R (reload) is not taken as Reject', patches.length === n0, `${patches.length - n0} saves`);
    await page.keyboard.press('f');
    await page.getByText('Reviewing 3 of 3').waitFor({ timeout: 3000 });
    check('V1-140 F flags (with the typed notes)', patches.at(-1).body.status === 'flagged' && patches.at(-1).body.notes === 'fsar', JSON.stringify(patches.at(-1).body));
    const scroll0 = await page.evaluate(() => window.scrollY);
    await page.evaluate(() => { window.__spaceDefault = null; window.addEventListener('keydown', (e) => { if (e.key === ' ') setTimeout(() => { window.__spaceDefault = e.defaultPrevented; }); }, { once: true }); });
    await page.keyboard.press(' ');
    await sleep(300);
    const prevented = await page.evaluate(() => window.__spaceDefault);
    check('V1-140 Space is play/pause (taken by the page, no scroll, no save)', prevented === true && patches.length === n0 + 1 && (await page.evaluate(() => window.scrollY)) === scroll0, `defaultPrevented=${prevented}`);
    await page.keyboard.press('s');
    await sleep(500);
    check('V1-140 S skips', patches.at(-1).body.status === 'skipped');
  });
  check('review: no script errors', errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Activity page live updates (V2-48, browser part) ────────────────────────
if (want('activity')) {
  const t = (s) => new Date(Date.now() - s * 1000).toISOString();
  const rows = Array.from({ length: 50 }, (_, i) => ({ id: `a${i}`, created_at: t(120 + i * 60), label: `Pressed Next ${i}`, category: 'control', user_email: 'op@example.com' }));
  const calls = [];
  let fresh = null;
  const extra = (method, path, query) => {
    if (path === '/activity/filters') return { users: [], stations: [], categories: [{ id: 'control', label: 'Control room' }] };
    if (path === '/activity/people') return { people: [] };
    if (path === '/activity') {
      calls.push({ ...query, at: Date.now() });
      if (query.after) return { total: null, items: fresh && fresh.created_at > query.after ? [fresh] : [] };
      const skip = Number(query.skip || 0);
      return { total: 120, items: skip ? rows.map((r) => ({ ...r, id: `p${skip}-${r.id}`, label: `Older ${r.label}` })) : rows };
    }
    return undefined;
  };
  const station = makeStation({ extra });
  const { ctx } = await adminContext(browser, DESKTOP, station, { storage: { 'activity-tab': 'staff', 'activity-view': 'all' } });
  await routeSiteSocket(ctx);
  const { page, errors } = await newPage(ctx);
  await safe('V2-48 live', async () => {
    await page.goto(`${BASE}/admin/activity`);
    await page.getByText('Pressed Next 0').first().waitFor({ timeout: 15000 });
    await sleep(500);
    fresh = { id: 'new1', created_at: t(1), label: 'Started the show', category: 'control', user_email: 'op@example.com' };
    await page.getByText('Started the show').waitFor({ timeout: 8000 });
    const live = calls.filter((c) => c.after);
    const lookBack = new Date(rows[0].created_at).getTime() - new Date(live[0].after).getTime();
    check('V2-48 live: polls every few seconds for rows newer than the newest shown minus 30 s', live.length >= 1 && Math.abs(lookBack - 30000) < 50, `${live.length} polls, look-back ${lookBack} ms`);
    const first = await page.getByTestId('activity-row').first().innerText();
    check('V2-48 live: a new row goes on top', first.includes('Started the show'), first.slice(0, 60));
    await page.getByRole('button', { name: 'Older ›' }).first().click();
    await page.getByText('Older Pressed Next 0').first().waitFor({ timeout: 5000 });
    const n = calls.filter((c) => c.after).length;
    await sleep(11000);
    check('V2-48 live: stops on page 2', calls.filter((c) => c.after).length === n, `${calls.filter((c) => c.after).length - n} polls on page 2`);
    await page.getByRole('button', { name: '‹ Newer' }).first().click();
    await page.getByText('Pressed Next 0').first().waitFor({ timeout: 5000 });
    const yesterday = new Date(Date.now() - 2 * 86400e3).toISOString().slice(0, 10);
    await page.locator('input[type="date"]').nth(1).fill(yesterday);
    await sleep(800);
    const m = calls.filter((c) => c.after).length;
    await sleep(11000);
    check('V2-48 live: stops when the range ends in the past', calls.filter((c) => c.after).length === m, `${calls.filter((c) => c.after).length - m} polls`);
  });
  check('activity: no script errors', errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Bug report diagnostics and screenshot (V2-98, V2-99) ────────────────────
async function decodeJpegSize(page, dataUrl) {
  return page.evaluate((src) => new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement('canvas');
      c.width = img.width; c.height = img.height;
      const g = c.getContext('2d');
      g.drawImage(img, 0, 0);
      const [r, gg, b] = g.getImageData(Math.floor(img.width / 2), Math.floor(img.height / 2), 1, 1).data;
      resolve({ w: img.width, h: img.height, center: [r, gg, b] });
    };
    img.onerror = () => resolve(null);
    img.src = src;
  }), dataUrl);
}

if (want('diag')) {
  const station = makeStation({ extra: (method, path) => (path === '/bug-reports' && method === 'POST' ? { id: 'b1', number: 77, mirrored: false } : undefined) });
  const ctx0 = await adminContext(browser, { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 }, station,
    { storage: { radio_last_station: 'st-1', my_secret_password: 'not-a-real-password', some_jwt: 'eyJhbGciOiJIUzI1NiJ9.e30.x' } });
  const ctx = ctx0.ctx;
  await routeSiteSocket(ctx);
  await ctx.route('**/__nowhere/**', (r) => r.fulfill({ status: 404, body: 'no' }));
  const { page } = await newPage(ctx);
  const ids = [];
  page.on('request', (r) => { if (/\/api\/v1\//.test(r.url())) ids.push([r.url().replace(/^.*\/api\/v1/, '').split('?')[0], r.headers()['x-request-id'] || '']); });
  const report = () => station.calls.filter((c) => c.path === '/bug-reports' && c.method === 'POST').at(-1)?.body;
  const sendReport = async (what) => {
    await page.locator('button[aria-label="Report a bug"]').click();
    const dlg = page.getByRole('dialog', { name: 'Report a bug' });
    await dlg.locator('#bug-what').fill(what);
    await dlg.getByRole('button', { name: 'Send report' }).click();
    await page.getByText(/77/).first().waitFor({ timeout: 8000 });
    await page.keyboard.press('Escape');
    await dlg.getByRole('button', { name: /Close|Done/ }).first().click().catch(() => {});
    await sleep(300);
  };
  await safe('V2-98', async () => {
    await page.goto(`${BASE}/admin/stations`);
    await page.getByRole('heading', { name: 'Manage Stations' }).waitFor({ timeout: 15000 });
    await page.evaluate(async () => {
      for (let i = 0; i < 450; i++) console.log(`e2e line ${i}`);
      await Promise.all(Array.from({ length: 320 }, (_, i) => fetch(`/__nowhere/${i}`).catch(() => {})));
      setTimeout(() => { throw new Error('e2e uncaught'); });
      Promise.reject(new Error('e2e rejection'));
      new AudioContext();
    });
    await sleep(500);
    await page.evaluate(() => {   // something to leave out of the screenshot (V2-99)
      const d = document.createElement('div');
      d.setAttribute('data-bug-reporter', '');
      d.style.cssText = 'position:fixed;inset:0;background:rgb(255,0,0);z-index:9';
      document.body.appendChild(d);
    });
    await sendReport('E2E diagnostics');
    const body = report();
    const c = body.context;
    const msgs = c.console.map((x) => x.msg);
    check('V2-98 the last 400 console lines', c.console.length === 400 && msgs.includes('e2e line 449') && !msgs.includes('e2e line 0'), `${c.console.length}`);
    check('V2-98 uncaught errors and unhandled rejections are kept', msgs.some((m) => m.includes('Uncaught') && m.includes('e2e uncaught')) && msgs.some((m) => m.includes('Unhandled rejection') && m.includes('e2e rejection')));
    const fetches = c.network.filter((n) => (n.url || '').includes('__nowhere'));
    check('V2-98 the last 300 requests, plain fetch included', c.network.length === 300 && fetches.length > 250 && fetches.at(-1).status === 404, `${c.network.length} (${fetches.length} fetch)`);
    check('V2-98 breadcrumbs (at most 300) with page changes', c.breadcrumbs.length > 0 && c.breadcrumbs.length <= 300 && c.breadcrumbs.some((b) => b.kind === 'page'), `${c.breadcrumbs.length}`);
    check('V2-98 audio elements, audio contexts and React Query states are carried', Array.isArray(c.audio) && c.audio_contexts.length >= 1 && Array.isArray(c.queries) && c.queries.length > 0, `${c.audio_contexts.length} contexts, ${c.queries.length} queries`);
    const keys = Object.keys(c.local_storage);
    const raw = JSON.stringify(body);
    check('V2-98 storage keys like tokens/passwords and JWT values are left out', keys.includes('radio_last_station') && !keys.some((k) => /token|password|jwt/i.test(k)) && !raw.includes('not-a-real-password') && !raw.includes('eyJhbGciOiJIUzI1NiJ9.e30'), keys.join(','));
    const plain = ['/site-release', '/now-playing/'];
    const missing = ids.filter(([p, id]) => !id && !plain.some((x) => p.startsWith(x)));
    check('V2-98 every API call through the client carries an X-Request-ID', ids.length > 2 && missing.length === 0, `${missing.length} of ${ids.length} without: ${[...new Set(missing.map((x) => x[0]))].join(',')}`);
    const img = body.screenshot ? await decodeJpegSize(page, body.screenshot) : null;
    check('V2-99 screenshot is a JPEG of the visible window, at most 1600 px wide', !!img && body.screenshot.startsWith('data:image/jpeg') && img.w <= 1600 && img.w >= 1500 && Math.abs(img.w / img.h - 1440 / 900) < 0.02, img ? `${img.w}x${img.h}` : 'none');
    check('V2-99 elements marked to be ignored are left out of the screenshot', !!img && !(img.center[0] > 200 && img.center[1] < 60 && img.center[2] < 60), img ? `centre ${img.center}` : '');
  });
  await safe('V2-99 capture fails', async () => {
    await page.evaluate(() => document.querySelectorAll('[data-bug-reporter=""]').forEach((d) => d.remove()));
    // html2canvas loads but throws (e.g. a page it cannot draw)
    await page.route(/html2canvas[^/]*\.js/, (r) => r.fulfill({ contentType: 'text/javascript', body: 'export default async function () { throw new Error("e2e capture failed"); }' }));
    await page.reload();
    await page.getByRole('heading', { name: 'Manage Stations' }).waitFor({ timeout: 15000 });
    await sendReport('E2E no screenshot');
    const body = report();
    check('V2-99 if the screenshot cannot be taken the report is still sent without one', body.description === 'E2E no screenshot' && body.screenshot === null, String(body.screenshot).slice(0, 30));
  });
  await ctx.close();
}

// ── Databank day log after a station change (F-DATABANK-62) ─────────────────
if (want('daylog')) {
  for (const [view, opts] of [['desktop', { viewport: { width: 1600, height: 900 } }], ['iphone', IPHONE]]) {
    const stations = [
      { id: 'kir', name: 'Kiritimati', timezone: 'Pacific/Kiritimati', is_active: true, channels: [] },
      { id: 'pago', name: 'Pago Pago', timezone: 'Pacific/Pago_Pago', is_active: true, channels: [] },
    ];
    const logCalls = [];
    const extra = (method, path, query) => {
      if (path === '/databank/items') return { total: 0, items: [] };
      if (path === '/databank/spots') return { today: '2026-11-17', spots: [] };
      if (path === '/databank/log') {
        logCalls.push({ ...query, at: Date.now() });
        const tz = stations.find((s) => s.id === query.station_id).timezone;
        const at = new Date(`${query.day}T12:00:00Z`).toISOString();
        return { timezone: tz, logs: [0, 1, 2].map((i) => ({ id: `${query.station_id}-${query.day}-${i}`, start_utc: new Date(new Date(at).getTime() + i * 200e3).toISOString(), end_utc: new Date(new Date(at).getTime() + i * 200e3 + 180e3).toISOString(), title: `${query.station_id} song ${i}`, artist: 'A', asset_type: 'music', file: null })) };
      }
      return undefined;
    };
    const station = makeStation({ stations, extra });
    const { ctx } = await adminContext(browser, opts, station, { storage: { radio_last_station: 'kir' } });
    await routeSiteSocket(ctx);
    const { page, errors } = await newPage(ctx);
    await safe(`F-DATABANK-62 ${view}`, async () => {
      await page.goto(`${BASE}/admin/databank?tab=log`);
      const lines = page.locator('[data-testid="daylog-lines"] button');
      await lines.first().waitFor({ timeout: 15000 });
      const dayShown = async () => (view === 'desktop'
        ? page.locator('[data-testid="daylog-days"] [aria-selected="true"]').innerText()
        : page.getByTestId('daylog-day').evaluate((s) => s.options[s.selectedIndex]?.text ?? ''));
      const kirToday = await dayShown();
      await lines.nth(2).click();
      await page.getByLabel('Station').selectOption('pago');
      await page.getByText('pago song 0').first().waitFor({ timeout: 5000 });
      const pagoToday = await dayShown();
      const highlighted = await page.locator('[data-testid="daylog-lines"] button.bg-\\[\\#000080\\]').count();
      const lastLog = logCalls.at(-1);
      check(`F-DATABANK-62 ${view}: "today" follows the new station's today (another time zone)`, kirToday !== pagoToday && pagoToday.length > 0 && lastLog.station_id === 'pago', `${kirToday} -> ${pagoToday}`);
      check(`F-DATABANK-62 ${view}: no line stays highlighted after the change`, highlighted === 0, `${highlighted}`);
      const n = logCalls.filter((c) => c.station_id === 'pago').length;
      await sleep(16000);
      check(`F-DATABANK-62 ${view}: today's 15 s refresh keeps running`, logCalls.filter((c) => c.station_id === 'pago').length > n, `${logCalls.filter((c) => c.station_id === 'pago').length - n} refreshes`);
      // An earlier day that both lists have is kept
      if (view === 'desktop') await page.locator('[data-testid="daylog-days"] button').nth(5).click();
      else await page.getByTestId('daylog-day').selectOption({ index: 5 });
      const earlier = await dayShown();
      await page.getByLabel('Station').selectOption('kir');
      await page.getByText('kir song 0').first().waitFor({ timeout: 5000 });
      check(`F-DATABANK-62 ${view}: an earlier day is kept when the new station's list has it`, (await dayShown()) === earlier, `${earlier} -> ${await dayShown()}`);
    });
    check(`F-DATABANK-62 ${view}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── Global player bar (F-GLOBAL-10) ─────────────────────────────────────────
if (want('player')) {
  const station = makeStation({});
  const { ctx } = await adminContext(browser, DESKTOP, station);
  await routeSiteSocket(ctx);
  const { page } = await newPage(ctx);
  let seen = false;
  for (const p of ['/stations', '/listen/st-1', '/admin/assets', '/admin/stations']) {
    await page.goto(`${BASE}${p}`);
    await sleep(1500);
    for (const b of await page.getByRole('button').all()) {
      const name = (await b.innerText().catch(() => '')).trim();
      if (/^(Play|▶)/.test(name) && (await b.isVisible())) { await b.click().catch(() => {}); await sleep(400); }
      if (await page.locator('.fixed.bottom-0 input[aria-label="Volume"]').count()) { seen = true; break; }
    }
    if (seen) break;
  }
  check('F-GLOBAL-10 a page starts the global player bar (volume slider, Stop)', seen);
  await ctx.close();
}

await browser.close();
process.exit(done());
