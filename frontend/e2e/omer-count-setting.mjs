/**
 * Stations page: "Omer count on the hour" (2026-11-19). Against a fake backend: the
 * section shows the saved setting, how many recordings are uploaded and the dates of the
 * count, what the station says each hour of a chosen day (silent hours struck through,
 * hours not recorded marked), and Save sends omer_count and keeps other settings.
 * Desktop and iPhone sizes, a screenshot of each when SHOTS=<dir>.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4313 --strictPort &)
 *   BASE_URL=http://localhost:4313 node e2e/omer-count-setting.mjs
 */
import { chromium, devices, checker, makeStation, adminContext, newPage, routeSiteSocket } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4313';
const SHOTS = process.env.SHOTS || '';
const { check, done } = checker({});

const hoursFor = (day) => Array.from({ length: 24 }, (_, h) => {
  const n = h >= 20 ? 9 : 8;
  const text = `Today is the ${n === 9 ? 'ninth' : 'eighth'} day, which is one week and ${n === 9 ? 'two days' : 'one day'} of the Omer.`;
  return {
    at: `${day}T${String((h + 4) % 24).padStart(2, '0')}:00:00Z`,
    local: `${h % 12 === 0 ? 12 : h % 12}:00 ${h < 12 ? 'AM' : 'PM'}`,
    omer_day: n, text,
    recorded: h !== 13,
    off_air: h >= 20 ? 'Shabbos Apr 30, 2027' : null,
  };
});

const browser = await chromium.launch();
for (const [view, opts] of [['desktop', { viewport: { width: 1440, height: 900 } }], ['iphone', { ...devices['iPhone 13'] }]]) {
  const saved = [];
  const asked = [];
  const extra = (method, path, query, body) => {
    if (method === 'PUT' && /^\/stations\/[^/]+$/.test(path)) { saved.push(body); }
    if (method === 'GET' && path === '/omer-count') {
      return { first_day: '2027-04-23', last_day: '2027-06-10', recorded: 48, total: 49, clips: [] };
    }
    if (method === 'GET' && path === '/omer-count/station/st-1') {
      const day = query?.get ? query.get('on') : query?.on;
      asked.push(day);
      return { station_id: 'st-1', enabled: true, switch_minutes: 0, place_known: true, date: day,
        shekia: '7:49 PM', switch_at: '7:49 PM', hours: hoursFor(day) };
    }
    return undefined;
  };
  const st = {
    id: 'st-1', name: "Kol B'ramah Main", timezone: 'America/New_York', is_active: true, channels: [],
    automation_config: { weather_region: 'lakewood', omer_count: { enabled: true, switch_minutes: 0 } },
  };
  const station = makeStation({ queueSize: 3, extra, stations: [st] });
  const { ctx } = await adminContext(browser, opts, station);
  await routeSiteSocket(ctx);
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/stations`);
  await page.getByText(st.name).first().click();
  const on = page.getByTestId('omer-count-on');
  await on.waitFor({ timeout: 8000 });
  check(`${view}: shows it on`, await on.isChecked());
  check(`${view}: switch at shekia`, (await page.getByTestId('omer-count-switch').inputValue()) === '0');
  const status = page.getByTestId('omer-count-status');
  await status.getByText('48 of 49').waitFor({ timeout: 5000 });
  const statusText = await status.innerText();
  check(`${view}: says how many are recorded and when`, /48 of 49/.test(statusText) && /2027/.test(statusText) && /Erev Shavuos/.test(statusText), statusText);
  const list = page.getByTestId('omer-count-hours');
  await list.waitFor({ timeout: 5000 });
  check(`${view}: opens on the first day of the count`, (await page.getByTestId('omer-count-day').inputValue()) === '2027-04-23' && asked.includes('2027-04-23'));
  const rows = await list.locator('li').allInnerTexts();
  check(`${view}: hours saying the same in one line`, rows.length === 4, rows.join(' || '));
  check(`${view}: the day until shekia`, rows[0].startsWith('12:00 AM to 12:00 PM') && rows[0].includes('Today is the eighth day'), rows[0]);
  check(`${view}: missing recording marked`, rows[1].startsWith('1:00 PM') && !rows[1].includes(' to ') && rows[1].includes('Not recorded yet'), rows[1]);
  check(`${view}: after shekia the next day, struck as silent`, rows[3].startsWith('8:00 PM to 11:00 PM') && rows[3].includes('Today is the ninth day') && rows[3].includes('Silent: Shabbos'), rows[3]);
  check(`${view}: never says night`, !rows.join(' ').toLowerCase().includes('night'));
  check(`${view}: shows shekia`, (await page.getByTestId('omer-count').innerText()).includes('Shekia 7:49 PM'));
  await page.getByTestId('omer-count-day').fill('2027-05-24');
  await page.waitForTimeout(400);
  check(`${view}: another day asks again`, asked.includes('2027-05-24'));
  const box = await page.getByTestId('omer-count').boundingBox();
  const vw = page.viewportSize().width;
  check(`${view}: fits the screen width`, box && box.x >= 0 && box.x + box.width <= vw + 1, JSON.stringify(box));
  await page.getByTestId('omer-count').scrollIntoViewIfNeeded();
  if (SHOTS) await page.getByTestId('omer-count').screenshot({ path: `${SHOTS}/${view}-omer-count.png` });
  await page.getByTestId('omer-count-switch').fill('50');
  await page.getByRole('button', { name: 'Save Automation Config' }).click();
  await page.waitForTimeout(600);
  const body = saved.at(-1)?.automation_config;
  check(`${view}: saves the new setting`, body?.omer_count?.enabled === true && body?.omer_count?.switch_minutes === 50, JSON.stringify(body?.omer_count));
  check(`${view}: keeps other settings`, body?.weather_region === 'lakewood');
  await on.uncheck();
  check(`${view}: minutes off while it is off`, await page.getByTestId('omer-count-switch').isDisabled());
  await page.getByRole('button', { name: 'Save Automation Config' }).click();
  await page.waitForTimeout(600);
  check(`${view}: saves it off`, saved.at(-1)?.automation_config?.omer_count?.enabled === false);
  check(`${view}: no page errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}
await browser.close();
process.exit(done());
