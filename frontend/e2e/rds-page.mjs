/**
 * The RDS page against a real backend (see wiki/rds-feed.md):
 * on-air display, settings save, adding a line from a template, the Hebrew and
 * length warnings while typing, pause/resume, the feed links, and a phone-size
 * pass with no sideways scroll. Every step runs under the look-at-the-screen guard.
 *
 *   BASE_URL=http://localhost:3000 API=http://localhost:8000 KBR_ADMIN_EMAIL=admin KBR_ADMIN_PASSWORD=... \
 *     SHOTS=/tmp/rds node e2e/rds-page.mjs
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import { guardPage } from './lib/guard.mjs';

const { chromium, devices } = createRequire(import.meta.url)('playwright');

const BASE = process.env.BASE_URL || 'http://localhost:3000';
const API = process.env.API || 'http://localhost:8000';
const SHOTS = process.env.SHOTS || '';
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

const login = await fetch(`${API}/api/v1/auth/login`, {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ email: process.env.KBR_ADMIN_EMAIL || 'admin', password: process.env.KBR_ADMIN_PASSWORD }),
}).then((r) => r.json());
if (!login.access_token) throw new Error('login failed');

const failures = [];
const check = (ok, what) => { console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`); if (!ok) failures.push(what); };

async function open(context, name) {
  await context.addInitScript(([a, r]) => {
    localStorage.setItem('access_token', a);
    localStorage.setItem('refresh_token', r);
  }, [login.access_token, login.refresh_token]);
  const page = await context.newPage();
  const guard = guardPage(page, name);
  await page.goto(`${BASE}/admin/rds`);
  await page.getByRole('heading', { name: 'RDS', exact: true }).waitFor();
  // The display shows a placeholder until the first preview answers
  await page.waitForFunction(() => {
    const d = document.querySelector('[aria-label="What radios show now"]');
    return d && d.children[0]?.textContent?.trim() && d.children[1]?.textContent?.trim() !== '…';
  });
  return { page, guard };
}

const browser = await chromium.launch();

// ── desktop ──
{
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const { page, guard } = await open(ctx, 'rds desktop');
  const display = page.getByLabel('What radios show now');
  const ps = (await display.locator('div').first().innerText()).trim();
  check(ps.length > 0 && ps.length <= 8, `PS on the display is 1-8 characters ("${ps}")`);
  const rt = (await display.locator('div').nth(1).innerText()).trim();
  check(rt.length > 0 && rt.length <= 64, `RadioText on the display is 1-64 characters ("${rt}")`);
  check((await page.locator('table').first().locator('tr').count()) > 5, 'next 10 minutes lists the coming lines');
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/desktop-page.png`, fullPage: true });
  check((await guard.expectOnly('open the page')).length === 0, 'page opened with nothing unexpected on screen');

  // Add a slogan line from the template
  await page.getByRole('button', { name: '+ Slogan' }).click();
  const line = page.locator('form input[maxlength="200"]');
  await line.fill('Shabbos שבת Kol B\'ramah');
  await page.getByText(/Hebrew is dropped/).waitFor();
  check(await page.getByText("Shows as: Shabbos Kol B'ramah").isVisible().catch(() => false)
    || (await page.locator('form').innerText()).includes("Shabbos Kol B'ramah"), 'Hebrew warning and the line as radios show it');
  await line.fill('x'.repeat(70));
  await page.getByText(/radios show 64/).waitFor();
  check(true, 'over-64 warning appears while typing');
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/desktop-too-long.png` });
  await page.getByRole('button', { name: 'Add line' }).click();
  await page.getByText(/radios show 64/).nth(1).waitFor().catch(() => {});
  check((await page.locator('form .text-red-600').count()) > 0, 'a 70-character line is refused with a reason on screen');
  await line.fill("Kol B'ramah - the voice of Lakewood");
  await page.getByRole('button', { name: 'Add line' }).click();
  await page.getByText("Kol B'ramah - the voice of Lakewood").first().waitFor();
  check(true, 'new line appears in the rotation list');
  check((await guard.expectOnly('add a line', [/422/, /status of 422/])).length === 0, 'adding a line: only the expected refusal');

  // Pause and resume it
  const row = page.locator('li', { hasText: "Kol B'ramah - the voice of Lakewood" });
  await row.getByRole('button', { name: 'Pause' }).click();
  await row.getByRole('button', { name: 'Resume' }).waitFor();
  await row.getByRole('button', { name: 'Resume' }).click();
  await row.getByRole('button', { name: 'Pause' }).waitFor();
  check(true, 'pause and resume a line');

  // Settings save
  await page.getByRole('button', { name: 'Save settings' }).click();
  await page.getByText('Saved.').waitFor();
  check(true, 'settings save');
  check((await page.locator('code', { hasText: '/rds.txt' }).count()) === 1, 'RadioText feed link shown');
  check((await guard.expectOnly('pause, resume, save')).length === 0, 'nothing unexpected after pause/resume/save');
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/desktop-after.png`, fullPage: true });
  await row.getByRole('button', { name: 'Delete' }).click();
  await row.waitFor({ state: 'detached' });
  check(true, 'delete a line');
  await ctx.close();
}

// ── phone ──
{
  const ctx = await browser.newContext({ ...devices['iPhone 13'] });
  const { page, guard } = await open(ctx, 'rds iphone');
  const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(sideways <= 1, `no sideways scroll on iPhone 13 (${sideways}px)`);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/iphone-page.png`, fullPage: true });
  await page.getByRole('button', { name: '+ Sponsor' }).click();
  const sideways2 = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check(sideways2 <= 1, `no sideways scroll with the line form open (${sideways2}px)`);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/iphone-sponsor-form.png`, fullPage: true });
  check((await guard.expectOnly('phone')).length === 0, 'phone: nothing unexpected on screen');
  await ctx.close();
}

await browser.close();
console.log(failures.length ? `\n${failures.length} FAILED` : '\nall passed');
process.exit(failures.length ? 1 : 0);
