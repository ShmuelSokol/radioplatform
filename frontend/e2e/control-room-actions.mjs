/**
 * Browser check of every control room button and queue action against a fake
 * backend (no real station is touched), with timings: press to visible change, and
 * press to the next item heard. Complements e2e/control-room.mjs (layout, long
 * queues, cart pages) and e2e/audio-engine.mjs (song changes in the player).
 *
 *   cd frontend && npm run build && (npx vite preview --port 4311 --strictPort &)
 *   BASE_URL=http://localhost:4311 node e2e/control-room-actions.mjs   # optional: ONLY=<part,...>
 *
 * Exits non-zero if a check fails. Known site bugs are listed in KNOWN_BUGS (XFAIL).
 */
import {
  chromium, devices, checker, sleep, makeStation, adminContext, newPage, watchDecks, timeClick, timeUntil,
} from './lib/fake-station.mjs';
import { startStreamServer } from './lib/stream-server.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const STREAM_PORT = Number(process.env.STREAM_PORT || 4322);
const ONLY = process.env.ONLY || '';
const DESKTOP = { viewport: { width: 1440, height: 900 } };
const PHONE = { ...devices['iPhone 13'] };

const KNOWN_BUGS = {};
const { check, done } = checker(KNOWN_BUGS);

async function openRoom(browser, contextOpts, station, opts = {}) {
  const { ctx, np } = await adminContext(browser, contextOpts, station, opts);
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/dashboard`);
  if (!opts.noWait) await page.getByTestId('queue-row').first().waitFor();
  return { ctx, np, page, errors };
}
const header = (page) => page.locator('[data-testid=control-room] header');
const headerText = (page) => header(page).innerText();
const listenHere = async (page) => {
  await page.locator('button:visible', { hasText: 'Listen' }).first().click();
  await timeUntil(page, `() => window.__audible().length >= 1 && window.__outLevel() > 0`, 4000);
};
const rowOf = (page, entryId) => page.locator(`[data-testid=queue-row][data-entry-id="${entryId}"]`);
const writes = (st, re) => st.calls.filter((c) => c.method !== 'GET' && re.test(c.path));
const toastText = (page) => page.getByTestId('toast').innerText().catch(() => '');

// ── Transport: station, status, clocks, now/next ─────────────
async function transport(browser) {
  const tag = 'transport';
  const station = makeStation();
  const { ctx, page, errors } = await openRoom(browser, DESKTOP, station, { storage: { radio_last_station: null } });
  const h = await headerText(page);
  check(`${tag}: PLAYING shown while on air`, /PLAYING/.test(h));
  const nowA = station.byId[station.playing().asset_id];
  const nextA = station.byId[station.pending()[0].asset_id];
  check(`${tag}: now playing title and type boxes`, h.includes(nowA.title) && h.includes(nowA.category));
  check(`${tag}: next title and type`, h.includes(nextA.title));
  check(`${tag}: on air elapsed / total shown on a desktop`, /\b0:2\d \/ \d:\d\d\b/.test(h), h.match(/\d+:\d\d \/ \d+:\d\d/)?.[0]);
  // Remaining counts down from the server's start time
  const rem = async () => { const [m, s] = (await page.getByTestId('remaining').innerText()).split(':'); return +m * 60 + +s; };
  const r1 = await rem(); await sleep(2100); const r2 = await rem();
  check(`${tag}: remaining counts down each second`, r1 - r2 >= 2 && r1 - r2 <= 3 && Math.abs(r1 - (nowA.duration - 20)) <= 2, `${r1} -> ${r2}`);
  // Clock in the station's time zone, ticking
  const clock = () => header(page).locator('.qn-digits').last().innerText();
  const c1 = await clock(); await sleep(1100); const c2 = await clock();
  const nyNow = new Date().toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }).split(' ')[0];
  check(`${tag}: clock shows station time and ticks`, c1 !== c2 && c2.replace(/\s/g, '').startsWith(nyNow.replace(/\s/g, '')), `${c1} -> ${c2} (NY ${nyNow})`);
  // Progress bar fills
  const prog = +(await page.getByRole('progressbar').getAttribute('aria-valuenow'));
  check(`${tag}: progress bar matches elapsed`, Math.abs(prog - (22 / nowA.duration) * 100) < 3, `${prog}%`);
  // Next box follows the server's next_entry_id (it skips items timed for later)
  const third = station.pending()[1];
  station.nextEntryId = third.id;
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction((t) => document.querySelector('[data-testid=control-room] header').innerText.includes(t), station.byId[third.asset_id].title, { timeout: 4000 }).catch(() => {});
  check(`${tag}: Next box shows the server's next_entry_id`, (await headerText(page)).includes(station.byId[third.asset_id].title));
  station.nextEntryId = undefined;
  // Back from a hidden tab: the queue is read again at once (F-CR-TRANSPORT-31)
  const gets = () => station.calls.filter((c) => c.method === 'GET' && c.path === `/stations/${station.SID}/queue`).length;
  const g0 = gets();
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { value: false, configurable: true }); document.dispatchEvent(new Event('visibilitychange')); });
  await sleep(400);
  check(`${tag}: back from a hidden tab the queue is read at once`, gets() > g0, `${g0} -> ${gets()}`);
  // Footer
  const foot = await page.locator('footer').innerText();
  check(`${tag}: footer shows shortcuts, station + timezone, user + role`, /N.*next.*\/.*search.*Esc/s.test(foot) && foot.includes('Kol Berama · America/New_York') && foot.includes('operator@example.com (admin)'), foot.replace(/\n/g, ' '));
  // Station switch: remembered, cancels insert marker and day filter
  await rowOf(page, station.pending()[2].id).getByTestId('insert-here').click();
  await page.getByLabel('Show day').selectOption({ index: 1 });
  await sleep(300);
  check(`${tag}: (set up) insert armed and a day picked`, await page.getByTestId('insert-marker').count() === 1 && await page.getByLabel('Show day').inputValue() !== '');
  await page.getByLabel('Station').first().selectOption('st-2');
  await sleep(500);
  check(`${tag}: station switch remembered in radio_last_station`, await page.evaluate(() => localStorage.getItem('radio_last_station')) === 'st-2');
  await page.getByLabel('Station').first().selectOption(station.SID);
  await page.getByTestId('queue-row').first().waitFor();
  check(`${tag}: station switch cancels the insert marker and day filter`, await page.getByTestId('insert-marker').count() === 0 && await page.getByLabel('Show day').inputValue() === '');
  await page.getByLabel('Station').first().selectOption('st-2');
  await sleep(300);
  await page.reload();
  await sleep(1500);
  check(`${tag}: chosen station kept after a reload`, await page.getByLabel('Station').first().inputValue() === 'st-2');
  const clk = await clock();
  const jNow = new Date().toLocaleTimeString('en-US', { timeZone: 'Asia/Jerusalem', hour: 'numeric', minute: '2-digit' }).split(' ')[0];
  check(`${tag}: clock follows the station's time zone`, clk.replace(/\s/g, '').startsWith(jNow), `${clk} vs Jerusalem ${jNow}`);
  check(`${tag}: an empty, stopped station shows Start, "nothing queued" and the empty text`,
    await page.getByRole('button', { name: '▶ Start' }).count() === 1 && /nothing queued/.test(await headerText(page))
    && await page.getByText('The queue is empty. Add songs from the library, or press Start.').count() === 1);
  await page.getByRole('button', { name: '▶ Start' }).click();
  await page.getByText('The queue is empty. Add something from the library first.').first().waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: Start on an empty queue says so`, await page.getByText('The queue is empty. Add something from the library first.').count() >= 1);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// Last 10 s: remaining red and pulsing, bar red. Stopped station: Start, Starting…
async function transportEnd(browser) {
  const tag = 'transport end';
  const station = makeStation({ startedAgo: 155 }); // m1 is 151 s: ends in ~6 s... use m1 duration
  const cur = station.playing();
  cur.started_at = new Date(Date.now() - (station.byId[cur.asset_id].duration - 8) * 1000).toISOString();
  const { ctx, page } = await openRoom(browser, DESKTOP, station);
  const cls = await page.getByTestId('remaining').getAttribute('class');
  const bar = await page.locator('[role=progressbar] > div').getAttribute('class');
  check(`${tag}: last 10 s: remaining red and pulsing`, /text-red-500/.test(cls) && /animate-pulse/.test(cls), cls);
  check(`${tag}: last 10 s: progress bar red`, /bg-red-500/.test(bar), bar);
  await ctx.close();

  const st2 = makeStation({ stopped: true });
  const { ctx: c2, page: p2 } = await openRoom(browser, DESKTOP, st2, { delay: (p) => (p.endsWith('/queue/start') ? 400 : 0) });
  check(`${tag}: stopped station shows ▶ Start, not PLAYING`, await p2.getByRole('button', { name: '▶ Start' }).count() === 1 && !/PLAYING/.test(await headerText(p2)));
  check(`${tag}: Go next, Fade, Soft fade and Loop disabled when stopped`, await p2.getByRole('button', { name: /Go next/ }).first().isDisabled()
    && await p2.locator('[data-testid=next-fade]').first().isDisabled() && await p2.locator('[data-testid=loop]').first().isDisabled());
  const ms = await timeClick(p2, p2.getByRole('button', { name: '▶ Start' }), `() => document.body.innerText.includes('Starting…')`);
  check(`${tag}: Start shows Starting… at once (S-1)`, ms >= 0 && ms < 150, `${ms} ms`);
  await p2.getByText('PLAYING').waitFor({ timeout: 4000 }).catch(() => {});
  check(`${tag}: Start calls /queue/start and goes on air`, writes(st2, /\/queue\/start$/).length === 1 && /PLAYING/.test(await headerText(p2)));
  await c2.close();

  // One station only: its name instead of a dropdown
  const st3 = makeStation({ stations: [{ id: 'st-1', name: 'Only Station', timezone: 'America/New_York', is_active: true, channels: [] }] });
  const { ctx: c3, page: p3 } = await openRoom(browser, DESKTOP, st3);
  check(`${tag}: a single station shows its name, no dropdown`, await p3.getByLabel('Station').count() === 0 && (await headerText(p3)).includes('Only Station'));
  await c3.close();
}

// ── Next, Fade, Soft fade, Play now: heard, and the fade length heard ─────────────
async function nextAndFades(browser, contextOpts, tag) {
  const station = makeStation();
  const { ctx, page, errors } = await openRoom(browser, contextOpts, station);
  await listenHere(page);
  const how = contextOpts.isMobile ? 'tap' : 'click';
  const press = { hard: () => page.getByRole('button', { name: /Go next/ }).first(), fade: () => page.locator('[data-testid=next-fade]:visible').first(), soft: () => page.locator('[data-testid=next-soft]:visible').first() };
  const want = { hard: [0, 0.3], fade: [0.25, 0.9], soft: [2.4, 3.6] };
  for (const kind of ['hard', 'fade', 'soft']) {
    await sleep(5000); // past the "just changed" window
    const out = station.playing().asset_id;
    const inc = station.pending()[0].asset_id;
    const watch = watchDecks(page, kind === 'soft' ? 4500 : 2000, 20);
    await sleep(30);
    const t0 = Date.now();
    await (how === 'tap' ? press[kind]().tap() : press[kind]().click());
    const samples = await watch;
    const rel = (s) => (s.now - t0) / 1000;
    const heard = samples.find((s) => rel(s) >= 0 && s.decks.some((d) => d.src === inc && d.playing && d.gain > 0.05));
    const gone = samples.find((s) => rel(s) >= 0 && !s.decks.some((d) => d.src === out && d.playing && d.gain > 0.05));
    const heardAt = heard ? rel(heard) : null;
    const goneAt = gone ? rel(gone) : null;
    check(`${tag}: ${kind} -> next item heard within 0.5 s (S-2)`, heardAt !== null && heardAt <= 0.5, heardAt === null ? 'never' : `${heardAt.toFixed(2)} s`);
    check(`${tag}: ${kind} -> outgoing item fades over ${want[kind].join('-')} s`, goneAt !== null && goneAt >= want[kind][0] && goneAt <= want[kind][1], goneAt === null ? 'never' : `${goneAt.toFixed(2)} s`);
    const asked = station.calls.filter((c) => c.path.endsWith('/queue/skip')).pop();
    check(`${tag}: ${kind} asks the server for fade=${kind}`, asked?.query.fade === kind, asked?.query.fade);
  }
  // Play now from an instant button: one request, heard soon after the server announces it
  await sleep(1000);
  if (contextOpts.isMobile) await page.getByRole('button', { name: 'Buttons' }).tap();
  const jingle = page.getByTestId('hot-button').filter({ hasText: 'Station ID 4' });
  const ms = await timeClick(page, jingle, `() => window.__audible().some(d => d.src === 'j4')`, 3000, how);
  check(`${tag}: Play now heard within 0.5 s (S-2)`, ms >= 0 && ms <= 500, `${ms} ms`);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Next refused: time/weather/sponsor on air, or just changed ─────────────
async function nextRefused(browser) {
  const tag = 'next refused';
  for (const [kind, reply, text] of [
    ['protected', { protected: true }, 'The time, weather and sponsorships always play to the end'],
    ['kept_incoming', { kept_incoming: true }, 'That item just started, so it keeps playing'],
  ]) {
    const station = makeStation({ startedAgo: kind === 'kept_incoming' ? 2 : 20 });
    if (kind === 'protected') { const cur = station.playing(); cur.asset_id = 'wx'; cur.source = 'block'; cur.preempt_at = cur.started_at; }
    station.skipReply = reply;
    const { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
    await listenHere(page);
    const before = { now: station.playing().asset_id, order: station.pending().map((e) => e.id).join() };
    const watch = watchDecks(page, 2500, 50);
    await page.getByRole('button', { name: /Go next/ }).first().click();
    const samples = await watch;
    await page.getByTestId('toast').waitFor({ timeout: 3000 }).catch(() => {});
    check(`${tag} (${kind}): the toast explains`, (await toastText(page)).includes(text), await toastText(page));
    check(`${tag} (${kind}): nothing changes on air or in the queue`, station.playing().asset_id === before.now && station.pending().map((e) => e.id).join() === before.order
      && (await headerText(page)).includes(station.byId[before.now].title));
    const switched = samples.filter((s) => s.decks.some((d) => d.src !== before.now && d.playing && d.gain > 0.05));
    check(`${tag} (${kind}): this browser does not play ahead`, switched.length === 0, switched[0] ? JSON.stringify(switched[0].decks) : '');
    check(`${tag} (${kind}): no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── Loop really repeats; Next ends the loop ─────────────
async function loop(browser) {
  const tag = 'loop';
  const station = makeStation();
  const { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
  await listenHere(page);
  const btn = page.locator('[data-testid=loop]:visible').first();
  const ms = await timeClick(page, btn, `() => [...document.querySelectorAll('[data-testid=loop]')].some(b => b.getAttribute('aria-pressed') === 'true')`);
  check(`${tag}: Loop lights within 150 ms of the server's answer (S-1, local server)`, ms >= 0 && ms < 150, `${ms} ms`);
  await page.getByTestId('toast').waitFor();
  check(`${tag}: on-toast`, (await toastText(page)).includes('Loop is on'));
  // The item ends: the server plays it again (a new start time, same item)
  const cur = station.playing();
  await sleep(1500);
  const repeat = station.add({ asset_id: cur.asset_id, position: cur.position });
  cur.status = 'played';
  repeat.status = 'playing'; repeat.started_at = new Date().toISOString();
  station.push();
  await sleep(1200);
  const d = (await page.evaluate(() => window.__audible())).filter((x) => x.src === cur.asset_id);
  check(`${tag}: the item is heard again from its top`, d.length === 1 && d[0].t < 2, JSON.stringify(d));
  await page.getByRole('button', { name: /Go next/ }).first().click();
  await page.waitForFunction(() => [...document.querySelectorAll('[data-testid=loop]')].every((b) => b.getAttribute('aria-pressed') === 'false'), null, { timeout: 3000 }).catch(() => {});
  check(`${tag}: Next ends the loop`, await btn.getAttribute('aria-pressed') === 'false');
  await btn.click();
  await sleep(300);
  await btn.click();
  await page.waitForFunction(() => document.querySelector('[data-testid=toast]')?.innerText.includes('Loop is off'), null, { timeout: 3000 }).catch(() => {});
  check(`${tag}: off-toast`, (await toastText(page)).includes('Loop is off'));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Toasts: ok 2.5 s, warn 6 s, ✕ ─────────────
async function toasts(browser) {
  const tag = 'toasts';
  const station = makeStation();
  const { ctx, page } = await openRoom(browser, DESKTOP, station);
  await page.getByRole('radio', { name: 'Play next' }).click();
  await page.getByTestId('hot-button').filter({ hasText: 'Station ID 2' }).click();
  await page.getByTestId('toast').waitFor();
  const okGone = await timeUntil(page, `() => !document.querySelector('[data-testid=toast]')`, 8000);
  check(`${tag}: ok toast hides after about 2.5 s`, okGone >= 2000 && okGone <= 3000, `${okGone} ms`);
  station.skipReply = { kept_incoming: true };
  await page.getByRole('button', { name: /Go next/ }).first().click();
  await page.getByTestId('toast').waitFor();
  const warnGone = await timeUntil(page, `() => !document.querySelector('[data-testid=toast]')`, 9000);
  check(`${tag}: warning toast stays about 6 s`, warnGone >= 5300 && warnGone <= 6500, `${warnGone} ms`);
  await page.getByRole('button', { name: /Go next/ }).first().click();
  await page.getByTestId('toast').waitFor();
  await page.getByTestId('toast').getByRole('button', { name: 'Dismiss' }).click();
  check(`${tag}: ✕ dismisses`, await page.getByTestId('toast').count() === 0);
  // Weather preview: time then weather heard off air; none; failure
  const airWrites = writes(station, /\/queue\/(skip|fire-now|insert|play-next)/).length;
  await page.getByRole('button', { name: 'Preview time & weather' }).click();
  await sleep(1500);
  const srcs = await page.evaluate(() => window.__audios.map((a) => a.src.split('/').pop()));
  check(`${tag}: time & weather preview plays the time clip, nothing sent to air`, srcs.includes('tc.wav') && writes(station, /\/queue\/(skip|fire-now|insert|play-next)/).length === airWrites, srcs.join());
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some((b) => b.textContent === 'Preview time & weather' && !b.disabled), null, { timeout: 30000 }).catch(() => {});
  const srcs2 = await page.evaluate(() => window.__audios.map((a) => a.src.split('/').pop()));
  check(`${tag}: then the weather clip`, srcs2.includes('wx.wav'), srcs2.join());
  station.weather = { time_url: null, weather_url: null };
  await page.getByRole('button', { name: 'Preview time & weather' }).click();
  await page.getByTestId('toast').waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: "No weather announcement" when there is none`, (await toastText(page)).includes('No weather announcement'));
  station.weather = 'error';
  await page.getByRole('button', { name: 'Preview time & weather' }).click();
  await page.waitForFunction(() => document.querySelector('[data-testid=toast]')?.innerText.includes('Could not load the weather preview'), null, { timeout: 3000 }).catch(() => {});
  check(`${tag}: error toast when the preview fails`, (await toastText(page)).includes('Could not load the weather preview'));
  await ctx.close();
}

// ── Badges: live show, playout ─────────────
async function badges(browser) {
  const tag = 'badges';
  const station = makeStation({ shows: [{ id: 'ls7', station_id: 'st-1', title: 'Erev Shabbos Live', status: 'live' }] });
  const { ctx, page } = await openRoom(browser, { viewport: { width: 1600, height: 900 } }, station);
  const live = page.getByRole('link', { name: /LIVE: Erev Shabbos Live/ });
  check(`${tag}: LIVE badge links to the host console`, await live.getAttribute('href') === '/admin/live/ls7/host');
  check(`${tag}: Go live tab turns red "● Live"`, await page.getByRole('tab', { name: '● Live' }).count() === 1);
  for (const [status, label] of [
    [{ mode: 'browser' }, 'Browser player'],
    [{ mode: 'server', engine: true, engine_ok: true, in_sync: true }, 'Server stream'],
    [{ mode: 'server', engine: true, engine_ok: false }, 'Server stream: not answering'],
    [{ mode: 'server', engine: true, engine_ok: true, in_sync: false, engine_title: 'Other' }, 'Server stream: out of step'],
  ]) {
    station.playout = { stations: { [station.SID]: status }, default: { mode: 'browser' } };
    await page.reload();
    await page.getByTestId('playout-badge').first().waitFor();
    const t = (await page.getByTestId('playout-badge').first().innerText()).trim();
    check(`${tag}: playout badge "${label}"`, t === label, t);
  }
  // Fullscreen
  await page.getByRole('button', { name: 'Full screen' }).click();
  await sleep(300);
  const fs1 = await page.evaluate(() => !!document.fullscreenElement);
  await page.getByRole('button', { name: 'Exit full screen' }).click().catch(() => {});
  await sleep(300);
  check(`${tag}: Full screen enters and leaves full screen`, fs1 && !(await page.evaluate(() => !!document.fullscreenElement)));
  // Monitor volume (2xl widths)
  await page.setViewportSize({ width: 2560, height: 1300 });
  await listenHere(page);
  const slider = page.locator('input[aria-label="Monitor volume"]:visible').first();
  check(`${tag}: monitor volume slider visible at 2xl width`, await slider.isVisible());
  await slider.fill('0.3');
  await sleep(300);
  check(`${tag}: monitor volume sets the level heard and is remembered`, Math.abs((await page.evaluate(() => window.__outLevel())) - 0.3) < 0.03 && await page.evaluate(() => localStorage.getItem('radio_volume')) === '0.3');
  await ctx.close();
}

// ── One control room tab makes sound ─────────────
async function oneSoundingTab(browser) {
  const tag = 'one sounding tab';
  const station = makeStation();
  const { ctx } = await adminContext(browser, DESKTOP, station);
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const a = await newPage(ctx); const b = await newPage(ctx);
  for (const p of [a.page, b.page]) { await p.goto(`${BASE}/admin/dashboard`); await p.getByTestId('queue-row').first().waitFor(); }
  await listenHere(a.page);
  check(`${tag}: tab A heard`, (await a.page.evaluate(() => window.__outLevel())) > 0.1);
  await b.page.bringToFront();
  await listenHere(b.page);
  await sleep(500);
  check(`${tag}: switching sound on in tab B silences tab A`, (await a.page.evaluate(() => window.__outLevel())) < 0.01 && (await b.page.evaluate(() => window.__outLevel())) > 0.1,
    `A ${await a.page.evaluate(() => window.__outLevel())}, B ${await b.page.evaluate(() => window.__outLevel())}`);
  check(`${tag}: tab A says the sound moved (while the station plays)`, (await toastText(a.page)).includes('Sound moved to another control room tab'), await toastText(a.page));
  check(`${tag}: tab A offers Listen again, meters still running`, await a.page.locator('button:visible', { hasText: 'Listen' }).count() > 0 && (await a.page.evaluate(() => window.__audible().length)) > 0);
  await ctx.close();
}

// ── Queue rows: labels, timed, blocks, silence, playing row ─────────────
async function queueRows(browser) {
  const tag = 'queue rows';
  const station = makeStation({ queueSize: 8 });
  const hourAt = new Date(Math.ceil((Date.now() + 120e3) / 3600e3) * 3600e3);
  const at = (Date.now() + 1800e3);
  // Due at the same moment: a block of three; a sponsor; a resumed shiur; a silence
  const p = station.pending();
  const base = Math.max(...p.map((e) => e.position));
  station.add({ asset_id: 'tc', position: base + 1, source: 'block', preempt_at: hourAt.toISOString(), hard_timed: true });
  station.add({ asset_id: 'wx', position: base + 2, source: 'block', preempt_at: hourAt.toISOString(), hard_timed: true });
  station.add({ asset_id: 's1', position: base + 3, source: 'ad_slot', preempt_at: hourAt.toISOString(), hard_timed: false });
  station.add({ asset_id: 'm50', position: base + 4, source: 'resume@754' });
  station.add({ asset_id: 'sil', position: base + 5, source: 'blackout', blackout_name: 'Shabbos', preempt_at: new Date(at).toISOString() });
  const { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
  const text = async (id) => (await rowOf(page, id).innerText()).replace(/\s+/g, ' ');
  const ids = station.pending().map((e) => e.id);
  const tc = ids[ids.length - 5], wx = ids[ids.length - 4], sp = ids[ids.length - 3], rs = ids[ids.length - 2], sl = ids[ids.length - 1];
  const hh = hourAt.toLocaleTimeString('en-US', { timeZone: 'America/New_York', hour: 'numeric', minute: '2-digit' }).replace(/\s?(AM|PM)/, '');
  check(`${tag}: timed row shows ⏰ and its exact time`, (await text(tc)).includes(`⏰ ${hh}`), await text(tc));
  check(`${tag}: hourly time check row airs at hh:00:00`, (await rowOf(page, tc).getByTestId('air-time').innerText()).startsWith(`${hh}:00`), await rowOf(page, tc).getByTestId('air-time').innerText());
  check(`${tag}: timed rows highlighted`, /qn-row-timed/.test(await rowOf(page, tc).locator('> div').last().getAttribute('class').catch(() => '')) || /qn-row-timed/.test(await rowOf(page, tc).innerHTML()));
  check(`${tag}: items due together grouped as BLOCK · 3 ITEMS`, (await rowOf(page, tc).getByTestId('block-badge').innerText().catch(() => '')) === 'BLOCK · 3 ITEMS' && await rowOf(page, wx).getByTestId('block-badge').count() === 0);
  check(`${tag}: SPONSOR badge on a sponsor slot`, (await text(sp)).includes('SPONSOR'));
  check(`${tag}: RESUMES badge on a paused item`, (await text(rs)).includes('RESUMES 12:34'), await text(rs));
  check(`${tag}: blackout silence row in red with its name`, (await text(sl)).includes('Scheduled silence: Shabbos') && /text-red-400/.test(await rowOf(page, sl).innerHTML()));
  // The playing row: ▶, no insert, no actions, not draggable, not selectable
  const cur = rowOf(page, station.playing().id);
  check(`${tag}: playing row marked ▶ without insert or actions`, await cur.getByLabel('Playing now').count() === 1 && await cur.getByTestId('insert-here').count() === 0
    && await cur.getByRole('button', { name: 'Move up' }).count() === 0 && await cur.locator('[draggable=true]').count() === 0);
  await cur.click();
  check(`${tag}: playing row cannot be selected`, await page.getByTestId('row-actions').count() === 0);
  // Count label and the day list
  const head = await page.locator('section[aria-label=Queue]').locator('div').first().innerText();
  check(`${tag}: count label "N items · h:mm:ss"`, /\b13 items · \d+:\d\d:\d\d/.test(head.replace(/\s+/g, ' ')), head.replace(/\s+/g, ' '));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Queue actions: move, remove, drag, insert from a button, optimistic + failure ─────────────
async function queueActions(browser) {
  const tag = 'queue actions';
  const station = makeStation({ queueSize: 12 });
  const SLOW = 400;
  const { ctx, page, errors } = await openRoom(browser, DESKTOP, station, { delay: (p, m) => (m !== 'GET' ? SLOW : 0) });
  const order = () => station.pending().map((e) => e.asset_id);
  const shown = () => page.evaluate(() => [...document.querySelectorAll('[data-testid=queue-row]')].map((r) => r.dataset.entryId));
  // Hover actions: ▲ ▼ ✕, shown in the list at once (S-1)
  const p = station.pending();
  const r3 = rowOf(page, p[2].id);
  await r3.hover();
  let ms = await timeClick(page, r3.getByRole('button', { name: 'Move up' }), `() => { const r = [...document.querySelectorAll('[data-testid=queue-row]')].map(x => x.dataset.entryId); return r.indexOf('${p[2].id}') < r.indexOf('${p[1].id}'); }`);
  check(`${tag}: ▲ moves up, on screen at once (S-1)`, ms >= 0 && ms < 150, `${ms} ms with a ${SLOW} ms server`);
  await sleep(SLOW + 300);
  check(`${tag}: ▲ calls move-up`, writes(station, /move-up$/).length === 1 && order()[1] === p[2].asset_id);
  await r3.hover();
  ms = await timeClick(page, r3.getByRole('button', { name: 'Move down' }), `() => { const r = [...document.querySelectorAll('[data-testid=queue-row]')].map(x => x.dataset.entryId); return r.indexOf('${p[2].id}') > r.indexOf('${p[1].id}'); }`);
  await sleep(SLOW + 300);
  check(`${tag}: ▼ moves down at once and calls move-down`, ms >= 0 && ms < 150 && writes(station, /move-down$/).length === 1 && order()[2] === p[2].asset_id, `${ms} ms`);
  const r5 = rowOf(page, p[4].id);
  await r5.hover();
  ms = await timeClick(page, r5.getByRole('button', { name: 'Remove' }), `() => !document.querySelector('[data-testid=queue-row][data-entry-id="${p[4].id}"]')`);
  await sleep(SLOW + 300);
  check(`${tag}: ✕ removes at once and on the server`, ms >= 0 && ms < 150 && !station.queue.some((e) => e.id === p[4].id), `${ms} ms`);
  // A failed change puts the old queue back with an error toast
  const before = await shown();
  station.failNext.add(`/stations/${station.SID}/queue/move-up`);
  const r4 = rowOf(page, p[3].id);
  await r4.hover();
  await r4.getByRole('button', { name: 'Move up' }).click();
  await sleep(SLOW + 600);
  check(`${tag}: a failed change restores the queue and says so`, (await shown()).join() === before.join() && /did not go through/.test(await toastText(page)), await toastText(page));
  // Drag a row onto another: reorder-dnd, warnings shown
  station.reorderWarnings = ['Category transition conflict: "slow" should not follow "lively"'];
  const src = rowOf(page, p[6].id).locator('[draggable=true]');
  await src.dragTo(rowOf(page, p[1].id).locator('[draggable=true]'));
  await sleep(SLOW + 400);
  const dnd = writes(station, /reorder-dnd$/).pop();
  check(`${tag}: drag a row onto another reorders it`, dnd?.body.entry_id === p[6].id && order()[1] === p[6].asset_id, JSON.stringify(dnd?.body));
  check(`${tag}: drag warnings shown as a toast`, (await toastText(page)).includes('Category transition conflict'));
  // Insert from an instant button while the marker is armed
  const target = station.pending()[3];
  await rowOf(page, target.id).getByTestId('insert-here').click();
  await page.getByTestId('cart').getByRole('button', { name: 'Edit', exact: true }).waitFor();
  ms = await timeClick(page, page.getByTestId('hot-button').filter({ hasText: 'Station ID 7' }), `() => { const r = [...document.querySelectorAll('[data-testid=queue-row]')]; const i = r.findIndex(x => x.dataset.entryId === '${target.id}'); return i > 0 && /Station ID 7/.test(r[i - 1].textContent); }`);
  check(`${tag}: placeholder row shows at once (S-1)`, ms >= 0 && ms < 150, `${ms} ms`);
  await sleep(SLOW + 400);
  const ins = writes(station, /\/insert$/).pop();
  const ord = station.pending();
  const ti = ord.findIndex((e) => e.id === target.id);
  check(`${tag}: an instant button inserts at the marker`, [ins?.body.asset_id, ...(ins?.body.asset_ids || [])].includes('j7') && ord[ti - 1]?.asset_id === 'j7', JSON.stringify(ins?.body));
  // The marked row starts playing: marker gone. Then a marker on a row that has already started
  const tgt2 = station.pending()[0];
  await rowOf(page, tgt2.id).getByTestId('insert-here').click();
  check(`${tag}: (set up) marker armed`, await page.getByTestId('insert-marker').count() === 1);
  station.goOnAir(tgt2.asset_id);
  await page.waitForFunction(() => !document.querySelector('[data-testid=insert-marker]'), null, { timeout: 4000 }).catch(() => {});
  check(`${tag}: marker disappears when that row starts playing`, await page.getByTestId('insert-marker').count() === 0);
  const tgt3 = station.pending()[2];
  await rowOf(page, tgt3.id).getByTestId('insert-here').click();
  station.queue = station.queue.filter((e) => e.id !== tgt3.id);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(() => !document.querySelector('[data-testid=insert-marker]'), null, { timeout: 4000 }).catch(() => {});
  check(`${tag}: marker disappears when that row is removed`, await page.getByTestId('insert-marker').count() === 0);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Day filter ─────────────
async function dayFilter(browser) {
  const tag = 'day filter';
  const station = makeStation({ queueSize: 700 }); // ~1.4 days of songs
  const { ctx, page } = await openRoom(browser, DESKTOP, station);
  const sel = page.getByLabel('Show day');
  await sel.focus();
  await sleep(800);
  const opts = await sel.locator('option').allInnerTexts();
  check(`${tag}: days listed after opening the list (whole queue loaded)`, opts.length >= 3 && opts[0] === 'All days', opts.join(' | '));
  check(`${tag}: opening the day list loads the whole queue`, station.calls.some((c) => c.method === 'GET' && c.path.endsWith('/queue') && +c.query.limit >= 700));
  await sel.selectOption({ index: 2 });
  await sleep(500);
  const label = (await page.locator('section[aria-label=Queue]').innerText()).match(/\d+ shown items/)?.[0];
  const ny = new Date(Date.now() + 86400e3).toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
  const ok = await page.evaluate(() => [...document.querySelectorAll('[data-testid=air-time]')].slice(0, 5).every((x) => /AM|PM/.test(x.textContent)));
  check(`${tag}: picking a day filters the queue`, !!label && ok, `${label} (${ny})`);
  await sel.selectOption('');
  await sleep(300);
  check(`${tag}: back to All days`, !/shown items/.test(await page.locator('section[aria-label=Queue]').innerText()));
  await ctx.close();
  // A day with nothing
  const st2 = makeStation({ queueSize: 3 });
  const { ctx: c2, page: p2 } = await openRoom(browser, DESKTOP, st2);
  await p2.evaluate(() => {
    const s = document.querySelector('select[aria-label="Show day"]');
    const o = document.createElement('option'); o.value = '2099-01-01'; o.textContent = 'far'; s.appendChild(o);
  });
  await p2.getByLabel('Show day').selectOption('2099-01-01');
  await sleep(300);
  check(`${tag}: "Nothing scheduled for that day"`, await p2.getByText('Nothing scheduled for that day.').count() === 1);
  await c2.close();
}

// ── Phone: tap a row for ▲ ▼ Remove ─────────────
async function phoneRows(browser) {
  const tag = 'phone rows';
  const station = makeStation({ queueSize: 10 });
  const { ctx, page, errors } = await openRoom(browser, PHONE, station);
  const p = station.pending();
  await rowOf(page, p[2].id).tap();
  const bar = page.getByTestId('row-actions');
  check(`${tag}: tap opens the action bar`, await bar.count() === 1);
  await bar.getByRole('button', { name: 'Move up' }).tap();
  await sleep(500);
  check(`${tag}: ▲ moves the row up`, station.pending()[1].id === p[2].id);
  await bar.getByRole('button', { name: 'Move down' }).tap();
  await sleep(500);
  check(`${tag}: ▼ moves it back down`, station.pending()[2].id === p[2].id);
  await bar.getByRole('button', { name: 'Remove' }).tap();
  await sleep(500);
  check(`${tag}: Remove takes it out and closes the bar`, !station.queue.some((e) => e.id === p[2].id) && await bar.count() === 0);
  await rowOf(page, p[3].id).tap();
  await rowOf(page, p[3].id).locator('[draggable=true]').tap();
  check(`${tag}: tapping the row again closes the bar`, await bar.count() === 0);
  // Transport row on a phone: elapsed hidden, buttons work by tap
  check(`${tag}: on air elapsed hidden on a phone`, !(await page.getByText('On air', { exact: true }).isVisible()));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Library ─────────────
async function library(browser, contextOpts, tag) {
  const station = makeStation({ queueSize: 6 });
  const SLOW = 300;
  const { ctx, page, errors } = await openRoom(browser, contextOpts, station, { delay: (p, m) => (m !== 'GET' ? SLOW : 0) });
  if (contextOpts.isMobile) await page.getByRole('button', { name: /Library/ }).first().tap();
  const box = page.getByRole('searchbox');
  const count = () => page.locator('[data-testid=library-scroll] > div').first().innerText();
  await page.getByTestId('library-row').first().waitFor();
  check(`${tag}: result count shown`, /^1[45]\d items/.test(await count()), await count());
  await box.fill('nigun');
  await page.waitForFunction(() => [...document.querySelectorAll('[data-testid=library-row]')].every((r) => /nigun/i.test(r.textContent)));
  await page.getByRole('button', { name: 'Clear search' }).click();
  check(`${tag}: ✕ clears the search and keeps the focus`, await box.inputValue() === '' && await box.evaluate((el) => document.activeElement === el));
  await page.getByRole('tab', { name: 'Jingle' }).click();
  await page.waitForFunction(() => { const r = document.querySelectorAll('[data-testid=library-row]'); return r.length === 9 && [...r].every((x) => /Station ID/.test(x.textContent)); }, null, { timeout: 3000 }).catch(() => {});
  check(`${tag}: type tab filters (server asked for jingles)`, await page.getByTestId('library-row').count() === 9 && station.calls.some((c) => c.path === '/assets' && c.query.asset_type === 'jingle'));
  await page.getByRole('tab', { name: 'All' }).click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid=library-row]').length === 50, null, { timeout: 3000 }).catch(() => {});
  await page.getByRole('button', { name: /Show more/ }).click();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid=library-row]').length === 100, null, { timeout: 3000 }).catch(() => {});
  check(`${tag}: "Show more" adds 50`, await page.getByTestId('library-row').count() === 100);
  // + End: placeholder at the end at once, then on the server
  await box.fill('Song number 76');
  await page.waitForFunction(() => document.querySelectorAll('[data-testid=library-row]').length === 1);
  const row = page.getByTestId('library-row').first();
  const endBtn = row.getByRole('button', { name: 'Add to the end of the queue' });
  const ms = await timeClick(page, endBtn, `() => [...document.querySelectorAll('[data-testid=library-row] button')].some(b => b.textContent === '…' && b.disabled)`, 3000, contextOpts.isMobile ? 'tap' : 'click');
  check(`${tag}: while sending, the row's buttons show … and are disabled (S-1)`, ms >= 0 && ms < 150, `${ms} ms`);
  await sleep(SLOW + 400);
  check(`${tag}: + End adds to the end of the queue`, writes(station, /\/queue\/add$/).length === 1 && station.pending().pop().asset_id === 'm76');
  // Pre-listen from a row: deck opens, pressed again stops
  const pfl = row.getByTestId('library-pfl');
  await pfl.click();
  await page.getByTestId('pfl-deck').waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: row ▶ opens the pre-listen deck`, await page.getByTestId('pfl-deck').count() === 1 && await pfl.getAttribute('aria-pressed') === 'true');
  await pfl.click();
  await sleep(300);
  check(`${tag}: pressing it again stops`, await pfl.getAttribute('aria-pressed') === 'false');
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Instant buttons ─────────────
async function cart(browser) {
  const tag = 'cart';
  const station = makeStation();
  const initDevices = () => {
    navigator.mediaDevices.enumerateDevices = async () => [
      { kind: 'audiooutput', deviceId: 'default', label: 'Default', groupId: 'g' },
      { kind: 'audiooutput', deviceId: 'hp-1', label: 'Headphones (USB)', groupId: 'g1' },
      { kind: 'audiooutput', deviceId: 'spk-2', label: 'Desk speakers', groupId: 'g2' },
    ];
  };
  const { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
  await ctx.addInitScript(initDevices);
  await page.reload();
  await page.getByTestId('queue-row').first().waitFor();
  await page.getByRole('radio', { name: 'Mix over' }).click();
  await page.reload();
  await page.getByTestId('queue-row').first().waitFor();
  check(`${tag}: fire mode remembered across a reload`, await page.getByRole('radio', { name: 'Mix over' }).getAttribute('aria-checked') === 'true');
  await page.getByRole('radio', { name: 'Play now' }).click();
  check(`${tag}: empty page hint`, await page.getByText('Page 1 is empty.', { exact: false }).count() === 1);
  check(`${tag}: jingles wall lists station IDs`, await page.getByTestId('hot-button').filter({ hasText: 'Station ID' }).count() === 9);
  check(`${tag}: sponsor spots wall lists spots`, await page.getByText('Sponsor spots', { exact: true }).count() === 1 && await page.getByTestId('hot-button').filter({ hasText: 'sponsor business' }).count() === 4);
  // Pin, then edit: ✕ removes, buttons cannot fire while editing
  await page.getByRole('searchbox').fill('Song number 33');
  await page.waitForFunction(() => document.querySelectorAll('[data-testid=library-row]').length === 1);
  await page.getByRole('button', { name: 'Add to instant buttons' }).click();
  await page.getByTestId('cart').getByRole('button', { name: 'Edit', exact: true }).click();
  const pinned = page.getByTestId('hot-button').filter({ hasText: 'Song number 33' });
  check(`${tag}: buttons cannot fire while editing`, await pinned.isDisabled());
  // Rename and delete sets
  page.once('dialog', (d) => d.accept('Chanukah'));
  await page.getByRole('button', { name: 'New set' }).click();
  page.once('dialog', (d) => d.accept('Chanukah 2026'));
  await page.getByRole('button', { name: 'Rename' }).click();
  check(`${tag}: Rename renames the set`, await page.getByLabel('Button set').evaluate((el) => el.selectedOptions[0].textContent) === 'Chanukah 2026');
  page.once('dialog', (d) => d.accept());
  await page.getByRole('button', { name: 'Delete set' }).click();
  check(`${tag}: Delete set removes it`, await page.getByLabel('Button set').locator('option').count() === 1);
  await page.getByRole('button', { name: 'Remove Song number 33 with a fairly long descriptive title from instant buttons' }).click();
  check(`${tag}: ✕ in edit mode removes the button`, await pinned.count() === 0);
  await page.getByTestId('cart').getByRole('button', { name: 'Done', exact: true }).click();
  // Headphones output: lists outputs, choice remembered, PFL audio sent there
  await page.getByTestId('pfl-toggle').click();
  const pick = page.getByLabel('Headphones output');
  if (await pick.count() === 0) await page.getByRole('button', { name: 'Choose the headphones output' }).click();
  await pick.waitFor({ timeout: 3000 }).catch(() => {});
  const outs = await pick.locator('option').allInnerTexts();
  check(`${tag}: headphones picker lists the outputs`, outs.includes('Headphones (USB)') && outs.includes('Desk speakers') && !outs.includes('Default'), outs.join(' | '));
  await pick.selectOption('hp-1');
  check(`${tag}: headphones choice remembered`, await page.evaluate(() => localStorage.getItem('kbr_pfl_output')) === 'hp-1');
  await page.getByTestId('hot-button').filter({ hasText: 'Station ID 2' }).click();
  await sleep(800);
  check(`${tag}: pre-listen audio goes to the chosen output`, (await page.evaluate(() => window.__sinks)).includes('hp-1'), JSON.stringify(await page.evaluate(() => window.__sinks)));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
  // No jingles: the hint
  const st2 = makeStation({ noJingles: true });
  const { ctx: c2, page: p2 } = await openRoom(browser, DESKTOP, st2);
  await p2.getByTestId('wall-empty').waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: no jingles -> hint`, /No station IDs or sweepers yet/.test(await p2.getByTestId('wall-empty').innerText().catch(() => '')));
  await c2.close();
}

// ── Pre-listen deck: keys, buttons, retry, phone ─────────────
async function pflDeck(browser, contextOpts, tag) {
  const station = makeStation();
  const { ctx, page, errors } = await openRoom(browser, contextOpts, station);
  await page.route(/\/api\/v1\/assets\/m1$/, (r) => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...station.byId.m1, duration: 151, intro_seconds: 12, metadata_extra: { audio_analysis: { cue_in_seconds: 1.5, cross_start_seconds: 140, cue_out_seconds: 148, version: 2 } } }) }));
  let failTone = true;
  await page.route('**/__tone/m1.wav', (r) => (failTone ? r.abort() : r.fallback()));
  if (contextOpts.isMobile) await page.getByRole('button', { name: /Library/ }).first().tap();
  await page.getByRole('searchbox').fill('Song number 1 ');
  await page.waitForFunction(() => document.querySelectorAll('[data-testid=library-row]').length >= 1);
  await page.getByTestId('library-pfl').first().click();
  const deck = page.getByTestId('pfl-deck');
  await deck.waitFor();
  await page.getByTestId('pfl-error').waitFor({ timeout: 5000 }).catch(() => {});
  check(`${tag}: network error -> retry message`, /Couldn't load this file/.test(await page.getByTestId('pfl-error').innerText().catch(() => '')));
  failTone = false;
  await page.getByTestId('pfl-playpause').click();
  await page.waitForFunction(() => !document.querySelector('[data-testid=pfl-error]'), null, { timeout: 5000 }).catch(() => {});
  await sleep(800);
  const secs = async () => { const [m, s] = (await page.getByTestId('pfl-elapsed').innerText()).split(':'); return +m * 60 + parseFloat(s); };
  check(`${tag}: ▶ retries and plays`, await page.getByTestId('pfl-error').count() === 0 && (await secs()) > 0.2, `${await secs()} s`);
  if (contextOpts.isMobile) {
    const r = await deck.evaluate((el) => { const b = el.getBoundingClientRect(); return { h: b.height, w: b.width, vw: innerWidth, vh: innerHeight }; });
    check(`${tag}: compact deck on a phone (under a quarter of the screen, full width, no overflow)`, r.h <= r.vh / 4 && r.w <= r.vw && await page.getByTestId('pfl-scrub').isVisible(), JSON.stringify(r));
  }
  const at = async () => { await sleep(250); return secs(); };
  await deck.getByRole('button', { name: 'Forward 10 seconds' }).click();
  const f = await at();
  await deck.getByRole('button', { name: 'Back 10 seconds' }).click();
  const b = await at();
  check(`${tag}: Forward 10 s / Back 10 s buttons`, f >= 10 && f < 13.5 && b >= 0 && b < f - 8, `${f} -> ${b}`);
  await deck.getByRole('button', { name: 'Intro end', exact: true }).click();
  const ie = await at();
  await deck.getByRole('button', { name: 'End', exact: true }).click();
  const en = await at();
  await deck.getByRole('button', { name: 'Start', exact: true }).click();
  const stv = await at();
  check(`${tag}: jump buttons Intro end, End -5 s, Start`, ie >= 12 && ie < 14 && en >= 143 && en < 145 && stv >= 1.5 && stv < 3, `${ie}, ${en}, ${stv}`);
  if (!contextOpts.isMobile) {
    await page.getByTestId('pfl-scrub').focus();
    const key = async (k) => { await page.keyboard.press(k); return at(); };
    const l = await key('l'); const j = await key('j');
    const k5 = await key('5'); const home = await key('Home'); const end = await key('End');
    check(`${tag}: L / J move 10 s`, l - stv > 9 && l - stv < 11.5 && Math.abs(l - j - 10) < 1.5, `${stv} -> ${l} -> ${j}`);
    check(`${tag}: 5 jumps to half way, Home to the start, End to 10 s before the end`, k5 > 74 && k5 < 78 && home < 1 && end > 140 && end < 143, `${k5}, ${home}, ${end}`);
    await page.keyboard.press('k');
    const p0 = await at(); await sleep(600);
    check(`${tag}: K pauses`, Math.abs((await secs()) - p0) < 0.05);
    await page.keyboard.press('ArrowLeft');
    check(`${tag}: ← moves 5 s back`, Math.abs(p0 - (await at()) - 5) < 1);
    await page.keyboard.press('Escape');
    await sleep(300);
    check(`${tag}: Esc closes the deck`, await page.getByTestId('pfl-deck').count() === 0);
  }
  check(`${tag}: nothing sent to the station from the deck`, station.calls.filter((c) => c.method !== 'GET' && !c.path.startsWith('/activity')).length === 0, station.calls.filter((c) => c.method !== 'GET').map((c) => c.path).join());
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Side panels: go live, end show, schedule, splitter ─────────────
async function side(browser) {
  const tag = 'side panels';
  const station = makeStation();
  const { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
  await page.getByRole('tab', { name: 'Schedule' }).click();
  await page.getByText('Afternoon music').waitFor();
  await page.getByLabel('Check a time').fill('2026-12-25T20:00');
  await page.getByText('Night shiurim').waitFor({ timeout: 4000 }).catch(() => {});
  check(`${tag}: "Check a time" previews another time`, await page.getByText('Night shiurim').count() === 1 && await page.getByText('Blacked out').count() === 1
    && station.calls.some((c) => c.path === '/schedules/timeline-preview' && c.query.at_time));
  await page.getByRole('button', { name: 'Now', exact: true }).click();
  await page.getByText('Afternoon music').waitFor({ timeout: 4000 }).catch(() => {});
  check(`${tag}: Now resets to the present`, await page.getByText('Afternoon music').count() === 1);
  // Splitter: keyboard and double-click reset
  const handle = page.getByTestId('split-handle');
  const v = async () => +(await handle.getAttribute('aria-valuenow'));
  const v0 = await v();
  await handle.focus(); await page.keyboard.press('ArrowDown'); await page.keyboard.press('ArrowDown');
  const v1 = await v();
  await page.keyboard.press('ArrowUp');
  const v2 = await v();
  await handle.dblclick();
  check(`${tag}: splitter ArrowDown/ArrowUp move by 5, double-click resets`, v1 === v0 + 10 && v2 === v0 + 5 && await v() === 60, `${v0} ${v1} ${v2} ${await v()}`);
  // Go live: type a name + Enter
  await page.getByRole('tab', { name: 'Go live' }).click();
  await page.getByPlaceholder('Show name').fill('Late night requests');
  await page.getByPlaceholder('Show name').press('Enter');
  await page.waitForURL(/\/admin\/live\/[^/]+\/host/, { timeout: 5000 }).catch(() => {});
  const created = writes(station, /^\/live-shows$/).pop();
  check(`${tag}: Go live (Enter) creates and starts the show, opens the host console`, created?.body.title === 'Late night requests' && writes(station, /\/live-shows\/ls2\/start$/).length === 1 && /\/admin\/live\/ls2\/host/.test(page.url()), page.url());
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  await page.getByRole('tab', { name: '● Live' }).click();
  check(`${tag}: scheduled show Start disabled while a show is live`, await page.getByRole('button', { name: 'Start', exact: true }).isDisabled());
  await page.getByRole('button', { name: 'End show and return to automation' }).click();
  await page.getByRole('button', { name: 'Keep going' }).click();
  check(`${tag}: End show asks first; Keep going cancels`, writes(station, /\/end$/).length === 0 && await page.getByRole('button', { name: 'End show and return to automation' }).count() === 1);
  await page.getByRole('button', { name: 'End show and return to automation' }).click();
  await page.getByRole('button', { name: 'Yes, end the show' }).click();
  await page.getByRole('tab', { name: 'Go live' }).waitFor({ timeout: 5000 }).catch(() => {});
  check(`${tag}: "Yes, end the show" ends it`, writes(station, /\/live-shows\/ls2\/end$/).length === 1 && await page.getByRole('tab', { name: 'Go live' }).count() === 1);
  await page.getByRole('tab', { name: 'Go live' }).click();
  await page.getByRole('button', { name: 'Start', exact: true }).click();
  await page.waitForURL(/\/admin\/live\/ls1\/host/, { timeout: 5000 }).catch(() => {});
  check(`${tag}: a scheduled show's Start starts it`, writes(station, /\/live-shows\/ls1\/start$/).length === 1);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── S-1: press to visible change, with a slow (300 ms) server ─────────────
async function speed(browser, contextOpts, tag) {
  const station = makeStation({ queueSize: 20 });
  const SLOW = 300;
  const { ctx, page, errors } = await openRoom(browser, contextOpts, station, { delay: (p, m) => (m !== 'GET' ? SLOW : 0) });
  const how = contextOpts.isMobile ? 'tap' : 'click';
  const res = [];
  const t = async (name, loc, cond) => { const ms = await timeClick(page, loc, cond, 3000, how); res.push([name, ms]); await sleep(SLOW + 400); };
  await t('Go next shows busy', page.getByRole('button', { name: /Go next/ }).first(), `() => [...document.querySelectorAll('button')].some(b => b.textContent === '…' && b.disabled)`);
  await sleep(4000);
  await t('Loop shows busy', page.locator('[data-testid=loop]:visible').first(), `() => [...document.querySelectorAll('[data-testid=loop]')].some(b => b.disabled)`);
  await t('Insert-here arms the marker', page.getByTestId('queue-row').nth(3).getByTestId('insert-here'), `() => !!document.querySelector('[data-testid=insert-marker]') || !!document.querySelector('[data-testid=insert-banner]')`);
  if (contextOpts.isMobile) { await page.getByRole('button', { name: 'Done' }).tap(); await page.getByRole('button', { name: 'Buttons' }).tap(); } else await page.keyboard.press('Escape');
  await t('Play now button pulses', page.getByTestId('hot-button').filter({ hasText: 'Station ID 5' }), `() => !!document.querySelector('[data-testid=hot-button].animate-pulse')`);
  await t('Play next mode selected', page.getByRole('radio', { name: 'Play next' }), `() => document.querySelector('[role=radio][aria-checked=true]')?.textContent === 'Play next'`);
  await t('Pre-listen opens the deck', page.getByTestId('pfl-toggle'), `() => !!document.querySelector('[data-testid=pfl-banner]')`);
  for (const [name, ms] of res) check(`${tag}: ${name} within 150 ms (S-1)`, ms >= 0 && ms < 150, `${ms} ms`);
  // S-8: a change on air shows in the header within 1 s
  const nextA = station.byId[station.pending()[0].asset_id];
  const p = timeUntil(page, `() => document.querySelector('[data-testid=control-room] header').innerText.includes(${JSON.stringify(nextA.title)})`, 3000);
  station.goOnAir(nextA.id);
  const shown = await p;
  check(`${tag}: a change on air shows in the header within 1 s (S-8)`, shown >= 0 && shown <= 1000, `${shown} ms`);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Mix over: not listening, a file that cannot load ─────────────
async function mixOver(browser) {
  const tag = 'mix over';
  const station = makeStation();
  const { ctx, np, page } = await openRoom(browser, DESKTOP, station);
  let fetched = 0;
  await page.route('**/mix-*.wav', (r) => { fetched++; return r.fulfill({ contentType: 'audio/wav', body: Buffer.alloc(0) }); });
  await listenHere(page);
  fetched = 0;
  np.raw({ type: 'mix_over', data: { url: `${BASE}/mix-b.wav`, asset_id: 'j1' } });
  await sleep(800);
  check(`${tag}: one message, fetched once`, fetched === 1, `${fetched}`);
  const on = station.playing().asset_id;
  const before = (await page.evaluate(() => window.__audible())).find((d) => d.src === on);
  np.raw({ type: 'mix_over', data: { url: 'http://localhost:1/blocked.wav', asset_id: 'j1' } });
  await sleep(1000);
  const after = (await page.evaluate(() => window.__audible())).find((d) => d.src === on);
  check(`${tag}: a file that cannot be fetched leaves the item on air untouched`, before && after && after.gain === before.gain && after.t > before.t, `${JSON.stringify(before)} -> ${JSON.stringify(after)}`);
  await ctx.close();
  // A tab whose player never started (no click yet: the browser keeps its audio suspended)
  const st2 = makeStation();
  const { ctx: c2, np: np2, page: p2 } = await openRoom(browser, DESKTOP, st2);
  await c2.addInitScript(() => {
    const Real = window.AudioContext;
    window.AudioContext = class extends Real {
      constructor(...a) { super(...a); void Real.prototype.suspend.call(this); }
      resume() { return Promise.resolve(); }
    };
  });
  await p2.reload();
  await p2.getByTestId('queue-row').first().waitFor();
  let f2 = 0;
  await p2.route('**/mix-*.wav', (r) => { f2++; return r.fulfill({ contentType: 'audio/wav', body: Buffer.alloc(0) }); });
  await sleep(500);
  np2.raw({ type: 'mix_over', data: { url: `${BASE}/mix-c.wav`, asset_id: 'j1' } });
  await sleep(800);
  check(`${tag}: skipped in a tab whose player is not running`, f2 === 0, `${f2} fetches`);
  await c2.close();
}

// ── Hard timed item loaded 60 s ahead on the idle deck (V1-129) ─────────────
async function timedPreload(browser) {
  const tag = 'timed preload';
  const station = makeStation();
  const T = Date.now() + 50_000;
  station.add({ asset_id: 'tc', position: 99, source: 'block', preempt_at: new Date(T).toISOString(), hard_timed: true });
  const { ctx, page } = await openRoom(browser, DESKTOP, station);
  await listenHere(page);
  await sleep(1500);
  const decks = await page.evaluate(() => window.__decks());
  check(`${tag}: a hard time check due within 60 s waits on the idle deck instead of the next song`, decks.some((d) => d.src === 'tc' && !d.playing), JSON.stringify(decks));
  await ctx.close();
  const st2 = makeStation();
  st2.add({ asset_id: 'tc', position: 99, source: 'block', preempt_at: new Date(Date.now() + 120_000).toISOString(), hard_timed: true });
  const { ctx: c2, page: p2 } = await openRoom(browser, DESKTOP, st2);
  await listenHere(p2);
  await sleep(1500);
  const d2 = await p2.evaluate(() => window.__decks());
  check(`${tag}: one due in 2 minutes is not loaded yet (the next song is)`, !d2.some((d) => d.src === 'tc') && d2.some((d) => d.src === st2.pending()[0].asset_id), JSON.stringify(d2));
  await c2.close();
}

// ── Engine mode: the control room plays the stream, falls back on error ─────────────
async function engineMode(browser, stream) {
  const tag = 'engine mode';
  for (const broken of [false, true]) {
    const station = makeStation();
    const base = station.nowPlaying;
    station.nowPlaying = (extra) => ({ ...base(extra), stream_url: broken ? stream.missing : stream.url(station.SID) });
    const { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
    await page.locator('button:visible', { hasText: 'Listen' }).first().click();
    await sleep(2500);
    const el = await page.evaluate(() => { const a = document.querySelector('[data-testid=control-room] audio'); return { src: a.src, paused: a.paused, muted: a.muted, t: a.currentTime }; });
    if (!broken) {
      check(`${tag}: listening plays the server stream`, el.src === stream.url(station.SID) && !el.paused && !el.muted && el.t > 0.3, JSON.stringify(el));
      check(`${tag}: the built-in player only measures (not heard)`, (await page.evaluate(() => window.__outLevel())) < 0.01);
    } else {
      check(`${tag}: a broken stream falls back to the built-in player`, (await page.evaluate(() => window.__outLevel())) > 0.1 && (await page.evaluate(() => window.__audible().length)) === 1, JSON.stringify(el));
    }
    check(`${tag}${broken ? ' (broken)' : ''}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

const browser = await chromium.launch();
const run = (name) => !ONLY || ONLY.split(',').includes(name);
try {
  if (run('transport')) { await transport(browser); await transportEnd(browser); }
  if (run('next')) { await nextAndFades(browser, DESKTOP, 'next desktop'); await nextAndFades(browser, PHONE, 'next iPhone'); }
  if (run('refused')) await nextRefused(browser);
  if (run('loop')) await loop(browser);
  if (run('toasts')) await toasts(browser);
  if (run('badges')) await badges(browser);
  if (run('tabs')) await oneSoundingTab(browser);
  if (run('rows')) await queueRows(browser);
  if (run('actions')) await queueActions(browser);
  if (run('days')) await dayFilter(browser);
  if (run('phone')) await phoneRows(browser);
  if (run('library')) { await library(browser, DESKTOP, 'library desktop'); await library(browser, PHONE, 'library iPhone'); }
  if (run('cart')) await cart(browser);
  if (run('pfl')) { await pflDeck(browser, DESKTOP, 'pfl desktop'); await pflDeck(browser, PHONE, 'pfl iPhone'); }
  if (run('side')) await side(browser);
  if (run('speed')) { await speed(browser, DESKTOP, 'speed desktop'); await speed(browser, PHONE, 'speed iPhone'); }
  if (run('mix')) await mixOver(browser);
  if (run('timed')) await timedPreload(browser);
  if (run('engine')) { const s = await startStreamServer(STREAM_PORT); try { await engineMode(browser, s); } finally { await s.close(); } }
} finally {
  await browser.close();
}
process.exit(done());
