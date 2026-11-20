/**
 * Stations page: "Station IDs" (2026-11-18; where they play added 2026-11-20). Against a fake backend:
 * the section shows the saved setting (on, minutes, category, hours), the fields
 * are disabled while it is off, and Save sends station_ids with the hours and keeps
 * other settings. Desktop and iPhone sizes, a screenshot of each when SHOTS=<dir>.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4313 --strictPort &)
 *   BASE_URL=http://localhost:4313 node e2e/station-ids-setting.mjs
 */
import { chromium, devices, checker, makeStation, adminContext, newPage, routeSiteSocket } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4313';
const SHOTS = process.env.SHOTS || '';
const { check, done } = checker({});

const browser = await chromium.launch();
for (const [view, opts] of [['desktop', { viewport: { width: 1440, height: 900 } }], ['iphone', { ...devices['iPhone 13'] }]]) {
  const saved = [];
  const extra = (method, path, _q, body) => {
    if (method === 'PUT' && /^\/stations\/[^/]+$/.test(path)) { saved.push(body); }
    return undefined;
  };
  const st = {
    id: 'st-1', name: "Kol B'ramah Main", timezone: 'America/New_York', is_active: true, channels: [],
    automation_config: {
      weather_region: 'lakewood',
      station_ids: { enabled: true, min_minutes: 15, max_minutes: 25, category: 'station_id', start_hour: 6, end_hour: 22, clear_minutes: 3 },
    },
  };
  const station = makeStation({ queueSize: 3, extra, stations: [st] });
  const { ctx } = await adminContext(browser, opts, station);
  await routeSiteSocket(ctx);
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/stations`);
  await page.getByText(st.name).first().click();
  const on = page.getByTestId('station-ids-on');
  await on.waitFor({ timeout: 8000 });
  check(`${view}: shows it on`, await on.isChecked());
  check(`${view}: shows the minutes`, (await page.getByTestId('station-ids-min').inputValue()) === '15'
    && (await page.getByTestId('station-ids-max').inputValue()) === '25');
  check(`${view}: once an hour by default, minutes only for between songs`,
    (await page.getByTestId('station-ids-where').inputValue()) === 'hourly'
    && await page.getByTestId('station-ids-min').isDisabled());
  check(`${view}: shows the hours`, (await page.getByTestId('station-ids-from').inputValue()) === '6'
    && (await page.getByTestId('station-ids-to').inputValue()) === '22');
  await page.getByTestId('station-ids-on').scrollIntoViewIfNeeded();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${view}-station-ids.png` });
  await page.getByTestId('station-ids-where').selectOption('every_block');
  check(`${view}: every break keeps the minutes off`, await page.getByTestId('station-ids-max').isDisabled());
  await page.getByTestId('station-ids-where').selectOption('between_songs');
  await page.getByTestId('station-ids-max').fill('30');
  await page.getByTestId('station-ids-from').selectOption('7');
  await page.getByRole('button', { name: 'Save Automation Config' }).click();
  await page.waitForTimeout(600);
  const body = saved.at(-1)?.automation_config;
  check(`${view}: saves the new setting`, body?.station_ids?.max_minutes === 30 && body?.station_ids?.start_hour === 7
    && body?.station_ids?.end_hour === 22 && body?.station_ids?.enabled === true && body?.station_ids?.clear_minutes === 3
    && body?.station_ids?.placement === 'between_songs', JSON.stringify(body?.station_ids));
  check(`${view}: keeps other settings`, body?.weather_region === 'lakewood');
  await on.uncheck();
  check(`${view}: fields off while it is off`, await page.getByTestId('station-ids-min').isDisabled());
  check(`${view}: no page errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}
await browser.close();
done();
