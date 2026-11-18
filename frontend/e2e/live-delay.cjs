/**
 * The broadcast delay of a live show, end to end: Dump, build-back, exit, delay length.
 * Part A is a host "microphone" sent from this script, so the test decides what is
 * said and when: a 1000 Hz "curse" is said, Dump is pressed, and no listener may
 * ever receive it. Part B presses every control of the delay panel on the Host
 * Console and the Call Screener in a browser with a fake microphone and reads what
 * the screen says, on a desktop and a phone size. Part C is the station's settings.
 * Part D (ONLY=desk): Cough and Mute, the dump log and its recording, Put On Air while
 * the delay is not safe, and with PLAYOUT_TOKEN the engine's copy of the show (live.mp3).
 * Part G (ONLY=record): the show is recorded and appears in Archives as a draft.
 * Part F (ONLY=callers): the caller desk (the caller's topic and notes, Hold, Next caller).
 * Part H (ONLY=carts): carts on the Host Console (jingles played into the show).
 * Part I (ONLY=talk): talkback, the screener or host talking to a caller off the air.
 * Part J (ONLY=restart, with BACKEND_RESTART=<command>): the server restarts during a show.
 * Part K (ONLY=multi): several callers on air together (up to four).
 * Part L (ONLY=chat): the staff chat between the host and the screener.
 * Part M (ONLY=history): repeat, priority and blocked callers.
 * Part N (ONLY=lock): a locked call is left alone by End Call, Hold and Next.
 * Part O (ONLY=cohost): a co-host joins from another place.
 * Part P (ONLY=filler): a filler plays after a Dump while the delay refills.
 * Part E (ONLY=gone): the control room badge (no host, filling, live) and the station
 * going back to music when the host is gone.
 * Needs a real backend and the site running against it (see wiki/live-shows.md):
 *   KBR_ADMIN_EMAIL=admin KBR_ADMIN_PASSWORD=... node e2e/live-delay.cjs <station id>
 * API_URL / BASE_URL default to :8000 and :3000; SHOTS_DIR keeps screenshots.
 * The station's live settings are changed for the run and put back at the end.
 */
const { chromium, devices } = require('playwright');
const fs = require('fs'), os = require('os'), path = require('path');

// The host goes on air: microphone on (nothing goes out yet), then the one GO LIVE press
async function goLive(page) {
  await page.getByTestId('mic-on-button').click();
  await page.getByTestId('go-live-button').click();
}
const WebSocket = globalThis.WebSocket;
const API = process.env.API_URL || 'http://localhost:8000/api/v1', SITE = process.env.BASE_URL || 'http://localhost:3000';
const STATION = process.argv[2];
const SHOTS = process.env.SHOTS_DIR;
const ONLY = process.env.ONLY;
const RATE = 24000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, d = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d !== '' ? '  (' + d + ')' : ''}`); if (!ok) fails++; };
function power(samples, f, rate = RATE) { // Goertzel
  const k = 2 * Math.cos(2 * Math.PI * f / rate); let s1 = 0, s2 = 0;
  for (const x of samples) { const s0 = x / 32768 + k * s1 - s2; s2 = s1; s1 = s0; }
  return Math.sqrt(Math.max(0, s1 * s1 + s2 * s2 - k * s1 * s2)) / Math.max(1, samples.length);
}
const tone = (f, n, phase) => { const b = new Int16Array(n); for (let i = 0; i < n; i++) b[i] = Math.round(8000 * Math.sin(2 * Math.PI * f * (phase + i) / RATE)); return b; };

/** A listener page's socket: every frame with the time it arrived */
function listener() {
  const l = { frames: [], states: [], ws: new WebSocket(`${API.replace(/^http/, 'ws')}/ws/live/station/${STATION}/listen`) };
  l.ws.binaryType = 'arraybuffer';
  l.ws.onmessage = (e) => {
    if (typeof e.data === 'string') { const m = JSON.parse(e.data); if (m.type === 'live_state') l.states.push({ live: m.live, at: Date.now() }); return; }
    l.frames.push({ at: Date.now(), pcm: new Int16Array(e.data) });
  };
  l.since = (t) => l.frames.filter((f) => f.at >= t).flatMap((f) => Array.from(f.pcm));
  l.live = () => (l.states.length ? l.states[l.states.length - 1].live : false);
  return l;
}

/** A host microphone driven by the script: say(f) sets the tone being "said" */
function hostMic(showId, token) {
  const h = { f: 440, sent: 0, ws: new WebSocket(`${API.replace(/^http/, 'ws')}/ws/live/${showId}/audio`), back: [] };
  h.ws.binaryType = 'arraybuffer';
  h.ready = new Promise((res) => { h.ws.onopen = () => { h.ws.send(JSON.stringify({ type: 'auth', token })); res(); }; });
  const t0 = Date.now();
  h.timer = setInterval(() => {
    if (h.ws.readyState !== 1) return;
    const due = Math.floor((Date.now() - t0) / 1000 * RATE) - h.sent;
    if (due <= 0) return;
    h.ws.send(h.f ? tone(h.f, due, h.sent).buffer : new Int16Array(due).buffer);
    h.sent += due;
  }, 40);
  h.say = (f) => { h.f = f; };
  h.close = () => { clearInterval(h.timer); h.ws.close(); };
  return h;
}

(async () => {
  if (!STATION) { console.error('usage: node e2e/live-delay.cjs <station id>'); process.exit(2); }
  const token = (await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: process.env.KBR_ADMIN_EMAIL || 'admin', password: process.env.KBR_ADMIN_PASSWORD }) })).json()).access_token;
  const H = { authorization: `Bearer ${token}`, 'content-type': 'application/json' };
  const api = async (method, url, body) => { const r = await fetch(`${API}${url}`, { method, headers: H, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, json: await r.json().catch(() => null) }; };
  const station = (await api('GET', `/stations/${STATION}`)).json;
  const before = station.automation_config || {};
  const setConfig = (extra) => api('PUT', `/stations/${STATION}`, { automation_config: { ...before, ...extra } });
  const endAll = async () => { for (const s of ((await api('GET', '/live-shows?limit=100')).json.shows || [])) if (s.status === 'live' && s.station_id === STATION) await api('POST', `/live-shows/${s.id}/end`); };
  const newShow = async (title, minutes = 60) => { const now = new Date(); return (await api('POST', '/live-shows', { station_id: STATION, title, scheduled_start: now.toISOString(), scheduled_end: new Date(+now + minutes * 60e3).toISOString(), calls_enabled: true })).json; };
  const air = async (id) => (await api('GET', `/live-shows/${id}/air`)).json;
  await endAll();
  await sleep(500);

  try {
    // ---------- Part A: what a listener receives ----------
    if (!ONLY || ONLY === 'audio') {
      await setConfig({ live_delay_seconds: 6, live_dump_seconds: 0 });
      const show = await newShow('Delay test A');
      const l = listener();
      await sleep(300);
      const mic = hostMic(show.id, token);
      await mic.ready;
      const t0 = Date.now();
      await sleep(3000);
      let a = await air(show.id);
      check('A1 while the delay fills nothing reaches listeners', l.frames.length === 0 && !l.live(), `${l.frames.length} frames, mode ${a.mode}`);
      check('A2 the console is told it is filling', a.mode === 'pre-roll' && a.delay > 2 && a.delay < 4, `${a.mode} ${a.delay}`);
      await sleep(4500);
      const firstAt = l.frames.length ? l.frames[0].at - t0 : -1;
      check('A3 listeners hear the host one delay later', l.live() && firstAt > 5500 && firstAt < 7200, `first audio after ${firstAt} ms, delay 6000`);
      a = await air(show.id);
      check('A4 the delay is full and safe', a.mode === 'steady' && a.safe && Math.abs(a.delay - 6) < 0.5, `${a.mode} ${a.delay}`);
      check('A5 the station is the show\'s once it is heard', ((await api('GET', `/stations/${STATION}`)).json.automation_config || {}).live_show_id === show.id);

      // The "curse": 2 s of 1000 Hz. Dump is pressed 1.5 s after it ends.
      const curseAt = Date.now();
      mic.say(1000);
      await sleep(2000);
      mic.say(440);
      await sleep(1500);
      const d = await api('POST', `/live-shows/${show.id}/dump`);
      check('A6 Dump answers with what it cut', d.status === 200 && d.json.dumped_seconds > 5 && d.json.air.dumps === 1, JSON.stringify(d.json && { cut: d.json.dumped_seconds, left: d.json.air.delay }));
      await sleep(12000);
      const after = l.since(curseAt);
      const p1000 = power(after, 1000), p440 = power(after, 440);
      check('A7 the dumped words never reach a listener', p1000 < 0.002 && p440 > 0.02, `1000 Hz ${p1000.toFixed(4)}, 440 Hz ${p440.toFixed(4)} over ${(after.length / RATE).toFixed(1)} s`);
      // The same tone left alone does arrive (the check above can fail)
      const t2 = Date.now();
      mic.say(1000); await sleep(1000); mic.say(440);
      await sleep(9000);
      check('A8 without a Dump the same words do air', power(l.since(t2), 1000) > 0.005, power(l.since(t2), 1000).toFixed(4));
      a = await air(show.id);
      check('A9 the delay builds back by itself (3 % a second under non-stop talk)', a.delay > 0.5 && (a.mode === 'building' || a.mode === 'steady'), `${a.mode} ${a.delay} s, ${((Date.now() - curseAt) / 1000).toFixed(0)} s after the Dump`);

      // Exit: winds down to really live
      let r = await api('POST', `/live-shows/${show.id}/delay`, { action: 'exit' });
      check('A10 Exit delay is accepted', r.status === 200 && r.json.target === 0);
      mic.say(0); // a pause: the delay closes quickly
      await sleep(5000);
      mic.say(440);
      a = await air(show.id);
      check('A11 after exiting there is no delay', a.mode === 'no delay' && a.delay < 0.4 && !a.safe, `${a.mode} ${a.delay}`);
      const t3 = Date.now();
      mic.say(1000); await sleep(600); mic.say(440);
      await sleep(900);
      check('A12 with no delay listeners hear the host at once', power(l.since(t3), 1000) > 0.005, power(l.since(t3), 1000).toFixed(4));
      r = await api('POST', `/live-shows/${show.id}/dump`);
      check('A13 Dump with no delay says nothing can be cut', r.status === 409 && /already heard|Nothing is waiting/.test(r.json.detail), `${r.status} ${JSON.stringify(r.json)}`);

      // Build to 4 s, then end the show: the last words air before the music returns
      r = await api('POST', `/live-shows/${show.id}/delay`, { action: 'build', seconds: 4 });
      check('A14 Build delay to a chosen length', r.status === 200 && r.json.target === 4 && r.json.configured === 4);
      mic.say(0); await sleep(9000);
      a = await air(show.id);
      check('A15 the delay built to the chosen length', Math.abs(a.delay - 4) < 0.6, `${a.delay}`);
      mic.say(1500);
      await sleep(1500);
      const endAt = Date.now();
      mic.close();
      await api('POST', `/live-shows/${show.id}/end`);
      await sleep(300);
      check('A16 End show: listeners still on the show while the delay plays out', l.live());
      await sleep(6000);
      const offAt = l.states.filter((s) => !s.live).pop();
      check('A17 the host\'s last words aired, then the show went off', !l.live() && power(l.since(endAt), 1500) > 0.005 && offAt && offAt.at - endAt > 1000, `off air ${offAt ? offAt.at - endAt : '?'} ms after End, 1500 Hz ${power(l.since(endAt), 1500).toFixed(4)}`);
      await sleep(600);
      check('A18 the station is handed back', !((await api('GET', `/stations/${STATION}`)).json.automation_config || {}).live_show_id);
      l.ws.close();
    }

    // ---------- Part B: every control, in a browser ----------
    if (!ONLY || ONLY === 'screen') {
      await setConfig({ live_delay_seconds: 5, live_dump_seconds: 0 });
      const DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'delay-'));
      { const rate = 48000, n = rate * 30, buf = Buffer.alloc(44 + n * 2);
        buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8);
        buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
        buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32);
        buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
        for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 440 * i / rate)), 44 + i * 2);
        fs.writeFileSync(path.join(DIR, 'host.wav'), buf); }
      const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${path.join(DIR, 'host.wav')}`, '--autoplay-policy=no-user-gesture-required'] });
      const problems = [];
      const open = async (url, opts = {}) => {
        const page = await (await browser.newContext({ permissions: ['microphone'], ...opts })).newPage();
        await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
        page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|dynamically imported module|MIME type/.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
        page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
        await page.goto(`${SITE}${url}`);
        return page;
      };
      const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
      const T = (page, id) => page.getByTestId(id);
      try {
        const show = await newShow('Delay test B');
        const l = listener();
        const host = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 900 } });
        await T(host, 'delay-panel').waitFor({ timeout: 15000 });
        check('B1 before broadcasting: NOT ON AIR and Dump is off', (await T(host, 'delay-state').innerText()) === 'NOT ON AIR' && await T(host, 'dump-button').isDisabled(), await T(host, 'dump-button').innerText());
        const sized = await host.getByText('of 5 s').waitFor({ timeout: 6000 }).then(() => true, () => false);
        check('B2 it shows the station\'s delay length', sized, (await T(host, 'delay-panel').innerText()).slice(0, 60).replace(/\n/g, ' | '));
        check('B3 delay controls wait for the show to be on air', await T(host, 'delay-length').isDisabled() && await T(host, 'delay-build').isDisabled());
        await shot(host, 'host-1-before');

        await goLive(host);
        await T(host, 'preroll-note').waitFor({ timeout: 4000 });
        check('B4 filling: the host is told when listeners will hear them', /Listeners hear you in \d s\. Start talking now\./.test(await T(host, 'preroll-note').innerText()) && (await T(host, 'delay-state').innerText()) === 'FILLING', await T(host, 'preroll-note').innerText());
        check('B5 listeners hear nothing yet', l.frames.length === 0);
        await shot(host, 'host-2-filling');
        await host.getByText('SAFE', { exact: true }).waitFor({ timeout: 9000 });
        const secs = parseFloat(await T(host, 'delay-seconds').innerText());
        check('B6 SAFE with the full delay shown', Math.abs(secs - 5) < 0.6 && /Listeners hear you 5 s later/.test(await T(host, 'heard-note').innerText()), `${secs} s; ${await T(host, 'heard-note').innerText()}`);
        check('B7 listeners now hear the show', l.live() && l.frames.length > 0);
        check('B8 Dump says what it will cut', /cuts all 5 s waiting \(Shift\+D\)/.test(await T(host, 'dump-button').innerText()), (await T(host, 'dump-button').innerText()).replace(/\n/g, ' | '));
        await shot(host, 'host-3-safe');

        // The screener sees the same panel, compact
        const screener = await open(`/admin/live/${show.id}/screen`, { viewport: { width: 1440, height: 900 } });
        await screener.getByText('SAFE', { exact: true }).waitFor({ timeout: 8000 });
        check('B9 Call Screener has the Dump and the live delay number', await T(screener, 'dump-button').isEnabled() && await T(screener, 'delay-length').count() === 0);
        await shot(screener, 'screener-1-safe');

        // Dump on the host console
        await T(host, 'dump-button').click();
        await T(host, 'delay-message').waitFor({ timeout: 3000 });
        const msg = await T(host, 'delay-message').innerText();
        check('B10 Dump: the screen says how much was cut', /^Dumped [45]\.\d s\. Listeners never heard it\.$/.test(msg), msg);
        await host.getByText('BUILDING', { exact: true }).waitFor({ timeout: 3000 });
        check('B11 after a Dump: BUILDING, counter 1', /Dumps this show: 1/.test(await T(host, 'dump-count').innerText()));
        await shot(host, 'host-4-dumped');
        await T(screener, 'last-dump').waitFor({ timeout: 3000 });
        check('B12 the screener sees who dumped', /Last dump: [45]\.\d s by admin/.test(await T(screener, 'last-dump').innerText()), await T(screener, 'last-dump').innerText());
        // A steady tone is non-stop talk: the delay gains 3 % a second (pauses fill it faster)
        await sleep(40000);
        const built = parseFloat(await T(host, 'delay-seconds').innerText());
        check('B13 the delay builds back by itself while the host talks', built > 0.8 && built < 2.5 && (await T(host, 'delay-state').innerText()) === 'BUILDING', `${built} s after 40 s`);
        check('B13b while building, Dump says how little it would cut', /cuts all [12](\.\d)? s waiting/.test(await T(host, 'dump-button').innerText()), (await T(host, 'dump-button').innerText()).replace(/\n/g, ' | '));

        // Shift+D
        await host.locator('body').click({ position: { x: 5, y: 5 } });
        await host.keyboard.press('Shift+D');
        await T(host, 'dump-count').getByText('Dumps this show: 2').waitFor({ timeout: 3000 });
        check('B14 Shift+D dumps', true);
        // Dump from the screener
        await sleep(2500);
        await T(screener, 'dump-button').click();
        await T(host, 'dump-count').getByText('Dumps this show: 3').waitFor({ timeout: 3000 });
        check('B15 Dump from the Call Screener reaches the host console', true);

        // Exit delay
        await T(host, 'delay-exit').click();
        await host.getByText('LIVE, NO DELAY', { exact: true }).waitFor({ timeout: 30000 });
        check('B16 Exit delay: LIVE, NO DELAY and Dump is off', await T(host, 'dump-button').isDisabled() && /nothing waiting to cut/.test(await T(host, 'dump-button').innerText()) && /Listeners hear you now/.test(await T(host, 'heard-note').innerText()));
        check('B17 the button turns into Build delay', await T(host, 'delay-build').isVisible() && await T(host, 'delay-exit').count() === 0);
        await shot(host, 'host-5-no-delay');
        // Build delay
        await T(host, 'delay-build').click();
        await host.getByText('BUILDING', { exact: true }).waitFor({ timeout: 4000 });
        check('B18 Build delay starts building to the station length', /of 5 s/.test(await T(host, 'delay-panel').innerText()));
        // Delay length
        await T(host, 'delay-length').selectOption('10');
        await T(host, 'delay-apply').click();
        await host.getByText('of 10 s').waitFor({ timeout: 3000 });
        check('B19 Delay length 10 s is taken up', /Building the delay to 10 s/.test(await T(host, 'delay-message').innerText()), await T(host, 'delay-message').innerText());
        check('B20 Set is off when the length is already the chosen one', await T(host, 'delay-apply').isDisabled());
        await T(host, 'delay-length').selectOption('0');
        await T(host, 'delay-apply').click();
        await host.getByText(/EXITING DELAY|LIVE, NO DELAY/).first().waitFor({ timeout: 4000 });
        check('B21 Delay length None exits the delay', true);

        // Phone size: the same panel, no sideways scroll, buttons tappable
        const phone = await open(`/admin/live/${show.id}/host`, { ...devices['iPhone 13'] });
        await T(phone, 'delay-panel').waitFor({ timeout: 15000 });
        await T(phone, 'delay-state').getByText(/LIVE, NO DELAY|EXITING DELAY/).waitFor({ timeout: 8000 });
        const m = await phone.evaluate(() => {
          const b = document.querySelector('[data-testid="dump-button"]').getBoundingClientRect();
          const small = [...document.querySelectorAll('[data-testid="delay-panel"] button, [data-testid="delay-panel"] select')].map((e) => e.getBoundingClientRect()).filter((r) => r.width && (r.height < 44 || r.width < 44)).length;
          return { scroll: document.documentElement.scrollWidth - window.innerWidth, h: b.height, w: b.width, small };
        });
        check('B22 phone: no sideways scroll, Dump is a big target, nothing under 44 px', m.scroll <= 0 && m.h >= 60 && m.small === 0, JSON.stringify(m));
        await shot(phone, 'host-phone');

        // End show from the host console
        host.on('dialog', (d) => d.accept());
        await host.getByRole('button', { name: 'End Show' }).click();
        await host.waitForURL(/\/admin\/live$/, { timeout: 8000 });
        await sleep(1500);
        check('B23 End Show: off air for listeners', !l.live());
        check('B24 no errors on any page', problems.length === 0, problems.slice(0, 4).join(' | '));
        l.ws.close();

        // ---------- Part C: station settings ----------
        const st = await open('/admin/stations', { viewport: { width: 1440, height: 900 } });
        await st.getByText(station.name).first().waitFor({ timeout: 15000 });
        if (!(await T(st, 'live-delay-seconds').count())) {
          // Open this station's settings
          const row = st.locator('tr, div').filter({ hasText: station.name }).last();
          const toggle = row.getByRole('button', { name: /settings|config|automation|expand|▸|▶|edit/i }).first();
          if (await toggle.count()) await toggle.click(); else await st.getByText(station.name).first().click();
        }
        await T(st, 'live-delay-seconds').first().waitFor({ timeout: 8000 });
        check('C1 the station shows its delay', (await T(st, 'live-delay-seconds').first().inputValue()) === '5');
        await T(st, 'live-delay-seconds').first().fill('20');
        await T(st, 'live-dump-seconds').first().fill('8');
        await T(st, 'live-timed-items').first().selectOption('all');
        await shot(st, 'station-settings');
        await st.getByRole('button', { name: 'Save Automation Config' }).first().click();
        await sleep(1500);
        const saved = (await api('GET', `/stations/${STATION}`)).json.automation_config;
        check('C2 delay, dump size and timed items are saved', saved.live_delay_seconds === 20 && saved.live_dump_seconds === 8 && saved.live_timed_items === 'all', JSON.stringify({ d: saved.live_delay_seconds, k: saved.live_dump_seconds, t: saved.live_timed_items }));
        check('C3 other settings are kept', Object.keys(before).every((k) => k.startsWith('live_') || JSON.stringify(saved[k]) === JSON.stringify(before[k])));
        const a = await air((await newShow('Delay test C')).id);
        check('C4 a new show takes the saved settings', a.configured === 20 && a.dump_size === 8, JSON.stringify(a));
      } finally {
        await browser.close();
      }
    }
    // ---------- Part D: cough button, dump log, callers and the delay, the engine's copy ----------
    if (!ONLY || ONLY === 'desk') {
      await setConfig({ live_delay_seconds: 3, live_dump_seconds: 0 });
      const DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'desk-'));
      { const rate = 48000, n = rate * 30, buf = Buffer.alloc(44 + n * 2);
        buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8);
        buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
        buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32);
        buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
        for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 440 * i / rate)), 44 + i * 2);
        fs.writeFileSync(path.join(DIR, 'host.wav'), buf); }
      const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${path.join(DIR, 'host.wav')}`, '--autoplay-policy=no-user-gesture-required'] });
      const problems = [];
      const T = (page, id) => page.getByTestId(id);
      const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
      const open = async (url, opts = {}) => {
        const page = await (await browser.newContext({ permissions: ['microphone'], ...opts })).newPage();
        await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
        page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|409/.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
        page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
        await page.goto(`${SITE}${url}`);
        return page;
      };
      const loud = (l, from, to) => power(l.frames.filter((f) => f.at >= from && f.at < to).flatMap((f) => Array.from(f.pcm)), 440);
      try {
        const show = await newShow('Delay test D');
        const l = listener();
        const host = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 900 } });
        await T(host, 'delay-panel').waitFor({ timeout: 15000 });
        check('D1 no Cough or Mute before the microphone is open', await T(host, 'cough-button').count() === 0 && await T(host, 'mute-button').count() === 0);
        await goLive(host);
        await host.getByText('SAFE', { exact: true }).waitFor({ timeout: 9000 });
        await sleep(1500);

        // Cough: held for 2 s. Listeners get that stretch as silence, 3 s later.
        const box = await T(host, 'cough-button').boundingBox();
        await host.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await host.mouse.down();
        const down = Date.now();
        await T(host, 'mic-muted-note').waitFor({ timeout: 1500 });
        check('D2 holding Cough: the screen says the mic is off', /MIC OFF: listeners do not hear you/.test(await T(host, 'mic-muted-note').innerText()), await T(host, 'mic-muted-note').innerText());
        await sleep(1800 - (Date.now() - down));
        const held = await T(host, 'mic-muted-note').count() === 1;
        await sleep(2000 - (Date.now() - down));
        await host.mouse.up();
        const up = Date.now();
        await sleep(300);
        check('D3 it stays off while held; letting go: the note is gone', held && await T(host, 'mic-muted-note').count() === 0);
        await sleep(5200);
        const before = loud(l, down + 1500, down + 2700), during = loud(l, down + 3600, up + 2700), after = loud(l, up + 3700, up + 4700);
        check('D4 listeners heard the host before, silence for the cough, the host again after', before > 0.005 && after > 0.005 && during < before / 50, `before ${before.toFixed(4)} cough ${during.toFixed(5)} after ${after.toFixed(4)}`);
        check('D5 the show stayed on air through the cough', l.live() && l.frames.filter((f) => f.at >= down + 3600 && f.at < up + 2700).length > 5);

        // The C key does the same while held
        await host.locator('body').click({ position: { x: 5, y: 5 } });
        await host.keyboard.down('c');
        const keyOn = await T(host, 'mic-muted-note').waitFor({ timeout: 1500 }).then(() => true, () => false);
        await host.keyboard.up('c');
        await sleep(300);
        check('D6 holding the C key coughs too', keyOn && await T(host, 'mic-muted-note').count() === 0);

        // Mute stays until pressed again
        await T(host, 'mute-button').click();
        await T(host, 'mic-muted-note').waitFor({ timeout: 1500 });
        const muteAt = Date.now();
        check('D7 Mute mic: the button turns into Unmute mic', /Unmute mic/.test(await T(host, 'mute-button').innerText()) && (await T(host, 'mute-button').getAttribute('aria-pressed')) === 'true');
        await shot(host, 'host-muted');
        await sleep(5500);
        check('D8 muted: listeners get silence, the show stays on air', loud(l, muteAt + 3700, muteAt + 5300) < 0.0005 && l.live(), loud(l, muteAt + 3700, muteAt + 5300).toFixed(5));
        await T(host, 'mute-button').click();
        await sleep(300);
        check('D9 Unmute: the note is gone and the button says Mute mic', await T(host, 'mic-muted-note').count() === 0 && /^Mute mic/.test(await T(host, 'mute-button').innerText()));
        await sleep(4500);
        check('D10 unmuted: listeners hear the host again', loud(l, Date.now() - 1000, Date.now()) > 0.005);

        // A caller waits, approved
        const call = await fetch(`${API}/call-in/${STATION}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: 'Dovid from Lakewood', note: 'A question' }) }).then((r) => r.json());
        const callId = call.call?.id || call.id || call.call_id;
        await api('POST', `/live-shows/${show.id}/calls/${callId}/approve`);
        await host.getByRole('button', { name: 'Put On Air' }).waitFor({ timeout: 5000 });

        // Dump, then the dump log
        check('D11 no dump log before the first Dump', await T(host, 'dump-log').count() === 0);
        await T(host, 'dump-button').click();
        await T(host, 'dump-row').first().waitFor({ timeout: 5000 });
        const row = await T(host, 'dump-row').first().innerText();
        check('D12 the dump log lists the Dump: time, seconds cut, who', /\d+:\d\d.*· [23]\.\d s cut · admin/.test(row.replace(/\n/g, ' ')), row.replace(/\n/g, ' | '));
        await sleep(1800);
        const audio = host.waitForResponse((r) => /\/dumps\/[^/]+\/audio/.test(r.url()), { timeout: 6000 });
        await T(host, 'dump-play').first().click();
        const got = await audio.then((r) => r, () => null);
        const bytes = got ? (await got.body()).length : 0;
        // 6 s heard + 0.5 s gap + about 3 s cut, 24 kHz 16-bit mono
        check('D13 Play fetches the recording: what aired before, then what was cut', !!got && got.status() === 200 && bytes > 24000 * 2 * 6 && bytes < 24000 * 2 * 11, `${got && got.status()} ${bytes} bytes`);
        const stopShown = await T(host, 'dump-play').first().getByText('Stop').waitFor({ timeout: 3000 }).then(() => true, () => false);
        check('D14 while it plays the button says Stop, and pressing it stops', stopShown);
        await shot(host, 'host-dump-log');
        await T(host, 'dump-play').first().click();
        await T(host, 'dump-play').first().getByText('Play').waitFor({ timeout: 2000 });
        const noLogin = await fetch(`${API}/live-shows/${show.id}/dumps`);
        check('D15 the dump log is not public', noLogin.status === 401 || noLogin.status === 403, noLogin.status);

        // The delay was just dumped: a caller put on air now could not be cut
        const asked = [];
        host.once('dialog', (d) => { asked.push(d.message()); d.dismiss(); });
        await host.getByRole('button', { name: 'Put On Air' }).click();
        await sleep(1200);
        check('D16 Put On Air right after a Dump: the host is told the delay is not safe', asked.length === 1 && /The delay is not safe yet: \d+ s of 3 s\..*could not be fully dumped\.\s+Put the caller on air anyway\?/s.test(asked[0]), asked[0]);
        let calls = (await api('GET', `/live-shows/${show.id}/calls`)).json.calls;
        check('D17 Cancel leaves the caller waiting', calls.find((c) => c.id === callId)?.status === 'approved', calls.find((c) => c.id === callId)?.status);
        host.once('dialog', (d) => { asked.push(d.message()); d.accept(); });
        await host.getByRole('button', { name: 'Put On Air' }).click();
        await host.getByText('ON AIR', { exact: true }).waitFor({ timeout: 4000 }).catch(() => {});
        calls = (await api('GET', `/live-shows/${show.id}/calls`)).json.calls;
        check('D18 OK puts the caller on air anyway', asked.length === 2 && calls.find((c) => c.id === callId)?.status === 'on_air', calls.find((c) => c.id === callId)?.status);
        check('D19 with a caller on air the panel offers "Dump and drop the caller"', await T(host, 'dump-drop-button').isVisible());
        await shot(host, 'host-caller-on-air');

        // The engine's copy of the show
        if (process.env.PLAYOUT_TOKEN) {
          const url = `${API}/playout/${STATION}/live.mp3`;
          const refused = await fetch(`${url}?token=${process.env.PLAYOUT_TOKEN}`);
          check('D20 live.mp3 needs the token in the header', refused.status === 403, refused.status);
          const ctl = new AbortController();
          const r = await fetch(url, { headers: { 'X-Playout-Token': process.env.PLAYOUT_TOKEN }, signal: ctl.signal });
          const reader = r.body.getReader(); let n = 0; const t0 = Date.now(); let first = null;
          while (Date.now() - t0 < 4000) { const { value, done } = await reader.read(); if (done) break; if (!first) first = value; n += value.length; }
          ctl.abort();
          // 192 kbps = 24,000 bytes a second
          check('D21 live.mp3 streams the show as MP3 at 192 kbps in real time', r.status === 200 && r.headers.get('content-type') === 'audio/mpeg' && first && first[0] === 0xff && (first[1] & 0xe0) === 0xe0 && n > 70000 && n < 125000, `${r.status} ${n} bytes in 4 s`);
        }

        host.on('dialog', (d) => d.accept());
        await host.getByRole('button', { name: 'End Show' }).click();
        await host.waitForURL(/\/admin\/live$/, { timeout: 8000 });
        await sleep(5000);
        if (process.env.PLAYOUT_TOKEN) {
          const r = await fetch(`${API}/playout/${STATION}/live.mp3`, { headers: { 'X-Playout-Token': process.env.PLAYOUT_TOKEN } });
          check('D22 after the show live.mp3 answers 204', r.status === 204, r.status);
        }
        check('D23 no errors on any page', problems.length === 0, problems.slice(0, 4).join(' | '));
        l.ws.close();
      } finally {
        await browser.close();
      }
    }
    // ---------- Part E: the host is gone; the control room's badge ----------
    if (!ONLY || ONLY === 'gone') {
      await setConfig({ live_delay_seconds: 6, live_dump_seconds: 0, live_host_gone_seconds: 5 });
      const show = await newShow('Delay test E');
      const browser = await chromium.launch();
      try {
        const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
        await page.addInitScript(([t, st]) => { localStorage.setItem('access_token', t); localStorage.setItem('radio_last_station', st); }, [token, STATION]);
        await api('POST', `/live-shows/${show.id}/start`);
        await page.goto(`${SITE}/admin/dashboard`);
        const badge = page.getByTestId('live-badge');
        await badge.waitFor({ timeout: 20000 });
        await page.getByText(/SHOW OPEN, NO HOST/).waitFor({ timeout: 6000 }).catch(() => {});
        const noHost = await badge.innerText();
        check('E1 control room, show started but no microphone: not LIVE', /SHOW OPEN, NO HOST: Delay test E/.test(noHost), noHost);
        const l = listener();
        const h = hostMic(show.id, token);
        await h.ready;
        const filling = await page.getByText(/FILLING DELAY, \d s: Delay test E/).waitFor({ timeout: 5000 }).then(() => true, () => false);
        check('E2 while the delay fills the badge says FILLING DELAY, and listeners hear nothing', filling && !l.live(), await badge.innerText());
        if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'control-room-filling.png') });
        const heard = await page.getByText(/LIVE, [56] s delay: Delay test E/).waitFor({ timeout: 12000 }).then(() => true, () => false);
        await sleep(300);
        check('E3 LIVE with the delay only once listeners hear the host', heard && l.live(), `${await badge.innerText()} / listeners live ${l.live()}`);
        if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'control-room-live.png') });

        // The host's page closes without End Show
        h.close();
        const gone = Date.now();
        let back = null;
        while (Date.now() - gone < 30000 && back === null) {
          await sleep(500);
          const st = (await api('GET', `/stations/${STATION}`)).json;
          if (!(st.automation_config || {}).live_show_id) back = (Date.now() - gone) / 1000;
        }
        // 6 s of delay play out, then 5 s without a host
        check('E4 host gone: the station is handed back to its music by itself', back !== null && back > 5.5 && back < 10, // the 6 s delay plays out, then at once (the 5 s limit counts from the last frame)
          `${back} s after the page closed`);
        const s2 = (await api('GET', `/live-shows/${show.id}`)).json;
        check('E5 the show stays open for the host to come back', s2.status === 'live' && !l.live(), s2.status);
        const alerts = (await api('GET', '/alerts?limit=20')).json;
        const list = alerts.alerts || alerts.items || alerts;
        check('E6 an alert says the show lost its host', Array.isArray(list) && list.some((a) => /Live show lost its host: Delay test E/.test(a.title)), JSON.stringify((list[0] || {}).title));
        const after = await page.getByText(/SHOW OPEN, NO HOST: Delay test E/).waitFor({ timeout: 6000 }).then(() => true, () => false);
        check('E7 the control room badge goes back to NO HOST', after, await badge.innerText());

        // The host comes back: on air again through the delay
        const h2 = hostMic(show.id, token);
        await h2.ready;
        const again = await page.getByText(/LIVE, [56] s delay: Delay test E/).waitFor({ timeout: 14000 }).then(() => true, () => false);
        check('E8 the host comes back and is on air again', again && l.live(), await badge.innerText());
        h2.close();
        l.ws.close();
        await api('POST', `/live-shows/${show.id}/end`);
      } finally {
        await browser.close();
      }
    }
    // ---------- Part F: the caller desk (notes, Hold, Next caller) ----------
    if (!ONLY || ONLY === 'callers') {
      await setConfig({ live_delay_seconds: 0, live_host_gone_seconds: 0 });
      const show = await newShow('Delay test F');
      await api('POST', `/live-shows/${show.id}/start`);
      const browser = await chromium.launch();
      const problems = [];
      const T = (page, id) => page.getByTestId(id);
      const open = async (url, opts) => {
        const page = await (await browser.newContext(opts)).newPage();
        await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
        page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|409|dynamically imported module|MIME type/.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
        page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
        page.on('dialog', (d) => { problems.push(`${url}: dialog "${d.message()}"`); d.dismiss(); });
        await page.goto(`${SITE}${url}`);
        return page;
      };
      const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
      const callIn = async (name, note) => { const r = await fetch(`${API}/call-in/${STATION}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name, note }) }).then((x) => x.json()); return r.call?.id || r.id || r.call_id; };
      const statusOf = async (id) => ((await api('GET', `/live-shows/${show.id}/calls`)).json.calls.find((c) => c.id === id) || {}).status;
      try {
        const screener = await open(`/admin/live/${show.id}/screen`, { viewport: { width: 1440, height: 900 } });
        const host = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 900 } });
        await screener.getByText('Call Screener').waitFor({ timeout: 15000 });
        const first = await callIn('Avi from Lakewood', 'A question about Shabbos candles');
        const second = await callIn('Berel from Jerusalem', '');
        const cardA = screener.locator('div.rounded-lg').filter({ hasText: 'Avi from Lakewood' }).last();
        const cardB = screener.locator('div.rounded-lg').filter({ hasText: 'Berel from Jerusalem' }).last();
        await cardA.getByText('A question about Shabbos candles').waitFor({ timeout: 6000 });
        check('F1 the screener sees what the caller wants to talk about', true);
        check('F2 a caller who wrote nothing shows "No note" and "add note"', /No note/.test(await T(cardB, 'caller-note').innerText()) && /add note/.test(await T(cardB, 'caller-note-button').innerText()));
        await T(cardB, 'caller-note-button').click();
        await screener.getByLabel('Note about this caller').fill('Wants to thank the station. Good line.');
        await T(screener, 'caller-note-save').click();
        await cardB.getByText('Wants to thank the station. Good line.').waitFor({ timeout: 4000 });
        check('F3 the screener writes a note and it is saved', /edit note/.test(await T(cardB, 'caller-note-button').innerText()));
        // Esc leaves the note as it was
        await T(cardA, 'caller-note-button').click();
        await screener.getByLabel('Note about this caller').fill('thrown away');
        await screener.keyboard.press('Escape');
        check('F4 Esc cancels a note edit', await cardA.getByText('A question about Shabbos candles').count() === 1 && await screener.getByText('thrown away').count() === 0);
        await shot(screener, 'screener-notes');

        await cardA.getByRole('button', { name: 'Approve' }).click();
        await screener.locator('div.rounded-lg').filter({ hasText: 'Berel from Jerusalem' }).last().getByRole('button', { name: 'Approve' }).click();
        const hostB = host.locator('div.rounded').filter({ hasText: 'Berel from Jerusalem' }).last();
        await hostB.getByText('Wants to thank the station. Good line.').waitFor({ timeout: 6000 });
        check('F5 the host sees the screener\'s note on the approved caller', await host.getByText('A question about Shabbos candles').count() === 1);
        check('F6 no Next caller button while nobody is on air', await T(host, 'next-caller-button').count() === 0 && await T(screener, 'next-caller-button').count() === 0);

        await host.locator('div.rounded').filter({ hasText: 'Avi from Lakewood' }).last().getByRole('button', { name: 'Put On Air' }).click();
        await T(host, 'hold-button').waitFor({ timeout: 5000 });
        await sleep(400);
        const onAirCard = T(host, 'on-air-card');
        check('F7 on air: the host sees the caller\'s topic in the on-air card', await onAirCard.getByText('A question about Shabbos candles').count() === 1 && await statusOf(first) === 'on_air');
        check('F8 Next caller names who is next', /Next caller: Berel from Jerusalem/.test(await T(host, 'next-caller-button').innerText()) && await T(screener, 'next-caller-button').isVisible(), await T(host, 'next-caller-button').innerText());
        await shot(host, 'host-caller-desk');

        // Hold: off the air, still in the queue
        await T(host, 'hold-button').click();
        await host.getByText('No caller on air').waitFor({ timeout: 5000 });
        await sleep(400);
        check('F9 Hold: the caller is off the air and back in the approved queue', await statusOf(first) === 'approved' && await host.getByRole('button', { name: 'Put On Air' }).count() === 2 && await T(host, 'hold-button').count() === 0);
        await screener.getByText('Approved (2)').waitFor({ timeout: 4000 });
        check('F10 the screener sees the held caller as approved again', await T(screener, 'hold-button').count() === 0);

        // On air again, then Next caller from the screener
        await host.locator('div.rounded').filter({ hasText: 'Avi from Lakewood' }).last().getByRole('button', { name: 'Put On Air' }).click();
        await T(screener, 'hold-button').waitFor({ timeout: 5000 });
        await T(screener, 'next-caller-button').click();
        await T(host, 'on-air-card').getByText('Berel from Jerusalem').waitFor({ timeout: 5000 });
        await sleep(400);
        check('F11 Next caller: the first call ends and the next caller is on air', await statusOf(first) === 'completed' && await statusOf(second) === 'on_air');
        check('F12 with nobody left in the queue the Next caller button is gone', await T(host, 'next-caller-button').count() === 0 && await host.getByText('Avi from Lakewood').count() === 0);
        await T(screener, 'hold-button').click();
        await screener.getByText('Approved (1)').waitFor({ timeout: 4000 });
        await sleep(400);
        check('F13 Hold works from the screener too', await statusOf(second) === 'approved');

        // Phone size: the note and the buttons are usable
        const phone = await open(`/admin/live/${show.id}/host`, { ...devices['iPhone 13'] });
        await phone.getByText('Berel from Jerusalem').first().waitFor({ timeout: 15000 });
        const m = await phone.evaluate(() => ({
          scroll: document.documentElement.scrollWidth - window.innerWidth,
          note: !!document.querySelector('[data-testid="caller-note"]'),
        }));
        check('F14 phone: the note shows and nothing scrolls sideways', m.scroll <= 0 && m.note, JSON.stringify(m));
        await shot(phone, 'host-caller-desk-phone');
        check('F15 no errors or unexpected dialogs on any page', problems.length === 0, problems.slice(0, 4).join(' | '));
        await api('POST', `/live-shows/${show.id}/end`);
      } finally {
        await browser.close();
      }
    }
    // ---------- Part H: carts on the Host Console ----------
    if (!ONLY || ONLY === 'carts') {
      await setConfig({ live_delay_seconds: 3, live_dump_seconds: 0 });
      const DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'carts-'));
      const wav = (f, seconds, rate = 48000) => { const n = rate * seconds, buf = Buffer.alloc(44 + n * 2);
        buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8);
        buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
        buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32);
        buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
        for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * f * i / rate)), 44 + i * 2);
        return buf; };
      fs.writeFileSync(path.join(DIR, 'host.wav'), wav(440, 30));
      const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${path.join(DIR, 'host.wav')}`, '--autoplay-policy=no-user-gesture-required'] });
      const problems = [];
      const T = (page, id) => page.getByTestId(id);
      const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
      const CART = { sets: [{ name: 'Default', pages: [[
        { id: '11111111-1111-4111-8111-111111111111', title: 'Station jingle', duration: 4, asset_type: 'jingle' },
        { id: '22222222-2222-4222-8222-222222222222', title: 'Applause', duration: 20, asset_type: 'jingle' },
        { id: '33333333-3333-4333-8333-333333333333', title: 'Broken file', duration: 5, asset_type: 'jingle' },
      ], [], [], [], [], [], [], [], [], []] }], set: 0, page: 0 };
      const open = async (url, opts = {}, cart = CART) => {
        const page = await (await browser.newContext({ permissions: ['microphone'], ...opts })).newPage();
        await page.addInitScript(([t, key, c]) => { localStorage.setItem('access_token', t); if (c) localStorage.setItem(key, JSON.stringify(c)); }, [token, `kbr_hot_buttons_${STATION}`, cart]);
        // The recordings: 1000 Hz for 4 s, 2000 Hz for 20 s, and one that is not audio
        await page.route('**/assets/*/audio-url', (r) => r.fulfill({ json: { url: `${SITE}/__cart/${r.request().url().split('/assets/')[1].split('/')[0]}.wav` } }));
        await page.route('**/__cart/*', (r) => { const id = r.request().url();
          if (id.includes('3333')) return r.fulfill({ status: 200, contentType: 'audio/wav', body: Buffer.from('not audio') });
          return r.fulfill({ status: 200, contentType: 'audio/wav', body: id.includes('1111') ? wav(1000, 4) : wav(2000, 20) }); });
        page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|409|EncodingError|decode/i.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
        page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
        await page.goto(`${SITE}${url}`);
        return page;
      };
      const heard = (l, f, from, to) => power(l.frames.filter((x) => x.at >= from && x.at < to).flatMap((x) => Array.from(x.pcm)), f);
      try {
        const show = await newShow('Delay test H');
        const l = listener();
        const host = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 900 } });
        await T(host, 'cart-panel').waitFor({ timeout: 15000 });
        const buttons = T(host, 'cart-button');
        check('H1 the instant buttons of the control room show as carts, off until the microphone is open',
          await buttons.count() === 3 && await buttons.first().isDisabled() && /Open your microphone to use the carts/.test(await T(host, 'cart-panel').innerText()), await T(host, 'cart-panel').innerText());
        await goLive(host);
        await host.getByText('SAFE', { exact: true }).waitFor({ timeout: 9000 });
        await sleep(1500);

        // The jingle (1000 Hz, 4 s): listeners get it with the host's voice, 3 s later
        const t1 = Date.now();
        await buttons.nth(0).click();
        await host.getByText('playing, press to stop').waitFor({ timeout: 3000 });
        check('H2 the button shows it is playing and Stop cart appears', await T(host, 'cart-stop').isVisible() && await buttons.nth(0).getAttribute('aria-pressed') === 'true');
        await shot(host, 'host-cart-playing');
        await sleep(8500);
        const before = heard(l, 1000, t1 - 1500, t1 + 2000), during = heard(l, 1000, t1 + 4000, t1 + 6500), after = heard(l, 1000, t1 + 7800, t1 + 8500);
        const voice = heard(l, 440, t1 + 4000, t1 + 6500);
        check('H3 listeners hear the cart 3 s later, with the host, and not before', before < 0.001 && during > 0.004 && voice > 0.004, `1000 Hz before ${before.toFixed(4)} during ${during.toFixed(4)}; host ${voice.toFixed(4)}`);
        check('H4 it ends by itself and the button goes back', after < 0.001 && await T(host, 'cart-stop').count() === 0 && await buttons.nth(0).getAttribute('aria-pressed') === 'false', `after ${after.toFixed(4)}`);

        // Mute mic: the cart still goes out, the microphone does not
        await T(host, 'mute-button').click();
        await sleep(300);
        const t2 = Date.now();
        await buttons.nth(1).click();
        await sleep(2500);
        // Stop cart
        await T(host, 'cart-stop').click();
        const t3 = Date.now();
        await sleep(6500);
        const cartMuted = heard(l, 2000, t2 + 3600, t2 + 5200), micMuted = heard(l, 440, t2 + 3600, t2 + 5200), stopped = heard(l, 2000, t3 + 3800, t3 + 6000);
        check('H5 with the mic muted the cart is still heard and the host is not', cartMuted > 0.004 && micMuted < 0.001, `cart ${cartMuted.toFixed(4)} mic ${micMuted.toFixed(4)}`);
        check('H6 Stop cart stops it for listeners', stopped < 0.001 && await T(host, 'cart-stop').count() === 0, `after stop ${stopped.toFixed(4)}`);
        await T(host, 'mute-button').click();

        // Pressing the playing button stops it; a second cart replaces the first
        await buttons.nth(1).click();
        await T(host, 'cart-stop').waitFor({ timeout: 3000 });
        await buttons.nth(0).click();
        await sleep(600);
        check('H7 a second cart replaces the first', await buttons.nth(0).getAttribute('aria-pressed') === 'true' && await buttons.nth(1).getAttribute('aria-pressed') === 'false');
        await buttons.nth(0).click();
        await sleep(300);
        check('H8 pressing the playing cart stops it', await T(host, 'cart-stop').count() === 0);

        // A file browsers cannot play: said on the screen, nothing stuck
        await buttons.nth(2).click();
        await host.getByText(/could not be played/).waitFor({ timeout: 5000 });
        check('H9 a broken recording says so and no button stays lit', await T(host, 'cart-stop').count() === 0);
        await shot(host, 'host-cart-broken');

        // Stop Broadcasting: the carts are off again
        await host.getByTestId('step-off-button').click();
        await sleep(500);
        check('H10 after Stop Broadcasting the carts are off', await buttons.first().isDisabled());

        // Phone, and a browser with no instant buttons
        const phone = await open(`/admin/live/${show.id}/host`, { ...devices['iPhone 13'] });
        await T(phone, 'cart-panel').waitFor({ timeout: 15000 });
        const m = await phone.evaluate(() => ({
          scroll: document.documentElement.scrollWidth - window.innerWidth,
          small: [...document.querySelectorAll('[data-testid="cart-button"]')].filter((b) => b.getBoundingClientRect().height < 44).length,
        }));
        check('H11 phone: nothing scrolls sideways and every cart is a full tap target', m.scroll <= 0 && m.small === 0, JSON.stringify(m));
        await shot(phone, 'host-cart-phone');
        const empty = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1280, height: 800 } }, null);
        await T(empty, 'cart-empty').waitFor({ timeout: 15000 });
        check('H12 with no instant buttons the panel says where to add them', /instant buttons in the control room/.test(await T(empty, 'cart-empty').innerText()));
        check('H13 no errors on any page', problems.length === 0, problems.slice(0, 4).join(' | '));
        await api('POST', `/live-shows/${show.id}/end`);
      } finally {
        await browser.close();
      }
    }
    // ---------- Part G: the show is recorded and lands in Archives as a draft ----------
    if (!ONLY || ONLY === 'record') {
      await setConfig({ live_delay_seconds: 3, live_dump_seconds: 0, live_host_gone_seconds: 0, live_record_shows: true });
      const title = `Delay test G ${Date.now() % 100000}`;
      const show = await newShow(title);
      await api('POST', `/live-shows/${show.id}/start`);
      const h = hostMic(show.id, token);
      await h.ready;
      await sleep(12000);
      h.say(1000);                       // the "curse"
      await sleep(1500);
      const d = await api('POST', `/live-shows/${show.id}/dump`, {});
      h.say(440);
      await sleep(16000);
      await api('POST', `/live-shows/${show.id}/end`);
      h.close();
      let found = null;
      for (let i = 0; i < 30 && !found; i++) {
        await sleep(1000);
        const list = (await api('GET', `/archives?drafts=true&limit=100&station_id=${STATION}`)).json;
        found = (list.archives || list.items || []).find((a) => a.title === title) || null;
      }
      check('G1 a recording of the show is in Archives after it ends', !!found, found ? `${found.duration_seconds} s` : 'none after 30 s');
      if (found) {
        check('G2 it is a draft (not public) with the show\'s length as listeners heard it', found.is_published === false && found.duration_seconds >= 22 && found.duration_seconds <= 30 && found.live_show_id === show.id, JSON.stringify({ p: found.is_published, d: found.duration_seconds }));
        const pub = (await (await fetch(`${API}/archives?limit=100&station_id=${STATION}`)).json());
        check('G3 listeners do not see the draft', !(pub.archives || pub.items || []).some((a) => a.title === title));
        check('G4 the Dump during the show worked (so the recording has no curse: unit test checks the audio)', d.status === 200 && d.json.dumped_seconds > 1, JSON.stringify(d.json && d.json.dumped_seconds));
        const browser = await chromium.launch();
        try {
          const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
          await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
          await page.goto(`${SITE}/admin/archives`);
          const row = page.locator('tr').filter({ hasText: title }).last();
          const seen = await page.getByText(title).first().waitFor({ timeout: 15000 }).then(() => true, () => false);
          check('G5 the Archives page lists it as Draft', seen && /Draft/.test(await row.innerText().catch(() => '')), seen ? (await row.innerText()).replace(/\n/g, ' | ').slice(0, 120) : 'not listed');
          if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, 'archives-draft.png'), fullPage: true }); }
        } finally { await browser.close(); }
        await api('DELETE', `/archives/${found.id}`);
      }
      // Switched off for the station: no recording
      await setConfig({ live_delay_seconds: 0, live_host_gone_seconds: 0, live_record_shows: false });
      const title2 = `Delay test G off ${Date.now() % 100000}`;
      const show2 = await newShow(title2);
      await api('POST', `/live-shows/${show2.id}/start`);
      const h2 = hostMic(show2.id, token);
      await h2.ready;
      await sleep(23000);
      await api('POST', `/live-shows/${show2.id}/end`);
      h2.close();
      await sleep(5000);
      const list2 = (await api('GET', `/archives?drafts=true&limit=100&station_id=${STATION}`)).json;
      check('G6 "Record every live show" off: nothing is recorded', !(list2.archives || list2.items || []).some((a) => a.title === title2));
    }
    // ---------- Part I: talkback (talking to a caller off the air) ----------
    if (!ONLY || ONLY === 'talk') {
      await setConfig({ live_delay_seconds: 0, live_host_gone_seconds: 0 });
      const DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'talk-'));
      { const rate = 48000, n = rate * 30, buf = Buffer.alloc(44 + n * 2);
        buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8);
        buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
        buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32);
        buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
        for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 1000 * i / rate)), 44 + i * 2);
        fs.writeFileSync(path.join(DIR, 'staff.wav'), buf); }
      // Every browser microphone in this part "says" 1000 Hz; the script's host says 440 and its caller 700
      const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${path.join(DIR, 'staff.wav')}`, '--autoplay-policy=no-user-gesture-required'] });
      const problems = [];
      const T = (page, id) => page.getByTestId(id);
      const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
      const open = async (url, opts = {}, signedIn = true) => {
        const page = await (await browser.newContext({ permissions: ['microphone'], ...opts })).newPage();
        if (signedIn) await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
        page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|409|dynamically imported module|MIME type/.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
        page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
        page.on('dialog', (d) => { problems.push(`${url}: dialog "${d.message()}"`); d.dismiss(); });
        await page.goto(`${SITE}${url}`);
        return page;
      };
      /** A web caller driven by the script: says 700 Hz, keeps what it hears and is told */
      const scriptCaller = async (name) => {
        const r = await fetch(`${API}/call-in/${STATION}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name, note: '' }) }).then((x) => x.json());
        const c = { id: r.call_id || r.id, frames: [], msgs: [], sent: 0, ws: new WebSocket(`${API.replace(/^http/, 'ws')}/ws/live/call/${r.call_id || r.id}`) };
        c.ws.binaryType = 'arraybuffer';
        await new Promise((res) => { c.ws.onopen = () => { c.ws.send(JSON.stringify({ type: 'auth', token: r.token })); res(); }; });
        c.ws.onmessage = (e) => { if (typeof e.data === 'string') c.msgs.push(JSON.parse(e.data)); else c.frames.push({ at: Date.now(), pcm: new Int16Array(e.data) }); };
        const t0 = Date.now();
        c.timer = setInterval(() => { if (c.ws.readyState !== 1) return; const due = Math.floor((Date.now() - t0) / 1000 * RATE) - c.sent; if (due > 0) { c.ws.send(tone(700, due, c.sent).buffer); c.sent += due; } }, 40);
        c.heard = (f, from, to) => power(c.frames.filter((x) => x.at >= from && x.at < to).flatMap((x) => Array.from(x.pcm)), f);
        c.close = () => { clearInterval(c.timer); c.ws.close(); };
        return c;
      };
      const heard = (l, f, from, to) => power(l.frames.filter((x) => x.at >= from && x.at < to).flatMap((x) => Array.from(x.pcm)), f);
      let show, mic, caller;
      try {
        show = await newShow('Delay test I');
        await api('POST', `/live-shows/${show.id}/start`);
        const statusOf = async (id) => ((await api('GET', `/live-shows/${show.id}/calls`)).json.calls.find((c) => c.id === id) || {}).status;
        const l = listener();
        mic = hostMic(show.id, token);
        await mic.ready;
        await sleep(1500);
        caller = await scriptCaller('Chaim from Monsey');
        const screener = await open(`/admin/live/${show.id}/screen`, { viewport: { width: 1440, height: 900 } });
        const card = screener.locator('div.rounded-lg').filter({ hasText: 'Chaim from Monsey' }).last();
        await card.getByRole('button', { name: 'Approve' }).waitFor({ timeout: 15000 });
        await sleep(1500);
        const t0 = Date.now();
        check('I1 a waiting caller hears the show', caller.heard(440, t0 - 1200, t0) > 0.004, caller.heard(440, t0 - 1200, t0).toFixed(4));
        check('I2 every waiting caller has a "Talk off air" button', /Talk off air/.test(await T(card, 'talk-button').innerText()));

        // The screener talks to the caller
        await T(card, 'talk-button').click();
        await T(screener, 'talk-banner').waitFor({ timeout: 5000 });
        await sleep(2500);
        const t1 = Date.now();
        check('I3 the screen says who is being talked to and that listeners hear nothing', /Talking off air to Chaim from Monsey\. Listeners hear nothing of this\./.test(await T(screener, 'talk-banner').innerText()) && /End talk/.test(await T(card, 'talk-button').innerText()), await T(screener, 'talk-banner').innerText());
        check('I4 the caller\'s page is told', caller.msgs.some((m) => m.type === 'talk' && m.on === true));
        const cs = caller.heard(1000, t1 - 1500, t1), ch = caller.heard(440, t1 - 1500, t1);
        check('I5 the caller hears the screener and not the show', cs > 0.004 && ch < 0.001, `screener ${cs.toFixed(4)} show ${ch.toFixed(4)}`);
        const ls = heard(l, 1000, t1 - 1500, t1), lc = heard(l, 700, t1 - 1500, t1), lh = heard(l, 440, t1 - 1500, t1);
        check('I6 listeners hear the host only: nothing of the screener or the caller', ls < 0.001 && lc < 0.001 && lh > 0.004, `screener ${ls.toFixed(4)} caller ${lc.toFixed(4)} host ${lh.toFixed(4)}`);
        await shot(screener, 'screener-talkback');

        // A second person takes the caller over (here: this script, to read what the staff side receives)
        const staff = { frames: [], closed: null, ws: new WebSocket(`${API.replace(/^http/, 'ws')}/ws/live/${show.id}/talk/${caller.id}`) };
        staff.ws.binaryType = 'arraybuffer';
        staff.ws.onopen = () => staff.ws.send(JSON.stringify({ type: 'auth', token }));
        staff.ws.onmessage = (e) => { if (typeof e.data !== 'string') staff.frames.push({ at: Date.now(), pcm: new Int16Array(e.data) }); };
        staff.ws.onclose = (e) => { staff.closed = e.code; };
        await T(screener, 'talk-banner').waitFor({ state: 'detached', timeout: 5000 });
        await sleep(1500);
        const t2 = Date.now();
        check('I7 taken over by someone else: the first screen goes back by itself, with no error', await T(screener, 'talk-banner').count() === 0 && /Talk off air/.test(await T(card, 'talk-button').innerText()));
        check('I8 the person talking hears the caller', heard(staff, 700, t2 - 1200, t2) > 0.004, heard(staff, 700, t2 - 1200, t2).toFixed(4));

        // On air: the talk ends first
        await api('POST', `/live-shows/${show.id}/calls/${caller.id}/approve`);
        await api('POST', `/live-shows/${show.id}/calls/${caller.id}/on-air?force=true`);
        await sleep(1500);
        const t3 = Date.now();
        check('I9 putting the caller on air ends the talk', staff.closed === 4000 && caller.msgs.some((m) => m.type === 'talk' && m.on === false) && await statusOf(caller.id) === 'on_air', `closed ${staff.closed}`);
        check('I10 and now listeners hear the caller', heard(l, 700, t3 - 1000, t3) > 0.004);
        // Talk to a caller who is on air: refused, and the screen says why
        check('I11 no Talk button on the caller on air', await screener.getByTestId('talk-button').count() === 0);
        await api('POST', `/live-shows/${show.id}/calls/${caller.id}/hold`);
        mic.close();
        await sleep(600);

        // The host talks to the caller: the host's microphone to listeners closes meanwhile
        const host = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 900 } });
        await goLive(host);
        await T(host, 'mute-button').waitFor({ timeout: 9000 });
        await sleep(2000);
        const t4 = Date.now();
        check('I12 the host is on air (1000 Hz from the browser microphone)', heard(l, 1000, t4 - 1200, t4) > 0.004);
        await T(host, 'talk-button').click();
        await T(host, 'talk-banner').waitFor({ timeout: 5000 });
        await sleep(2500);
        const t5 = Date.now();
        check('I13 the host screen says TALKING OFF AIR', /TALKING OFF AIR: only the caller hears you/.test(await T(host, 'mic-muted-note').innerText()), await T(host, 'mic-muted-note').innerText());
        check('I14 while the host talks to the caller listeners hear nothing of it, and the caller hears the host', heard(l, 1000, t5 - 1500, t5) < 0.001 && heard(l, 700, t5 - 1500, t5) < 0.001 && caller.heard(1000, t5 - 1500, t5) > 0.004,
          `listeners ${heard(l, 1000, t5 - 1500, t5).toFixed(4)} caller hears ${caller.heard(1000, t5 - 1500, t5).toFixed(4)}`);
        await shot(host, 'host-talkback');
        await T(host, 'talk-banner').getByRole('button', { name: 'End talk' }).click();
        await sleep(2500);
        const t6 = Date.now();
        check('I15 End talk: the host is back on air and the note is gone', heard(l, 1000, t6 - 1200, t6) > 0.004 && await T(host, 'mic-muted-note').count() === 0 && await T(host, 'talk-banner').count() === 0);
        check('I16 a muted microphone stays muted after a talk', await (async () => {
          await T(host, 'mute-button').click(); await T(host, 'talk-button').click(); await T(host, 'talk-banner').waitFor({ timeout: 5000 });
          await T(host, 'talk-button').click(); await sleep(1800);
          const t = Date.now(); return /Unmute mic/.test(await T(host, 'mute-button').innerText()) && heard(l, 1000, t - 1000, t) < 0.001; })());

        // The caller's own page says it, and a caller who left gives a clear message
        const page = await open(`/call/${STATION}`, { ...devices['iPhone 13'] }, false);
        await page.getByPlaceholder('Name').fill('Dina from Brooklyn');
        await page.getByRole('button', { name: 'Call in' }).click();
        const cardD = screener.locator('div.rounded-lg').filter({ hasText: 'Dina from Brooklyn' }).last();
        await T(cardD, 'talk-button').waitFor({ timeout: 10000 });
        await sleep(800);
        await T(cardD, 'talk-button').click();
        await T(page, 'talk-note').waitFor({ timeout: 6000 });
        check('I17 the caller\'s page says the staff are talking to them and they are not on air', /talking to you now\. You are not on the air/.test(await T(page, 'talk-note').innerText()), await T(page, 'talk-note').innerText());
        await shot(page, 'caller-talkback-phone');
        await T(cardD, 'talk-button').click();
        await T(page, 'talk-note').waitFor({ state: 'detached', timeout: 5000 });
        check('I18 End talk: the note on the caller\'s page goes away', true);
        caller.close();
        await sleep(700);
        const cardC = screener.locator('div.rounded-lg').filter({ hasText: 'Chaim from Monsey' }).last();
        if (await T(cardC, 'talk-button').count()) {
          await T(cardC, 'talk-button').click();
          await screener.getByText(/not connected from the website any more/).waitFor({ timeout: 5000 });
          check('I19 a caller whose page is gone: the screen says they cannot be talked to here', await T(screener, 'talk-banner').getByText('Talking off air').count() === 0);
        } else check('I19 a caller whose page is gone is no longer listed', true);
        check('I20 no errors or unexpected dialogs on any page', problems.length === 0, problems.slice(0, 4).join(' | '));
        await api('POST', `/live-shows/${show.id}/end`);
      } finally {
        if (mic) mic.close();
        if (caller) caller.close();
        await browser.close();
      }
    }
    // ---------- Part J: the server restarts during a show (needs BACKEND_RESTART, a command that restarts the backend) ----------
    if (ONLY === 'restart') {
      if (!process.env.BACKEND_RESTART) { console.error('ONLY=restart needs BACKEND_RESTART="<command that restarts the backend and returns when it answers>"'); process.exit(2); }
      await setConfig({ live_delay_seconds: 3, live_dump_seconds: 0, live_host_gone_seconds: 15 });
      const DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'restart-'));
      { const rate = 48000, n = rate * 30, buf = Buffer.alloc(44 + n * 2);
        buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8);
        buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
        buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32);
        buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
        for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 440 * i / rate)), 44 + i * 2);
        fs.writeFileSync(path.join(DIR, 'host.wav'), buf); }
      const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${path.join(DIR, 'host.wav')}`, '--autoplay-policy=no-user-gesture-required'] });
      const T = (page, id) => page.getByTestId(id);
      const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
      try {
        const show = await newShow('Delay test J');
        const l = listener();
        const host = await (await browser.newContext({ permissions: ['microphone'], viewport: { width: 1440, height: 900 } })).newPage();
        await host.addInitScript((t) => localStorage.setItem('access_token', t), token);
        const dialogs = [];
        host.on('dialog', (d) => { dialogs.push(d.message()); d.dismiss(); });
        await host.goto(`${SITE}/admin/live/${show.id}/host`);
        await goLive(host);
        await host.getByText('SAFE', { exact: true }).waitFor({ timeout: 9000 });
        await sleep(2000);
        const t0 = Date.now();
        check('J1 before the restart listeners hear the host', power(l.since(t0 - 1200), 440) > 0.004);

        // The server goes away and comes back (a deploy)
        const { exec } = require('child_process');
        const restarted = new Promise((res) => exec(process.env.BACKEND_RESTART, (err) => res(!err)));
        await T(host, 'reconnecting-note').waitFor({ timeout: 8000 });
        check('J2 the host is told the connection is lost and that it is reconnecting', /Reconnecting/.test(await T(host, 'reconnecting-note').innerText()) && await host.getByTestId('step-off-button').count() === 1, await T(host, 'reconnecting-note').innerText());
        await shot(host, 'host-reconnecting');
        const down = Date.now();
        check('J3 the backend came back', await restarted);
        const up = Date.now();
        await T(host, 'reconnecting-note').waitFor({ state: 'detached', timeout: 8000 });
        const back = Date.now();
        check('J4 the host page is connected again within 4 s of the server answering, with nothing pressed', back - up < 4000, `${back - up} ms after the server answered (${back - down} ms after the drop was seen)`);
        const l2 = listener();
        await sleep(3500);
        const t1 = Date.now();
        const st = (await api('GET', `/stations/${STATION}`)).json;
        check('J5 the station is still the show\'s and listeners hear the host again at once (no second pre-roll)', (st.automation_config || {}).live_show_id === show.id && l2.live() && power(l2.since(t1 - 1500), 440) > 0.004, `live ${l2.live()} power ${power(l2.since(t1 - 1500), 440).toFixed(4)}`);
        const a = await air(show.id);
        check('J6 the delay is building back', a && a.on_air && a.target === 3, JSON.stringify(a).slice(0, 160));
        check('J7 no error on the screen, no dialog, still broadcasting', dialogs.length === 0 && await host.getByText(/connection to the station was lost/).count() === 0 && await T(host, 'mute-button').count() === 1, dialogs.join(' | '));
        await sleep(16000);
        const t2 = Date.now();
        check('J8 20 s later the show is still on air (not handed back as "host gone")', ((await api('GET', `/stations/${STATION}`)).json.automation_config || {}).live_show_id === show.id && power(l2.since(t2 - 1500), 440) > 0.004);
        await api('POST', `/live-shows/${show.id}/end`);
      } finally {
        await browser.close();
      }
    }
    // ---------- Part K: several callers on air together ----------
    if (!ONLY || ONLY === 'multi') {
      await setConfig({ live_delay_seconds: 0, live_host_gone_seconds: 0 });
      const browser = await chromium.launch();
      const problems = [];
      const T = (page, id) => page.getByTestId(id);
      const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
      const open = async (url, opts = {}) => {
        const page = await (await browser.newContext(opts)).newPage();
        await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
        page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|409|dynamically imported module|MIME type/.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
        page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
        page.on('dialog', (d) => { problems.push(`${url}: dialog "${d.message()}"`); d.dismiss(); });
        await page.goto(`${SITE}${url}`);
        return page;
      };
      const scriptCaller = async (name, f) => {
        const r = await fetch(`${API}/call-in/${STATION}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name, note: '' }) }).then((x) => x.json());
        const c = { id: r.call_id || r.id, frames: [], sent: 0, ws: new WebSocket(`${API.replace(/^http/, 'ws')}/ws/live/call/${r.call_id || r.id}`) };
        c.ws.binaryType = 'arraybuffer';
        await new Promise((res) => { c.ws.onopen = () => { c.ws.send(JSON.stringify({ type: 'auth', token: r.token })); res(); }; });
        c.ws.onmessage = (e) => { if (typeof e.data !== 'string') c.frames.push({ at: Date.now(), pcm: new Int16Array(e.data) }); };
        const t0 = Date.now();
        c.timer = setInterval(() => { if (c.ws.readyState !== 1) return; const due = Math.floor((Date.now() - t0) / 1000 * RATE) - c.sent; if (due > 0) { c.ws.send(tone(f, due, c.sent).buffer); c.sent += due; } }, 40);
        c.close = () => { clearInterval(c.timer); c.ws.close(); };
        return c;
      };
      const heard = (l, f, from, to) => power(l.frames.filter((x) => x.at >= from && x.at < to).flatMap((x) => Array.from(x.pcm)), f);
      let mic; const lines = [];
      try {
        const show = await newShow('Delay test K');
        await api('POST', `/live-shows/${show.id}/start`);
        const statusOf = async (id) => ((await api('GET', `/live-shows/${show.id}/calls`)).json.calls.find((c) => c.id === id) || {}).status;
        const l = listener();
        mic = hostMic(show.id, token);
        await mic.ready;
        const a = await scriptCaller('Avi from Lakewood', 700); lines.push(a);
        const b = await scriptCaller('Berel from Jerusalem', 900); lines.push(b);
        const screener = await open(`/admin/live/${show.id}/screen`, { viewport: { width: 1440, height: 900 } });
        const host = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 900 } });
        for (const name of ['Avi from Lakewood', 'Berel from Jerusalem']) {
          await screener.locator('div.rounded-lg').filter({ hasText: name }).last().getByRole('button', { name: 'Approve' }).click();
          await sleep(500);
        }
        const approved = (page, name) => page.locator('div.rounded, div.rounded-lg').filter({ hasText: name }).last();
        await approved(screener, 'Avi from Lakewood').getByRole('button', { name: 'Put On Air' }).click();
        await T(screener, 'on-air-card').first().waitFor({ timeout: 5000 });
        const second = approved(screener, 'Berel from Jerusalem').getByRole('button', { name: 'Put On Air' });
        check('K1 with one caller on air, Put On Air for a second is not greyed out', await second.isEnabled() && /together with the caller already there/.test(await second.getAttribute('title') || ''), await second.getAttribute('title'));
        await second.click();
        await screener.getByText('On Air (2 together)').waitFor({ timeout: 5000 });
        await host.getByText('Caller On Air (2 together)').waitFor({ timeout: 5000 });
        check('K2 both screens show two callers on air, each with its own Hold and End Call', await T(screener, 'on-air-card').count() === 2 && await T(host, 'on-air-card').count() === 2 && await T(host, 'hold-button').count() === 2 && await host.getByRole('button', { name: 'End Call' }).count() === 2);
        await shot(host, 'host-two-callers');
        await shot(screener, 'screener-two-callers');
        await sleep(2500);
        const t1 = Date.now();
        const lv = [440, 700, 900].map((f) => heard(l, f, t1 - 1500, t1));
        check('K3 listeners hear the host and both callers', lv.every((v) => v > 0.004), lv.map((v) => v.toFixed(4)).join(' / '));
        check('K4 each caller hears the host and the other caller, not themselves', heard(a, 440, t1 - 1500, t1) > 0.004 && heard(a, 900, t1 - 1500, t1) > 0.004 && heard(a, 700, t1 - 1500, t1) < 0.001 && heard(b, 700, t1 - 1500, t1) > 0.004 && heard(b, 900, t1 - 1500, t1) < 0.001,
          `A hears 440 ${heard(a, 440, t1 - 1500, t1).toFixed(4)} 900 ${heard(a, 900, t1 - 1500, t1).toFixed(4)} own ${heard(a, 700, t1 - 1500, t1).toFixed(4)}`);

        // Hold one of the two from the Host Console: the other stays on air
        await T(host, 'on-air-card').filter({ hasText: 'Avi from Lakewood' }).getByTestId('hold-button').click();
        await host.getByText('Caller On Air (2 together)').waitFor({ state: 'detached', timeout: 5000 });
        await sleep(2000);
        const t2 = Date.now();
        check('K5 Hold on one: that caller is off the air, the other is still heard', await statusOf(a.id) === 'approved' && await statusOf(b.id) === 'on_air' && heard(l, 700, t2 - 1200, t2) < 0.001 && heard(l, 900, t2 - 1200, t2) > 0.004 && await T(host, 'on-air-card').count() === 1);

        // The limit is four
        const more = [];
        for (const [n, f] of [['Chaim', 1100], ['Dovid', 1300], ['Eli', 1500]]) { const c = await scriptCaller(n, f); lines.push(c); more.push(c); await api('POST', `/live-shows/${show.id}/calls/${c.id}/approve`); }
        await api('POST', `/live-shows/${show.id}/calls/${a.id}/on-air`);
        await api('POST', `/live-shows/${show.id}/calls/${more[0].id}/on-air`);
        await api('POST', `/live-shows/${show.id}/calls/${more[1].id}/on-air`);
        await host.getByText('Caller On Air (4 together)').waitFor({ timeout: 6000 });
        const fifth = approved(host, 'Eli').getByRole('button', { name: 'Put On Air' });
        const refused = await api('POST', `/live-shows/${show.id}/calls/${more[2].id}/on-air`);
        check('K6 four on air: a fifth is refused and its button is off, saying why', await fifth.isDisabled() && /4 callers are on air already/.test(await fifth.getAttribute('title') || '') && refused.status === 409, `${refused.status} ${await fifth.getAttribute('title')}`);
        check('K7 only the last card carries Next caller', await T(host, 'next-caller-button').count() === 1 && /Next caller: Eli/.test(await T(host, 'next-caller-button').innerText()));
        await shot(host, 'host-four-callers');
        const phone = await open(`/admin/live/${show.id}/host`, { ...devices['iPhone 13'] });
        await phone.getByText('Caller On Air (4 together)').waitFor({ timeout: 15000 });
        check('K8 phone: four callers on air and nothing scrolls sideways', await phone.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) <= 0 && await T(phone, 'on-air-card').count() === 4);
        await shot(phone, 'host-four-callers-phone');

        // Next caller ends everyone on air and brings the next one on
        await T(host, 'next-caller-button').click();
        await host.getByText('Caller On Air (4 together)').waitFor({ state: 'detached', timeout: 6000 });
        await sleep(800);
        const sts = [];
        for (const c of [a, b, more[0], more[1], more[2]]) sts.push(await statusOf(c.id));
        check('K9 Next caller ends all four and puts the next caller on air', sts.join(',') === 'completed,completed,completed,completed,on_air' && await T(host, 'on-air-card').count() === 1, sts.join(','));
        check('K10 no errors or unexpected dialogs on any page', problems.length === 0, problems.slice(0, 4).join(' | '));
        await api('POST', `/live-shows/${show.id}/end`);
      } finally {
        if (mic) mic.close();
        for (const c of lines) c.close();
        await browser.close();
      }
    }
    // ---------- Part L: staff chat between the host and the screener ----------
    if (!ONLY || ONLY === 'chat') {
      await setConfig({ live_delay_seconds: 0, live_host_gone_seconds: 0 });
      const show = await newShow('Delay test L');
      await api('POST', `/live-shows/${show.id}/start`);
      const browser = await chromium.launch();
      const problems = [];
      const T = (page, id) => page.getByTestId(id);
      const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
      const open = async (url, opts = {}) => {
        const page = await (await browser.newContext(opts)).newPage();
        await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
        page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|409|422|dynamically imported module|MIME type/.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
        page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
        page.on('dialog', (d) => { problems.push(`${url}: dialog "${d.message()}"`); d.dismiss(); });
        await page.goto(`${SITE}${url}`);
        return page;
      };
      try {
        const screener = await open(`/admin/live/${show.id}/screen`, { viewport: { width: 1440, height: 900 } });
        const host = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 900 } });
        await T(host, 'chat-panel').waitFor({ timeout: 15000 });
        await T(screener, 'chat-panel').waitFor({ timeout: 15000 });
        check('L1 both screens have the staff chat, empty and saying what it is for', /No messages yet/.test(await T(host, 'chat-lines').innerText()) && /off the air/.test(await T(screener, 'chat-panel').innerText()) && await T(host, 'chat-send').isDisabled());
        await sleep(800);
        await screener.getByLabel("Message to the show's staff").fill('Caller 2 is ready, good line');
        await screener.keyboard.press('Enter');
        await T(host, 'chat-line').filter({ hasText: 'Caller 2 is ready, good line' }).waitFor({ timeout: 4000 });
        check('L2 the screener writes (Enter sends) and the host sees it within moments, with who and when', /admin/i.test(await T(host, 'chat-line').first().innerText()) && await screener.getByLabel("Message to the show's staff").inputValue() === '' && await T(screener, 'chat-line').count() === 1, await T(host, 'chat-line').first().innerText());
        await shot(host, 'host-chat');
        await host.getByLabel("Message to the show's staff").fill('Thanks. Wrap up after this one.');
        await T(host, 'chat-send').click();
        await T(screener, 'chat-line').filter({ hasText: 'Wrap up after this one.' }).waitFor({ timeout: 4000 });
        check('L3 the host answers with the Send button and the screener sees both lines in order', (await T(screener, 'chat-line').allInnerTexts()).map((t) => (/Caller 2/.test(t) ? 'a' : 'b')).join('') === 'ab');
        await shot(screener, 'screener-chat');

        // A page opened later gets the lines that were written before
        const late = await open(`/admin/live/${show.id}/host`, { ...devices['iPhone 13'] });
        await T(late, 'chat-line').nth(1).waitFor({ timeout: 15000 });
        const m = await late.evaluate(() => ({ scroll: document.documentElement.scrollWidth - window.innerWidth, send: document.querySelector('[data-testid="chat-send"]').getBoundingClientRect().height }));
        check('L4 a phone opened later shows the earlier lines, nothing scrolls sideways, Send is a full tap target', await T(late, 'chat-line').count() === 2 && m.scroll <= 0 && m.send >= 44, JSON.stringify(m));
        await shot(late, 'host-chat-phone');

        // A very long word wraps instead of widening the page; spaces only cannot be sent
        await host.getByLabel("Message to the show's staff").fill('W'.repeat(300));
        await T(host, 'chat-send').click();
        await T(host, 'chat-line').nth(2).waitFor({ timeout: 4000 });
        await host.getByLabel("Message to the show's staff").fill('    ');
        check('L5 a long unbroken line wraps and an empty one cannot be sent', await host.evaluate(() => document.documentElement.scrollWidth - window.innerWidth) <= 0 && await T(host, 'chat-send').isDisabled());
        const listed = (await api('GET', `/live-shows/${show.id}/chat`)).json.messages;
        const anon = await fetch(`${API}/live-shows/${show.id}/chat`);
        check('L6 the lines are stored with the show and not public', listed.length === 3 && anon.status === 401, `${listed.length} lines, anonymous ${anon.status}`);
        check('L7 no errors or unexpected dialogs on any page', problems.length === 0, problems.slice(0, 4).join(' | '));
        await api('POST', `/live-shows/${show.id}/end`);
      } finally {
        await browser.close();
      }
    }
    // ---------- Part M: caller history (repeat, priority and blocked callers) ----------
    if (!ONLY || ONLY === 'history') {
      await setConfig({ live_delay_seconds: 0, live_host_gone_seconds: 0 });
      const show = await newShow('Delay test M');
      await api('POST', `/live-shows/${show.id}/start`);
      const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] });
      const problems = [];
      const T = (page, id) => page.getByTestId(id);
      const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
      const open = async (url, opts = {}, signedIn = true) => {
        const page = await (await browser.newContext(opts)).newPage();
        if (signedIn) await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
        page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|409|403|dynamically imported module|MIME type/.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
        page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
        page.on('dialog', (d) => { problems.push(`${url}: dialog "${d.message()}"`); d.dismiss(); });
        await page.goto(`${SITE}${url}`);
        return page;
      };
      const call = async (name, caller) => { const r = await fetch(`${API}/call-in/${STATION}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name, caller }) }); return { status: r.status, json: await r.json().catch(() => null) }; };
      const calls = async () => (await api('GET', `/live-shows/${show.id}/calls`)).json.calls;
      try {
        // Leftovers of an earlier run: nobody is marked
        for (const m of (await api('GET', '/caller-profiles')).json.callers) await api('PUT', `/caller-profiles/${m.id}`, { standing: 'normal' });
        const screener = await open(`/admin/live/${show.id}/screen`, { viewport: { width: 1440, height: 900 } });
        await T(screener, 'marked-callers').waitFor({ timeout: 15000 });
        const card = (page, name) => page.locator('div.rounded-lg').filter({ hasText: name }).filter({ has: page.getByTestId('caller-history') }).last();

        // The first call comes from the real call-in page: it makes its own id
        const phone = await open(`/call/${STATION}`, { ...devices['iPhone 13'] }, false);
        await phone.getByPlaceholder('Name').fill('Moshe from Lakewood');
        await phone.getByRole('button', { name: 'Call in' }).click();
        await T(card(screener, 'Moshe from Lakewood'), 'caller-history-line').waitFor({ timeout: 10000 });
        const id = await phone.evaluate(() => localStorage.getItem('kbr_caller_id'));
        check('M1 a new caller from the call-in page is listed as "First call"; the page keeps a random id', /^First call$/.test(await T(card(screener, 'Moshe from Lakewood'), 'caller-history-line').innerText()) && /^[0-9a-f]{36}$/.test(id || ''), id);
        const first = (await calls())[0];
        await api('POST', `/live-shows/${show.id}/calls/${first.id}/approve`);
        await api('POST', `/live-shows/${show.id}/calls/${first.id}/on-air?force=true`);
        await sleep(500);
        await api('POST', `/live-shows/${show.id}/calls/${first.id}/end-call`);
        await phone.close();

        // The same person calls again, and a stranger after them
        const again = (await call('Moshe', id)).json;
        const other = (await call('Dovid from Monsey', 'e2e-stranger-0123456789')).json;
        await T(card(screener, 'Dovid from Monsey'), 'caller-history-line').waitFor({ timeout: 8000 });
        const lineA = await T(card(screener, 'Moshe'), 'caller-history-line').innerText();
        check('M2 the repeat caller\'s card says how often they called and were on air, and when', /^Called once before, on air once, last \w+ \d+$/.test(lineA), lineA);
        const order = async () => (await screener.getByTestId('caller-history').evaluateAll((els) => els.map((e) => e.parentElement.innerText.split('\n')[0])));
        const before = await order();

        // Priority: a badge, and that caller moves to the top of Waiting
        await sleep(600);
        await T(card(screener, 'Dovid from Monsey'), 'caller-priority-button').click();
        await T(card(screener, 'Dovid from Monsey'), 'caller-priority').waitFor({ timeout: 5000 });
        const after = await order();
        check('M3 Priority puts a badge on the card and the caller first in Waiting', /^Moshe/.test(before[0]) && /^Dovid/.test(after[0]) && await T(card(screener, 'Dovid from Monsey'), 'caller-priority-button').innerText() === 'Not priority', `${before.join(' | ')} -> ${after.join(' | ')}`);

        // A note about the person
        await T(card(screener, 'Moshe'), 'caller-person-note-button').click();
        await screener.getByLabel('Note about this person, kept for their later calls').fill('Long winded, keep it to one question');
        await screener.keyboard.press('Enter');
        await T(card(screener, 'Moshe'), 'caller-person-note').waitFor({ timeout: 5000 });
        await shot(screener, 'screener-history');

        // The host sees the same on an approved caller, without the buttons
        await api('POST', `/live-shows/${show.id}/calls/${other.call_id}/approve`);
        const host = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 900 } });
        await T(host, 'caller-priority').waitFor({ timeout: 15000 });
        check('M4 the Host Console shows the badge and the history, read only', /First call|Called .* before/.test((await T(host, 'caller-history').allInnerTexts()).join(' ')) && await T(host, 'caller-block-button').count() === 0 && await T(host, 'caller-priority-button').count() === 0);
        await shot(host, 'host-history');

        // Block: asks once more on the button, then the waiting call is gone and new calls are refused
        await T(card(screener, 'Moshe'), 'caller-block-button').click();
        check('M5 Block asks once more on the button itself', /Yes, block and remove/.test(await T(card(screener, 'Moshe'), 'caller-block-sure').innerText()) && (await calls()).find((c) => c.id === again.call_id).status === 'waiting');
        await T(card(screener, 'Moshe'), 'caller-block-sure').click();
        await screener.getByTestId('caller-person-note').waitFor({ state: 'detached', timeout: 5000 });
        const refused = await call('Moshe', id);
        check('M6 the blocked caller leaves the queue and their next call is refused with a plain message', (await calls()).find((c) => c.id === again.call_id).status === 'rejected' && refused.status === 403 && /cannot take your call/.test(refused.json.detail), `${refused.status} ${JSON.stringify(refused.json)}`);

        // The list of marked callers: one priority, one blocked; lift the block
        await sleep(600);
        await T(screener, 'marked-callers-toggle').click();
        await T(screener, 'marked-caller').nth(1).waitFor({ timeout: 5000 });
        const listText = await T(screener, 'marked-callers').innerText();
        check('M7 the list shows who is priority and who is blocked, with the note', /1 priority, 1 blocked/.test(listText) && /Moshe/.test(listText) && /Long winded/.test(listText) && /Dovid from Monsey/.test(listText), listText.replace(/\n/g, ' / '));
        await shot(screener, 'screener-marked');
        await T(screener, 'marked-caller').filter({ hasText: 'Moshe' }).getByTestId('marked-caller-clear').click();
        await T(screener, 'marked-caller').nth(1).waitFor({ state: 'detached', timeout: 5000 });
        const back = await call('Moshe', id);
        await T(card(screener, 'Moshe'), 'caller-history-line').waitFor({ timeout: 8000 });
        const lineBack = await T(card(screener, 'Moshe'), 'caller-history-line').innerText();
        check('M8 "Allow again" lets them call; the card counts both earlier calls and keeps the note', back.status === 201 && /^Called 2 times before, on air once/.test(lineBack) && /Long winded/.test(await T(card(screener, 'Moshe'), 'caller-person-note').innerText()), `${back.status} ${lineBack}`);

        // Phone size
        const small = await open(`/admin/live/${show.id}/screen`, { ...devices['iPhone 13'] });
        await T(small, 'caller-history').first().waitFor({ timeout: 15000 });
        const m = await small.evaluate(() => ({ scroll: document.documentElement.scrollWidth - window.innerWidth, low: [...document.querySelectorAll('[data-testid="caller-history"] button')].map((b) => Math.round(b.getBoundingClientRect().height)).filter((h) => h < 24) }));
        check('M9 on a phone nothing scrolls sideways and the buttons can be tapped', m.scroll <= 0 && m.low.length === 0, JSON.stringify(m));
        await shot(small, 'screener-history-phone');
        check('M10 no errors or unexpected dialogs on any page', problems.length === 0, problems.slice(0, 4).join(' | '));
        for (const mk of (await api('GET', '/caller-profiles')).json.callers) await api('PUT', `/caller-profiles/${mk.id}`, { standing: 'normal' });
        await api('POST', `/live-shows/${show.id}/end`);
      } finally {
        await browser.close();
      }
    }
    // ---------- Part N: a locked call cannot be dropped by a slip of the hand ----------
    if (!ONLY || ONLY === 'lock') {
      await setConfig({ live_delay_seconds: 0, live_host_gone_seconds: 0 });
      const show = await newShow('Delay test N');
      await api('POST', `/live-shows/${show.id}/start`);
      const browser = await chromium.launch();
      const problems = [];
      const T = (page, id) => page.getByTestId(id);
      const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
      const open = async (url, opts = {}) => {
        const page = await (await browser.newContext(opts)).newPage();
        await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
        page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|409|dynamically imported module|MIME type/.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
        page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
        page.on('dialog', (d) => { problems.push(`${url}: dialog "${d.message()}"`); d.dismiss(); });
        await page.goto(`${SITE}${url}`);
        return page;
      };
      const call = async (name) => (await fetch(`${API}/call-in/${STATION}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name }) }).then((x) => x.json())).call_id;
      const statusOf = async (id) => ((await api('GET', `/live-shows/${show.id}/calls`)).json.calls.find((c) => c.id === id) || {}).status;
      try {
        const guest = await call('Rabbi Guest'), avi = await call('Avi'), berel = await call('Berel');
        for (const id of [guest, avi, berel]) await api('POST', `/live-shows/${show.id}/calls/${id}/approve`);
        for (const id of [guest, avi]) await api('POST', `/live-shows/${show.id}/calls/${id}/on-air?force=true`);
        const host = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 900 } });
        const screener = await open(`/admin/live/${show.id}/screen`, { viewport: { width: 1440, height: 900 } });
        const cardOf = (page, name) => T(page, 'on-air-card').filter({ hasText: name });
        await cardOf(host, 'Rabbi Guest').waitFor({ timeout: 15000 });
        await cardOf(screener, 'Rabbi Guest').waitFor({ timeout: 15000 });
        await sleep(800);
        await T(cardOf(host, 'Rabbi Guest'), 'lock-button').click();
        await T(cardOf(host, 'Rabbi Guest'), 'locked-badge').waitFor({ timeout: 5000 });
        await T(cardOf(screener, 'Rabbi Guest'), 'locked-badge').waitFor({ timeout: 5000 });
        const g = cardOf(host, 'Rabbi Guest');
        check('N1 Lock on the Host Console: a LOCKED badge on both screens, End Call and Hold greyed out, the button reads Unlock',
          await T(g, 'end-call-button').isDisabled() && await T(g, 'hold-button').isDisabled() && await T(g, 'lock-button').innerText() === 'Unlock'
          && await T(cardOf(screener, 'Rabbi Guest'), 'end-call-button').isDisabled() && await T(cardOf(host, 'Avi'), 'end-call-button').isEnabled());
        await shot(host, 'host-locked');
        const refused = await api('POST', `/live-shows/${show.id}/calls/${guest}/end-call`);
        check('N2 the server refuses to end a locked call too', refused.status === 409 && /locked/.test(refused.json.detail) && await statusOf(guest) === 'on_air', `${refused.status} ${JSON.stringify(refused.json)}`);
        await T(host, 'next-caller-button').click();
        await cardOf(host, 'Berel').waitFor({ timeout: 6000 });
        await cardOf(host, 'Avi').waitFor({ state: 'detached', timeout: 6000 }).catch(() => {});
        check('N3 Next caller ends the other caller and brings the next one; the locked guest stays on air', await statusOf(guest) === 'on_air' && await statusOf(avi) === 'completed' && await statusOf(berel) === 'on_air' && await T(host, 'on-air-card').count() === 2, [await statusOf(guest), await statusOf(avi), await statusOf(berel)].join(','));
        // Unlock from the screener; then End Call works
        await T(cardOf(screener, 'Rabbi Guest'), 'lock-button').click();
        await T(cardOf(host, 'Rabbi Guest'), 'locked-badge').waitFor({ state: 'detached', timeout: 5000 });
        await T(cardOf(host, 'Rabbi Guest'), 'end-call-button').click();
        await cardOf(host, 'Rabbi Guest').waitFor({ state: 'detached', timeout: 5000 });
        check('N4 unlocked from the Call Screener, End Call works again', await statusOf(guest) === 'completed');
        const small = await open(`/admin/live/${show.id}/host`, { ...devices['iPhone 13'] });
        await T(small, 'lock-button').first().waitFor({ timeout: 15000 });
        const m = await small.evaluate(() => ({ scroll: document.documentElement.scrollWidth - window.innerWidth, h: Math.round(document.querySelector('[data-testid="lock-button"]').getBoundingClientRect().height) }));
        check('N5 on a phone the Lock button fits beside Hold and End Call', m.scroll <= 0 && m.h >= 32, JSON.stringify(m));
        await shot(small, 'host-lock-phone');
        check('N6 no errors or unexpected dialogs on any page', problems.length === 0, problems.slice(0, 4).join(' | '));
        await api('POST', `/live-shows/${show.id}/end`);
      } finally {
        await browser.close();
      }
    }
    // ---------- Part O: a co-host joins from another place ----------
    if (!ONLY || ONLY === 'cohost') {
      await setConfig({ live_delay_seconds: 0, live_host_gone_seconds: 0 });
      const DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'cohost-'));
      { const rate = 48000, n = rate * 60, buf = Buffer.alloc(44 + n * 2);
        buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8);
        buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
        buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32);
        buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
        for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 880 * i / rate)), 44 + i * 2);
        fs.writeFileSync(path.join(DIR, 'cohost.wav'), buf); }
      const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${path.join(DIR, 'cohost.wav')}`, '--autoplay-policy=no-user-gesture-required'] });
      const problems = [];
      const T = (page, id) => page.getByTestId(id);
      const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
      const open = async (url, opts = {}) => {
        const page = await (await browser.newContext(opts)).newPage();
        await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
        page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|409|dynamically imported module|MIME type/.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
        page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
        page.on('dialog', (d) => { problems.push(`${url}: dialog "${d.message()}"`); d.dismiss(); });
        await page.goto(`${SITE}${url}`);
        return page;
      };
      const P = (l, f, from, to) => power(l.frames.filter((x) => x.at >= from && x.at < to).flatMap((x) => Array.from(x.pcm)), f);
      let mic;
      try {
        const show = await newShow('Delay test O');
        await api('POST', `/live-shows/${show.id}/start`);
        const l = listener();
        mic = hostMic(show.id, token);
        mic.ws.addEventListener('message', (e) => { if (typeof e.data !== 'string') mic.back.push({ at: Date.now(), pcm: new Int16Array(e.data) }); });
        await mic.ready;
        const watcher = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 900 } });
        const co = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 900 } });
        await T(co, 'cohost-join').waitFor({ timeout: 15000 });
        await T(watcher, 'cohost-names').waitFor({ timeout: 15000 });
        check('O1 the Host Console offers Join as co-host and says nobody is co-hosting', /No co-host on the show/.test(await T(co, 'cohost-names').innerText()) && await T(co, 'cohost-join').isEnabled());
        await sleep(2000);
        const t0 = Date.now();
        await sleep(1500);
        const before = P(l, 880, t0, Date.now());
        await T(co, 'cohost-join').click();
        await T(co, 'cohost-on').waitFor({ timeout: 8000 });
        await watcher.getByText(/On the show with the host: /).waitFor({ timeout: 5000 });
        check('O2 joined: the page says so, the host cannot also be started here, and another screen names the co-host', /You are on the show as a co-host\. Listeners hear you/.test(await T(co, 'cohost-on').innerText()) && await co.getByTestId('mic-on-button').isDisabled() && /On the show with the host: \S+/.test(await T(watcher, 'cohost-names').innerText()), await T(watcher, 'cohost-names').innerText());
        await sleep(2500);
        const t1 = Date.now();
        await sleep(2500);
        const t2 = Date.now();
        const withCo = P(l, 880, t1, t2), hostTone = P(l, 440, t1, t2);
        check('O3 listeners hear the co-host together with the host', before < 0.001 && withCo > 0.002 && hostTone > 0.002, `880 Hz before ${before.toFixed(4)}, with co-host ${withCo.toFixed(4)}; host 440 Hz ${hostTone.toFixed(4)}`);
        const hostHears = power(mic.back.filter((x) => x.at >= t1 && x.at < t2).flatMap((x) => Array.from(x.pcm)), 880);
        check('O4 the host hears the co-host', hostHears > 0.002, hostHears.toFixed(4));
        await shot(co, 'cohost-on');
        await T(co, 'cohost-mute').click();
        await sleep(1500);
        const t3 = Date.now();
        await sleep(2000);
        const mutedP = P(l, 880, t3, Date.now());
        check('O5 Mute takes the co-host off the air while the host goes on', mutedP < 0.001 && P(l, 440, t3, Date.now()) > 0.002 && /muted/.test(await T(co, 'cohost-on').innerText()), `880 Hz ${mutedP.toFixed(4)}`);
        await T(co, 'cohost-mute').click();
        await sleep(2000);
        const t4 = Date.now();
        await sleep(2000);
        check('O6 Unmute brings them back', P(l, 880, t4, Date.now()) > 0.002, P(l, 880, t4, Date.now()).toFixed(4));
        await T(co, 'cohost-leave').click();
        await watcher.getByText('No co-host on the show.').waitFor({ timeout: 5000 });
        await sleep(1500);
        const t5 = Date.now();
        await sleep(2000);
        check('O7 Leave the show: off the air, the other screen says no co-host, Join is offered again', P(l, 880, t5, Date.now()) < 0.001 && await T(co, 'cohost-join').isEnabled());
        // The show ends while a co-host is on: their page says so
        await T(co, 'cohost-join').click();
        await T(co, 'cohost-on').waitFor({ timeout: 8000 });
        const small = await open(`/admin/live/${show.id}/host`, { ...devices['iPhone 13'] });
        await T(small, 'cohost-panel').waitFor({ timeout: 15000 });
        const m = await small.evaluate(() => ({ scroll: document.documentElement.scrollWidth - window.innerWidth, h: Math.round(document.querySelector('[data-testid="cohost-join"]').getBoundingClientRect().height) }));
        check('O8 on a phone the co-host box fits and its button is a full tap target', m.scroll <= 0 && m.h >= 44, JSON.stringify(m));
        await shot(small, 'cohost-phone');
        await api('POST', `/live-shows/${show.id}/end`);
        await co.getByText('SHOW ENDED').waitFor({ timeout: 8000 });
        await sleep(500);
        check('O9 the show ends: the co-host\'s page says SHOW ENDED and listeners no longer hear them', (await api('GET', `/live-shows/${show.id}/air`)).json.cohosts.length === 0);
        const code = await new Promise((res) => { const w = new WebSocket(`${API.replace(/^http/, 'ws')}/ws/live/${show.id}/cohost`); w.onopen = () => w.send(JSON.stringify({ type: 'auth', token })); w.onclose = (e) => res(e.code); setTimeout(() => res('timeout'), 5000); });
        check('O10 joining a show that is over is refused', code === 4009, String(code));
        check('O11 no errors or unexpected dialogs on any page', problems.length === 0, problems.slice(0, 4).join(' | '));
      } finally {
        if (mic) mic.close();
        await browser.close();
      }
    }
    // ---------- Part P: a filler plays after a Dump while the delay refills ----------
    if (!ONLY || ONLY === 'filler') {
      const DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'filler-'));
      const wav = (f, seconds, rate = 48000) => { const n = rate * seconds, buf = Buffer.alloc(44 + n * 2);
        buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8);
        buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
        buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32);
        buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
        for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * f * i / rate)), 44 + i * 2);
        return buf; };
      fs.writeFileSync(path.join(DIR, 'host.wav'), wav(440, 30));
      // The filler: a 1000 Hz "jingle" of 20 s, uploaded to the library like any item
      const NAME = `Delay test filler ${Date.now() % 100000}`;
      const jingle = wav(1000, 20, 24000);
      const form = new FormData();
      form.append('file', new Blob([jingle], { type: 'audio/wav' }), 'filler.wav');
      form.append('title', NAME); form.append('asset_type', 'jingle'); form.append('format', 'mp3');
      const up = await fetch(`${API}/assets/upload`, { method: 'POST', headers: { authorization: H.authorization }, body: form });
      const asset = await up.json();
      let server = null;
      if (/localhost|127\.0\.0\.1/.test(API)) {
        // A local backend keeps uploads on disk with no address: serve the same audio here
        server = require('http').createServer((q, r) => { r.writeHead(200, { 'content-type': 'audio/wav', 'content-length': jingle.length }); r.end(jingle); });
        await new Promise((res) => server.listen(4899, '127.0.0.1', res));
        await api('PATCH', `/assets/${asset.id}`, { file_path: 'http://127.0.0.1:4899/filler.wav' });
      }
      await setConfig({ live_delay_seconds: 6, live_dump_seconds: 0, live_dump_filler_asset_id: null, live_dump_filler_title: null });
      const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${path.join(DIR, 'host.wav')}`, '--autoplay-policy=no-user-gesture-required'] });
      const problems = [];
      const T = (page, id) => page.getByTestId(id);
      const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
      const open = async (url, opts = {}) => {
        const page = await (await browser.newContext({ permissions: ['microphone'], ...opts })).newPage();
        await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
        page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|409/.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
        page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
        await page.goto(`${SITE}${url}`);
        return page;
      };
      const heard = (l, f, from, to) => power(l.frames.filter((x) => x.at >= from && x.at < to).flatMap((x) => Array.from(x.pcm)), f);
      try {
        check('P1 the filler is uploaded to the library', up.status === 201 && !!asset.id, `${up.status}`);
        // Station settings: search the library, pick the filler, Save
        const st = await open('/admin/stations', { viewport: { width: 1440, height: 900 } });
        await st.getByText(station.name, { exact: true }).first().click();
        await T(st, 'live-filler-search').first().waitFor({ timeout: 15000 });
        await T(st, 'live-filler-search').first().fill(NAME);
        await T(st, 'live-filler-results').first().getByRole('button', { name: new RegExp(NAME) }).click();
        check('P2 station settings: the filler is found in the library and shown as chosen', (await T(st, 'live-filler-chosen').first().innerText()).includes(NAME));
        await T(st, 'live-filler').first().scrollIntoViewIfNeeded();
        await shot(st, 'filler-setting');
        await st.getByRole('button', { name: 'Save Automation Config' }).first().click();
        await sleep(1500);
        const saved = (await api('GET', `/stations/${STATION}`)).json.automation_config;
        check('P3 it is saved with the other settings kept', saved.live_dump_filler_asset_id === asset.id && saved.live_delay_seconds === 6 && Object.keys(before).every((k) => k.startsWith('live_') || JSON.stringify(saved[k]) === JSON.stringify(before[k])), JSON.stringify({ f: saved.live_dump_filler_asset_id, d: saved.live_delay_seconds }));

        const show = await newShow('Delay test P');
        const l = listener();
        const host = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 900 } });
        await T(host, 'delay-panel').waitFor({ timeout: 15000 });
        await goLive(host);
        await host.getByText('SAFE', { exact: true }).waitFor({ timeout: 14000 });
        await T(host, 'filler-note').waitFor({ timeout: 8000 });
        for (let i = 0; i < 20 && !(await air(show.id)).filler; i++) await sleep(500);
        check('P4 the Host Console says which filler plays after a Dump', (await T(host, 'filler-note').innerText()).includes(NAME), await T(host, 'filler-note').innerText());
        await sleep(1500);

        // Dump: listeners hear the filler at once (no silence), for as long as was cut
        const t0 = Date.now();
        await T(host, 'dump-button').click();
        await host.getByText('FILLER ON AIR', { exact: true }).waitFor({ timeout: 4000 });
        check('P5 after Dump the panel says FILLER ON AIR and names it', (await T(host, 'insert-now').innerText()).includes(NAME), await T(host, 'insert-now').innerText());
        await shot(host, 'filler-on-air');
        await sleep(3000);
        const j1 = heard(l, 1000, t0 + 800, t0 + 2800), h1 = heard(l, 440, t0 + 800, t0 + 2800);
        check('P6 listeners hear the filler right after the Dump, not the host and not silence', j1 > 0.02 && h1 < j1 / 5, `filler ${j1.toFixed(4)} host ${h1.toFixed(4)}`);
        await host.getByText('SAFE', { exact: true }).waitFor({ timeout: 9000 });
        const took = (Date.now() - t0) / 1000;
        const a = await air(show.id);
        check('P7 the delay is full again when the filler ends (about 6 s, not a minute)', took < 9 && a.delay >= 5.4 && a.safe === true, `${took.toFixed(1)} s, delay ${a.delay}`);
        await sleep(2500);
        const t1 = Date.now();
        const j2 = heard(l, 1000, t1 - 1800, t1), h2 = heard(l, 440, t1 - 1800, t1);
        check('P8 after the filler listeners hear the host again', h2 > 0.003 && j2 < h2 / 5, `host ${h2.toFixed(4)} filler ${j2.toFixed(4)}`);
        // A second Dump right away cuts a whole delay again
        const d2 = await api('POST', `/live-shows/${show.id}/dump`);
        check('P9 a second Dump right after cuts the whole delay again', d2.status === 200 && d2.json.dumped_seconds >= 5.4, JSON.stringify(d2.json && d2.json.dumped_seconds));
        await host.getByText('SAFE', { exact: true }).waitFor({ timeout: 10000 });

        // Phone size: the note and the state fit
        const small = await open(`/admin/live/${show.id}/screen`, { ...devices['iPhone 13'] });
        await T(small, 'filler-note').first().waitFor({ timeout: 15000 });
        const m = await small.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        check('P10 on a phone the Call Screener shows the filler note with no sideways scroll', m <= 0, `${m}`);
        await shot(small, 'filler-phone');
        await api('POST', `/live-shows/${show.id}/end`);

        // "No filler" in station settings takes it off again
        await st.reload();
        await st.getByText(station.name, { exact: true }).first().click();
        await T(st, 'live-filler-chosen').first().waitFor({ timeout: 15000 });
        check('P11 station settings show the saved filler after a reload', (await T(st, 'live-filler-chosen').first().innerText()).includes(NAME));
        await T(st, 'live-filler-clear').first().click();
        await st.getByRole('button', { name: 'Save Automation Config' }).first().click();
        await sleep(1500);
        check('P12 "No filler" and Save takes it off', !((await api('GET', `/stations/${STATION}`)).json.automation_config.live_dump_filler_asset_id));
        check('P13 no errors or unexpected dialogs on any page', problems.length === 0, problems.slice(0, 4).join(' | '));
      } finally {
        await browser.close();
        if (server) server.close();
        if (asset && asset.id) await api('DELETE', `/assets/${asset.id}`);
      }
    }
  } finally {
    await endAll();
    await api('PUT', `/stations/${STATION}`, { automation_config: before });
  }
  console.log(fails ? `\n${fails} check(s) FAILED` : '\nAll checks passed');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
