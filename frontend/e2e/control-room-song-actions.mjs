/**
 * Browser check of the control room's quick song actions (the "Song" panel on the
 * song on air and on every queue row) against a fake backend: change category, do
 * not play / allow again, edit details, open in the Library, view only for a DJ.
 * Desktop and iPhone sizes, upright and sideways.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4311 --strictPort &)
 *   BASE_URL=http://localhost:4311 node e2e/control-room-song-actions.mjs
 *
 * Exits non-zero if a check fails.
 */
import { chromium, devices, checker, sleep, makeStation, adminContext, newPage } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const SHOTS = process.env.SHOTS || '';
const DESKTOP = { viewport: { width: 1440, height: 900 } };
const PHONE = { ...devices['iPhone 13'] };
const PHONE_SE = { ...devices['iPhone SE'] };
const PHONE_LAND = { ...devices['iPhone 13 landscape'], screen: { width: 844, height: 390 } };
const { check, done } = checker({});

const CATEGORIES = ['Top 500 Hits', 'relax', 'lively', 'kids', 'do_not_play'];

/** A fake station whose library answers the song panel's calls and remembers changes. */
function songStation(opts = {}) {
  let station;
  const extra = (method, path, _query, body) => {
    if (path === '/auth/me' && opts.role) return { id: 'u1', email: 'dj@example.com', role: opts.role, is_active: true };
    if (path === '/categories') return CATEGORIES.map((name, i) => ({ id: `c${i}`, name, created_at: null }));
    const m = /^\/assets\/([^/]+)(\/do-not-play)?$/.exec(path);
    if (!m || m[1] === 'upload') return undefined;
    const a = station.byId[m[1]];
    if (!a) return { __status: 404, detail: 'Not found' };
    const full = () => ({ album: null, file_path: 'x.mp3', album_art_path: null, metadata_extra: null, created_by: null, created_at: null, last_played_at: null, vote: 0, release_date: null, sponsor_id: null, sponsor_name: null, ...a });
    if (m[2] && method === 'POST') {
      if (opts.failDnp) return { __status: 500 };
      let removed = 0, kept = 0;
      if (body.on) {
        if (a.category !== 'do_not_play') { a.before = a.category; a.category = 'do_not_play'; }
        station.queue = station.queue.filter((e) => {
          if (e.asset_id !== a.id || e.status === 'played') return true;
          if (e.status === 'playing' || e.preempt_at) { kept++; return true; }
          removed++; return false;
        });
      } else if (a.category === 'do_not_play') { a.category = a.before ?? null; }
      return { id: a.id, category: a.category, do_not_play: a.category === 'do_not_play', removed_from_queue: removed, still_to_play: kept };
    }
    if (method === 'PATCH') {
      if (opts.failPatch) return { __status: 403, detail: 'Only managers can change the library' };
      Object.assign(a, body);
      return full();
    }
    if (method === 'GET') return full();
    return undefined;
  };
  station = makeStation({ queueSize: 12, ...opts, extra });
  return station;
}

async function openRoom(browser, contextOpts, station) {
  const { ctx } = await adminContext(browser, contextOpts, station, {});
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  return { ctx, page, errors };
}
const rowOf = (page, entryId) => page.locator(`[data-testid=queue-row][data-entry-id="${entryId}"]`);
const panel = (page) => page.getByTestId('song-panel');
const toastText = (page) => page.getByTestId('toast').innerText().catch(() => '');
const writes = (st, re) => st.calls.filter((c) => c.method !== 'GET' && re.test(c.path));
const shot = async (page, name) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` }); };
/** Calls that would change what is on air or the queue order: the panel must make none. */
const airCalls = (st) => writes(st, /\/queue\/(skip|fire-now|play-next|insert|panic|start|loop|mix-over|move-up|move-down|reorder-dnd)/);

async function openForRow(page, entryId, touch) {
  if (touch) {
    await rowOf(page, entryId).locator('[draggable=true]').tap();
    await page.getByTestId('row-song-touch').tap();
  } else {
    await rowOf(page, entryId).locator('[draggable=true]').hover();
    await rowOf(page, entryId).getByTestId('row-song').click();
  }
  await panel(page).waitFor();
}

// ── Desktop: the song on air ─────────────────────────────────
async function onAir(browser) {
  const tag = 'on air';
  const station = songStation();
  const { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
  const cur = station.playing();
  const a = station.byId[cur.asset_id];
  // a second copy of the song on air waits further down, and a timed copy
  station.add({ asset_id: a.id, position: 50, source: 'auto' });
  station.add({ asset_id: a.id, position: 51, source: 'block', preempt_at: new Date(Date.now() + 3600e3).toISOString() });
  await page.getByTestId('now-song').click();
  await panel(page).waitFor();
  check(`${tag}: Song button beside the song on air opens the panel`, await panel(page).isVisible());
  check(`${tag}: panel names the song and says ON AIR`, (await page.getByTestId('song-name').innerText()).includes(a.title) && await page.getByTestId('song-on-air').isVisible());
  check(`${tag}: current category is selected`, await page.getByTestId('song-category').inputValue() === a.category);
  await shot(page, 'desktop-song-panel');
  const before = cur.started_at;
  const t0 = Date.now();
  await page.getByTestId('song-dnp').click();
  await page.getByTestId('song-state').filter({ hasText: 'DO NOT PLAY' }).waitFor({ timeout: 4000 });
  check(`${tag}: do not play shows in the panel within 1 s`, Date.now() - t0 < 1000, `${Date.now() - t0} ms`);
  check(`${tag}: one call to do-not-play with on:true`, writes(station, /do-not-play$/).length === 1 && writes(station, /do-not-play$/)[0].body.on === true);
  check(`${tag}: the song stays on air, same start`, station.playing()?.id === cur.id && station.playing().started_at === before);
  check(`${tag}: no Next, fire, insert or move was sent`, airCalls(station).length === 0);
  const t = await toastText(page);
  check(`${tag}: toast says what was removed and what still plays`, /Will not play again/.test(t) && /1 waiting copy removed/.test(t) && /2 already on air, loaded or timed/.test(t), t);
  check(`${tag}: waiting copy left the queue, timed copy stayed`, station.queue.filter((e) => e.asset_id === a.id).length === 2);
  check(`${tag}: button now offers to allow it again`, /Allow it to play again/.test(await page.getByTestId('song-dnp').innerText()));
  await sleep(700);
  check(`${tag}: transport type box shows do_not_play after the refresh`, (await page.locator('[data-testid=control-room] header').innerText()).includes('do_not_play'));
  await shot(page, 'desktop-song-panel-dnp');
  await page.getByTestId('song-dnp').click();
  await page.getByTestId('song-state').filter({ hasText: a.before }).waitFor({ timeout: 4000 });
  check(`${tag}: allow again restores the old category`, a.category === a.before && /Allowed again, back in/.test(await toastText(page)));
  // Escape closes, and does not reach the page's own Escape handling badly
  await page.keyboard.press('Escape');
  check(`${tag}: Escape closes the panel`, await panel(page).count() === 0);
  // clicking the row on air opens it too
  await rowOf(page, cur.id).locator('div.qn-row-cur').click();
  await panel(page).waitFor();
  check(`${tag}: clicking the row on air opens the panel`, await page.getByTestId('song-on-air').isVisible());
  await page.getByTestId('song-panel-backdrop').click({ position: { x: 5, y: 5 } });
  check(`${tag}: clicking outside closes the panel`, await panel(page).count() === 0);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Desktop: a queue row ─────────────────────────────────────
async function queueRow(browser) {
  const tag = 'queue row';
  const station = songStation();
  const { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
  const p = station.pending();
  const target = p[3];
  const a = station.byId[target.asset_id];
  const order = station.pending().map((e) => e.id).join();
  await openForRow(page, target.id, false);
  check(`${tag}: row's Song button opens the panel, not ON AIR`, (await page.getByTestId('song-name').innerText()).includes(a.title) && await page.getByTestId('song-on-air').count() === 0);
  check(`${tag}: opening the panel does not select or move the row`, station.pending().map((e) => e.id).join() === order && await page.getByTestId('row-actions').count() === 0);
  // category
  const t0 = Date.now();
  await page.getByTestId('song-category').selectOption('relax');
  await page.getByTestId('toast').filter({ hasText: 'Category is now' }).waitFor({ timeout: 4000 });
  check(`${tag}: category change saved in one PATCH within 1 s`, a.category === 'relax' && writes(station, new RegExp(`/assets/${a.id}$`)).length === 1 && Date.now() - t0 < 1000, `${Date.now() - t0} ms`);
  await sleep(700);
  check(`${tag}: the row shows the new category`, (await rowOf(page, target.id).innerText()).includes('relax'));
  check(`${tag}: the row keeps its place after a category change`, station.pending().map((e) => e.id).join() === order);
  await page.getByTestId('song-category').selectOption('');
  await page.getByTestId('toast').filter({ hasText: 'Category cleared' }).waitFor({ timeout: 4000 });
  check(`${tag}: "No category" clears it`, a.category === null);
  // details
  check(`${tag}: Save is off until something changes`, await page.getByTestId('song-save').isDisabled());
  await page.getByTestId('song-title').fill('  Fixed title ');
  await page.getByTestId('song-artist').fill('New Artist');
  await page.getByTestId('song-vote').fill('abc');
  check(`${tag}: a rating that is not a number is refused on screen`, await page.getByTestId('song-vote-error').isVisible() && await page.getByTestId('song-save').isDisabled());
  await page.getByTestId('song-vote').fill('101');
  check(`${tag}: a rating over 100 is refused on screen`, await page.getByTestId('song-save').isDisabled());
  await page.getByTestId('song-vote').fill('7');
  await page.getByTestId('song-title').press('n');
  check(`${tag}: typing N in a field does not press Next`, writes(station, /\/queue\/skip/).length === 0);
  await page.getByTestId('song-title').fill('  Fixed title ');
  await page.getByTestId('song-save').click();
  await page.getByTestId('toast').filter({ hasText: 'Song details saved' }).waitFor({ timeout: 4000 });
  check(`${tag}: details saved trimmed`, a.title === 'Fixed title' && a.artist === 'New Artist' && a.vote === 7);
  await sleep(700);
  check(`${tag}: the row shows the new title`, (await rowOf(page, target.id).innerText()).includes('Fixed title'));
  await page.getByTestId('song-title').fill('   ');
  check(`${tag}: an empty title cannot be saved`, await page.getByTestId('song-save').isDisabled());
  await page.getByTestId('song-title').fill('Fixed title');
  // do not play on a waiting row: it leaves the queue, the panel stays
  station.add({ asset_id: a.id, position: 60, source: 'auto' });
  await page.getByTestId('song-dnp').click();
  await page.getByTestId('song-state').filter({ hasText: 'DO NOT PLAY' }).waitFor({ timeout: 4000 });
  check(`${tag}: both waiting copies removed`, !station.queue.some((e) => e.asset_id === a.id) && /2 waiting copies removed\./.test(await toastText(page)), await toastText(page));
  await sleep(700);
  check(`${tag}: the row is gone from the list, the panel is still open`, await rowOf(page, target.id).count() === 0 && await panel(page).isVisible());
  check(`${tag}: the song on air was not touched`, airCalls(station).length === 0 && station.playing()?.id === 'q1');
  // library link
  check(`${tag}: Library link points at the song's page`, await page.getByTestId('song-library').getAttribute('href') === `/admin/assets/${a.id}`);
  await page.getByTestId('song-library').click();
  await page.waitForURL(`**/admin/assets/${a.id}`);
  check(`${tag}: Library link opens the song's page`, true);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Failures and view only ───────────────────────────────────
async function refusals(browser) {
  const tag = 'refusals';
  let station = songStation({ failDnp: true, failPatch: true });
  let { ctx, page, errors } = await openRoom(browser, DESKTOP, station);
  const target = station.pending()[2];
  const a = station.byId[target.asset_id];
  const was = a.category;
  await openForRow(page, target.id, false);
  await page.getByTestId('song-dnp').click();
  await page.getByTestId('toast').filter({ hasText: 'did not go through' }).waitFor({ timeout: 4000 });
  check(`${tag}: a failed do-not-play says so and changes nothing`, a.category === was && /Do not play this song again/.test(await page.getByTestId('song-dnp').innerText()));
  await page.getByTestId('song-category').selectOption('kids');
  await page.getByTestId('toast').filter({ hasText: 'Only managers' }).waitFor({ timeout: 4000 });
  await sleep(500);
  check(`${tag}: a refused category change shows the server's reason and snaps back`, a.category === was && await page.getByTestId('song-category').inputValue() === was);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();

  station = songStation({ role: 'dj' });
  ({ ctx, page, errors } = await openRoom(browser, DESKTOP, station));
  await page.getByTestId('now-song').click();
  await panel(page).waitFor();
  check(`${tag}: a DJ sees "View only"`, await page.getByTestId('song-view-only').isVisible());
  check(`${tag}: a DJ cannot change category, block or save`, await page.getByTestId('song-category').isDisabled() && await page.getByTestId('song-dnp').isDisabled() && await page.getByTestId('song-title').isDisabled() && await page.getByTestId('song-save').isDisabled());
  check(`${tag}: a DJ can still open the Library page`, await page.getByTestId('song-library').isVisible());
  check(`${tag}: no writes from a DJ's panel`, writes(station, /\/assets\//).length === 0);
  await ctx.close();

  // Silence and a stopped station have no Song button
  station = songStation({ stopped: true });
  ({ ctx, page, errors } = await openRoom(browser, DESKTOP, station));
  check(`${tag}: no Song button in the transport when nothing is on air`, await page.getByTestId('now-song').count() === 0);
  await ctx.close();
}

// ── Findings from the live walk-through (2026-11-18): buttons a mouse could not reach ──
async function reach(browser) {
  for (const [w, h] of [[1366, 900], [1366, 768], [1280, 720]]) {
    const tag = `reach ${w}x${h}`;
    const station = songStation();
    const { ctx, page, errors } = await openRoom(browser, { viewport: { width: w, height: h } }, station);
    const p = station.pending();
    // A click selects the row; its Song / Move up / Move down are then pressable without hovering first
    const row = rowOf(page, p[3].id);
    await row.locator('[draggable=true]').click({ position: { x: 300, y: 8 } });
    await page.mouse.move(5, 5);
    const quick = row.getByTestId('row-quick');
    check(`${tag}: a selected row shows Song, Move up, Move down, Remove without hover`, await quick.getByRole('button').count() === 4 && await quick.isVisible() && (await quick.evaluate((el) => getComputedStyle(el).pointerEvents)) !== 'none');
    await quick.getByRole('button', { name: 'Move up' }).click({ timeout: 3000 });
    await sleep(400);
    check(`${tag}: Move up on the selected row works by plain click`, station.pending()[2].id === p[3].id);
    await rowOf(page, p[3].id).getByTestId('row-quick').getByRole('button', { name: 'Move down' }).click({ timeout: 3000 });
    await sleep(400);
    check(`${tag}: Move down on the selected row works by plain click`, station.pending()[3].id === p[3].id);
    await rowOf(page, p[3].id).getByTestId('row-song').click({ timeout: 3000 });
    await panel(page).waitFor();
    check(`${tag}: Song on the selected row opens its panel by plain click`, (await page.getByTestId('song-name').innerText()).includes(station.byId[p[3].asset_id].title));
    await page.keyboard.press('Escape');
    // the playing row always shows its Song button
    await page.mouse.move(5, 5);
    const curSong = rowOf(page, station.playing().id).getByTestId('row-song');
    check(`${tag}: the row on air shows Song without hover`, await curSong.isVisible());
    await curSong.click({ timeout: 3000 });
    await panel(page).waitFor();
    check(`${tag}: Song on the row on air opens the ON AIR panel`, await page.getByTestId('song-on-air').isVisible());
    await page.keyboard.press('Escape');
    // an unselected row: a plain click on its Song button still lands (the mouse is over the row by then)
    await page.mouse.move(5, 5);
    await rowOf(page, p[6].id).getByTestId('row-song').click({ timeout: 3000 });
    await panel(page).waitFor();
    check(`${tag}: Song on an unselected row can be pressed with a mouse`, (await page.getByTestId('song-name').innerText()).includes(station.byId[p[6].asset_id].title));
    await page.keyboard.press('Escape');
    await page.mouse.move(5, 5);
    // an unselected row still keeps them out of the way
    check(`${tag}: an unselected, unhovered row hides its buttons`, (await rowOf(page, p[5].id).getByTestId('row-quick').evaluate((el) => getComputedStyle(el).opacity)) === '0');
    // Right-hand column: scrolling to its last tab never moves Loop
    const aside = page.locator('aside[aria-label=Controls]');
    const loop = aside.getByTestId('loop');
    const before = await loop.boundingBox();
    await aside.getByRole('tab').last().scrollIntoViewIfNeeded();
    await aside.getByRole('tab').last().click();
    const after = await loop.boundingBox();
    const ab = await aside.boundingBox();
    const hit = await loop.evaluate((el) => { const r = el.getBoundingClientRect(); const top = document.elementFromPoint(r.left + r.width / 2, r.top + 2); return top === el || el.contains(top); });
    check(`${tag}: Loop is fully visible at the top of the column after going to the last tab`, !!after && after.y === before.y && after.y >= ab.y && hit, JSON.stringify({ before, after, ab }));
    for (const id of ['go-next', 'next-fade', 'next-soft']) {
      const el = aside.getByTestId(id);
      const b = await el.boundingBox();
      const onTop = await el.evaluate((n) => { const r = n.getBoundingClientRect(); const t = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return t === n || n.contains(t); });
      check(`${tag}: ${id} stays in place and uncovered`, !!b && b.y >= ab.y && b.y + b.height <= ab.y + ab.height + 0.5 && onTop, JSON.stringify(b));
    }
    await aside.evaluate((el) => { el.scrollTop = 0; });
    const hs = await aside.getByRole('button', { name: /^HS/ }).boundingBox();
    check(`${tag}: HS is below the pinned buttons, not under them, at the top of the scroll`, !!hs && hs.y >= after.y + 4 * 32, JSON.stringify(hs));
    check(`${tag}: the last tab (Errors) can still be reached and pressed`, await aside.getByRole('tab').last().getAttribute('aria-selected') === 'true');
    check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── A queue time on another day: the day name stays on its own row ──
async function dayName(browser) {
  for (const [name, opts] of [['desktop 1366x900', { viewport: { width: 1366, height: 900 } }], ['desktop 1024x768', { viewport: { width: 1024, height: 768 } }], ['iPhone 13', PHONE], ['iPhone SE', PHONE_SE], ['iPhone 13 sideways', PHONE_LAND]]) {
    const tag = `day name ${name}`;
    const station = songStation({ queueSize: 700 });
    const { ctx, page, errors } = await openRoom(browser, opts, station);
    const pick = page.getByLabel('Show day');
    await pick.dispatchEvent('pointerdown');
    await page.waitForFunction(() => document.querySelector('select[aria-label="Show day"]').options.length >= 3, null, { timeout: 8000 });
    await pick.selectOption({ index: 2 });
    await sleep(500);
    const rows = await page.locator('[data-testid=queue-row]').evaluateAll((els) => els.slice(0, 12).map((row) => {
      const t = row.querySelector('[data-testid=air-time]');
      const rr = row.getBoundingClientRect();
      const range = document.createRange(); range.selectNodeContents(t);
      const tr = range.getBoundingClientRect();
      let sib = t.nextElementSibling; while (sib && sib.getBoundingClientRect().width === 0) sib = sib.nextElementSibling;
      const next = sib.getBoundingClientRect();
      return { text: t.innerText.replace(/\s+/g, ' '), inRow: tr.top >= rr.top - 0.5 && tr.bottom <= rr.bottom + 0.5, clear: tr.right <= next.left + 0.5, cut: t.scrollWidth > t.clientWidth + 1 };
    }));
    check(`${tag}: rows of another day show the day name`, rows.length > 3 && rows.every((r) => /(Sun|Mon|Tue|Wed|Thu|Fri|Sat)/.test(r.text)), JSON.stringify(rows[0]));
    check(`${tag}: time and day name stay inside their own row`, rows.every((r) => r.inRow), JSON.stringify(rows.filter((r) => !r.inRow)[0]));
    check(`${tag}: time and day name do not run into the next column or get cut`, rows.every((r) => r.clear && !r.cut), JSON.stringify(rows.filter((r) => !r.clear || r.cut)[0]));
    await shot(page, `day-name-${name.replace(/\W+/g, '-')}`);
    check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
    await ctx.close();
  }
}

// ── Phones ───────────────────────────────────────────────────
async function phone(browser, contextOpts, tag, landscape = false) {
  const station = songStation();
  const { ctx, page, errors } = await openRoom(browser, contextOpts, station);
  const vp = page.viewportSize();
  const now = page.getByTestId('now-song');
  const nb = await now.boundingBox();
  check(`${tag}: Song button on the song on air is on screen and wide enough to tap`, !!nb && nb.width >= 44 && nb.x + nb.width <= vp.width && nb.height >= 44, JSON.stringify(nb));
  await now.tap();
  await panel(page).waitFor();
  const box = await panel(page).boundingBox();
  check(`${tag}: panel fits the screen`, box.x >= 0 && box.x + box.width <= vp.width + 0.5 && box.y >= 0 && box.y + box.height <= vp.height + 0.5, JSON.stringify(box));
  check(`${tag}: page does not scroll sideways`, await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  for (const id of ['song-category', 'song-dnp', 'song-save', 'song-library']) {
    const el = page.getByTestId(id);
    await el.scrollIntoViewIfNeeded();
    const b = await el.boundingBox();
    check(`${tag}: ${id} is at least 44 px tall and inside the panel`, b.height >= 43.5 && b.x >= box.x && b.x + b.width <= box.x + box.width + 0.5, JSON.stringify(b));
  }
  const fs = await page.getByTestId('song-title').evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  check(`${tag}: fields are 16 px text (no zoom on focus in Safari)`, fs >= 16, String(fs));
  await page.getByTestId('song-dnp').scrollIntoViewIfNeeded();
  await shot(page, `${tag.replace(/\W+/g, '-')}-panel`);
  await page.getByTestId('song-dnp').tap();
  await page.getByTestId('song-state').filter({ hasText: 'DO NOT PLAY' }).waitFor({ timeout: 4000 });
  check(`${tag}: do not play works by tap, song stays on air`, station.playing()?.id === 'q1' && airCalls(station).length === 0);
  await page.getByRole('button', { name: 'Close' }).tap();
  check(`${tag}: Close closes`, await panel(page).count() === 0);
  if (!landscape) {
    // queue row: tap the row, then Song in its action bar
    const target = station.pending()[2];
    await rowOf(page, target.id).locator('[draggable=true]').tap();
    const bar = page.getByTestId('row-actions');
    const bb = await bar.boundingBox();
    const kids = await bar.locator('button').evaluateAll((els) => els.map((el) => { const r = el.getBoundingClientRect(); return { t: el.textContent.trim(), l: r.left, r: r.right, h: r.height, w: r.width, sw: el.scrollWidth, cw: el.clientWidth }; }));
    check(`${tag}: action bar holds Insert, ▲, ▼, Song, Remove on one line inside the screen`, kids.length === 5 && kids.every((k) => k.l >= bb.x - 0.5 && k.r <= vp.width + 0.5 && k.h >= 43.5 && k.w >= 43.5), JSON.stringify(kids));
    check(`${tag}: no action bar label is cut off`, kids.every((k) => k.sw <= k.cw + 1), JSON.stringify(kids));
    await shot(page, `${tag.replace(/\W+/g, '-')}-row-bar`);
    await page.getByTestId('row-song-touch').tap();
    await panel(page).waitFor();
    check(`${tag}: Song in the row's bar opens that song`, (await page.getByTestId('song-name').innerText()).includes(station.byId[target.asset_id].title));
    await page.getByTestId('song-category').selectOption('kids');
    await page.getByTestId('toast').filter({ hasText: 'Category is now' }).waitFor({ timeout: 4000 });
    check(`${tag}: category change by tap`, station.byId[target.asset_id].category === 'kids');
  }
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
const ONLY = process.env.ONLY || '';
const run = (name) => !ONLY || ONLY.split(',').includes(name);
try {
  if (run('air')) await onAir(browser);
  if (run('row')) await queueRow(browser);
  if (run('refusals')) await refusals(browser);
  if (run('reach')) await reach(browser);
  if (run('day')) await dayName(browser);
  if (run('phone')) {
    await phone(browser, PHONE, 'iPhone 13');
    await phone(browser, PHONE_SE, 'iPhone SE');
    await phone(browser, PHONE_LAND, 'iPhone 13 sideways', true);
  }
} finally {
  await browser.close();
}
process.exit(done());
