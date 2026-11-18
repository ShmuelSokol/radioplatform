/**
 * Browser check of three display findings from the screenshot walk-through (2026-11-18),
 * against a fake backend (no real station is touched):
 *   1. Library: a spot row's Type cell showed "spot..." and a clipped sponsor search box.
 *   2. Library: a long category chip such as do_not_play ran into the Type column.
 *   3. Live Shows: the New Show dialog did not close with Esc.
 * Looked at on desktop 1366x900, a narrow laptop 1024x768 and iPhone 13.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4331 --strictPort &)
 *   BASE_URL=http://localhost:4331 node e2e/library-display.mjs
 *
 * Exits non-zero if a check fails.
 */
import { chromium, devices, checker, sleep, makeStation, adminContext, newPage } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4331';
const SHOTS = process.env.SHOTS || '';
const SIZES = [
  ['desktop 1366x900', { viewport: { width: 1366, height: 900 } }, true],
  ['laptop 1024x768', { viewport: { width: 1024, height: 768 } }, true],
  ['iPhone 13', { ...devices['iPhone 13'] }, false],
];

const { check, done } = checker({});

const asset = (id, title, asset_type, category, sponsor) => ({
  id, title, artist: 'Artist', album: null, duration: 31, file_path: `${id}.mp3`, album_art_path: null,
  metadata_extra: {}, created_by: null, asset_type, category, created_at: '2026-11-01T00:00:00Z',
  last_played_at: null, release_date: null, sponsor_id: sponsor ? sponsor.id : null, sponsor_name: sponsor ? sponsor.name : null,
});
const SPONSORS = [
  { id: 'sp1', name: 'Lakewood Family Dental and Orthodontics Center' },
  { id: 'sp2', name: 'Glatt Bite' },
  { id: 'sp3', name: 'Seforim World' },
].map((s) => ({ ...s, length_seconds: 30, priority: 1, audio_file_path: '', target_rules: null, insertion_policy: 'between_tracks', created_at: '', updated_at: '' }));
const CATEGORIES = ['lively', 'relax', 'do_not_play', 'a_very_long_category_name_for_checking'].map((name, i) => ({ id: `c${i}`, name }));
const ASSETS = [
  asset('a1', 'Spot with no sponsor yet', 'spot', null, null),
  asset('a2', 'Spot with a long sponsor name', 'spot', 'sponsorships', SPONSORS[0]),
  asset('a3', 'Song that must not play', 'music', 'do_not_play', null),
  asset('a4', 'Song in a very long category', 'music', 'a_very_long_category_name_for_checking', null),
  asset('a5', 'Announcement', 'announcement', 'lively', null),
  asset('a6', 'Last row spot', 'spot', 'do_not_play', SPONSORS[1]),
];

async function wire(ctx, patches) {
  const json = (route, body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await ctx.route(/\/api\/v1\/assets(\?.*)?$/, (route) => (route.request().method() === 'GET' ? json(route, { assets: ASSETS, total: ASSETS.length }) : route.fallback()));
  await ctx.route(/\/api\/v1\/categories(\?.*)?$/, (route) => (route.request().method() === 'GET' ? json(route, CATEGORIES) : route.fallback()));
  await ctx.route(/\/api\/v1\/sponsors(\?.*)?$/, (route) => (route.request().method() === 'GET' ? json(route, SPONSORS) : route.fallback()));
  await ctx.route(/\/api\/v1\/assets\/a\d$/, (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback();
    const id = route.request().url().split('/').pop();
    const body = route.request().postDataJSON();
    patches.push({ id, body });
    const a = ASSETS.find((x) => x.id === id);
    return json(route, { ...a, ...body });
  });
}

/** Rectangles of a row's cells and what is inside them, measured in the page. */
const measureRow = (page, title) => page.evaluate((t) => {
  const row = [...document.querySelectorAll('tbody tr')].find((r) => r.textContent.includes(t));
  if (!row) return null;
  const rect = (el) => { const r = el.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height }; };
  const cells = [...row.children].filter((c) => c.getBoundingClientRect().width > 0);
  const out = { cells: [] };
  for (const c of cells) {
    // what is really seen of each thing: cut by any box up to the cell that hides overflow
    const seen = (e) => { const r = rect(e);
      for (let a = e.parentElement; a && a !== c.parentElement; a = a.parentElement) {
        if (getComputedStyle(a).overflowX !== 'visible') { const ar = a.getBoundingClientRect(); r.l = Math.max(r.l, ar.left); r.r = Math.min(r.r, ar.right); }
      }
      return r; };
    const inside = [...c.querySelectorAll('button, span, a, input')].filter((e) => e.getBoundingClientRect().width > 0)
      .map((e) => ({ text: (e.textContent || '').trim().slice(0, 60), tag: e.tagName, clippedText: e.scrollWidth > e.clientWidth + 1, hasTitle: !!(e.getAttribute('title') || e.closest('[title]')), ...seen(e) }));
    out.cells.push({ ...rect(c), text: c.textContent.trim().slice(0, 80), inside });
  }
  return out;
}, title);

const spills = (row) => {
  const bad = [];
  for (const c of row.cells) for (const e of c.inside) {
    if (e.l < c.l - 1 || e.r > c.r + 1) bad.push(`"${e.text}" ${Math.round(e.l)}..${Math.round(e.r)} outside its cell ${Math.round(c.l)}..${Math.round(c.r)}`);
  }
  return bad;
};

const menuBox = (page) => page.evaluate(() => {
  const m = document.querySelector('[data-floating-menu]');
  if (!m) return null;
  const r = m.getBoundingClientRect();
  // Is every corner of the menu really on top (nothing covers or clips it)?
  const pts = [[r.left + 3, r.top + 3], [r.right - 3, r.top + 3], [r.left + 3, r.bottom - 3], [r.right - 3, r.bottom - 3], [r.left + r.width / 2, r.top + r.height / 2]];
  const onTop = pts.every(([x, y]) => { const el = document.elementFromPoint(x, y); return el && m.contains(el); });
  return { l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height, onTop, vw: window.innerWidth, vh: window.innerHeight };
});

const pageOverflow = (page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

const browser = await chromium.launch();
try {
  for (const [name, opts, wide] of SIZES) {
    const station = makeStation();
    const patches = [];
    const { ctx } = await adminContext(browser, opts, station);
    await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
    await wire(ctx, patches);
    const { page, errors } = await newPage(ctx);

    // ── Library ──────────────────────────────────────────────────────────────
    await page.goto(`${BASE}/admin/assets`);
    await page.getByText('Spot with no sponsor yet').first().waitFor({ timeout: 15000 });
    await sleep(300);
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/library-${name.replace(/\W+/g, '-')}.png`, fullPage: true });
    check(`${name}: Library does not scroll sideways`, (await pageOverflow(page)) <= 1, `overflow ${await pageOverflow(page)} px`);

    const full = opts.viewport.width >= 1280; // below that the chips may be cut with "..." (full name on hover)
    if (wide) {
      for (const a of ASSETS) {
        const row = await measureRow(page, a.title);
        const bad = row ? spills(row) : ['row not found'];
        check(`${name}: nothing in the "${a.title}" row runs out of its column`, bad.length === 0, bad.join('; '));
      }
      // 1. the Type cell says "spot" in full, with the sponsor chip under it
      const r1 = await measureRow(page, 'Spot with no sponsor yet');
      const typeCell = r1.cells.find((c) => c.inside.some((e) => e.text === 'spot'));
      const typeText = typeCell?.inside.find((e) => e.text === 'spot');
      check(`${name}: spot row shows the type "spot" in full`, !!typeText && !typeText.clippedText, typeText ? `clipped=${typeText.clippedText}` : 'no "spot" text found');
      const chip = typeCell?.inside.find((e) => e.text === 'Set sponsor');
      check(`${name}: "Set sponsor" is shown in full under the type`, !!chip && (!chip.clippedText || !full) && chip.t >= typeText.b - 1, chip ? `clipped=${chip.clippedText}` : 'no chip');

      // a long sponsor name is cut with "..." inside the cell, with the full name on hover
      const r2 = await measureRow(page, 'Spot with a long sponsor name');
      const longChip = r2.cells.flatMap((c) => c.inside).find((e) => e.tag === 'BUTTON' && e.text.startsWith('Lakewood Family'));
      check(`${name}: a long sponsor name stays in the Type column and has the full name on hover`, !!longChip && longChip.hasTitle);

      // 2. the long category chips stay left of the Type column
      for (const t of ['Song that must not play', 'Song in a very long category']) {
        const r = await measureRow(page, t);
        const ci = r.cells.findIndex((c) => c.inside.some((e) => e.tag === 'BUTTON' && /do_not_play|a_very_long/.test(e.text)));
        const cat = r.cells[ci]?.inside.find((e) => e.tag === 'BUTTON');
        const next = r.cells[ci + 1];
        check(`${name}: category chip of "${t}" ends before the Type column`, !!cat && !!next && cat.r <= next.l + 1, cat && next ? `chip ends ${Math.round(cat.r)}, Type starts ${Math.round(next.l)}` : 'not found');
      }
      const dnp = (await measureRow(page, 'Song that must not play')).cells.flatMap((c) => c.inside).find((e) => e.tag === 'BUTTON' && e.text.startsWith('do_not_play'));
      check(`${name}: "do_not_play" is readable in full`, !!dnp && (!dnp.clippedText || (!full && dnp.hasTitle)));

      // the sponsor search box opens whole, on top, inside the window (first and last row)
      for (const t of ['Spot with no sponsor yet', 'Last row spot']) {
        const row = page.locator('tbody tr', { hasText: t });
        await row.getByRole('button', { name: t === 'Last row spot' ? 'Glatt Bite' : 'Set sponsor' }).click();
        await page.getByPlaceholder('Search sponsors...').waitFor({ timeout: 3000 });
        await sleep(150);
        const m = await menuBox(page);
        check(`${name}: sponsor search box of "${t}" is whole and inside the window`, !!m && m.onTop && m.w >= 180 && m.l >= 0 && m.t >= 0 && m.r <= m.vw && m.b <= m.vh, JSON.stringify(m));
        const input = await page.getByPlaceholder('Search sponsors...').boundingBox();
        check(`${name}: the search field of "${t}" is wide enough to type in`, !!input && input.width >= 150, `width ${input?.width}`);
        if (SHOTS && t === 'Spot with no sponsor yet') await page.screenshot({ path: `${SHOTS}/library-sponsor-menu-${name.replace(/\W+/g, '-')}.png` });
        if (t === 'Spot with no sponsor yet') {
          await page.getByPlaceholder('Search sponsors...').fill('glatt');
          await page.locator('[data-floating-menu]').getByRole('button', { name: 'Glatt Bite' }).click();
          await sleep(200);
          check(`${name}: picking a sponsor from the box saves it`, patches.some((p) => p.id === 'a1' && p.body.sponsor_id === 'sp2'), JSON.stringify(patches));
        } else {
          await page.keyboard.press('Escape');
          await page.mouse.click(5, 5);
          await sleep(150);
        }
        check(`${name}: the sponsor box of "${t}" closes afterwards`, (await page.locator('[data-floating-menu]').count()) === 0);
      }

      // the category menu opens whole too, and still saves
      await page.locator('tbody tr', { hasText: 'Song that must not play' }).getByRole('button', { name: 'do_not_play' }).click();
      await page.locator('[data-floating-menu]').waitFor({ timeout: 3000 });
      await sleep(150);
      const cm = await menuBox(page);
      check(`${name}: category menu is whole and inside the window`, !!cm && cm.onTop && cm.l >= 0 && cm.t >= 0 && cm.r <= cm.vw && cm.b <= cm.vh, JSON.stringify(cm));
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/library-category-menu-${name.replace(/\W+/g, '-')}.png` });
      await page.locator('[data-floating-menu]').getByRole('button', { name: 'relax', exact: true }).click();
      await sleep(200);
      check(`${name}: picking a category from the menu saves it`, patches.some((p) => p.id === 'a3' && p.body.category === 'relax'), JSON.stringify(patches));
      check(`${name}: the category menu closes afterwards`, (await page.locator('[data-floating-menu]').count()) === 0);
    }

    // ── 3. Live Shows: Esc closes the New Show dialog ─────────────────────────
    await page.goto(`${BASE}/admin/live`);
    const newShow = page.getByRole('button', { name: '+ New Show' });
    await newShow.waitFor({ timeout: 15000 });
    await newShow.click();
    const heading = page.getByRole('heading', { name: 'Create Live Show' });
    await heading.waitFor({ timeout: 3000 });
    const box = await page.getByRole('dialog').boundingBox();
    const vp = page.viewportSize();
    check(`${name}: the New Show dialog fits the window`, !!box && box.x >= 0 && box.x + box.width <= vp.width + 1, JSON.stringify(box));
    await page.keyboard.press('Escape');
    await sleep(200);
    check(`${name}: Esc closes the New Show dialog`, (await heading.count()) === 0);
    // Esc while typing in a field of the dialog closes it too
    await newShow.click();
    await heading.waitFor({ timeout: 3000 });
    await page.getByRole('dialog').locator('input').first().click();
    await page.keyboard.press('Escape');
    await sleep(200);
    check(`${name}: Esc closes the New Show dialog from inside a field`, (await heading.count()) === 0);
    check(`${name}: Live Shows does not scroll sideways`, (await pageOverflow(page)) <= 1);
    check(`${name}: no page errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
} finally {
  await browser.close();
}
process.exit(done());
