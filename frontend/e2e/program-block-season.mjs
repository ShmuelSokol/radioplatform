/**
 * Program Blocks page: "Only part of the year" (the kids-in-car reminders, 2026-11-18).
 * Against a fake backend: a block with a season shows "May 15 to Sep 15 each year" in the
 * list; editing opens the season pickers filled in; changing the last day saves "MM-DD";
 * switching it off saves null for both (all year); a new block saves its season.
 * Desktop and iPhone sizes, a screenshot of each when SHOTS=<dir>.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4312 --strictPort &)
 *   BASE_URL=http://localhost:4312 node e2e/program-block-season.mjs
 */
import { chromium, devices, checker, makeStation, adminContext, newPage, routeSiteSocket } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4312';
const SHOTS = process.env.SHOTS || '';
const { check, done } = checker({});

function blockStation() {
  const saved = [];
  let blocks;
  const extra = (method, path, _query, body) => {
    if (path === '/program-blocks' && method === 'GET') return blocks;
    if (path === '/playlists/asset-types') return [{ asset_type: 'spot', category: 'kids_car_summer', count: 2 }, { asset_type: 'spot', category: 'kids_car_year', count: 2 }];
    if (path === '/program-blocks/preview') return { timezone: 'America/New_York', hours: 24, occurrences: [] };
    if (path === '/program-blocks' && method === 'POST') {
      saved.push({ method, body });
      const b = { id: `pb${blocks.length + 1}`, is_active: true, latest_minutes: null, ...body };
      blocks.push(b);
      return b;
    }
    const m = /^\/program-blocks\/([^/]+)$/.exec(path);
    if (m && method === 'PUT') {
      saved.push({ method, id: m[1], body });
      const b = blocks.find((x) => x.id === m[1]);
      Object.assign(b, body);
      return b;
    }
    return undefined;
  };
  const station = makeStation({ queueSize: 3, extra });
  blocks = [
    {
      id: 'pb1', station_id: station.SID, name: 'Kids in car reminder - summer', is_active: true,
      days_of_week: '0,1,2,3,4,5,6', times: '08:20,11:50,14:20,17:50', timing_mode: 'soft',
      latest_minutes: null, season_start: '05-15', season_end: '09-15',
      items: [{ kind: 'asset', asset_id: 'j1', label: 'Siren' }, { kind: 'category', asset_type: 'spot', category: 'kids_car_summer', label: 'Kids in car reminder' }],
    },
    {
      id: 'pb2', station_id: station.SID, name: 'Hourly ID', is_active: true,
      days_of_week: '0,1,2,3,4,5,6', times: '09:00', timing_mode: 'hard', latest_minutes: null,
      items: [{ kind: 'asset', asset_id: 'j2', label: 'Station ID' }],
    },
  ];
  return { station, saved };
}

const browser = await chromium.launch();
for (const [view, opts] of [['desktop', { viewport: { width: 1440, height: 900 } }], ['iphone', { ...devices['iPhone 13'] }]]) {
  const { station, saved } = blockStation();
  const { ctx } = await adminContext(browser, opts, station);
  await routeSiteSocket(ctx);
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/program-blocks`);
  const row = page.getByText(/May 15 to Sep 15 each year/);
  await row.first().waitFor({ timeout: 8000 }).catch(() => {});
  check(`${view}: list says the season`, await row.count() === 1);
  check(`${view}: a block with no season says nothing about it`,
    !(await page.getByText('Hourly ID').locator('..').locator('..').getByText(/each year/).count()));
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${view}-1-list.png`, fullPage: true });

  // Edit the seasonal block: pickers filled in, move the last day to Sep 30
  await page.getByRole('button', { name: 'Edit' }).first().click();
  const form = page.locator('form');
  const box = form.getByLabel('Only part of the year');
  check(`${view}: edit shows the season ticked`, await box.isChecked());
  check(`${view}: first day filled in`,
    (await form.getByLabel('First day month').inputValue()) === '5' && (await form.getByLabel('First day day').inputValue()) === '15');
  await form.getByLabel('Last day day').selectOption('30');
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${view}-2-edit.png`, fullPage: true });
  await form.getByRole('button', { name: 'Save changes' }).click();
  await page.waitForTimeout(500);
  let last = saved.at(-1);
  check(`${view}: save sends MM-DD`, last?.method === 'PUT' && last.body.season_start === '05-15' && last.body.season_end === '09-30', JSON.stringify(last?.body ?? null));

  // Month with fewer days clamps the day: Mar 31 -> Feb gives Feb 29
  await page.getByRole('button', { name: 'Edit' }).first().click();
  await form.getByLabel('First day month').selectOption('3');
  await form.getByLabel('First day day').selectOption('31');
  await form.getByLabel('First day month').selectOption('2');
  check(`${view}: day clamps to the month`, (await form.getByLabel('First day day').inputValue()) === '29');

  // Switch the season off: all year
  await form.getByLabel('Only part of the year').uncheck();
  check(`${view}: pickers hide when off`, !(await form.getByLabel('First day month').count()));
  await form.getByRole('button', { name: 'Save changes' }).click();
  await page.waitForTimeout(500);
  last = saved.at(-1);
  check(`${view}: off saves all year`, last?.body.season_start === null && last?.body.season_end === null);

  // A new block with a season wrapping the new year
  await page.getByRole('button', { name: 'New block' }).click();
  await form.getByPlaceholder('Weather Sponsors').fill('Kids in car reminder - rest of year');
  await form.getByLabel('Only part of the year').check();
  await form.getByLabel('First day month').selectOption('9');
  await form.getByLabel('First day day').selectOption('16');
  await form.getByLabel('Last day month').selectOption('5');
  await form.getByLabel('Last day day').selectOption('14');
  check(`${view}: new block opens with the season off`, true);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${view}-3-new.png`, fullPage: true });
  check(`${view}: no page errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}
await browser.close();
done();
