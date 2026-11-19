/**
 * Start times to the second, on each station's own clock (Shmuel 2026-11-19: "in the
 * played view ... I want to be able to see the seconds, like what time exactly it
 * started"). Checks the control room's Played list and the RDS page's "what radios
 * showed" list on all three stations, on a desktop and on iPhones, and that the time
 * never gets cut off or wraps on a narrow phone.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4341 --strictPort &)
 *   BASE_URL=http://localhost:4341 node e2e/played-seconds.mjs   # optional: SHOTS_DIR
 *
 * Exits non-zero if a check fails.
 */
import fs from 'node:fs';
import { chromium, devices, checker, sleep, makeStation, adminContext, newPage } from './lib/fake-station.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4341';
const SHOTS = process.env.SHOTS_DIR || '';
const { check, done } = checker({});

const STATIONS = [
  { id: 'st-main', name: "Kol B'ramah Main", timezone: 'America/New_York', is_active: true, channels: [] },
  { id: 'st-stories', name: "Kol B'ramah - All Day Stories", timezone: 'America/Chicago', is_active: true, channels: [] },
  { id: 'st-jlm', name: 'KBR Jerusalem', timezone: 'Asia/Jerusalem', is_active: true, channels: [] },
];
// Two plays: 13:30:05Z and 17:00:00Z (the widest time a 12-hour clock can show, "12:00:00 PM" in New York)
const PLAYS = ['2026-11-19T13:30:05.400Z', '2026-11-19T17:00:00.000Z'];
const WANT = {
  'st-main': ['8:30:05 AM', '12:00:00 PM'],
  'st-stories': ['7:30:05 AM', '11:00:00 AM'],
  'st-jlm': ['3:30:05 PM', '7:00:00 PM'],
};

const SIZES = [
  { tag: 'desktop 1440x900', opts: { viewport: { width: 1440, height: 900 } } },
  { tag: 'iPhone 13', opts: { ...devices['iPhone 13'] }, phone: true },
  { tag: 'iPhone SE', opts: { ...devices['iPhone SE'] }, phone: true },
];

function station(sid) {
  const s = makeStation({ sid, stations: STATIONS });
  const base = s.handle.bind(s);
  s.handle = (method, path, query, body) => {
    if (path === `/stations/${sid}/queue/log`) {
      return { logs: PLAYS.map((at, i) => ({ id: `l${i}`, start_utc: at, title: `Played song ${i + 1}`, artist: 'Artist', asset_type: 'music', source: 'auto' })).reverse() };
    }
    if (path === `/rds/stations/${sid}/settings`) return { station_id: sid, ps_text: null, ps_mode: 'static', rotation_seconds: 6, now_playing_weight: 1, sponsor_while_spot_airs: true, fallback_text: null, default_ps: 'KBR', default_fallback: 'Kol Bramah' };
    if (path === '/rds/messages') return { messages: [] };
    if (path === `/rds/stations/${sid}/preview`) { const slot = { at: PLAYS[1], ps: 'KBR', rt: 'Now playing', kind: 'now_playing', message_id: null, sponsor_id: null, sponsor_name: null, changes_at: null }; return { now: slot, coming: [] }; }
    if (path === '/rds/plays') return { plays: PLAYS.map((at, i) => ({ id: `r${i}`, station_id: sid, kind: 'now_playing', text: `Line ${i + 1}`, ps_text: null, sponsor_id: null, sponsor_name: null, first_seen_at: at, last_seen_at: at, fetches: 1 })), sponsor_totals: [] };
    if (path === '/sponsors') return [];
    return base(method, path, query, body);
  };
  return s;
}

const shot = async (page, name) => {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: `${SHOTS}/${name.replace(/[^\w-]+/g, '-')}.png` });
};

async function run(browser, size, st) {
  const tag = `${size.tag} ${st.name}`;
  const s = station(st.id);
  const { ctx } = await adminContext(browser, size.opts, s, { storage: { radio_last_station: st.id, 'kbr-cr-listen': null } });
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);

  // Control room, Played
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  if (size.phone) await page.locator('[aria-label="Control room sections"] button', { hasText: 'More' }).first().tap();
  else await page.getByRole('tab', { name: 'Played' }).click();
  await page.getByText('Played song 1').first().waitFor({ timeout: 5000 }).catch(() => {});
  const cells = page.getByTestId('played-at').locator('visible=true');
  const shown = (await cells.allInnerTexts()).map(t => t.replace(/\s+/g, ' ').replace(/(\d)(AM|PM)/, '$1 $2').trim());
  const want = [...WANT[st.id]].reverse();
  check(`${tag}: Played shows start times to the second on the station's clock`, JSON.stringify(shown) === JSON.stringify(want), shown.join(', '));
  const fits = await cells.evaluateAll(els => els.every(e => e.scrollWidth <= e.clientWidth + 1 && e.getBoundingClientRect().height < 24));
  check(`${tag}: the time fits on one line without being cut`, fits);
  const titleW = await page.getByText('Played song 1').first().evaluate(e => e.getBoundingClientRect().width);
  check(`${tag}: the title still has room`, titleW > 60, `${Math.round(titleW)} px`);
  await shot(page, `${size.tag}-${st.id}-played`);

  // RDS page: what radios showed, to the second
  await page.goto(`${BASE}/admin/rds`);
  await page.getByText('Line 1').first().waitFor({ timeout: 5000 }).catch(() => {});
  const body = await page.locator('body').innerText();
  const first = WANT[st.id][0];
  check(`${tag}: RDS log shows the station's time to the second`, body.includes(first), first);
  const sideways = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(`${tag}: RDS page has no sideways scroll`, sideways <= 1, `${sideways} px`);

  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// A browser in another zone than any station, so a time shown in the viewer's zone fails
const browser = await chromium.launch();
try {
  for (const size of SIZES) for (const st of STATIONS) await run(browser, { ...size, opts: { ...size.opts, timezoneId: 'Pacific/Auckland' } }, st);
} finally {
  await browser.close();
}
process.exit(done());
