/**
 * Control room on a car screen, a tablet, phones and a desktop: how many queue rows
 * fit, one line per row, and the level meters on every size. Found 2026-11-18: in
 * Shmuel's Tesla only about 3 songs showed at a time (rows were two lines, 66 px), and
 * the left/right meters were missing on anything narrower than 1024 px.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4331 --strictPort &)
 *   BASE_URL=http://localhost:4331 node e2e/control-room-compact.mjs   # optional: SHOTS_DIR, ONLY=<size,...>
 *
 * Exits non-zero if a check fails.
 */
import fs from 'node:fs';
import {
  chromium, devices, checker, sleep, makeStation, adminContext, newPage, IOS_SOUND_POLICY,
} from './lib/fake-station.mjs';
import { startStreamServer } from './lib/stream-server.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4331';
const SHOTS = process.env.SHOTS_DIR || '';
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
const STREAM_PORT = Number(process.env.STREAM_PORT || 4333);
const { check, done } = checker({});

// Car screens: the Tesla browser is a Chromium on a wide touch screen; its page area
// depends on the model and zoom, so two likely sizes plus the coordinator's 1200x900.
const SIZES = [
  { tag: 'car 960x540', opts: { viewport: { width: 960, height: 540 }, hasTouch: true, isMobile: false }, minRows: 8 },
  { tag: 'car 1000x620', opts: { viewport: { width: 1000, height: 620 }, hasTouch: true, isMobile: false }, minRows: 9 },
  { tag: 'car 1200x900', opts: { viewport: { width: 1200, height: 900 }, hasTouch: true, isMobile: false }, minRows: 9 },
  { tag: 'iPhone 13', opts: { ...devices['iPhone 13'] }, minRows: 6, ios: true },
  { tag: 'iPhone SE (2nd/3rd)', opts: { ...devices['iPhone SE (3rd gen)'] ?? { viewport: { width: 375, height: 667 }, isMobile: true, hasTouch: true }, viewport: { width: 375, height: 667 } }, minRows: 4, ios: true },
  { tag: 'iPhone SE (1st)', opts: { ...devices['iPhone SE'] }, minRows: 3, ios: true },
  { tag: 'iPhone 13 sideways', opts: { ...devices['iPhone 13 landscape'] }, minRows: 3, ios: true },
  { tag: 'desktop 1440x900', opts: { viewport: { width: 1440, height: 900 } }, minRows: 9 },
];

const STATIONS = [
  { id: 'st-main', name: "Kol B'ramah Main", timezone: 'America/New_York', is_active: true, channels: [] },
  { id: 'st-stories', name: "Kol B'ramah - All Day Stories", timezone: 'America/New_York', is_active: true, channels: [] },
  { id: 'st-jlm', name: 'KBR Jerusalem', timezone: 'Asia/Jerusalem', is_active: true, channels: [] },
];

// A queue that looks like Main's: long titles, Hebrew, a timed block with a sponsor
function station(sid, stream) {
  const s = makeStation({ sid, stations: STATIONS, queueSize: 0,
    playout: { stations: Object.fromEntries(STATIONS.map(x => [x.id, { mode: stream && x.id === sid ? 'stream' : 'browser' }])), default: { mode: 'browser' } } });
  s.assets.push({ id: 'heb', title: 'יהי רצון שתהא השעה הזאת שעת רחמים ועת רצון מלפניך', artist: 'Dedi', album: null, duration: 240, asset_type: 'music', category: 'top_hits' });
  s.assets.push({ id: 'long', title: 'A really very long song title that cannot possibly fit on one line of any phone screen at all', artist: 'Yaakov Shwekey and the Shira Choir', album: null, duration: 312, asset_type: 'music', category: 'hot_now' });
  s.assets.push({ id: 'sp4', title: 'TEST SPONSOR 04 - Estreia', artist: "Kol B'ramah", album: null, duration: 30, asset_type: 'spot', category: 'test' });
  s.assets.push({ id: 'shiur', title: 'Daf Yomi shiur: Bava Basra daf 12 with Rabbi Example', artist: 'Rabbi Example', album: null, duration: 2700, asset_type: 'shiur', category: 'shiurim' });
  for (const a of s.assets.slice(-4)) s.byId[a.id] = a;
  let pos = 0;
  const add = (asset_id, extra = {}) => s.add({ asset_id, position: ++pos, ...extra });
  const on = add('heb'); on.status = 'playing'; on.started_at = new Date(Date.now() - 60_000).toISOString();
  add('long');
  const at = new Date(Date.now() + 9 * 60_000).toISOString();
  add('tc', { preempt_at: at, hard_timed: true, source: 'block' });
  add('sp4', { preempt_at: at, hard_timed: true, source: 'ad_slot' });
  add('wx', { preempt_at: at, hard_timed: true, source: 'block' });
  add('shiur');
  for (let i = 1; i <= 60; i++) add(`m${i}`);
  if (stream) {
    const base = s.nowPlaying;
    s.nowPlaying = (extra) => { const d = base(extra); return d && { ...d, stream_url: stream.url(sid) }; };
  }
  return s;
}

async function open(browser, size, st, storage = {}) {
  const { ctx } = await adminContext(browser, size.opts, st, { storage: { radio_last_station: st.SID, 'kbr-cr-listen': null, ...storage } });
  if (size.ios) await ctx.addInitScript(IOS_SOUND_POLICY);
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  await sleep(600);
  return { ctx, page, errors };
}

const shot = async (page, name) => {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name.replace(/[^a-z0-9]+/gi, '-').toLowerCase()}.png` });
};

// Rows wholly inside the queue's scroll area
const rowsOnScreen = (page) => page.evaluate(() => {
  const sc = document.querySelector('[data-testid=queue-scroll]');
  if (!sc) return { full: 0, heights: [] };
  const box = sc.getBoundingClientRect();
  const rows = [...sc.querySelectorAll('[data-testid=queue-row]')];
  const full = rows.filter(r => { const b = r.getBoundingClientRect(); return b.height > 0 && b.top >= box.top - 1 && b.bottom <= box.bottom + 1; });
  const visiblePart = Math.max(0, Math.min(box.bottom, innerHeight) - box.top);
  return { full: full.length, heights: [...new Set(rows.slice(0, 10).map(r => Math.round(r.getBoundingClientRect().height)))], areaH: Math.round(visiblePart) };
});

// Each row's title is one line: no wrapping, cut with an ellipsis
const titleLines = (page) => page.evaluate(() => [...document.querySelectorAll('[data-testid=queue-row] [data-testid=row-title]')].slice(0, 12).map(el => {
  const lh = parseFloat(getComputedStyle(el).lineHeight) || parseFloat(getComputedStyle(el).fontSize) * 1.25;
  return { text: el.textContent.slice(0, 30), lines: Math.round(el.getBoundingClientRect().height / lh), cut: el.scrollWidth > el.clientWidth + 1, ell: getComputedStyle(el).textOverflow, font: parseFloat(getComputedStyle(el).fontSize) };
}));

async function sizeChecks(browser, size, stream) {
  for (const s of STATIONS) {
    const tag = `${size.tag} · ${s.name}`;
    const st = station(s.id, s.id === 'st-stories' ? null : stream);   // Main and Jerusalem on the server stream, Stories in the browser
    const { ctx, page, errors } = await open(browser, size, st);
    if (s.id === 'st-main') await shot(page, `${size.tag}-1-queue`);

    const r = await rowsOnScreen(page);
    check(`${tag}: at least ${size.minRows} whole rows on screen`, r.full >= size.minRows, `${r.full} rows in ${r.areaH} px; heights ${r.heights}`);
    const lines = await titleLines(page);
    check(`${tag}: every title is on one line`, lines.length > 0 && lines.every(l => l.lines === 1), JSON.stringify(lines.filter(l => l.lines !== 1)));
    check(`${tag}: titles too long for the row end with an ellipsis`, lines.filter(l => l.cut).every(l => l.ell === 'ellipsis'), JSON.stringify(lines.filter(l => l.cut).map(l => l.text)));
    const minFont = Math.min(...lines.map(l => l.font));
    check(`${tag}: title text not made smaller (13 px desktop, 15 px touch)`, minFont >= (size.tag.startsWith('desktop') || size.tag === 'car 1200x900' ? 13 : 15), `${minFont} px`);
    const clock = await page.evaluate(() => { const el = document.querySelector('[data-testid=lcd-clock]'); if (!el) return null; const b = el.getBoundingClientRect(); return { right: Math.round(b.right), vw: innerWidth, w: Math.round(b.width) }; });
    check(`${tag}: the clock is wholly on screen`, !clock || (clock.right <= clock.vw && clock.w > 60), JSON.stringify(clock));
    const docW = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
    check(`${tag}: no sideways scrolling`, docW <= 0, `${docW} px too wide`);

    // The insert button stays a usable tap target (touch sizes)
    if (!size.tag.startsWith('desktop') && size.tag !== 'car 1200x900') {
      const b = await page.getByTestId('insert-here').first().boundingBox();
      check(`${tag}: + button is a usable tap target`, b && b.width >= 40 && b.height >= 38, b ? `${Math.round(b.width)}x${Math.round(b.height)}` : 'missing');
      // Tap a row: its actions (Insert, move, Song, Remove) appear, 44 px tall
      const row = page.getByTestId('queue-row').nth(5);
      await row.tap({ position: { x: 160, y: 12 } });
      const bar = page.getByTestId('row-actions');
      await bar.waitFor({ timeout: 2000 }).catch(() => {});
      const btns = await bar.locator('button').evaluateAll(els => els.map(e => { const b = e.getBoundingClientRect(); return { t: e.textContent.trim(), h: Math.round(b.height), w: Math.round(b.width), vis: b.bottom <= innerHeight && b.right <= innerWidth }; })).catch(() => []);
      check(`${tag}: tapping a row shows Insert, ▲, ▼, Song, Remove`, btns.length >= 5, btns.map(b => b.t).join(' '));
      check(`${tag}: row action buttons are 44 px tall and on screen`, btns.length > 0 && btns.every(b => b.h >= 44 && b.vis), JSON.stringify(btns));
      if (s.id === 'st-main') await shot(page, `${size.tag}-2-row-actions`);
      // Move down really moves it
      const id = await row.getAttribute('data-entry-id');
      await bar.getByRole('button', { name: 'Move down' }).tap();
      await sleep(400);
      check(`${tag}: ▼ sends the move`, st.calls.some(c => c.path.endsWith('/move-down')), st.calls.slice(-3).map(c => c.path).join(' '));
      await row.tap({ position: { x: 160, y: 12 }, timeout: 3000 }).catch(() => {});
    } else {
      // Desktop: the row's own buttons show on hover
      const row = page.getByTestId('queue-row').nth(3);
      await row.hover();
      const q = row.getByTestId('row-quick');
      check(`${tag}: hover shows Song and move buttons`, await q.isVisible() && await q.locator('button').count() >= 3);
    }

    // Level meters: present, and moving once the page has been tapped
    const meters = page.locator('[data-testid=vu-meters]:visible');
    if (size.opts.viewport.width < 375) {   // the first iPhone SE: no room beside the clock
      check(`${tag}: no meters squeezed onto a 320 px screen`, await meters.count() === 0);
      check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
      await ctx.close();
      continue;
    }
    check(`${tag}: left and right meters are on screen`, await meters.count() === 1, `${await meters.count()} visible`);
    const lit = () => page.locator('[data-testid=vu-meters]:visible [data-lit="1"]').count();
    const before = await lit();
    if (size.opts.hasTouch) await page.getByText('Up next', { exact: true }).first().tap();
    else await page.getByText('Up next', { exact: true }).first().click();
    let after = 0;
    for (let i = 0; i < 25 && after === 0; i++) { await sleep(200); after = await lit(); }
    check(`${tag}: meters move after the first tap (${s.id === 'st-stories' ? 'browser player' : 'server stream'})`, after > 0, `${before} -> ${after} lit`);
    // Then Listen: they keep moving while the station is heard
    const listen = page.getByTestId('listen-here').locator('visible=true').first();
    if (await listen.count()) {
      if (size.opts.hasTouch) await listen.tap(); else await listen.click();
      await sleep(1200);
      const heard = await lit();
      check(`${tag}: meters keep moving while listening`, heard > 0, `${heard} lit`);
    }
    if (s.id === 'st-main') await shot(page, `${size.tag}-3-meters`);
    check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });
const stream = await startStreamServer(STREAM_PORT);
try {
  for (const size of SIZES) {
    if (ONLY.length && !ONLY.some(o => size.tag.includes(o))) continue;
    await sizeChecks(browser, size, stream);
  }
} finally {
  await browser.close();
  await stream.close?.();
}
process.exit(done());
