/**
 * Web call-ins, end to end with two browsers: the host (fake mic, 440 Hz) on the
 * Host Console and a caller (fake mic, 880 Hz) on /call/<station>, plus a socket
 * listening like a listener page. Checks who hears what at each step.
 * Needs a real backend and the site built against it (see wiki/live-shows.md); defaults
 * :8000 and :3000, or API_URL / BASE_URL:
 *   KBR_ADMIN_EMAIL=admin KBR_ADMIN_PASSWORD=... node e2e/web-call-in.cjs <station id>
 */
const { chromium } = require('playwright');
const fs = require('fs'), os = require('os'), path = require('path');
const DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'callin-'));
for (const [f, name] of [[440, 'host'], [880, 'caller']]) {
  const rate = 48000, n = rate * 30, buf = Buffer.alloc(44 + n * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8);
  buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * f * i / rate)), 44 + i * 2);
  fs.writeFileSync(path.join(DIR, `${name}.wav`), buf);
}
const WebSocket = globalThis.WebSocket;
const API = process.env.API_URL || 'http://localhost:8000/api/v1', SITE = process.env.BASE_URL || 'http://localhost:3000';
const STATION = process.argv[2];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, d = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d ? '  (' + d + ')' : ''}`); if (!ok) fails++; };
// Noise suppression in the browser turns a steady test tone down a lot, so levels are small
function power(samples, f, rate = 24000) { // Goertzel
  const k = 2 * Math.cos(2 * Math.PI * f / rate); let s1 = 0, s2 = 0;
  for (const x of samples) { const s0 = x / 32768 + k * s1 - s2; s2 = s1; s1 = s0; }
  return Math.sqrt(Math.max(0, s1 * s1 + s2 * s2 - k * s1 * s2)) / samples.length;
}
const tones = (pcm) => ({ host: power(pcm, 440), caller: power(pcm, 880) });
const fmt = (t) => `440 ${t.host.toFixed(3)} / 880 ${t.caller.toFixed(3)}`;
const RECORD = () => {
  const Real = window.WebSocket;
  window.__rx = {};
  window.WebSocket = class extends Real {
    constructor(url, p) {
      super(url, p);
      const key = url.includes('/audio') ? 'host' : url.includes('/ws/live/call/') ? 'caller' : null;
      if (key) this.addEventListener('message', (e) => {
        if (!(e.data instanceof ArrayBuffer)) return;
        const a = (window.__rx[key] ||= []); a.push(Array.from(new Int16Array(e.data))); if (a.length > 12) a.shift();
      });
    }
  };
};
const recent = (page, key) => page.evaluate((k) => (window.__rx[k] || []).slice(-6).flat(), key);

(async () => {
  const token = (await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: process.env.KBR_ADMIN_EMAIL || 'admin', password: process.env.KBR_ADMIN_PASSWORD }) })).json()).access_token;
  const H = { authorization: `Bearer ${token}`, 'content-type': 'application/json' };
  // End anything live on this station first
  for (const s of (await (await fetch(`${API}/live-shows?limit=100`, { headers: H })).json()).shows || [])
    if (s.status === 'live') await fetch(`${API}/live-shows/${s.id}/end`, { method: 'POST', headers: H });
  const now = new Date();
  const show = await (await fetch(`${API}/live-shows`, { method: 'POST', headers: H, body: JSON.stringify({ station_id: STATION, title: 'Call-in test', scheduled_start: now.toISOString(), scheduled_end: new Date(+now + 3600e3).toISOString(), calls_enabled: true }) })).json();

  // Listener socket
  const heard = [];
  const lws = new WebSocket(`${API.replace(/^http/, 'ws')}/ws/live/station/${STATION}/listen`);
  lws.binaryType = 'arraybuffer';
  lws.onmessage = (e) => { if (typeof e.data !== 'string') { heard.push(...new Int16Array(e.data)); if (heard.length > 48000) heard.splice(0, heard.length - 48000); } };
  const lastHeard = () => heard.slice(-12000);

  const args = (wav) => ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${path.join(DIR, wav + '.wav')}`, '--autoplay-policy=no-user-gesture-required'];
  const hostB = await chromium.launch({ args: args('host') });
  const callerB = await chromium.launch({ args: args('caller') });
  try {
    const host = await (await hostB.newContext({ permissions: ['microphone'] })).newPage();
    await host.addInitScript((t) => localStorage.setItem('access_token', t), token);
    await host.addInitScript(RECORD);
    await host.goto(`${SITE}/admin/live/${show.id}/host`);
    await host.getByText('Start Broadcasting').click();
    await sleep(3000);
    let t = tones(lastHeard());
    check('listeners hear the host', t.host > 0.003 && t.caller < 0.001, fmt(t));
    // F-HOST-1: the VU meter moves with the microphone
    const vu = await host.evaluate(() => {
      const bar = [...document.querySelectorAll('div[style*="width"]')].find((d) => d.parentElement?.className.includes('rounded-full'));
      return bar ? parseFloat(bar.style.width) : -1;
    });
    check('host VU meter moves', vu > 0, `${vu.toFixed(1)}%`);

    // F-SCREENER-1: the screener is open before the caller joins and lists them live
    const screener = await (await hostB.newContext()).newPage();
    await screener.addInitScript((tk) => localStorage.setItem('access_token', tk), token);
    await screener.goto(`${SITE}/admin/live/${show.id}/screen`);
    await screener.getByText('Waiting (0)').waitFor({ timeout: 10000 });

    const caller = await (await callerB.newContext({ permissions: ['microphone'] })).newPage();
    await caller.addInitScript(RECORD);
    await caller.goto(`${SITE}/call/${STATION}`);
    await caller.getByPlaceholder('Name').fill('Moshe');
    await caller.getByText('Call in', { exact: true }).click();
    await caller.getByText("You're in line").waitFor({ timeout: 10000 });
    check('caller page: in line', true);
    await sleep(2500);
    t = tones(await recent(caller, 'caller'));
    check('waiting caller hears the show (the host)', t.host > 0.003 && t.caller < 0.001, fmt(t));
    t = tones(lastHeard());
    check('waiting caller is not on air', t.caller < 0.001, fmt(t));

    // Screener: the caller is listed; approve, then on air
    const calls = (await (await fetch(`${API}/live-shows/${show.id}/calls`, { headers: H })).json()).calls;
    const call = calls.find((c) => c.caller_name === 'Moshe');
    check('screener lists the web caller', !!call && call.status === 'waiting', JSON.stringify(calls.map((c) => [c.caller_name, c.status])));
    const t0 = Date.now();
    await screener.getByText('Waiting (1)').waitFor({ timeout: 3000 });
    check('Call Screener lists Moshe in Waiting without a reload', await screener.getByText('Moshe').first().isVisible(), `${Date.now() - t0} ms after the API check`);
    // Approve on the screener; it moves to Approved there and on the host console
    await screener.getByRole('button', { name: 'Approve' }).first().click();
    await caller.getByText('Approved').waitFor({ timeout: 5000 });
    check('caller page: approved', true);
    await screener.getByText('Approved (1)').waitFor({ timeout: 3000 });
    check('screener: Moshe moves to Approved', await screener.getByText('Waiting (0)').isVisible());
    // F-HOST-3: the host puts the caller on air from the console's queue
    await host.getByText('Approved Queue (1)').waitFor({ timeout: 5000 });
    await host.getByRole('button', { name: 'Put On Air' }).click();
    await caller.getByText("You're on the air").waitFor({ timeout: 5000 });
    check('caller page: on the air', true);
    await host.getByText('No caller on air').waitFor({ state: 'detached', timeout: 5000 });
    check('host console: Moshe in Caller On Air', await host.getByText('ON AIR', { exact: true }).isVisible());
    await sleep(3000);
    t = tones(lastHeard());
    check('listeners hear host and caller together', t.host > 0.003 && t.caller > 0.003, fmt(t));
    t = tones(await recent(host, 'host'));
    check('host hears the caller', t.caller > 0.003 && t.host < 0.001, fmt(t));
    t = tones(await recent(caller, 'caller'));
    check('caller hears the host, not themselves', t.host > 0.003 && t.caller < 0.001, fmt(t));

    await caller.getByText('Hang up').click();
    await caller.getByText('Your call has ended').waitFor({ timeout: 5000 });
    await sleep(2500);
    t = tones(lastHeard());
    check('after hanging up only the host is heard', t.host > 0.003 && t.caller < 0.001, fmt(t));
    await sleep(16000);
    const after = (await (await fetch(`${API}/live-shows/${show.id}/calls`, { headers: H })).json()).calls.find((c) => c.id === call.id);
    check('the call is closed after the caller leaves', after.status === 'completed', after.status);
    // F-HOST-1: Stop Broadcasting takes the host off the air (listeners are told)
    const offAir = new Promise((res) => {
      const f = (e) => { if (typeof e.data === 'string' && JSON.parse(e.data).live === false) { lws.removeEventListener('message', f); res(true); } };
      lws.addEventListener('message', f); setTimeout(() => res(false), 5000);
    });
    await host.getByText('Stop Broadcasting').click();
    check('Stop Broadcasting: listeners are told the host is off air', await offAir);
    check('Start Broadcasting is back', await host.getByText('Start Broadcasting').isVisible());
    await fetch(`${API}/live-shows/${show.id}/end`, { method: 'POST', headers: H });
  } finally {
    lws.close(); await hostB.close(); await callerB.close();
  }
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
