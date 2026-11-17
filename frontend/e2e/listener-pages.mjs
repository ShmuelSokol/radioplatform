/**
 * Browser check for the public listener pages other than the Listen player itself,
 * against the fake backend (e2e/lib/fake-station.mjs), desktop and iPhone size:
 *   - a11y:   accessibility basics on /stations, /listen/:id, /guide, /hosts, /archives,
 *             /call/:id: html lang="en", every button and link has a real accessible
 *             name (an icon or a symbol alone is not one), every form field has a label
 *   - player: the global AudioPlayer bar (playerStore)
 *
 *   cd frontend && npm run build && (npx vite preview --port 4311 --strictPort &)
 *   BASE_URL=http://localhost:4311 node e2e/listener-pages.mjs      # optional: ONLY=<part>
 *
 * Exits non-zero if any check fails. Known site bugs are listed in KNOWN_BUGS and
 * print XFAIL; when one is fixed its check prints XPASS and fails until removed here.
 */
import {
  chromium, devices, checker, sleep, makeStation, routeApi, routeNowPlaying, AUDIO_PROBE, routeSiteSocket,
} from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const ONLY = process.env.ONLY || '';

const KNOWN_BUGS = {};
const { check, done } = checker(KNOWN_BUGS);

/** A fake station that also answers the public pages' reads. */
function publicStation() {
  const station = makeStation({
    extra: (method, path) => {
      if (path === '/users/public/hosts') {
        return { hosts: [
          { id: 'h1', display_name: 'Reb Moshe', title: 'Morning host', bio: 'Shiurim and stories', photo_url: null, social_links: { twitter: 'kolbramah', instagram: 'kolbramah', website: 'https://example.com' } },
          { id: 'h2', display_name: 'Chaim', title: null, bio: null, photo_url: null, social_links: {} },
        ], total: 2 };
      }
      if (path === '/archives' && method === 'GET') {
        return { archives: [
          { id: 'a1', station_id: station.SID, title: 'Motzei Shabbos Live, week 12', description: 'Questions and answers', host_name: 'Reb Moshe', recorded_at: new Date(Date.now() - 86400e3).toISOString(), duration_seconds: 3600, audio_url: '/__tone/m1.wav', cover_image_url: null, is_published: true, download_count: 4, live_show_id: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
          { id: 'a2', station_id: station.SID, title: 'Thursday Night Live', description: null, host_name: null, recorded_at: null, duration_seconds: 1800, audio_url: '/__tone/m2.wav', cover_image_url: null, is_published: true, download_count: 0, live_show_id: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
        ], total: 2 };
      }
      if (/^\/schedules\/epg\//.test(path)) {
        return { station_id: station.SID, date: new Date().toISOString().slice(0, 10), blocks: [
          { id: 'b1', name: 'Morning shiurim', description: 'Daf yomi', start_time: '06:00', end_time: '09:00', playback_mode: 'sequential', schedule_name: 'Weekday' },
          { id: 'b2', name: 'Afternoon music', description: null, start_time: '12:00', end_time: '18:00', playback_mode: 'shuffle', schedule_name: 'Weekday' },
        ] };
      }
      return undefined;
    },
  });
  // A live show with call-ins open, so /call shows its form
  station.live.shows = [{ id: 'ls1', station_id: station.SID, title: 'Thursday Night Live', status: 'live' }];
  return station;
}

async function publicContext(browser, contextOpts, station) {
  const ctx = await browser.newContext(contextOpts);
  await ctx.addInitScript(AUDIO_PROBE);
  await routeApi(ctx, station);
  await routeNowPlaying(ctx, station);
  await routeSiteSocket(ctx);
  return ctx;
}

/**
 * What is wrong on the page as it stands, by the accessibility tree Chromium builds:
 *  - unnamed:  buttons/links/fields whose accessible name is empty or has no letter
 *              or digit (a "★", "✕" or an SVG alone tells a screen reader nothing)
 *  - unlabelled: inputs, selects and text areas with no <label>, aria-label or
 *              aria-labelledby (a placeholder is not a label: it disappears on typing)
 */
async function audit(page) {
  const cdp = await page.context().newCDPSession(page);
  const { nodes } = await cdp.send('Accessibility.getFullAXTree');
  await cdp.detach();
  const ROLES = new Set(['button', 'link', 'textbox', 'combobox', 'slider', 'checkbox', 'spinbutton', 'searchbox', 'radio', 'switch', 'menuitem', 'listbox']);
  const unnamed = nodes
    .filter((n) => !n.ignored && ROLES.has(n.role?.value))
    .filter((n) => !/[\p{L}\p{N}]/u.test(String(n.name?.value ?? '')))
    .map((n) => `${n.role.value} "${n.name?.value ?? ''}"`);
  const unlabelled = await page.evaluate(() => [...document.querySelectorAll('input, select, textarea')]
    .filter((el) => el.type !== 'hidden' && el.getClientRects().length > 0)
    .filter((el) => !(el.labels && el.labels.length) && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby'))
    .map((el) => `${el.tagName.toLowerCase()}${el.type ? `[${el.type}]` : ''} ${el.getAttribute('placeholder') || el.name || ''}`.trim()));
  return { unnamed, unlabelled };
}

async function a11y(browser, contextOpts, tag) {
  const station = publicStation();
  const ctx = await publicContext(browser, contextOpts, station);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const tap = (loc) => (contextOpts.isMobile ? loc.tap() : loc.click());
  const pages = [
    { path: '/stations', ready: () => page.getByText('Kol Berama').first().waitFor() },
    {
      path: `/listen/${station.SID}`,
      ready: async () => { await page.getByText('On Air', { exact: true }).waitFor(); },
      // Open everything a listener can open: the player controls, the song request
      // form, the registration form; then log in for the rating stars and favourite
      more: async () => {
        await tap(page.getByRole('button', { name: 'Listen Live' }));
        await page.getByRole('slider').waitFor({ timeout: 3000 }).catch(() => {});
        await tap(page.getByRole('button', { name: 'Request a Song' }));
        await tap(page.getByRole('button', { name: 'Register' }).first());
        const first = await audit(page);
        await page.getByRole('textbox', { name: /PIN/ }).or(page.getByPlaceholder('Enter 6-digit PIN')).fill('123456');
        await tap(page.getByRole('button', { name: 'Login' }));
        await page.getByText('Welcome, Test Listener!').waitFor({ timeout: 3000 }).catch(() => {});
        return first;
      },
    },
    { path: '/guide', ready: () => page.getByText('Morning shiurim').waitFor() },
    { path: '/hosts', ready: () => page.getByText('Reb Moshe').first().waitFor() },
    { path: '/archives', ready: () => page.getByText('Thursday Night Live').waitFor() },
    { path: `/call/${station.SID}`, ready: () => page.locator('input').first().waitFor() },
  ];
  for (const p of pages) {
    await page.goto(`${BASE}${p.path}`);
    await p.ready().catch(() => {});
    await sleep(300);
    const extra = p.more ? await p.more() : { unnamed: [], unlabelled: [] };
    if (contextOpts.isMobile) {
      // The navbar's menu button and what it opens
      const menu = page.locator('nav button[aria-expanded], nav button.md\\:hidden, nav button').filter({ hasNot: page.locator('text=/\\w{3,}/') }).first();
      if (await menu.count()) await menu.tap().catch(() => {});
      await sleep(200);
    }
    const r = await audit(page);
    const unnamed = [...new Set([...extra.unnamed, ...r.unnamed])];
    const unlabelled = [...new Set([...extra.unlabelled, ...r.unlabelled])];
    check(`${tag} ${p.path.replace(station.SID, ':id')}: every button and link has a name`, unnamed.length === 0, unnamed.join(', '));
    check(`${tag} ${p.path.replace(station.SID, ':id')}: every form field has a label`, unlabelled.length === 0, unlabelled.join(', '));
    const lang = await page.evaluate(() => document.documentElement.lang);
    check(`${tag} ${p.path.replace(station.SID, ':id')}: html lang="en"`, lang === 'en', lang);
  }
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Global AudioPlayer bar (playerStore) ─────────────
// The bar (components/audio/AudioPlayer.tsx, in Layout) shows only once something calls
// usePlayerStore().play(stationId, name, hlsUrl). Nothing in the site does: the public
// Archives page plays with its own Audio element, the Listen page and the admin
// library have their own players. So the check is that the bar is never shown on the
// pages a listener can open (and that playing an archive does not need it).
async function globalPlayer(browser) {
  const tag = 'global player bar';
  const station = publicStation();
  const ctx = await publicContext(browser, { viewport: { width: 1280, height: 900 } }, station);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  const bar = () => page.locator('div.fixed.bottom-0.bg-gray-900').count();
  for (const path of ['/stations', `/listen/${station.SID}`, '/guide', '/hosts', '/archives']) {
    await page.goto(`${BASE}${path}`);
    await sleep(800);
    check(`${tag}: not shown on ${path.replace(station.SID, ':id')} (nothing calls playerStore.play)`, (await bar()) === 0);
  }
  await page.getByText('Thursday Night Live').waitFor();
  await page.getByRole('button', { name: /^Play Motzei Shabbos/ }).click();
  await sleep(800);
  const playing = await page.evaluate(() => window.__audios.filter((a) => !a.paused).map((a) => a.src.split('/').pop()));
  check(`${tag}: an archive plays on the Archives page's own player, no global bar`, playing.length === 1 && playing[0] === 'm1.wav' && (await bar()) === 0, playing.join());
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
const run = (name) => !ONLY || ONLY.split(',').includes(name);
try {
  if (run('a11y')) {
    await a11y(browser, { viewport: { width: 1280, height: 900 } }, 'a11y desktop');
    await a11y(browser, { ...devices['iPhone 13'] }, 'a11y iPhone');
  }
  if (run('player')) await globalPlayer(browser);
} finally {
  await browser.close();
}
process.exit(done());
