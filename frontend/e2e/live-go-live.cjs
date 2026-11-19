/**
 * Going on air from the Host Console, as a person does it (the first real show,
 * 2026-11-18): one GO LIVE press, a banner that always says whether listeners hear
 * you, a level meter, a mic check, hear-yourself, and a caller who has just called
 * shown on the host's own screen.
 * Needs a real backend and the site running against it (see wiki/live-shows.md):
 *   KBR_ADMIN_EMAIL=admin KBR_ADMIN_PASSWORD=... node e2e/live-go-live.cjs <station id>
 * API_URL / BASE_URL default to :8000 and :3000; SHOTS_DIR keeps screenshots.
 */
const { chromium, devices } = require('playwright');
const fs = require('fs'), os = require('os'), path = require('path');
const API = process.env.API_URL || 'http://localhost:8000/api/v1', SITE = process.env.BASE_URL || 'http://localhost:3000';
const STATION = process.argv[2];
const SHOTS = process.env.SHOTS_DIR;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, d = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d !== '' ? '  (' + d + ')' : ''}`); if (!ok) fails++; };

/** A wav the fake microphone plays: a steady 440 Hz tone at a speaking level */
function toneWav(file, seconds = 30, amp = 6000) {
  const rate = 48000, n = rate * seconds, data = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) data.writeInt16LE(Math.round(amp * Math.sin(2 * Math.PI * 440 * i / rate)), i * 2);
  const h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + data.length, 4); h.write('WAVEfmt ', 8); h.writeUInt32LE(16, 16);
  h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22); h.writeUInt32LE(rate, 24); h.writeUInt32LE(rate * 2, 28);
  h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write('data', 36); h.writeUInt32LE(data.length, 40);
  fs.writeFileSync(file, Buffer.concat([h, data]));
}

(async () => {
  if (!STATION) { console.error('usage: node e2e/live-go-live.cjs <station id>'); process.exit(2); }
  const token = (await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: process.env.KBR_ADMIN_EMAIL || 'admin', password: process.env.KBR_ADMIN_PASSWORD }) })).json()).access_token;
  const H = { authorization: `Bearer ${token}`, 'content-type': 'application/json' };
  const api = async (method, url, body) => { const r = await fetch(`${API}${url}`, { method, headers: H, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, json: await r.json().catch(() => null) }; };
  const station = (await api('GET', `/stations/${STATION}`)).json;
  const before = station.automation_config || {};
  const setConfig = (extra) => api('PUT', `/stations/${STATION}`, { automation_config: { ...before, ...extra } });
  const stationNow = async () => (await api('GET', `/stations/${STATION}`)).json.automation_config || {};
  const showNow = async (id) => (await api('GET', `/live-shows/${id}`)).json;
  for (const s of ((await api('GET', '/live-shows?limit=100')).json.shows || [])) if (s.status === 'live' && s.station_id === STATION) await api('POST', `/live-shows/${s.id}/end`);
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'golive-'));
  toneWav(path.join(dir, 'host.wav'));
  const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${path.join(dir, 'host.wav')}`, '--autoplay-policy=no-user-gesture-required'] });
  const problems = [];
  const open = async (url, opts = {}) => {
    const page = await (await browser.newContext({ permissions: ['microphone'], ...opts })).newPage();
    await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
    page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|dynamically imported module|MIME type/.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
    page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
    page.on('dialog', (d) => d.accept().catch(() => {}));
    await page.goto(`${SITE}${url}`);
    return page;
  };
  const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
  const T = (page, id) => page.getByTestId(id);
  const state = (page) => T(page, 'air-banner').getAttribute('data-state');
  try {
    for (const [label, opts, delay] of [['desktop', { viewport: { width: 1440, height: 900 } }, 4], ['phone', { ...devices['iPhone 13'] }, 0]]) {
      await setConfig({ live_delay_seconds: delay, live_dump_seconds: 0 });
      const now = new Date();
      const show = (await api('POST', '/live-shows', { station_id: STATION, title: `Go live ${label}`, scheduled_start: now.toISOString(), scheduled_end: new Date(+now + 3600e3).toISOString(), calls_enabled: true })).json;
      const P = `${label}: `;

      // The list: the show opens the studio; nothing starts by itself
      const list = await open('/admin/live', opts);
      await T(list, 'open-studio').first().waitFor({ timeout: 15000 });
      check(P + 'G1 the list offers "Open studio", not a button that starts the show unseen', await list.getByRole('button', { name: 'Go Live' }).count() === 0);
      await list.close();

      const host = await open(`/admin/live/${show.id}/host`, opts);
      await T(host, 'air-banner').waitFor({ timeout: 15000 });
      check(P + 'G2 opening the studio: NOT ON AIR, microphone off', await state(host) === 'off' && /NOT ON AIR/.test(await T(host, 'air-banner').innerText()), await T(host, 'air-banner').innerText());
      check(P + 'G3 the show has not started and the station is untouched', (await showNow(show.id)).status === 'scheduled' && !(await stationNow()).live_show_id);
      await shot(host, `${label}-1-off`);

      await T(host, 'mic-on-button').click();
      await T(host, 'go-live-button').waitFor({ timeout: 8000 });
      check(P + 'G4 microphone on: still NOT ON AIR, and the banner says so', await state(host) === 'ready' && /nobody hears you/.test(await T(host, 'air-banner').innerText()));
      await sleep(1200);
      const level = parseFloat(await T(host, 'level-meter').getAttribute('data-level'));
      check(P + 'G5 the meter moves with the voice and says the level is good', level > -45 && level < -3 && /Good level|Quiet|Loud/.test(await T(host, 'level-word').innerText()), `${level} dB, "${await T(host, 'level-word').innerText()}"`);
      check(P + 'G6 with the microphone on the show is still not started', (await showNow(show.id)).status === 'scheduled' && !(await stationNow()).live_show_id);

      await T(host, 'mic-check-button').click();
      await host.getByText(/Recording… talk for 5 s/).waitFor({ timeout: 2000 });
      const playing = await host.getByText('Playing it back…').waitFor({ timeout: 8000 }).then(() => true, () => false);
      const back = await host.getByText(/record 5 s and hear how you sound/).waitFor({ timeout: 9000 }).then(() => true, () => false);
      check(P + 'G7 mic check records 5 s, plays it back, and is ready again', playing && back);
      check(P + 'G8 the mic check sent nothing on air', (await showNow(show.id)).status === 'scheduled');

      await T(host, 'monitor-toggle').locator('input').check();
      check(P + 'G9 hear-myself is remembered', await host.evaluate(() => localStorage.getItem('kbr_host_monitor')) === '1');
      await shot(host, `${label}-2-ready`);

      await T(host, 'go-live-button').click();
      await host.waitForFunction(() => ['soon', 'on', 'connecting'].includes(document.querySelector('[data-testid="air-banner"]')?.getAttribute('data-state')), null, { timeout: 5000 });
      if (delay > 0) {
        const soon = await host.waitForFunction(() => document.querySelector('[data-testid="air-banner"]')?.getAttribute('data-state') === 'soon', null, { timeout: 4000 }).then(() => true, () => false);
        check(P + 'G10 GO LIVE with a delay: "GOING ON AIR: listeners hear you in N s"', soon && /GOING ON AIR: listeners hear you in \d+ s/.test(await T(host, 'air-banner').innerText()), await T(host, 'air-banner').innerText());
        check(P + 'G11 the music plays on while the delay fills', !(await stationNow()).live_show_id);
        await shot(host, `${label}-3-soon`);
      }
      await host.waitForFunction(() => document.querySelector('[data-testid="air-banner"]')?.getAttribute('data-state') === 'on', null, { timeout: 12000 });
      check(P + 'G12 then ON AIR, in red, and the show is live on the station', /ON AIR: listeners hear you/.test(await T(host, 'air-banner').innerText()) && (await showNow(show.id)).status === 'live');
      // The station record is written just after the first sound (one commit): up to 3 s on prod
      for (let i = 0; i < 10 && (await stationNow()).live_show_id !== show.id; i++) await sleep(300);
      check(P + 'G13 the station is the show\'s once the host is heard', (await stationNow()).live_show_id === show.id);
      check(P + 'G14 no GO LIVE or mic check while on air', await T(host, 'go-live-button').count() === 0 && await T(host, 'mic-check-button').count() === 0);
      await shot(host, `${label}-4-on-air`);

      // A listener calls: the host sees them without the screener's page
      const call = await fetch(`${API}/call-in/${STATION}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: 'Shmuel', note: 'Pizza' }) });
      check(P + 'G15 a call is taken', call.status === 201, String(call.status));
      const seen = await T(host, 'waiting-caller').first().waitFor({ timeout: 8000 }).then(() => true, () => false);
      check(P + 'G16 the caller shows on the host\'s screen as "Calling now", with what it means', seen && /You do not hear them/.test(await T(host, 'waiting-callers').innerText()), seen ? (await T(host, 'waiting-callers').innerText()).replace(/\n/g, ' | ').slice(0, 120) : '');
      await shot(host, `${label}-5-caller`);
      if (seen) {
        await T(host, 'waiting-put-on-air').first().click();
        const aired = await T(host, 'on-air-card').first().waitFor({ timeout: 8000 }).then(() => true, () => false);
        check(P + 'G17 Put On Air from the host\'s screen puts the caller on air', aired);
      }

      await host.getByRole('button', { name: 'End Show' }).click();
      await host.waitForURL(/\/admin\/live$/, { timeout: 8000 }).catch(() => {});
      await sleep(delay * 1000 + 4000);
      check(P + 'G18 End Show ends the show and gives the station back', (await showNow(show.id)).status === 'ended' && !(await stationNow()).live_show_id);
      await host.close();
    }
    const active = await (await fetch(`${API}/live-shows/station/${STATION}/active`)).json();
    check('G19 the show lookup carries the station\'s stream address field (for a page opened during a show)', 'stream_url' in active, JSON.stringify(active).slice(0, 80));
    check('G20 no console errors', problems.length === 0, problems.slice(0, 3).join(' ; '));
  } finally {
    await api('PUT', `/stations/${STATION}`, { automation_config: before });
    await browser.close();
  }
  console.log(fails ? `\n${fails} FAILED` : '\nall passed');
  process.exit(fails ? 1 : 0);
})();
