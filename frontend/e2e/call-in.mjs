/**
 * Browser check for the web call-in page (/call/:station) against a fake backend and
 * a fake call socket: no show / calls closed, microphone refused, the status line
 * through screening to on air, rejected, a dropped connection that comes back with
 * the call held, the mic meter, and Hang up. Uses Chromium's fake microphone.
 * (e2e/web-call-in.cjs is the end-to-end run against a real backend.)
 *
 *   BASE_URL=http://localhost:4311 node e2e/call-in.mjs
 */
import { chromium, devices, checker, sleep, makeStation, routeApi, routeSiteSocket, timeUntil } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const { check, done } = checker({});

async function openCallIn(browser, contextOpts, station, { denyMic = false } = {}) {
  const ctx = await browser.newContext({ ...contextOpts, permissions: denyMic ? [] : ['microphone'] });
  if (denyMic) await ctx.addInitScript(() => { navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('denied', 'NotAllowedError')); });
  await routeApi(ctx, station);
  await routeSiteSocket(ctx);
  const call = { sockets: [], auths: [] };
  await ctx.routeWebSocket(/\/ws\/live\/call\//, (ws) => {
    const entry = { ws, open: true, url: ws.url() };
    call.sockets.push(entry);
    ws.onMessage((m) => { if (typeof m === 'string') { try { const j = JSON.parse(m); if (j.type === 'auth') call.auths.push(j.token); } catch { /* audio */ } } });
    ws.onClose(() => { entry.open = false; });
  });
  call.status = (status) => call.sockets.filter((s) => s.open).forEach((s) => s.ws.send(JSON.stringify({ type: 'call_status', status })));
  call.drop = () => call.sockets.filter((s) => s.open).forEach((s) => { s.open = false; s.ws.close({ code: 1011 }).catch(() => {}); });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${BASE}/call/${station.SID}`);
  return { ctx, page, errors, call };
}

function stationWith(show) {
  const st = makeStation({
    shows: show ? [show] : [],
    extra: (method, path, query, body) => {
      if (method === 'POST' && path === `/call-in/st-1`) { st.callBody = body; return { call_id: 'call-1', token: 'tok-1' }; }
      return undefined;
    },
  });
  return st;
}

async function run(browser, contextOpts, tag) {
  // No show on
  {
    const st = stationWith(null);
    const { ctx, page } = await openCallIn(browser, contextOpts, st);
    await page.getByText('There is no live show on right now', { exact: false }).waitFor({ timeout: 5000 }).catch(() => {});
    check(`${tag}: no live show -> says so`, await page.getByText('There is no live show on right now', { exact: false }).count() === 1);
    st.live.shows.push({ id: 'ls5', station_id: 'st-1', title: 'Ask the Rav', status: 'live', calls_enabled: true });
    const ms = await timeUntil(page, `() => document.body.innerText.includes('Live now: Ask the Rav')`, 17000);
    check(`${tag}: the show appears within one 15 s poll`, ms >= 0, `${ms} ms`);
    await ctx.close();
  }
  // Calls closed
  {
    const st = stationWith({ id: 'ls5', station_id: 'st-1', title: 'Ask the Rav', status: 'live' });
    const base = st.handle;
    st.handle = (m, p, q, b) => (p === '/live-shows/station/st-1/active' ? { active: true, show: { id: 'ls5', title: 'Ask the Rav', calls_enabled: false } } : base(m, p, q, b));
    const { ctx, page } = await openCallIn(browser, contextOpts, st);
    await page.getByText('is not taking calls right now', { exact: false }).waitFor({ timeout: 5000 }).catch(() => {});
    check(`${tag}: calls closed -> says so`, await page.getByText('Ask the Rav is not taking calls right now.').count() === 1);
    await ctx.close();
  }
  // Microphone refused
  {
    const st = stationWith({ id: 'ls5', station_id: 'st-1', title: 'Ask the Rav', status: 'live' });
    const { ctx, page, call } = await openCallIn(browser, contextOpts, st, { denyMic: true });
    await page.getByPlaceholder('Name').fill('Yaakov');
    await page.getByRole('button', { name: 'Call in' }).click();
    await page.getByText('Please allow the microphone to call in.').waitFor({ timeout: 4000 }).catch(() => {});
    check(`${tag}: microphone refused -> error, no call made`, await page.getByText('Please allow the microphone to call in.').count() === 1 && !st.callBody && call.sockets.length === 0);
    await ctx.close();
  }
  // A full call
  const st = stationWith({ id: 'ls5', station_id: 'st-1', title: 'Ask the Rav', status: 'live' });
  const { ctx, page, errors, call } = await openCallIn(browser, contextOpts, st);
  check(`${tag}: Call in disabled until a name is typed`, await page.getByRole('button', { name: 'Call in' }).isDisabled());
  await page.getByPlaceholder('Name').fill('Yaakov');
  await page.locator('input').nth(1).fill('Hilchos Chanukah');
  await page.getByRole('button', { name: 'Call in' }).click();
  await timeUntil(page, `() => document.body.innerText.includes('Hang up')`, 5000);
  await sleep(500);
  check(`${tag}: call sent with name and topic, socket authenticated`, st.callBody?.name === 'Yaakov' && st.callBody?.note === 'Hilchos Chanukah' && call.auths[0] === 'tok-1', JSON.stringify(st.callBody));
  const text = () => page.locator('.rounded-lg.p-4').first().innerText();
  call.status('waiting');
  await sleep(300);
  check(`${tag}: waiting`, (await text()).includes("You're in line"));
  // The mic meter moves with the (fake) microphone
  const width = () => page.evaluate(() => { const bars = [...document.querySelectorAll('.bg-green-500')]; return bars.length ? parseFloat(bars[bars.length - 1].style.width) : 0; });
  let w = 0;
  for (let i = 0; i < 20 && w === 0; i++) { await sleep(150); w = await width(); }
  check(`${tag}: mic level meter moves`, w > 0, `${w}%`);
  // A dropped connection comes back and the call is held
  const before = call.sockets.length;
  call.drop();
  const back = await (async () => { const s = Date.now(); while (call.sockets.length === before && Date.now() - s < 4000) await sleep(50); return Date.now() - s; })();
  await sleep(300);
  check(`${tag}: dropped connection reconnects within 1.5 s and authenticates again`, call.sockets.length > before && back <= 1500 && call.auths.length >= 2 && call.auths.at(-1) === 'tok-1', `${back} ms`);
  check(`${tag}: the call is held (still in the call)`, await page.getByRole('button', { name: 'Hang up' }).count() === 1);
  call.status('approved');
  await sleep(300);
  check(`${tag}: approved`, (await text()).includes('Approved'));
  call.status('on_air');
  await sleep(300);
  check(`${tag}: on air with a red pulse`, (await text()).includes("You're on the air!") && await page.locator('.animate-pulse.bg-red-500').count() === 1);
  await page.getByRole('button', { name: 'Hang up' }).click();
  await sleep(300);
  check(`${tag}: Hang up ends the call and closes the socket`, await page.getByText('Your call has ended', { exact: false }).count() === 1 && call.sockets.every((s) => !s.open));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
  // Rejected by the screener
  {
    const st2 = stationWith({ id: 'ls5', station_id: 'st-1', title: 'Ask the Rav', status: 'live' });
    const { ctx: c2, page: p2, call: k2 } = await openCallIn(browser, contextOpts, st2);
    await p2.getByPlaceholder('Name').fill('Leah');
    await p2.getByRole('button', { name: 'Call in' }).click();
    await timeUntil(p2, `() => document.body.innerText.includes('Hang up')`, 5000);
    await sleep(300);
    k2.status('rejected');
    await sleep(300);
    check(`${tag}: rejected by the screener -> says so, mic released`, await p2.getByText("The show can't take your call right now.", { exact: false }).count() === 1 && await p2.getByRole('link', { name: 'Back to listening' }).count() === 1);
    await c2.close();
  }
}

const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] });
try {
  await run(browser, { viewport: { width: 1280, height: 900 } }, 'call-in desktop');
  await run(browser, { ...devices['iPhone 13'] }, 'call-in iPhone');
} finally {
  await browser.close();
}
process.exit(done());
