/**
 * Layout audit for every admin page, against a RUNNING site + backend.
 *
 *   BASE_URL=http://localhost:3000 ADMIN_USER=admin ADMIN_PASS=... node e2e/admin-layout-audit.mjs
 *   optional: SHOTS_DIR=/some/dir  ONLY=/admin/assets
 *
 * For each page at a wide desktop and at iPhone size it checks: no sideways
 * page scroll, content uses the window width (desktop), the nav stays at the
 * top when scrolled, no script errors and no failed API calls.
 * Exits non-zero if any check fails.
 */
import fs from 'node:fs';
import { createRequire } from 'node:module';
const { chromium, devices } = createRequire(import.meta.url)('playwright');
import { guardBrowser, guardVerdict } from './lib/guard.mjs';   // look at the screen: see lib/guard.mjs

const BASE = process.env.BASE_URL || 'http://localhost:3000';
const SHOTS = process.env.SHOTS_DIR || '';
const API = `${BASE}/api/v1`;

const login = await (await fetch(`${API}/auth/login`, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ email: process.env.ADMIN_USER || 'admin', password: process.env.ADMIN_PASS || '613Radio' }),
})).json();
if (!login.access_token) { console.error('Login failed', login); process.exit(2); }
const auth = { Authorization: `Bearer ${login.access_token}` };
const get = async (p) => (await fetch(`${API}${p}`, { headers: auth })).json().catch(() => ({}));
const assetId = (await get('/assets?limit=1')).assets?.[0]?.id;
const scheduleId = ((await get('/schedules')).schedules ?? [])[0]?.id;

let pages = [
  '/admin/stations', '/admin/assets', '/admin/assets/upload', assetId && `/admin/assets/${assetId}`,
  '/admin/reviews', '/admin/users', '/admin/rules', '/admin/schedules',
  scheduleId && `/admin/schedules/${scheduleId}/blocks`, '/admin/holidays', '/admin/sponsors',
  '/admin/spot-schedule', '/admin/schedule-check', '/admin/sponsorship-rules', '/admin/playlists',
  '/admin/categories', '/admin/asset-types', '/admin/alerts', '/admin/analytics', '/admin/live',
  '/admin/requests', '/admin/archives', '/admin/studio', '/admin/listeners', '/admin/crm',
  '/admin/program-clock', '/admin/activity', '/admin/dashboard', '/admin/databank',
].filter(Boolean);
if (process.env.ONLY) pages = pages.filter(p => p.startsWith(process.env.ONLY));

const VIEWS = [
  { name: 'desktop-1920', opts: { viewport: { width: 1920, height: 1080 } }, wide: true },
  { name: 'iphone', opts: { ...devices['iPhone 13'] }, wide: false },
  { name: 'small-phone', opts: { ...devices['iPhone SE'] }, wide: false },
];

let failures = 0;
const browser = guardBrowser(await chromium.launch());
for (const view of VIEWS) {
  const ctx = await browser.newContext(view.opts);
  await ctx.addInitScript(([a, r]) => { localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r); }, [login.access_token, login.refresh_token]);
  for (const path of pages) {
    const page = await ctx.newPage();
    const errors = [];
    const badApi = [];
    page.on('pageerror', e => errors.push(String(e).slice(0, 120)));
    page.on('response', r => { if (r.url().includes('/api/v1/') && r.status() >= 400) badApi.push(`${r.status()} ${new URL(r.url()).pathname.replace('/api/v1', '')}`); });
    await page.goto(`${BASE}${path}`);
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForTimeout(400);
    const m = await page.evaluate((deviceW) => {
      // A phone browser widens its layout viewport to fit content that sticks out,
      // so innerWidth would hide the overflow. Measure against the real screen width.
      const vw = deviceW;
      const main = document.querySelector('main');
      let widest = 0;
      const offenders = [];
      if (main) for (const el of main.querySelectorAll('*')) {
        const r = el.getBoundingClientRect();
        if (r.width > widest && r.width <= vw + 1) widest = r.width;
        // An element sticking out of the window that is not inside its own scroller
        if (r.right > vw + 1 && r.width > 0) {
          let p = el.parentElement, scrolled = false;
          while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') { scrolled = true; break; } p = p.parentElement; }
          if (!scrolled && offenders.length < 3) offenders.push(`${el.tagName.toLowerCase()}.${String(el.className).split(' ')[0]}`);
        }
      }
      return { vw, docW: document.documentElement.scrollWidth, used: Math.round((widest / vw) * 100), offenders, docH: document.documentElement.scrollHeight, vh: innerHeight };
    }, view.opts.viewport.width);
    let navOk = true;
    if (m.docH > m.vh + 50) {
      await page.evaluate(() => scrollTo(0, 400));
      await page.waitForTimeout(100);
      navOk = await page.evaluate(() => Math.round(document.querySelector('nav')?.getBoundingClientRect().top ?? -1) === 0);
      await page.evaluate(() => scrollTo(0, 0));
    }
    const problems = [];
    if (m.docW > m.vw + 1) problems.push(`sideways scroll (${m.docW}px wide in ${m.vw}px${m.offenders.length ? `: ${m.offenders.join(', ')}` : ''})`);
    if (view.wide && m.used < 90) problems.push(`content uses only ${m.used}% of the width`);
    if (!navOk) problems.push('nav scrolls away');
    if (errors.length) problems.push(`script error: ${errors[0]}`);
    if (badApi.length) problems.push(`API: ${[...new Set(badApi)].join(', ')}`);
    console.log(`${problems.length ? 'FAIL' : 'PASS'}  ${view.name.padEnd(13)} ${path.replace(/[0-9a-f-]{36}/g, ':id').padEnd(34)} ${problems.join('; ')}`);
    if (problems.length) failures++;
    if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: `${SHOTS}/${view.name}${path.replace(/[0-9a-f-]{36}/g, 'id').replace(/\//g, '_')}.png` }); }
    await page.close();
  }
  await ctx.close();
}
await browser.close();
console.log(failures ? `\n${failures} page view(s) with problems` : '\nAll page views passed');
process.exit((failures ? 1 : 0) || (guardVerdict() ? 1 : 0));
