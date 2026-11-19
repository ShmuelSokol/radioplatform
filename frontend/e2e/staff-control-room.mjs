/**
 * Control room presses that must land once and on the right station, against the fake
 * station (e2e/lib/fake-station.mjs; nothing real is touched):
 *   V3-17  a double-click or a key repeat on Next or Play now sends one action
 *          (Panic's double-click is covered by e2e/control-room.mjs and control-room-actions.mjs)
 *   V3-18  two tabs on two stations: every press goes to its own tab's station, also after
 *          one tab switches station (the remembered station is shared through localStorage)
 *   V1-142 the headphones (PFL) output choice is stored and restored; a device no longer
 *          present falls back to the default output
 *
 *   cd frontend && npm run build && (npx vite preview --port 4302 --strictPort &)
 *   BASE_URL=http://localhost:4302 node e2e/staff-control-room.mjs
 * Exits non-zero if a check fails.
 */
import { chromium, checker, sleep, makeStation, adminContext, newPage, allowEverywhere } from './lib/fake-station.mjs';

allowEverywhere(/That item just started/, 'a second Next right after the first is refused by the station on purpose');

const BASE = process.env.BASE_URL || 'http://localhost:4302';
const DESKTOP = { viewport: { width: 1440, height: 900 } };
const { check, done } = checker({});

async function openRoom(ctx) {
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const { page, errors } = await newPage(ctx);
  await page.goto(`${BASE}/admin/dashboard`);
  await page.getByTestId('queue-row').first().waitFor();
  return { page, errors };
}
const posts = (st, re) => st.calls.filter((c) => c.method === 'POST' && re.test(c.path));

async function noDoubleActions(browser) {
  const station = makeStation();
  const { ctx } = await adminContext(browser, DESKTOP, station, { storage: { radio_last_station: null } });
  const { page, errors } = await openRoom(ctx);
  await sleep(5000); // past the "just changed" window of the item on air

  // Double-click on Next
  const next = page.getByRole('button', { name: /Go next/ }).first();
  let before = posts(station, /\/queue\/skip$/).length;
  await next.dblclick();
  await sleep(1200);
  let sent = posts(station, /\/queue\/skip$/).length - before;
  check('V3-17 a double-click on Next sends one Next', sent === 1, `${sent} sent`);

  // Key repeat on Next: Enter held down (keydown repeats without keyup)
  await sleep(5000);
  before = posts(station, /\/queue\/skip$/).length;
  await next.focus();
  for (let i = 0; i < 4; i++) { await page.keyboard.down('Enter'); await sleep(35); }
  await page.keyboard.up('Enter');
  await sleep(1200);
  sent = posts(station, /\/queue\/skip$/).length - before;
  check('V3-17 Enter held on Next (key repeat) sends one Next', sent === 1, `${sent} sent`);

  // Double-click on a Play now (instant) button
  const jingle = page.getByTestId('hot-button').filter({ hasText: 'Station ID 4' });
  before = posts(station, /\/queue\/(fire-now|play-now|insert)/).length;
  await jingle.dblclick();
  await sleep(1200);
  const fired = posts(station, /\/queue\/(fire-now|play-now|insert)/).slice(before);
  check('V3-17 a double-click on a Play now button plays it once', fired.length === 1, fired.map((c) => c.path).join(', ') || 'none');
  check('V3-17 no script errors', errors.length === 0, errors.join(' | '));
  await ctx.close();
}

async function twoTabs(browser) {
  const station = makeStation();
  const { ctx } = await adminContext(browser, DESKTOP, station, { storage: { radio_last_station: null } });
  const a = await openRoom(ctx);
  const b = await openRoom(ctx);
  // Tab B moves to the second station; tab A stays on the first
  await b.page.getByLabel('Station').first().selectOption('st-2');
  await sleep(800);
  check('V3-18 tab A still shows its own station after tab B switched',
    await a.page.getByLabel('Station').first().inputValue() === station.SID, await a.page.getByLabel('Station').first().inputValue());
  // Play now (an instant button) works whether or not the station is playing
  const fires = () => posts(station, /\/queue\/fire-now$/);
  const fire = async (tab) => {
    const n = fires().length;
    await tab.page.bringToFront();
    await tab.page.getByTestId('hot-button').filter({ hasText: 'Station ID 4' }).click();
    await sleep(1000);
    return fires().slice(n).map((c) => c.path);
  };
  await sleep(1000);
  let got = await fire(a);
  check('V3-18 Play now in tab A goes to tab A\'s station', got.length === 1 && got[0] === `/stations/${station.SID}/queue/fire-now`, got.join(', '));
  got = await fire(b);
  check('V3-18 Play now in tab B goes to tab B\'s station (st-2)', got.length === 1 && got[0] === '/stations/st-2/queue/fire-now', got.join(', '));
  // Both tabs switch the other way round: still each its own
  await b.page.getByLabel('Station').first().selectOption(station.SID);
  await a.page.getByLabel('Station').first().selectOption('st-2');
  await sleep(1500);
  got = await fire(a);
  check('V3-18 after both tabs switched, tab A\'s Play now goes to st-2', got.length === 1 && got[0] === '/stations/st-2/queue/fire-now', got.join(', '));
  got = await fire(b);
  check('V3-18 after both tabs switched, tab B\'s Play now goes to the first station', got.length === 1 && got[0] === `/stations/${station.SID}/queue/fire-now`, got.join(', '));
  await ctx.close();
}

// V1-142: the headphones (PFL) output is remembered; a device that is gone falls back
async function pflOutput(browser) {
  const station = makeStation();
  const { ctx } = await adminContext(browser, DESKTOP, station, { storage: { radio_last_station: null, kbr_pfl_output: null } });
  await ctx.addInitScript(() => {
    const gone = localStorage.getItem('__hp_gone') === '1';
    navigator.mediaDevices.enumerateDevices = async () => [
      { kind: 'audiooutput', deviceId: 'default', label: 'Default', groupId: 'g' },
      ...(gone ? [] : [{ kind: 'audiooutput', deviceId: 'hp-1', label: 'Headphones (USB)', groupId: 'g1' }]),
      { kind: 'audiooutput', deviceId: 'spk-2', label: 'Desk speakers', groupId: 'g2' },
    ];
  });
  const { page } = await openRoom(ctx);
  const picker = async () => {
    const pick = page.getByLabel('Headphones output');
    if (await pick.count() === 0) await page.getByRole('button', { name: 'Choose the headphones output' }).click();
    await pick.waitFor({ timeout: 3000 }).catch(() => {});
    return pick;
  };
  // PFL audio: an <audio> that is not one of the on-air decks
  const pflPlaying = () => page.evaluate(() => {
    const decks = new Set(window.__deckEls());
    const a = window.__audios.filter((x) => !decks.has(x) && /j2/.test(x.currentSrc || x.src)).pop();
    return a ? { playing: !a.paused && a.readyState >= 2, t: a.currentTime } : null;
  });
  await page.getByTestId('pfl-toggle').click();
  await (await picker()).selectOption('hp-1');
  check('V1-142 headphones choice stored in kbr_pfl_output', await page.evaluate(() => localStorage.getItem('kbr_pfl_output')) === 'hp-1');

  // Reload: the choice comes back and pre-listen goes there
  await page.reload();
  await page.getByTestId('queue-row').first().waitFor();
  if (await page.getByLabel('Headphones output').count() === 0 && await page.getByRole('button', { name: 'Choose the headphones output' }).count() === 0) await page.getByTestId('pfl-toggle').click();
  const pick = await picker();
  check('V1-142 after a reload the picker shows the stored headphones', await pick.inputValue() === 'hp-1', await pick.inputValue());
  await page.evaluate(() => { window.__sinks.length = 0; });
  await page.getByTestId('hot-button').filter({ hasText: 'Station ID 2' }).click();
  await sleep(800);
  check('V1-142 after a reload pre-listen goes to the stored output', (await page.evaluate(() => window.__sinks)).includes('hp-1'), JSON.stringify(await page.evaluate(() => window.__sinks)));
  await page.getByTestId('hot-button').filter({ hasText: 'Station ID 2' }).click(); // stop it

  // The headphones are unplugged: the stored id is no longer listed
  await page.evaluate(() => localStorage.setItem('__hp_gone', '1'));
  await page.reload();
  await page.getByTestId('queue-row').first().waitFor();
  if (await page.getByLabel('Headphones output').count() === 0 && await page.getByRole('button', { name: 'Choose the headphones output' }).count() === 0) await page.getByTestId('pfl-toggle').click();
  const pick2 = await picker();
  const shownLabel = await pick2.evaluate((el) => el.selectedOptions[0]?.textContent || '');
  check('V1-142 a device that is gone shows "Same output as everything else"', shownLabel === 'Same output as everything else', shownLabel);
  await page.getByTestId('hot-button').filter({ hasText: 'Station ID 2' }).click();
  await sleep(1200);
  const st = await pflPlaying();
  check('V1-142 with the device gone pre-listen still plays (default output)', !!st && st.playing && st.t > 0.2, JSON.stringify(st));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await noDoubleActions(browser);
  await twoTabs(browser);
  await pflOutput(browser);
} finally {
  await browser.close();
}
process.exit(done());
