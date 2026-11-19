/**
 * Listen page during a live show, against a fake backend (e2e/lib/fake-station.mjs):
 * the page's volume and mute carry over to the host's audio (F-LISTEN-40), and the
 * live-audio socket reconnects with back-off 1, 2, 4, 8, 15 s after it drops, the
 * music being allowed again meanwhile (F-LISTEN-45); the rating widget during a show
 * (F-LISTEN-30, a known bug: XFAIL); a 5 s time check handed over on the second
 * (F-HANDOVER-8). Parts (ONLY=): handover live rating.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4301 --strictPort &)
 *   BASE_URL=http://localhost:4301 node e2e/listen-rows.mjs
 *
 * Exits non-zero if any check fails. Takes about a minute.
 */
import {
  chromium, checker, sleep, makeStation, routeApi, routeNowPlaying, AUDIO_PROBE, timeUntil, routeSiteSocket, watchDecks,
} from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4301';
const { check, done } = checker({
  'rating widget hidden during the live show': 'found 2026-11-19: the widget follows now-playing (isWsPlaying), which keeps the last song during a show, so it shows over the live show',
});

// The live-audio socket: counted, and refused (closed before it opens, as when the
// server cannot be reached) once window.__liveRefuse is set. The host's audio
// context is noted (window.__liveCtx) when the first frame is scheduled.
function LIVE_PROBE() {
  window.__liveConnects = [];
  window.__liveRefuse = false;
  // (Playwright's socket routing puts its own WebSocket in place too: wrap whichever
  // is current)
  let Inner = window.WebSocket;
  function Probe(url, protocols) {
    if (/\/ws\/live\/station\//.test(String(url))) {
      window.__liveConnects.push(performance.now());
      if (window.__liveRefuse) {
        const fake = { readyState: 0, binaryType: 'blob', send() {}, close() {}, onopen: null, onclose: null, onmessage: null };
        setTimeout(() => { fake.readyState = 3; fake.onclose?.({ code: 1006 }); }, 30);
        return fake;
      }
    }
    return new Inner(url, protocols);
  }
  Object.assign(Probe, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  Probe.prototype = Inner.prototype;
  Object.defineProperty(window, 'WebSocket', {
    configurable: true,
    get: () => Probe,
    set: (v) => { Inner = v; Probe.prototype = v.prototype; },
  });
  window.__liveFrames = 0;
  const cbs = AudioContext.prototype.createBufferSource;
  AudioContext.prototype.createBufferSource = function (...a) { window.__liveFrames++; window.__liveCtx = this; return cbs.apply(this, a); };
  // The host's output level: the speaker gain in the live audio context
  window.__liveLevel = () => {
    const g = (window.__outs || []).filter((x) => x.context === window.__liveCtx).pop();
    return g ? +g.gain.value.toFixed(3) : null;
  };
}

const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
async function liveShowAudio() {
  const tag = 'listen live show';
  const station = makeStation({ shows: [{ id: 'ls9', station_id: 'st-1', title: 'Motzei Shabbos Live', status: 'live' }] });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await ctx.addInitScript(AUDIO_PROBE);
  await ctx.addInitScript(LIVE_PROBE);
  await routeApi(ctx, station);
  await routeNowPlaying(ctx, station);
  await routeSiteSocket(ctx);
  const sockets = [];
  const frame = Buffer.alloc(4800); // 0.1 s of 16-bit mono at 24 kHz
  for (let i = 0; i < 2400; i++) frame.writeInt16LE(Math.round(8000 * Math.sin(2 * Math.PI * 300 * i / 24000)), i * 2);
  // Registered after the context's catch-all, so it answers the listen socket
  await ctx.routeWebSocket(/\/ws\/live\/station\/[^/]+\/listen/, (ws) => {
    const e = { ws, open: true, timer: null };
    sockets.push(e);
    ws.onClose(() => { e.open = false; clearInterval(e.timer); });
    ws.send(JSON.stringify({ type: 'live_state', live: true, sample_rate: 24000 }));
    e.timer = setInterval(() => { if (e.open) { try { ws.send(frame); } catch { /* closed */ } } }, 100);
  });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${BASE}/listen/${station.SID}`);
  await page.getByText('LIVE: Motzei Shabbos Live').waitFor({ timeout: 5000 });
  await page.getByRole('button', { name: 'Listen Live' }).click();
  const heard = await timeUntil(page, '() => window.__liveFrames > 0 && window.__liveLevel() !== null', 3000);
  check(`${tag}: the host is heard after Listen`, heard >= 0, `${heard} ms`);
  await page.getByRole('slider', { name: 'Volume' }).waitFor({ timeout: 4000 });

  // F-LISTEN-40: volume and mute carry over to the host's audio
  const slider = page.getByRole('slider', { name: 'Volume' });
  await slider.fill('0.3');
  await sleep(200);
  const at30 = await page.evaluate(() => window.__liveLevel());
  check(`${tag}: the volume slider sets the host's level (F-LISTEN-40)`, at30 === 0.3, `host level ${at30} at volume 30%`);
  await page.getByRole('button', { name: 'Mute' }).click();
  await sleep(200);
  const muted = await page.evaluate(() => window.__liveLevel());
  check(`${tag}: Mute silences the host too (F-LISTEN-40)`, muted === 0, `host level ${muted}`);
  await page.getByRole('button', { name: 'Unmute' }).click();
  await sleep(200);
  const back = await page.evaluate(() => window.__liveLevel());
  check(`${tag}: Unmute brings the host back at the chosen volume (F-LISTEN-40)`, back === 0.3, `host level ${back}`);
  const musicDuringShow = await page.evaluate(() => window.__audible().length);
  check(`${tag}: no music while the show is heard`, musicDuringShow === 0, `${musicDuringShow} decks audible`);

  // F-LISTEN-45: the live socket drops and the server cannot be reached for a while
  const before = await page.evaluate(() => window.__liveConnects.length);
  const t0 = await page.evaluate(() => { window.__liveRefuse = true; return performance.now(); });
  sockets.forEach((e) => { if (e.open) { e.open = false; e.ws.close({ code: 1011 }).catch(() => {}); } });
  await sleep(2000);
  const staleMusic = await page.evaluate(() => window.__audible().length);
  check(`${tag}: right after the drop the music waits for a fresh now-playing (F-LISTEN-45)`, staleMusic === 0, `${staleMusic} decks audible`);
  station.push(station.nowPlaying());
  const music = await timeUntil(page, '() => window.__audible().length === 1', 4000);
  check(`${tag}: while the live socket is down the music is allowed again, over a show still on air (F-LISTEN-45, as built: decide)`,
    music >= 0, music >= 0 ? `music after ${music} ms` : 'no music');
  await sleep(30500);
  const tries = await page.evaluate(([n, t0]) => window.__liveConnects.slice(n).map((t) => (t - t0) / 1000), [before, t0]);
  const gaps = tries.map((t, i) => +(t - (i ? tries[i - 1] : 0)).toFixed(2));
  const want = [1, 2, 4, 8, 15];
  check(`${tag}: reconnects with back-off 1, 2, 4, 8, 15 s (F-LISTEN-45)`,
    gaps.length >= 5 && want.every((w, i) => Math.abs(gaps[i] - w) < 0.4),
    `gaps ${gaps.join(', ')} s`);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// F-LISTEN-30, live-show part: a logged-in listener sees no rating widget while the
// host is on air (the now-playing record still names the last song)
async function ratingDuringShow() {
  const tag = 'listen rating';
  const station = makeStation({ shows: [{ id: 'ls9', station_id: 'st-1', title: 'Motzei Shabbos Live', status: 'live' }] });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await ctx.addInitScript(AUDIO_PROBE);
  await ctx.addInitScript(LIVE_PROBE);
  await routeApi(ctx, station);
  await routeNowPlaying(ctx, station);
  await routeSiteSocket(ctx);
  await ctx.routeWebSocket(/\/ws\/live\/station\/[^/]+\/listen/, (ws) => {
    ws.send(JSON.stringify({ type: 'live_state', live: true, sample_rate: 24000 }));
  });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/listen/${station.SID}`);
  await page.getByText('LIVE: Motzei Shabbos Live').waitFor({ timeout: 5000 });
  await page.getByPlaceholder('Enter 6-digit PIN').fill('123456');
  await page.getByRole('button', { name: 'Login' }).click();
  await page.getByText('Welcome, Test Listener!').waitFor({ timeout: 3000 });
  await page.getByRole('button', { name: 'Listen Live' }).click();
  await timeUntil(page, '() => window.__liveFrames >= 0 && document.body.innerText.includes("Stop")', 3000);
  await sleep(1000);
  const widgets = await page.getByRole('button', { name: 'Rate 1 star' }).count();
  check(`${tag}: rating widget hidden during the live show (F-LISTEN-30)`, widgets === 0, `${widgets} rating widget(s) shown`);
  await ctx.close();
}

// F-HANDOVER-8 on the Listen page: a 5 s time check is heard whole, the next item
// starts within 0.3 s of its end and they overlap under 0.1 s. The server moves on
// 0.3 s before the time check's end (its hand-over rule for short items).
async function listenHandover() {
  const tag = 'listen handover';
  const station = makeStation({ shows: [] });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await ctx.addInitScript(AUDIO_PROBE);
  await routeApi(ctx, station);
  await routeNowPlaying(ctx, station);
  await routeSiteSocket(ctx);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${BASE}/listen/${station.SID}`);
  await page.getByText('On Air', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Listen Live' }).click();
  const songId = station.playing().asset_id;
  await timeUntil(page, `() => window.__audible().some(d => d.src === '${songId}')`, 4000);
  await sleep(1500);
  const tcStart = Date.now();
  station.goOnAir('tc', { started_at: new Date(tcStart).toISOString() });
  const nextId = station.pending()[0].asset_id;
  const serverEnd = tcStart + (station.byId.tc.duration - 0.3) * 1000;
  setTimeout(() => station.goOnAir(nextId, { started_at: new Date(serverEnd).toISOString() }), serverEnd - Date.now());
  const samples = await watchDecks(page, 7500, 25);
  const on = (s, id, min = 0.05) => s.decks.some((d) => d.src === id && d.playing && d.gain > min);
  const at = (s, id) => s.decks.find((d) => d.src === id && d.playing);
  const first = samples.find((s) => on(s, 'tc'));
  const tMax = Math.max(...samples.map((s) => at(s, 'tc')?.t ?? 0));
  check(`${tag}: the time check is heard whole (F-HANDOVER-8)`, !!first && at(first, 'tc').t < 0.6 && tMax >= 4.75,
    `from ${first ? at(first, 'tc').t : '-'} s to ${tMax} s of 5 s`);
  const tcOut = [...samples].reverse().find((s) => on(s, 'tc'));
  const nextIn = samples.find((s) => on(s, nextId));
  const gap = tcOut && nextIn ? (nextIn.at - tcOut.at) / 1000 : null;
  check(`${tag}: the next item starts within 0.3 s of its end (F-HANDOVER-8)`, gap !== null && gap <= 0.3,
    gap === null ? 'next item never heard' : `${gap.toFixed(2)} s after the time check was last heard`);
  let both = 0;
  for (let i = 1; i < samples.length; i++) if (on(samples[i], 'tc', 0.2) && on(samples[i], nextId, 0.2)) both += (samples[i].at - samples[i - 1].at) / 1000;
  check(`${tag}: overlap under 0.1 s (F-HANDOVER-8)`, both < 0.1, `${both.toFixed(3)} s together`);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

try {
  if (!process.env.ONLY || process.env.ONLY === 'handover') await listenHandover();
  if (!process.env.ONLY || process.env.ONLY === 'live') await liveShowAudio();
  if (!process.env.ONLY || process.env.ONLY === 'rating') await ratingDuringShow();
} finally {
  await browser.close();
}
process.exit(done());
