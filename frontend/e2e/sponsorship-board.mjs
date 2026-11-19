import { chromium, devices } from 'playwright';
import { mkdirSync } from 'fs';
/**
 * The Sponsorship board against a real backend: the week grid (desktop) and one day as a
 * list (iPhone), tapping a slot shows who is booked, Book into this slot opens the booking
 * window on that slot, Break settings opens, no sideways scroll, no page errors.
 *   BASE_URL=http://localhost:3000 API=http://localhost:8000 KBR_ADMIN_PASSWORD=... node e2e/sponsorship-board.mjs
 */
const BASE = process.env.BASE_URL || 'http://localhost:3000', API = `${process.env.API || 'http://localhost:8000'}/api/v1`;
const OUT = process.env.SHOTS_DIR || '/tmp/sponsorship-board'; mkdirSync(OUT, { recursive: true });
const login = await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: process.env.KBR_ADMIN_EMAIL || 'admin', password: process.env.KBR_ADMIN_PASSWORD }) })).json();
const H = { Authorization: `Bearer ${login.access_token}` };
const stations = (await (await fetch(`${API}/stations`, { headers: H })).json()).stations;
// The station with the most bookings this week
let station = stations[0], most = -1;
for (const s of stations) {
  const room = await (await fetch(`${API}/spot-schedules/room?station_id=${s.id}&days=7`, { headers: H })).json();
  const n = room.cells.reduce((a, c) => a + c.booked, 0);
  if (n > most) { most = n; station = s; }
}
// A client with a spot ready to air, for the booking window
let withSpot = null;
for (const c of (await (await fetch(`${API}/sponsors/clients`, { headers: H })).json())) {
  const acc = await (await fetch(`${API}/sponsors/${c.id}/account`, { headers: H })).json();
  if ((acc.spots ?? []).some((x) => x.asset_id && x.stage !== 'rejected' && !/stand-?in/i.test(x.title))) { withSpot = c; break; }
}
if (!withSpot) { console.log('no client with a spot ready to air'); process.exit(1); }
const fails = []; const ok = (c, m) => { if (!c) { fails.push(m); console.log('FAIL', m); } };
const b = await chromium.launch();
for (const [name, dev] of [['desktop', { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 }], ['iphone', devices['iPhone 13']]]) {
  const ctx = await b.newContext(dev);
  await ctx.addInitScript(([a, r]) => { localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r); }, [login.access_token, login.refresh_token]);
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', (e) => errs.push(String(e))); p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  await p.goto(`${BASE}/admin/sponsorship-board?station=${station.id}`);
  const where = p.getByTestId(name === 'desktop' ? 'board-grid' : 'board-day');
  await where.waitFor();
  await p.waitForTimeout(400);
  await p.screenshot({ path: `${OUT}/${name}-board.png` });
  const cells = where.locator('[data-cell]');
  ok(await cells.count() >= (name === 'desktop' ? 42 : 6), `${name}: ${await cells.count()} slots shown`);
  // a booked slot
  let target = null;
  for (const c of await cells.all()) { if (/^[1-9]\d* of \d+|^[1-9]/.test((await c.textContent()).replace(/^.*?(AM|PM)/, '').trim())) { target = c; break; } }
  target = target ?? cells.first();
  await target.click();
  const detail = p.getByTestId('board-detail').filter({ visible: true });
  await detail.waitFor();
  await p.screenshot({ path: `${OUT}/${name}-detail.png`, fullPage: name === 'desktop' });
  if (most > 0) ok(await detail.getByRole('link').count() > 0, `${name}: no client in the tapped slot`);
  const book = detail.getByRole('button', { name: 'Book into this slot' });
  if (await book.count()) {
    await book.click();
    const dlg = p.getByTestId('book-sponsorship'); await dlg.waitFor();
    // through to the time slots: the tapped slot is ticked and the usual week (Sunday to Friday) is chosen
    const slotKey = (await target.getAttribute('data-cell')).split('|')[1];
    await dlg.getByLabel('Client').selectOption(withSpot.id);
    const spot = dlg.getByTestId('step-spot').locator('button[aria-pressed]').first();
    await spot.waitFor(); await spot.click();
    await dlg.getByRole('button', { name: 'Next ›' }).click();
    await dlg.getByTestId('step-days').waitFor();
    const days = await dlg.getByTestId('step-days').locator('button[aria-pressed=true]').allTextContents();
    ok(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sun'].every((d) => days.includes(d)), `${name}: days chosen are ${days}`);
    ok(await dlg.getByLabel('Station').inputValue() === String(station.id), `${name}: the board's station is not chosen`);
    await dlg.getByRole('button', { name: 'Next ›' }).click();
    await dlg.getByTestId('step-slots').waitFor();
    const ticked = await dlg.locator('[data-slot][aria-pressed=true]').evaluateAll((els) => els.map((e) => e.dataset.slot));
    ok(ticked.length === 1 && ticked[0] === slotKey, `${name}: slots ticked ${ticked}, tapped ${slotKey}`);
    // a click beside the window keeps the half-made booking; Esc closes it
    await p.mouse.click(2, 2);
    ok(await dlg.isVisible(), `${name}: a click beside the window closed a half-made booking`);
    await p.keyboard.press('Escape');
    ok(await dlg.waitFor({ state: 'hidden', timeout: 3000 }).then(() => true, () => false), `${name}: Esc did not close the window`);
  }
  await p.getByRole('button', { name: 'Break settings' }).click();
  ok(await p.getByTestId('break-settings').waitFor({ timeout: 5000 }).then(() => true, () => false), `${name}: break settings did not open`);
  ok(!(await p.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)), `${name}: sideways scroll`);
  if (name === 'iphone') {
    await p.getByRole('button', { name: 'Next day' }).click();
    ok(await p.getByTestId('board-day').locator('[data-cell]').count() >= 6, 'iphone: next day empty');
  }
  // Arriving from the menu on another page, the Back line names that page
  await p.goto(`${BASE}/admin/alerts`);
  await p.getByRole('heading', { level: 1 }).first().waitFor();
  if (name === 'iphone') await p.getByRole('button', { name: /menu/i }).first().click();
  else await p.getByRole('button', { name: /^Sponsors/ }).first().click();
  await p.getByRole('link', { name: 'Sponsorship board' }).first().click();
  await p.getByTestId(name === 'desktop' ? 'board-grid' : 'board-day').waitFor();
  const backLine = (await p.getByRole('link', { name: /Back to/ }).or(p.getByRole('button', { name: /Back to/ })).first().textContent()).trim();
  ok(/Alerts/.test(backLine), `${name}: Back line after coming from Alerts says "${backLine}"`);
  ok(!errs.length, `${name}: page errors ${errs}`);
  await ctx.close();
}
await b.close(); console.log(fails.length ? fails : 'all ok'); process.exit(fails.length ? 1 : 0);
