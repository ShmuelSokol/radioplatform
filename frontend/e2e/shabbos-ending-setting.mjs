/**
 * Stations page: "Last song before Shabbos" (2026-11-19). Against a fake backend: the
 * section is off with its pickers disabled; turning it on, picking the song and the item
 * at candle lighting from a library search and saving sends shabbos_ending with both ids
 * and titles and keeps other settings; it shows the saved choice when the page opens
 * again; turning it off saves enabled false. Desktop and iPhone sizes, SHOTS=<dir> for
 * screenshots.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4313 --strictPort &)
 *   BASE_URL=http://localhost:4313 node e2e/shabbos-ending-setting.mjs
 */
import { chromium, devices, checker, makeStation, adminContext, newPage, routeSiteSocket } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4313';
const SHOTS = process.env.SHOTS || '';
const { check, done } = checker({});
const SONG = { id: 'song-gs', title: 'Time to Say Good Shabbos', artist: 'Lev Tahor', duration: 279.48, asset_type: 'music' };
const SHOFAR = { id: 'shofar-1', title: 'Shofar blowing', artist: null, duration: 20, asset_type: 'jingle' };
const CANDLE = { id: 'candle-1', title: 'Candle lighting 4:13', artist: null, duration: 300, asset_type: 'jingle' };
// The coming Friday on this device's calendar (the page keys the week's announcement by it)
const FRIDAY = (() => {
  const t = new Date(); const d = new Date(t.getFullYear(), t.getMonth(), t.getDate());
  d.setDate(d.getDate() + ((5 - d.getDay() + 7) % 7));
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
})();

const browser = await chromium.launch();
for (const [view, opts] of [['desktop', { viewport: { width: 1440, height: 900 } }], ['iphone', { ...devices['iPhone 13'] }]]) {
  const saved = [];
  const st = {
    id: 'st-1', name: "KBR Jerusalem", timezone: 'Asia/Jerusalem', is_active: true, channels: [],
    automation_config: { weather_region: 'jerusalem', crossfade_seconds: 1 },
  };
  const extra = (method, path, query, body) => {
    if (method === 'PUT' && /^\/stations\/[^/]+$/.test(path)) { saved.push(body); st.automation_config = body.automation_config; }
    if (method === 'GET' && path === '/assets' && query.search) {
      const t = query.search.toLowerCase();
      const list = [SONG, SHOFAR, CANDLE].filter((a) => a.title.toLowerCase().includes(t));
      return { assets: list, total: list.length };
    }
    return undefined;
  };
  const station = makeStation({ queueSize: 3, extra, stations: [st] });
  const { ctx } = await adminContext(browser, opts, station);
  await routeSiteSocket(ctx);
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/stations`);
  await page.getByText(st.name).first().click();
  const on = page.getByTestId('shabbos-ending-on');
  await on.waitFor({ timeout: 8000 });
  check(`${view}: off at first`, !(await on.isChecked()));
  check(`${view}: song search disabled while off`, await page.getByTestId('shabbos-ending-song-search').isDisabled());
  await on.check();
  await page.getByTestId('shabbos-ending-song-search').fill('good shab');
  await page.getByTestId('shabbos-ending-song-results').getByRole('button', { name: /Time to Say Good Shabbos/ }).click();
  check(`${view}: song chosen`, (await page.getByTestId('shabbos-ending-song-chosen').innerText()).includes('Good Shabbos'));
  await page.getByTestId('shabbos-ending-after-search').fill('shofar');
  await page.getByTestId('shabbos-ending-after-results').getByRole('button', { name: /Shofar blowing/ }).click();
  check(`${view}: candle lighting picker names the coming Friday`,
    (await page.getByTestId('shabbos-ending').innerText()).includes(`Candle lighting announcement for Friday ${FRIDAY}`));
  await page.getByTestId('shabbos-ending-candle-search').fill('candle');
  await page.getByTestId('shabbos-ending-candle-results').getByRole('button', { name: /Candle lighting 4:13/ }).click();
  check(`${view}: Erev Shabbos on, Yom Tov off by default`, await page.getByTestId('shabbos-ending-shabbos').isChecked()
    && !(await page.getByTestId('shabbos-ending-yomtov').isChecked()));
  await page.getByTestId('shabbos-ending').scrollIntoViewIfNeeded();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${view}-shabbos-ending.png` });
  await page.getByRole('button', { name: 'Save Automation Config' }).click();
  await page.waitForTimeout(600);
  const body = saved.at(-1)?.automation_config;
  const e = body?.shabbos_ending;
  check(`${view}: saves the setting`, e?.enabled === true && e?.song_asset_id === SONG.id && e?.after_asset_id === SHOFAR.id
    && e?.song_title === SONG.title && e?.shabbos === true && e?.yom_tov === false, JSON.stringify(e));
  check(`${view}: saves this week's candle lighting item`, e?.candle_items?.[FRIDAY] === CANDLE.id
    && e?.candle_titles?.[FRIDAY] === CANDLE.title && Object.keys(e?.candle_items ?? {}).length === 1, JSON.stringify(e));
  check(`${view}: keeps other settings`, body?.weather_region === 'jerusalem');

  // Opened again: shows what was saved
  await page.reload();
  await page.getByText(st.name).first().click();
  await page.getByTestId('shabbos-ending-on').waitFor({ timeout: 8000 });
  check(`${view}: shows it on after reopening`, await page.getByTestId('shabbos-ending-on').isChecked());
  check(`${view}: shows the chosen song`, (await page.getByTestId('shabbos-ending-song-chosen').innerText()).includes('Good Shabbos'));
  check(`${view}: shows the chosen item`, (await page.getByTestId('shabbos-ending-after-chosen').innerText()).includes('Shofar'));
  check(`${view}: shows this week's candle lighting item`,
    (await page.getByTestId('shabbos-ending-candle-chosen').innerText()).includes('Candle lighting 4:13'));
  // "None this week" takes it off: the week ends with the song
  await page.getByTestId('shabbos-ending-candle').getByRole('button', { name: 'None this week' }).click();
  await page.getByRole('button', { name: 'Save Automation Config' }).click();
  await page.waitForTimeout(600);
  check(`${view}: none this week saves no item`,
    Object.keys(saved.at(-1)?.automation_config?.shabbos_ending?.candle_items ?? {}).length === 0);
  await page.getByTestId('shabbos-ending-on').uncheck();
  await page.getByRole('button', { name: 'Save Automation Config' }).click();
  await page.waitForTimeout(600);
  check(`${view}: off saves enabled false`, saved.at(-1)?.automation_config?.shabbos_ending?.enabled === false);
  check(`${view}: no page errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}
await browser.close();
done();
