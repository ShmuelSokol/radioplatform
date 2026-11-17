/**
 * Browser check for the public Listen page (/listen/:station) against a fake backend
 * and a fake now-playing channel, desktop and iPhone size. Covers the buttons, the
 * now-playing card, the phone's lock-screen controls, song requests, the listener
 * profile, network loss and coming back to the tab, the server-stream mode (with a
 * small MP3 stream server on STREAM_PORT) and the hourly time check heard on time.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4311 --strictPort &)
 *   BASE_URL=http://localhost:4311 node e2e/listen-page.mjs      # optional: ONLY=<part>
 *
 * Exits non-zero if any check fails. Known site bugs are listed in KNOWN_BUGS and
 * print XFAIL; when one is fixed its check prints XPASS and fails until removed here.
 */
import {
  chromium, devices, checker, sleep, makeStation, routeApi, routeNowPlaying, AUDIO_PROBE,
  watchDecks, timeClick, timeUntil, routeSiteSocket, skewClock,
} from './lib/fake-station.mjs';
import { startStreamServer } from './lib/stream-server.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4311';
const STREAM_PORT = Number(process.env.STREAM_PORT || 4321);
const ONLY = process.env.ONLY || '';

const KNOWN_BUGS = {};
const { check, done } = checker(KNOWN_BUGS);

async function listenContext(browser, contextOpts, station, { initScripts = [], delay } = {}) {
  const ctx = await browser.newContext(contextOpts);
  await ctx.addInitScript(AUDIO_PROBE);
  for (const s of initScripts) await (Array.isArray(s) ? ctx.addInitScript(...s) : ctx.addInitScript(s));
  await routeApi(ctx, station, { delay });
  const np = await routeNowPlaying(ctx, station);
  await routeSiteSocket(ctx);
  return { ctx, np };
}
async function openListen(ctx, sid) {
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto(`${BASE}/listen/${sid}`);
  return { page, errors };
}
const audibleSrc = (page) => page.evaluate(() => window.__audible().map((d) => d.src));
const listenBtn = (page) => page.getByRole('button', { name: 'Listen Live' });
const stopBtn = (page) => page.getByRole('button', { name: 'Stop', exact: true });

// ── Header, buttons, now playing, volume, lock screen, heartbeat ─────────────
async function basics(browser, contextOpts, tag) {
  const station = makeStation();
  const { ctx, np } = await listenContext(browser, contextOpts, station);
  const { page, errors } = await openListen(ctx, 'no-such-station');
  await page.getByText('Station not found').waitFor({ timeout: 5000 }).catch(() => {});
  check(`${tag}: a bad station id says "Station not found"`, await page.getByText('Station not found').count() === 1);
  await page.goto(`${BASE}/listen/${station.SID}`);
  await page.getByRole('heading', { name: 'Kol Berama' }).waitFor();
  check(`${tag}: header shows type and timezone`, await page.getByText('internet · America/New_York').count() === 1);
  check(`${tag}: header shows the description`, await page.getByText('Music all day').count() === 1);
  await page.getByTestId('playout-badge').waitFor({ timeout: 5000 }).catch(() => {});
  check(`${tag}: playout badge says Browser player`, /Browser player/.test(await page.getByTestId('playout-badge').textContent().catch(() => '')));
  await page.getByText('On Air', { exact: true }).waitFor({ timeout: 5000 }).catch(() => {});
  check(`${tag}: status pill says On Air while the station plays`, await page.getByText('On Air', { exact: true }).count() === 1);
  const cur = station.byId[station.playing().asset_id];
  check(`${tag}: Now Playing card shows title, artist and album`,
    await page.getByText(cur.title, { exact: true }).count() === 1 && await page.getByText(cur.artist, { exact: true }).count() === 1 && await page.getByText(cur.album, { exact: true }).count() === 1);
  check(`${tag}: nothing is heard before Listen is pressed`, (await audibleSrc(page)).length === 0);

  // S-7: Listen Live is heard within 1.5 s of the press
  const heardMs = await timeClick(page, listenBtn(page), `() => window.__audible().some(d => d.src === '${cur.id}')`, 5000, contextOpts.isMobile ? 'tap' : 'click');
  check(`${tag}: Listen Live heard within 1.5 s of the press (S-7)`, heardMs >= 0 && heardMs <= 1500, `${heardMs} ms`);
  const live = await page.evaluate((sid) => window.__decks(), station.SID);
  const deck = live.find((d) => d.src === cur.id && d.playing);
  const liveAt = (Date.now() - new Date(station.playing().started_at).getTime()) / 1000;
  check(`${tag}: joins the item at its live position`, !!deck && Math.abs(deck.t - liveAt) < 2.5, deck ? `at ${deck.t}, live ${liveAt.toFixed(1)}` : 'not playing');
  check(`${tag}: Stop button replaces Listen Live`, await stopBtn(page).count() === 1);

  // F-PLAYER-27: the next item waits, loaded, on the idle deck
  await sleep(1200);
  const nextId = station.pending()[0].asset_id;
  const decks = await page.evaluate(() => window.__decks());
  check(`${tag}: next item pre-loaded on the idle deck, silent`, decks.some((d) => d.src === nextId && !d.playing), JSON.stringify(decks));

  // Heartbeat while listening
  const beats = station.calls.filter((c) => c.path === '/listeners/heartbeat');
  check(`${tag}: listener heartbeat sent for this station`, beats.length >= 1 && beats[0].body?.station_id === station.SID, JSON.stringify(beats[0]?.body ?? null));

  // Lock screen: metadata, playback state
  const ms = () => page.evaluate(() => ({ title: navigator.mediaSession.metadata?.title, artist: navigator.mediaSession.metadata?.artist, album: navigator.mediaSession.metadata?.album, state: navigator.mediaSession.playbackState }));
  let m = await ms();
  check(`${tag}: lock screen shows the item, artist and station`, m.title === cur.title && m.artist === cur.artist && m.album === 'Kol Berama', JSON.stringify(m));
  check(`${tag}: lock screen state is playing`, m.state === 'playing', m.state);

  // S-8 / F-LISTEN-5: a change on air shows within 1 s, card and lock screen
  const next = station.byId[nextId];
  const shownP = timeUntil(page, `() => document.body.innerText.includes(${JSON.stringify(next.title)})`, 3000);
  station.goOnAir(nextId);
  const shownMs = await shownP;
  check(`${tag}: now playing updates within 1 s of a change on air (S-8)`, shownMs >= 0 && shownMs <= 1000, `${shownMs} ms`);
  await sleep(300);
  m = await ms();
  check(`${tag}: lock screen follows the change`, m.title === next.title, JSON.stringify(m));
  const heardNext = await timeUntil(page, `() => window.__audible().some(d => d.src === '${nextId}')`, 3000);
  check(`${tag}: the new item is heard within 1 s of the announcement`, heardNext >= 0 && heardNext <= 1000, `${heardNext} ms`);

  // Volume and mute (the built-in player's output level)
  const level = () => page.evaluate(() => window.__outLevel());
  await page.locator('input[type=range]').fill('0.4');
  await sleep(300);
  check(`${tag}: volume slider shows percent and sets the level`, await page.getByText('40%').count() === 1 && Math.abs((await level()) - 0.4) < 0.05, `level ${await level()}`);
  await page.getByRole('button', { name: 'Mute' }).click();
  await sleep(300);
  check(`${tag}: Mute silences the player`, (await level()) < 0.01 && await page.getByRole('button', { name: 'Unmute' }).count() === 1, `level ${await level()}`);
  await page.getByRole('button', { name: 'Unmute' }).click();
  await sleep(300);
  check(`${tag}: Unmute brings it back`, Math.abs((await level()) - 0.4) < 0.05, `level ${await level()}`);

  // The phone's own controls: pause stops, play rejoins live, no seeking
  const handlers = await page.evaluate(() => Object.fromEntries(Object.entries(window.__msHandlers).map(([k, v]) => [k, typeof v === 'function'])));
  check(`${tag}: lock screen play/pause/stop wired, seeking switched off`,
    handlers.play && handlers.pause && handlers.stop && handlers.seekto === false && handlers.seekforward === false && handlers.seekbackward === false, JSON.stringify(handlers));
  await page.evaluate(() => window.__msHandlers.pause());
  await sleep(500);
  check(`${tag}: lock-screen pause stops the sound`, (await audibleSrc(page)).length === 0 && await listenBtn(page).count() === 1, (await audibleSrc(page)).join());
  check(`${tag}: lock screen state follows Stop`, (await ms()).state === 'paused', (await ms()).state);
  check(`${tag}: stopping says so to the listener count`, station.calls.some((c) => c.path === '/listeners/disconnect'));
  await sleep(3000);
  await page.evaluate(() => window.__msHandlers.play());
  await sleep(1500);
  const after = (await page.evaluate(() => window.__decks())).find((d) => d.playing && d.gain > 0.05);
  const liveNow = (Date.now() - new Date(station.playing().started_at).getTime()) / 1000;
  check(`${tag}: lock-screen play rejoins where the station is now (not where it paused)`, !!after && after.src === nextId && Math.abs(after.t - liveNow) < 2.5, after ? `${after.src}@${after.t}, live ${liveNow.toFixed(1)}` : 'silent');

  // Stop and listen again from the page
  await stopBtn(page).click();
  await sleep(400);
  check(`${tag}: Stop silences at once`, (await audibleSrc(page)).length === 0);
  await listenBtn(page).click();
  const again = await timeUntil(page, `() => window.__audible().length === 1`, 3000);
  check(`${tag}: Listen again is heard`, again >= 0, `${again} ms`);

  // Station stops: "Offline" and "Nothing playing right now"
  station.playing().status = 'played';
  station.push();
  await page.getByText('Nothing playing right now').waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: "Nothing playing right now" and Offline when the station stops`, await page.getByText('Nothing playing right now').count() === 1 && await page.getByText('Offline', { exact: true }).count() === 1);
  await sleep(500);
  check(`${tag}: and nothing is heard`, (await audibleSrc(page)).length === 0);

  // Phone: every button big enough to tap, no sideways scroll
  if (contextOpts.isMobile) {
    await page.getByRole('button', { name: 'Request a Song' }).tap();
    const small = await page.evaluate(() => [...document.querySelectorAll('main button, main a[href], main input, main textarea')]
      .filter((b) => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.height < 44; })
      .map((b) => `${(b.textContent || b.getAttribute('placeholder') || b.tagName).trim().slice(0, 16)}:${Math.round(b.getBoundingClientRect().height)}`));
    check(`${tag}: every button and field is at least 44 px tall`, small.length === 0, small.join(', '));
    const w = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, vw: innerWidth }));
    check(`${tag}: no sideways scrolling`, w.doc <= w.vw, `${w.doc} > ${w.vw}`);
  }
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
  void np;
}

// ── Live show banner, song requests, listener profile ─────────────
async function forms(browser) {
  const tag = 'listen forms';
  const station = makeStation({ shows: [] });
  const { ctx } = await listenContext(browser, { viewport: { width: 1280, height: 900 } }, station, { delay: (p) => (p === '/song-requests' ? 400 : 0) });
  const { page, errors } = await openListen(ctx, station.SID);
  await page.getByRole('heading', { name: 'Kol Berama' }).waitFor();
  check(`${tag}: no live banner when no show is on`, await page.getByText(/^LIVE:/).count() === 0);
  station.live.shows.push({ id: 'ls9', station_id: station.SID, title: 'Motzei Shabbos Live', description: 'Call in with your questions', status: 'live' });
  const polled = await timeUntil(page, `() => document.body.innerText.includes('LIVE: Motzei Shabbos Live')`, 17000);
  check(`${tag}: live banner appears within one 15 s poll`, polled >= 0 && polled <= 16000, `${polled} ms`);
  check(`${tag}: banner shows the description and a call-in link`, await page.getByText('Call in with your questions').count() === 1
    && await page.getByRole('link', { name: 'Call in from here' }).getAttribute('href') === `/call/${station.SID}`);

  // Song request
  await page.getByRole('button', { name: 'Request a Song' }).click();
  await page.getByRole('button', { name: 'Submit Request' }).click();
  check(`${tag}: name and song are required`, await page.getByText('Name and song title are required.').count() === 1
    && !station.calls.some((c) => c.path === '/song-requests'));
  await page.getByPlaceholder('Enter your name').fill('Moshe');
  await page.getByPlaceholder('What song would you like to hear?').fill('Song number 12');
  await page.getByPlaceholder('Artist name').fill('Artist 3');
  await page.getByPlaceholder('Dedication or message...').fill('For my mother');
  const busyMs = await timeClick(page, page.getByRole('button', { name: 'Submit Request' }), `() => document.body.innerText.includes('Submitting...')`);
  check(`${tag}: Submitting... shows at once`, busyMs >= 0 && busyMs < 150, `${busyMs} ms`);
  await page.getByText('Your song has been added to the queue!').waitFor({ timeout: 3000 }).catch(() => {});
  const sent = station.calls.find((c) => c.path === '/song-requests');
  check(`${tag}: request sent with every field`, sent && sent.body.station_id === station.SID && sent.body.requester_name === 'Moshe'
    && sent.body.song_title === 'Song number 12' && sent.body.song_artist === 'Artist 3' && sent.body.requester_message === 'For my mother', JSON.stringify(sent?.body));
  check(`${tag}: success message with the match and wait`, await page.getByText('Your song has been added to the queue!', { exact: false }).count() === 1
    && await page.getByText(/~2 songs \(~7 min\)/).count() === 1);
  check(`${tag}: form clears after sending`, await page.getByPlaceholder('Enter your name').inputValue() === '');
  await page.getByPlaceholder('Enter your name').fill('Moshe');
  await page.getByPlaceholder('What song would you like to hear?').fill('please fail');
  await page.getByRole('button', { name: 'Submit Request' }).click();
  await page.getByText('Failed to submit request. Please try again.').waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: a failed request says so`, await page.getByText('Failed to submit request. Please try again.').count() === 1);

  // Listener profile: PIN login
  const pin = page.getByPlaceholder('Enter 6-digit PIN');
  await pin.fill('12a3');
  check(`${tag}: PIN box keeps digits only`, await pin.inputValue() === '123');
  await page.getByRole('button', { name: 'Login' }).click();
  check(`${tag}: short PIN refused`, await page.getByText('PIN must be 6 digits').count() === 1 && !station.calls.some((c) => c.path === '/crm/login'));
  await pin.fill('999999');
  await page.getByRole('button', { name: 'Login' }).click();
  await page.getByText('Invalid PIN').waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: wrong PIN says Invalid PIN`, await page.getByText('Invalid PIN').count() === 1);
  await pin.fill('123456');
  await page.getByRole('button', { name: 'Login' }).click();
  await page.getByText('Welcome, Test Listener!').waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: right PIN logs in`, await page.getByText('Welcome, Test Listener!').count() === 1 && await page.getByText('Kumzitz lover', { exact: false }).count() === 1);
  // Rate the song on air and favourite it; counts refresh
  await page.getByRole('button', { name: '★' }).nth(3).click();
  await page.getByText('1 songs rated').waitFor({ timeout: 3000 }).catch(() => {});
  const rate = station.calls.filter((c) => c.path === '/crm/rate').pop();
  check(`${tag}: 4 stars sent for the song on air`, rate?.body.rating === 4 && rate.body.asset_id === station.playing().asset_id, JSON.stringify(rate?.body));
  check(`${tag}: profile counts refresh after rating`, await page.getByText('1 songs rated').count() === 1);
  await page.getByTitle('Favorite').click();
  await page.getByText('1 favorites').waitFor({ timeout: 3000 }).catch(() => {});
  const fav = station.calls.filter((c) => c.path === '/crm/rate').pop();
  check(`${tag}: favourite sent and counted`, fav?.body.is_favorite === true && fav.body.rating === 4 && await page.getByText('1 favorites').count() === 1);
  // V1-134: rating and favourite reset when the track changes
  const lit = () => page.evaluate(() => [...document.querySelectorAll('button')].filter((b) => (b.textContent === '★' && b.className.includes('text-yellow-400')) || (b.textContent === '♥' && b.className.includes('text-red-500'))).length);
  check(`${tag}: rating shown lit before the change`, (await lit()) === 5, `${await lit()} lit`);
  station.goOnAir(station.pending()[0].asset_id);
  await sleep(600);
  check(`${tag}: rating and favourite reset when the track changes`, (await lit()) === 0, `${await lit()} lit`);
  // Raffle
  await page.getByRole('button', { name: 'Enter Raffle' }).click();
  await page.getByText('Entered!').waitFor({ timeout: 3000 }).catch(() => {});
  check(`${tag}: Enter Raffle -> Entered!`, await page.getByText('Entered!').count() === 1 && station.calls.some((c) => c.path === '/crm/raffles/rf1/enter'));
  // Logout, then register
  await page.getByRole('button', { name: 'Logout' }).click();
  check(`${tag}: Logout goes back to the PIN box`, await pin.count() === 1);
  await page.getByRole('button', { name: 'Register' }).first().click();
  await page.getByRole('button', { name: 'Register' }).last().click();
  check(`${tag}: register needs a name`, await page.getByText('Name is required').count() === 1);
  await page.getByPlaceholder('Your name', { exact: true }).fill('Rivka');
  await page.getByPlaceholder('Phone', { exact: true }).fill('555-0100');
  await page.getByPlaceholder('Email', { exact: true }).fill('rivka@example.com');
  await page.getByRole('button', { name: 'Register' }).last().click();
  await page.getByText('Welcome, Rivka!').waitFor({ timeout: 3000 }).catch(() => {});
  const reg = station.calls.find((c) => c.path === '/crm/register');
  check(`${tag}: register sends name, phone, email and logs in`, reg?.body.name === 'Rivka' && reg.body.phone === '555-0100' && reg.body.email === 'rivka@example.com'
    && await page.getByText('Welcome, Rivka!').count() === 1, JSON.stringify(reg?.body));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Network: socket drop, polling fall-back, back from background, overdue, offline ──
async function network(browser) {
  const tag = 'listen network';
  const station = makeStation();
  const { ctx, np } = await listenContext(browser, { viewport: { width: 1280, height: 900 } }, station);
  const { page, errors } = await openListen(ctx, station.SID);
  await page.getByText('On Air', { exact: true }).waitFor();
  await listenBtn(page).click();
  await timeUntil(page, `() => window.__audible().length === 1`, 3000);
  const npReads = () => station.calls.filter((c) => c.path === `/now-playing/${station.SID}`).length;

  // A dropped socket comes back by itself
  const before = np.opened.length;
  np.dropAll();
  const t0 = Date.now();
  while (np.opened.length === before && Date.now() - t0 < 5000) await sleep(50);
  check(`${tag}: a dropped socket reconnects within 2 s`, np.opened.length > before && Date.now() - t0 < 2000, `${Date.now() - t0} ms`);
  station.goOnAir(station.pending()[0].asset_id);
  const shown = await timeUntil(page, `() => document.body.innerText.includes(${JSON.stringify(station.byId[station.playing().asset_id].title)})`, 3000);
  check(`${tag}: updates arrive on the new socket`, shown >= 0, `${shown} ms`);

  // Network loss mid-song: the player carries on, and is at the live position after
  await ctx.setOffline(true);
  const during = await watchDecks(page, 4000, 250);
  await ctx.setOffline(false);
  const silent = during.filter((s) => !s.decks.some((d) => d.playing && d.gain > 0.05)).length;
  check(`${tag}: no silence while the network is down for 4 s`, silent === 0, `${silent} of ${during.length} samples silent`);
  await sleep(1500);
  const cur = (await page.evaluate(() => window.__audible()))[0];
  const live = (Date.now() - new Date(station.playing().started_at).getTime()) / 1000;
  check(`${tag}: still at the live position after the network returns`, !!cur && Math.abs(cur.t - live) < 2.5, cur ? `${cur.src}@${cur.t} live ${live.toFixed(1)}` : 'silent');

  // Back from the background after more than 5 s: re-reads now playing and reconnects
  const reads0 = npReads();
  const opened0 = np.opened.length;
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { value: true, configurable: true }); Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true }); document.dispatchEvent(new Event('visibilitychange')); });
  await sleep(5500);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { value: false, configurable: true }); Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true }); document.dispatchEvent(new Event('visibilitychange')); });
  await sleep(1000);
  check(`${tag}: back from the background re-reads now playing`, npReads() > reads0, `${reads0} -> ${npReads()}`);
  check(`${tag}: back from the background opens a fresh socket`, np.opened.length > opened0);

  // Overdue: the item shown has ended and nothing came (a dead socket with no close)
  const cur2 = station.playing();
  const a = station.byId[cur2.asset_id];
  cur2.started_at = new Date(Date.now() - (a.duration + 7) * 1000).toISOString();
  station.push();
  const reads1 = npReads();
  const overdue = await (async () => { const s = Date.now(); while (npReads() === reads1 && Date.now() - s < 10000) await sleep(100); return Date.now() - s; })();
  check(`${tag}: an overdue item makes the page re-read now playing`, npReads() > reads1, `${overdue} ms`);

  // Socket refused over and over: falls back to polling the REST endpoint
  await ctx.unrouteAll({ behavior: 'ignoreErrors' }).catch(() => {});
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

async function pollingFallback(browser) {
  const tag = 'listen polling';
  const station = makeStation();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await ctx.addInitScript(AUDIO_PROBE);
  await routeApi(ctx, station);
  let attempts = 0;
  const at = [];
  await ctx.routeWebSocket(/\/ws\/now-playing\//, (ws) => { attempts++; at.push(Date.now()); ws.close({ code: 1011 }).catch(() => {}); });
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const { page, errors } = await openListen(ctx, station.SID);
  const reads = () => station.calls.filter((c) => c.path === `/now-playing/${station.SID}`).length;
  const t0 = Date.now();
  while (reads() === 0 && Date.now() - t0 < 45000) await sleep(200);
  check(`${tag}: after repeated socket failures it polls now playing`, reads() > 0, `${attempts} socket attempts, first poll after ${((Date.now() - t0) / 1000).toFixed(1)} s`);
  await page.getByText('On Air', { exact: true }).waitFor({ timeout: 4000 }).catch(() => {});
  check(`${tag}: polled now playing is shown`, await page.getByText(station.byId[station.playing().asset_id].title, { exact: true }).count() === 1);
  // V1-125: back-off 1, 2, 4, 8, 16 s between attempts, then polls every 3 s
  const gaps = at.slice(1).map((t, i) => (t - at[i]) / 1000);
  const want = [1, 2, 4, 8, 16];
  check(`${tag}: reconnects back off 1, 2, 4, 8, 16 s`, gaps.length >= 5 && want.every((w, i) => Math.abs(gaps[i] - w) < 0.6), gaps.map((g) => g.toFixed(1)).join(', '));
  const r0 = reads();
  await sleep(6500);
  const polls = reads() - r0;
  check(`${tag}: then polls every 3 s`, polls >= 2 && polls <= 3, `${polls} polls in 6.5 s`);
  const stillPolling = attempts;
  await sleep(1000);
  check(`${tag}: stays on polling (no more socket attempts)`, attempts === stillPolling);
  // Hidden and shown again: tries the socket again
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { value: true, configurable: true }); document.dispatchEvent(new Event('visibilitychange')); });
  await sleep(5500);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { value: false, configurable: true }); document.dispatchEvent(new Event('visibilitychange')); });
  await sleep(800);
  check(`${tag}: shown again after the background, it tries the socket again`, attempts > stillPolling, `${stillPolling} -> ${attempts}`);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Station switch: /listen/A -> /listen/B stops A ─────────────
async function stationSwitch(browser) {
  const tag = 'listen station switch';
  const station = makeStation();
  const { ctx, np } = await listenContext(browser, { viewport: { width: 1280, height: 900 } }, station);
  const { page, errors } = await openListen(ctx, station.SID);
  await page.getByText('On Air', { exact: true }).waitFor();
  await listenBtn(page).click();
  await timeUntil(page, `() => window.__audible().length === 1`, 3000);
  await page.evaluate(() => { history.pushState({}, '', '/listen/st-2'); dispatchEvent(new PopStateEvent('popstate')); });
  await page.getByRole('heading', { name: 'Second Feed' }).waitFor({ timeout: 5000 }).catch(() => {});
  await sleep(1500);
  const heard = await audibleSrc(page);
  check(`${tag}: the first station is not heard after moving to the second`, heard.length === 0, heard.join());
  const toOld = np.sockets.filter((s) => s.sid === station.SID && s.open).length;
  check(`${tag}: the first station's socket is closed`, toOld === 0, `${toOld} open`);
  // A late message for the old station must not bring it back
  station.goOnAir(station.pending()[0].asset_id);
  await sleep(1000);
  check(`${tag}: a late message for the first station is not played`, (await audibleSrc(page)).length === 0);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── The hourly time check: heard at its time, no hole, control room and Listen ──
// The fake station plays the role of the server: the time check is due at T (as
// at hh:00:00), queued with its exact time, and the server announces it 0.25 s
// after T (the scheduler starts it at T, then the message travels).
async function timeCheck(browser, where, { preload = true, clock = 0 } = {}) {
  const tag = `time check (${where}${preload ? '' : ', not pre-loaded'}${clock ? `, device clock ${clock > 0 ? '+' : ''}${clock / 1000} s` : ''})`;
  const initScripts = clock ? [[skewClock, clock]] : [];
  const station = makeStation();
  if (!preload) {
    // The time check was queued after the last announcement: the page was told the next song
    const base = station.nowPlaying;
    station.nowPlaying = (extra) => { const d = base(extra); if (d.asset_id !== 'tc') d.next_asset = { id: 'm40', title: 'x', audio_url: '/__tone/m40.wav', cue_in: 0, replay_gain_db: 0 }; return d; };
  }
  const T = Date.now() + 9000;
  // The time check is the next item and is due before the song ends (the server's
  // prediction sends it as next_asset, so listeners pre-load it)
  station.pending().forEach((e) => { e.position += 1; });
  station.add({ asset_id: 'tc', position: 2, source: 'block', preempt_at: new Date(T).toISOString(), hard_timed: true });
  const opts = { viewport: { width: 1440, height: 900 } };
  let page, errors, ctx;
  if (where === 'Listen') {
    ({ ctx } = await listenContext(browser, opts, station, { initScripts }));
    ({ page, errors } = await openListen(ctx, station.SID));
    await page.getByText('On Air', { exact: true }).waitFor();
    await listenBtn(page).click();
  } else {
    ctx = await browser.newContext(opts);
    await ctx.addInitScript(() => { localStorage.setItem('access_token', 'test'); localStorage.setItem('refresh_token', 'test'); });
    await ctx.addInitScript(AUDIO_PROBE);
    for (const sc of initScripts) await ctx.addInitScript(...sc);
    await routeApi(ctx, station);
    await routeNowPlaying(ctx, station);
    await routeSiteSocket(ctx);
    page = await ctx.newPage();
    errors = [];
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(`${BASE}/admin/dashboard`);
    await page.getByTestId('queue-row').first().waitFor();
    await page.locator('button:visible', { hasText: 'Listen' }).first().click();
  }
  const songId = station.playing().asset_id;
  await timeUntil(page, `() => window.__audible().some(d => d.src === '${songId}')`, 4000);
  // The server: at T the time check goes on air, announced 0.25 s later
  const wait = T + 250 - Date.now();
  setTimeout(() => station.goOnAir('tc', { started_at: new Date(T).toISOString() }), wait);
  const samples = await watchDecks(page, T + 4000 - Date.now(), 25);
  const rel = (s) => (s.now - clock - T) / 1000;
  const first = samples.find((s) => s.decks.some((d) => d.src === 'tc' && d.playing && d.gain > 0.05));
  const heardAt = first ? rel(first) : null;
  let gap = 0, longest = 0;
  for (const s of samples) {
    if (rel(s) < -2) continue;
    const any = s.decks.some((d) => d.playing && d.gain > 0.05);
    gap = any ? 0 : gap + 0.025;
    longest = Math.max(longest, gap);
  }
  const songAt = samples.filter((s) => Math.abs(rel(s)) < 0.05).map((s) => s.decks.find((d) => d.src === songId)?.gain ?? 0);
  const songAfter = samples.filter((s) => rel(s) > 1.5).map((s) => s.decks.find((d) => d.src === songId && d.playing)?.gain ?? 0);
  check(`${tag}: time check heard within 1 s of its time`, heardAt !== null && heardAt >= -0.1 && heardAt <= 1, heardAt === null ? 'never' : `${heardAt.toFixed(2)} s after`);
  check(`${tag}: no hole before the time check`, longest < 0.5, `longest silence ${longest.toFixed(2)} s`);
  check(`${tag}: the song is out once the time check plays`, songAfter.every((g) => g < 0.05), songAfter.slice(0, 5).join());
  if (where === 'control room') {
    check(`${tag}: the song is faded down by the time check's time`, songAt.every((g) => g < 0.5), songAt.join());
  } else {
    // Informational: the Listen page has no pre-fade (it does not know the queue)
    console.log(`info   ${tag}: song level at the due time ${songAt.join() || 'n/a'}`);
  }
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Player: remembered volume, drift correction, crossfade with no animation frames ──
async function playerExtras(browser) {
  const tag = 'listen player';
  const station = makeStation();
  const { ctx } = await listenContext(browser, { viewport: { width: 1280, height: 900 } }, station);
  const { page, errors } = await openListen(ctx, station.SID);
  await page.getByText('On Air', { exact: true }).waitFor();
  await listenBtn(page).click();
  await timeUntil(page, `() => window.__audible().length === 1`, 3000);
  await page.locator('input[type=range]').fill('0.35');
  await page.getByRole('button', { name: 'Mute' }).click();
  await page.reload();
  await page.getByText('On Air', { exact: true }).waitFor();
  await listenBtn(page).click();
  await timeUntil(page, `() => window.__decks().some(d => d.playing)`, 3000);
  await sleep(300);
  check(`${tag}: volume and mute remembered across a reload (V1-127)`, await page.getByText('35%').count() === 1
    && await page.getByRole('button', { name: 'Unmute' }).count() === 1 && (await page.evaluate(() => window.__outLevel())) < 0.01, `level ${await page.evaluate(() => window.__outLevel())}`);
  await page.getByRole('button', { name: 'Unmute' }).click();

  // V1-126: a player that fell more than 5 s behind is put back on the live position
  // (checked every 10 s, not within 15 s of a track change); one ahead is left alone
  await sleep(15500);
  const pos = () => page.evaluate(() => { const a = window.__deckEls().find((x) => !x.paused); return a ? a.currentTime : null; });
  const live = () => (Date.now() - new Date(station.playing().started_at).getTime()) / 1000;
  await page.evaluate(() => { const a = window.__deckEls().find((x) => !x.paused); a.currentTime = Math.max(0, a.currentTime - 8); });
  const behindAt = Date.now();
  let caught = -1;
  while (Date.now() - behindAt < 12000) { const p = await pos(); if (p !== null && Math.abs(p - live()) < 2) { caught = Date.now() - behindAt; break; } await sleep(250); }
  check(`${tag}: 8 s behind is corrected within one 10 s check (V1-126)`, caught >= 0 && caught <= 10600, `${caught} ms`);
  await page.evaluate(() => { const a = window.__deckEls().find((x) => !x.paused); a.currentTime += 7; });
  await sleep(11000);
  const ahead = (await pos()) - live();
  check(`${tag}: a player ahead of the server is not pulled back (V1-126)`, ahead > 5, `${ahead.toFixed(1)} s ahead`);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();

  // V1-128: the crossfade runs on a timer, so it happens on time with no animation
  // frames at all (a background tab or a locked phone)
  const st2 = makeStation();
  const cur = st2.playing();
  const dur = st2.byId[cur.asset_id].duration;
  cur.started_at = new Date(Date.now() - (dur - 9) * 1000).toISOString();
  const { ctx: c2 } = await listenContext(browser, { viewport: { width: 1280, height: 900 } }, st2);
  const { page: p2 } = await openListen(c2, st2.SID);
  await p2.getByText('On Air', { exact: true }).waitFor();
  await listenBtn(p2).click();
  await timeUntil(p2, `() => window.__audible().length === 1`, 3000);
  await p2.evaluate(() => { window.requestAnimationFrame = () => 0; });
  const nextId = st2.pending()[0].asset_id;
  const crossAt = new Date(cur.started_at).getTime() + (dur - 3) * 1000;
  const heard = await timeUntil(p2, `() => window.__audible().some(d => d.src === '${nextId}')`, 12000);
  const late = heard < 0 ? null : (Date.now() - crossAt) / 1000;
  check(`${tag}: crossfade on time with no animation frames (V1-128)`, late !== null && Math.abs(late) < 1.2, late === null ? 'never' : `${late.toFixed(2)} s from the crossfade point`);
  await c2.close();
}

// Listen pressed on a device whose clock is off: the item must start where the station is
async function joinWithSkew(browser, off) {
  const tag = `device clock ${off > 0 ? '+' : ''}${off / 1000} s`;
  const station = makeStation();
  const { ctx } = await listenContext(browser, { viewport: { width: 1280, height: 900 } }, station, { initScripts: [[skewClock, off]] });
  const { page, errors } = await openListen(ctx, station.SID);
  await page.getByText('On Air', { exact: true }).waitFor();
  await sleep(500);
  await listenBtn(page).click();
  await timeUntil(page, `() => window.__audible().length === 1`, 3000);
  await sleep(500);
  const d = (await page.evaluate(() => window.__audible()))[0];
  const live = (Date.now() - new Date(station.playing().started_at).getTime()) / 1000;
  check(`${tag}: Listen joins the item at the station's position, not the device clock's`, !!d && Math.abs(d.t - live) < 2.5, d ? `at ${d.t}, live ${live.toFixed(1)}` : 'silent');
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── Server stream mode ─────────────
async function streamMode(browser, stream) {
  const tag = 'listen stream';
  const station = makeStation();
  const realNp = station.nowPlaying;
  station.nowPlaying = (extra) => ({ ...realNp(extra), stream_url: stream.url(station.SID) });
  station.playout = { stations: { [station.SID]: { mode: 'server', engine: true, engine_ok: true, in_sync: true } }, default: { mode: 'browser' } };
  const { ctx } = await listenContext(browser, { viewport: { width: 1280, height: 900 } }, station);
  const toneReqs = [];
  ctx.on('request', (r) => { if (r.url().includes('/__tone/')) toneReqs.push(r.url()); });
  const { page, errors } = await openListen(ctx, station.SID);
  await page.getByText('On Air', { exact: true }).waitFor();
  await sleep(300);
  check(`${tag}: preconnect to the stream server before Listen`, await page.locator(`link[rel=preconnect][href="${stream.origin}"]`).count() === 1);
  check(`${tag}: badge says Server stream`, /Server stream/.test(await page.getByTestId('playout-badge').textContent()));
  const el = () => page.evaluate(() => {
    const a = document.querySelector('audio');
    const b = a.buffered;
    return { src: a.src, paused: a.paused, t: a.currentTime, lag: b.length ? b.end(b.length - 1) - a.currentTime : null, volume: a.volume };
  });
  const heard = await timeClick(page, listenBtn(page), `() => { const a = document.querySelector('audio'); return !a.paused && a.currentTime > 0.05; }`, 5000);
  check(`${tag}: stream heard within 1.5 s of Listen (S-7)`, heard >= 0 && heard <= 1500, `${heard} ms`);
  let s = await el();
  check(`${tag}: played through Media Source (starts on first data)`, s.src.startsWith('blob:'), s.src);
  await sleep(4000);
  s = await el();
  check(`${tag}: stays about 1 s behind live (S-7)`, s.lag !== null && s.lag < 1.6, `${s.lag?.toFixed(2)} s behind`);
  check(`${tag}: LIVE tag and On Air shown`, await page.getByText('LIVE', { exact: true }).count() === 1 && await page.getByText('On Air', { exact: true }).count() === 1);
  check(`${tag}: built-in player silent and next item not downloaded`, (await audibleSrc(page)).length === 0 && toneReqs.length === 0, toneReqs.join());
  stream.stall(4000);
  await sleep(4500);
  await sleep(1500);
  s = await el();
  check(`${tag}: after a 4 s stall it jumps back near live`, s.lag !== null && s.lag < 2.6 && !s.paused, `${s.lag?.toFixed(2)} s behind`);
  await page.locator('input[type=range]').fill('0.3');
  await sleep(200);
  check(`${tag}: volume slider sets the stream level`, Math.abs((await el()).volume - 0.3) < 0.02);
  await page.getByRole('button', { name: 'Mute' }).click();
  await sleep(200);
  check(`${tag}: Mute silences the stream`, (await el()).volume === 0);
  await page.getByRole('button', { name: 'Unmute' }).click();
  // V1-131: the engine or Icecast restarts and closes the stream: the page carries on with a plain element that reconnects
  const connects = stream.stats.connects;
  stream.dropAll();
  const back = await timeUntil(page, `() => { const a = document.querySelector('audio'); return a.src.startsWith('http') && !a.paused && a.currentTime > 0.2; }`, 8000);
  check(`${tag}: a closed stream reconnects on a plain element (V1-131)`, back >= 0 && stream.stats.connects > connects, `${back} ms, ${stream.stats.connects - connects} new connections`);
  await stopBtn(page).click();
  await sleep(300);
  await listenBtn(page).click();
  await sleep(500);
  // V1-132: a long session drops audio older than 20 s from the buffer
  await sleep(46000);
  const buf = await page.evaluate(() => { const a = document.querySelector('audio'); const b = a.buffered; return { start: b.length ? b.start(0) : null, t: a.currentTime, mse: a.src.startsWith('blob:') }; });
  check(`${tag}: after 46 s only about the last 20-40 s stay buffered (V1-132)`, buf.mse && buf.start !== null && buf.start > 0 && buf.t - buf.start <= 41, JSON.stringify(buf));
  await stopBtn(page).click();
  await sleep(300);
  s = await el();
  check(`${tag}: Stop stops the stream`, s.paused && !s.src.startsWith('blob:'), JSON.stringify(s));
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();

  // Browsers without MSE for MP3 (Safari on iPhone) get the plain element
  {
    const st2 = makeStation();
    const base2 = st2.nowPlaying;
    st2.nowPlaying = (extra) => ({ ...base2(extra), stream_url: stream.url(st2.SID) });
    const { ctx: c2 } = await listenContext(browser, { ...devices['iPhone 13'] }, st2, { initScripts: [() => { delete window.MediaSource; window.MediaSource = undefined; }] });
    const { page: p2, errors: e2 } = await openListen(c2, st2.SID);
    await p2.getByText('On Air', { exact: true }).waitFor();
    const ms = await timeClick(p2, listenBtn(p2), `() => { const a = document.querySelector('audio'); return !a.paused && a.currentTime > 0.05; }`, 8000, 'tap');
    const src = await p2.evaluate(() => document.querySelector('audio').src);
    check(`${tag}: without MSE the plain <audio> plays the stream URL`, src === stream.url(st2.SID) && ms >= 0, `${src}, ${ms} ms`);
    // Chromium without MSE is only a stand-in for Safari: the timing is checked in real WebKit (e2e/webkit-player.py)
    console.log(`info   ${tag}: plain element (Chromium, MSE removed) first sound after ${ms} ms`);
    check(`${tag}: no script errors (no MSE)`, e2.length === 0, e2.join(' | '));
    await c2.close();
  }

  // A stream that fails falls back to the built-in player
  {
    const st3 = makeStation();
    const base3 = st3.nowPlaying;
    st3.nowPlaying = (extra) => ({ ...base3(extra), stream_url: stream.missing });
    const { ctx: c3 } = await listenContext(browser, { viewport: { width: 1280, height: 900 } }, st3);
    const { page: p3, errors: e3 } = await openListen(c3, st3.SID);
    await p3.getByText('On Air', { exact: true }).waitFor();
    const fb = await timeClick(p3, listenBtn(p3), `() => window.__audible().some(d => d.src === '${st3.playing().asset_id}')`, 6000);
    check(`${tag}: a broken stream falls back to the built-in player`, fb >= 0, `${fb} ms`);
    check(`${tag}: fall-back heard within 1.5 s (S-7)`, fb >= 0 && fb <= 1500, `${fb} ms`);
    check(`${tag}: no script errors (broken stream)`, e3.length === 0, e3.join(' | '));
    await c3.close();
  }
}
const browser = await chromium.launch();
const run = (name) => !ONLY || ONLY.split(',').includes(name);
try {
  if (run('basics')) {
    await basics(browser, { viewport: { width: 1280, height: 900 } }, 'listen desktop');
    await basics(browser, { ...devices['iPhone 13'] }, 'listen iPhone');
  }
  if (run('forms')) await forms(browser);
  if (run('network')) await network(browser);
  if (run('polling')) await pollingFallback(browser);
  if (run('switch')) await stationSwitch(browser);
  if (run('player')) await playerExtras(browser);
  if (run('clock')) {
    // V2-121: a device clock 20 s off must not move songs or the time check
    for (const off of [20000, -20000]) {
      await joinWithSkew(browser, off);
      await timeCheck(browser, 'Listen', { clock: off });
      await timeCheck(browser, 'control room', { clock: off });
    }
  }
  if (run('timecheck')) {
    await timeCheck(browser, 'Listen');
    await timeCheck(browser, 'Listen', { preload: false });
    await timeCheck(browser, 'control room');
    await timeCheck(browser, 'control room', { preload: false });
  }
  if (run('stream')) {
    const stream = await startStreamServer(STREAM_PORT);
    try { await streamMode(browser, stream); } finally { await stream.close(); }
  }
} finally {
  await browser.close();
}
process.exit(done());
