/**
 * Browser check for the public listener pages other than the Listen player itself,
 * against the fake backend (e2e/lib/fake-station.mjs), desktop and iPhone size:
 *   - a11y:   accessibility basics on /stations, /listen/:id, /guide, /hosts, /archives,
 *             /call/:id: html lang="en", every button and link has a real accessible
 *             name (an icon or a symbol alone is not one), every form field has a label
 *   - player: the global AudioPlayer bar (playerStore)
 *   - stations, guide, hosts, archives: the public station list, program guide (fake
 *             clock for "Now" and midnight), DJs and archives (F-STLIST-3.., F-GUIDE-4..,
 *             F-HOSTS-2.., F-PARCH-4..)
 *
 *   cd frontend && npm run build && (npx vite preview --port 4311 --strictPort &)
 *   BASE_URL=http://localhost:4311 node e2e/listener-pages.mjs      # optional: ONLY=<part>
 *
 * Exits non-zero if any check fails. Known site bugs are listed in KNOWN_BUGS and
 * print XFAIL; when one is fixed its check prints XPASS and fails until removed here.
 */
import {
  chromium, devices, checker, sleep, makeStation, routeApi, routeNowPlaying, AUDIO_PROBE, routeSiteSocket,
} from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const ONLY = process.env.ONLY || '';

const KNOWN_BUGS = {};
const { check, done } = checker(KNOWN_BUGS);

/** A fake station that also answers the public pages' reads. */
function publicStation() {
  const station = makeStation({
    extra: (method, path) => {
      if (path === '/users/public/hosts') {
        return { hosts: [
          { id: 'h1', display_name: 'Reb Moshe', title: 'Morning host', bio: 'Shiurim and stories', photo_url: null, social_links: { twitter: 'kolbramah', instagram: 'kolbramah', website: 'https://example.com' } },
          { id: 'h2', display_name: 'Chaim', title: null, bio: null, photo_url: null, social_links: {} },
        ], total: 2 };
      }
      if (path === '/archives' && method === 'GET') {
        return { archives: [
          { id: 'a1', station_id: station.SID, title: 'Motzei Shabbos Live, week 12', description: 'Questions and answers', host_name: 'Reb Moshe', recorded_at: new Date(Date.now() - 86400e3).toISOString(), duration_seconds: 3600, audio_url: '/__tone/m1.wav', cover_image_url: null, is_published: true, download_count: 4, live_show_id: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
          { id: 'a2', station_id: station.SID, title: 'Thursday Night Live', description: null, host_name: null, recorded_at: null, duration_seconds: 1800, audio_url: '/__tone/m2.wav', cover_image_url: null, is_published: true, download_count: 0, live_show_id: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
        ], total: 2 };
      }
      if (/^\/schedules\/epg\//.test(path)) {
        return { station_id: station.SID, date: new Date().toISOString().slice(0, 10), blocks: [
          { id: 'b1', name: 'Morning shiurim', description: 'Daf yomi', start_time: '06:00', end_time: '09:00', playback_mode: 'sequential', schedule_name: 'Weekday' },
          { id: 'b2', name: 'Afternoon music', description: null, start_time: '12:00', end_time: '18:00', playback_mode: 'shuffle', schedule_name: 'Weekday' },
        ] };
      }
      return undefined;
    },
  });
  // A live show with call-ins open, so /call shows its form
  station.live.shows = [{ id: 'ls1', station_id: station.SID, title: 'Thursday Night Live', status: 'live' }];
  return station;
}

async function publicContext(browser, contextOpts, station) {
  const ctx = await browser.newContext(contextOpts);
  await ctx.addInitScript(AUDIO_PROBE);
  await routeApi(ctx, station);
  await routeNowPlaying(ctx, station);
  await routeSiteSocket(ctx);
  return ctx;
}

/**
 * What is wrong on the page as it stands, by the accessibility tree Chromium builds:
 *  - unnamed:  buttons/links/fields whose accessible name is empty or has no letter
 *              or digit (a "★", "✕" or an SVG alone tells a screen reader nothing)
 *  - unlabelled: inputs, selects and text areas with no <label>, aria-label or
 *              aria-labelledby (a placeholder is not a label: it disappears on typing)
 */
async function audit(page) {
  const cdp = await page.context().newCDPSession(page);
  const { nodes } = await cdp.send('Accessibility.getFullAXTree');
  await cdp.detach();
  const ROLES = new Set(['button', 'link', 'textbox', 'combobox', 'slider', 'checkbox', 'spinbutton', 'searchbox', 'radio', 'switch', 'menuitem', 'listbox']);
  const unnamed = nodes
    .filter((n) => !n.ignored && ROLES.has(n.role?.value))
    .filter((n) => !/[\p{L}\p{N}]/u.test(String(n.name?.value ?? '')))
    .map((n) => `${n.role.value} "${n.name?.value ?? ''}"`);
  const unlabelled = await page.evaluate(() => [...document.querySelectorAll('input, select, textarea')]
    .filter((el) => el.type !== 'hidden' && el.getClientRects().length > 0)
    .filter((el) => !(el.labels && el.labels.length) && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby'))
    .map((el) => `${el.tagName.toLowerCase()}${el.type ? `[${el.type}]` : ''} ${el.getAttribute('placeholder') || el.name || ''}`.trim()));
  return { unnamed, unlabelled };
}

async function a11y(browser, contextOpts, tag) {
  const station = publicStation();
  const ctx = await publicContext(browser, contextOpts, station);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const tap = (loc) => (contextOpts.isMobile ? loc.tap() : loc.click());
  const pages = [
    { path: '/stations', ready: () => page.getByText('Kol Berama').first().waitFor() },
    {
      path: `/listen/${station.SID}`,
      ready: async () => { await page.getByText('On Air', { exact: true }).waitFor(); },
      // Open everything a listener can open: the player controls, the song request
      // form, the registration form; then log in for the rating stars and favourite
      more: async () => {
        await tap(page.getByRole('button', { name: 'Listen Live' }));
        await page.getByRole('slider').waitFor({ timeout: 3000 }).catch(() => {});
        await tap(page.getByRole('button', { name: 'Request a Song' }));
        await tap(page.getByRole('button', { name: 'Register' }).first());
        const first = await audit(page);
        await page.getByRole('textbox', { name: /PIN/ }).or(page.getByPlaceholder('Enter 6-digit PIN')).fill('123456');
        await tap(page.getByRole('button', { name: 'Login' }));
        await page.getByText('Welcome, Test Listener!').waitFor({ timeout: 3000 }).catch(() => {});
        return first;
      },
    },
    { path: '/guide', ready: () => page.getByText('Morning shiurim').waitFor() },
    { path: '/hosts', ready: () => page.getByText('Reb Moshe').first().waitFor() },
    { path: '/archives', ready: () => page.getByText('Thursday Night Live').waitFor() },
    { path: `/call/${station.SID}`, ready: () => page.locator('input').first().waitFor() },
  ];
  for (const p of pages) {
    await page.goto(`${BASE}${p.path}`);
    await p.ready().catch(() => {});
    await sleep(300);
    const extra = p.more ? await p.more() : { unnamed: [], unlabelled: [] };
    if (contextOpts.isMobile) {
      // The navbar's menu button and what it opens
      const menu = page.locator('nav button[aria-expanded], nav button.md\\:hidden, nav button').filter({ hasNot: page.locator('text=/\\w{3,}/') }).first();
      if (await menu.count()) await menu.tap().catch(() => {});
      await sleep(200);
    }
    const r = await audit(page);
    const unnamed = [...new Set([...extra.unnamed, ...r.unnamed])];
    const unlabelled = [...new Set([...extra.unlabelled, ...r.unlabelled])];
    check(`${tag} ${p.path.replace(station.SID, ':id')}: every button and link has a name`, unnamed.length === 0, unnamed.join(', '));
    check(`${tag} ${p.path.replace(station.SID, ':id')}: every form field has a label`, unlabelled.length === 0, unlabelled.join(', '));
    const lang = await page.evaluate(() => document.documentElement.lang);
    check(`${tag} ${p.path.replace(station.SID, ':id')}: html lang="en"`, lang === 'en', lang);
  }
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Global AudioPlayer bar (playerStore) ─────────────
// The bar (components/audio/AudioPlayer.tsx, in Layout) shows only once something calls
// usePlayerStore().play(stationId, name, hlsUrl). Nothing in the site does: the public
// Archives page plays with its own Audio element, the Listen page and the admin
// library have their own players. So the check is that the bar is never shown on the
// pages a listener can open (and that playing an archive does not need it).
async function globalPlayer(browser) {
  const tag = 'global player bar';
  const station = publicStation();
  const ctx = await publicContext(browser, { viewport: { width: 1280, height: 900 } }, station);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const bar = () => page.locator('div.fixed.bottom-0.bg-gray-900').count();
  for (const path of ['/stations', `/listen/${station.SID}`, '/guide', '/hosts', '/archives']) {
    await page.goto(`${BASE}${path}`);
    await sleep(800);
    check(`${tag}: not shown on ${path.replace(station.SID, ':id')} (nothing calls playerStore.play)`, (await bar()) === 0);
  }
  await page.getByText('Thursday Night Live').waitFor();
  await page.getByRole('button', { name: /^Play Motzei Shabbos/ }).click();
  await sleep(800);
  const playing = await page.evaluate(() => window.__audios.filter((a) => !a.paused).map((a) => a.src.split('/').pop()));
  check(`${tag}: an archive plays on the Archives page's own player, no global bar`, playing.length === 1 && playing[0] === 'm1.wav' && (await bar()) === 0, playing.join());
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Station list, program guide, DJs, archives (F-STLIST, F-GUIDE, F-HOSTS, F-PARCH) ──

const DESKTOP = { viewport: { width: 1280, height: 900 } };
const IPHONE = { ...devices['iPhone 13'] };
const sideways = (page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
/** Client-side navigation, as a click on a navbar link does (no page reload). */
const goInApp = (page, path) => page.evaluate((p) => { history.pushState({}, '', p); dispatchEvent(new PopStateEvent('popstate')); }, path);
/** A station list the API answers page by page (skip/limit, 50 by default) like the server. */
function pagedStations(n, extraFields = () => ({})) {
  return Array.from({ length: n }, (_, i) => ({
    id: i === 0 ? 'st-1' : `st-${i + 1}`, name: i === 0 ? 'Kol Berama' : `Feed ${String(i + 1).padStart(3, '0')}`,
    timezone: i === 1 ? 'Asia/Jerusalem' : 'America/New_York', type: 'internet', is_active: true, description: null, channels: [], ...extraFields(i),
  }));
}
function pageOf(list, query, key) {
  const skip = +(query.skip || 0), limit = +(query.limit || 50);
  return { [key]: list.slice(skip, skip + limit), total: list.length };
}

/** A station whose public reads are set per check. handlers: { [path or RegExp source]: (method, query, path) => data } */
function stationFor(handlers, opts = {}) {
  const st = makeStation({
    ...opts,
    extra: (method, path, query) => {
      for (const [k, fn] of Object.entries(handlers)) {
        if (k === path || (k.startsWith('^') && new RegExp(k).test(path))) return fn(method, query, path);
      }
      return undefined;
    },
  });
  return st;
}

async function openAt(browser, contextOpts, station, path, { delay, down, clock, timezoneId } = {}) {
  const ctx = await browser.newContext({ ...contextOpts, ...(timezoneId ? { timezoneId } : {}) });
  await ctx.addInitScript(AUDIO_PROBE);
  if (clock) await ctx.clock.install({ time: new Date(clock) });
  await routeApi(ctx, station, { delay, down });
  await routeNowPlaying(ctx, station);
  await routeSiteSocket(ctx);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${BASE}${path}`);
  return { ctx, page, errors };
}

// ── Station list ──
async function stationListChecks(browser) {
  const tag = 'F-STLIST';
  {
    const st = stationFor({});
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/stations', { down: ['/stations'] });
    await page.getByText('Unable to connect to the server').waitFor({ timeout: 8000 }).catch(() => {});
    const text = await page.locator('body').innerText();
    check(`${tag}-3: server unreachable -> "Unable to connect to the server", no "backend API is not yet configured" line`,
      text.includes('Unable to connect to the server') && !text.includes('not yet configured'), text.slice(0, 200).replace(/\n/g, ' '));
    await ctx.close();
  }
  const list = pagedStations(120, (i) => (i === 2 ? { is_active: false, name: 'Feed 003 (old)' } : i === 0 ? { description: 'Torah, music and news. '.repeat(20) } : {}));
  // On air: Kol Berama (st-1) and the inactive st-3; Feed 002 is switched on with nothing playing
  const st = stationFor({ '/stations': (m, q) => pageOf(list, q, 'stations'), '/now-playing': () => ({ playing: ['st-1', 'st-3'] }) }, {
    playout: { stations: { 'st-2': { mode: 'server', engine: true, engine_ok: true, in_sync: true } }, default: { mode: 'browser' } },
  });
  for (const [view, opts] of [['desktop', DESKTOP], ['iPhone', IPHONE]]) {
    const { ctx, page, errors } = await openAt(browser, opts, st, '/stations');
    await page.getByText('Feed 120').waitFor({ timeout: 8000 }).catch(() => {});
    const cards = page.locator('a[href^="/listen/"]');
    if (view === 'desktop') {
      const off = cards.filter({ hasText: 'Feed 003 (old)' });
      check(`${tag}-4 ${view}: an inactive station is listed with an "Offline" pill and a working /listen link (today's behaviour; decide)`,
        await off.count() === 1 && (await off.innerText()).includes('Offline') && (await off.getAttribute('href')) === '/listen/st-3');
      const first = cards.filter({ hasText: 'Kol Berama' });
      const pill = async (name) => (await cards.filter({ hasText: name }).locator('span.rounded-full').last().innerText()).trim();
      const p1 = await pill('Kol Berama'), p2 = await pill('Feed 002'), p3 = await pill('Feed 003 (old)');
      const onAirReads = st.calls.filter((c) => c.path === '/now-playing').length;
      const perCard = st.calls.filter((c) => /^\/(now-playing|stations)\/[^/]+(\/now-playing)?$/.test(c.path)).length;
      check(`${tag}-5 ${view}: "On Air" only when something plays; a switched-on station with nothing playing says Offline; an inactive one stays Offline`,
        p1 === 'On Air' && p2 === 'Offline' && p3 === 'Offline', `${p1} / ${p2} / ${p3}`);
      check(`${tag}-5 ${view}: one request for the whole list, not one per card`, onAirReads === 1 && perCard === 0, `/now-playing x${onAirReads}, per-station reads ${perCard}`);
      const firstText = await first.innerText();
      const clamp = await first.locator('p.line-clamp-2').evaluate((el) => getComputedStyle(el).webkitLineClamp).catch(() => '');
      const feed2 = await cards.filter({ hasText: 'Feed 002' }).innerText();
      check(`${tag}-6 ${view}: card shows type · timezone, playout badge (browser / server), description clamped to 2 lines`,
        firstText.includes('internet · America/New_York') && firstText.includes('Browser player') && feed2.includes('Server stream') && clamp === '2',
        `clamp=${clamp}`);
      check(`${tag}-6 ${view}: more than 100 stations all load (pages through)`, await cards.count() === 120, `${await cards.count()} cards`);
    } else {
      const xs = await cards.evaluateAll((els) => [...new Set(els.slice(0, 6).map((e) => Math.round(e.getBoundingClientRect().left)))]);
      const h = await cards.first().evaluate((e) => e.getBoundingClientRect().height);
      check(`${tag}-7 ${view}: one column, cards tappable (>= 44 px), no sideways scroll`, xs.length === 1 && h >= 44 && (await sideways(page)) <= 0, `columns at x=${xs} h=${h}`);
      await cards.filter({ hasText: 'Feed 005' }).tap();
      await page.waitForURL(/\/listen\/st-5/, { timeout: 5000 }).catch(() => {});
      check(`${tag}-7 ${view}: tapping a card opens its Listen page`, page.url().endsWith('/listen/st-5'), page.url());
    }
    check(`${tag} ${view}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── Program guide ──
const NIGHT = { id: 'g-night', name: 'Night learning', description: 'Gemara b\'iyun', start_time: '22:00', end_time: '02:00', playback_mode: 'sequential', schedule_name: 'Daily' };
const GUIDE_DAY = [
  { id: 'g-morning', name: 'Morning shiurim', description: 'Daf yomi', start_time: '06:00', end_time: '09:00', playback_mode: 'sequential', schedule_name: 'Weekday' },
  { id: 'g-mid', name: 'Mid-morning music', description: null, start_time: '09:00', end_time: '12:00', playback_mode: 'shuffle', schedule_name: 'Weekday' },
  NIGHT,
];
const nowBlocks = (page) => page.evaluate(() => [...document.querySelectorAll('span')]
  .filter((s) => s.textContent.trim() === 'Now')
  .map((s) => { const card = s.closest('.rounded-lg'); return `${card?.querySelector('h3')?.textContent.trim()}${card?.textContent.includes('Started last night') ? ' (last night)' : ''}`; }));
const dateLabel = (page) => page.locator('span.uppercase.tracking-wider').first().innerText();
const dateValue = (page) => page.getByLabel('Date').inputValue();

async function guideChecks(browser) {
  const tag = 'F-GUIDE';
  const epgCalls = [];
  const epg = (blocks) => ({ '^/schedules/epg/': (m, q, path) => { epgCalls.push(q.date); return { station_id: path.split('/').pop(), date: q.date, blocks }; } });
  // F-GUIDE-4: 01:00 in New York (the station's zone) on 2026-11-17 = 06:00Z
  {
    const st = stationFor(epg(GUIDE_DAY));
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/guide', { clock: '2026-11-17T06:00:00Z' });
    await page.getByText('Morning shiurim').waitFor({ timeout: 8000 }).catch(() => {});
    await sleep(800);
    const now = await nowBlocks(page);
    check(`${tag}-4: overnight block (22:00-02:00) is "Now" in its early-morning part on today's view`,
      (await dateValue(page)) === '2026-11-17' && now.length === 1 && now[0].startsWith('Night learning'), JSON.stringify(now));
    await ctx.close();
  }
  // F-GUIDE-5: the Now marker moves on an open page (08:58 -> 09:01), and past midnight "today" moves on
  {
    const st = stationFor(epg(GUIDE_DAY));
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/guide', { clock: '2026-11-17T13:58:00Z' });
    await page.getByText('Morning shiurim').waitFor({ timeout: 8000 }).catch(() => {});
    await sleep(500);
    const before = await nowBlocks(page);
    await ctx.clock.fastForward('03:00');
    await sleep(800);
    const after = await nowBlocks(page);
    check(`${tag}-5: "Now" moves from one block to the next on an open page, no reload`,
      before.join() === 'Morning shiurim' && after.join() === 'Mid-morning music', `${before} -> ${after}`);
    await ctx.close();
  }
  {
    const st = stationFor(epg(GUIDE_DAY));
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/guide', { clock: '2026-11-18T04:59:20Z' }); // 23:59:20 New York
    await page.getByText('Morning shiurim').waitFor({ timeout: 8000 }).catch(() => {});
    await sleep(500);
    const d0 = await dateValue(page), n0 = await nowBlocks(page);
    await ctx.clock.fastForward('01:00');
    await sleep(1000);
    const d1 = await dateValue(page), n1 = await nowBlocks(page), l1 = await dateLabel(page);
    check(`${tag}-5: at midnight in the station's zone the open page moves to the new day, "Today", and last night's block stays "Now"`,
      d0 === '2026-11-17' && n0.join() === 'Night learning' && d1 === '2026-11-18' && /today/i.test(l1) && n1.join() === 'Night learning (last night)',
      `${d0} ${n0} -> ${d1} ${l1} ${n1}`);
    await ctx.close();
  }
  // F-GUIDE-6: states
  {
    const st = stationFor(epg(GUIDE_DAY));
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/guide', { delay: (p) => (p.startsWith('/schedules/epg/') ? 1500 : 0) });
    const spin = await page.getByText('Loading schedule...').waitFor({ timeout: 1400 }).then(() => true).catch(() => false);
    check(`${tag}-6: loading spinner while the EPG is fetched`, spin && await page.locator('.animate-spin').count() > 0);
    await ctx.close();
  }
  {
    const st = stationFor({});
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/guide', { down: ['/schedules/epg/'] });
    await page.getByText('Failed to load schedule').waitFor({ timeout: 10000 }).catch(() => {});
    check(`${tag}-6: EPG fails -> "Failed to load schedule"`, await page.getByText('Failed to load schedule').count() === 1);
    await ctx.close();
  }
  {
    const st = stationFor(epg([]));
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/guide');
    await page.getByText('No programs scheduled for this day').waitFor({ timeout: 8000 }).catch(() => {});
    check(`${tag}-6: no blocks -> "No programs scheduled for this day"`, await page.getByText('No programs scheduled for this day').count() === 1);
    await ctx.close();
  }
  {
    const st = stationFor({ '/stations': () => ({ stations: [], total: 0 }) });
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/guide');
    await page.getByText('Select a station', { exact: false }).waitFor({ timeout: 8000 }).catch(() => {});
    const opts = await page.getByLabel('Station').locator('option').allInnerTexts();
    check(`${tag}-6: no stations -> "No stations available" option and "Select a station" text`,
      opts.join() === 'No stations available' && await page.getByText('Select a station to view its program guide').count() === 1, opts.join());
    await ctx.close();
  }
  // F-GUIDE-7: block card
  {
    const st = stationFor(epg([
      { id: 'c1', name: 'Midnight to noon', description: 'Long words. '.repeat(60), start_time: '00:00', end_time: '12:00', playback_mode: 'weighted', schedule_name: 'Main' },
      { id: 'c2', name: 'Odd mode', description: null, start_time: '12:30', end_time: '13:05', playback_mode: 'mystery', schedule_name: null },
      { id: 'c3', name: 'No times', description: null, start_time: null, end_time: null, playback_mode: 'rotation', schedule_name: 'Main' },
    ]));
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/guide');
    await page.getByText('Midnight to noon').waitFor({ timeout: 8000 }).catch(() => {});
    const card = (name) => page.locator('.rounded-lg').filter({ has: page.getByRole('heading', { name }) }).last();
    const c1 = await card('Midnight to noon').innerText();
    const c2 = await card('Odd mode').innerText();
    const c3 = await card('No times').innerText();
    const greyBadge = await card('Odd mode').locator('span.border').filter({ hasText: /mystery/i }).getAttribute('class');
    const clamp = await card('Midnight to noon').locator('p.line-clamp-2').evaluate((el) => getComputedStyle(el).webkitLineClamp).catch(() => '');
    check(`${tag}-7: 12-hour times (12:00 AM - 12:00 PM, 12:30 PM - 1:05 PM), "Schedule: <name>", description clamped`,
      c1.includes('12:00 AM - 12:00 PM') && c2.includes('12:30 PM - 1:05 PM') && c1.includes('Schedule: Main') && !c2.includes('Schedule:') && clamp === '2', `${c1.slice(0, 40)} | ${c2.slice(0, 30)}`);
    check(`${tag}-7: playback-mode badge; an unknown mode is grey`, /weighted/i.test(c1) && /bg-gray-600/.test(greyBadge || ''), greyBadge);
    check(`${tag}-7: a missing time shows "??:??"`, c3.includes('??:?? - ??:??'), c3.slice(0, 30));
    await ctx.close();
  }
  // F-GUIDE-8: station change and the date (New York 20:00 = Jerusalem 03:00 the next day)
  {
    const st = stationFor(epg(GUIDE_DAY));
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/guide', { clock: '2026-11-18T01:00:00Z' });
    await page.getByText('Morning shiurim').waitFor({ timeout: 8000 }).catch(() => {});
    await sleep(300);
    const ny = await dateValue(page);
    await page.getByLabel('Station').selectOption('st-2');
    await sleep(500);
    const jlm = await dateValue(page);
    check(`${tag}-8: before a date is picked, changing station moves "today" to that station's zone`, ny === '2026-11-17' && jlm === '2026-11-18', `${ny} -> ${jlm}`);
    await page.getByLabel('Date').fill('2026-11-25');
    await sleep(300);
    await page.getByLabel('Station').selectOption('st-1');
    await sleep(500);
    const kept = await dateValue(page);
    check(`${tag}-8: a picked date is kept when the station changes`, kept === '2026-11-25' && epgCalls.at(-1) === '2026-11-25', `${kept}, last EPG date ${epgCalls.at(-1)}`);
    await ctx.close();
  }
  // F-GUIDE-9: another day's label for a viewer west of UTC
  {
    const st = stationFor(epg(GUIDE_DAY));
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/guide', { timezoneId: 'America/Los_Angeles', clock: '2026-03-08T20:00:00Z' });
    await page.getByText('Morning shiurim').waitFor({ timeout: 8000 }).catch(() => {});
    await page.getByLabel('Date').fill('2026-03-10');
    await sleep(500);
    const l = await dateLabel(page);
    check(`${tag}-9: date label for another day reads "Tuesday, March 10" for a viewer in Los Angeles`, /tuesday, march 10/i.test(l), l);
    await ctx.close();
  }
  // F-GUIDE-10: phone controls
  {
    const st = stationFor(epg(GUIDE_DAY));
    const { ctx, page } = await openAt(browser, IPHONE, st, '/guide');
    await page.getByText('Morning shiurim').waitFor({ timeout: 8000 }).catch(() => {});
    const dh = await page.getByLabel('Date').evaluate((e) => e.getBoundingClientRect().height);
    const sh = await page.getByLabel('Station').evaluate((e) => e.getBoundingClientRect().height);
    check(`${tag}-10 iPhone: the date input and station select are at least 44 px tall, no sideways scroll`, dh >= 44 && sh >= 44 && (await sideways(page)) <= 0, `date ${dh}px, select ${sh}px`);
    await ctx.close();
  }
  // F-GUIDE-11: every station in the dropdown
  {
    const list = pagedStations(120);
    const st = stationFor({ '/stations': (m, q) => pageOf(list, q, 'stations'), ...epg(GUIDE_DAY) });
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/guide');
    await page.getByText('Morning shiurim').waitFor({ timeout: 8000 }).catch(() => {});
    await sleep(500);
    const n = await page.getByLabel('Station').locator('option').count();
    check(`${tag}-11: the station dropdown lists every station (120, past the API's page of 50)`, n === 120, `${n} options`);
    await ctx.close();
  }
}

// ── DJs ──
async function hostsChecks(browser) {
  const tag = 'F-HOSTS';
  {
    const st = stationFor({ '/users/public/hosts': () => ({ hosts: [] }) });
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/hosts', { delay: (p) => (p === '/users/public/hosts' ? 1200 : 0) });
    const loading = await page.getByText('Loading hosts...').waitFor({ timeout: 1100 }).then(() => true).catch(() => false);
    await page.getByText('No public host profiles yet.').waitFor({ timeout: 5000 }).catch(() => {});
    check(`${tag}-2: loading spinner, then "No public host profiles yet" when none`, loading && await page.getByText('No public host profiles yet.').count() === 1);
    await ctx.close();
  }
  {
    const st = stationFor({});
    const { ctx, page } = await openAt(browser, DESKTOP, st, '/hosts', { down: ['/users/public/hosts'] });
    await page.getByText('Unable to load host profiles.').waitFor({ timeout: 10000 }).catch(() => {});
    check(`${tag}-2: "Unable to load host profiles" on error`, await page.getByText('Unable to load host profiles.').count() === 1);
    await ctx.close();
  }
  const hosts = [
    { id: 'h1', display_name: 'Reb Moshe', title: 'Morning host', bio: 'Shiurim', photo_url: null, social_links: { twitter: 'kolbramah', instagram: 'kbr.radio', website: 'example.org' } },
    { id: 'h2', display_name: 'Chaim', title: null, bio: null, photo_url: '/__missing/photo.jpg', social_links: { twitter: 'https://x.com/chaim', website: 'javascript:alert(1)' } },
    { id: 'h3', display_name: 'Dovid', title: null, bio: null, photo_url: '/__photo/dovid.png', social_links: {} },
  ];
  const st = stationFor({ '/users/public/hosts': () => ({ hosts }) });
  for (const [view, opts] of [['desktop', DESKTOP], ['iPhone', IPHONE]]) {
    const { ctx, page, errors } = await openAt(browser, opts, st, '/hosts');
    await page.route('**/__missing/**', (r) => r.fulfill({ status: 404, body: '' }));
    await page.route('**/__photo/**', (r) => r.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64') }));
    await page.reload();
    await page.getByText('Reb Moshe').waitFor({ timeout: 8000 }).catch(() => {});
    await sleep(800);
    const card = (name) => page.locator('div.rounded-xl').filter({ has: page.getByRole('heading', { name }) });
    if (view === 'desktop') {
      const links = await card('Reb Moshe').locator('a').evaluateAll((els) => els.map((a) => ({ href: a.getAttribute('href'), target: a.target, rel: a.rel })));
      const chaim = await card('Chaim').locator('a').evaluateAll((els) => els.map((a) => a.getAttribute('href')));
      check(`${tag}-3: a bare handle becomes twitter.com/<h> / instagram.com/<h>, a bare site gets https://, new tab with noopener`,
        links.map((l) => l.href).join() === 'https://twitter.com/kolbramah,https://instagram.com/kbr.radio,https://example.org'
          && links.every((l) => l.target === '_blank' && /noopener/.test(l.rel)), JSON.stringify(links));
      check(`${tag}-3: a full URL is kept; a "javascript:" value never becomes a live link`,
        chaim[0] === 'https://x.com/chaim' && chaim.every((h) => !/^\s*javascript:/i.test(h)), chaim.join());
      const ph1 = await card('Reb Moshe').locator('svg path[d^="M12 12c2.21"]').count();
      const img2 = await card('Chaim').locator('img').evaluateAll((els) => els.map((i) => i.complete && i.naturalWidth > 0));
      const ph2 = await card('Chaim').locator('svg path[d^="M12 12c2.21"]').count();
      const img3 = await card('Dovid').locator('img').evaluate((i) => i.naturalWidth).catch(() => 0);
      check(`${tag}-4: no photo -> placeholder silhouette; a working photo shows`, ph1 === 1 && img3 > 0, `placeholder=${ph1} img3=${img3}`);
      check(`${tag}-4: a broken photo URL shows the placeholder, not a broken image`, ph2 === 1 && img2.every(Boolean), `imgs=${JSON.stringify(img2)} placeholder=${ph2}`);
    } else {
      const sizes = await page.locator('a[title]').evaluateAll((els) => els.map((a) => { const r = a.getBoundingClientRect(); return Math.round(Math.min(r.width, r.height)); }));
      const xs = await page.locator('div.rounded-xl').evaluateAll((els) => [...new Set(els.map((e) => Math.round(e.getBoundingClientRect().left)))]);
      check(`${tag}-5 ${view}: social icons are at least 44 px tap targets`, sizes.length === 5 && sizes.every((s) => s >= 44), sizes.join());
      check(`${tag}-5 ${view}: one column, no sideways scroll on the full-bleed page`, xs.length === 1 && (await sideways(page)) <= 0, `x=${xs}`);
    }
    check(`${tag} ${view}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── Archives ──
function archiveRow(i, over = {}) {
  return {
    id: `a${i}`, station_id: 'st-1', title: `Show number ${i}`, description: null, host_name: null,
    recorded_at: new Date(Date.UTC(2026, 10, 16) - i * 86400e3).toISOString(), duration_seconds: 1800, audio_url: '/__tone/m1.wav',
    cover_image_url: null, is_published: true, download_count: 0, live_show_id: null, created_at: '2026-11-01T00:00:00Z', updated_at: '2026-11-01T00:00:00Z', ...over,
  };
}
const playingSrcs = (page) => page.evaluate(() => window.__audios.filter((a) => !a.paused).map((a) => a.src.split('/').pop()));

async function archivesChecks(browser) {
  const tag = 'F-PARCH';
  const few = [
    archiveRow(1, { title: 'Motzei Shabbos Live', host_name: 'Reb Moshe', duration_seconds: 3725, audio_url: '/__tone/m1.wav', description: 'Questions. '.repeat(50) }),
    archiveRow(2, { title: 'Thursday Night Live', duration_seconds: 125, audio_url: '/__tone/m2.wav', recorded_at: null }),
    archiveRow(3, { title: 'Zero length', duration_seconds: 0, audio_url: '/__tone/m3.wav', station_id: 'st-gone' }),
    archiveRow(4, { title: 'Broken file', duration_seconds: null, audio_url: '/__broken/x.mp3' }),
  ];
  const st = stationFor({ '/archives': (m, q) => pageOf(q.station_id ? few.filter((a) => a.station_id === q.station_id) : few, q, 'archives') });
  // F-PARCH-4, -5, -8, -9 on desktop
  {
    const { ctx, page, errors } = await openAt(browser, DESKTOP, st, '/archives');
    await page.route('**/__broken/**', (r) => r.fulfill({ status: 404, body: 'nope' }));
    await page.getByText('Thursday Night Live').waitFor({ timeout: 8000 }).catch(() => {});
    const card = (title) => page.locator('div.rounded-xl').filter({ has: page.getByRole('heading', { name: title }) });
    // -9 card
    const c1 = await card('Motzei Shabbos Live').innerText();
    const c2 = await card('Thursday Night Live').innerText();
    const zeroBadges = await card('Zero length').locator('div.relative > span').allInnerTexts();
    const c3 = await card('Zero length').innerText();
    const clamp = await card('Motzei Shabbos Live').locator('p.line-clamp-2').evaluate((el) => getComputedStyle(el).webkitLineClamp).catch(() => '');
    check(`${tag}-9: duration badge h:mm:ss / m:ss, host | station, recorded date, description clamped`,
      c1.includes('1:02:05') && c2.includes('2:05') && /Reb Moshe\s*\|\s*Kol Berama/.test(c1) && /\d{4}/.test(c1) && clamp === '2', c1.slice(0, 80).replace(/\n/g, ' / '));
    check(`${tag}-9: a 0 duration shows no stray "0"; a station not in the list shows "Unknown"`,
      zeroBadges.length === 0 && !/(^|\n)0(\n|$)/.test(c3) && c3.includes('Unknown'), `badges=${JSON.stringify(zeroBadges)} text=${c3.replace(/\n/g, ' / ')}`);
    // -8 RSS
    const rssNone = await page.getByRole('link', { name: /RSS Feed/ }).count();
    await page.getByLabel('Station').selectOption('st-1');
    await sleep(500);
    const rss = page.getByRole('link', { name: /RSS Feed/ });
    const href = await rss.getAttribute('href').catch(() => '');
    check(`${tag}-8: RSS Feed only with a station picked; opens /archives/station/<id>/rss in a new tab`,
      rssNone === 0 && /\/archives\/station\/st-1\/rss$/.test(href || '') && (await rss.getAttribute('target')) === '_blank', href);
    await page.getByLabel('Station').selectOption('');
    await page.getByText('Zero length').waitFor({ timeout: 5000 }).catch(() => {});
    // -5 play / pause / switch / end / error
    const btn = (title) => page.locator('button[aria-label$="' + title + '"]');
    await btn('Motzei Shabbos Live').click();
    await sleep(800);
    const p1 = await playingSrcs(page);
    await btn('Motzei Shabbos Live').click();
    await sleep(400);
    const p2 = await playingSrcs(page);
    const labelPaused = await btn('Motzei Shabbos Live').getAttribute('aria-label');
    await btn('Motzei Shabbos Live').click();
    await sleep(600);
    const p3 = await playingSrcs(page);
    check(`${tag}-5: the same card pauses and resumes (button says Play while paused)`,
      p1.join() === 'm1.wav' && p2.length === 0 && /^Play /.test(labelPaused) && p3.join() === 'm1.wav', `${p1} | ${p2} ${labelPaused} | ${p3}`);
    await btn('Thursday Night Live').click();
    await sleep(800);
    const p4 = await playingSrcs(page);
    check(`${tag}-5: another card stops the first`, p4.join() === 'm2.wav', p4.join());
    await page.evaluate(() => { const a = window.__audios.find((x) => !x.paused); a.currentTime = Math.max(0, a.duration - 0.3); });
    await sleep(1500);
    const labelEnd = await btn('Thursday Night Live').getAttribute('aria-label');
    check(`${tag}-5: the icon goes back to play at the end`, /^Play /.test(labelEnd) && (await playingSrcs(page)).length === 0, labelEnd);
    await btn('Broken file').click();
    await sleep(1500);
    const labelBroken = await btn('Broken file').getAttribute('aria-label');
    const errText = await card('Broken file').innerText();
    check(`${tag}-5: a file that will not play shows an error, not a pause icon over silence`,
      /^Play /.test(labelBroken) && /could not be played/i.test(errText), `${labelBroken} | ${errText.replace(/\n/g, ' / ')}`);
    // -4 leaving the page
    await btn('Motzei Shabbos Live').click();
    await sleep(800);
    const before = await playingSrcs(page);
    await goInApp(page, '/stations');
    await sleep(1000);
    const archiveStill = await page.evaluate(() => window.__audios.filter((a) => !a.paused && a.src.endsWith('/__tone/m1.wav')).length);
    check(`${tag}-4: leaving the Archives page (in-app link) stops the archive audio`,
      before.join() === 'm1.wav' && archiveStill === 0 && page.url().endsWith('/stations'), `before=${before} still=${archiveStill}`);
    check(`${tag} desktop: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
  // F-PARCH-6: icon visible on phones, hover-only on desktop
  for (const [view, opts] of [['iPhone', IPHONE], ['desktop', DESKTOP]]) {
    const { ctx, page } = await openAt(browser, opts, st, '/archives');
    await page.getByText('Thursday Night Live').waitFor({ timeout: 8000 }).catch(() => {});
    await page.mouse.move(1, 1).catch(() => {});
    await sleep(600);
    const ops = await page.locator('button[aria-label^="Play "] > span').evaluateAll((els) => els.map((e) => +getComputedStyle(e).opacity));
    if (view === 'iPhone') check(`${tag}-6 ${view}: the play icon is visible on every card without hover`, ops.length === 4 && ops.every((o) => o > 0.9), ops.join());
    else check(`${tag}-6 ${view}: with a mouse the icon still appears on hover only`, ops.length === 4 && ops.every((o) => o < 0.1), ops.join());
    await ctx.close();
  }
  // F-PARCH-7: more than 50
  {
    const many = Array.from({ length: 120 }, (_, i) => archiveRow(i + 1));
    const st2 = stationFor({ '/archives': (m, q) => pageOf(many, q, 'archives') });
    const { ctx, page } = await openAt(browser, DESKTOP, st2, '/archives');
    await page.getByText('Show number 1', { exact: true }).waitFor({ timeout: 8000 }).catch(() => {});
    const count = () => page.locator('div.rounded-xl h3').count();
    const n0 = await count();
    const more = page.getByRole('button', { name: 'Load more' });
    const hasMore = await more.count();
    if (hasMore) { await more.click(); await page.getByText('Show number 100', { exact: true }).waitFor({ timeout: 5000 }).catch(() => {}); }
    const n1 = await count();
    if (await more.count()) { await more.click(); await page.getByText('Show number 120', { exact: true }).waitFor({ timeout: 5000 }).catch(() => {}); }
    const n2 = await count();
    check(`${tag}-7: more than 50 archives: "Load more" reaches the older ones (50 -> 100 -> 120, then no button)`,
      n0 === 50 && hasMore === 1 && n1 === 100 && n2 === 120 && await more.count() === 0 && await page.getByText('Show number 120', { exact: true }).count() === 1, `${n0} -> ${n1} -> ${n2}`);
    await ctx.close();
  }
  // F-PARCH-10: states
  {
    const st3 = stationFor({ '/archives': () => ({ archives: [], total: 0 }) });
    const { ctx, page } = await openAt(browser, DESKTOP, st3, '/archives', { delay: (p) => (p === '/archives' ? 1200 : 0) });
    const loading = await page.getByText('Loading archives...').waitFor({ timeout: 1100 }).then(() => true).catch(() => false);
    await page.getByText('No archived shows yet').waitFor({ timeout: 5000 }).catch(() => {});
    check(`${tag}-10: "Loading archives..." then "No archived shows yet"`, loading && await page.getByText('No archived shows yet').count() === 1);
    await ctx.close();
  }
  {
    const st4 = stationFor({});
    const { ctx, page } = await openAt(browser, DESKTOP, st4, '/archives', { down: ['/archives'] });
    await page.getByText('Unable to load archives.').waitFor({ timeout: 10000 }).catch(() => {});
    check(`${tag}-10: "Unable to load archives" when the server fails`, await page.getByText('Unable to load archives.').count() === 1);
    await ctx.close();
  }
}

const browser = await chromium.launch();
const run = (name) => !ONLY || ONLY.split(',').includes(name);
try {
  if (run('a11y')) {
    await a11y(browser, { viewport: { width: 1280, height: 900 } }, 'a11y desktop');
    await a11y(browser, { ...devices['iPhone 13'] }, 'a11y iPhone');
  }
  if (run('player')) await globalPlayer(browser);
  if (run('stations')) await stationListChecks(browser);
  if (run('guide')) await guideChecks(browser);
  if (run('hosts')) await hostsChecks(browser);
  if (run('archives')) await archivesChecks(browser);
} finally {
  await browser.close();
}
process.exit(done());
