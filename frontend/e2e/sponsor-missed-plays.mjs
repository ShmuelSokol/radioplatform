// Sponsor dashboard: the Missed Plays panel, against a fake backend, desktop and phone.
//   npm run build && (npx vite preview --port 4173 &) && node e2e/sponsor-missed-plays.mjs
import { createRequire } from 'node:module';

const { chromium } = createRequire(import.meta.url)('playwright');

const BASE = process.env.BASE_URL || 'http://localhost:4173';
const SHOTS = process.env.SHOTS_DIR || '';
const missed = {
  days: 14, missed_past: 3, missed_coming: 2, moved_coming: 1,
  rows: [
    { when: 'past', date: '2026-11-17', daypart: 'evening', station_name: "Kol B'ramah Main", asset_title: 'Pita Hut', promised: 2, played: 1, missed: 1, moved: 0, note: 'Did not air' },
    { when: 'past', date: '2026-11-16', daypart: 'morning', station_name: "Kol B'ramah Main", asset_title: 'Pita Hut', promised: 2, played: 0, missed: 2, moved: 0, note: 'Did not air' },
    { when: 'coming', date: '2026-11-19', daypart: 'evening', station_name: "Kol B'ramah Main", asset_title: 'Pita Hut', promised: 4, played: 0, missed: 2, moved: 0, note: 'No room left at this time of day' },
    { when: 'coming', date: '2026-11-19', daypart: 'afternoon', station_name: "Kol B'ramah Main", asset_title: 'Pita Hut', promised: 1, played: 0, missed: 0, moved: 1, note: 'Moved to a later time the same day (9:30 PM)' },
  ],
};
const answers = (rows) => (path) => {
  if (path === '/auth/me') return { id: 'u1', email: 'sponsor@example.com', role: 'sponsor', is_active: true };
  if (path === '/sponsor-portal/missed-plays') return rows;
  if (path === '/sponsor-portal/stats') return { total_plays_month: 12, total_plays_alltime: 40 };
  if (path === '/sponsor-portal/play-history') return { entries: [], total: 0, page: 1, limit: 25 };
  if (path === '/site-release') return { mode: 'auto' };
  return [];
};

let failed = 0;
const check = (ok, name, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`); if (!ok) failed += 1; };

const browser = await chromium.launch();
for (const [label, viewport, rows] of [
  ['desktop', { width: 1280, height: 900 }, missed],
  ['phone', { width: 390, height: 844 }, missed],
  ['none', { width: 1280, height: 900 }, { days: 14, missed_past: 0, missed_coming: 0, moved_coming: 0, rows: [] }],
]) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  await ctx.addInitScript(() => { localStorage.setItem('access_token', 'test'); localStorage.setItem('refresh_token', 'test'); });
  const page = await ctx.newPage();
  const answer = answers(rows);
  await page.route('**/api/v1/**', (route) => {
    const path = new URL(route.request().url()).pathname.replace(/^.*\/api\/v1/, '');
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(answer(path)) });
  });
  await page.goto(`${BASE}/sponsor/dashboard`);
  const panel = page.getByTestId('missed-plays');
  await panel.waitFor({ timeout: 15000 });
  const text = (await panel.innerText()).replace(/\s+/g, ' ');
  if (label === 'none') {
    check(text.includes('No missed plays'), `${label}: says there are no missed plays`);
  } else {
    check(text.includes('3 did not air') && text.includes('2 coming up with no room') && text.includes('1 moved to a later time'), `${label}: totals`, text.slice(0, 160));
    check(text.includes('Evening (6 PM to 12 AM)') && text.includes('Did not air'), `${label}: a past missed play with its time of day`);
    check(text.includes('Coming up: No room left at this time of day'), `${label}: a coming play with no room`);
    check(text.includes('Moved to a later time the same day (9:30 PM)'), `${label}: a moved play`);
    const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    check(!wide, `${label}: the page does not scroll sideways`);
  }
  if (SHOTS) await panel.screenshot({ path: `${SHOTS}/missed-plays-${label}.png` });
  await ctx.close();
}
await browser.close();
console.log(failed ? `${failed} failed` : 'all passed');
process.exit(failed ? 1 : 0);
