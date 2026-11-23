/**
 * Back that returns to where you were (Shmuel 2026-11-19: "if they clicked into spot from
 * main dashboard, back > goes back to dashboard with the last selection they clicked
 * highlighted"). Real backend with made-up stations and clients, Chromium at iPhone 13 and
 * desktop sizes, every step under the look-at-the-screen guard.
 *
 *   cd frontend && BASE_URL=http://localhost:3000 API=http://localhost:8000 \
 *     KBR_ADMIN_EMAIL=... KBR_ADMIN_PASSWORD=... SHOTS_DIR=/tmp/shots node e2e/back-navigation.mjs
 *
 * Needs: the three stations of e2e/walkthrough-seed.py and at least two clients, one with
 * a contract on each station (seed in wiki/back-navigation.md).
 *
 * Checks:
 *  1. Sponsors list -> client -> Back: the list with the same search, the client's row lit.
 *  2. Control room (each station) -> Sponsors tab -> a play -> Sponsor page -> Back: the
 *     control room on the same station with the Sponsor panel open again and the play's
 *     row and the "Sponsor page" link lit. The browser's own back does the same.
 *  3. Another station chosen meanwhile: Back does not open the first station's panel.
 *  4. A client page opened straight from a link: Back goes to the client list (not out
 *     of the site) and lights that client.
 *  5. A client deleted from its own page: Back from the list never returns to it.
 *  6. Library: search and page kept, the item's row lit after Back.
 *  7. A dialog (contract Edit) closes back to the page beneath, same address.
 *  8. The Back line is on every staff page (not the control room) and never makes the
 *     page wider than the phone.
 */
import { chromium, devices } from 'playwright';
import fs from 'node:fs';
import { allowEverywhere, guardPage, guardVerdict } from './lib/guard.mjs';

allowEverywhere(/localhost:4331|ERR_CONNECTION_REFUSED|WaveSurfer/, 'the seed\'s audio files are not served in this test (walkthrough-seed AUDIO_BASE)');

const BASE = process.env.BASE_URL || 'http://localhost:3000';
const API = (process.env.API || 'http://localhost:8000') + '/api/v1';
const SHOTS = process.env.SHOTS_DIR || '';
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });

const login = await (await fetch(`${API}/auth/login`, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ email: process.env.KBR_ADMIN_EMAIL || 'admin', password: process.env.KBR_ADMIN_PASSWORD }),
})).json();
if (!login.access_token) { console.error('Login failed', login); process.exit(2); }
const H = { Authorization: `Bearer ${login.access_token}`, 'content-type': 'application/json' };
const api = async (method, path, body) => {
  const r = await fetch(API + path, { method, headers: H, body: body ? JSON.stringify(body) : undefined });
  return r.ok ? r.json().catch(() => null) : null;
};
const stations = (await api('GET', '/stations')).stations;
const clients = await api('GET', '/sponsors/clients');
const withContract = clients.filter((c) => c.contracts_total > 0);
if (!withContract.length) { console.error('Seed clients with contracts first'); process.exit(2); }

const SIZES = (process.env.SIZES || 'iphone-13,desktop').split(',').map((n) => [n, n === 'desktop' ? { viewport: { width: 1440, height: 900 } } : n === 'iphone-se' ? devices['iPhone SE'] : devices['iPhone 13']]);
const failures = [];
const fail = (msg) => { failures.push(msg); console.log('FAIL', msg); };
const ok = (msg) => console.log('ok  ', msg);

const browser = await chromium.launch();

for (const [size, device] of SIZES) {
  const phone = size !== 'desktop';
  const ctx = await browser.newContext({ ...device, deviceScaleFactor: 1 });
  await ctx.addInitScript(([a, r]) => { localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r); }, [login.access_token, login.refresh_token]);
  const page = await ctx.newPage();
  page.on('dialog', (d) => d.accept()); // "Delete <client>?"
  const guard = guardPage(page, `back ${size}`);
  const shot = async (label) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${size}-${label}.png` }); };
  const width = device.viewport.width;
  const noSideways = async (where) => {
    const w = await page.evaluate(() => document.documentElement.scrollWidth);
    if (w > width + 1) fail(`${size} ${where}: page is ${w}px wide on a ${width}px screen`);
  };
  const backBar = page.getByTestId('nav-back');
  /** Something on the page carries the "you came from here" light. */
  const litText = async () => page.evaluate(() => Array.from(document.querySelectorAll('[data-nav-returned]'))
    .map((el) => (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60)));
  const clearLit = () => page.evaluate(() => document.querySelectorAll('[data-nav-returned]').forEach((el) => el.removeAttribute('data-nav-returned')));
  const waitLit = async (re, what) => {
    for (let i = 0; i < 40; i++) {
      const lit = await litText();
      if (lit.some((t) => re.test(t))) return lit;
      await page.waitForTimeout(150);
    }
    fail(`${size} ${what}: nothing lit matching ${re} (lit: ${JSON.stringify(await litText())})`);
    return [];
  };

  // ── 1. list -> client -> Back ──────────────────────────────────────────
  const target = withContract[0];
  await page.goto(`${BASE}/admin/sponsors`);
  await page.getByTestId('client-list').waitFor();
  await page.getByLabel('Find a client').fill(target.name.slice(0, 4));
  await page.waitForTimeout(300);
  if (phone) await page.evaluate(() => window.scrollTo(0, 200));
  const scrolledFrom = await page.evaluate(() => Math.round(window.scrollY));
  await page.locator(`[data-nav-id="client-${target.id}"]`).first().click();
  await page.waitForURL(new RegExp(`/admin/sponsors/${target.id}`));
  await backBar.waitFor();
  const words1 = (await backBar.innerText()).trim();
  if (!/Back to Clients/.test(words1)) fail(`${size} 1: Back line says "${words1}", wanted "Back to Clients"`);  // the page is "Clients" since PR 435
  await noSideways('client page');
  await shot('1-client-page');
  await backBar.click();
  await page.waitForURL(/\/admin\/sponsors(\?|$)/);
  if (!page.url().includes(`q=${encodeURIComponent(target.name.slice(0, 4))}`) && !page.url().includes(`q=${target.name.slice(0, 4)}`)) fail(`${size} 1: search not kept (${page.url()})`);
  if ((await page.getByLabel('Find a client').inputValue()) !== target.name.slice(0, 4)) fail(`${size} 1: search box empty after Back`);
  await waitLit(new RegExp(target.name), '1 client row');
  const scrollBack = await page.evaluate(() => Math.round(window.scrollY));
  if (Math.abs(scrollBack - scrolledFrom) > 60 && scrolledFrom > 0) fail(`${size} 1: scroll ${scrollBack}, was ${scrolledFrom}`);
  await shot('1-back-to-list');
  ok(`${size} 1: list -> client -> Back keeps search and lights ${target.name}`);
  await guard.expectOnly('1 list and back');

  // ── 2 & 3. control room on each station ───────────────────────────────
  for (const st of stations) {
    await page.evaluate((id) => localStorage.setItem('radio_last_station', id), st.id);
    await page.goto(`${BASE}/admin/dashboard`);
    await page.getByTestId('control-room').waitFor();
    if (await backBar.count()) fail(`${size} 2: the control room shows a Back line`);
    // the Sponsors list of today's plays
    if (phone) {
      await page.getByRole('button', { name: /More/ }).last().click();
      await page.getByRole('button', { name: /^Sponsors$/ }).click();
    } else {
      await page.getByRole('tab', { name: /^Sponsors$/ }).click();
    }
    const row = page.getByTestId('sponsors-today-row').first();
    try { await row.waitFor({ timeout: 8000 }); } catch {
      fail(`${size} 2 ${st.name}: no sponsorship plays listed today (seed?)`);
      await shot(`2-${st.name.replace(/\W+/g, '-')}-no-plays`);
      continue;
    }
    const rowId = await row.getAttribute('data-nav-id');
    const rowWords = (await row.innerText()).replace(/\s+/g, ' ').trim().slice(0, 20);
    await row.click();
    const link = page.getByTestId('link-sponsors');
    await link.waitFor();
    await link.click();
    try { await page.waitForURL(/\/admin\/sponsors(\/|$|\?)/, { timeout: 8000 }); } catch {
      await shot(`2-${st.name.replace(/\W+/g, '-')}-link-stuck`);
      fail(`${size} 2 ${st.name}: "Sponsor page" did not open the client (${page.url()})`);
      continue;
    }
    await backBar.waitFor();
    const w2 = (await backBar.innerText()).trim();
    if (!/Back to Dashboard/.test(w2)) fail(`${size} 2 ${st.name}: Back line says "${w2}"`);
    await shot(`2-${st.name.replace(/\W+/g, '-')}-client`);
    await backBar.click();
    await page.waitForURL(/\/admin\/dashboard/);
    await page.getByTestId('control-room').waitFor();
    await page.getByTestId('link-sponsors').waitFor({ timeout: 5000 }).catch(() => fail(`${size} 2 ${st.name}: Sponsor panel not open again after Back`));
    const lit = await waitLit(/Sponsor page|Sponsors/, `2 ${st.name} sponsor link`);
    const rowLit = await page.locator(`[data-nav-id="${rowId}"][data-nav-returned]`).count();
    if (!rowLit) fail(`${size} 2 ${st.name}: the play's row (${rowWords}) is not lit after Back (lit: ${JSON.stringify(lit)})`);
    await shot(`2-${st.name.replace(/\W+/g, '-')}-back`);
    ok(`${size} 2 ${st.name}: control room -> play -> Sponsor page -> Back reopens the panel, row lit`);

    // the browser's own back does the same
    await page.getByTestId('link-sponsors').click();
    await page.waitForURL(/\/admin\/sponsors(\/|$|\?)/);
    await clearLit();
    await page.goBack();
    await page.waitForURL(/\/admin\/dashboard/);
    await page.getByTestId('link-sponsors').waitFor({ timeout: 5000 }).catch(() => fail(`${size} 2 ${st.name}: browser back did not reopen the panel`));
    await waitLit(/Sponsor page|Sponsors/, `2 ${st.name} browser back`);

    // 3. another station chosen meanwhile
    const other = stations.find((s) => s.id !== st.id);
    await page.getByTestId('link-sponsors').click();
    await page.waitForURL(/\/admin\/sponsors(\/|$|\?)/);
    await page.evaluate((id) => localStorage.setItem('radio_last_station', id), other.id);
    await backBar.click();
    await page.waitForURL(/\/admin\/dashboard/);
    await page.getByTestId('control-room').waitFor();
    await page.waitForTimeout(1200);
    const panelOpen = await page.getByTestId('link-sponsors').count();
    const shownStation = await page.evaluate(() => localStorage.getItem('radio_last_station'));
    if (shownStation === other.id && panelOpen) fail(`${size} 3: ${st.name}'s Sponsor panel opened on ${other.name}`);
    await guard.expectOnly(`2/3 control room ${st.name}`);
  }

  // ── 4. straight from a link ───────────────────────────────────────────
  await page.goto(`${BASE}/admin/sponsors/${target.id}?tab=contracts`);
  await backBar.waitFor();
  const w4 = (await backBar.innerText()).trim();
  if (!/Back to Clients/.test(w4)) fail(`${size} 4: opened from a link, Back says "${w4}"`);
  await backBar.click();
  await page.waitForURL(/\/admin\/sponsors$/);
  await waitLit(new RegExp(target.name), '4 client row after a direct link');
  ok(`${size} 4: direct link -> Back to the client list with the client lit`);
  await guard.expectOnly('4 direct link');

  // ── 7. a dialog closes to the page beneath ────────────────────────────
  await page.goto(`${BASE}/admin/sponsors/${target.id}?tab=contracts`);
  const edit = page.getByTestId('contract-edit').first();
  await edit.waitFor({ timeout: 6000 }).catch(() => {});
  if (await edit.count()) {
    await edit.click();
    const dialog = page.getByRole('dialog').first();
    await dialog.waitFor({ timeout: 4000 }).catch(() => {});
    await shot('7-dialog');
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    if (await page.getByRole('dialog').count()) {
      const close = page.getByRole('button', { name: /Cancel|Close/ }).first();
      if (await close.count()) await close.click();
    }
    if (!page.url().includes(`/admin/sponsors/${target.id}`)) fail(`${size} 7: closing the dialog left the client page`);
    if (await page.getByRole('dialog').count()) fail(`${size} 7: the dialog did not close`);
    ok(`${size} 7: contract Edit closes back to the client page`);
  } else fail(`${size} 7: no Edit button on the contracts tab`);
  await guard.expectOnly('7 dialog');

  // ── 5. deleted client ────────────────────────────────────────────────
  const doomed = await api('POST', '/sponsors', { name: `Delete me ${size}` });
  await page.goto(`${BASE}/admin/sponsors`);
  await page.getByTestId('client-list').waitFor();
  await page.locator(`[data-nav-id="client-${doomed.id}"]`).first().click();
  await page.waitForURL(new RegExp(doomed.id));
  // the Details tab, a tap like a person (a new address in the same tab)
  await page.getByRole('tab', { name: /^Details/ }).click();
  const del = page.getByRole('button', { name: /Delete client/ }).first();
  await del.waitFor({ timeout: 5000 }).catch(() => {});
  if (await del.count()) {
    await del.click();
    await page.waitForURL(/\/admin\/sponsors$/, { timeout: 6000 }).catch(() => fail(`${size} 5: delete did not go to the list`));
    await backBar.waitFor();
    const w5 = (await backBar.innerText()).trim();
    if (/Delete me/.test(w5)) fail(`${size} 5: Back offers the deleted client`);
    await backBar.click();
    await page.waitForTimeout(800);
    if (page.url().includes(doomed.id)) fail(`${size} 5: Back went to the deleted client (${page.url()})`);
    // the browser's back must step over it too
    await page.goto(`${BASE}/admin/sponsors`);
    ok(`${size} 5: Back after a delete skips the deleted client ("${w5}")`);
  } else fail(`${size} 5: no Delete button on the client's details`);
  await guard.expectOnly('5 delete', [/404|not found/i]);

  // ── 6. library ───────────────────────────────────────────────────────
  await page.goto(`${BASE}/admin/assets`);
  const title = page.getByPlaceholder(/title/i).first();
  await title.waitFor();
  await title.fill('a');
  await page.waitForTimeout(800);
  const itemLink = page.locator('main a[href^="/admin/assets/"]:not([href$="upload"])').first();
  if (await itemLink.count()) {
    const href = await itemLink.getAttribute('href');
    const itemWords = (await itemLink.evaluate((a) => (a.closest('tr, li, [data-nav-id]') || a).textContent || '')).replace(/\s+/g, ' ').trim().slice(0, 20);
    await itemLink.click();
    await page.waitForURL(new RegExp(href));
    await backBar.waitFor();
    if (!/Back to Library/.test(await backBar.innerText())) fail(`${size} 6: Back says "${await backBar.innerText()}"`);
    await backBar.click();
    await page.waitForURL(/\/admin\/assets$/);
    if ((await page.getByPlaceholder(/title/i).first().inputValue()) !== 'a') fail(`${size} 6: library search not kept after Back`);
    await waitLit(new RegExp(itemWords.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')), '6 library row');
    ok(`${size} 6: library search kept and item lit`);
  } else fail(`${size} 6: no library item links`);
  await guard.expectOnly('6 library');

  // ── 8. Back line on staff pages, no sideways scroll ───────────────────
  for (const p of ['/admin/sponsors', '/admin/spot-schedule', '/admin/campaigns', '/admin/assets', '/admin/schedules', '/admin/databank', '/admin/program-blocks', '/admin/stations']) {
    await page.goto(BASE + p);
    await backBar.waitFor({ timeout: 6000 }).catch(() => fail(`${size} 8: no Back line on ${p}`));
    await page.waitForTimeout(400);
    await noSideways(p);
  }
  await shot('8-spot-schedule');
  await guard.expectOnly('8 pages');
  await ctx.close();
}

await browser.close();
const unexpected = guardVerdict();
if (unexpected.length) fail(`guard: ${unexpected.length} unexpected finding(s)`);
console.log(failures.length ? `\n${failures.length} FAILED` : '\nALL PASSED');
process.exit(failures.length ? 1 : 0);
