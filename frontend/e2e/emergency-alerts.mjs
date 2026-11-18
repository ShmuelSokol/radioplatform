// Emergency Alerts page and the red banner, against a fake backend, desktop and phone.
//   npm run build && (npx vite preview --port 4173 &) && node e2e/emergency-alerts.mjs
import { createRequire } from 'node:module';

const { chromium } = createRequire(import.meta.url)('playwright');
import { allowEverywhere, guardBrowser, guardVerdict } from './lib/guard.mjs';

allowEverywhere(/ws\/site/, 'the fake backend has no release socket');
allowEverywhere(/EMERGENCY ALERT ON AIR/, 'the banner is the point of this test');
allowEverywhere(/11\.45|emergency\/activate/, 'the refused clip and its 422 are asserted below');

const BASE = process.env.BASE_URL || 'http://localhost:4173';
const SHOTS = process.env.SHOTS_DIR || '';
const JLM = 'st-jlm';
const onAir = { alert_id: 'al-1', since: 0, source: 'staff', kind: 'test', title: 'Test of the emergency override', until: null };
const status = (live) => ({
  device_api: false,
  stations: [
    { station_id: JLM, name: 'KBR Jerusalem', player: 'engine', box: false, on_air: live ? onAir : null },
    { station_id: 'st-main', name: "Kol B'ramah Main", player: 'engine', box: false, on_air: null },
    { station_id: 'st-stories', name: "Kol B'ramah - All Day Stories", player: 'browser', box: false, on_air: null },
  ],
});
const log = {
  total: 2,
  alerts: [
    { id: 'al-1', station_id: JLM, station_name: 'KBR Jerusalem', kind: 'test', source: 'staff', title: 'Test of the emergency override',
      requested_by: 'manager@example.com', status: 'on_air', has_tones: false, audio_url: null,
      requested_at: '2026-11-18T17:00:00Z', started_at: '2026-11-18T17:00:01Z', ended_at: null, seconds_on_air: null, error: null },
    { id: 'al-0', station_id: JLM, station_name: 'KBR Jerusalem', kind: 'rmt', source: 'line', title: "From the EAS box's audio line",
      requested_by: null, status: 'done', has_tones: true, audio_url: null,
      requested_at: '2026-11-17T15:10:00Z', started_at: '2026-11-17T15:10:00Z', ended_at: '2026-11-17T15:11:02Z', seconds_on_air: 62.4, error: null },
  ],
};

let failed = 0;
const check = (ok, name, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`); if (!ok) failed += 1; };

const browser = guardBrowser(await chromium.launch());
for (const [label, viewport, role] of [
  ['desktop', { width: 1280, height: 900 }, 'manager'],
  ['phone', { width: 390, height: 844 }, 'manager'],
  ['viewer', { width: 1280, height: 900 }, 'viewer'],
]) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  await ctx.addInitScript(() => { localStorage.setItem('access_token', 'test'); localStorage.setItem('refresh_token', 'test'); });
  const page = await ctx.newPage();
  let live = true;
  const posts = [];
  page.on('dialog', (d) => d.accept());
  await page.route('**/api/v1/**', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^.*\/api\/v1/, '');
    const json = (body, code = 200) => route.fulfill({ status: code, contentType: 'application/json', body: JSON.stringify(body) });
    if (req.method() === 'POST') {
      posts.push(path);
      if (path === '/emergency/al-1/stop') { live = false; return json({ ok: true, status: 'cancelled' }); }
      if (path === '/emergency/activate') {
        return json({ detail: 'This recording holds EAS tones (the attention signal or the SAME data bursts). Airing them outside a real alert or an authorised test breaks FCC rule 47 CFR 11.45.' }, 422);
      }
      return json({});
    }
    if (path === '/auth/me') return json({ id: 'u1', email: `${role}@example.com`, role, is_active: true });
    if (path === '/emergency/status') return json(status(live));
    if (path === '/emergency/active') return json({ on_air: live ? { [JLM]: onAir } : {} });
    if (path === '/emergency') return json(log);
    if (path === '/site-release') return json({ mode: 'auto' });
    if (path.startsWith('/alerts')) return json({ alerts: [], total: 0, unresolved_count: 0 });
    return json([]);
  });

  // The banner on another staff page
  await page.goto(`${BASE}/admin/alerts`);
  const banner = page.getByTestId('emergency-banner');
  await banner.waitFor({ timeout: 15000 });
  check((await banner.innerText()).includes('EMERGENCY ALERT ON AIR'), `${label}: red banner on another staff page`);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/emergency-banner-${label}.png` });

  await page.goto(`${BASE}/admin/emergency`);
  const onAirBox = page.getByTestId('emergency-on-air');
  await onAirBox.waitFor({ timeout: 15000 });
  const text = (await page.locator('main').innerText()).replace(/\s+/g, ' ');
  check(text.includes('EMERGENCY ALERT ON AIR · KBR Jerusalem'), `${label}: alert on air named with its station`);
  check(text.includes('Browser player') && text.includes('Stream server'), `${label}: how each station plays`);
  check(text.includes('Monthly test (RMT)') && text.includes('62.4 s') && text.includes('(EAS tones)'), `${label}: log rows`);
  check(text.includes('No key is set up yet'), `${label}: says the EAS box key is not set`);
  check(await page.getByTestId('emergency-banner').count() === 0, `${label}: no second banner on the page itself`);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check(!wide, `${label}: the page does not scroll sideways`);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/emergency-page-${label}.png`, fullPage: true });

  const stop = page.getByRole('button', { name: 'Stop alert' });
  const send = page.getByRole('button', { name: 'Send test now' });
  if (role === 'viewer') {
    check(await stop.count() === 0 && await send.count() === 0, `${label}: no Stop or test controls for a viewer`);
  } else {
    await stop.click();
    await page.waitForFunction(() => !document.querySelector('[data-testid="emergency-on-air"]'), null, { timeout: 8000 });
    check(posts.includes('/emergency/al-1/stop'), `${label}: Stop sends the stop`);
    check(await send.isDisabled(), `${label}: Send is off until a clip is chosen`);
    await page.getByLabel('Spoken clip').setInputFiles({ name: 'tones.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('ID3fake') });
    await send.click();
    const msg = page.getByRole('status');
    await msg.waitFor({ timeout: 8000 });
    check((await msg.innerText()).includes('11.45'), `${label}: a refused clip shows why`);
    const station = await page.locator('select').first().inputValue();
    check(station === JLM, `${label}: the test defaults to KBR Jerusalem`, station);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/emergency-refused-${label}.png`, fullPage: true });
  }
  await ctx.close();
}
await browser.close();
console.log(failed ? `${failed} failed` : 'all passed');
process.exit((failed ? 1 : 0) || (guardVerdict() ? 1 : 0));
