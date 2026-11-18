/**
 * Browser check of the display findings from the fresh-eyes pass over every staff page
 * (2026-11-18), against a fake backend (no real station is touched). One check or more
 * per finding, at desktop 1366x900 and iPhone 13 (390x844):
 *   1 Program Clock: no placeholder sponsor title as the default
 *   2 Sponsors: no "max ?/hr · every ? songs"
 *   3 Analytics: items with no category told apart; do_not_play says what it is
 *   4 Databank on the phone: no two dots in a row
 *   5 Blackouts, 9 Archives / Users / Rules / Stations on the phone: rows are cards, the
 *     action buttons are in sight, nothing wraps one word per line
 *   6 Review Queues on the phone: the heading is on one line
 *   7 Page headings: one style (as wide as the words)
 *   8 Recording Studio: Weather Readouts opens on the station last used
 *  10 Analytics on the phone: Top Assets titles readable in full
 *  11 The bug button: the last controls of a page scroll clear of it
 *
 *   cd frontend && npm run build && (npx vite preview --port 4333 --strictPort &)
 *   BASE_URL=http://localhost:4333 node e2e/display-findings.mjs      # SHOTS=<dir> for pictures
 */
import { chromium, devices, checker, sleep, makeStation, adminContext, newPage } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4333';
const SHOTS = process.env.SHOTS || '';
const SIZES = [
  ['desktop 1366x900', { viewport: { width: 1366, height: 900 } }, false],
  ['iPhone 13', { ...devices['iPhone 13'] }, true],
];
const { check, done } = checker({});
const NOW = Date.now();
const iso = (h) => new Date(NOW + h * 3600e3).toISOString();

const DATA = {
  '/sponsors': [
    { id: 'sp1', name: 'No limits sponsor', length_seconds: 30, priority: 1, audio_file_path: '', target_rules: null, insertion_policy: 'between_tracks', created_at: '', updated_at: '' },
    { id: 'sp2', name: 'Limited sponsor', length_seconds: 30, priority: 1, audio_file_path: '', target_rules: { hour_start: 6, hour_end: 22, max_per_hour: 2, songs_between: 5 }, insertion_policy: 'every_n_songs', created_at: '', updated_at: '' },
  ],
  '/analytics/summary': { period_days: 7, total_plays: 100, unique_assets: 20, total_airtime_hours: 5, avg_plays_per_day: 14, plays_by_source: { auto: 100 } },
  '/analytics/play-counts': { data: [{ date: '2026-11-17', plays: 50 }] },
  '/analytics/top-assets': { assets: [
    { id: 't1', title: 'Mainx music number one with a long name', artist: 'A', asset_type: 'music', category: 'lively', play_count: 9 },
    { id: 't2', title: 'Mainx music number two with a long name', artist: 'A', asset_type: 'music', category: 'lively', play_count: 8 },
  ] },
  '/analytics/category-breakdown': { categories: [
    { category: 'uncategorized', asset_type: 'music', play_count: 30 },
    { category: 'uncategorized', asset_type: 'stories', play_count: 20 },
    { category: 'do_not_play', asset_type: 'music', play_count: 2 },
    { category: 'lively', asset_type: 'music', play_count: 40 },
  ] },
  '/analytics/hourly-distribution': { hours: [] },
  '/holidays': { total: 2, holidays: [
    { id: 'h1', name: 'Shabbos Parshas Vayeitzei', start_datetime: iso(48), end_datetime: iso(73), is_blackout: true, affected_stations: null, replacement_content: null, reason: 'Shabbos', created_at: '', updated_at: '' },
    { id: 'h2', name: 'Maintenance', start_datetime: iso(-30), end_datetime: iso(-28), is_blackout: true, affected_stations: null, replacement_content: null, reason: null, created_at: '', updated_at: '' },
  ] },
  '/archives': { total: 1, archives: [
    { id: 'ar1', station_id: 'st-2', title: 'Thursday night show with a long title', description: null, host_name: 'Reb Moshe', recorded_at: iso(-50), duration_seconds: 3600, audio_url: 'https://example.com/a.mp3', cover_image_url: null, is_published: true, download_count: 3, live_show_id: null, created_at: iso(-49), updated_at: iso(-49) },
  ] },
  '/users': { total: 2, users: [
    { id: 'u1', email: 'operator@example.com', role: 'admin', is_active: true, display_name: 'Operator', phone_number: null, title: null, alert_preferences: null, bio: null, photo_url: null, is_public: false, social_links: null, last_seen_at: iso(-1), last_action: 'Skipped a song', station_ids: null },
    { id: 'u2', email: 'a.very.long.address.for.a.dj@example.com', role: 'dj', is_active: true, display_name: 'Evening DJ', phone_number: '555 0100', title: 'Host', alert_preferences: null, bio: null, photo_url: null, is_public: true, social_links: null, last_seen_at: null, last_action: null, station_ids: ['st-2'] },
  ] },
  '/rules': { total: 1, rules: [
    { id: 'r1', name: 'Stories every half hour in the afternoon', description: null, rule_type: 'interval', asset_type: 'stories', category: 'kids', hour_start: 13, hour_end: 18, days_of_week: '0,1,2,3,4', interval_minutes: 30, songs_between: null, priority: 5, is_active: true, constraints: null, station_id: null },
  ] },
  '/rules/preview': { date: '2026-11-18', slots: [], total_hours: 0 },
  '/reviews/queues': { total: 1, queues: [{ id: 'q1', name: 'New uploads', description: null, status: 'open', created_by: 'u1', total_items: 4, reviewed_items: 1, created_at: iso(-5), updated_at: iso(-5) }] },
  '/databank/items': { total: 3, items: [
    { id: 'd1', artist: 'jerux artist 1', title: 'Song without a category', type: null, asset_type: 'music', vote: 0, length: 200, intro: null, file: 'a.mp3', last_played: iso(-2) },
    { id: 'd2', artist: null, title: 'Song without artist or category', type: null, asset_type: 'music', vote: 0, length: 200, intro: null, file: 'b.mp3', last_played: iso(-3) },
    { id: 'd3', artist: 'Artist 3', title: 'Song with both', type: 'lively', asset_type: 'music', vote: 0, length: 200, intro: null, file: 'c.mp3', last_played: null },
  ] },
  '/databank/types': { types: ['lively'] },
  '/weather-readouts': { readouts: [], total: 0 },
};

async function wire(ctx, calls) {
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  for (const [path, body] of Object.entries(DATA)) {
    const re = new RegExp(`/api/v1${path.replace(/[/-]/g, '\\$&')}(\\?.*)?$`);
    await ctx.route(re, (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      calls.push(route.request().url());
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });
  }
}

const overflow = (page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
const bodyText = (page) => page.evaluate(() => document.body.innerText);
const shot = async (page, name, size) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}-${size.replace(/\W+/g, '-')}.png`, fullPage: true }); };

/** The page's h1: is it only as wide as its words? */
const heading = (page) => page.evaluate(() => {
  const h = document.querySelector('h1');
  if (!h) return null;
  const r = h.getBoundingClientRect();
  const range = document.createRange(); range.selectNodeContents(h);
  const words = range.getBoundingClientRect().width;
  return { text: h.textContent.trim(), width: r.width, words, lines: Math.round(r.height / parseFloat(getComputedStyle(h).lineHeight || '17')) , height: r.height };
});

/** Phone tables: each row is a card; nothing is off to the side; no cell wraps one word per line. */
const cards = (page, rowText) => page.evaluate((t) => {
  const row = [...document.querySelectorAll('table.phone-cards > tbody > tr')].find((r) => r.textContent.includes(t));
  if (!row) return null;
  const vw = window.innerWidth;
  const cells = [...row.children].filter((c) => c.getBoundingClientRect().height > 0);
  const off = [...row.querySelectorAll('button, a, select, input, span')].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && (r.right > vw + 1 || r.left < -1); }).map((e) => (e.textContent || e.title || e.tagName).trim().slice(0, 30));
  const buttons = [...row.querySelectorAll('button, a')].filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.right <= vw + 1 && r.left >= 0; }).length;
  const narrow = cells.filter((c) => c.getBoundingClientRect().width < vw * 0.6).length;
  const stacked = cells.every((c, i) => i === 0 || c.getBoundingClientRect().top >= cells[i - 1].getBoundingClientRect().bottom - 1);
  return { cells: cells.length, off, buttons, narrow, stacked, height: row.getBoundingClientRect().height, headShown: !!row.closest('table').querySelector('thead')?.getBoundingClientRect().height };
}, rowText);

const browser = await chromium.launch();
try {
  for (const [size, opts, phone] of SIZES) {
    const station = makeStation();
    const calls = [];
    const { ctx } = await adminContext(browser, opts, station, { storage: { radio_last_station: 'st-2' } });
    await wire(ctx, calls);
    const { page, errors } = await newPage(ctx);
    const open = async (path, ready) => { await page.goto(`${BASE}${path}`); await page.getByText(ready).first().waitFor({ timeout: 15000 }); await sleep(250); };

    // 1 Program Clock
    await open('/admin/program-clock', 'Ad Asset Title');
    const adTitle = await page.locator('label:has-text("Ad Asset Title") + input').inputValue();
    check(`${size}: 1 Program Clock "Ad Asset Title" has no placeholder sponsor as its default`, adTitle === '', `value "${adTitle}"`);
    check(`${size}: 1 no old spelling of the station on Program Clock`, !/Kol Bramah|Kol Berama/i.test(await bodyText(page)));

    // 2 Sponsors
    await open('/admin/sponsors', 'No limits sponsor');
    let text = await bodyText(page);
    check(`${size}: 2 Sponsors shows no "?" for a sponsor without limits`, !/max \?|every \? songs/.test(text) && /no hourly limit/.test(text));
    check(`${size}: 2 a sponsor with limits still shows them`, /max 2\/hr/.test(text) && /every 5 songs/.test(text));
    await shot(page, 'sponsors', size);

    // 3 + 10 Analytics
    await open('/admin/analytics', 'By Category');
    text = await bodyText(page);
    check(`${size}: 3 Analytics tells the two "no category" rows apart`, /No category \(music\)/.test(text) && /No category \(stories\)/.test(text) && !/^uncategorized$/m.test(text));
    check(`${size}: 3 do_not_play says what it is`, /do_not_play \(blocked, played by hand\)/.test(text));
    const titles = await page.evaluate(() => [...document.querySelectorAll('div[title^="Mainx music"]')].map((e) => ({ cut: e.scrollWidth > e.clientWidth + 1, w: e.getBoundingClientRect().width })));
    if (phone) check(`${size}: 10 Top Assets titles are readable in full`, titles.length === 2 && titles.every((t) => !t.cut), JSON.stringify(titles));
    check(`${size}: Analytics does not scroll sideways`, (await overflow(page)) <= 1);
    await shot(page, 'analytics', size);

    // 4 Databank on the phone
    if (phone) {
      await open('/admin/databank', 'Song without a category');
      text = await bodyText(page);
      check(`${size}: 4 Databank rows have no two dots in a row and none at the start`, !/·\s*·/.test(text) && !/^\s*·/m.test(text), (text.match(/.*·\s*·.*/) || [''])[0].slice(0, 80));
      check(`${size}: 4 a row with artist, category and time still shows all three`, /Artist 3 · lively/.test(text) && /jerux artist 1 · \d\d\/\d\d\/\d{4}/.test(text));
      await shot(page, 'databank', size);
    }

    // 5 + 9 tables
    const TABLES = [
      ['/admin/holidays', 'Shabbos Parshas Vayeitzei', 'Blackouts', 2],
      ['/admin/archives', 'Thursday night show', 'Show Archives', 2],
      ['/admin/users', 'a.very.long.address', 'Users', 1],
      ['/admin/rules', 'Stories every half hour', 'Rules', 1],
      ['/admin/stations', 'Second Feed', 'Manage Stations', 1],
    ];
    for (const [path, rowText, label, minButtons] of TABLES) {
      await open(path, rowText);
      check(`${size}: ${label} does not scroll sideways`, (await overflow(page)) <= 1, `${await overflow(page)} px`);
      if (phone) {
        const c = await cards(page, rowText);
        check(`${size}: 5/9 ${label}: each row is a card with its lines one under the other`, !!c && c.stacked && !c.headShown && c.narrow === 0, JSON.stringify(c));
        check(`${size}: 5/9 ${label}: nothing in the row is off to the side and the action buttons are in sight`, !!c && c.off.length === 0 && c.buttons >= minButtons, JSON.stringify(c));
        const box = await page.evaluate(() => { const t = document.querySelector('table.phone-cards'); const p = t.parentElement; return p.scrollWidth - p.clientWidth; });
        check(`${size}: 5/9 ${label}: the table itself does not scroll sideways`, box <= 1, `${box} px`);
      } else {
        const head = await page.evaluate(() => !!document.querySelector('table.phone-cards > thead')?.getBoundingClientRect().height);
        check(`${size}: ${label} is still an ordinary table with its header`, head);
      }
      await shot(page, label.replace(/\W+/g, '-').toLowerCase(), size);
    }

    // 6 Review Queues heading, 7 one heading style
    const HEADS = ['/admin/reviews', '/admin/assets/upload', '/admin/categories', '/admin/sponsors', '/admin/users', '/admin/holidays'];
    const seen = [];
    for (const path of HEADS) {
      await page.goto(`${BASE}${path}`);
      const there = await page.locator('h1').first().waitFor({ timeout: 8000 }).then(() => true, () => false);
      if (!there) { check(`${size}: 7 ${path} shows its heading`, false, (await bodyText(page)).slice(0, 120)); continue; }
      await sleep(200);
      const h = await heading(page);
      seen.push({ path, ...h });
      check(`${size}: 7 the heading of ${path} is as wide as its words, not a bar`, !!h && h.width <= h.words + 20, JSON.stringify(h));
      if (path === '/admin/reviews') {
        check(`${size}: 6 "Review Queues" is on one line`, !!h && h.height < 30, JSON.stringify(h));
        await shot(page, 'review-queues', size);
      }
    }
    const heights = new Set(seen.map((h) => Math.round(h.height)));
    check(`${size}: 7 all headings are the same height`, heights.size === 1, [...heights].join(', '));

    // 8 Recording Studio: Weather Readouts opens on the station last used
    await page.goto(`${BASE}/admin/studio`);
    await page.getByRole('button', { name: /Weather Readouts/ }).first().click().catch(() => {});
    const sel = page.locator('select', { has: page.locator('option', { hasText: 'Select station...' }) }).first();
    await sel.waitFor({ timeout: 15000 });
    await sleep(300);
    check(`${size}: 8 Weather Readouts opens on the station last used`, (await sel.inputValue()) === 'st-2', `value "${await sel.inputValue()}"`);
    check(`${size}: 8 and reads that station's readouts`, calls.some((u) => u.includes('/weather-readouts') && u.includes('station_id=st-2')));
    await sel.selectOption({ index: 1 });
    const picked = await sel.inputValue();
    check(`${size}: 8 choosing another station is remembered for the other pages`, (await page.evaluate(() => localStorage.getItem('radio_last_station'))) === picked);

    // 11 the bug button does not cover the last controls of a page
    if (phone) {
      for (const path of ['/admin/site-updates', '/admin/users', '/admin/sponsors']) {
        await page.goto(`${BASE}${path}`);
        await page.locator('h1').first().waitFor({ timeout: 15000 });
        await sleep(400);
        await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
        await sleep(200);
        const under = await page.evaluate(() => {
          const bug = document.querySelector('button[aria-label="Report a bug"]');
          if (!bug || !bug.getBoundingClientRect().width) return { bug: false };
          const b = bug.getBoundingClientRect();
          const hit = [...document.querySelectorAll('main button, main a, main input, main select, main label, main textarea')].filter((e) => {
            const r = e.getBoundingClientRect();
            return r.width > 0 && r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top;
          }).map((e) => (e.textContent || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30));
          return { bug: true, hit };
        });
        check(`${size}: 11 scrolled to the end of ${path}, the bug button covers no control`, under.bug && under.hit.length === 0, JSON.stringify(under));
      }
    }
    check(`${size}: no page errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
} finally {
  await browser.close();
}
process.exit(done());
