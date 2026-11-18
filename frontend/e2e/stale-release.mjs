/**
 * A new website release comes out under an open page; the person then opens each
 * screen whose code loads only when first opened. The old build's code files are
 * gone and the host answers with the site's page (200, text/html), as the real host
 * does. Every such screen must come up on the new release by itself: no error box,
 * no "Something went wrong", never stuck on "Loading...".
 * (Shmuel's iPhone 2026-11-17 10:08 PM; the PC's walk-through 2026-11-18 04:29Z.)
 *
 * Builds the site twice itself (two build ids), so no preview server is needed:
 *   cd frontend && node e2e/stale-release.mjs      # optional: ONLY=<text in the route>  CONCURRENCY=6  DIST_A= DIST_B=
 *
 * Exits non-zero if a check fails.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, checker, sleep, makeStation, AUDIO_PROBE, routeApi, routeNowPlaying } from './lib/fake-station.mjs';
import { startReleaseHost } from './lib/release-server.mjs';
import { allowEverywhere } from './lib/guard.mjs';

// What this suite provokes on purpose: the old release's code files answered with the
// site's page. Each browser reports that in the console, and the release box may show
// for a moment before the tab moves on (the checks below judge that box themselves).
allowEverywhere(/MIME type of "text\/html"|Failed to fetch dynamically imported module|Importing a module script failed|ErrorBoundary caught: TypeError: Failed to fetch dynamically/,
  'the old release\'s code files are gone and the host answers with the site\'s page: the situation under test');
allowEverywhere(/error shown: The site was updated while this tab was open/, 'the release box this suite checks for itself');
// The fake backend (lib/fake-station.mjs) answers only what the player screens need. These
// screens get no data of the shape they read and show "Something went wrong"; with real
// data they are checked by admin-flows.mjs and the walk-through (walkthrough.mjs).
const NO_FAKE_DATA = ['/admin/assets', '/admin/assets/m1', '/admin/program-blocks', '/admin/campaigns', '/admin/campaigns/c1',
  '/admin/schedules', '/admin/schedules/s1/blocks', '/admin/playlists', '/admin/sponsors', '/admin/sponsorship-rules',
  '/admin/crm', '/admin/spot-schedule', '/admin/spot-recordings', '/archives'];
const pageOf = (url) => { try { return new URL(url, 'http://x').pathname; } catch { return ''; } };
allowEverywhere((rec) => NO_FAKE_DATA.includes(pageOf(rec.url))
  && /^(TypeError: |ErrorBoundary caught: TypeError: |Something went wrong )/.test(rec.text || ''),
'this screen has no data in the fake backend; real data: admin-flows.mjs and walkthrough.mjs');
allowEverywhere(/WebSocket connection to '.*\/api\/v1\/listeners\/ws' failed/, 'the fake backend has no listeners WebSocket');

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = Number(process.env.PORT || 4331);
const ONLY = process.env.ONLY || '';
const CONCURRENCY = Number(process.env.CONCURRENCY || 6);
const { check, done } = checker({});

function build(id) {
  const out = fs.mkdtempSync(path.join(os.tmpdir(), `kbr-release-${id}-`));
  execFileSync('npx', ['vite', 'build', '--outDir', out, '--emptyOutDir', '--logLevel', 'error'], { cwd: root, env: { ...process.env, VERCEL_GIT_COMMIT_SHA: id }, stdio: 'inherit' });
  return out;
}
const distA = process.env.DIST_A || build('release-a');
const distB = process.env.DIST_B || build('release-b');
const names = (dist) => fs.readdirSync(path.join(dist, 'assets')).filter((f) => f.endsWith('.js'));
const gone = names(distA).filter((f) => !names(distB).includes(f));
check('setup: the two builds differ (code files of the first are gone in the second)', gone.length > 10, `${gone.length} of ${names(distA).length} files replaced`);

// Every screen in App.tsx that loads its code when first opened, by where it is opened from
const FROM_PUBLIC = ['/listen/st-1', '/call/st-1', '/guide', '/hosts', '/archives'];
const FROM_ADMIN = [
  '/admin/dashboard', '/admin/stations', '/admin/assets', '/admin/assets/upload', '/admin/assets/m1', '/admin/reviews', '/admin/reviews/q1',
  '/admin/users', '/admin/rules', '/admin/schedules', '/admin/schedules/s1/blocks', '/admin/holidays', '/admin/sponsors', '/admin/campaigns',
  '/admin/campaigns/c1', '/admin/spot-schedule', '/admin/spot-recordings', '/admin/databank', '/admin/program-blocks', '/admin/schedule-check',
  '/admin/sponsorship-rules', '/admin/playlists', '/admin/categories', '/admin/asset-types', '/admin/alerts', '/admin/bug-reports',
  '/admin/site-updates', '/admin/activity', '/admin/analytics', '/admin/live', '/admin/live/ls1/host', '/admin/live/ls1/screen', '/admin/requests',
  '/admin/archives', '/admin/audio-enhance/m1', '/admin/studio', '/admin/listeners', '/admin/crm', '/admin/program-clock',
];
// The route list above must name every lazy screen of App.tsx
{
  const app = fs.readFileSync(path.join(root, 'src/App.tsx'), 'utf8');
  const lazy = [...app.matchAll(/const (\w+) = lazy\(/g)].map((m) => m[1]);
  const routes = [...app.matchAll(/<Route path="([^"]+)" element=\{(?:<\w+>)*<(\w+) \/>/g)].filter((m) => lazy.includes(m[2])).map((m) => m[1]);
  const shape = (p) => p.replace(/\/(st-1|m1|q1|s1|c1|ls1)(?=\/|$)/g, '/:x');
  const covered = new Set([...FROM_PUBLIC, ...FROM_ADMIN, '/stations'].map(shape));
  const missing = routes.filter((r) => !/^\/sponsor\//.test(r) && !covered.has(r.replace(/:\w+/g, ':x')));
  check('setup: every lazy screen in App.tsx (staff and public) is in this check', missing.length === 0 && routes.length > 30, missing.length ? `missing: ${missing.join(', ')}` : `${routes.length} lazy routes`);
}

const host = await startReleaseHost(PORT, distA, distB);

async function openOn(browser, startPath, { listen = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const station = makeStation();
  await ctx.addInitScript(() => { localStorage.setItem('access_token', 'test'); localStorage.setItem('refresh_token', 'test'); });
  await ctx.addInitScript(AUDIO_PROBE);
  await routeApi(ctx, station);
  await routeNowPlaying(ctx, station);
  await ctx.routeWebSocket(/\/ws\/(live|listeners|site)/, () => {});
  await ctx.setExtraHTTPHeaders({ 'x-kbr-build': '1' });
  const page = await ctx.newPage();
  const tab = { ctx, page, loads: 0, errors: [], moduleErrors: 0 };
  page.on('load', () => tab.loads++);
  page.on('pageerror', (e) => tab.errors.push(String(e)));
  page.on('console', (m) => { if (/module script/i.test(m.text())) tab.moduleErrors++; });
  await page.goto(host.origin + startPath);
  await page.waitForFunction(() => !!document.querySelector('nav, [data-testid=control-room]'), null, { timeout: 8000 });
  if (listen) {
    await page.getByTestId('queue-row').first().waitFor();
    await page.locator('button:visible', { hasText: 'Listen' }).first().click();
    await page.waitForFunction(() => window.__audible().length >= 1 && window.__outLevel() > 0, null, { timeout: 5000 }).catch(() => {});
  }
  await sleep(400);
  return tab;
}
const version = (page) => page.evaluate(async () => {
  // the build id is baked into the entry file the page runs
  const src = [...document.scripts].map((s) => s.src).find((s) => /\/assets\/index-/.test(s));
  return src ? src.split('/').pop() : null;
});
const entry = (dist) => names(dist).find((f) => f.startsWith('index-'));

async function openScreen(browser, startPath, route, tag, opts = {}) {
  const tab = await openOn(browser, startPath, opts);
  const { page } = tab;
  const before = await version(page);
  await tab.ctx.setExtraHTTPHeaders({ 'x-kbr-build': '2' }); // the release comes out
  const asked0 = host.requests.length;
  if (opts.click) await page.locator(opts.click).locator('visible=true').first().click();
  else await page.evaluate((to) => { window.history.pushState({}, '', to); window.dispatchEvent(new PopStateEvent('popstate')); }, route);
  // Settled: reloaded onto the new build, on the screen asked for, with something drawn
  const ok = await page.waitForFunction(([want]) => {
    const src = [...document.scripts].map((s) => s.src).find((s) => /\/assets\/index-/.test(s));
    return !!src && src.endsWith(want) && document.readyState === 'complete'
      && !!document.querySelector('nav, [data-testid=control-room], main, h1, h2')
      && !/^\s*Loading\.\.\.\s*$/.test(document.getElementById('root')?.innerText || 'Loading...');
  }, [entry(distB)], { timeout: 8000 }).then(() => true, () => false);
  await sleep(300);
  const text = (await page.locator('body').innerText().catch(() => '')).replace(/\s+/g, ' ');
  const box = await page.locator('[data-testid=error-boundary]').count();
  const after = await version(page);
  const staleAsked = host.requests.slice(asked0).filter((r) => !r.found && r.path.startsWith('/assets/')).length;
  check(`${tag}: the old code file was asked for and the host sent the site page`, staleAsked >= 1 && before === entry(distA), `${staleAsked} stale requests`);
  check(`${tag}: the tab moves to the new release by itself`, ok && after === entry(distB) && tab.loads === 2, `loads=${tab.loads} runs ${after === entry(distB) ? 'new' : 'old'} build`);
  // (the fake backend has no data for some screens, which then show their own error box:
  // that is not this check's subject, only an error about the release or a code file is)
  const releaseError = /site was updated while|MIME type|dynamically imported|module script|Importing a module/i.test(text);
  check(`${tag}: the screen asked for is shown on the new release, no release error box, not stuck on Loading`, ok && !releaseError && new URL(page.url()).pathname === route.split('?')[0], `box=${box} url=${new URL(page.url()).pathname} text=${JSON.stringify(text.slice(0, 60))}`);
  check(`${tag}: no script errors after the move`, tab.errors.length === 0, tab.errors.join(' | ').slice(0, 200));
  await tab.ctx.close();
}

const jobs = [];
for (const r of FROM_PUBLIC) jobs.push([`from the station list to ${r}`, (b) => openScreen(b, '/stations', r, `stations -> ${r}`)]);
for (const r of FROM_ADMIN) {
  const from = r === '/admin/dashboard' ? '/admin/stations' : '/admin/dashboard';
  jobs.push([`from ${from} to ${r}`, (b) => openScreen(b, from, r, `${from.replace('/admin/', '')} -> ${r}`)]);
}
// The walk-through's case: the control room with Listen on, then the DB button
jobs.push(['control room listening, DB button', (b) => openScreen(b, '/admin/dashboard', '/admin/databank', 'control room (listening) -> DB button', { listen: true, click: '[data-testid=cr-databank]' })]);
jobs.push(['control room silent, DB button', (b) => openScreen(b, '/admin/dashboard', '/admin/databank', 'control room -> DB button', { click: '[data-testid=cr-databank]' })]);

const browser = await chromium.launch();
try {
  const queue = jobs.filter(([name]) => !ONLY || name.includes(ONLY));
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, queue.length) }, async () => {
    while (queue.length) {
      const [name, fn] = queue.shift();
      try { await fn(browser); } catch (e) { check(`${name}: ran to the end`, false, String(e).split('\n')[0]); }
    }
  }));
} finally {
  await browser.close();
  await host.close();
}
process.exit(done());
