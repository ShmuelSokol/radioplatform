/**
 * The Holiday Music page (Shmuel 2026-11-19) and the holiday tag in the control room's
 * queue, against a real backend, at desktop and phone sizes. Fails on sideways page
 * scroll, a missing part, a setting that does not save, and anything the
 * look-at-the-screen guard catches (error toasts, console errors, failed requests).
 *
 *   cd frontend && BASE_URL=http://localhost:3000 API=http://localhost:8000 \
 *     KBR_ADMIN_EMAIL=admin KBR_ADMIN_PASSWORD=... SHOTS_DIR=/tmp/shots node e2e/holiday-music.mjs
 *
 * Needs: the three stations, a Chanukah list with songs and a shiur for every station
 * and Chanukah in season on Main today (lead days long enough), a Pesach list for All
 * Day Stories only, and a library song by "Shwekey" not on any list (wiki/holiday-music.md).
 */
import { chromium, devices } from 'playwright';
import fs from 'node:fs';
import { guardPage } from './lib/guard.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:3000';
const API = (process.env.API || 'http://localhost:8000') + '/api/v1';
const SHOTS = process.env.SHOTS_DIR || '';
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
const MAIN = '95358e09-1260-48d5-85b5-f851000773c0';
const STORIES = '13c2697e-b9f8-42e8-b97a-4e1afa3b5a0e';

const login = await (await fetch(`${API}/auth/login`, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ email: process.env.KBR_ADMIN_EMAIL || 'admin', password: process.env.KBR_ADMIN_PASSWORD }),
})).json();
if (!login.access_token) { console.error('Login failed', login); process.exit(2); }
const auth = { Authorization: `Bearer ${login.access_token}` };
const api = async (path) => (await fetch(`${API}${path}`, { headers: auth })).json();

const SIZES = [
  ['desktop', { viewport: { width: 1440, height: 900 } }],
  ['iphone-13', devices['iPhone 13']],
  ['iphone-se', { ...devices['iPhone SE'] }],
];
const failures = [];
const fail = (msg) => { failures.push(msg); console.log('FAIL', msg); };
const ok = (msg) => console.log('ok  ', msg);

const browser = await chromium.launch();
for (const [name, device] of SIZES) {
  const ctx = await browser.newContext({ ...device, deviceScaleFactor: SHOTS ? 2 : 1 });
  await ctx.addInitScript(([a, r, s]) => {
    localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r);
    if (!sessionStorage.getItem('kbr_e2e_station')) { localStorage.setItem('radio_last_station', s); sessionStorage.setItem('kbr_e2e_station', '1'); }
  }, [login.access_token, login.refresh_token, MAIN]);
  const page = await ctx.newPage();
  const guard = guardPage(page, `holiday music ${name}`);
  const width = device.viewport.width;
  const noSideways = async (where) => {
    const w = await page.evaluate(() => document.documentElement.scrollWidth);
    if (w > width + 1) fail(`${name} ${where}: page is ${w}px wide on a ${width}px screen`);
  };
  const shot = async (label) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}-${label}.png`, fullPage: true }); };

  // 1. The list of holidays on Main
  const overview = await api(`/holiday-music?station_id=${MAIN}`);
  await page.goto(`${BASE}/admin/holiday-music`);
  await page.getByRole('heading', { name: 'Holiday Music' }).waitFor();
  const chanukahRow = page.getByRole('button', { name: /^Chanukah/ });
  await chanukahRow.waitFor();
  const rows = await page.locator('[data-nav-id]').count();
  if (rows !== overview.holidays.length) fail(`${name}: ${rows} holidays shown, server has ${overview.holidays.length}`);
  else ok(`${name}: all ${rows} holidays listed`);
  const ch = overview.holidays.find((h) => h.key === 'chanukah');
  const rowText = await chanukahRow.innerText();
  if (!rowText.includes(`1 in ${ch.today_every} today`)) fail(`${name}: Chanukah row says "${rowText}", today is 1 in ${ch.today_every}`);
  else ok(`${name}: Chanukah row shows today's rate 1 in ${ch.today_every}`);
  await noSideways('list'); await shot('1-holidays');

  // 2. Open Chanukah: settings, day by day, the list
  await chanukahRow.click();
  await page.getByText('Settings for').waitFor();
  const lead = await page.getByLabel('Days before the holiday to start').inputValue();
  if (Number(lead) !== ch.settings.lead_days) fail(`${name}: lead shows ${lead}, setting is ${ch.settings.lead_days}`);
  await page.locator('table tr').first().waitFor();
  const dayRows = await page.locator('table tr').count();
  const todayShown = await page.locator('tr[data-today]').evaluate((row) => {
    const box = row.closest('div').getBoundingClientRect(); const r = row.getBoundingClientRect();
    return r.top >= box.top && r.bottom <= box.bottom;
  });
  if (!todayShown) fail(`${name}: the day-by-day box does not open on today`);
  const firstDay = new Date(ch.first_day), lastDay = new Date(ch.last_day);
  const want = ch.settings.lead_days + Math.round((lastDay - firstDay) / 86400000) + 1;
  if (dayRows !== want) fail(`${name}: ${dayRows} days in the table, expected ${want}`);
  else ok(`${name}: day-by-day table has ${want} days`);
  if (!(await page.locator('tr.bg-yellow-50').innerText()).includes('(today)')) fail(`${name}: today not marked`);
  const items = await api(`/holiday-music/chanukah/items?station_id=${MAIN}`);
  await page.getByText(`On the list (${items.length})`).waitFor();
  ok(`${name}: list shows ${items.length} items`);
  await noSideways('chanukah'); await shot('2-chanukah');

  // 3. Change a setting, save, and see it come back; then put it back
  if (name === 'desktop') {
    await page.getByLabel('The day before: 1 in every').fill('3');
    await page.getByRole('button', { name: 'Save settings' }).click();
    await page.getByText('Saved.').waitFor();
    const after = await api(`/holiday-music?station_id=${MAIN}`);
    if (after.holidays.find((h) => h.key === 'chanukah').settings.peak_every !== 3) fail('peak did not save');
    else ok('saving "the day before: 1 in 3" reached the server');
    await page.getByLabel('The day before: 1 in every').fill('4');
    await page.getByRole('button', { name: 'Save settings' }).click();
    await page.getByText('Saved.').waitFor();
    await guard.expectOnly('save settings', []);

    // 4. Suggest from titles (everything named Chanukah is listed already), search and add to Purim
    await page.getByRole('button', { name: 'Suggest from titles' }).click();
    await page.getByText(/No titles in the library name this holiday|Add .* to the list/).first().waitFor();
    await page.getByRole('button', { name: /^Chanukah/ }).click(); // close
    await page.getByRole('button', { name: /^Purim/ }).click();
    await page.getByText('On the list (0)').waitFor();
    await page.getByLabel('Search the library').fill('Shwekey');
    await page.getByRole('button', { name: 'Search', exact: true }).click();
    const box = page.locator('section li input[type=checkbox]').first();
    await box.waitFor();
    await box.check();
    await shot('3-purim-search');
    await page.getByRole('button', { name: /Add 1 to the list/ }).click();
    await page.getByText('Added 1.').waitFor();
    await page.getByText('On the list (1)').waitFor();
    ok('search, tick and add put one song on the Purim list');
    await page.getByRole('button', { name: 'Remove' }).click();
    await page.getByText('On the list (0)').waitFor();
    ok('remove took it off again');
    await guard.expectOnly('add and remove', []);
  }

  // 5. Another station: All Day Stories has its own Pesach list
  await page.goto(`${BASE}/admin/holiday-music?station=${STORIES}`);
  const pesach = page.getByRole('button', { name: /^Pesach/ });
  await pesach.waitFor();
  const st = await api(`/holiday-music?station_id=${STORIES}`);
  const p = st.holidays.find((h) => h.key === 'pesach');
  const pt = await pesach.innerText();
  if (!pt.includes(`${p.items.long} shiur`)) fail(`${name}: Stories Pesach row "${pt}" does not show ${p.items.long} stories`);
  else ok(`${name}: Stories shows its own Pesach list (${p.items.long} stories/shiurim)`);
  const mainPesach = (await api(`/holiday-music?station_id=${MAIN}`)).holidays.find((h) => h.key === 'pesach');
  if (mainPesach.items.long + mainPesach.items.music !== 0) fail('the Stories-only Pesach list shows on Main');
  await pesach.click();
  await page.getByText('Settings for').waitFor();
  await noSideways('stories pesach'); await shot('4-stories-pesach');

  // 6. The control room: holiday items in Main's queue carry the holiday's name
  const queue = await api(`/stations/${MAIN}/queue?limit=3000`);
  const tagged = queue.entries.filter((e) => e.holiday);
  if (!tagged.length) fail('no holiday item in Main\'s queue');
  await page.goto(`${BASE}/admin/dashboard`);
  const tag = page.getByTestId('row-holiday').first();
  await tag.waitFor({ timeout: 15000 }).catch(() => {});
  const shown = await page.getByTestId('row-holiday').count();
  if (!shown) fail(`${name}: no CHANUKAH tag on the control room queue (${tagged.length} in the queue)`);
  else ok(`${name}: control room shows ${shown} rows tagged with the holiday`);
  if (shown) await tag.scrollIntoViewIfNeeded();
  await shot('5-control-room');
  const bad = guard.findings().filter((f) => !/ws|websocket|stream|audio|\.mp3|playout/i.test(f.text));
  for (const f of bad) fail(`${name}: screen showed ${f.kind}: ${f.text}`);
  await ctx.close();
}
await browser.close();
console.log(failures.length ? `\n${failures.length} FAILED` : '\nall passed');
process.exit(failures.length ? 1 : 0);
