/**
 * 🐞 can be tapped with any dialog open, on every page (Shmuel's iPhone, 2026-11-19: the Edit
 * contract card covered the top bar and there was no 🐞 to tap).
 *
 * Walks every staff and listener page at iPhone and desktop size against the fake backend,
 * presses each button that opens a dialog, panel or card (New..., Add..., Edit..., Upload...,
 * a row's details), and for each one that opens checks that
 *   - a 🐞 shows and is the top thing where it sits (a tap reaches it),
 *   - it covers none of the page's or the dialog's buttons, links or fields,
 *   - a tap on it opens the report with the dialog still open behind it.
 * The Edit contract card is opened by name too (fake contract data below). A fake backend
 * has no rows on many pages, so the walk cannot reach every dialog; the last part puts
 * made-up dialogs of every shape on every page (a card over the top bar, a full-screen sheet
 * with buttons on every edge, a bottom sheet, a layer above the floating 🐞) and checks the
 * same three things, so a dialog the walk cannot open is covered by the same rule.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4173 &)
 *   BASE_URL=http://localhost:4173 node e2e/bug-everywhere.mjs     (ONLY=/admin/sponsors for one page)
 */
import { chromium, devices, checker, sleep, makeStation, adminContext, newPage } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4173';
const { check, done } = checker();
const ROUTES = [
  '/admin/dashboard', '/admin/stations', '/admin/assets', '/admin/users', '/admin/rules', '/admin/schedules',
  '/admin/holidays', '/admin/sponsors', '/admin/campaigns', '/admin/spot-schedule', '/admin/spot-recordings',
  '/admin/databank', '/admin/program-blocks', '/admin/schedule-check', '/admin/playlists',
  '/admin/categories', '/admin/asset-types', '/admin/alerts', '/admin/bug-reports', '/admin/site-updates',
  '/admin/activity', '/admin/analytics', '/admin/live', '/admin/requests', '/admin/archives', '/admin/rds',
  '/admin/emergency', '/admin/reviews', '/admin/program-clock',
  // Not here: /admin/sponsorship-rules and /admin/crm need data shapes the fake backend does not have
  '/stations', '/guide', '/hosts', '/archives',
].filter((r) => !process.env.ONLY || r === process.env.ONLY);
const OPENERS = /^(\+\s*)?(new|add|edit|create|upload|schedule new|invite|compose|details|view|record|manage|report|send test|test)\b/i;
const SKIP = /report a bug|bug report|delete|remove|sign out|log ?out|go live|panic|stop|skip|next/i;

/** Everything a test needs to know about the 🐞 now: is one tappable, where, what it covers. */
const BUG_STATE = () => {
  const out = [];
  for (const el of document.querySelectorAll('[data-bug-trigger]')) {
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8 || r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) continue;
    const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    const reach = !!top && (top === el || el.contains(top));
    // What of the page lies under the 🐞 (the reporter's own parts left out)
    const under = new Set();
    for (const [fx, fy] of [[0.5, 0.5], [0.15, 0.15], [0.85, 0.15], [0.15, 0.85], [0.85, 0.85]]) {
      const hit = document.elementsFromPoint(r.left + fx * r.width, r.top + fy * r.height).find((e) => !e.closest('[data-bug-reporter]') && !e.closest('[data-bug-trigger]'));
      const t = hit?.closest('button, a[href], input, select, textarea, [role="button"], [role="tab"], [role="checkbox"]');
      if (t) under.add((t.getAttribute('aria-label') || t.textContent || t.tagName).trim().slice(0, 40));
    }
    out.push({ reach, lifted: el.hasAttribute('data-bug-lifted'), box: [Math.round(r.left), Math.round(r.top), Math.round(r.width)], under: [...under] });
  }
  return out;
};

/** Dialog-like things open now: role=dialog, or a fixed layer over most of the window. */
const OVERLAYS = () => {
  const found = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('[data-bug-reporter]')) continue;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 40 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    const dialog = el.getAttribute('role') === 'dialog' || el.getAttribute('aria-modal') === 'true';
    const layer = cs.position === 'fixed' && r.width * r.height > innerWidth * innerHeight * 0.5 && el.tagName !== 'NAV' && !el.closest('nav, header');
    if (dialog || layer) found.push((el.getAttribute('aria-label') || el.querySelector('h1,h2,h3')?.textContent || el.className.toString()).slice(0, 50));
  }
  return found;
};

/** The fake backend answers {} where it has no data; most lists on these pages want []. */
async function emptyLists(ctx, station) {
  await ctx.route('**/api/v1/**', async (r) => {
    const req = r.request();
    if (req.method() !== 'GET') return r.fallback();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, '');
    const data = station.handle('GET', path, Object.fromEntries(url.searchParams), null);
    if (path === '/spot-schedules/preview') return r.fulfill({ contentType: 'application/json',
      body: JSON.stringify({ station_id: 'st-1', timezone: 'America/New_York', days: 7, planned: [], unplaced: [], off_air: [] }) });
    if (path === '/spot-schedules/timing-defaults') return r.fulfill({ contentType: 'application/json',
      body: JSON.stringify({ on_the_hour: 'hard', half_hour: 'soft', other: 'soft', half_hour_time_check: false }) });
    if (path === '/program-blocks/preview') return r.fulfill({ contentType: 'application/json',
      body: JSON.stringify({ timezone: 'America/New_York', hours: 24, occurrences: [] }) });
    if (!path.endsWith('/preview') && data && typeof data === 'object' && !Array.isArray(data) && !Object.keys(data).length) return r.fulfill({ contentType: 'application/json', body: '[]' });
    return r.fallback();
  });
}

async function tryDialog(page, tag, opener) {
  const before = await page.evaluate(OVERLAYS);
  await opener();
  await sleep(700);
  const after = await page.evaluate(OVERLAYS);
  if (after.length <= before.length) return false;
  const name = after.find((x) => !before.includes(x)) || after[after.length - 1];
  await sleep(600); // the reporter looks twice a second
  const bugs = await page.evaluate(BUG_STATE);
  const good = bugs.filter((b) => b.reach);
  check(`${tag} [${name}]: a 🐞 shows and can be tapped`, good.length > 0, JSON.stringify(bugs));
  if (!good.length) return true;
  const b = good.find((x) => x.lifted) || good[0];
  check(`${tag} [${name}]: the 🐞 covers no button, link or field`, b.under.length === 0, JSON.stringify(b));
  await page.mouse.click(b.box[0] + b.box[2] / 2, b.box[1] + b.box[2] / 2);
  const form = page.getByRole('dialog', { name: /report a bug/i });
  const opened = await form.waitFor({ timeout: 15000 }).then(() => true, () => false);
  check(`${tag} [${name}]: tapping it opens the report`, opened);
  if (opened) {
    const still = await page.evaluate(OVERLAYS);
    check(`${tag} [${name}]: the dialog is still open behind the report`, still.includes(name), JSON.stringify(still));
  }
  return true;
}

const browser = await chromium.launch();
let opened = 0;
for (const [view, opts] of [['iphone', devices['iPhone 13']], ['desktop', { viewport: { width: 1440, height: 900 } }]]) {
  for (const route of ROUTES) {
    const station = makeStation({ queueSize: 8 });
    const { ctx } = await adminContext(browser, opts, station);
    await emptyLists(ctx, station);
    await ctx.routeWebSocket(/\/ws\//, () => {});
    const { page } = await newPage(ctx);
    const tag = `${view} ${route}`;
    try {
      await page.goto(BASE + route);
      await sleep(1800);
      const names = await page.evaluate(([o, s]) => {
        const re = new RegExp(o, 'i'), skip = new RegExp(s, 'i');
        const seen = new Set();
        return [...document.querySelectorAll('button, [role="button"], a')].filter((b) => {
          const r = b.getBoundingClientRect(); if (!r.width || !r.height) return false;
          if (b.closest('[data-bug-reporter], nav, header')) return false;
          const n = (b.getAttribute('aria-label') || b.textContent || '').trim().replace(/\s+/g, ' ');
          if (!re.test(n) || skip.test(n) || seen.has(n) || (b.tagName === 'A' && b.getAttribute('href') && !b.getAttribute('href').startsWith('#'))) return false;
          seen.add(n); return true;
        }).map((b) => (b.getAttribute('aria-label') || b.textContent || '').trim().replace(/\s+/g, ' ')).slice(0, 6);
      }, [OPENERS.source, SKIP.source]);
      for (const n of names) {
        await page.goto(BASE + route);
        await sleep(1500);
        const btn = page.getByRole('button', { name: n, exact: true }).first();
        if (!(await btn.isVisible().catch(() => false))) continue;
        if (await tryDialog(page, tag, () => btn.click({ timeout: 3000 }).catch(() => {}))) opened++;
        await page.keyboard.press('Escape').catch(() => {});
      }
    } catch (e) {
      check(`${tag}: ran`, false, String(e).slice(0, 200));
    }
    await ctx.close();
  }
}
console.log(`real dialogs opened and checked: ${opened}`);
if (!process.env.ONLY) check('(setup) the walk opened real dialogs', opened >= 6, `${opened}`);

// The Edit contract card, where Shmuel found no 🐞 (Spot Schedule's Edit; the same card as a client's page)
const CONTRACT = { id: 'c-1', sponsor_id: 'sp-1', asset_id: 'a-spot', station_id: null, name: null, days_of_week: '0,1,2,3,4',
  dayparts: 'morning,afternoon', plays_per_daypart: 2, start_date: '2026-11-01', end_date: '2026-12-31', is_active: true,
  fixed_times: null, timing_mode: null, created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z' };
for (const [view, opts] of [['iphone', devices['iPhone 13']], ['iphone-se', devices['iPhone SE']], ['desktop', { viewport: { width: 1440, height: 900 } }]]) {
  if (process.env.ONLY && process.env.ONLY !== '/admin/spot-schedule') break;
  const station = makeStation({ queueSize: 8 });
    const { ctx } = await adminContext(browser, opts, station);
    await emptyLists(ctx, station);
  await ctx.routeWebSocket(/\/ws\//, () => {});
  await ctx.route(/\/api\/v1\/spot-schedules(\?.*)?$/, (r) => r.request().method() === 'GET'
    ? r.fulfill({ contentType: 'application/json', body: JSON.stringify([CONTRACT]) }) : r.fallback());
  await ctx.route(/\/api\/v1\/spot-schedules\/preview/, (r) => r.fulfill({ contentType: 'application/json',
    body: JSON.stringify({ station_id: 'st-1', timezone: 'America/New_York', days: 7, planned: [], unplaced: [], off_air: [] }) }));
  await ctx.route(/\/api\/v1\/spot-schedules\/timing-defaults/, (r) => r.fulfill({ contentType: 'application/json',
    body: JSON.stringify({ on_the_hour: 'hard', half_hour: 'soft', other: 'soft', half_hour_time_check: false }) }));
  await ctx.route(/\/api\/v1\/sponsors(\?.*)?$/, (r) => r.fulfill({ contentType: 'application/json', body: JSON.stringify([{ id: 'sp-1', name: 'Test Bakery', is_active: true }]) }));
  const { page } = await newPage(ctx);
  try {
    await page.goto(BASE + '/admin/spot-schedule');
    const edit = page.getByTestId('contract-edit').first();
    await edit.waitFor({ timeout: 15000 });
    const ok = await tryDialog(page, `${view} Edit contract`, () => edit.click());
    check(`${view} Edit contract: the card opened`, ok && await page.getByTestId('contract-editor').isVisible());
  } catch (e) {
    check(`${view} Edit contract: ran`, false, String(e).slice(0, 200));
  }
  await ctx.close();
}

// The same card from a client's page (Sponsors, a client, Contracts, Edit), scrolled down first
const CLIENT = { id: 'sp-1', name: 'Test Bakery', company_name: 'Test Bakery LLC', contact_name: 'Moshe', contact_email: null,
  contact_phone: '732-555-0100', status: 'running', active_from: '2026-11-01', active_until: '2026-12-31', open_ended: false,
  contracts_running: 6, contracts_total: 6, spots_on_air: 1, spots_in_progress: 0, plays_7_days: 40, plays_total: 90,
  last_played_at: '2026-11-18T15:00:00Z', has_portal_login: false, created_at: '2026-11-01T00:00:00Z' };
const ACCOUNT = { client: CLIENT, notes: null, stages: [{ key: 'on_air', label: 'On the air' }], spots: [],
  contracts: [...Array(6)].map((_, i) => ({ ...CONTRACT, id: `c-${i}`, name: `Contract ${i + 1}`, station_name: 'Every station',
    asset_title: 'Bakery spot', duration: 30, state: 'running', problem: null, plays_in_contract: 15, plays_7_days: 7,
    last_played_at: '2026-11-18T15:00:00Z', created_at: '2026-11-01T00:00:00Z' })) };
for (const [view, opts] of [['iphone', devices['iPhone 13']], ['iphone-se', devices['iPhone SE']], ['desktop', { viewport: { width: 1440, height: 900 } }]]) {
  if (process.env.ONLY && process.env.ONLY !== '/admin/sponsors') break;
  const station = makeStation({ queueSize: 8 });
    const { ctx } = await adminContext(browser, opts, station);
    await emptyLists(ctx, station);
  await ctx.routeWebSocket(/\/ws\//, () => {});
  await ctx.route(/\/api\/v1\/sponsors\/sp-1\/account/, (r) => r.fulfill({ contentType: 'application/json', body: JSON.stringify(ACCOUNT) }));
  const { page } = await newPage(ctx);
  try {
    await page.goto(BASE + '/admin/sponsors/sp-1?tab=contracts');
    const edit = page.getByTestId('contract-edit').last();
    await edit.waitFor({ timeout: 15000 });
    await edit.scrollIntoViewIfNeeded();
    await page.mouse.wheel(0, 2000);
    await sleep(300);
    const ok = await tryDialog(page, `${view} client page Edit contract`, () => edit.click());
    check(`${view} client page Edit contract: the card opened`, ok && await page.getByTestId('contract-editor').isVisible());
  } catch (e) {
    check(`${view} client page Edit contract: ran`, false, String(e).slice(0, 200));
  }
  await ctx.close();
}

// Made-up dialogs of every shape on every page
const SHAPES = {
  'card over the top bar': `<div style="position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.4);overflow:auto;padding:8px">
     <form role="dialog" aria-label="T card" style="background:#fff;border-radius:12px;padding:16px;min-height:96vh">
       <h2>T card</h2><button type="button">Close</button><input placeholder="Name"><button type="submit">Save</button></form></div>`,
  'full screen with rows edge to edge': `<div role="dialog" aria-label="T sheet" style="position:fixed;inset:0;z-index:55;background:#fff">
     ${[...Array(8)].map((_, i) => `<button style="position:absolute;left:0;width:100%;top:${i * 8}%;height:7%">Row ${i}</button>`).join('')}
     <button style="position:absolute;left:0;width:100%;bottom:0;height:56px">Save</button></div>`,
  'bottom sheet': `<div style="position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.5)"><div role="dialog" aria-label="T bottom"
     style="position:absolute;left:0;right:0;bottom:0;height:60%;background:#fff;padding:12px"><h3>T bottom</h3>
     <button style="width:100%;height:48px">Save</button><button style="width:100%;height:48px;position:absolute;bottom:0;left:0">Cancel</button></div></div>`,
  'layer above the floating 🐞': `<div role="dialog" aria-label="T top layer" style="position:fixed;inset:0;z-index:62;background:#fafafa;padding:20px">
     <h2>T top layer</h2><a href="#x">A link</a></div>`,
};
const SAMPLE = ['/admin/dashboard', '/admin/sponsors', '/admin/spot-schedule', '/admin/assets', '/admin/databank', '/admin/live', '/stations', '/guide']
  .filter((r) => !process.env.ONLY || r === process.env.ONLY);
for (const [view, opts] of [['iphone', devices['iPhone 13']], ['desktop', { viewport: { width: 1440, height: 900 } }]]) {
  for (const route of SAMPLE) {
    for (const [shape, html] of Object.entries(SHAPES)) {
      const station = makeStation({ queueSize: 8 });
    const { ctx } = await adminContext(browser, opts, station);
    await emptyLists(ctx, station);
      await ctx.routeWebSocket(/\/ws\//, () => {});
      const { page } = await newPage(ctx);
      try {
        await page.goto(BASE + route);
        await sleep(1500);
        await tryDialog(page, `${view} ${route} made-up ${shape}`, () => page.evaluate((h) => document.body.insertAdjacentHTML('beforeend', h), html));
      } catch (e) {
        check(`${view} ${route} made-up ${shape}: ran`, false, String(e).slice(0, 200));
      }
      await ctx.close();
    }
  }
}
await browser.close();
done();
