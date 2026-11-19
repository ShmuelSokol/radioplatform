/**
 * Staff pages against a fake backend (every /api/v1 call is answered here; nothing real
 * is touched): the Sponsors menu per role, the global-rule "used by" line, Show Archives
 * at iPhone width, the archive "recorded" time with the browser in another time zone, and
 * the archive dialog's keyboard basics.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4302 --strictPort &)
 *   BASE_URL=http://localhost:4302 node e2e/staff-pages.mjs
 *
 * Checklist: V3-83, F-RULES-5, F-ARCHIVES-PHONE, V2-108 (and the archive part of V2-107,
 * V2-101). Exits non-zero if a check fails.
 */
import { chromium, checker, sleep } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4302';
const { check, done } = checker({});

const NY = 'America/New_York';
const STATIONS = [
  { id: 's-fm', name: 'Station FM', type: 'internet', timezone: NY, is_active: true, protected: false, automation_config: {} },
  { id: 's-web', name: 'Station Web', type: 'internet', timezone: NY, is_active: true, protected: false, automation_config: {} },
  { id: 's-two', name: 'Station Two', type: 'internet', timezone: NY, is_active: true, protected: false, automation_config: {} },
];
const user = (role) => ({ id: `u-${role}`, email: `${role}@t.test`, role, display_name: role, is_active: true, station_ids: null });

const rule = (id, extra) => ({
  id, name: id, description: null, rule_type: 'rotation', asset_type: 'music', category: null,
  hour_start: 0, hour_end: 24, days_of_week: '0,1,2,3,4,5,6', interval_minutes: null, songs_between: null,
  priority: 10, is_active: true, constraints: null, station_id: null, ...extra,
});

const archive = (extra) => ({
  id: 'a-1', station_id: 's-fm', title: 'Motzei Shabbos Show', description: null, host_name: 'Host',
  recorded_at: '2026-11-19T06:30:00Z', duration_seconds: 3600, audio_url: 'https://example.com/a.mp3',
  cover_image_url: null, is_published: true, download_count: 0, live_show_id: null,
  created_at: '2026-11-18T23:30:00Z', updated_at: '2026-11-18T23:30:00Z', ...extra,
});

function generic(path) {
  if (path.startsWith('/stations')) return { stations: STATIONS, total: STATIONS.length };
  if (path.includes('count')) return { count: 0 };
  if (path.startsWith('/site-release')) return { mode: 'auto', push: null };
  return [];
}

/** A signed-in context; `api(method, path, body)` may answer, else a generic empty answer. */
async function context(browser, role, api = () => undefined, opts = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, ...opts });
  await ctx.addInitScript(() => {
    localStorage.setItem('access_token', 'a');
    localStorage.setItem('refresh_token', 'r');
  });
  const calls = [];
  await ctx.routeWebSocket(/\/ws\//, () => {});
  await ctx.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname.replace(/^.*\/api\/v1/, '');
    let body = null;
    try { body = req.postDataJSON(); } catch { /* none */ }
    calls.push({ method: req.method(), path, body });
    const out = path === '/auth/me' ? { status: 200, body: user(role) } : await api(req.method(), path, body);
    const ans = out === undefined ? { status: 200, body: generic(path) } : out;
    return route.fulfill({ status: ans.status, contentType: 'application/json', body: JSON.stringify(ans.body) });
  });
  return { ctx, calls };
}

// ---- V3-83: the Sponsors menu per role ------------------------------------------------
const ALL_SPONSOR_LINKS = ['All Clients', 'Spot Schedule', 'Record Spots', 'Campaigns', 'Sponsorship Rules'];
const EXPECT_MENU = {
  admin: ALL_SPONSOR_LINKS,
  manager: ALL_SPONSOR_LINKS,
  dj: ['Campaigns'],      // access.ts: /admin/campaigns is open to all staff (read only below manager)
  viewer: ['Campaigns'],
};

async function sponsorsMenu(browser) {
  for (const [role, want] of Object.entries(EXPECT_MENU)) {
    const { ctx } = await context(browser, role);
    const page = await ctx.newPage();
    await page.goto(`${BASE}/admin/rules`);
    const nav = page.locator('nav');
    const button = nav.getByRole('button', { name: /^Sponsors/ });
    await button.waitFor({ timeout: 8000 }).catch(() => {});
    const groups = (await nav.locator('div.hidden.md\\:flex > div.relative > button').allInnerTexts()).map((s) => s.trim());
    check(`V3-83 ${role}: Sponsors is its own top menu, first after Dashboard`, groups[0] === 'Sponsors', groups.join(' | '));
    await button.click().catch(() => {});
    const links = (await nav.locator('div.hidden.md\\:flex div.absolute a').allInnerTexts()).map((s) => s.trim());
    check(`V3-83 ${role}: Sponsors menu shows exactly ${want.join(', ')}`, JSON.stringify(links) === JSON.stringify(want), links.join(' | '));
    if (want.includes('Campaigns')) {
      await nav.getByRole('link', { name: 'Campaigns' }).click();
      await page.waitForURL(/\/admin\/campaigns$/, { timeout: 4000 }).catch(() => {});
      check(`V3-83 ${role}: Campaigns opens the campaigns page`, new URL(page.url()).pathname === '/admin/campaigns', page.url());
    }
    await ctx.close();
  }
  // sponsor: no staff menu at all, and the client list is not reachable by address
  const { ctx } = await context(browser, 'sponsor');
  const page = await ctx.newPage();
  await page.goto(`${BASE}/sponsor/dashboard`);
  await sleep(1200);
  check('V3-83 sponsor: no Sponsors staff menu', (await page.locator('nav').getByRole('button', { name: /^Sponsors/ }).count()) === 0);
  await page.goto(`${BASE}/admin/sponsors`);
  await sleep(1200);
  check('V3-83 sponsor: /admin/sponsors does not open the client list', new URL(page.url()).pathname !== '/admin/sponsors'
    || !(await page.getByText('All Clients').first().isVisible().catch(() => false)), page.url());
  await ctx.close();
}

// ---- F-RULES-5: which stations a global rule is used by --------------------------------
async function rulesUse(browser) {
  const cases = [
    ['every station', [rule('Global A')], /^global, used by every station$/, false],
    ['some stations', [rule('Global A'), rule('Own FM', { station_id: 's-fm' })], /^global, used by Station Web, Station Two only$/, false],
    ['no station', [rule('Global A'), ...STATIONS.map((s) => rule(`Own ${s.id}`, { station_id: s.id }))],
      /^global, not used by any station \(each has rules of its own\)$/, true],
  ];
  for (const [tag, rules, want, amber] of cases) {
    const { ctx } = await context(browser, 'admin', (m, path) => (path === '/rules' ? { status: 200, body: { rules, total: rules.length } } : undefined));
    const page = await ctx.newPage();
    await page.goto(`${BASE}/admin/rules`);
    const cell = page.locator('tr', { hasText: 'Global A' }).getByTestId('global-rule-use');
    await cell.waitFor({ timeout: 8000 }).catch(() => {});
    const text = (await cell.innerText().catch(() => '')).replace(/\s+/g, ' ').trim();
    check(`F-RULES-5 ${tag}: "${want.source}"`, want.test(text), text);
    const isAmber = await cell.locator('span.text-amber-400').count();
    check(`F-RULES-5 ${tag}: amber only when no station uses it`, (isAmber > 0) === amber, `amber spans: ${isAmber}`);
    await ctx.close();
  }
  // an inactive global rule says nothing about stations
  const rules = [rule('Global Off', { is_active: false })];
  const { ctx } = await context(browser, 'admin', (m, path) => (path === '/rules' ? { status: 200, body: { rules, total: 1 } } : undefined));
  const page = await ctx.newPage();
  await page.goto(`${BASE}/admin/rules`);
  const cell = page.locator('tr', { hasText: 'Global Off' }).getByTestId('global-rule-use');
  await cell.waitFor({ timeout: 8000 }).catch(() => {});
  const text = (await cell.innerText().catch(() => '')).trim();
  check('F-RULES-5 inactive global rule says only "global"', text === 'global', text);
  await ctx.close();
}

// ---- F-ARCHIVES-PHONE, V2-108, V2-107 (archives), V2-101 (archive dialog) ---------------
async function archives(browser) {
  // iPhone width: "+ New Archive" fully on screen, page does not scroll sideways
  {
    const { ctx } = await context(browser, 'admin', (m, path) => (path === '/archives' ? { status: 200, body: { archives: [archive()], total: 1 } } : undefined),
      { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 3 });
    const page = await ctx.newPage();
    await page.goto(`${BASE}/admin/archives`);
    const btn = page.getByRole('button', { name: '+ New Archive' });
    await btn.waitFor({ timeout: 8000 });
    const box = await btn.boundingBox();
    const vw = await page.evaluate(() => window.innerWidth);
    check('F-ARCHIVES-PHONE "+ New Archive" fully inside a 390 px screen', !!box && box.x >= 0 && box.x + box.width <= vw, JSON.stringify(box));
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check('F-ARCHIVES-PHONE no sideways page scroll', sideways <= 1, `${sideways}px`);
    await btn.click();
    const dlg = page.getByRole('dialog', { name: 'New Archive' });
    const shown = await dlg.waitFor({ timeout: 3000 }).then(() => true).catch(() => false);
    check('F-ARCHIVES-PHONE the button opens the New Archive dialog on the phone', shown);
    await ctx.close();
  }

  // Browser in Los Angeles, station in New York: the box means the station's clock
  let patched = null; let posted = null;
  const { ctx, calls } = await context(browser, 'admin', (m, path, body) => {
    if (m === 'GET' && path === '/archives') return { status: 200, body: { archives: [archive()], total: 1 } };
    if (m === 'POST' && path === '/archives') { posted = body; return { status: 201, body: archive({ id: 'a-2', ...body }) }; }
    if (m === 'PATCH' && path === '/archives/a-1') { patched = body; return { status: 200, body: archive(body) }; }
    return undefined;
  }, { timezoneId: 'America/Los_Angeles' });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/admin/archives`);
  const row = page.locator('tr', { hasText: 'Motzei Shabbos Show' });
  await row.waitFor({ timeout: 8000 });
  // 06:30Z is 01:30 on Nov 19 in New York but 22:30 on Nov 18 in LA: listed on the station's date
  check('V2-107 archive list shows the recorded date on the station clock', /11\/19\/2026/.test(await row.innerText()), await row.innerText());

  // Edit and save without touching anything: the recorded time is not sent, so it cannot move
  await row.getByRole('button', { name: 'Edit' }).click();
  const edit = page.getByRole('dialog', { name: 'Edit Archive' });
  await edit.waitFor({ timeout: 3000 });
  await edit.getByRole('button', { name: 'Update' }).click();
  for (let i = 0; i < 30 && !patched; i++) await sleep(100);
  check('V2-108 saving an untouched archive sends a PATCH', !!patched);
  check('V2-108 that PATCH does not rewrite recorded_at (no drift by the viewer\'s offset)', !!patched && !('recorded_at' in patched), JSON.stringify(patched));

  // Esc closes the dialog (V2-101)
  await page.getByRole('button', { name: '+ New Archive' }).click();
  const create = page.getByRole('dialog', { name: 'New Archive' });
  await create.waitFor({ timeout: 3000 });
  await page.keyboard.press('Escape');
  const gone = await create.waitFor({ state: 'detached', timeout: 2000 }).then(() => true).catch(() => false);
  check('V2-101 Esc closes the New Archive dialog', gone);
  await page.getByRole('button', { name: '+ New Archive' }).click();
  await create.waitFor({ timeout: 3000 });
  check('V2-101 the dialog\'s × button is named "Close"', (await create.getByRole('button', { name: 'Close' }).count()) === 1);

  // Create: 20:00 typed for a New York station from an LA browser means 01:00Z next day
  await create.locator('select').first().selectOption('s-fm');
  await create.getByPlaceholder('Show title').fill('Night Show');
  await create.getByPlaceholder('https://example.com/audio.mp3').fill('https://example.com/n.mp3');
  await create.locator('input[type="datetime-local"]').fill('2026-11-18T20:00');
  await create.getByRole('button', { name: 'Create' }).click();
  for (let i = 0; i < 30 && !posted; i++) await sleep(100);
  const at = posted?.recorded_at ? new Date(posted.recorded_at).toISOString() : null;
  check('V2-107 recorded 20:00 on a New York station from an LA browser is stored as 01:00Z', at === '2026-11-19T01:00:00.000Z', String(posted?.recorded_at));
  check('V2-108 no stray archive writes', calls.filter((c) => c.method !== 'GET' && c.path.startsWith('/archives')).length === 2);
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await sponsorsMenu(browser);
  await rulesUse(browser);
  await archives(browser);
} finally {
  await browser.close();
}
process.exit(done());
