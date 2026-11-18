/**
 * Browser check of the control room's Sponsor panel: tapping a sponsorship spot (on
 * air or in the queue) opens the sponsor, its contract(s), today's and coming plays
 * with Skip / Put back, missed plays, Pause / Resume, and links into the sponsorship
 * pages, instead of the song options (Shmuel 2026-11-18). Fake backend; desktop and
 * iPhone sizes; each of the three stations; manager, DJ and viewer.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4311 --strictPort &)
 *   BASE_URL=http://localhost:4311 node e2e/control-room-sponsor-panel.mjs
 *
 * Exits non-zero if a check fails.
 */
import { chromium, devices, checker, sleep, makeStation, adminContext, newPage } from './lib/fake-station.mjs';
import { allowEverywhere } from './lib/guard.mjs';

allowEverywhere(/skip-sponsorship|due within 2 minutes|status of 409/, 'a play too close to air is refused on purpose to see the message');

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const SHOTS = process.env.SHOTS || '';
const DESKTOP = { viewport: { width: 1440, height: 900 } };
const PHONE = { ...devices['iPhone 13'] };
const PHONE_SE = { ...devices['iPhone SE'] };
const PHONE_LAND = { ...devices['iPhone 13 landscape'], screen: { width: 844, height: 390 } };
const { check, done } = checker({});

const STATIONS = [
  { id: 'st-main', name: "Kol B'ramah Main", timezone: 'America/New_York', is_active: true, channels: [] },
  { id: 'st-stories', name: "Kol B'ramah - All Day Stories", timezone: 'America/New_York', is_active: true, channels: [] },
  { id: 'st-jlm', name: 'KBR Jerusalem', timezone: 'Asia/Jerusalem', is_active: true, channels: [] },
];
const SPONSOR = { id: 'sp27', name: 'TEST SPONSOR 27', company_name: 'Kesher Moving and Storage', contact_phone: '732-555-0127', contact_email: null };
const fmt = (iso, tz) => new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: tz });

/** A fake station with sponsorship plays of spot s1 in its queue, answering the panel's calls. */
function sponsorStation(sid, opts = {}) {
  let station;
  const st = STATIONS.find((s) => s.id === sid);
  const contract = {
    id: 'c27', name: 'Kesher weekday mornings', sponsor: SPONSOR, station_id: sid, station_name: st.name,
    on_this_station: true, state: 'running', days_of_week: '0,1,2,3,6', days_text: 'Sun-Thu',
    dayparts: ['morning', 'afternoon'], plays_per_daypart: 2, fixed_times: [], timing_mode: null,
    start_date: '2026-11-01', end_date: '2026-12-31', is_active: true,
  };
  const elsewhere = { ...contract, id: 'c28', name: 'Kesher Jerusalem evenings', station_id: 'st-other', station_name: 'Another station', on_this_station: false, state: 'paused', is_active: false };
  const extra = (method, path, query) => {
    if (path === '/auth/me' && opts.role) return { id: 'u1', email: `${opts.role}@example.com`, role: opts.role, is_active: true };
    if (opts.noContract && path.startsWith('/spot-schedules/for-asset/')) {
      return { asset: { ...station.byId.s2, duration: 30 }, station: { id: sid, name: st.name, timezone: st.timezone }, owner: null, contracts: [], aired_today: 0, today: [], upcoming: [], missed: [] };
    }
    const fa = /^\/spot-schedules\/for-asset\/([^/]+)$/.exec(path);
    if (fa && method === 'GET') {
      if (opts.failRead) return { __status: 500 };
      const a = station.byId[fa[1]];
      const midnight = new Date(); midnight.setHours(24, 0, 0, 0);
      const rows = station.queue.filter((e) => e.asset_id === a.id && e.preempt_at);
      const out = (e) => {
        const due = new Date(e.preempt_at).getTime();
        const change = e.source === 'ad_slot' && due > Date.now() + 120e3;
        return {
          entry_id: e.id, status: e.status, status_text: { pending: 'Waiting', played: 'Aired', playing: 'On air', cancelled: 'Skipped by staff' }[e.status],
          source: e.source, at: e.preempt_at, due: e.preempt_at, local_time: fmt(e.preempt_at, st.timezone),
          local_day: due < midnight.getTime() ? 'Today' : 'Tomorrow', timing_mode: 'soft',
          can_skip: change && e.status === 'pending', can_restore: change && e.status === 'cancelled',
        };
      };
      return {
        asset: { id: a.id, title: a.title, artist: a.artist, asset_type: 'spot', category: 'Spots', duration: a.duration },
        station: { id: sid, name: st.name, timezone: st.timezone },
        owner: SPONSOR,
        contracts: [{ ...contract, is_active: station.contractOn, state: station.contractOn ? 'running' : 'paused' }, elsewhere],
        aired_today: 2,
        today: rows.filter((e) => new Date(e.preempt_at) < midnight).map(out),
        upcoming: rows.filter((e) => new Date(e.preempt_at) >= midnight).map(out),
        missed: [{ when: 'past', local_date: '2026-11-17', day: 'Tue Nov 17', daypart: 'morning', promised: 2, played: 1, missed: 1, moved: 0, note: 'Did not air' }],
      };
    }
    const sk = /^\/stations\/[^/]+\/queue\/([^/]+)\/skip-sponsorship$/.exec(path);
    if (sk && method === 'POST') {
      const e = station.queue.find((x) => x.id === sk[1]);
      if (new Date(e.preempt_at).getTime() <= Date.now() + 120e3) return { __status: 409, detail: 'It is due within 2 minutes and may already be loading.' };
      const restore = query.restore === 'true' || query.restore === true;
      e.status = restore ? 'pending' : 'cancelled';
      return { entry_id: e.id, status: e.status };
    }
    if (path === '/spot-schedules/c27' && method === 'PUT') {
      station.contractOn = !station.contractOn;
      return { ...contract, is_active: station.contractOn, sponsor_id: SPONSOR.id, asset_id: 's1', created_at: '', updated_at: '', warnings: [] };
    }
    if (path === '/spot-schedules' && method === 'GET') {
      return [{ id: 'c27', sponsor_id: SPONSOR.id, asset_id: 's1', station_id: sid, name: contract.name, days_of_week: '0,1,2,3,6', dayparts: 'morning,afternoon', plays_per_daypart: 2, fixed_times: null, timing_mode: null, start_date: '2026-11-01', end_date: '2026-12-31', is_active: true, created_at: '', updated_at: '' }];
    }
    if (path === '/sponsors') return [{ ...SPONSOR, length_seconds: 30, priority: 1, audio_file_path: '', target_rules: null, insertion_policy: 'between_tracks', user_id: null }];
    if (path === '/spot-schedules/preview') return { station_id: sid, timezone: st.timezone, days: 7, planned: [], unplaced: [], off_air: [], spots_off: false };
    if (path === '/spot-schedules/timing-defaults') return { station_id: sid, on_the_hour: 'hard', other: 'soft' };
    return undefined;
  };
  station = makeStation({ queueSize: 10, sid, stations: STATIONS, ...opts, extra });
  station.contractOn = true;
  station.byId.s1.title = 'TEST SPONSOR 27 - Kesher Moving and Storage';
  station.byId.s1.artist = "Kol B'ramah";
  const soon = (min) => new Date(Date.now() + min * 60e3).toISOString();
  station.ad = {
    near: station.add({ asset_id: 's1', position: 3, source: 'ad_slot', preempt_at: soon(1), hard_timed: false }),
    later: station.add({ asset_id: 's1', position: 6, source: 'ad_slot', preempt_at: soon(40), hard_timed: false }),
    tomorrow: station.add({ asset_id: 's1', position: 90, source: 'ad_slot', preempt_at: soon(26 * 60), hard_timed: false }),
  };
  return station;
}

async function openRoom(browser, contextOpts, station) {
  const { ctx } = await adminContext(browser, contextOpts, station, { storage: { radio_last_station: station.SID } });
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  return { ctx, page, errors };
}
const rowOf = (page, id) => page.locator(`[data-testid=queue-row][data-entry-id="${id}"]`);
const panel = (page) => page.getByTestId('sponsor-panel');
const toastText = (page) => page.getByTestId('toast').innerText().catch(() => '');
const writes = (st, re) => st.calls.filter((c) => c.method !== 'GET' && re.test(c.path));
const airCalls = (st) => writes(st, /\/queue\/(skip$|fire-now|play-next|insert|panic|start|loop|mix-over|move-up|move-down|reorder)/);
const shot = async (page, name) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` }); };

async function openForRow(page, id, touch) {
  if (touch) {
    await rowOf(page, id).locator('[draggable=true]').tap();
    await page.getByTestId('row-song-touch').tap();
  } else {
    await rowOf(page, id).hover({ position: { x: 300, y: 8 } });
    await rowOf(page, id).getByTestId('row-song').click();
  }
  await panel(page).waitFor();
  await page.getByTestId('sponsor-contract').first().waitFor();
}

// ── Desktop, each of the three stations, as a manager ──
async function desktop(browser, sid) {
  const name = STATIONS.find((s) => s.id === sid).name;
  const tag = `desktop ${name}`;
  const station = sponsorStation(sid);
  const { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
  const order = station.pending().map((e) => e.id).join();
  await openForRow(page, station.ad.later.id, false);
  check(`${tag}: the row's button says Spot`, (await rowOf(page, station.ad.later.id).getByTestId('row-song').innerText()).trim() === 'Spot');
  check(`${tag}: a sponsorship row opens the Sponsor panel, not the song panel`, await panel(page).isVisible() && await page.getByTestId('song-panel').count() === 0);
  check(`${tag}: the panel names the spot and the sponsor with its business`, (await page.getByTestId('sponsor-spot-name').innerText()).includes('TEST SPONSOR 27') && /Sponsor: TEST SPONSOR 27 \(Kesher Moving and Storage\)/.test(await page.getByTestId('sponsor-name').innerText()));
  check(`${tag}: it says how often it aired today on this station`, (await page.getByTestId('sponsor-summary').innerText()).includes(`aired 2× today on ${name}`));
  check(`${tag}: the sponsor's phone is a tap-to-call link`, await page.getByTestId('sponsor-contact').locator('a[href="tel:732-555-0127"]').count() === 1);
  const card = page.getByTestId('sponsor-contracts').getByTestId('sponsor-contract').first();
  const cardText = await card.innerText();
  check(`${tag}: contract shows name, RUNNING, station, days, times of day, plays and dates`,
    /Kesher weekday mornings/.test(cardText) && /RUNNING/.test(cardText) && cardText.includes(name) && /Sun-Thu/.test(cardText)
      && /2 plays in each: Morning \(6 AM-12 PM\), Afternoon \(12-6 PM\)/.test(cardText) && /From Nov 1, 2026 until Dec 31, 2026/.test(cardText), cardText);
  check(`${tag}: another station's contract is folded away, not mixed in`, await page.getByTestId('sponsor-contracts').getByTestId('sponsor-contract').count() === 2
    && !(await page.getByTestId('sponsor-elsewhere').getAttribute('open') !== null) && /On other stations \(1\)/.test(await page.getByTestId('sponsor-elsewhere').innerText()));
  const today = page.getByTestId('sponsor-today');
  const near = fmt(station.ad.near.preempt_at, STATIONS.find((s) => s.id === sid).timezone);
  const later = fmt(station.ad.later.preempt_at, STATIONS.find((s) => s.id === sid).timezone);
  check(`${tag}: today lists both plays with the station's clock times`, (await today.innerText()).includes(near) && (await today.innerText()).includes(later), await today.innerText());
  check(`${tag}: the play due in a minute has no Skip, the later one has`, await today.getByTestId('sponsor-play').nth(0).getByTestId('sponsor-skip').count() === 0 && await today.getByTestId('sponsor-play').nth(1).getByTestId('sponsor-skip').count() === 1);
  check(`${tag}: coming days lists tomorrow's play`, /Tomorrow/.test(await page.getByTestId('sponsor-coming').innerText()));
  check(`${tag}: missed plays show day, time of day, and how many aired`, /Tue Nov 17, morning: 1 of 2 aired/.test(await page.getByTestId('sponsor-missed').innerText()));
  check(`${tag}: links go to Spot Schedule (this station), Sponsors, Campaigns (this sponsor), Schedule Check`,
    await page.getByTestId('link-spot-schedule').getAttribute('href') === `/admin/spot-schedule?station=${sid}`
      && await page.getByTestId('link-sponsors').getAttribute('href') === '/admin/sponsors'
      && await page.getByTestId('link-campaigns').getAttribute('href') === '/admin/campaigns?sponsor=sp27'
      && await page.getByTestId('link-schedule-check').getAttribute('href') === '/admin/schedule-check');
  check(`${tag}: opening the panel moved nothing in the queue`, station.pending().map((e) => e.id).join() === order && airCalls(station).length === 0);
  await shot(page, `sponsor-panel-${sid}`);

  // Skip the 40-minute play, then put it back
  const t0 = Date.now();
  await today.getByTestId('sponsor-play').nth(1).getByTestId('sponsor-skip').click();
  await today.getByTestId('sponsor-restore').waitFor({ timeout: 4000 });
  check(`${tag}: Skip shows "Skipped by staff" and Put back within 1.5 s`, Date.now() - t0 < 1500 && /Skipped by staff/.test(await today.getByTestId('sponsor-play').nth(1).innerText()), `${Date.now() - t0} ms`);
  check(`${tag}: one skip call for that play, nothing sent to the player`, writes(station, /skip-sponsorship$/).length === 1 && writes(station, /skip-sponsorship$/)[0].path.includes(station.ad.later.id) && airCalls(station).length === 0);
  let t = await toastText(page);
  check(`${tag}: toast names the time and says it counts as missed, with an undo`, t.includes(later) && /counts as a missed play/.test(t) && /Put back undoes it/.test(t), t);
  await today.getByTestId('sponsor-restore').click();
  await today.getByTestId('sponsor-play').nth(1).getByTestId('sponsor-skip').waitFor({ timeout: 4000 });
  t = await toastText(page);
  check(`${tag}: Put back returns it to Waiting`, station.ad.later.status === 'pending' && /back in the queue/.test(t), t);

  // Pause and resume the contract
  await card.getByTestId('sponsor-pause').click();
  await card.getByTestId('sponsor-contract-state').filter({ hasText: 'PAUSED' }).waitFor({ timeout: 4000 });
  t = await toastText(page);
  check(`${tag}: Pause sends is_active false and says the coming plays leave this station's queue`,
    writes(station, /\/spot-schedules\/c27$/).at(-1)?.body?.is_active === false && /is paused/.test(t) && t.includes(name), t);
  check(`${tag}: the button now offers Resume`, /Resume this contract/.test(await card.getByTestId('sponsor-pause').innerText()));
  await card.getByTestId('sponsor-pause').click();
  await card.getByTestId('sponsor-contract-state').filter({ hasText: 'RUNNING' }).waitFor({ timeout: 4000 });
  check(`${tag}: Resume turns it back on`, writes(station, /\/spot-schedules\/c27$/).at(-1)?.body?.is_active === true && /running again/.test(await toastText(page)));

  // Recording options are one button away
  await page.getByTestId('sponsor-item-options').click();
  await page.getByTestId('song-panel').waitFor();
  check(`${tag}: "Recording options" opens the song options for the same spot`, (await page.getByTestId('song-name').innerText()).includes('TEST SPONSOR 27') && await panel(page).count() === 0);
  await page.keyboard.press('Escape');
  check(`${tag}: Escape closes it`, await page.getByTestId('song-panel').count() === 0);

  // A music row still opens the song panel
  const music = station.pending().find((e) => e.asset_id.startsWith('m'));
  await rowOf(page, music.id).locator('[draggable=true]').hover();
  await rowOf(page, music.id).getByTestId('row-song').click();
  await page.getByTestId('song-panel').waitFor();
  check(`${tag}: a song row still opens the song panel, its button says Song`, await panel(page).count() === 0 && (await rowOf(page, music.id).getByTestId('row-song').innerText()).trim() === 'Song');
  await page.keyboard.press('Escape');
  // The weather is of type spot too, but it is not a sponsorship
  const wx = station.add({ asset_id: 'wx', position: 8, source: 'manual' });
  await sleep(2500);
  await rowOf(page, wx.id).hover({ position: { x: 300, y: 8 } });
  await rowOf(page, wx.id).getByTestId('row-song').click();
  await page.getByTestId('song-panel').waitFor();
  check(`${tag}: the weather (type spot, category weather) opens the song panel`, await panel(page).count() === 0);
  await page.keyboard.press('Escape');

  // Contract link opens Spot Schedule pointing at the contract
  await openForRow(page, station.ad.later.id, false);
  await page.getByTestId('sponsor-contract-link').first().click();
  await page.waitForURL(/\/admin\/spot-schedule\?contract=c27/);
  await page.locator('#contract-c27').waitFor({ timeout: 5000 });
  check(`${tag}: "Change it on Spot Schedule" opens that page with the contract marked`, /outline/.test(await page.locator('#contract-c27').getAttribute('class')));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── The spot on air, a refused skip, Escape and the backdrop ──
async function onAir(browser) {
  const tag = 'on air';
  const station = sponsorStation('st-main');
  const { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
  station.goOnAir('s1');
  await page.getByTestId('now-song').waitFor();
  await sleep(1200);
  await page.getByTestId('now-song').click();
  await panel(page).waitFor();
  check(`${tag}: the Song button beside a spot on air opens the Sponsor panel marked ON AIR`, await page.getByTestId('sponsor-on-air').isVisible());
  await page.getByTestId('sponsor-contract').first().waitFor();
  // A play inside 2 minutes refused by the server shows the reason and stays
  station.ad.later.preempt_at = new Date(Date.now() + 60e3).toISOString();
  await page.getByTestId('sponsor-today').getByTestId('sponsor-skip').first().click();
  await page.getByTestId('toast').filter({ hasText: 'due within 2 minutes' }).waitFor({ timeout: 4000 });
  check(`${tag}: a skip refused by the server shows its reason and nothing changes`, station.ad.later.status === 'pending');
  await page.keyboard.press('Escape');
  check(`${tag}: Escape closes`, await panel(page).count() === 0);
  await page.getByTestId('now-song').click();
  await panel(page).waitFor();
  await page.getByTestId('sponsor-panel-backdrop').click({ position: { x: 5, y: 5 } });
  check(`${tag}: clicking outside closes`, await panel(page).count() === 0);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Roles: a DJ can skip but not pause; a viewer only looks ──
async function roles(browser) {
  for (const role of ['dj', 'viewer']) {
    const tag = `role ${role}`;
    const station = sponsorStation('st-main', { role });
    const { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
    await openForRow(page, station.ad.later.id, false);
    check(`${tag}: no Pause button`, await page.getByTestId('sponsor-pause').count() === 0);
    const skips = await page.getByTestId('sponsor-skip').count();
    check(`${tag}: ${role === 'dj' ? 'can' : 'cannot'} skip a play`, role === 'dj' ? skips > 0 : skips === 0);
    check(`${tag}: no links to pages this role cannot open`, await page.getByTestId('link-spot-schedule').count() === 0 && await page.getByTestId('link-sponsors').count() === 0 && await page.getByTestId('sponsor-contract-link').count() === 0);
    check(`${tag}: still sees sponsor, contract and plays`, /TEST SPONSOR 27/.test(await page.getByTestId('sponsor-name').innerText()) && await page.getByTestId('sponsor-play').count() >= 2);
    check(`${tag}: no writes from just looking`, station.calls.filter((c) => c.method !== 'GET' && !/page-view/.test(c.path)).length === 0);
    check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── A spot no contract uses, and a failed read ──
async function edges(browser) {
  let station = sponsorStation('st-stories', { noContract: true });
  let r = await openRoom(browser, DESKTOP, station);
  const loose = station.add({ asset_id: 's2', position: 4, source: 'manual' });
  await sleep(2500);
  await rowOf(r.page, loose.id).locator('[draggable=true]').hover();
  await rowOf(r.page, loose.id).getByTestId('row-song').click();
  await panel(r.page).waitFor();
  await r.page.getByTestId('sponsor-no-contract').waitFor();
  check('no contract: says no contract schedules it here and no sponsor is linked',
    /No contract schedules this recording on Kol B'ramah - All Day Stories/.test(await r.page.getByTestId('sponsor-no-contract').innerText())
      && /No sponsor is linked/.test(await r.page.getByTestId('sponsor-name').innerText()));
  check('no contract: missed plays say nothing is promised', /nothing is promised/.test(await r.page.getByTestId('sponsor-missed').innerText()));
  check('no contract: no script errors', r.errors.length === 0, r.errors.join(' | '));
  await r.ctx.close();

  station = sponsorStation('st-main', { failRead: true });
  r = await openRoom(browser, DESKTOP, station);
  await rowOf(r.page, station.ad.later.id).locator('[draggable=true]').hover();
  await rowOf(r.page, station.ad.later.id).getByTestId('row-song').click();
  await panel(r.page).waitFor();
  await r.page.getByTestId('sponsor-error').waitFor({ timeout: 15000 });
  check('failed read: says it could not load, offers Try again, and Recording options still work',
    /could not be loaded/.test(await r.page.getByTestId('sponsor-error').innerText()) && await r.page.getByTestId('sponsor-item-options').isVisible());
  await r.ctx.close();
}

// ── iPhone sizes ──
async function phone(browser, contextOpts, tag, landscape = false) {
  const station = sponsorStation('st-main');
  const { ctx, page, errors } = await openRoom(browser, contextOpts, station);
  const vp = page.viewportSize();
  if (landscape) {
    station.goOnAir('s1');
    await sleep(1500);
    await page.getByTestId('now-song').tap();
    await panel(page).waitFor();
    await page.getByTestId('sponsor-contract').first().waitFor();
  } else {
    await openForRow(page, station.ad.later.id, true);
  }
  const box = await panel(page).boundingBox();
  check(`${tag}: panel fits the screen`, box.x >= 0 && box.x + box.width <= vp.width + 0.5 && box.y >= -0.5 && box.y + box.height <= vp.height + 0.5, JSON.stringify(box));
  check(`${tag}: page does not scroll sideways`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  const wide = await panel(page).evaluate((el) => [...el.querySelectorAll('*')].filter((x) => x.scrollWidth > el.clientWidth + 1).map((x) => x.className).slice(0, 3));
  check(`${tag}: nothing inside the panel is wider than it`, wide.length === 0, JSON.stringify(wide));
  for (const id of ['sponsor-skip', 'sponsor-pause', 'link-spot-schedule', 'sponsor-item-options']) {
    const el = page.getByTestId(id).first();
    await el.scrollIntoViewIfNeeded();
    const b = await el.boundingBox();
    check(`${tag}: ${id} is at least 40 px tall and inside the panel`, b.height >= 39.5 && b.x >= box.x - 0.5 && b.x + b.width <= box.x + box.width + 0.5, JSON.stringify(b));
  }
  await page.getByTestId('sponsor-spot-name').scrollIntoViewIfNeeded();
  await shot(page, `${tag.replace(/\W+/g, '-')}-sponsor-panel`);
  const skip = page.getByTestId('sponsor-today').getByTestId('sponsor-skip').first();
  await skip.scrollIntoViewIfNeeded();
  await skip.tap();
  await page.getByTestId('sponsor-restore').first().waitFor({ timeout: 4000 });
  check(`${tag}: Skip works by tap`, station.ad.later.status === 'cancelled');
  await page.getByTestId('sponsor-restore').first().tap();
  await page.getByTestId('sponsor-today').getByTestId('sponsor-skip').first().waitFor({ timeout: 4000 });
  check(`${tag}: Put back works by tap`, station.ad.later.status === 'pending');
  await page.getByRole('button', { name: 'Close' }).tap();
  check(`${tag}: Close closes`, await panel(page).count() === 0);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
const ONLY = process.env.ONLY || '';
const run = (name) => !ONLY || ONLY.split(',').includes(name);
try {
  if (run('desktop')) for (const s of STATIONS) await desktop(browser, s.id);
  if (run('air')) await onAir(browser);
  if (run('roles')) await roles(browser);
  if (run('edges')) await edges(browser);
  if (run('phone')) {
    await phone(browser, PHONE, 'iPhone 13');
    await phone(browser, PHONE_SE, 'iPhone SE');
    await phone(browser, PHONE_LAND, 'iPhone 13 sideways', true);
  }
} finally {
  await browser.close();
}
process.exit(done());
