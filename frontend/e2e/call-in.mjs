/**
 * Browser check for the web call-in page (/call/:station) against a fake backend and
 * a fake call socket: no show / calls closed, microphone refused, the status line
 * through screening to on air, rejected, a dropped connection that comes back with
 * the call held, the mic meter, and Hang up. Uses Chromium's fake microphone.
 * Then F-CALLIN-12..20: the call state wins over the show poll, refused calls, name
 * rules, giving up after 5 reconnects, leaving mid-call, a failing show lookup, phone layout.
 * (e2e/web-call-in.cjs is the end-to-end run against a real backend.)
 *
 *   BASE_URL=http://localhost:4311 node e2e/call-in.mjs
 */
import { chromium, devices, checker, sleep, makeStation, routeApi, routeSiteSocket, timeUntil } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const { check, done } = checker({});

async function openCallIn(browser, contextOpts, station, { denyMic = false, down = [], routes } = {}) {
  const ctx = await browser.newContext({ ...contextOpts, permissions: denyMic ? [] : ['microphone'] });
  // Keep each microphone stream the page opens, so a check can see whether it was released
  await ctx.addInitScript(() => {
    window.__mics = [];
    const gum = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices);
    if (gum) navigator.mediaDevices.getUserMedia = async (c) => { const s = await gum(c); window.__mics.push(s); return s; };
  });
  if (denyMic) await ctx.addInitScript(() => { navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('denied', 'NotAllowedError')); });
  await routeApi(ctx, station, { down });
  if (routes) await routes(ctx);
  await routeSiteSocket(ctx);
  const call = { sockets: [], auths: [], refuse: false };
  await ctx.routeWebSocket(/\/ws\/live\/call\//, (ws) => {
    const entry = { ws, open: true, url: ws.url() };
    call.sockets.push(entry);
    if (call.refuse) { entry.open = false; ws.close({ code: 1011 }).catch(() => {}); return; }
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


// ── F-CALLIN-12..20 ──
const micLive = (page) => page.evaluate(() => (window.__mics || []).some((s) => s.getTracks().some((t) => t.readyState === 'live')));
const startCall = async (page, name = 'Yaakov') => {
  await page.getByPlaceholder('Name').fill(name);
  await page.getByRole('button', { name: 'Call in' }).click();
  await timeUntil(page, `() => document.body.innerText.includes('Hang up')`, 5000);
  await sleep(400);
};

async function more(browser) {
  const tag = 'call-in';
  const DESK = { viewport: { width: 1280, height: 900 } };
  const PHONE = { ...devices['iPhone 13'] };
  const showRow = { id: 'ls5', station_id: 'st-1', title: 'Ask the Rav', status: 'live' };
  // F-CALLIN-12: the 15 s poll says calls are off, then no show, while the caller is in line / on air
  {
    const st = stationWith(showRow);
    let callsOn = true, showOn = true;
    const base = st.handle;
    st.handle = (m, p, q, b) => (p === '/live-shows/station/st-1/active'
      ? (showOn ? { active: true, show: { id: 'ls5', title: 'Ask the Rav', calls_enabled: callsOn } } : { active: false, show: null })
      : base(m, p, q, b));
    const { ctx, page, call, errors } = await openCallIn(browser, DESK, st);
    await startCall(page);
    call.status('waiting');
    await sleep(300);
    callsOn = false;
    await sleep(16000);
    const hang1 = await page.getByRole('button', { name: 'Hang up' }).count();
    const text1 = await page.locator('body').innerText();
    check(`${tag} F-CALLIN-12: calls turned off mid-call -> status, mic meter and Hang up stay`,
      hang1 === 1 && text1.includes("You're in line") && text1.includes('Your mic') && !text1.includes('not taking calls') && await micLive(page), text1.slice(0, 160).replace(/\n/g, ' / '));
    call.status('on_air');
    showOn = false;
    await sleep(16000);
    const hang2 = await page.getByRole('button', { name: 'Hang up' }).count();
    const text2 = await page.locator('body').innerText();
    check(`${tag} F-CALLIN-12: the poll says no show while on air -> still on air with Hang up`,
      hang2 === 1 && text2.includes("You're on the air!") && !text2.includes('There is no live show'), text2.slice(0, 160).replace(/\n/g, ' / '));
    await page.getByRole('button', { name: 'Hang up' }).click({ timeout: 3000 }).catch(() => {});
    await sleep(300);
    check(`${tag} F-CALLIN-12: Hang up then releases the mic and closes the socket`, !(await micLive(page)) && call.sockets.every((s) => !s.open));
    check(`${tag} F-CALLIN-12: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
  // F-CALLIN-13: the server refuses the call
  for (const [label, reply, expect] of [
    ['no live show (409)', { status: 409, detail: 'There is no live show right now' }, 'There is no live show right now'],
    ['calls closed (409)', { status: 409, detail: 'The show is not taking calls right now' }, 'The show is not taking calls right now'],
    ['too many calls (429)', { status: 429, detail: 'Too many calls in a few minutes. Please wait and try again.' }, 'Too many calls in a few minutes'],
    ['no network', null, 'Could not reach the show'],
  ]) {
    const st = stationWith(showRow);
    const ctxOpts = DESK;
    const { ctx, page, call } = await openCallIn(browser, ctxOpts, st, {
      routes: (c) => c.route('**/api/v1/call-in/**', (r) => (reply ? r.fulfill({ status: reply.status, contentType: 'application/json', body: JSON.stringify({ detail: reply.detail }) }) : r.abort('internetdisconnected'))),
    });
    await page.getByPlaceholder('Name').fill('Yaakov');
    await page.getByRole('button', { name: 'Call in' }).click();
    await page.getByText(expect, { exact: false }).waitFor({ timeout: 5000 }).catch(() => {});
    await sleep(300);
    const shown = await page.getByText(expect, { exact: false }).count();
    check(`${tag} F-CALLIN-13: ${label} -> shows "${expect}", mic released, no socket, can try again`,
      shown >= 1 && !(await micLive(page)) && call.sockets.length === 0 && await page.getByRole('button', { name: 'Call in' }).isEnabled());
    await ctx.close();
  }
  // F-CALLIN-14: name rules
  {
    const st = stationWith(showRow);
    const { ctx, page } = await openCallIn(browser, DESK, st);
    await page.getByPlaceholder('Name').fill('    ');
    const disabledBlank = await page.getByRole('button', { name: 'Call in' }).isDisabled();
    const maxName = await page.getByPlaceholder('Name').getAttribute('maxlength');
    const maxNote = await page.locator('input').nth(1).getAttribute('maxlength');
    await page.getByPlaceholder('Name').fill('x'.repeat(150));
    const len = (await page.getByPlaceholder('Name').inputValue()).length;
    check(`${tag} F-CALLIN-14: Call in disabled for a blank or spaces-only name; name max 100, note max 300`,
      disabledBlank && maxName === '100' && maxNote === '300' && len === 100, `blank disabled=${disabledBlank} ${maxName}/${maxNote} typed=${len}`);
    await ctx.close();
  }
  // F-CALLIN-15: reconnect gives up after 5 tries; 4001 / 4004 end at once (phone size)
  {
    const st = stationWith(showRow);
    const { ctx, page, call } = await openCallIn(browser, PHONE, st);
    await startCall(page);
    call.status('waiting');
    await sleep(200);
    call.refuse = true;  // every new socket is closed at once
    const t0 = Date.now();
    call.drop();
    await page.getByText('Your call has ended', { exact: false }).waitFor({ timeout: 25000 }).catch(() => {});
    const took = Date.now() - t0;
    const tries = call.sockets.length - 1;
    check(`${tag} F-CALLIN-15 iPhone: reconnect gives up after 5 tries (1+2+3+4+5 s) and shows "Your call has ended"`,
      tries === 5 && took >= 14000 && took < 20000 && await page.getByText('Your call has ended', { exact: false }).count() === 1 && !(await micLive(page)), `${tries} tries in ${took} ms`);
    await ctx.close();
  }
  for (const code of [4001, 4004]) {
    const st = stationWith(showRow);
    const { ctx, page, call } = await openCallIn(browser, PHONE, st);
    await startCall(page);
    const n = call.sockets.length;
    call.sockets.filter((s) => s.open).forEach((s) => { s.open = false; s.ws.close({ code }).catch(() => {}); });
    await sleep(2500);
    check(`${tag} F-CALLIN-15 iPhone: close ${code} ends the call at once (no reconnect)`,
      call.sockets.length === n && await page.getByText('Your call has ended', { exact: false }).count() === 1 && !(await micLive(page)), `${call.sockets.length - n} new sockets`);
    await ctx.close();
  }
  // F-CALLIN-16 and F-CALLIN-20
  {
    const st = stationWith(showRow);
    const { ctx, page, call } = await openCallIn(browser, DESK, st);
    await startCall(page);
    call.status('approved');
    await sleep(300);
    call.status('on_hold_new_status');
    await sleep(400);
    const t = await page.locator('body').innerText();
    check(`${tag} F-CALLIN-20: an unknown call_status keeps the call panel (status text and Hang up)`,
      t.includes('Approved. The host will bring you on shortly.') && await page.getByRole('button', { name: 'Hang up' }).count() === 1, t.slice(0, 160).replace(/\n/g, ' / '));
    call.status('completed');
    await sleep(400);
    const back = page.getByRole('link', { name: 'Back to listening' });
    check(`${tag} F-CALLIN-16: call ended -> "Back to listening" goes to /listen/:stationId; no Call in button until a reload (decide)`,
      (await back.getAttribute('href')) === '/listen/st-1' && await page.getByRole('button', { name: 'Call in' }).count() === 0);
    await back.click();
    await page.waitForURL(/\/listen\/st-1/, { timeout: 5000 }).catch(() => {});
    check(`${tag} F-CALLIN-16: the link opens the Listen page`, page.url().endsWith('/listen/st-1'), page.url());
    await ctx.close();
  }
  // F-CALLIN-17: leaving mid-call hangs up
  {
    const st = stationWith(showRow);
    const { ctx, page, call } = await openCallIn(browser, DESK, st);
    await startCall(page);
    call.status('on_air');
    await sleep(300);
    const liveBefore = await micLive(page);
    await page.evaluate(() => { history.pushState({}, '', '/stations'); dispatchEvent(new PopStateEvent('popstate')); });
    await sleep(800);
    check(`${tag} F-CALLIN-17: leaving the page mid-call releases the mic and closes the socket`,
      liveBefore && !(await micLive(page)) && call.sockets.every((s) => !s.open), `mic before=${liveBefore}`);
    await ctx.close();
  }
  {
    const st = stationWith(showRow);
    const { ctx, page, call } = await openCallIn(browser, DESK, st);
    await startCall(page);
    await page.close();
    await sleep(800);
    check(`${tag} F-CALLIN-17: closing the tab mid-call closes the socket (the server then drops the caller)`, call.sockets.every((s) => !s.open));
    await ctx.close();
  }
  // F-CALLIN-18: the active-show lookup keeps failing
  {
    const st = stationWith(showRow);
    const { ctx, page } = await openCallIn(browser, DESK, st, { down: ['/live-shows/station/'] });
    await sleep(2500);
    const t = await page.locator('body').innerText();
    check(`${tag} F-CALLIN-18: active-show lookup failing -> says it could not reach the station and is retrying (not a bare "Loading…")`,
      /could not reach the station/i.test(t), t.slice(0, 160).replace(/\n/g, ' / '));
    await ctx.close();
  }
  // F-CALLIN-19: phone layout
  {
    const st = stationWith(showRow);
    const { ctx, page } = await openCallIn(browser, PHONE, st);
    await page.getByPlaceholder('Name').waitFor({ timeout: 5000 }).catch(() => {});
    const hs = await page.locator('input').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().height)));
    const hint = await page.getByText('Headphones work best', { exact: false }).isVisible();
    const sw = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(`${tag} F-CALLIN-19 iPhone: name and note fields at least 44 px tall, headphones hint visible, no sideways scroll`,
      hs.length === 2 && hs.every((h) => h >= 44) && hint && sw <= 0, `heights ${hs} scroll ${sw}`);
    await ctx.close();
  }
}

const browser = await chromium.launch({ args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] });
try {
  await run(browser, { viewport: { width: 1280, height: 900 } }, 'call-in desktop');
  await run(browser, { ...devices['iPhone 13'] }, 'call-in iPhone');
  await more(browser);
} finally {
  await browser.close();
}
process.exit(done());
