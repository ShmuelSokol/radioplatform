/**
 * HS, Spots mode, Panic failures, the Errors tab and the song request "Match" box,
 * against the fake backend (no real station is touched). Checklist rows F-MODES-* and
 * F-MATCH-* in docs/test-checklist.md; the real-backend run is e2e/station-modes.mjs.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4182 --strictPort &)
 *   BASE_URL=http://localhost:4182 node e2e/modes-match.mjs        # optional: ONLY=modes,errors,match
 */
import { chromium, devices, checker, sleep, makeStation, adminContext, newPage } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4182';
const ONLY = process.env.ONLY || '';
const DESKTOP = { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 };
const PHONE = { ...devices['iPhone 13'], deviceScaleFactor: 1 };
const PHONE_SIDEWAYS = { ...PHONE, viewport: { width: 844, height: 390 } };
const { check, done } = checker({});

const toastText = (page) => page.getByTestId('toast').innerText().then((t) => t.replace(/\s*✕\s*$/, '')).catch(() => '');
const waitToast = (page, re) => page.waitForFunction((src) => new RegExp(src).test(document.querySelector('[data-testid=toast]')?.innerText || ''), re.source, { timeout: 4000 }).then(() => true).catch(() => false);
const noSideScroll = (page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);

/** A fake station that also answers /queue/modes and keeps the modes in the queue body. */
function modesStation(opts = {}) {
  const state = { modes: { hard_start: true, spots: true }, modesReply: null, role: opts.role || 'admin', alerts: opts.alerts || [], alertsFail: false };
  const st = makeStation({
    ...opts,
    extra: (method, path, query, body) => {
      const q = `/stations/${st.SID}/queue`;
      if (path === '/auth/me') return { id: 'u1', email: 'operator@example.com', role: state.role, is_active: true };
      if (path === q && method === 'GET') return { ...st.view(+(query.limit || Infinity)), modes: state.modes };
      if (path === `${q}/modes`) {
        if (state.modesReply) return state.modesReply;
        state.modes = { hard_start: body.hard_start ?? state.modes.hard_start, spots: body.spots ?? state.modes.spots };
        return { modes: state.modes, queue: { ...st.view(), modes: state.modes } };
      }
      if (path === '/alerts' && method === 'GET') {
        if (state.alertsFail) return { __status: 500, detail: 'boom' };
        const open = state.alerts.filter((a) => !a.is_resolved && (!query.station_id || !a.station_id || a.station_id === query.station_id));
        return { alerts: open, total: open.length };
      }
      const m = /^\/alerts\/([^/]+)\/resolve$/.exec(path);
      if (m) { const a = state.alerts.find((x) => x.id === m[1]); a.is_resolved = true; return a; }
      return undefined;
    },
  });
  st.state = state;
  return st;
}

async function openRoom(browser, contextOpts, station, opts = {}) {
  const { ctx, np } = await adminContext(browser, contextOpts, station, opts);
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/dashboard`);
  if (!opts.noWait) await page.getByTestId('queue-row').first().waitFor();
  return { ctx, np, page, errors };
}
const btn = (page, id) => page.locator(`[data-testid=${id}]:visible`).first();
async function showModes(page) {
  if (await btn(page, 'mode-hs').count()) return;
  await page.getByRole('button', { name: /More/ }).first().click();
  await btn(page, 'mode-hs').waitFor({ timeout: 4000 });
}

// ── HS / Spots: not optimistic, greyed while a request runs, toasts, failures ──
async function modes(browser, contextOpts, tag) {
  const station = modesStation();
  // The first queue read is slow: the buttons must not be pressable on a guessed state (F-MODES-38)
  let firstQueue = true;
  const delay = (path, method) => {
    if (path === `/stations/${station.SID}/queue` && method === 'GET' && firstQueue) { firstQueue = false; return 1500; }
    if (path.endsWith('/queue/modes')) return 600;
    return 0;
  };
  const { ctx, np, page, errors } = await openRoom(browser, contextOpts, station, { delay, noWait: true });
  await page.waitForSelector('[data-testid=control-room]');
  await showModes(page);
  check(`${tag}: before the queue loads HS and Spots are greyed (F-MODES-38)`,
    await btn(page, 'mode-hs').isDisabled() && await btn(page, 'mode-spots').isDisabled());
  check(`${tag}: ... but Panic is not held back`, !(await btn(page, 'panic').isDisabled()));
  await page.waitForFunction(() => [...document.querySelectorAll('[data-testid=mode-hs]')].some((b) => b.offsetParent && !b.disabled), null, { timeout: 5000 }).catch(() => {});
  check(`${tag}: HS on by default and lit (F-MODES-1)`, await btn(page, 'mode-hs').getAttribute('aria-pressed') === 'true');
  check(`${tag}: Spots mode on by default (F-MODES-7)`, await btn(page, 'mode-spots').getAttribute('aria-pressed') === 'true');
  if (tag.includes('desktop')) {
    const t1 = await btn(page, 'mode-hs').getAttribute('title');
    const t2 = await btn(page, 'mode-spots').getAttribute('title');
    const t3 = await btn(page, 'panic').getAttribute('title');
    check(`${tag}: tooltips say what each does now (F-MODES-37)`, /HS \(hard start\) is on/.test(t1) && /Spots mode is on/.test(t2) && /Tap twice/.test(t3), `${t1} | ${t2} | ${t3}`);
    const boxes = await Promise.all(['mode-hs', 'mode-spots', 'panic'].map((id) => btn(page, id).boundingBox()));
    check(`${tag}: the three buttons are stacked in one column (F-MODES-37)`, boxes[0].y < boxes[1].y && boxes[1].y < boxes[2].y && Math.abs(boxes[0].x - boxes[2].x) < 2, JSON.stringify(boxes.map((b) => [b.x, b.y])));
  } else {
    for (const id of ['mode-hs', 'mode-spots', 'panic']) {
      const box = await btn(page, id).boundingBox();
      check(`${tag}: ${id} is a 44 px tap target (F-MODES-18)`, box && box.height >= 44, `${box?.height}`);
    }
  }

  // HS off: greyed within 150 ms, not lit until the server answers (F-MODES-20)
  const t0 = Date.now();
  await btn(page, 'mode-hs').click();
  await page.waitForFunction(() => [...document.querySelectorAll('[data-testid=mode-hs],[data-testid=mode-spots],[data-testid=panic]')].filter((b) => b.offsetParent).every((b) => b.disabled), null, { timeout: 1000 }).catch(() => {});
  const greyed = Date.now() - t0;
  check(`${tag}: all three greyed within 150 ms of the press (F-MODES-20)`, greyed < 150 + 60, `${greyed} ms`);
  check(`${tag}: HS still lit while the server works (not optimistic)`, await btn(page, 'mode-hs').getAttribute('aria-pressed') === 'true');
  const ok = await waitToast(page, /HS is off: timed items now wait/);
  check(`${tag}: toast says timed items now wait (F-MODES-1)`, ok, await toastText(page));
  check(`${tag}: new state within 1 s`, Date.now() - t0 < 1000 + 200, `${Date.now() - t0} ms`);
  const sent = station.calls.filter((c) => c.path.endsWith('/queue/modes'));
  check(`${tag}: one POST /modes {hard_start: false}`, sent.length === 1 && JSON.stringify(sent[0].body) === '{"hard_start":false}', JSON.stringify(sent.map((c) => c.body)));
  check(`${tag}: HS shows off and amber`, await btn(page, 'mode-hs').getAttribute('aria-pressed') === 'false' && /amber/.test(await btn(page, 'mode-hs').getAttribute('class')));
  if (!tag.includes('desktop')) check(`${tag}: the phone button reads "HS off"`, (await btn(page, 'mode-hs').innerText()).includes('HS off'));
  else check(`${tag}: tooltip follows the off state`, /HS is off/.test(await btn(page, 'mode-hs').getAttribute('title')));

  // Spots mode off, then on
  await btn(page, 'mode-spots').click();
  check(`${tag}: Spots off toast (F-MODES-7)`, await waitToast(page, /Spots mode is off: no sponsorships/), await toastText(page));
  check(`${tag}: Spots button shows "Spots off" amber`, (await btn(page, 'mode-spots').innerText()).includes('off') && /amber/.test(await btn(page, 'mode-spots').getAttribute('class')));
  await btn(page, 'mode-spots').click();
  check(`${tag}: Spots back on toast`, await waitToast(page, /Spots mode is on/), await toastText(page));

  // Refused / failed: the server's words, or the connection text; state kept (F-MODES-21)
  station.state.modesReply = { __status: 403, detail: 'Your login is not set up to operate this station' };
  await btn(page, 'mode-hs').click();
  check(`${tag}: a 403 shows the server's words`, await waitToast(page, /not set up to operate this station/), await toastText(page));
  check(`${tag}: and HS keeps its state`, await btn(page, 'mode-hs').getAttribute('aria-pressed') === 'false');
  station.state.modesReply = { __status: 404, detail: 'Station not found' };
  await sleep(700);
  await btn(page, 'mode-hs').click();
  check(`${tag}: a 404 shows the server's words`, await waitToast(page, /Station not found/), await toastText(page));
  station.state.modesReply = null;
  await page.route('**/queue/modes', (r) => r.abort('internetdisconnected'), { times: 1 });
  await sleep(700);
  await btn(page, 'mode-spots').click();
  check(`${tag}: network down: "That did not go through..."`, await waitToast(page, /That did not go through\. Check the connection and try again\./), await toastText(page));
  check(`${tag}: and Spots keeps its state`, await btn(page, 'mode-spots').getAttribute('aria-pressed') === 'true' && station.state.modes.spots === true);

  // Panic network down: nothing changes on air (F-MODES-22)
  const was = station.playing().asset_id;
  await page.route('**/queue/panic', (r) => r.abort('internetdisconnected'), { times: 1 });
  await btn(page, 'panic').click(); await sleep(500); await btn(page, 'panic').click();
  check(`${tag}: Panic network down: "Panic did not go through..." (F-MODES-22)`, await waitToast(page, /Panic did not go through\. Check the connection and try again\./), await toastText(page));
  check(`${tag}: and nothing changed on air`, station.playing().asset_id === was);
  // Panic server error: the server's words, nothing changes
  station.failNext.add(`/stations/${station.SID}/queue/panic`);
  await sleep(600);
  await btn(page, 'panic').click(); await sleep(500); await btn(page, 'panic').click();
  check(`${tag}: Panic server error shows its words`, await waitToast(page, /Server error/), await toastText(page));
  check(`${tag}: and still nothing changed on air`, station.playing().asset_id === was);

  // Another control room switched HS back on: shown here at once (F-MODES-39)
  const before = Date.now();
  np.raw({ type: 'modes', data: { station_id: station.SID, hard_start: true, spots: false } });
  await page.waitForFunction(() => document.querySelector('[data-testid=mode-spots]:not([style*="none"])') && [...document.querySelectorAll('[data-testid=mode-spots]')].some((b) => b.offsetParent && b.getAttribute('aria-pressed') === 'false'), null, { timeout: 2000 }).catch(() => {});
  check(`${tag}: a change made in another control room shows within 1 s (F-MODES-39)`,
    await btn(page, 'mode-spots').getAttribute('aria-pressed') === 'false' && await btn(page, 'mode-hs').getAttribute('aria-pressed') === 'true', `${Date.now() - before} ms`);
  check(`${tag}: no sideways scroll`, await noSideScroll(page));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Viewer: buttons greyed (F-MODES-15); phone sideways: everything reachable (F-MODES-41) ──
async function viewerAndSideways(browser) {
  {
    const station = modesStation({ role: 'viewer' });
    const { ctx, page } = await openRoom(browser, DESKTOP, station);
    await showModes(page);
    const all = await Promise.all(['mode-hs', 'mode-spots', 'panic'].map((id) => btn(page, id).isDisabled()));
    check('viewer: HS, Spots and Panic are greyed (F-MODES-15)', all.every(Boolean), JSON.stringify(all));
    await ctx.close();
  }
  {
    const station = modesStation();
    const { ctx, page, errors } = await openRoom(browser, PHONE_SIDEWAYS, station);
    await showModes(page);
    for (const id of ['mode-hs', 'mode-spots', 'panic']) {
      const b = btn(page, id);
      await b.scrollIntoViewIfNeeded();
      const box = await b.boundingBox();
      const vp = page.viewportSize();
      check(`phone sideways: ${id} on screen and tappable (F-MODES-41)`, box && box.height >= 40 && box.y >= 0 && box.y + box.height <= vp.height + 1 && box.x + box.width <= vp.width + 1, JSON.stringify(box));
    }
    await btn(page, 'mode-hs').click();
    check('phone sideways: HS press works', await waitToast(page, /HS is off/));
    // Sideways the left column has Go live / Played / Schedule / Errors buttons (.cr-side)
    const more = page.locator('.cr-side button:visible', { hasText: 'More' });
    if (await more.count() && !(await page.locator('button:visible', { hasText: /^Errors$/ }).count())) await more.first().click();
    const errBtn = page.locator('button:visible', { hasText: /^Errors$/ });
    await errBtn.first().waitFor({ timeout: 3000 }).catch(() => {});
    if (await errBtn.count()) await errBtn.first().click();
    else await page.getByRole('tab', { name: 'Errors' }).first().click();
    await page.getByTestId('errors-panel').first().waitFor({ timeout: 4000 }).catch(() => {});
    check('phone sideways: Errors reachable', await page.locator('[data-testid=errors-panel]:visible').count() === 1);
    check('phone sideways: no sideways scroll', await noSideScroll(page));
    check('phone sideways: no script errors', errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── Errors tab (F-MODES-17, -35, -36, -13) ─────────────
async function openErrors(page) {
  const tab = page.getByRole('tab', { name: 'Errors' });
  if (await tab.locator('visible=true').count()) await tab.locator('visible=true').first().click();
  else { await page.getByRole('button', { name: /More/ }).first().click(); await page.getByRole('button', { name: 'Errors', exact: true }).first().click(); }
  const panel = page.locator('[data-testid=errors-panel]:visible').first();
  await panel.waitFor({ timeout: 4000 });
  return panel;
}

async function errorsTab(browser, contextOpts, tag) {
  const week = new Date(Date.now() - 6 * 86400e3).toISOString();
  const now = new Date().toISOString();
  const mk = (id, title, sev, station_id, at) => ({ id, title, message: `(${id}) details`, severity: sev, station_id, created_at: at, updated_at: at, is_resolved: false, alert_type: 'system' });
  const station = modesStation({
    alerts: [
      mk('a1', 'Panic pressed in the control room', 'warning', 'st-1', now),
      mk('a2', 'Dead air last week', 'critical', 'st-1', week),
      mk('a3', 'Engine offline', 'info', null, now),
      mk('a4', 'Other station problem', 'critical', 'st-2', now),
    ],
  });
  station.SID === 'st-1' || station.state.alerts.forEach((a) => { if (a.station_id === 'st-1') a.station_id = station.SID; });
  const resolveDelay = (path, method) => (method === 'PATCH' && /\/alerts\//.test(path) ? 800 : 0);
  const { ctx, page, errors } = await openRoom(browser, contextOpts, station, { delay: resolveDelay });
  const panel = await openErrors(page);
  await panel.getByText('Panic pressed in the control room', { exact: true }).waitFor({ timeout: 4000 });
  const text = await panel.innerText();
  check(`${tag}: lists this station's alerts and those with no station, not other stations' (F-MODES-17)`,
    text.includes('Panic pressed') && text.includes('Engine offline') && text.includes('Dead air last week') && !text.includes('Other station problem'));
  check(`${tag}: asks the server for this station only`, station.calls.some((c) => c.path === '/alerts' && c.query.station_id === station.SID));
  const whens = await panel.getByTestId('alert-when').allInnerTexts();
  const tz = 'America/New_York';
  const oldDay = new Date(week).toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: tz });
  check(`${tag}: an alert from last week shows its date, today's only the time (F-MODES-35)`,
    whens.some((w) => w.startsWith(oldDay)) && whens.some((w) => /^\d{1,2}:\d\d [AP]M$/.test(w)), whens.join(' | '));
  const sevCls = await panel.getByText('Dead air last week', { exact: true }).getAttribute('class');
  check(`${tag}: severity colour (critical red)`, /red/.test(sevCls));
  check(`${tag}: "All alerts" link`, await panel.getByRole('link', { name: 'All alerts' }).getAttribute('href') === '/admin/alerts');
  // Done: greyed while it runs, then the alert leaves the list (F-MODES-36, -13)
  const doneBtns = panel.getByRole('button', { name: /dealt with/ });
  check(`${tag}: a Done on each alert`, await doneBtns.count() === 3, `${await doneBtns.count()}`);
  await panel.getByRole('button', { name: 'Mark "Engine offline" as dealt with' }).click();
  await sleep(150);
  const dis = await Promise.all((await doneBtns.all()).map((b) => b.isDisabled()));
  check(`${tag}: every Done is greyed while a resolve runs (F-MODES-36)`, dis.every(Boolean), JSON.stringify(dis));
  await panel.getByText('Engine offline', { exact: true }).waitFor({ state: 'detached', timeout: 4000 }).catch(() => {});
  check(`${tag}: Done on an alert with no station resolves it (it leaves the list)`, !(await panel.innerText()).includes('Engine offline') && station.state.alerts.find((a) => a.id === 'a3').is_resolved);
  // Empty list
  station.state.alerts.forEach((a) => { a.is_resolved = true; });
  await panel.getByRole('button', { name: 'Mark "Panic pressed in the control room" as dealt with' }).click();
  await panel.getByText('No open errors for this station.').waitFor({ timeout: 4000 }).catch(() => {});
  check(`${tag}: "No open errors for this station."`, (await panel.innerText()).includes('No open errors for this station.'));
  check(`${tag}: no sideways scroll`, await noSideScroll(page));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();

  // Server error: "Can't load the errors."
  const broken = modesStation();
  broken.state.alertsFail = true;
  const b = await openRoom(browser, contextOpts, broken);
  const p2 = await openErrors(b.page);
  await p2.getByText("Can't load the errors.").waitFor({ timeout: 8000 }).catch(() => {});
  check(`${tag}: "Can't load the errors." when the list fails`, (await p2.innerText()).includes("Can't load the errors."));
  await b.ctx.close();

  // DJ: errors are for managers, and no alerts are read
  const dj = modesStation({ role: 'dj' });
  const d = await openRoom(browser, contextOpts, dj);
  const p3 = await openErrors(d.page);
  check(`${tag}: a DJ sees "Errors are shown to managers."`, (await p3.innerText()).includes('Errors are shown to managers.'));
  check(`${tag}: and no alert list is read for a DJ`, !dj.calls.some((c) => c.path === '/alerts'));
  await d.ctx.close();
}

// ── Song request Match box (F-MATCH-1, -2, -10, -15, -16, -17) ─────────────
function requestsBackend() {
  const songs = [
    { id: 's1', title: 'One Day', artist: 'Matisyahu', asset_type: 'music', duration: 200 },
    { id: 's2', title: 'One Day More', artist: 'Shlomo Simcha', asset_type: 'music', duration: 200 },
    { id: 's3', title: 'Rachem', artist: 'Shwekey', asset_type: 'music', duration: 200 },
  ];
  const mk = (id, status, title, artist = null, asset = null) => ({ id, station_id: 'st-1', station_name: 'Kol B\'ramah', requester_name: `Listener ${id}`, song_title: title, song_artist: artist, status, asset_id: asset, matched_asset_title: asset ? 'One Day' : null, matched_asset_artist: null, reviewed_by: null, created_at: new Date(Date.now() - 600e3).toISOString() });
  const s = {
    requests: [
      mk('r1', 'pending', 'One Day', 'Matisyahu'),
      mk('r2', 'approved', 'Rachem'),
      mk('r3', 'queued', 'One Day', null, 's1'),
      mk('r4', 'played', 'Old song', null, 's1'),
      mk('r5', 'rejected', 'Nope'),
    ],
    calls: [], searchDelay: () => 0, failSearch: false, patchDelay: 0,
  };
  s.handle = (method, path, query, body) => {
    s.calls.push({ method, path, query, body, at: Date.now() });
    if (path === '/auth/me') return { id: 'u1', email: 'manager@example.com', role: 'manager', is_active: true };
    if (path === '/song-requests' && method === 'GET') {
      const list = s.requests.filter((r) => !query.status || r.status === query.status);
      return { requests: list, total: list.length };
    }
    if (path === '/song-requests/analytics/top-requested') return { top_requested: [] };
    const m = /^\/song-requests\/([^/]+)$/.exec(path);
    if (m && method === 'PATCH') {
      const r = s.requests.find((x) => x.id === m[1]);
      r.asset_id = body.asset_id;
      r.matched_asset_title = songs.find((x) => x.id === body.asset_id)?.title;
      if (r.status === 'approved') r.status = 'queued';
      return r;
    }
    if (path === '/assets') {
      if (s.failSearch) return { __status: 500 };
      const t = (query.search || '').toLowerCase();
      const list = songs.filter((a) => (!query.asset_type || a.asset_type === query.asset_type) && (a.title.toLowerCase().includes(t) || a.artist.toLowerCase().includes(t)));
      return { assets: list.slice(0, +(query.limit || 50)), total: list.length };
    }
    if (path === '/stations') return { stations: [{ id: 'st-1', name: 'Kol B\'ramah', timezone: 'America/New_York', is_active: true }], total: 1 };
    if (path === '/alerts/unresolved-count') return { unresolved_count: 0 };
    if (path === '/site-release') return { mode: 'auto', push: null, server_time: new Date().toISOString() };
    return {};
  };
  return s;
}

async function match(browser, contextOpts, tag) {
  const be = requestsBackend();
  const ctx = await browser.newContext(contextOpts);
  await ctx.addInitScript(() => { localStorage.setItem('access_token', 'test'); localStorage.setItem('refresh_token', 'test'); });
  await ctx.routeWebSocket(/\/ws\//, () => {});
  await ctx.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, '');
    let body = null;
    try { body = req.postDataJSON(); } catch { /* none */ }
    const query = Object.fromEntries(url.searchParams);
    const data = be.handle(req.method(), path, query, body);
    const d = path === '/assets' ? be.searchDelay(query.search) : req.method() === 'PATCH' ? be.patchDelay : 0;
    if (d) await sleep(d);
    const status = data && data.__status ? data.__status : 200;
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) }).catch(() => {});
  });
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/requests`);
  const scope = page.locator(tag.includes('desktop') ? 'table' : 'div.md\\:hidden.space-y-2').first();
  await scope.getByText('Listener r1').waitFor({ timeout: 8000 });
  const rowOf = (id) => (tag.includes('desktop') ? scope.locator('tr', { hasText: `Listener ${id}` }) : scope.locator(':scope > div', { hasText: `Listener ${id}` }));
  const has = async (id) => (await rowOf(id).getByTestId('match-to-song').count()) === 1;
  check(`${tag}: Match on pending and approved, not on queued, played or rejected (F-MATCH-1)`,
    await has('r1') && await has('r2') && !(await has('r3')) && !(await has('r4')) && !(await has('r5')));

  // Open: title filled in and selected
  await rowOf('r1').getByTestId('match-to-song').click();
  const box = page.getByTestId('match-box');
  const input = box.getByRole('textbox');
  const sel = await input.evaluate((el) => [el.value, el.selectionStart, el.selectionEnd]);
  check(`${tag}: the box opens with the request's title, selected (F-MATCH-2)`, sel[0] === 'One Day' && sel[1] === 0 && sel[2] === 7, JSON.stringify(sel));
  // Search from 2 letters, 200 ms after typing stops; "Searching..." while it looks
  be.searchDelay = () => 400;
  const searches = () => be.calls.filter((c) => c.path === '/assets');
  const n0 = searches().length;
  await input.fill('o');
  await sleep(400);
  check(`${tag}: one letter does not search`, searches().length === n0);
  const tType = Date.now();
  await input.fill('one');
  await sleep(100);
  check(`${tag}: no search before 200 ms`, searches().length === n0);
  await box.getByText('Searching…').waitFor({ timeout: 1000 }).catch(() => {});
  check(`${tag}: "Searching…" while it looks`, await box.getByText('Searching…').count() === 1);
  await box.getByRole('button', { name: /One Day — Matisyahu/ }).waitFor({ timeout: 3000 });
  const s = searches().at(-1);
  check(`${tag}: searches music only, up to 8`, s.query.search === 'one' && s.query.asset_type === 'music' && s.query.limit === '8' && s.at - tType >= 190, JSON.stringify(s.query));
  check(`${tag}: lists "title — artist"`, (await box.innerText()).includes('One Day More — Shlomo Simcha'));
  // Speed: results within 500 ms of typing stopping (200 ms wait + a fast search) (F-MATCH-10)
  be.searchDelay = () => 50;
  await input.fill('rach');
  const tStop = Date.now();
  await box.getByRole('button', { name: /Rachem — Shwekey/ }).waitFor({ timeout: 3000 });
  check(`${tag}: results on screen within 500 ms of typing stopping (F-MATCH-10)`, Date.now() - tStop < 500, `${Date.now() - tStop} ms`);
  // Nothing found / failed search
  await input.fill('zzzz');
  await box.getByText('No songs found').waitFor({ timeout: 2000 }).catch(() => {});
  check(`${tag}: "No songs found" when nothing comes back`, await box.getByText('No songs found').count() === 1);
  be.failSearch = true;
  await input.fill('one d');
  await sleep(600);
  check(`${tag}: a failed search shows "No songs found" (F-MATCH-15)`, await box.getByText('No songs found').count() === 1);
  be.failSearch = false;
  // Typing fast: only the newest search's results show
  be.searchDelay = (q) => (q === 'one' ? 900 : 50);
  await input.fill('one');
  await sleep(260);
  await input.fill('rachem');
  await sleep(1300);
  const shown = await box.innerText();
  check(`${tag}: typing fast, only the newest results show (F-MATCH-15)`, shown.includes('Rachem') && !shown.includes('One Day'), shown.replace(/\n/g, ' | '));
  // Esc closes without a change
  await input.press('Escape');
  check(`${tag}: Esc closes the box`, await page.getByTestId('match-box').count() === 0);
  await rowOf('r1').getByTestId('match-to-song').click();
  await page.getByTestId('match-box').getByRole('button', { name: 'Close search' }).click();
  check(`${tag}: ✕ closes the box`, await page.getByTestId('match-box').count() === 0);
  check(`${tag}: closing changed nothing`, !be.calls.some((c) => c.method === 'PATCH'));

  // Phone fit (F-MATCH-17)
  await rowOf('r2').getByTestId('match-to-song').click();
  be.searchDelay = () => 0;
  const b2 = page.getByTestId('match-box');
  await b2.getByRole('textbox').fill('one');
  await b2.getByRole('button', { name: /One Day — Matisyahu/ }).waitFor({ timeout: 3000 });
  if (!tag.includes('desktop')) {
    const vw = page.viewportSize().width;
    const list = await b2.locator('div.absolute').boundingBox();
    check(`${tag}: the box stays on screen (F-MATCH-17)`, list.x >= 0 && list.x + list.width <= vw + 1, JSON.stringify(list));
    const fs = await b2.getByRole('textbox').evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
    check(`${tag}: the field is 16 px, so iPhone does not zoom (fixed)`, fs >= 16, `${fs}px`);
    const h = (await b2.getByRole('button', { name: /One Day — Matisyahu/ }).boundingBox()).height;
    check(`${tag}: each result is a 44 px tap target (fixed)`, h >= 44, `${h}px`);
    check(`${tag}: no sideways scroll with the box open`, await noSideScroll(page));
  }
  // Pick on an approved request: while saving every Match is greyed, then it moves to queued (F-MATCH-16)
  be.patchDelay = 700;
  await b2.getByRole('button', { name: /One Day — Matisyahu/ }).click();
  await sleep(150);
  const greyed = await Promise.all((await scope.getByTestId('match-to-song').all()).map((x) => x.isDisabled()));
  check(`${tag}: every Match button greyed while the save runs (F-MATCH-16)`, greyed.length > 0 && greyed.every(Boolean), JSON.stringify(greyed));
  const patch = be.calls.find((c) => c.method === 'PATCH');
  check(`${tag}: the pick sends PATCH {asset_id}`, patch && patch.path === '/song-requests/r2' && JSON.stringify(patch.body) === '{"asset_id":"s1"}', JSON.stringify(patch?.body));
  await page.waitForFunction(() => true);
  await sleep(1200);
  check(`${tag}: afterwards the list is read again and the request shows queued, without Match`,
    be.calls.filter((c) => c.path === '/song-requests' && c.method === 'GET').length >= 2 && !(await has('r2')) && /QUEUED|queued/i.test(await rowOf('r2').innerText().catch(() => '')));
  await page.getByRole('button', { name: 'Queued', exact: true }).first().click();
  await scope.getByText('Listener r2').waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: it is in the Queued tab`, await scope.getByText('Listener r2').count() >= 1);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
const run = (name) => !ONLY || ONLY.split(',').includes(name);
try {
  if (run('modes')) { await modes(browser, DESKTOP, 'modes desktop'); await modes(browser, PHONE, 'modes iPhone'); await viewerAndSideways(browser); }
  if (run('errors')) { await errorsTab(browser, DESKTOP, 'errors desktop'); await errorsTab(browser, PHONE, 'errors iPhone'); }
  if (run('match')) { await match(browser, DESKTOP, 'match desktop'); await match(browser, PHONE, 'match iPhone'); }
} finally {
  await browser.close();
}
process.exit(done());
