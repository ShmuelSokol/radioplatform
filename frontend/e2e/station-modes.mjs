/**
 * HS, Spots mode, Panic and Errors in the control room, against a RUNNING site + backend
 * whose station plays real audio (browser player, not the playout engine).
 *
 *   BASE_URL=http://localhost:3000 ADMIN_USER=admin ADMIN_PASS=... STATION_ID=... node e2e/station-modes.mjs
 *   optional: SHOTS_DIR=/some/dir
 *
 * Listening check: the control room's on-air meters are sampled every 200 ms before and
 * after Panic; the station must not go quiet for more than a second, and a different
 * item must be on air afterwards. Do not run against a station that is on the air (Panic
 * cuts the item playing); KBR Jerusalem or All Day Stories only on production.
 */
import fs from 'node:fs';
import { createRequire } from 'node:module';
const { chromium, devices } = createRequire(import.meta.url)('playwright');

const BASE = process.env.BASE_URL || 'http://localhost:3000';
const SHOTS = process.env.SHOTS_DIR || '';
const API = `${BASE}/api/v1`;
const SID = process.env.STATION_ID;
if (!SID) { console.error('Set STATION_ID'); process.exit(2); }
const login = await (await fetch(`${API}/auth/login`, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ email: process.env.ADMIN_USER || 'admin', password: process.env.ADMIN_PASS }),
})).json();
if (!login.access_token) { console.error('Login failed', login); process.exit(2); }
const H = { Authorization: `Bearer ${login.access_token}` };
const queue = async () => (await fetch(`${API}/stations/${SID}/queue?limit=5`, { headers: H })).json();
const onAirId = async () => (await queue()).entries?.find(e => e.status === 'playing')?.id ?? null;

let failures = 0;
const check = (ok, what) => { console.log(`${ok ? 'PASS' : 'FAIL'} ${what}`); if (!ok) failures++; };
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });

/* ── Desktop ── */
{
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  await ctx.addInitScript(([a, r, s]) => {
    localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r);
    localStorage.setItem('radio_last_station', s);
  }, [login.access_token, login.refresh_token, SID]);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e).slice(0, 160)));
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor({ timeout: 20000 });
  await page.getByText('Up next', { exact: true }).first().click();  // starts the (silent) player for the meters
  const lit = () => page.locator('header .bg-cyan-400, header .bg-red-500').count();
  const sample = async (ms) => {
    const out = [];
    for (let t = 0; t < ms; t += 200) { out.push(await lit()); await page.waitForTimeout(200); }
    return out;
  };
  const longestQuiet = (s) => { let best = 0, run = 0; for (const v of s) { run = v === 0 ? run + 1 : 0; best = Math.max(best, run); } return best * 200; };

  let first = 0;
  for (let i = 0; i < 40 && first === 0; i++) { await page.waitForTimeout(250); first = await lit(); }
  check(first > 0, `desktop: station audio heard on the meters before Panic (${first} lit)`);
  const before = await sample(3000);
  check(longestQuiet(before) <= 400, `desktop: no silence before Panic (longest quiet ${longestQuiet(before)} ms)`);

  // Panic: first tap only arms it
  const was = await onAirId();
  await page.getByTestId('panic').click();
  check((await page.getByTestId('panic').innerText()).includes('Tap again'), 'desktop: first Panic tap asks for a second');
  check(await onAirId() === was, 'desktop: one tap cuts nothing');
  await page.getByTestId('panic').click();
  await page.getByText(/Panic: cut what was on air/).waitFor({ timeout: 8000 });
  const after = await sample(6000);
  const now = await onAirId();
  check(now && now !== was, 'desktop: Panic put the next item on air');
  check(after.some(v => v > 0) && longestQuiet(after) <= 1000, `desktop: sound carries on after Panic (longest quiet ${longestQuiet(after)} ms)`);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/desktop-panic.png` });

  // HS off and on again
  await page.getByTestId('mode-hs').click();
  await page.getByText(/HS is off/).waitFor({ timeout: 8000 });
  check((await queue()).modes?.hard_start === false, 'desktop: HS off reaches the server');
  check(await page.getByTestId('mode-hs').getAttribute('aria-pressed') === 'false', 'desktop: HS button shows off');
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/desktop-hs-off.png` });
  await page.getByTestId('mode-hs').click();
  await page.getByText(/HS is on/).waitFor({ timeout: 8000 });
  check((await queue()).modes?.hard_start === true, 'desktop: HS back on');

  // Spots mode off and on again
  await page.getByTestId('mode-spots').click();
  await page.getByText(/Spots mode is off/).waitFor({ timeout: 8000 });
  check((await queue()).modes?.spots === false, 'desktop: Spots mode off reaches the server');
  await page.getByTestId('mode-spots').click();
  await page.getByText(/Spots mode is on/).waitFor({ timeout: 8000 });
  check((await queue()).modes?.spots === true, 'desktop: Spots mode back on');

  const after2 = await sample(3000);
  check(longestQuiet(after2) <= 400, `desktop: no silence while switching modes (longest quiet ${longestQuiet(after2)} ms)`);

  // Errors pane lists the Panic
  await page.getByRole('tab', { name: 'Errors' }).click();
  await page.getByTestId('errors-panel').getByText('Panic pressed in the control room').first().waitFor({ timeout: 8000 });
  check(true, 'desktop: Errors pane lists the Panic');
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/desktop-errors.png` });
  check(errors.length === 0, `desktop: no script errors ${errors.join(' | ')}`);
  await ctx.close();
}

/* ── iPhone ── */
{
  const ctx = await browser.newContext({ ...devices['iPhone 13'] });
  await ctx.addInitScript(([a, r, s]) => {
    localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r);
    localStorage.setItem('radio_last_station', s);
  }, [login.access_token, login.refresh_token, SID]);
  const page = await ctx.newPage();
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByRole('button', { name: /More/ }).click();
  for (const id of ['mode-hs', 'mode-spots', 'panic']) {
    const box = await page.getByTestId(id).boundingBox();
    check(box && box.height >= 44, `iphone: ${id} is a 44 px tap target (${box?.height})`);
  }
  await page.getByRole('button', { name: 'Errors', exact: true }).click();
  await page.getByTestId('errors-panel').waitFor();
  check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'iphone: no sideways scroll');
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/iphone-more.png` });
  await ctx.close();
}

await browser.close();
console.log(failures ? `${failures} failed` : 'All passed');
process.exit(failures ? 1 : 0);
