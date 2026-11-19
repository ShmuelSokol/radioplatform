/**
 * Screenshots of every staff page at desktop and iPhone size, for looking at the qnet
 * skin by eye (Shmuel 2026-11-19: "the view behind Go live looks weird and not in sync
 * with the qnet visual language"). Also measures, on each page, how much of the screen
 * is still the old dark navy look (pixels close to #0a0a28 / #14143c / gray-900), so a
 * page that falls outside the skin shows up as a number, not only in a picture.
 *
 * Needs the stack of e2e/walkthrough.mjs (Postgres backend + walkthrough-seed.py + the
 * built site on vite preview):
 *   E2E_ADMIN_EMAIL=... E2E_ADMIN_PASS=... BASE_URL=http://localhost:4173 \
 *     API_URL=http://localhost:8000/api/v1 SHOTS_DIR=/tmp/shots node e2e/qnet-look-shots.mjs
 * Env: ONLY=<comma list of paths>, SIZES=desktop,phone, MAX_DARK (default 0.08: share of
 * dark navy pixels above which a page is reported). Exit code 1 when a page is too dark.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const { chromium, devices } = createRequire(import.meta.url)('playwright');

const BASE = process.env.BASE_URL || 'http://localhost:4173';
const API = process.env.API_URL || 'http://localhost:8000/api/v1';
const SHOTS = process.env.SHOTS_DIR || '';
const ONLY = (process.env.ONLY || '').split(',').map((s) => s.trim()).filter(Boolean);
const SIZES = (process.env.SIZES || 'desktop,phone').split(',');
const MAX_DARK = Number(process.env.MAX_DARK || 0.08);

async function api(token, p, init = {}) {
  const r = await fetch(`${API}${p}`, { ...init, headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}), ...(init.headers || {}) } });
  return r.ok ? r.json() : null;
}

const login = await api(null, '/auth/login', { method: 'POST', body: JSON.stringify({ email: process.env.E2E_ADMIN_EMAIL, password: process.env.E2E_ADMIN_PASS }) });
if (!login?.access_token) { console.error('login failed'); process.exit(2); }
const token = login.access_token;
const first = (x, k) => (Array.isArray(x) ? x : x?.[k] ?? [])[0];
const show = first(await api(token, '/live-shows?limit=5'), 'shows');
const sponsor = first(await api(token, '/sponsors'), 'sponsors') ?? first(await api(token, '/sponsors/clients'), 'clients');
const schedule = first(await api(token, '/schedules'), 'schedules');
const asset = first(await api(token, '/assets?limit=1'), 'assets');

const ROUTES = [
  '/admin/dashboard', '/admin/stations', '/admin/assets', '/admin/assets/upload',
  asset && `/admin/assets/${asset.id}`, '/admin/reviews', '/admin/users', '/admin/rules', '/admin/schedules',
  schedule && `/admin/schedules/${schedule.id}/blocks`, '/admin/holidays', '/admin/sponsors',
  sponsor && `/admin/sponsors/${sponsor.id}`, '/admin/sponsors?book=1', '/admin/campaigns', '/admin/spot-schedule',
  '/admin/sponsorship-board', '/admin/spot-recordings', '/admin/databank', '/admin/rds', '/admin/program-blocks',
  '/admin/holiday-music', '/admin/schedule-check', '/admin/sponsorship-rules', '/admin/playlists',
  '/admin/categories', '/admin/asset-types', '/admin/alerts', '/admin/emergency', '/admin/bug-reports',
  '/admin/site-updates', '/admin/activity', '/admin/analytics', '/admin/live',
  show && `/admin/live/${show.id}/host`, show && `/admin/live/${show.id}/screen`,
  '/admin/requests', '/admin/archives', '/admin/studio', '/admin/listeners', '/admin/crm', '/admin/program-clock',
].filter(Boolean).filter((r) => !ONLY.length || ONLY.some((o) => r.startsWith(o)));

// Control room side tabs (the Go live view lives here) and the bug report dialog
const CR_TABS = ['Go live', 'Played', 'Sponsors', 'Schedule', 'Errors'];

/** Share of the page's pixels that are the old dark navy / near-black-blue look. */
async function darkShare(page) {
  return page.evaluate(async () => {
    // Read the rendered colours of every visible element box (no pixel access in-page):
    // weight each element by its own visible area not covered by children with a background.
    const W = innerWidth, H = innerHeight;
    const grid = 24; let dark = 0, total = 0;
    for (let y = grid / 2; y < H; y += grid) for (let x = grid / 2; x < W; x += grid) {
      let el = document.elementFromPoint(x, y); total++;
      while (el) {
        const bg = getComputedStyle(el).backgroundColor;
        const m = /rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)/.exec(bg);
        if (m && (m[4] === undefined || Number(m[4]) > 0.5)) {
          const [r, g, b] = [+m[1], +m[2], +m[3]];
          // dark and bluish (navy panels), or the gray-800/900 slabs of the old dark pages
          if ((r < 60 && g < 60 && b > r + 10 && b < 110) || (r < 50 && g < 55 && b < 70 && b > 20)) dark++;
          break;
        }
        el = el.parentElement;
      }
    }
    return total ? dark / total : 0;
  });
}

const browser = await chromium.launch();
const report = [];
let bad = 0;
for (const size of SIZES) {
  const opts = size === 'phone' ? { ...devices['iPhone 13'] } : { viewport: { width: 1440, height: 900 } };
  const ctx = await browser.newContext(opts);
  await ctx.addInitScript(([a, r]) => { localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r); }, [token, login.refresh_token]);
  const page = await ctx.newPage();
  const shoot = async (label) => {
    const share = await darkShare(page);
    const name = `${size}-${label.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '')}.png`;
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, name), fullPage: size !== 'phone' || !label.startsWith('admin-dashboard') });
    const flag = share > MAX_DARK;
    if (flag) bad++;
    report.push({ size, label, dark: Math.round(share * 100), shot: name });
    console.log(`${flag ? 'DARK ' : 'ok   '} ${size.padEnd(7)} ${String(Math.round(share * 100)).padStart(3)}%  ${label}`);
  };
  for (const r of ROUTES) {
    await page.goto(`${BASE}${r}`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(r.includes('dashboard') ? 2500 : 1500);
    await shoot(r.slice(1));
    if (r === '/admin/dashboard') {
      for (const t of CR_TABS) {
        // Phone: Go live is a bottom tab; the others are under More
        let btn = page.getByRole(size === 'phone' ? 'button' : 'tab', { name: t, exact: true }).first();
        if (size === 'phone' && t !== 'Go live') {
          await page.getByRole('button', { name: /More/ }).first().click().catch(() => {});
          await page.waitForTimeout(300);
          btn = page.getByRole('button', { name: t, exact: true }).first();
        }
        if (await btn.count()) {
          await btn.click().catch(() => {});
          await page.waitForTimeout(800);
          await shoot(`dashboard-tab-${t}`);
        }
      }
    }
  }
  // Dialogs and popups that a page walk does not open by itself
  const DIALOGS = [
    ['/admin/stations', 'bug-report-dialog', async () => { await page.locator('[data-bug-trigger]:visible').first().click(); await page.getByRole('dialog', { name: 'Report a bug' }).waitFor({ timeout: 8000 }); }],
    ['/admin/live', 'new-live-show-dialog', async () => { await page.getByRole('button', { name: /New Show/ }).first().click(); }],
    ['/admin/sponsors', 'book-sponsorship-dialog', async () => { await page.getByRole('button', { name: /Book a sponsorship/ }).first().click(); }],
  ].filter(([r]) => !ONLY.length || ONLY.some((o) => r.startsWith(o)));
  for (const [r, label, open] of DIALOGS) {
    await page.goto(`${BASE}${r}`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    try { await open(); await page.waitForTimeout(1000); await shoot(label); }
    catch (e) { console.log(`skip  ${size} ${label}: ${String(e).split('\n')[0]}`); }
  }
  await ctx.close();
}
await browser.close();
if (SHOTS) fs.writeFileSync(path.join(SHOTS, 'report.json'), JSON.stringify(report, null, 1));
console.log(`\n${bad} screen(s) above ${Math.round(MAX_DARK * 100)}% dark navy`);
process.exit(bad ? 1 : 0);
