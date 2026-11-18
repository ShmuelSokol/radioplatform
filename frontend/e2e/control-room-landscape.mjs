/**
 * The control room with the phone held sideways (docs/test-checklist.md F-LANDSCAPE-*),
 * against the fake backend. Shmuel, 2026-11-17: "the control room with a phone held
 * sideways is unusable". Checks that an operator can see what is on air and the time
 * left, press Go next / Fade / Panic, see and scroll the queue, insert from the library
 * and fire instant buttons, on common phones, with no zoom, no sideways scroll, nothing
 * cut off and every tap target at least 44 px; and that turning the phone keeps state
 * and sound.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4184 --strictPort &)
 *   BASE_URL=http://localhost:4184 node e2e/control-room-landscape.mjs   # optional: ONLY=<part,...> SHOTS_DIR=<dir>
 */
import fs from 'fs';
import { fileURLToPath } from 'url';
import {
  chromium, checker, sleep, makeStation, adminContext, newPage, timeClick, timeUntil,
} from './lib/fake-station.mjs';
import { allowEverywhere } from './lib/guard.mjs';

// Provoked on purpose; anything else the screen or console shows fails the run (e2e/lib/guard.mjs)
allowEverywhere(/warning toast: (HS is off|Spots mode is off|Panic: cut what was on air)/, 'amber on purpose: each says what the switch just did');

const BASE = process.env.BASE_URL || 'http://localhost:4184';
const ONLY = process.env.ONLY || '';
const SHOTS = process.env.SHOTS_DIR || '';
const SRC = fileURLToPath(new URL('../src/pages/admin/controlroom/', import.meta.url));
const { check, done } = checker({});

const phoneCtx = (w, h, extra = {}) => ({ viewport: { width: w, height: h }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, ...extra });
// Sideways sizes (CSS px). "Safari" heights are the screen minus Safari's bars.
const PHONES = [
  ['iPhone SE', 667, 375],
  ['iPhone SE in Safari', 667, 323],
  ['iPhone 14', 844, 390],
  ['iPhone 14 in Safari', 844, 340],
  ['iPhone 14 Pro Max', 932, 430],
  ['iPhone Pro Max in Safari', 932, 300],
  ['Pixel 7', 915, 412],
  ['Galaxy S21', 800, 360],
  ['Chrome on iPhone 14', 844, 350],
];

async function openRoom(browser, contextOpts, station = makeStation({ queueSize: 30 }), opts = {}) {
  const { ctx, np } = await adminContext(browser, contextOpts, station, opts);
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}${opts.path || '/admin/dashboard'}`);
  if (!opts.path) await page.getByTestId('queue-row').first().waitFor();
  return { ctx, np, page, errors, station };
}
const tab = (page, name) => page.locator('[aria-label="Control room sections"] button', { hasText: name }).first();
const writes = (st, re) => st.calls.filter((c) => c.method !== 'GET' && re.test(c.path));
const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: `${SHOTS}/${name}.png` }); } };

/** Everything an operator needs, measured in the page. */
const measure = (page) => page.evaluate(() => {
  const vw = innerWidth, vh = innerHeight;
  const room = document.querySelector('[data-testid=control-room]');
  const rect = (el) => el && el.getBoundingClientRect();
  const inView = (el) => { const r = rect(el); return !!r && r.width > 0 && r.height > 0 && r.top >= -1 && r.left >= -1 && r.bottom <= vh + 1 && r.right <= vw + 1; };
  const byText = (re) => [...room.querySelectorAll('button')].find((b) => re.test(b.textContent));
  const header = room.querySelector(':scope > header');
  const side = room.querySelector(':scope > .cr-side');
  const qs = room.querySelector('[data-testid=queue-scroll]');
  const qr = rect(qs);
  const rows = [...room.querySelectorAll('[data-testid=queue-row]')].filter((r) => {
    const b = r.getBoundingClientRect();
    return qr && b.height > 0 && b.top >= qr.top - 1 && b.bottom <= qr.bottom + 1 && b.bottom <= vh + 1;
  }).length;
  // Anything sticking out sideways (cut off) inside the control room
  const sticking = [...room.querySelectorAll('button, select, input, a, [data-testid=remaining]')].filter((el) => {
    const b = el.getBoundingClientRect();
    if (!b.width || !b.height) return false;
    // inside a sideways scroller is fine (the library type buttons)
    for (let p = el.parentElement; p && p !== room; p = p.parentElement) {
      const s = getComputedStyle(p);
      if ((s.overflowX === 'auto' || s.overflowX === 'scroll') && p.scrollWidth > p.clientWidth) return false;
    }
    return b.right > vw + 1 || b.left < -1;
  }).map((el) => (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 20));
  return {
    vw, vh, docW: document.documentElement.scrollWidth, scrollY, roomTop: Math.round(rect(room).top + scrollY),
    landscape: !!room.querySelector('[data-testid=transport-landscape]'),
    headerW: header ? Math.round(rect(header).width) : 0,
    headerH: header ? Math.round(rect(header).height) : 0,
    sideH: side ? Math.round(rect(side).height) : 0,
    rows,
    goNext: inView(room.querySelector('[data-testid=go-next]')),
    fade: inView(room.querySelector('[data-testid=next-fade]')),
    soft: inView(room.querySelector('[data-testid=next-soft]')),
    panic: inView(room.querySelector('[data-testid=panic]')),
    hs: inView(room.querySelector('[data-testid=mode-hs]')),
    listen: inView(byText(/Listen/) || room.querySelector('button[aria-label=Mute],button[aria-label=Unmute]')),
    remaining: inView(room.querySelector('[data-testid=remaining]')),
    tabs: inView(room.querySelector('[aria-label="Control room sections"]')),
    nowNext: [...header.querySelectorAll('.qn-sunken')].filter(inView).length,
    sticking,
    nav: !!document.querySelector('.app-shell > nav'),
  };
});

/** Visible buttons, selects, inputs and links smaller than 44 x 44 px. */
const smallTargets = (page) => page.evaluate(() => {
  const vw = innerWidth, vh = innerHeight;
  const room = document.querySelector('[data-testid=control-room]');
  return [...room.querySelectorAll('button, select, input:not([type=range]), a[href]')].filter((el) => {
    const b = el.getBoundingClientRect();
    if (!b.width || !b.height || b.bottom <= 0 || b.top >= vh || b.right <= 0 || b.left >= vw) return false;
    if (getComputedStyle(el).visibility === 'hidden') return false;
    // Clipped out of sight by a scrolling pane
    for (let p = el.parentElement; p && p !== room; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (/(auto|scroll|hidden)/.test(s.overflowY + s.overflowX)) {
        const r = p.getBoundingClientRect();
        if (b.bottom <= r.top || b.top >= r.bottom || b.right <= r.left || b.left >= r.right) return false;
      }
    }
    return b.height < 43.5 || b.width < 43.5;
  }).map((el) => `${(el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 18)} ${Math.round(el.getBoundingClientRect().width)}x${Math.round(el.getBoundingClientRect().height)}`);
});

// ── F-LANDSCAPE-1, 2, 3, 7, 10, 13: the layout on each phone ─────────────
async function layout(browser) {
  for (const [name, w, h] of PHONES) {
    const tag = `layout ${name} ${w}x${h}`;
    const { ctx, page, errors } = await openRoom(browser, phoneCtx(w, h));
    await sleep(400);
    const m = await measure(page);
    check(`${tag}: sideways layout is on`, m.landscape);
    const want = Math.min(400, Math.max(260, w * 0.42));
    check(`${tag}: transport column 42 % of the width, 260-400 px (F-LANDSCAPE-1)`, Math.abs(m.headerW - want) <= 2, `${m.headerW} px, want ${Math.round(want)}`);
    check(`${tag}: transport and queue both use the full height`, Math.abs(m.headerH - h) <= 1 && Math.abs(m.sideH - h) <= 1, `${m.headerH}/${m.sideH} of ${h}`);
    check(`${tag}: at least 4 queue rows on screen`, m.rows >= 4, `${m.rows}`);
    check(`${tag}: Go next, Fade, Soft fade, Panic, HS, Listen on screen`, m.goNext && m.fade && m.soft && m.panic && m.hs && m.listen, JSON.stringify(m));
    check(`${tag}: remaining time, on air and next titles, the tabs on screen`, m.remaining && m.nowNext === 2 && m.tabs, JSON.stringify(m));
    check(`${tag}: page starts scrolled to the control room, site menu above (F-LANDSCAPE-2)`, m.nav && Math.abs(m.scrollY - m.roomTop) <= 1 && m.roomTop > 0, `scrollY ${m.scrollY}, room at ${m.roomTop}`);
    check(`${tag}: no sideways scroll (F-LANDSCAPE-3)`, m.docW <= m.vw, `doc ${m.docW} in ${m.vw}`);
    check(`${tag}: nothing cut off at the sides`, m.sticking.length === 0, m.sticking.join(', '));
    // Tap targets in every pane (F-LANDSCAPE-7)
    const small = [];
    small.push(...(await smallTargets(page)).map((s) => `Queue: ${s}`));
    for (const t of ['Buttons', 'Library', 'Go live', 'More']) {
      await tab(page, t).click();
      await sleep(250);
      small.push(...(await smallTargets(page)).map((s) => `${t}: ${s}`));
      const mm = await measure(page);
      if (mm.docW > mm.vw || mm.sticking.length) small.push(`${t}: sideways overflow ${mm.docW} ${mm.sticking.join(',')}`);
      if (t === 'Library') await shot(page, `${name.replace(/\s+/g, '-')}-library`);
    }
    check(`${tag}: every visible control at least 44 x 44 px in every pane (F-LANDSCAPE-7)`, small.length === 0, small.join(' | '));
    // Back to the queue, scroll it: the transport stays put, Go next still reachable
    await tab(page, 'Queue').click();
    const qs = page.getByTestId('queue-scroll');
    await qs.evaluate((el) => { el.scrollTop = 400; });
    await sleep(150);
    const sc = await qs.evaluate((el) => el.scrollTop);
    const m2 = await measure(page);
    check(`${tag}: the queue scrolls on its own; Go next and remaining stay on screen`, sc >= 300 && m2.goNext && m2.remaining && m2.tabs, `scrollTop ${sc}`);
    // The site menu comes back by scrolling the page up
    await page.evaluate(() => window.scrollTo(0, 0));
    const navSeen = await page.evaluate(() => { const r = document.querySelector('.app-shell > nav').getBoundingClientRect(); return r.top >= 0 && r.height > 0; });
    check(`${tag}: the site menu is reachable by scrolling up`, navSeen);
    await page.evaluate(() => document.querySelector('[data-testid=control-room]').scrollIntoView({ block: 'start' }));
    await shot(page, `${name.replace(/\s+/g, '-')}-queue`);
    check(`${tag}: no script errors (F-LANDSCAPE-3)`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── F-LANDSCAPE-13: the transport column scrolls when taller than the screen ─────────────
async function tallTransport(browser) {
  const tag = 'tall transport';
  const station = makeStation({ queueSize: 20, shows: [{ id: 'ls1', station_id: 'st-1', title: 'Thursday Night Live', status: 'live', scheduled_start: new Date().toISOString() }] });
  const { ctx, page, errors } = await openRoom(browser, phoneCtx(700, 240), station);
  await sleep(400);
  const r = await page.evaluate(() => {
    const h = document.querySelector('[data-testid=control-room] > header');
    const next = document.querySelector('[data-testid=go-next]').getBoundingClientRect();
    const panic = document.querySelector('[data-testid=panic]').getBoundingClientRect();
    return { overflowY: getComputedStyle(h).overflowY, sh: h.scrollHeight, ch: h.clientHeight, nextBottom: next.bottom, panicBottom: panic.bottom, vh: innerHeight };
  });
  check(`${tag}: the column scrolls on its own`, r.overflowY === 'auto', JSON.stringify(r));
  check(`${tag}: Go next and Panic are on screen without scrolling it, even 240 px tall`, r.nextBottom <= r.vh && r.panicBottom <= r.vh, JSON.stringify(r));
  if (r.sh > r.ch) {
    await page.locator('[data-testid=control-room] > header').evaluate((el) => { el.scrollTop = 999; });
    const st = await page.locator('[data-testid=control-room] > header').evaluate((el) => el.scrollTop);
    check(`${tag}: and scrolls to the rest (Listen, station)`, st > 0, `${st}`);
  }
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── F-LANDSCAPE-11, 12, 8: the queries, tablets, exactly 500 px, no :has() ─────────────
async function queries(browser) {
  const tag = 'queries';
  const css = fs.readFileSync(SRC + 'classic.css', 'utf8');
  const ts = fs.readFileSync(SRC + 'shared.ts', 'utf8');
  const tsQ = ts.match(/PHONE_LANDSCAPE = '([^']+)'/)?.[1];
  // One query only: the page decides (usePhoneLandscape) and sets html.cr-land; the CSS follows the class
  check(`${tag}: the CSS has no copy of the query to drift out of step; it follows html.cr-land (F-LANDSCAPE-11)`,
    !!tsQ && !/@media[^{]*max-height: 500px/.test(css) && /html\.cr-land \.cr-root > header/.test(css), tsQ);
  const block = css.slice(css.indexOf('html.cr-land'));
  const hasRules = block.split('\n').filter((l) => l.includes(':has(') && !l.trim().startsWith('/*') && !l.trim().startsWith('*'));
  check(`${tag}: the sideways layout does not need :has() (only the keyboard nicety does) (F-LANDSCAPE-12)`, hasRules.length === 1 && /input\[type="search"\]:focus/.test(hasRules[0]), hasRules.join(' | '));
  check(`${tag}: notch and rounded corners: left, right and bottom safe areas kept (F-LANDSCAPE-8)`,
    /header \{[^}]*padding-left: env\(safe-area-inset-left\)[^}]*padding-bottom: env\(safe-area-inset-bottom\)/s.test(block)
    && /\.cr-side \{[^}]*padding-right: env\(safe-area-inset-right\)/.test(block)
    && /\.cr-tabs \{[^}]*padding-bottom: env\(safe-area-inset-bottom\)/.test(block));
  const viewport = fs.readFileSync(fileURLToPath(new URL('../index.html', import.meta.url)), 'utf8');
  check(`${tag}: the page asks for the full screen (viewport-fit=cover) so the safe areas apply`, /viewport-fit=cover/.test(viewport));
  for (const [name, opts, wantLand] of [
    ['iPad Mini sideways 1024x768', { viewport: { width: 1024, height: 768 }, isMobile: true, hasTouch: true }, false],
    ['phone exactly 500 px tall (900x500)', phoneCtx(900, 500), true],
    ['501 px tall (900x501)', phoneCtx(900, 501), false],
    ['iPhone portrait 390x844', phoneCtx(390, 844), false],
  ]) {
    const { ctx, page, errors } = await openRoom(browser, opts);
    await sleep(300);
    const m = await page.evaluate(() => ({
      land: !!document.querySelector('[data-testid=transport-landscape]'),
      cls: document.documentElement.classList.contains('cr-land'),
      mq: matchMedia('(orientation: landscape) and (max-height: 500px) and (max-width: 1023.98px)').matches,
      desktop: !!document.querySelector('aside[aria-label=Controls]'),
      docW: document.documentElement.scrollWidth, vw: innerWidth,
    }));
    check(`${tag}: ${name} gets the ${wantLand ? 'sideways' : 'normal'} layout${name.startsWith('iPad') ? ' (desktop)' : ''}`, m.land === wantLand && m.cls === wantLand && m.mq === wantLand && (!name.startsWith('iPad') || m.desktop), JSON.stringify(m));
    check(`${tag}: ${name}: no sideways scroll, no script errors`, m.docW <= m.vw && errors.length === 0, `${m.docW} ${errors.join(' | ')}`);
    await ctx.close();
  }
}

// ── F-LANDSCAPE-4, 6: turning the phone mid-use ─────────────
async function rotate(browser) {
  const tag = 'rotate';
  const { ctx, page, errors, station } = await openRoom(browser, phoneCtx(390, 844));
  await page.locator('button:visible', { hasText: 'Listen' }).first().click();
  const heard = await timeUntil(page, `() => window.__audible().length >= 1 && window.__outLevel() > 0`, 4000);
  check(`${tag}: Listen plays in portrait`, heard >= 0);
  // Scroll the queue to the 8th row and select the 9th
  const qs = page.getByTestId('queue-scroll');
  await qs.evaluate((el) => { el.scrollTop = 66 * 8; });
  await sleep(200);
  const target = station.pending()[8];
  await page.locator(`[data-testid=queue-row][data-entry-id="${target.id}"] > div`).last().click({ position: { x: 200, y: 20 } }).catch(() => {});
  await page.getByTestId('row-actions').waitFor({ timeout: 3000 }).catch(() => {});
  const topRow = () => page.evaluate(() => {
    const qs = document.querySelector('[data-testid=queue-scroll]').getBoundingClientRect();
    const r = [...document.querySelectorAll('[data-testid=queue-row]')].find((x) => x.getBoundingClientRect().bottom > qs.top + 4);
    return r?.dataset.entryId;
  });
  const top0 = await topRow();
  // Sample the sound every 25 ms while turning
  await page.evaluate(() => { window.__gaps = 0; window.__samp = setInterval(() => { if (!(window.__audible().length >= 1 && window.__outLevel() > 0)) window.__gaps++; }, 25); });
  const t0 = Date.now();
  await page.setViewportSize({ width: 844, height: 390 });
  const settled = await timeUntil(page, `() => !!document.querySelector('[data-testid=transport-landscape]') && document.querySelector('[data-testid=control-room]').getBoundingClientRect().width === innerWidth && document.querySelector('[data-testid=control-room] > header').getBoundingClientRect().height === innerHeight`, 2000);
  check(`${tag}: portrait -> sideways settles within 300 ms (F-LANDSCAPE-6)`, settled >= 0 && settled < 300, `${settled} ms (wall ${Date.now() - t0})`);
  await sleep(300);
  const m = await measure(page);
  check(`${tag}: sideways: scrolled to the control room`, Math.abs(m.scrollY - m.roomTop) <= 1 && m.roomTop > 0, `scrollY ${m.scrollY} room ${m.roomTop}`);
  check(`${tag}: sideways: the selected row is still selected`, (await page.getByTestId('row-actions').count()) === 1);
  const top1 = await topRow();
  check(`${tag}: sideways: the queue is still scrolled to the same rows`, top1 === top0, `${top0} -> ${top1}`);
  check(`${tag}: sideways: same station`, (await page.locator('select[aria-label=Station]:visible').inputValue()) === station.SID);
  // Open the library sideways and turn back
  await tab(page, 'Library').click();
  await page.getByTestId('library-row').first().waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  const back = await timeUntil(page, `() => !document.querySelector('[data-testid=transport-landscape]') && !!document.querySelector('[data-testid=library-row]')`, 2000);
  check(`${tag}: sideways -> portrait settles within 300 ms and keeps the open pane (Library)`, back >= 0 && back < 300, `${back} ms`);
  await sleep(300);
  const pageScroll = await page.evaluate(() => ({ y: scrollY, docH: document.documentElement.scrollHeight, vh: innerHeight }));
  check(`${tag}: portrait again: no page scroll left over`, pageScroll.y === 0 && pageScroll.docH <= pageScroll.vh, JSON.stringify(pageScroll));
  await page.evaluate(() => clearInterval(window.__samp));
  const gaps = await page.evaluate(() => window.__gaps);
  check(`${tag}: Listen kept playing through both turns, no break (F-LANDSCAPE-4, 6)`, gaps === 0, `${gaps} silent samples`);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── F-LANDSCAPE-5, 6: every control works sideways, and shows within 150 ms ─────────────
async function controls(browser) {
  const tag = 'controls sideways';
  const SLOW = 300;
  const { ctx, page, errors, station } = await openRoom(browser, phoneCtx(844, 390), makeStation({ queueSize: 20 }), { delay: (p, m) => (m !== 'GET' ? SLOW : 0) });
  const times = [];
  const t = async (name, loc, cond) => { const ms = await timeClick(page, loc, cond, 3000, 'tap'); times.push([name, ms]); await sleep(SLOW + 300); return ms; };
  // Listen
  await t('Listen', page.locator('button:visible', { hasText: 'Listen' }).first(), `() => !!document.querySelector('button[aria-label=Mute],button[aria-label=Unmute]')`);
  check(`${tag}: Listen plays`, await timeUntil(page, `() => window.__audible().length >= 1`, 4000) >= 0);
  // Go next, Fade, Soft fade
  const busy = `() => { const b = document.querySelector('[data-testid=go-next]'); return b.disabled; }`;
  for (const [name, loc, fade] of [['Go next', 'go-next', 'hard'], ['Fade', 'next-fade', 'fade'], ['Soft fade', 'next-soft', 'soft']]) {
    const n = writes(station, /\/queue\/skip$/).length;
    await t(name, page.getByTestId(loc), busy);
    await sleep(3500);
    const last = writes(station, /\/queue\/skip$/).slice(-1)[0];
    check(`${tag}: ${name} sends a skip (${fade})`, writes(station, /\/queue\/skip$/).length === n + 1 && (fade === 'hard' ? !last.query.fade || last.query.fade === 'hard' : last.query.fade === fade), JSON.stringify(last?.query));
  }
  // HS and Spots
  await t('HS', page.getByTestId('mode-hs'), `() => document.querySelector('[data-testid=mode-hs]').disabled`);
  check(`${tag}: HS sends the mode change`, writes(station, /\/queue\/modes$/).length === 1);
  await sleep(400);
  await t('Spots', page.getByTestId('mode-spots'), `() => document.querySelector('[data-testid=mode-spots]').disabled`);
  check(`${tag}: Spots sends the mode change`, writes(station, /\/queue\/modes$/).length === 2);
  // Panic: two separate taps
  await sleep(400);
  const panic = page.getByTestId('panic');
  await t('Panic arms', panic, `() => document.querySelector('[data-testid=panic]').textContent.includes('Tap again')`);
  await panic.tap();
  await page.getByTestId('toast').waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: Panic (second tap) sends one panic`, writes(station, /\/queue\/panic$/).length === 1);
  await page.getByTestId('toast').getByRole('button', { name: 'Dismiss' }).tap().catch(() => {});
  // Insert from the library: + on a queue row, search, Insert
  const row = page.getByTestId('queue-row').nth(3);
  const after = await page.getByTestId('queue-row').nth(2).getAttribute('data-entry-id');
  await t('Insert-here opens the library', row.getByTestId('insert-here'), `() => !!document.querySelector('[data-testid=insert-banner]')`);
  const box = page.getByRole('searchbox');
  await box.tap();
  await box.fill('Song number 76');
  await page.waitForFunction(() => document.querySelectorAll('[data-testid=library-row]').length === 1, null, { timeout: 4000 }).catch(() => {});
  const ins = page.getByTestId('library-row').first().getByRole('button', { name: 'Insert' });
  await t('Insert', ins, `() => [...document.querySelectorAll('[data-testid=library-row] button')].some(b => b.textContent === '…')`);
  const insw = writes(station, /\/queue\/insert$/);
  check(`${tag}: Insert puts the song before the chosen row`, insw.length === 1 && insw[0].body.asset_ids?.[0] === 'm76' && insw[0].body.after_entry_id === after, JSON.stringify(insw[0]?.body));
  await page.getByRole('button', { name: 'Done' }).first().tap().catch(() => {});
  // Library + End and pre-listen
  await tab(page, 'Library').tap();
  await box.fill('Song number 75');
  await page.waitForFunction(() => document.querySelectorAll('[data-testid=library-row]').length === 1, null, { timeout: 4000 }).catch(() => {});
  await t('+ End', page.getByTestId('library-row').first().getByRole('button', { name: 'Add to the end of the queue' }), `() => [...document.querySelectorAll('[data-testid=library-row] button')].some(b => b.textContent === '…')`);
  check(`${tag}: + End adds to the queue`, writes(station, /\/queue\/add$/).length === 1);
  await t('Pre-listen', page.getByTestId('library-pfl').first(), `() => !!document.querySelector('[data-testid=pfl-deck]')`);
  check(`${tag}: pre-listen opens the deck`, (await page.getByTestId('pfl-deck').count()) === 1);
  await page.getByTestId('library-pfl').first().tap();
  // Instant buttons
  await tab(page, 'Buttons').tap();
  await t('Instant button', page.getByTestId('hot-button').filter({ hasText: 'Station ID 5' }), `() => !!document.querySelector('[data-testid=hot-button].animate-pulse')`);
  check(`${tag}: an instant button fires (Play now)`, writes(station, /\/queue\/fire-now$/).length === 1);
  // Go live, Played, Schedule, Errors
  await tab(page, 'Go live').tap();
  await page.getByText('Thursday Night Live').first().waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: Go live pane shows the shows`, (await page.getByText('Thursday Night Live').count()) >= 1);
  await tab(page, 'More').tap();
  await page.getByText('Earlier song').waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: Played shows the log`, (await page.getByText('Earlier song').count()) === 1);
  await page.locator('.cr-side button', { hasText: 'Schedule' }).tap();
  await page.getByText('Afternoon music').waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: Schedule shows the block on now`, (await page.getByText('Afternoon music').count()) >= 1);
  await page.locator('.cr-side button', { hasText: 'Errors' }).tap();
  await sleep(300);
  check(`${tag}: Errors opens`, (await page.locator('.cr-side button[aria-pressed=true]', { hasText: 'Errors' }).count()) === 1);
  check(`${tag}: Panic is not shown twice (left column only)`, (await page.getByTestId('panic').count()) === 1);
  for (const [name, ms] of times) check(`${tag}: ${name} shows within 150 ms (S-1, F-LANDSCAPE-6)`, ms >= 0 && ms < 150, `${ms} ms`);
  const m = await measure(page);
  check(`${tag}: still no sideways scroll`, m.docW <= m.vw);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── F-LANDSCAPE-9: typing in the library with the on-screen keyboard up ─────────────
async function keyboard(browser) {
  for (const [name, w, h, kbH] of [['iPhone 14', 844, 390, 200], ['iPhone SE', 667, 375, 200]]) {
    const tag = `keyboard ${name}`;
    const { ctx, page, errors } = await openRoom(browser, phoneCtx(w, h));
    await tab(page, 'Library').tap();
    const box = page.getByRole('searchbox');
    await box.tap();
    // Android shrinks the window to what the keyboard leaves; iOS scrolls the field into view
    await page.setViewportSize({ width: w, height: h - kbH });
    await box.fill('Nigun');
    await page.waitForFunction(() => [...document.querySelectorAll('[data-testid=library-row]')].every((r) => /Nigun/.test(r.textContent)), null, { timeout: 4000 }).catch(() => {});
    await box.evaluate((el) => el.scrollIntoView({ block: 'nearest' }));
    await sleep(200);
    const r = await page.evaluate(() => {
      const vh = innerHeight;
      const s = document.querySelector('input[type=search]').getBoundingClientRect();
      const rows = [...document.querySelectorAll('[data-testid=library-row]')].filter((x) => { const b = x.getBoundingClientRect(); return b.top >= 0 && b.top + 30 <= vh; }).length;
      const tabs = document.querySelector('[aria-label="Control room sections"]');
      return { vh, field: s.top >= 0 && s.bottom <= vh, rows, tabsHidden: !tabs || getComputedStyle(tabs).display === 'none', focused: document.activeElement?.type === 'search' };
    });
    check(`${tag}: with the keyboard up the search field stays on screen`, r.field && r.focused, JSON.stringify(r));
    check(`${tag}: and at least one result shows (the tabs step aside)`, r.rows >= 1 && r.tabsHidden, JSON.stringify(r));
    await shot(page, `keyboard-${name.replace(/\s+/g, '-')}`);
    await box.blur();
    await page.setViewportSize({ width: w, height: h });
    await sleep(200);
    const tabsBack = await page.evaluate(() => getComputedStyle(document.querySelector('[aria-label="Control room sections"]')).display !== 'none');
    check(`${tag}: the tabs come back when the keyboard goes`, tabsBack);
    check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── F-LANDSCAPE-15: upright Android phone with the keyboard open stays upright ─────────────
async function androidKeyboard(browser) {
  const tag = 'Android upright + keyboard';
  const station = makeStation({ queueSize: 20 });
  const { ctx } = await adminContext(browser, phoneCtx(412, 915, { screen: { width: 412, height: 915 } }), station);
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  // The phone's screen stays upright whatever the window does (Chrome on Android)
  await ctx.addInitScript(() => {
    const d = Object.getOwnPropertyDescriptor(ScreenOrientation.prototype, 'type');
    Object.defineProperty(ScreenOrientation.prototype, 'type', { configurable: true, get() { return window.__orient || d.get.call(this); } });
    window.__orient = 'portrait-primary';
  });
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  await tab(page, 'Library').tap();
  const box = page.getByRole('searchbox');
  await box.tap();
  // The keyboard takes ~540 px: the window is now wider than tall
  await page.setViewportSize({ width: 412, height: 375 });
  await sleep(300);
  const r = await page.evaluate(() => ({ land: !!document.querySelector('[data-testid=transport-landscape]'), cls: document.documentElement.classList.contains('cr-land'), focused: document.activeElement?.type === 'search' }));
  check(`${tag}: does not flip into the sideways layout while typing (F-LANDSCAPE-15)`, !r.land && !r.cls && r.focused, JSON.stringify(r));
  await box.fill('Nigun');
  await sleep(300);
  check(`${tag}: the search field keeps the focus and the text`, (await box.inputValue()) === 'Nigun' && await box.evaluate((el) => document.activeElement === el));
  // Really turned sideways (screen landscape): the sideways layout, even while typing
  await page.evaluate(() => { window.__orient = 'landscape-primary'; });
  await page.setViewportSize({ width: 915, height: 412 });
  await page.waitForFunction(() => !!document.querySelector('[data-testid=transport-landscape]'), null, { timeout: 2000 }).catch(() => {});
  check(`${tag}: turned sideways for real, it goes sideways`, (await page.getByTestId('transport-landscape').count()) === 1);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── F-LANDSCAPE-17: pre-listen deck, toast, live show: queue keeps 4 rows, Go next on screen ─────────────
async function extras(browser) {
  for (const [name, w, h] of [['iPhone 14', 844, 390], ['iPhone SE', 667, 375]]) {
    const tag = `extras ${name}`;
    const station = makeStation({ queueSize: 20, shows: [{ id: 'ls1', station_id: 'st-1', title: 'Thursday Night Live', status: 'live', scheduled_start: new Date().toISOString() }] });
    const { ctx, page, errors } = await openRoom(browser, phoneCtx(w, h), station);
    await tab(page, 'Library').tap();
    await page.getByTestId('library-pfl').first().tap();
    await page.getByTestId('pfl-deck').waitFor({ timeout: 3000 }).catch(() => {});
    await tab(page, 'Queue').tap();
    await page.getByTestId('go-next').tap();
    await sleep(300);
    const m = await measure(page);
    const deck = await page.getByTestId('pfl-deck').count();
    check(`${tag}: with the pre-listen deck open and a live show on, at least 4 queue rows (F-LANDSCAPE-17)`, deck === 1 && m.rows >= 4, `deck ${deck}, ${m.rows} rows`);
    check(`${tag}: Go next, Panic, remaining and the tabs still on screen`, m.goNext && m.panic && m.remaining && m.tabs, JSON.stringify(m));
    check(`${tag}: live show link in the transport column`, (await page.locator('[data-testid=transport-landscape] a', { hasText: 'LIVE' }).count()) === 1);
    await shot(page, `extras-${name.replace(/\s+/g, '-')}`);
    check(`${tag}: no sideways scroll, no script errors`, m.docW <= m.vw && errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── F-LANDSCAPE-14: other screens an operator uses sideways ─────────────
async function otherScreens(browser) {
  for (const [name, path, ready] of [
    ['Databank', '/admin/databank', 'table, [role=table], [data-testid=databank]'],
    ['Public Listen page', '/listen/st-1', 'button'],
    ['Host console', '/admin/live/ls1/host', 'button'],
    ['Call screener', '/admin/live/ls1/screen', 'button'],
  ]) {
    const tag = `other screens sideways: ${name}`;
    const { ctx, page, errors } = await openRoom(browser, phoneCtx(844, 390), makeStation({ queueSize: 10 }), { path });
    await page.locator(ready).first().waitFor({ timeout: 6000 }).catch(() => {});
    await sleep(500);
    const m = await page.evaluate(() => ({ docW: document.documentElement.scrollWidth, vw: innerWidth, text: document.body.innerText.length }));
    check(`${tag}: loads and has no sideways scroll`, m.text > 20 && m.docW <= m.vw, JSON.stringify(m));
    check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
    await shot(page, `other-${name.replace(/\s+/g, '-')}`);
    await ctx.close();
  }
}

const browser = await chromium.launch();
const run = (name) => !ONLY || ONLY.split(',').includes(name);
try {
  if (run('layout')) await layout(browser);
  if (run('tall')) await tallTransport(browser);
  if (run('queries')) await queries(browser);
  if (run('rotate')) await rotate(browser);
  if (run('controls')) await controls(browser);
  if (run('keyboard')) await keyboard(browser);
  if (run('android')) await androidKeyboard(browser);
  if (run('extras')) await extras(browser);
  if (run('other')) await otherScreens(browser);
} finally {
  await browser.close();
}
process.exit(done());
