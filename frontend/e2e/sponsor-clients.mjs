/**
 * The Sponsors page as a client database (Shmuel 2026-11-18): the client list,
 * a client's page with every tab, at desktop and phone sizes, against a real
 * backend with made-up clients. Fails on sideways page scroll, a missing part,
 * and anything the look-at-the-screen guard catches (error toasts, console
 * errors, failed requests).
 *
 *   cd frontend && BASE_URL=http://localhost:3000 API=http://localhost:8000 \
 *     KBR_ADMIN_EMAIL=admin KBR_ADMIN_PASSWORD=... SHOTS_DIR=/tmp/shots node e2e/sponsor-clients.mjs
 *
 * Needs at least one client with a running contract and plays (wiki/sponsor-accounts.md
 * has the seed used on 2026-11-18).
 */
import { chromium, devices } from 'playwright';
import fs from 'node:fs';
import { guardPage, guardVerdict } from './lib/guard.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:3000';
const API = (process.env.API || 'http://localhost:8000') + '/api/v1';
const SHOTS = process.env.SHOTS_DIR || '';
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

const login = await (await fetch(`${API}/auth/login`, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ email: process.env.KBR_ADMIN_EMAIL || 'admin', password: process.env.KBR_ADMIN_PASSWORD }),
})).json();
if (!login.access_token) { console.error('Login failed', login); process.exit(2); }
const clients = await (await fetch(`${API}/sponsors/clients`, { headers: { Authorization: `Bearer ${login.access_token}` } })).json();
const busy = clients.find((c) => c.status === 'running' && c.plays_total > 0);
if (!busy) { console.error('Seed a client with a running contract and plays first'); process.exit(2); }

const SIZES = [
  ['desktop', { viewport: { width: 1440, height: 900 } }],
  ['iphone-13', devices['iPhone 13']],
  ['iphone-se', { ...devices['iPhone SE'] }],
];
const failures = [];
const fail = (msg) => { failures.push(msg); console.log('FAIL', msg); };

const browser = await chromium.launch();
for (const [name, device] of SIZES) {
  const ctx = await browser.newContext({ ...device, deviceScaleFactor: 1 });
  await ctx.addInitScript(([a, r]) => { localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r); }, [login.access_token, login.refresh_token]);
  const page = await ctx.newPage();
  const guard = guardPage(page, `sponsors ${name}`);
  const width = device.viewport.width;
  const noSideways = async (where) => {
    const w = await page.evaluate(() => document.documentElement.scrollWidth);
    if (w > width + 1) fail(`${name} ${where}: page is ${w}px wide on a ${width}px screen`);
  };
  const shot = async (label) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}-${label}.png`, fullPage: true }); };

  await page.goto(`${BASE}/admin/sponsors`);
  await page.getByTestId('client-list').waitFor();
  const rows = await page.getByTestId('client-row').count();
  if (rows !== clients.length) fail(`${name}: list shows ${rows} clients, the server has ${clients.length}`);
  await noSideways('list'); await shot('1-list');

  // filters: "On the air" shows only running clients
  await page.getByRole('button', { name: /^On the air/ }).click();
  const running = clients.filter((c) => c.status === 'running').length;
  if ((await page.getByTestId('client-row').count()) !== running) fail(`${name}: On the air filter shows the wrong clients`);
  await page.getByRole('button', { name: /^All/ }).click();
  await page.getByLabel('Find a client').fill(busy.name.slice(-6));
  if ((await page.getByTestId('client-row').count()) < 1) fail(`${name}: search does not find ${busy.name}`);
  await guard.expectOnly('list and filters');

  // open the busy client
  await page.getByRole('link', { name: busy.name }).first().click();
  await page.getByTestId('client-tiles').waitFor();
  if (!page.url().includes(`/admin/sponsors/${busy.id}`)) fail(`${name}: clicking a client did not open its page`);
  await page.getByTestId('spot-flow').waitFor();
  if (!(await page.getByTestId('stage-on_air').count())) fail(`${name}: running client shows no spot on the air`);
  await noSideways('spots'); await shot('2-spots');

  await page.getByRole('tab', { name: /Contracts/ }).click();
  await page.getByTestId('contract-table').first().waitFor();
  await noSideways('contracts'); await shot('3-contracts');

  await page.getByRole('tab', { name: 'Play history' }).click();
  await page.getByTestId('play-table').waitFor();
  const total = await page.getByTestId('plays-total').innerText();
  if (!total.startsWith(String(busy.plays_total))) fail(`${name}: play history says "${total}", expected ${busy.plays_total}`);
  await noSideways('plays'); await shot('4-plays');

  await page.getByRole('tab', { name: 'Missed plays' }).click();
  await page.getByText(/promised plays? did not air/).waitFor();
  await noSideways('missed'); await shot('5-missed');

  await page.getByRole('tab', { name: 'Details' }).click();
  await page.getByRole('button', { name: 'Save details' }).waitFor();
  await noSideways('details'); await shot('6-details');

  // a tab is in the address, so a link can open it
  await page.goto(`${BASE}/admin/sponsors/${busy.id}?tab=contracts`);
  await page.getByTestId('contract-table').first().waitFor();
  await guard.expectOnly('client page');
  await ctx.close();
}
await browser.close();

const odd = guardVerdict();
if (odd) fail(`guard: ${odd} unexpected things on screen`);
console.log(failures.length ? `${failures.length} failure(s)` : 'sponsor clients: all checks passed');
process.exit(failures.length ? 1 : 0);
