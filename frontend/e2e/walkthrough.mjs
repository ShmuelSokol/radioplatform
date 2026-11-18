/**
 * Walk through the staff site the way a person does and LOOK at it (Shmuel, 2026-11-17).
 *
 * For each of three stations (seeded by e2e/walkthrough-seed.py, each owning things named
 * with its own tag) it opens every staff page, then presses every control on the page one
 * by one: buttons, tabs, selects, checkboxes. After the page opens and after every press
 * it takes a screenshot and asks the guard (e2e/lib/guard.mjs) what the screen showed.
 * Findings, even when the press "worked":
 *   - an error or warning toast, an alert, the error page
 *   - a console error, an uncaught error, a failed or refused request
 *   - anything named with ANOTHER station's tag on a station's own page
 *   - a press that took longer than SLOW_MS to settle
 *   - a control that runs off the window, is cut off, lies under something else or cannot
 *     be pressed, at 1366x900 and at phone size
 *   - a page with its own station picker opening on another station than the one last used
 *   - a station setting the walk left changed (every switch is pressed twice to put it back)
 * In the control room it also drags a queue row to a new place with the mouse (the path
 * that showed Shmuel a warning from another station's rule) and checks the row moved.
 *
 * Controls that destroy the run's own data (Delete, Panic, End show, Sign out ...) are
 * listed as "not pressed" in the report; the create / edit / delete flows have their own
 * tests (e2e/admin-flows.mjs).
 *
 * Setup (see e2e/admin-flows.mjs for the same stack): backend on a local Postgres, the
 * built site on vite preview, a static server for the seed's audio (AUDIO_BASE), then
 *   STATIONS_JSON='<the seed's output line>' E2E_ADMIN_EMAIL=... E2E_ADMIN_PASS=... \
 *     BASE_URL=http://localhost:4330 API_URL=http://localhost:8230/api/v1 SHOTS_DIR=/path node e2e/walkthrough.mjs
 * Env: ONLY=<comma list of route paths>, STATIONS=<comma list of tags>, MAX_CONTROLS (default 80), PER_KIND (default 2: a queue of 40 rows has 40 Move up buttons; two are pressed),
 *   SLOW_MS (default 2500).
 * Output: one line per page, FINDING lines, <SHOTS_DIR>/findings.json and index.html (every
 * screenshot in order with its label, for the fresh-eyes review). Exit code 1 on any finding.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { guardPage } from './lib/guard.mjs';

const { chromium } = createRequire(import.meta.url)('playwright');

const BASE = process.env.BASE_URL || 'http://localhost:4330';
const API = process.env.API_URL || 'http://localhost:8230/api/v1';
const SHOTS = process.env.SHOTS_DIR || '';
const ONLY = (process.env.ONLY || '').split(',').map((s) => s.trim()).filter(Boolean);
const ONLY_ST = (process.env.STATIONS || '').split(',').map((s) => s.trim()).filter(Boolean);
const MAX_CONTROLS = Number(process.env.MAX_CONTROLS || 80);
const PER_KIND = Number(process.env.PER_KIND || 2);   // presses of controls that differ only by their row
const SLOW_MS = Number(process.env.SLOW_MS || 2500);
const STATIONS = JSON.parse(process.env.STATIONS_JSON || '{"stations":[]}').stations;
const TAGS = STATIONS.map((s) => s.tag);

// Every staff page that needs no id in its address
const ROUTES = [
  '/admin/dashboard', '/admin/stations', '/admin/assets', '/admin/assets/upload', '/admin/reviews', '/admin/users',
  '/admin/rules', '/admin/schedules', '/admin/holidays', '/admin/sponsors', '/admin/campaigns', '/admin/spot-schedule',
  '/admin/spot-recordings', '/admin/databank', '/admin/program-blocks', '/admin/schedule-check',
  '/admin/sponsorship-rules', '/admin/playlists', '/admin/categories', '/admin/asset-types', '/admin/alerts',
  '/admin/bug-reports', '/admin/site-updates', '/admin/activity', '/admin/analytics', '/admin/live', '/admin/requests',
  '/admin/archives', '/admin/studio', '/admin/listeners', '/admin/crm', '/admin/program-clock',
  '/stations', '/guide', '/hosts', '/archives',
];
// Pages about the whole site or the shared library: another station's things belong there
const SITE_WIDE = {
  '/admin/stations': 'lists every station',
  '/admin/rules': 'lists every station\'s rules, each with its station named',
  '/admin/assets': 'the library is shared',
  '/admin/databank': 'the library is shared',
  '/admin/reviews': 'review queues are of library items',
  '/admin/sponsors': 'sponsors are not tied to one station',
  '/admin/campaigns': 'campaigns are not tied to one station',
  '/admin/spot-recordings': 'recordings are library items',
  '/admin/users': 'accounts are site-wide',
  '/admin/bug-reports': 'reports are site-wide',
  '/admin/activity': 'the activity log covers every station unless filtered',
  '/admin/categories': 'categories are library-wide',
  '/admin/asset-types': 'types are library-wide',
  '/stations': 'the public list of every station',
  '/archives': 'the public archive of every station',
  '/guide': 'has its own station picker',
  '/hosts': 'hosts are site-wide',
  '/admin/schedules': 'lists every station\'s schedules',
  '/admin/holidays': 'lists every blackout, each with its stations',
  '/admin/alerts': 'lists every station\'s alerts',
  '/admin/live': 'lists every station\'s live shows',
  '/admin/requests': 'lists every station\'s requests',
  '/admin/archives': 'lists every station\'s archives',
};
// The sizes a control must be fully visible and pressable at (1366x900 is the walk's own size)
const SIZES = [{ name: 'phone 390x844', width: 390, height: 844 }];
const WALK_SIZE = { width: 1366, height: 900 };
const NOT_PRESSED = /delete|\bdel\b|remove|clear|panic|end show|go live|start show|sign out|log out|logout|resolve all|reset|push now|wipe|purge|seed|regenerate|🐞|report a problem/i;

const findings = [];
const shots = [];
let shotN = 0;
const slug = (s) => s.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').slice(0, 60).toLowerCase();

async function shoot(page, label) {
  if (!SHOTS) return '';
  const file = `${String(++shotN).padStart(4, '0')}-${slug(label)}.jpg`;
  try { await page.screenshot({ path: path.join(SHOTS, file), type: 'jpeg', quality: 55 }); } catch { return ''; }
  shots.push({ file, label });
  return file;
}
const told = new Set();
function finding(station, route, step, kind, text, shot) {
  // The same thing on the same page is one finding, however many presses it stays through
  const key = `${station}|${route}|${kind}|${text}`;
  if (told.has(key)) return;
  told.add(key);
  findings.push({ station, route, step, kind, text, shot });
  console.log(`FINDING [${station} ${route} / ${step}] ${kind}: ${text}${shot ? '  (' + shot + ')' : ''}`);
}

async function loginApi() {
  const r = await fetch(`${API}/auth/login`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email: process.env.E2E_ADMIN_EMAIL, password: process.env.E2E_ADMIN_PASS }) });
  if (!r.ok) throw new Error(`login failed: ${r.status}`);
  return r.json();
}

async function stationSettings(tokens) {
  const r = await fetch(`${API}/stations?skip=0&limit=100`, { headers: { authorization: `Bearer ${tokens.access_token}` } });
  const out = {};
  for (const s of (await r.json()).stations || []) out[s.id] = JSON.stringify({ active: s.is_active, config: s.automation_config || {}, protected: s.protected });
  return out;
}

/** Visible controls in the page body (not the site navigation), in screen order. */
async function controls(page) {
  return page.evaluate(() => {
    const out = [];
    const seenLabels = {};
    const els = document.querySelectorAll('button, [role="button"], [role="tab"], select, input[type="checkbox"], input[type="radio"], summary');
    for (const el of els) {
      if (el.closest('nav, [data-testid="navbar"], [data-testid="site-nav"], header[role="banner"]')) continue;
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) continue;
      const s = getComputedStyle(el);
      if (s.visibility === 'hidden' || s.display === 'none') continue;
      let label = (el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || el.value || el.getAttribute('name') || el.getAttribute('data-testid') || el.tagName).replace(/\s+/g, ' ').trim().slice(0, 50);
      if (el.tagName === 'SELECT') label = `select ${el.getAttribute('aria-label') || el.name || el.id || ''}`.trim();
      if (el.tagName === 'INPUT') label = `${el.type} ${label}`;
      const n = (seenLabels[label] = (seenLabels[label] || 0) + 1);
      el.setAttribute('data-walk', `${label}#${n}`);
      out.push({ key: `${label}#${n}`, label, tag: el.tagName, nth: n });
    }
    return out;
  });
}

/**
 * Controls a person cannot fully see or press with a mouse or finger at this window size:
 * running off the side, cut off by its box, or lying under something else (PC walk-through
 * 2026-11-18: Loop half under the new Song button at 1366x900). Uses the data-walk marks
 * controls() left. Also says when the whole page scrolls sideways.
 */
async function unreachable(page) {
  return page.evaluate(() => {
    const out = [];
    const name = (el) => (el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || el.value || el.tagName || '').replace(/\s+/g, ' ').trim().slice(0, 40);
    const vw = document.documentElement.clientWidth;
    if (document.documentElement.scrollWidth > vw + 2) out.push({ label: 'page', kind: 'the page scrolls sideways', text: `${document.documentElement.scrollWidth} px wide in a ${vw} px window` });
    for (const el of document.querySelectorAll('[data-walk]')) {
      const st = getComputedStyle(el);
      if (st.opacity === '0' || st.pointerEvents === 'none') continue;   // shown on hover, or not for pressing
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      const r = el.getBoundingClientRect();
      if (r.width < 4 || r.height < 4) continue;
      const label = el.getAttribute('data-walk');
      if (r.right > vw + 1 || r.left < -1) { out.push({ label, kind: 'runs off the side of the window', text: `from ${Math.round(r.left)} to ${Math.round(r.right)} px in a ${vw} px window` }); continue; }
      // what its boxes leave visible
      let l = r.left, t = r.top, rr = r.right, b = r.bottom;
      for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
        const s = getComputedStyle(p);
        if (!/(hidden|clip|auto|scroll)/.test(s.overflowX + s.overflowY)) continue;
        const pr = p.getBoundingClientRect();
        l = Math.max(l, pr.left); t = Math.max(t, pr.top); rr = Math.min(rr, pr.right); b = Math.min(b, pr.bottom);
      }
      const seen = Math.max(0, rr - l) * Math.max(0, b - t) / (r.width * r.height);
      if (seen < 0.7) { out.push({ label, kind: 'cut off by its box', text: `${Math.round(seen * 100)}% of it shows` }); continue; }
      // what lies on top of it, at the middle and towards each corner
      const pts = [[0.5, 0.5], [0.2, 0.3], [0.8, 0.3], [0.2, 0.7], [0.8, 0.7]];
      let mine = 0; let over = null;
      const same = (top) => top && (top === el || el.contains(top) || top.contains(el)
        || (el.labels && [...el.labels].some((lb) => lb === top || lb.contains(top)))
        || (top.closest('label') && top.closest('label').contains(el)));
      for (const [fx, fy] of pts) {
        const top = document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy);
        if (same(top)) mine++; else if (top && !over) over = top;
      }
      if (mine === 0) out.push({ label, kind: 'cannot be pressed: it lies under something else', text: `under "${name(over || document.body)}"` });
      else if (mine <= 3 && over) out.push({ label, kind: 'partly hidden under something else', text: `${5 - mine} of 5 points are under "${name(over)}"` });
    }
    return out;
  });
}

/** The page's own station picker (a select listing every station), if it has one. */
async function stationPicker(page) {
  return page.evaluate((names) => {
    for (const sel of document.querySelectorAll('select')) {
      const texts = [...sel.options].map((o) => o.textContent.trim());
      if (names.every((n) => texts.some((t) => t.includes(n)))) {
        sel.setAttribute('data-walk-station', '1');
        // a filter that starts on "all stations" is not a station picker
        if ([...sel.options].some((o) => o.value === '')) continue;
        return { chosen: (sel.selectedOptions[0]?.textContent || '').trim() };
      }
    }
    return null;
  }, STATIONS.map((s) => s.name));
}

async function settle(page, ms = 450) {
  await page.waitForLoadState('networkidle', { timeout: 2500 }).catch(() => {});
  await page.waitForTimeout(ms);
}

function otherTags(text, tag) {
  return TAGS.filter((t) => t !== tag && text.includes(t));
}

async function lookAt(page, guard, st, route, step, expected = []) {
  const shot = await shoot(page, `${st.tag}${route}-${step}`);
  for (const rec of await guard.expectOnly(step, expected)) finding(st.tag, route, step, rec.kind, rec.text, shot);
  if (!SITE_WIDE[route]) {
    const text = await page.evaluate(() => document.body.innerText).catch(() => '');
    const others = otherTags(text, st.tag);
    if (others.length) {
      const t = others[0];
      const at = text.indexOf(t);
      finding(st.tag, route, step, "another station's thing on this station's page",
        `${others.join(', ')}: "…${text.slice(Math.max(0, at - 50), at + 60).replace(/\s+/g, ' ')}…"`, shot);
    }
  }
  return shot;
}

let current = null;   // the station being walked
async function open(page, route) {
  await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded' });
  await settle(page, 700);
  // A page with its own station picker: say so when it opens on another station than the
  // one last worked on, then choose ours so the rest of the walk is about this station
  const pick = current && await stationPicker(page);
  if (pick && !pick.chosen.includes(current.name)) {
    if (pick.chosen) finding(current.tag, route, 'open', 'opens on another station than the one last worked on', `shows "${pick.chosen}"`, await shoot(page, `${current.tag}${route}-other-station`));
    await page.locator('[data-walk-station]').first().selectOption({ label: await page.locator('[data-walk-station] option').evaluateAll((os, n) => (os.find((o) => o.textContent.includes(n)) || {}).textContent, current.name) }).catch(() => {});
    await settle(page, 500);
  }
}

async function reachAt(page, st, route, sizeName) {
  await controls(page);
  const bad = await unreachable(page);
  if (!bad.length) return;
  await page.evaluate(() => window.scrollTo(0, 0));
  const shot = await shoot(page, `${st.tag}${route}-reach-${sizeName}`);
  for (const b of bad.slice(0, 8)) finding(st.tag, route, `look at ${sizeName}`, b.kind, `${b.label}: ${b.text} (${sizeName})`, shot);
}

async function restore(page, route) {
  // Close whatever the press opened, and come back if it navigated
  for (let i = 0; i < 2; i++) await page.keyboard.press('Escape').catch(() => {});
  if (new URL(page.url()).pathname !== route) await open(page, route);
}

async function walkPage(page, guard, st, route) {
  await open(page, route);
  if (new URL(page.url()).pathname !== route) {
    finding(st.tag, route, 'open', 'did not open', `landed on ${new URL(page.url()).pathname}`, await shoot(page, `${st.tag}${route}-redirect`));
    return { pressed: 0, skipped: [], total: 0 };
  }
  await lookAt(page, guard, st, route, 'open');
  await reachAt(page, st, route, `${WALK_SIZE.width}x${WALK_SIZE.height}`);
  for (const size of SIZES) {
    await page.setViewportSize({ width: size.width, height: size.height });
    await open(page, route);
    await lookAt(page, guard, st, route, `open at ${size.name}`);
    await reachAt(page, st, route, size.name);
  }
  await page.setViewportSize(WALK_SIZE);
  await open(page, route);
  const first = await controls(page);
  const done = new Set();
  const skipped = [];
  const kinds = {};
  let repeats = 0;
  let pressed = 0;
  for (let round = 0; round < MAX_CONTROLS * 2 && pressed < MAX_CONTROLS; round++) {
    const now = await controls(page);
    const next = now.find((c) => !done.has(c.key));
    if (!next) break;
    done.add(next.key);
    if (NOT_PRESSED.test(next.label)) { skipped.push(next.label); continue; }
    // The same control on another row: "Move up" x40, "Insert a song before <title>" x40
    const kind = `${next.tag}:${next.label.replace(/[“"].*$/, '').split(' ').slice(0, 4).join(' ').replace(/\d+/g, 'N')}`;
    kinds[kind] = (kinds[kind] || 0) + 1;
    if (kinds[kind] > PER_KIND) { repeats++; continue; }
    const el = page.locator(`[data-walk="${next.key.replace(/"/g, '\\"')}"]`).first();
    if (await el.getAttribute('data-walk-station').catch(() => null)) continue;   // stay on this station
    const handle = await el.elementHandle().catch(() => null);
    const lookBefore = handle ? await handle.evaluate((e) => e.className + '|' + (e.getAttribute('aria-pressed') || '')).catch(() => '') : '';
    const t0 = Date.now();
    let was = null;
    try {
      if (next.tag === 'SELECT') {
        const values = await el.evaluate((s) => [...s.options].map((o) => o.value));
        was = await el.inputValue();
        const other = values.find((v) => v !== was && v !== '');
        if (other === undefined) continue;
        await el.selectOption(other, { timeout: 2000 });
      } else {
        await el.click({ timeout: 2000 });
      }
    } catch (e) {
      // Gone after the last press is nothing; still there and not pressable is what a
      // person meets as "the button does nothing"
      if (await el.count().catch(() => 0)) {
        const why = /intercepts pointer events/.test(e.message) ? 'something else lies on top of it'
          : /not visible|outside of the viewport/.test(e.message) ? 'it cannot be brought into view' : String(e.message).split('\n')[0].slice(0, 120);
        finding(st.tag, route, `press ${next.label}`, 'could not be pressed', `${next.label}: ${why}`, await shoot(page, `${st.tag}${route}-cannot-press-${next.label}`));
      }
      continue;
    }
    pressed++;
    await settle(page, 350);
    const took = Date.now() - t0;
    const step = `press ${next.label}${next.nth > 1 ? ' #' + next.nth : ''}`;
    const shot = await lookAt(page, guard, st, route, step);
    if (took > SLOW_MS + 900) finding(st.tag, route, step, 'slow', `${took} ms until the page settled`, shot);
    // Put back what the press switched, so the walk leaves the station as it found it
    // and later pages are not looked at in a state the walk itself made
    try {
      if (next.tag === 'SELECT') {
        if (await el.count()) { await el.selectOption(was, { timeout: 1500 }); await settle(page, 300); await guard.expectOnly('put back'); }
      } else if (handle && await handle.evaluate((e, before) => e.isConnected && (e.matches('input[type="checkbox"], [role="switch"], [aria-pressed], [aria-checked]')
          // a chip that changed its own look when pressed (a station chosen in a form) is a switch too
          || (e.className + '|' + (e.getAttribute('aria-pressed') || '')) !== before
          || /\b(on|off)\b|pause|resume|activate|deactivate|enable|disable|publish|unpublish|mute|hide|show/i.test(e.getAttribute('aria-label') || e.getAttribute('title') || e.innerText || '')), lookBefore)) {
        await handle.click({ timeout: 1500 });
        await settle(page, 300);
        await lookAt(page, guard, st, route, `${step} again`);
      }
    } catch { /* gone or covered now; the end-of-station check says if anything stayed changed */ }
    if (next.tag === 'SELECT') await open(page, route); else await restore(page, route);
  }
  return { pressed, skipped, total: first.length, repeats };
}

/** Control room: drag the third waiting row above the first, with the mouse. */
async function dragInQueue(page, guard, st) {
  const route = '/admin/dashboard';
  await open(page, route);
  const rows = page.locator('[data-testid="queue-row"]');
  const n = await rows.count();
  if (n < 4) { finding(st.tag, route, 'drag', 'cannot test', `only ${n} queue rows`, await shoot(page, `${st.tag}-drag-norows`)); return; }
  const ids = async () => rows.evaluateAll((els) => els.map((e) => e.getAttribute('data-entry-id')));
  const titles = async () => rows.evaluateAll((els) => els.map((e) => (e.innerText || '').replace(/\s+/g, ' ').slice(0, 40)));
  const before = await ids();
  await guard.expectOnly('before drag');
  const src = rows.nth(3).locator('[draggable="true"]').first();
  const dst = rows.nth(1).locator('[draggable="true"]').first();
  await shoot(page, `${st.tag}-drag-before`);
  try {
    await src.dragTo(dst, { timeout: 5000 });
  } catch (e) {
    finding(st.tag, route, 'drag', 'drag failed', String(e.message).split('\n')[0], await shoot(page, `${st.tag}-drag-failed`));
    return;
  }
  await settle(page, 800);
  const shot = await lookAt(page, guard, st, route, 'drag a queue row up');
  const after = await ids();
  const moved = before[3];
  if (after.indexOf(moved) >= before.indexOf(moved)) {
    finding(st.tag, route, 'drag a queue row up', 'the row did not move', `was row ${before.indexOf(moved) + 1}, now row ${after.indexOf(moved) + 1}: ${JSON.stringify(await titles())}`, shot);
  }
  await page.reload({ waitUntil: 'domcontentloaded' });
  await settle(page, 700);
  const reloaded = await ids();
  if (reloaded.indexOf(moved) !== after.indexOf(moved)) {
    finding(st.tag, route, 'drag a queue row up', 'the new order did not survive a reload', `row ${after.indexOf(moved) + 1} before the reload, ${reloaded.indexOf(moved) + 1} after`, await shoot(page, `${st.tag}-drag-reloaded`));
  }
  await guard.expectOnly('after drag reload');
}

async function main() {
  if (!STATIONS.length) throw new Error('STATIONS_JSON is empty: run e2e/walkthrough-seed.py first');
  if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
  const tokens = await loginApi();
  const browser = await chromium.launch();
  const summary = [];
  for (const st of STATIONS) {
    if (ONLY_ST.length && !ONLY_ST.includes(st.tag)) continue;
    current = st;
    const settingsBefore = await stationSettings(tokens);
    const ctx = await browser.newContext({ viewport: WALK_SIZE });
    await ctx.addInitScript(([a, r, sid]) => {
      localStorage.setItem('access_token', a); localStorage.setItem('refresh_token', r);
      localStorage.setItem('radio_last_station', sid);
    }, [tokens.access_token, tokens.refresh_token, st.id]);
    const page = await ctx.newPage();
    page.on('dialog', (d) => d.dismiss().catch(() => {}));
    page.setDefaultTimeout(6000);
    const guard = guardPage(page, st.tag);
    guard.allow(/\/tone\d\.mp3/, "the seed's audio comes from a test file server without CORS");
    guard.allow(/warning toast: Loop is on/, 'Loop says so in amber on purpose: it changes what Next does');
    guard.allow(/warning toast: (HS|Spots mode) is off/, 'switching a station mode off says so in amber on purpose');
    guard.allow(/You cannot disable your own account|PUT .*\/users\/[0-9a-f-]+$|status of 400 .*\/users\//, 'the walk is signed in as the only admin: Disable on itself is refused, and the page says so');
    guard.allow(/preview-weather|Could not load the weather preview/, 'no voice service on the test server');
    await dragInQueue(page, guard, st).catch((e) => finding(st.tag, '/admin/dashboard', 'drag', 'test error', e.message, ''));
    for (const route of ROUTES) {
      if (ONLY.length && !ONLY.includes(route)) continue;
      const before = findings.length;
      let res;
      try { res = await walkPage(page, guard, st, route); } catch (e) {
        finding(st.tag, route, 'walk', 'test error', String(e.message).split('\n')[0], await shoot(page, `${st.tag}${route}-error`));
        res = { pressed: 0, skipped: [], total: 0 };
      }
      const line = { station: st.tag, route, controls: res.total, pressed: res.pressed, not_pressed: res.skipped, findings: findings.length - before };
      summary.push(line);
      console.log(`${line.findings ? 'LOOK' : 'ok  '} ${st.tag} ${route}: ${res.pressed} controls pressed (${res.total} on the page at first, ${res.repeats || 0} more of the same kind, ${res.skipped.length} left alone), ${line.findings} findings`);
    }
    await ctx.close();
    // A test must leave every station's modes as it found them (Spots mode was left off
    // on a live station by a test script, 2026-11-18)
    const settingsAfter = await stationSettings(tokens);
    for (const id of Object.keys(settingsBefore)) {
      if (settingsBefore[id] !== settingsAfter[id]) {
        const who = STATIONS.find((s) => s.id === id)?.tag || id;
        finding(st.tag, '(whole walk)', 'end', who === st.tag ? 'the walk left this station changed' : 'walking one station changed another',
          `${who}: ${settingsBefore[id]} -> ${settingsAfter[id]}`, '');
      }
    }
  }
  await browser.close();
  if (SHOTS) {
    fs.writeFileSync(path.join(SHOTS, 'findings.json'), JSON.stringify({ findings, summary }, null, 1));
    const esc = (s) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
    fs.writeFileSync(path.join(SHOTS, 'index.html'), `<!doctype html><meta charset="utf-8"><title>Walk-through</title><style>body{font:14px sans-serif;background:#111;color:#eee}figure{display:inline-block;margin:6px;vertical-align:top;width:470px}img{width:470px;border:1px solid #444}figcaption{font-size:12px}</style>` +
      shots.map((s) => `<figure><a href="${s.file}"><img loading="lazy" src="${s.file}"></a><figcaption>${esc(s.file)}</figcaption></figure>`).join('\n'));
  }
  const pressed = summary.reduce((a, s) => a + s.pressed, 0);
  console.log(`\n${summary.length} pages walked, ${pressed} controls pressed, ${shotN} screenshots, ${findings.length} findings`);
  process.exit(findings.length ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(2); });
