/**
 * The control room's queue and library columns, and "Why it is in the queue"
 * (Shmuel 2026-11-19, looking at Main's queue: drag and double-click column borders
 * like Excel, tighter columns, "Type" not lined up under its heading, and "what
 * playlist is it playing from? I want to click into the playlist and open up the
 * reason why the song is playing").
 *
 * Fake backend; three stations; desktop (with a 17 px scrollbar like Windows), a car
 * screen, iPhone 13 and iPhone SE; manager and viewer.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4331 --strictPort &)
 *   BASE_URL=http://localhost:4331 node e2e/queue-columns.mjs     # optional: SHOTS=<dir>
 *
 * Exits non-zero if a check fails.
 */
import fs from 'node:fs';
import { chromium, devices, checker, sleep, makeStation, adminContext, newPage } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4331';
const SHOTS = process.env.SHOTS || '';
const { check, done } = checker({});

const STATIONS = [
  { id: 'st-main', name: "Kol B'ramah Main", timezone: 'America/New_York', is_active: true, channels: [] },
  { id: 'st-stories', name: "Kol B'ramah - All Day Stories", timezone: 'America/New_York', is_active: true, channels: [] },
  { id: 'st-jlm', name: 'KBR Jerusalem', timezone: 'Asia/Jerusalem', is_active: true, channels: [] },
];
const PLAYLIST = { 'st-main': 'Top 500 Hits', 'st-jlm': 'Jerusalem Day', 'st-stories': null };

const SIZES = [
  { tag: 'desktop 1440x900', opts: { viewport: { width: 1440, height: 900 } }, desk: true, bar: 17 },
  { tag: 'desktop 1920x1080', opts: { viewport: { width: 1920, height: 1080 } }, desk: true, bar: 0 },
  { tag: 'car 1200x900', opts: { viewport: { width: 1200, height: 900 }, hasTouch: true }, desk: true, bar: 0 },
  { tag: 'iPhone 13', opts: { ...devices['iPhone 13'] } },
  { tag: 'iPhone SE', opts: { ...devices['iPhone SE'] } },
];

function station(sid) {
  const s = makeStation({ sid, stations: STATIONS, queueSize: 0 });
  for (const a of s.assets) if (a.category === 'lively') a.category = 'top_hits'; else if (a.category === 'slow') a.category = 'hot_now';
  s.assets.push({ id: 'chan', title: 'Mi Yimalel (Chanukah)', artist: 'Avraham Fried', album: null, duration: 200, asset_type: 'music', category: 'holiday' });
  s.assets.push({ id: 'sid1', title: "You're listening to Kol B'ramah", artist: null, album: null, duration: 8, asset_type: 'jingle', category: 'station_id' });
  s.assets.push({ id: 'spx', title: 'TEST SPONSOR 14', artist: null, album: null, duration: 30, asset_type: 'spot', category: 'test', sponsor_id: 'sp14' });
  for (const a of s.assets.slice(-3)) s.byId[a.id] = a;
  const pl = PLAYLIST[sid];
  let pos = 0;
  const add = (asset_id, extra = {}) => s.add({ asset_id, position: ++pos, source: 'auto', ...extra });
  const blk = pl ? 'b-day' : undefined;
  const on = add('m1', { block_id: blk }); on.status = 'playing'; on.started_at = new Date(Date.now() - 30_000).toISOString();
  add('m2', { block_id: blk });                           // top_hits: step 1
  add('m3', { block_id: blk });                           // hot_now: step 2
  add('m4', { source: 'manual' });                        // added by hand
  const at = new Date(Date.now() + 12 * 60_000).toISOString();
  add('tc', { preempt_at: at, hard_timed: true });        // time check
  add('spx', { preempt_at: at, hard_timed: false, source: 'ad_slot' });
  add('sid1', { block_id: undefined });                   // station ID
  add('m5', { source: 'request' });
  add('chan', { block_id: blk, holiday: 'Chanukah' });
  add('m6', { block_id: pl ? 'b-gone' : undefined });     // block deleted since
  for (let i = 7; i <= 50; i++) add(`m${i}`, { block_id: blk });
  s.schedules = pl ? [{ id: 'sch1', station_id: sid, name: 'Weekday', is_active: true, priority: 1, blocks: [
    { id: 'b-day', schedule_id: 'sch1', name: 'Daytime music', start_time: '06:00:00', end_time: '22:00:00', recurrence_type: 'daily', priority: 1, playback_mode: 'shuffle', playlist_template_id: 'tpl-' + sid },
  ] }] : [];
  s.templates = [
    { id: 'tpl-st-main', name: 'Top 500 Hits', description: null, station_id: 'st-main', is_active: true, slots: [
      { id: 'slot-1', template_id: 'tpl-st-main', position: 0, asset_type: 'music', category: 'top_hits' },
      { id: 'slot-2', template_id: 'tpl-st-main', position: 1, asset_type: 'music', category: 'hot_now' },
      { id: 'slot-3', template_id: 'tpl-st-main', position: 2, asset_type: 'music', category: 'top_hits' },
    ] },
    { id: 'tpl-st-jlm', name: 'Jerusalem Day', description: null, station_id: 'st-jlm', is_active: true, slots: [
      { id: 'slot-j1', template_id: 'tpl-st-jlm', position: 0, asset_type: 'music', category: 'hot_now' },
      { id: 'slot-j2', template_id: 'tpl-st-jlm', position: 1, asset_type: 'music', category: 'top_hits' },
    ] },
  ];
  return s;
}

async function open(browser, size, st, { role = 'admin', bar = 0, storage = {} } = {}) {
  const { ctx } = await adminContext(browser, size.opts, st, { storage: { radio_last_station: st.SID, ...storage } });
  await ctx.routeWebSocket(/\/ws\//, () => {});
  // Registered after the fake backend, so these answer first
  await ctx.route(/\/api\/v1\/(schedules|playlists)(\?|$|\/)/, async (route) => {
    const u = new URL(route.request().url());
    const p = u.pathname.replace(/.*\/api\/v1/, '');
    if (p === '/schedules' || p === '/schedules/') return route.fulfill({ json: st.schedules });
    if (p === '/playlists') return route.fulfill({ json: st.templates });
    if (p === '/playlists/generation') return route.fulfill({ json: { station_id: u.searchParams.get('station_id'), enabled: true, hours: 48, artist_distance: 3, min_vote: 0 } });
    if (p === '/playlists/asset-types') return route.fulfill({ json: [] });
    return route.fallback();
  });
  await ctx.route(/\/api\/v1\/auth\/me$/, (route) => route.fulfill({ json: { id: 'u1', email: 'operator@example.com', role, is_active: true } }));
  if (bar) {
    // A Windows-like scrollbar on the queue: the headings must still line up with the rows
    await ctx.addInitScript((w) => {
      document.addEventListener('DOMContentLoaded', () => {
        const el = document.createElement('style');
        el.textContent = `[data-testid=queue-scroll]::-webkit-scrollbar{width:${w}px;background:#444}[data-testid=queue-scroll]::-webkit-scrollbar-thumb{background:#999}`;
        document.head.appendChild(el);
      });
    }, bar);
  }
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  await sleep(700);
  return { ctx, page, errors };
}

const shot = async (page, name) => {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.png` });
};

// Left edge and width of a column's heading and of its cells in the first rows
const geometry = (page, id, scope = 'queue') => page.evaluate(([id, scope]) => {
  const head = document.querySelector(`[data-testid=${scope}-head] [data-testid=col-${id}]`);
  const rows = document.querySelector(scope === 'queue' ? '[data-testid=queue-scroll]' : '[data-testid=library-scroll]');
  const cells = [...(rows?.querySelectorAll(`[data-col="${id}"]`) ?? [])].slice(0, 8);
  const h = head?.getBoundingClientRect();
  return {
    head: h ? { x: Math.round(h.left), w: Math.round(h.width) } : null,
    cells: cells.map((c) => { const b = c.getBoundingClientRect(); return { x: Math.round(b.left), w: Math.round(b.width) }; }),
  };
}, [id, scope]);

const lined = (g) => !!g.head && g.cells.length > 0 && g.cells.every((c) => Math.abs(c.x - g.head.x) <= 1 && Math.abs(c.w - g.head.w) <= 1);
const fromTexts = (page) => page.evaluate(() => [...document.querySelectorAll('[data-testid=queue-row]')].slice(0, 12).map((r) => ({
  title: r.querySelector('[data-testid=row-title]')?.textContent ?? '',
  from: r.querySelector('[data-testid=row-from]')?.textContent ?? null,
})));

async function borderDrag(page, id, dx, scope = 'queue') {
  const h = await page.locator(`[data-testid=${scope}-head] [data-testid=col-resize-${id}]`).boundingBox();
  const x = h.x + h.width / 2, y = h.y + h.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  for (let i = 1; i <= 6; i++) await page.mouse.move(x + (dx * i) / 6, y);
  await page.mouse.up();
  await sleep(150);
}

async function desktop(browser, size, sid) {
  const name = STATIONS.find((s) => s.id === sid).name;
  const tag = `${size.tag} · ${name}`;
  const st = station(sid);
  const { ctx, page } = await open(browser, size, st, { bar: size.bar });
  const main = sid === 'st-main';
  if (main) await shot(page, `${size.tag}-1-queue`);

  // Headings line up with the rows (also with a 17 px scrollbar)
  for (const id of ['time', 'len', 'title', 'type', 'from', 'last']) {
    const g = await geometry(page, id);
    check(`${tag}: "${id}" heading sits exactly over its column`, lined(g), JSON.stringify({ head: g.head, cell: g.cells[0] }));
  }
  const heads = await page.locator('[data-testid=queue-head] [role=columnheader]').allTextContents();
  check(`${tag}: headings read Time, Length, Title, Song type, Playing from, Last played`, heads.join('|') === 'Time|Length|Title|Song type|Playing from|Last played', heads.join('|'));

  // Tighter: Song type starts within ~600 px of the title's start, not at the far right
  const gT = await geometry(page, 'title'); const gY = await geometry(page, 'type');
  check(`${tag}: Song type sits next to the title (not at the far right)`, gY.head.x - gT.head.x <= 470, `title at ${gT.head.x}, type at ${gY.head.x}`);

  // What each row says it plays from
  const rows = await fromTexts(page);
  const pl = PLAYLIST[sid];
  const want = (frag) => rows.find((r) => r.title.includes(frag))?.from;
  check(`${tag}: playlist songs say their playlist`, want('Song number 2') === (pl ?? 'Station rules'), String(want('Song number 2')));
  check(`${tag}: a song added by hand says so`, want('Song number 4') === 'Added by hand', String(want('Song number 4')));
  check(`${tag}: the time check says Time check`, want('Time check') === 'Time check', String(want('Time check')));
  check(`${tag}: the sponsorship says Sponsorship`, want('TEST SPONSOR 14') === 'Sponsorship', String(want('TEST SPONSOR 14')));
  check(`${tag}: the station ID says Station ID`, want('listening to') === 'Station ID', String(want('listening to')));
  check(`${tag}: the request says Song request`, want('Song number 5') === 'Song request', String(want('Song number 5')));
  check(`${tag}: the holiday song names its list`, (want('Mi Yimalel') ?? '').includes('Chanukah'), String(want('Mi Yimalel')));
  check(`${tag}: a song of a deleted block says so`, want('Song number 6') === (pl ? 'Removed block' : 'Station rules'), String(want('Song number 6')));
  const typeCell = await page.locator('[data-testid=queue-row]').nth(1).getByTestId('row-type').textContent();
  check(`${tag}: Song type shows the category`, typeCell === 'top_hits', typeCell);

  // Click "Playing from" on a playlist song: the Why panel, the step lit, links
  const row2 = page.locator('[data-testid=queue-row]').filter({ hasText: 'Song number 3 ' }).first();
  await row2.getByTestId('row-from').click();
  const why = page.getByTestId('why-panel');
  await why.waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: clicking Playing from opens Why it is in the queue`, await why.isVisible());
  check(`${tag}: ... and does not select the row`, (await page.getByTestId('row-actions').count()) === 0);
  const whyTitle = await why.getByTestId('why-title').textContent().catch(() => '');
  if (pl) {
    check(`${tag}: Why names the playlist`, whyTitle === `Playlist “${pl}”`, whyTitle);
    const lines = (await why.getByTestId('why-line').allTextContents()).join(' ');
    check(`${tag}: Why names the schedule, block and hours`, lines.includes('Schedule “Weekday”, block “Daytime music” (6 AM to 10 PM, every day)'), lines);
    const lit = await why.getByTestId('why-step-match').allTextContents();
    check(`${tag}: the step the song filled is lit (hot_now)`, lit.length === 1 && lit[0].includes('hot_now'), lit.join(' | '));
    const links = await why.getByTestId('why-link').evaluateAll((els) => els.map((e) => ({ t: e.textContent, href: e.getAttribute('href') })));
    check(`${tag}: links open the playlist at that step and the block`,
      links.some((l) => l.href === `/admin/playlists?open=tpl-${sid}&step=${sid === 'st-main' ? 'slot-2' : 'slot-j1'}`) && links.some((l) => l.href === '/admin/schedules/sch1/blocks?block=b-day'),
      JSON.stringify(links));
    if (main) await shot(page, `${size.tag}-2-why-playlist`);
    // Open the playlist: it is open and the step is lit; Back brings the panel back
    await why.getByTestId('why-link').first().click();
    await page.waitForURL(/\/admin\/playlists/);
    await page.getByTestId('playlist-step-lit').waitFor({ timeout: 4000 }).catch(() => {});
    check(`${tag}: the playlist page opens that playlist with the step lit`, await page.getByTestId('playlist-step-lit').isVisible().catch(() => false));
    if (main) await shot(page, `${size.tag}-3-playlist-step`);
    await page.goBack();
    await page.getByTestId('why-panel').waitFor({ timeout: 4000 }).catch(() => {});
    check(`${tag}: Back returns to the control room with Why open again`, await page.getByTestId('why-panel').isVisible().catch(() => false));
  } else {
    check(`${tag}: Why says it is not from a playlist`, whyTitle === 'Not from a playlist', whyTitle);
  }
  await page.keyboard.press('Escape');
  await sleep(200);
  check(`${tag}: Esc closes Why`, (await page.getByTestId('why-panel').count()) === 0);

  // The sponsorship's Why offers the Sponsor panel
  await page.locator('[data-testid=queue-row]').filter({ hasText: 'TEST SPONSOR 14' }).getByTestId('row-from').click();
  const sp = await page.getByTestId('why-song-panel').textContent().catch(() => '');
  check(`${tag}: a sponsorship's Why opens the Sponsor panel`, sp === 'Open the Sponsor panel', sp);
  await page.keyboard.press('Escape');

  // Drag the Title border 150 px left: Title narrower, Song type moves with it, cells too
  const before = await geometry(page, 'title');
  const typeBefore = await geometry(page, 'type');
  await borderDrag(page, 'title', -150);
  const after = await geometry(page, 'title');
  const typeAfter = await geometry(page, 'type');
  check(`${tag}: dragging the Title border narrows Title by the drag`, Math.abs(before.head.w - 150 - after.head.w) <= 2, `${before.head.w} -> ${after.head.w}`);
  check(`${tag}: ... Song type moves left with it, still over its cells`, Math.abs(typeBefore.head.x - 150 - typeAfter.head.x) <= 2 && lined(typeAfter), `${typeBefore.head.x} -> ${typeAfter.head.x}`);

  // Double-click the border between Time and Length: Time fits its widest entry
  await page.locator('[data-testid=queue-head] [data-testid=col-resize-time]').dblclick();
  await sleep(150);
  const fitted = await page.evaluate(() => [...document.querySelectorAll('[data-testid=queue-scroll] [data-col=time]')].map((c) => ({ sw: c.scrollWidth, cw: c.clientWidth })));
  const tg = await geometry(page, 'time');
  check(`${tag}: double-click fits Time: no time cut off`, fitted.every((f) => f.sw <= f.cw + 1), JSON.stringify(fitted.filter((f) => f.sw > f.cw + 1).slice(0, 2)));
  check(`${tag}: ... and no wider than it needs (under 115 px)`, tg.head.w < 115 && lined(tg), `${tg.head.w} px`);
  // Squeezed too narrow, a double-click widens it again
  await borderDrag(page, 'time', -60);
  const squeezed = (await geometry(page, 'time')).head.w;
  await page.locator('[data-testid=queue-head] [data-testid=col-resize-time]').dblclick();
  await sleep(150);
  const refit = (await geometry(page, 'time')).head.w;
  check(`${tag}: double-click widens a squeezed column to fit`, refit > squeezed + 20 && Math.abs(refit - tg.head.w) <= 2, `${squeezed} -> ${refit}`);
  if (main) await shot(page, `${size.tag}-4-resized`);

  // Kept after a reload, on another station too; Reset widths puts them back
  const kept = { title: (await geometry(page, 'title')).head.w, time: (await geometry(page, 'time')).head.w };
  await page.reload();
  await page.getByTestId('queue-row').first().waitFor();
  await sleep(500);
  const re = { title: (await geometry(page, 'title')).head.w, time: (await geometry(page, 'time')).head.w };
  check(`${tag}: widths are kept after a reload`, Math.abs(re.title - kept.title) <= 1 && Math.abs(re.time - kept.time) <= 1, JSON.stringify({ kept, re }));
  await page.getByTestId('col-reset').first().click();
  await sleep(150);
  const back = await geometry(page, 'title');
  check(`${tag}: Reset widths puts the columns back`, Math.abs(back.head.w - before.head.w) <= 2 && (await page.locator('[data-testid=queue-head] [data-testid=col-reset]').count()) === 0, `${back.head.w} vs ${before.head.w}`);

  // The library below: same headings that line up and drag
  const lib = await geometry(page, 'artist', 'library');
  if (lib.head) {
    check(`${tag}: library headings line up with the rows`, lined(lib) && lined(await geometry(page, 'type', 'library')), JSON.stringify({ head: lib.head, cell: lib.cells[0] }));
    await borderDrag(page, 'artist', -80, 'library');
    const lib2 = await geometry(page, 'artist', 'library');
    check(`${tag}: dragging the library's Artist border narrows it`, Math.abs(lib.head.w - 80 - lib2.head.w) <= 2 && lined(lib2), `${lib.head.w} -> ${lib2.head.w}`);
  }
  const docW = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  check(`${tag}: no sideways scrolling`, docW <= 0, `${docW}`);
  await ctx.close();
}

async function phone(browser, size, sid) {
  const name = STATIONS.find((s) => s.id === sid).name;
  const tag = `${size.tag} · ${name}`;
  const st = station(sid);
  const { ctx, page } = await open(browser, size, st);
  const main = sid === 'st-main';
  check(`${tag}: no column headings on a phone`, (await page.getByTestId('queue-head').count()) === 0);
  const row = page.locator('[data-testid=queue-row]').filter({ hasText: 'Song number 2 ' }).first();
  await row.tap({ position: { x: 150, y: 14 } });
  const strip = page.getByTestId('row-from-touch');
  await strip.waitFor({ timeout: 2000 }).catch(() => {});
  const text = await strip.textContent().catch(() => '');
  check(`${tag}: tapping a row says where it plays from`, text.includes(PLAYLIST[sid] ?? 'Station rules') && text.includes('Why'), text);
  const btns = await page.getByTestId('row-actions').locator('button').evaluateAll((els) => els.map((e) => { const b = e.getBoundingClientRect(); return { t: e.textContent.trim(), h: Math.round(b.height), ok: b.bottom <= innerHeight && b.right <= innerWidth }; }));
  check(`${tag}: the row's buttons are still 44 px and on screen`, btns.length >= 5 && btns.every((b) => b.h >= 44 && b.ok), JSON.stringify(btns));
  // The rows below are drawn where they really are (the virtual list counts the taller bar)
  const gap = await page.evaluate(() => {
    const bar = document.querySelector('[data-testid=row-actions]')?.getBoundingClientRect();
    const sel = document.querySelector('[data-testid=row-actions]')?.closest('[data-testid=queue-row]');
    const next = sel?.nextElementSibling?.getBoundingClientRect();
    return bar && next ? Math.round(next.top - bar.bottom) : null;
  });
  check(`${tag}: the next row follows the bar with no gap or overlap`, gap === 0, String(gap));
  if (main) await shot(page, `${size.tag}-1-row-tapped`);
  await strip.tap();
  const why = page.getByTestId('why-panel');
  await why.waitFor({ timeout: 2000 }).catch(() => {});
  const box = await why.boundingBox().catch(() => null);
  check(`${tag}: Why opens and fits the screen`, !!box && box.x >= 0 && box.x + box.width <= size.opts.viewport.width + 1, JSON.stringify(box));
  const links = await why.locator('a, button:not([aria-label=Close])').evaluateAll((els) => els.filter((e) => e.offsetParent).map((e) => Math.round(e.getBoundingClientRect().height)));
  check(`${tag}: Why's links are finger-sized`, links.length > 0 && links.every((h) => h >= 36), JSON.stringify(links));
  if (main) await shot(page, `${size.tag}-2-why`);
  await why.getByRole('button', { name: 'Close' }).tap();
  const docW = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  check(`${tag}: no sideways scrolling`, docW <= 0, `${docW}`);
  await ctx.close();
}

// A viewer cannot select rows: a tap opens the Song panel, which leads to Why
async function viewer(browser, size, sid) {
  const tag = `${size.tag} · viewer`;
  const st = station(sid);
  const { ctx, page } = await open(browser, size, st, { role: 'viewer' });
  await page.locator('[data-testid=queue-row]').filter({ hasText: 'Song number 2 ' }).first().tap({ position: { x: 150, y: 14 } });
  const btn = page.getByTestId('song-why');
  await btn.waitFor({ timeout: 2000 }).catch(() => {});
  check(`${tag}: the Song panel says where it plays from`, (await btn.textContent().catch(() => '')).includes('Top 500 Hits'));
  await btn.tap().catch(() => {});
  await page.getByTestId('why-panel').waitFor({ timeout: 2000 }).catch(() => {});
  check(`${tag}: ... and opens Why`, await page.getByTestId('why-panel').isVisible().catch(() => false));
  // A viewer may not open the playlist page: no dead link, a word instead
  const links = await page.getByTestId('why-link').evaluateAll((els) => els.map((e) => e.getAttribute('href')));
  check(`${tag}: no link to pages a viewer cannot open`, !links.some((h) => h.startsWith('/admin/playlists')), JSON.stringify(links));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  for (const size of SIZES) {
    for (const s of STATIONS) {
      if (size.desk) await desktop(browser, size, s.id);
      else await phone(browser, size, s.id);
    }
  }
  await viewer(browser, SIZES[3], 'st-main');
} finally {
  await browser.close();
}
process.exit(done());
