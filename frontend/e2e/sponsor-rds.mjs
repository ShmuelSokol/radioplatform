import { chromium, devices } from 'playwright';
import { mkdirSync } from 'fs';
/**
 * A sponsor's radio text (RDS) from the sponsor screens, against a real backend: the client
 * page's badge and Radio text tab (add, change, switch off, remove), the client list's
 * column and the booking window's line. Desktop and iPhone. Leaves no line behind.
 *   BASE_URL=http://localhost:3000 API=http://localhost:8000 KBR_ADMIN_PASSWORD=... node e2e/sponsor-rds.mjs
 */
const BASE = process.env.BASE_URL || 'http://localhost:3000', API = `${process.env.API || 'http://localhost:8000'}/api/v1`;
const OUT = process.env.SHOTS_DIR || '/tmp/sponsor-rds'; mkdirSync(OUT, { recursive: true });
const login = await (await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: process.env.KBR_ADMIN_EMAIL || 'admin', password: process.env.KBR_ADMIN_PASSWORD }) })).json();
const H = { Authorization: `Bearer ${login.access_token}` };
const clients = await (await fetch(`${API}/sponsors/clients`, { headers: H })).json();
const lines = (await (await fetch(`${API}/rds/messages`, { headers: H })).json()).messages;
// a client with a spot ready and no radio text yet
let client = null;
for (const c of clients) {
  if (lines.some((m) => m.sponsor_id === c.id)) continue;
  const acc = await (await fetch(`${API}/sponsors/${c.id}/account`, { headers: H })).json();
  if ((acc.spots ?? []).some((x) => x.asset_id && x.stage !== 'rejected' && !/stand-?in/i.test(x.title))) { client = c; break; }
}
if (!client) { console.log('no client with a spot and no radio text'); process.exit(1); }
const fails = []; const ok = (c, m) => { if (!c) { fails.push(m); console.log('FAIL', m); } };
const b = await chromium.launch();
try {
  for (const [name, dev] of [['desktop', { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 }], ['iphone', devices['iPhone 13']]]) {
    const ctx = await b.newContext(dev);
    await ctx.addInitScript(([a, r]) => { localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r); }, [login.access_token, login.refresh_token]);
    const p = await ctx.newPage();
    p.on('dialog', (d) => d.accept());
    const errs = []; p.on('pageerror', (e) => errs.push(String(e))); p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
    await p.goto(`${BASE}/admin/sponsors/${client.id}`);
    const badge = p.getByTestId('rds-badge').first();
    await badge.waitFor();
    ok((await badge.textContent()) === 'RDS not set', `${name}: badge at the start says ${await badge.textContent()}`);
    await badge.click();
    const box = p.getByTestId('sponsor-rds'); await box.waitFor();
    await p.screenshot({ path: `${OUT}/${name}-not-set.png`, fullPage: true });
    await box.getByTestId('rds-add').click();
    const input = box.getByLabel('Text on the radio');
    ok((await input.inputValue()).startsWith("Kol B'ramah sponsor"), `${name}: no starting text`);
    // Hebrew alone is refused, with the reason on screen
    await input.fill('שלום');
    await box.getByRole('button', { name: 'Save radio text' }).click();
    ok(await box.getByRole('alert').waitFor({ timeout: 5000 }).then(() => true, () => false), `${name}: Hebrew-only text gave no message`);
    const text = `Kol B'ramah sponsor ${client.name}`.slice(0, 64);
    await input.fill(text);
    await box.getByRole('button', { name: 'Save radio text' }).click();
    await box.getByTestId('rds-line').waitFor();
    ok((await box.getByTestId('rds-line').textContent()).includes(text), `${name}: saved line not shown`);
    ok((await p.getByTestId('rds-badge').first().textContent()) === 'RDS set', `${name}: badge after saving`);
    await p.screenshot({ path: `${OUT}/${name}-set.png`, fullPage: true });
    ok(!(await p.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)), `${name}: sideways scroll`);
    // the client list
    await p.goto(`${BASE}/admin/sponsors`);
    const row = p.locator('tr', { hasText: client.name }).first(); await row.waitFor();
    ok(await row.getByTestId('rds-badge').filter({ hasText: 'RDS set' }).waitFor({ timeout: 5000 }).then(() => true, () => false), `${name}: client list does not show RDS set`);
    // the booking window's last step
    await p.goto(`${BASE}/admin/sponsors?book=1&sponsor=${client.id}`);
    const dlg = p.getByTestId('book-sponsorship'); await dlg.waitFor();
    const spot = dlg.getByTestId('step-spot').locator('button[aria-pressed]').first(); await spot.waitFor(); await spot.click();
    for (let i = 0; i < 3; i++) await dlg.getByRole('button', { name: 'Next ›' }).click();
    const line = dlg.getByTestId('booking-rds'); await line.waitFor();
    ok((await line.textContent()).includes(text), `${name}: booking window says "${await line.textContent()}"`);
    await p.screenshot({ path: `${OUT}/${name}-booking.png` });
    await p.keyboard.press('Escape');
    // switch off = not set; then remove
    await p.goto(`${BASE}/admin/sponsors/${client.id}?tab=rds`);
    await p.getByTestId('rds-line').waitFor();
    await p.getByRole('button', { name: 'Switch off' }).click();
    ok(await p.getByTestId('rds-badge').filter({ hasText: 'RDS not set' }).first().waitFor({ timeout: 5000 }).then(() => true, () => false), `${name}: switched off still reads set`);
    await p.getByRole('button', { name: 'Remove' }).click();
    ok(await p.getByTestId('rds-line').waitFor({ state: 'detached', timeout: 5000 }).then(() => true, () => false), `${name}: line not removed`);
    ok(!errs.filter((e) => !/422/.test(e)).length, `${name}: page errors ${errs}`);
    await ctx.close();
  }
} finally {
  for (const m of (await (await fetch(`${API}/rds/messages`, { headers: H })).json()).messages) {
    if (m.sponsor_id === client.id) await fetch(`${API}/rds/messages/${m.id}`, { method: 'DELETE', headers: H });
  }
  await b.close();
}
console.log(fails.length ? fails : 'all ok'); process.exit(fails.length ? 1 : 0);
