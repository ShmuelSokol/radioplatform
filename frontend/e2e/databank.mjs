/**
 * Databank (KBR Studio's database window) against a RUNNING site + backend with some
 * songs, spots with schedules, blocks and plays in it.
 *
 *   BASE_URL=http://localhost:3000 ADMIN_USER=admin ADMIN_PASS=... node e2e/databank.mjs
 *   optional: SHOTS_DIR=/some/dir
 *
 * Desktop and iPhone: every tab draws rows, sorting by V flips the order, Find narrows
 * the view, Edit mode saves a V and a spot end date (and puts them back), the day log
 * lists Started/Ended lines to the millisecond, no sideways scroll, no script errors.
 */
import fs from 'node:fs';
import { createRequire } from 'node:module';
const { chromium, devices } = createRequire(import.meta.url)('playwright');

const BASE = process.env.BASE_URL || 'http://localhost:3000';
const SHOTS = process.env.SHOTS_DIR || '';
const API = `${BASE}/api/v1`;
const login = await (await fetch(`${API}/auth/login`, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ email: process.env.ADMIN_USER || 'admin', password: process.env.ADMIN_PASS }),
})).json();
if (!login.access_token) { console.error('Login failed', login); process.exit(2); }

let failures = 0;
const check = (ok, what) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${what}`); if (!ok) failures++; };
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

const VIEWS = [
  { name: 'desktop', opts: { viewport: { width: 1600, height: 900 } }, desktop: true },
  { name: 'iphone', opts: { ...devices['iPhone 13'] }, desktop: false },
];

const browser = await chromium.launch();
for (const view of VIEWS) {
  const ctx = await browser.newContext(view.opts);
  await ctx.addInitScript(([a, r]) => { localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r); }, [login.access_token, login.refresh_token]);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e).slice(0, 160)));
  page.on('response', r => { if (r.url().includes('/databank') && r.status() >= 400) errors.push(`${r.status()} ${r.url()}`); });
  const shot = async (n) => SHOTS && page.screenshot({ path: `${SHOTS}/${view.name}-${n}.png` });
  const rows = () => page.locator('[data-testid="db-table"] [role="row"][data-row]');
  const noSideScroll = async (what) => check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${view.name}: no sideways scroll (${what})`);

  await page.goto(`${BASE}/admin/databank`);
  await rows().first().waitFor({ timeout: 15000 });
  check(await rows().count() > 0, `${view.name}: songs listed`);
  check(/Tot [1-9]/.test(await page.getByTestId('db-tot').innerText()), `${view.name}: Tot shows the library size`);
  await noSideScroll('songs');
  await shot('1-songs');

  // Find narrows View
  const viewBefore = await page.getByTestId('db-view').innerText();
  await page.getByTestId('db-find').fill('Shalsheles');
  await page.waitForFunction(b => document.querySelector('[data-testid="db-view"]')?.textContent !== b, viewBefore, { timeout: 5000 }).catch(() => {});
  const texts = await rows().allInnerTexts();
  check(texts.length > 0 && texts.every(t => /shalsheles/i.test(t)), `${view.name}: Find keeps only matching rows (${texts.length})`);
  await page.getByRole('button', { name: 'ALL' }).click();

  if (view.desktop) {
    // Sort by V, highest first
    await page.locator('.qn-log-head button', { hasText: /^V/ }).click();
    await page.waitForTimeout(800);
    const votes = (await rows().evaluateAll(rs => rs.map(r => Number(r.children[3].textContent))));
    check(votes.length > 1 && votes.every((v, i) => i === 0 || votes[i - 1] >= v), `${view.name}: sorted by V, highest first`);

    // Edit mode: change V on the first row, then put it back
    const first = rows().first();
    const oldV = (await first.locator(':scope > span').nth(3).innerText()).trim();
    const rowId = await first.getAttribute('data-row');
    await page.getByTestId('db-edit-mode').click();
    await first.locator(':scope > span').nth(3).click();
    await page.getByTestId('db-edit-input').fill('42');
    await page.getByTestId('db-edit-input').press('Enter');
    await page.getByText('Saved.').waitFor({ timeout: 5000 });
    await shot('2-edit-saved');
    const asset = await (await fetch(`${API}/assets/${rowId}`, { headers: { Authorization: `Bearer ${login.access_token}` } })).json();
    check(asset.vote === 42, `${view.name}: Edit mode saved V=42`);
    await fetch(`${API}/assets/${rowId}`, { method: 'PATCH', headers: { Authorization: `Bearer ${login.access_token}`, 'content-type': 'application/json' }, body: JSON.stringify({ vote: Number(oldV) }) });
    await page.getByTestId('db-edit-mode').click();
  }

  // Jingles
  await page.getByRole('tab', { name: 'Jingles' }).click();
  await page.waitForTimeout(800);
  check(await rows().count() > 0, `${view.name}: jingles listed`);
  await shot('3-jingles');

  // Spots: an expired one shows red
  await page.getByRole('tab', { name: 'Spots' }).click();
  await rows().first().waitFor();
  const expired = page.locator('[data-expired]');
  check(await expired.count() > 0, `${view.name}: an expired spot is marked`);
  if (await expired.count()) {
    const color = await expired.first().evaluate(el => getComputedStyle(el).color);
    check(color === 'rgb(255, 64, 64)', `${view.name}: expired spot drawn in red (${color})`);
  }
  await noSideScroll('spots');
  await shot('4-spots');

  if (view.desktop) {
    // Edit mode: move the expired spot's end date 30 days on, then back
    const row = expired.first();
    const id = await row.getAttribute('data-row');
    const before = (await (await fetch(`${API}/databank/spots`, { headers: { Authorization: `Bearer ${login.access_token}` } })).json()).spots.find(s => s.id === id);
    await page.getByTestId('db-edit-mode').click();
    await row.getByTestId('spot-end').click();
    const d = new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10);
    await page.getByTestId('db-edit-input').fill(d);
    await page.getByTestId('db-edit-input').press('Enter');
    await page.getByText(/now runs until/).waitFor({ timeout: 8000 });
    check(await page.locator(`[data-row="${id}"][data-expired]`).count() === 0, `${view.name}: new end date clears the red`);
    await shot('5-spot-extended');
    await fetch(`${API}/databank/spots/${id}/end-date`, { method: 'PATCH', headers: { Authorization: `Bearer ${login.access_token}`, 'content-type': 'application/json' }, body: JSON.stringify({ end_date: before.end_date }) });
    await page.getByTestId('db-edit-mode').click();
  }

  // Blocks: one row per time, weekdays in KBR digits
  await page.getByRole('tab', { name: 'Blocks' }).click();
  await page.waitForTimeout(1000);
  const blockText = await rows().allInnerTexts();
  check(blockText.length > 0, `${view.name}: blocks listed (${blockText.length} rows)`);
  await shot('6-blocks');

  // Log of a day
  await page.getByRole('tab', { name: 'Log' }).click();
  const lines = page.locator('[data-testid="daylog-lines"] button');
  await lines.first().waitFor({ timeout: 8000 });
  const firstLine = await lines.first().innerText();
  check(/\d\d:\d\d:\d\d\.\d{3}/.test(firstLine) && /(Started|Ended)/.test(firstLine), `${view.name}: log line to the millisecond (${firstLine.slice(0, 60).replace(/\s+/g, ' ')})`);
  await noSideScroll('log');
  await shot('7-log-today');
  if (view.desktop) await page.locator('[data-testid="daylog-days"] button').nth(1).click();
  else await page.getByTestId('daylog-day').selectOption({ index: 1 });
  await page.waitForTimeout(1000);
  check(await lines.count() > 0, `${view.name}: yesterday's log listed`);
  await shot('8-log-yesterday');

  check(errors.length === 0, `${view.name}: no script or API errors ${errors.join(' | ')}`);
  await ctx.close();
}
await browser.close();
console.log(failures ? `${failures} failed` : 'All passed');
process.exit(failures ? 1 : 0);
