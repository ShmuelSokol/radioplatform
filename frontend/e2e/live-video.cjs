/**
 * Live video of a show for people on the website, on a real backend (Postgres) with a
 * fake camera and microphone in Chromium:
 *   node e2e/live-video.cjs <station id>
 * Needs KBR_ADMIN_PASSWORD (and KBR_ADMIN_EMAIL), the site on BASE_URL (default
 * http://localhost:3000) and the API on API_URL (default http://localhost:8000/api/v1).
 * SHOTS_DIR=<folder> keeps screenshots. Changes the station's live delay for the run
 * and puts it back. Use a test station: the show takes the station over.
 */
const { chromium, devices } = require('playwright');
const fs = require('fs'), path = require('path');
const WebSocket = globalThis.WebSocket;
const API = process.env.API_URL || 'http://localhost:8000/api/v1', SITE = process.env.BASE_URL || 'http://localhost:3000';
const STATION = process.argv[2];
const SHOTS = process.env.SHOTS_DIR;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, d = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d !== '' ? '  (' + d + ')' : ''}`); if (!ok) fails++; };

/** A watching page's socket: every message with the time it arrived */
function watcher() {
  const w = { frames: [], states: [], ws: new WebSocket(`${API.replace(/^http/, 'ws')}/ws/live/station/${STATION}/watch`) };
  w.ws.binaryType = 'arraybuffer';
  w.ws.onmessage = (e) => {
    if (typeof e.data === 'string') { w.states.push({ ...JSON.parse(e.data), at: Date.now() }); return; }
    const b = new Uint8Array(e.data);
    w.frames.push({ at: Date.now(), key: (b[0] & 1) === 1, size: b.length - 1 });
  };
  w.since = (t) => w.frames.filter((f) => f.at >= t);
  return w;
}

(async () => {
  if (!STATION) { console.error('usage: node e2e/live-video.cjs <station id>'); process.exit(2); }
  const token = (await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: process.env.KBR_ADMIN_EMAIL || 'admin', password: process.env.KBR_ADMIN_PASSWORD }) })).json()).access_token;
  const H = { authorization: `Bearer ${token}`, 'content-type': 'application/json' };
  const api = async (method, url, body) => { const r = await fetch(`${API}${url}`, { method, headers: H, body: body ? JSON.stringify(body) : undefined }); return { status: r.status, json: await r.json().catch(() => null) }; };
  const station = (await api('GET', `/stations/${STATION}`)).json;
  const before = station.automation_config || {};
  const endAll = async () => { for (const s of ((await api('GET', '/live-shows?limit=100')).json.shows || [])) if (s.status === 'live' && s.station_id === STATION) await api('POST', `/live-shows/${s.id}/end`); };
  await endAll();
  await api('PUT', `/stations/${STATION}`, { automation_config: { ...before, live_delay_seconds: 4, live_dump_seconds: 0, live_dump_filler_asset_id: null } });
  const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream', '--autoplay-policy=no-user-gesture-required'] });
  const problems = [];
  const T = (page, id) => page.getByTestId(id);
  const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, name + '.png'), fullPage: true }); } };
  const open = async (url, opts = {}, signedIn = true) => {
    const page = await (await browser.newContext({ permissions: ['microphone', 'camera'], ...opts })).newPage();
    if (signedIn) await page.addInitScript((t) => localStorage.setItem('access_token', t), token);
    page.on('console', (m) => { if (m.type() === 'error' && !/favicon|ERR_CONNECTION|WebSocket|409|404/.test(m.text())) problems.push(`${url}: ${m.text().slice(0, 200)}`); });
    page.on('pageerror', (e) => problems.push(`${url}: ${e.message}`));
    await page.goto(`${SITE}${url}`);
    return page;
  };
  /** What the canvas shows: its size, how much of it is not black, and a fingerprint */
  const picture = (page) => page.evaluate(() => {
    const c = document.querySelector('[data-testid="live-video-canvas"]');
    if (!c || !c.width) return null;
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let lit = 0, sum = 0;
    for (let i = 0; i < d.length; i += 4 * 97) { if (d[i] + d[i + 1] + d[i + 2] > 40) lit++; sum = (sum * 31 + d[i] + d[i + 1] * 3 + d[i + 2] * 7) % 1000000007; }
    return { w: c.width, h: c.height, lit: lit / (d.length / (4 * 97)), sum };
  });
  try {
    const now = new Date();
    const show = (await api('POST', '/live-shows', { station_id: STATION, title: 'Video test', scheduled_start: now.toISOString(), scheduled_end: new Date(+now + 3600e3).toISOString(), calls_enabled: false })).json;
    const w = watcher();
    await sleep(500);
    check('V1 before the show a watching page is told there is no picture', w.states.length === 1 && w.states[0].on === false, JSON.stringify(w.states));

    const host = await open(`/admin/live/${show.id}/host`, { viewport: { width: 1440, height: 1000 } });
    await T(host, 'camera-panel').waitFor({ timeout: 15000 });
    check('V2 the camera cannot start before the microphone is on the show', await T(host, 'camera-start').isDisabled() && /Go on air first/.test(await T(host, 'camera-help').innerText()));
    await host.getByText('Start Broadcasting').click();
    await host.getByText('SAFE', { exact: true }).waitFor({ timeout: 12000 });
    await sleep(1500);
    const t0 = Date.now();
    await T(host, 'camera-start').click();
    await T(host, 'camera-on').waitFor({ timeout: 8000 });
    check('V3 Start camera: the host sees the preview and is told it is on the website with the delay', await T(host, 'camera-preview').count() === 1 && /on the website, 4 s later/.test(await T(host, 'camera-on').innerText()), await T(host, 'camera-on').innerText());
    await shot(host, 'camera-on');
    await sleep(7000);
    const on = w.states.find((s) => s.on);
    check('V4 the watching page is told the picture is on and how to decode it', !!on && /^(vp8|avc1)/.test(on.codec) && on.width >= 320 && on.height >= 180, JSON.stringify(on));
    const first = w.frames[0];
    check('V5 the picture reaches a watcher the delay later, not at once (4 s)', !!first && first.at - t0 > 3300 && first.at - t0 < 6500, first ? `${((first.at - t0) / 1000).toFixed(1)} s` : 'no frame');
    check('V6 the first frame is a keyframe and about 15 frames a second follow', !!first && first.key && w.since(Date.now() - 2000).length >= 20 && w.since(Date.now() - 2000).length <= 40, `${w.since(Date.now() - 2000).length} frames in 2 s`);

    // A listener's page: Listen, then Watch
    const page = await open(`/listen/${STATION}`, { viewport: { width: 1280, height: 900 } }, false);
    await page.getByRole('button', { name: 'Listen Live' }).click();
    await T(page, 'live-video-watch').waitFor({ timeout: 10000 });
    check('V7 the Listen page offers "Watch the show live" and loads no video before it is pressed', await T(page, 'live-video-canvas').count() === 0);
    await T(page, 'live-video-watch').click();
    await T(page, 'live-video-canvas').waitFor({ timeout: 5000 });
    await T(page, 'live-video-wait').waitFor({ state: 'detached', timeout: 8000 });
    const p1 = await picture(page);
    await sleep(1500);
    const p2 = await picture(page);
    check('V8 the picture shows and moves', !!p1 && !!p2 && p1.lit > 0.5 && p1.sum !== p2.sum && p1.w >= 320, JSON.stringify({ p1, p2 }));
    await shot(page, 'listen-video');

    // Dump: the picture of the cut seconds goes too and restarts on a keyframe
    await sleep(2500);
    const t1 = Date.now();
    const d = await api('POST', `/live-shows/${show.id}/dump`);
    await sleep(2500);
    const after = w.since(t1 + 150);
    check('V9 Dump cuts the picture with the sound: it carries on from a keyframe', d.status === 200 && d.json.dumped_seconds >= 3.5 && after.length > 5 && after[0].key, JSON.stringify({ cut: d.json && d.json.dumped_seconds, frames: after.length, key: after[0] && after[0].key }));
    const p3 = await picture(page);
    await sleep(1200);
    const p4 = await picture(page);
    check('V10 the listener\'s picture keeps moving after the Dump', !!p3 && !!p4 && p3.sum !== p4.sum);

    // Phone size
    const small = await open(`/listen/${STATION}`, { ...devices['iPhone 13'] }, false);
    await small.getByRole('button', { name: 'Listen Live' }).click();
    await T(small, 'live-video-watch').waitFor({ timeout: 10000 });
    await T(small, 'live-video-watch').click();
    await T(small, 'live-video-wait').waitFor({ state: 'detached', timeout: 8000 });
    const m = await small.evaluate(() => ({ scroll: document.documentElement.scrollWidth - window.innerWidth, w: Math.round(document.querySelector('[data-testid="live-video-canvas"]').getBoundingClientRect().width) }));
    check('V11 on a phone the video fills the width with no sideways scroll', m.scroll <= 0 && m.w >= 300, JSON.stringify(m));
    await shot(small, 'listen-video-phone');
    await T(small, 'live-video-hide').click();
    check('V12 Hide the video takes it away and the Watch button is back', await T(small, 'live-video-canvas').count() === 0 && await T(small, 'live-video-watch').count() === 1);

    // Stop camera: pages lose the Watch offer; the show goes on
    await T(host, 'camera-stop').click();
    await T(page, 'live-video').waitFor({ state: 'detached', timeout: 6000 });
    const off = w.states[w.states.length - 1];
    check('V13 Stop camera: watchers are told the picture is off and the Listen page drops the video', off.on === false && await T(host, 'camera-start').isEnabled());
    const a = (await api('GET', `/live-shows/${show.id}/air`)).json;
    check('V14 the show itself is still on air', a.on_air === true, a.mode);

    // Camera on again, then Stop Broadcasting ends the picture too
    await T(host, 'camera-start').click();
    await T(host, 'camera-on').waitFor({ timeout: 8000 });
    await T(page, 'live-video-canvas').waitFor({ timeout: 12000 });
    await host.getByText('Stop Broadcasting').click();
    await T(host, 'camera-on').waitFor({ state: 'detached', timeout: 5000 });
    check('V15 the camera stops with the microphone', await T(host, 'camera-preview').count() === 0);
    await api('POST', `/live-shows/${show.id}/end`);
    await sleep(1500);
    check('V16 after the show a watcher has no picture', w.states[w.states.length - 1].on === false);
    const bad = await new Promise((res) => { const x = new WebSocket(`${API.replace(/^http/, 'ws')}/ws/live/${show.id}/video`); x.onopen = () => x.send(JSON.stringify({ type: 'auth', token })); x.onclose = (e) => res(e.code); setTimeout(() => res('timeout'), 5000); });
    check('V17 a camera for a show that is not on air is refused (4009)', bad === 4009, `${bad}`);
    check('V18 no errors or unexpected dialogs on any page', problems.length === 0, problems.slice(0, 4).join(' | '));
  } finally {
    await browser.close();
    await endAll();
    await api('PUT', `/stations/${STATION}`, { automation_config: before });
  }
  console.log(fails ? `\n${fails} check(s) FAILED` : '\nAll checks passed');
  process.exit(fails ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
