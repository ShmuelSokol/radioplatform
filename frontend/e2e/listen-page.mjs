/**
 * Browser check for the public Listen page (/listen/:station) against a fake backend
 * and a fake now-playing channel, desktop and iPhone size. Covers the buttons, the
 * now-playing card, the phone's lock-screen controls, song requests, the listener
 * profile, network loss and coming back to the tab, the server-stream mode (with a
 * small MP3 stream server on STREAM_PORT), the hourly time check heard on time, and
 * short items and hard timed items handed over on the second (ONLY=handover).
 * Parts: basics forms network polling switch player elapsed clock timecheck stream handover.
 * The socket back-off and polling part runs on a fake clock (page.clock).
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

/** Init script: counts play() calls made while a click is being handled (browsers,
 *  Safari above all, allow sound only from inside the press). React handles the
 *  click at its root, between the window's capture and bubble listeners. */
function CLICK_PROBE() {
  window.__inClick = false;
  window.__playsInClick = 0;
  window.__playedInClick = [];
  addEventListener('click', () => { window.__inClick = true; }, true);
  addEventListener('click', () => { window.__inClick = false; });
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...a) { if (window.__inClick) { window.__playsInClick++; window.__playedInClick.push(this); } return play.apply(this, a); };
}

async function listenContext(browser, contextOpts, station, { initScripts = [], delay } = {}) {
  const ctx = await browser.newContext(contextOpts);
  await ctx.addInitScript(AUDIO_PROBE);
  await ctx.addInitScript(CLICK_PROBE);
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
  const inClick = await page.evaluate(() => window.__playsInClick);
  check(`${tag}: Listen Live starts the player inside the click (F-LISTEN-2)`, inClick >= 1, `${inClick} play() calls in the click`);
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
  check(`${tag}: Stop silences at once (F-LISTEN-3)`, (await audibleSrc(page)).length === 0);
  // Stay stopped a while, so "live" and "where it stopped" are 3 s apart
  await sleep(3000);
  check(`${tag}: stays silent while stopped (F-LISTEN-3)`, (await audibleSrc(page)).length === 0);
  await listenBtn(page).click();
  const again = await timeUntil(page, `() => window.__audible().length === 1`, 3000);
  check(`${tag}: Listen again is heard`, again >= 0, `${again} ms`);
  await sleep(300);
  const rejoined = (await page.evaluate(() => window.__audible()))[0];
  const liveAgain = (Date.now() - new Date(station.playing().started_at).getTime()) / 1000;
  check(`${tag}: Listen again joins live, not where Stop left it (F-LISTEN-3)`, !!rejoined && rejoined.src === station.playing().asset_id && Math.abs(rejoined.t - liveAgain) < 2.5,
    rejoined ? `${rejoined.src}@${rejoined.t}, live ${liveAgain.toFixed(1)}` : 'silent');

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
  await page.getByRole('button', { name: 'Rate 4 stars' }).click();
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

// V1-125 on a fake clock (page.clock), so minutes of back-off and polling pass in moments
async function pollingFallback(browser) {
  const tag = 'listen polling';
  const station = makeStation({ startedAgo: 5 });
  // A long item, so it is not overdue (which re-reads and reconnects) while minutes pass
  station.byId[station.playing().asset_id].duration = 3600;
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  await ctx.addInitScript(AUDIO_PROBE);
  // Mix-overs the page heard
  await ctx.addInitScript(() => {
    window.__mixOvers = 0;
    window.addEventListener('kbr-mix-over', () => { window.__mixOvers++; });
  });
  await routeApi(ctx, station);
  let accept = false;
  const open = [];
  // When, on the page's (fake) clock, each now-playing socket was asked for. The
  // clock only moves in runFor() steps, so reading it here is exact to one step.
  const attemptsAt = [];
  let pg = null;
  await ctx.routeWebSocket(/\/ws\/now-playing\//, async (ws) => {
    attemptsAt.push(pg ? await pg.evaluate(() => Date.now()).catch(() => NaN) : NaN);
    if (!accept) { ws.close({ code: 1011 }).catch(() => {}); return; }
    open.push(ws);
    ws.send(JSON.stringify({ type: 'now_playing', data: station.nowPlaying() }));
  });
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  pg = page;
  await page.clock.install();
  await page.goto(`${BASE}/listen/${station.SID}`);
  await page.getByRole('heading', { name: 'Kol Berama' }).waitFor();
  const reads = () => station.calls.filter((c) => c.path === `/now-playing/${station.SID}`).length;
  const wsAt = async () => attemptsAt.slice();
  const now = () => page.evaluate(() => Date.now());
  // Move the page's clock on in small steps, giving the socket and fetches real time between
  const advance = async (ms, until = null) => {
    for (let left = ms; left > 0; left -= 200) {
      await page.clock.runFor(Math.min(200, left));
      await sleep(15);
      if (until && (await until())) return true;
    }
    return false;
  };
  await advance(45000, async () => reads() > 0);
  const at = await wsAt();
  check(`${tag}: after repeated socket failures it polls now playing (V1-125)`, reads() > 0, `${at.length} socket attempts`);
  await advance(400);
  check(`${tag}: polled now playing is shown`, await page.getByText(station.byId[station.playing().asset_id].title, { exact: true }).count() === 1);
  // Back-off 1, 2, 4, 8, 16 s between attempts (5 retries: the 30 s cap is never reached), then polling
  const gaps = at.slice(1).map((t, i) => (t - at[i]) / 1000);
  const want = [1, 2, 4, 8, 16];
  check(`${tag}: reconnects back off 1, 2, 4, 8, 16 s, then polls (V1-125)`, gaps.length === 5 && want.every((w, i) => Math.abs(gaps[i] - w) < 0.5), gaps.map((g) => g.toFixed(1)).join(', '));
  const pollStart = await now();
  const r0 = reads();
  await advance(9000);
  const polls = reads() - r0;
  check(`${tag}: then polls every 3 s (V1-125)`, polls >= 3 && polls <= 4, `${polls} polls in 9 s`);
  // Stays on polling; the socket is tried again only after a minute
  await advance(40000);
  check(`${tag}: stays on polling, no socket attempt for the next minute (V1-125)`, (await wsAt()).length === at.length, `${(await wsAt()).length - at.length} attempts`);
  await advance(15000, async () => (await wsAt()).length > at.length);
  const retry = ((await wsAt())[at.length] - pollStart) / 1000;
  check(`${tag}: while polling, the socket is tried again every 60 s (V1-125, fixed)`, Math.abs(retry - 60) < 1, `retry ${retry.toFixed(1)} s after polling began`);
  await advance(3500);
  check(`${tag}: a failed retry keeps polling`, reads() > r0 + polls + 10, `${reads() - r0} polls`);
  // Hidden and shown again: tries the socket at once
  const before = (await wsAt()).length;
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { value: true, configurable: true }); document.dispatchEvent(new Event('visibilitychange')); });
  await advance(5600);
  await page.evaluate(() => { Object.defineProperty(document, 'hidden', { value: false, configurable: true }); document.dispatchEvent(new Event('visibilitychange')); });
  await advance(200);
  check(`${tag}: shown again after the background, it tries the socket at once`, (await wsAt()).length > before, `${before} -> ${(await wsAt()).length}`);
  // The socket server is back: the next retry gets through, mix-overs arrive again, polling stops
  await advance(32000);
  accept = true;
  await advance(62000, async () => open.length > 0);
  check(`${tag}: once the socket server is back, a retry connects while visible (V1-125, fixed)`, open.length === 1);
  await advance(400);
  open[0]?.send(JSON.stringify({ type: 'mix_over', data: { asset_id: 'j1', audio_url: '/__tone/j1.wav' } }));
  await advance(400);
  check(`${tag}: mix-over messages arrive again after the socket is back`, (await page.evaluate(() => window.__mixOvers)) === 1);
  const r1 = reads();
  await advance(9000);
  check(`${tag}: polling stops once the socket is back`, reads() === r1, `${reads() - r1} polls in 9 s`);
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
  // The server: at T the time check goes on air; its message reaches pages 1.2 s later
  // (measured live 13:00Z and 14:00Z 2026-11-17: pages started it 1.1-1.8 s late
  // while they waited for it)
  const wait = T + 1200 - Date.now();
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
  const songAfter = samples.filter((s) => rel(s) > 2.7).map((s) => s.decks.find((d) => d.src === songId && d.playing)?.gain ?? 0);
  // Pre-loaded, the page starts it itself at its time; not pre-loaded, it waits for the message
  const within = preload ? 0.3 : 2;
  check(`${tag}: time check heard within ${within} s of its time`, heardAt !== null && heardAt >= -0.1 && heardAt <= within, heardAt === null ? 'never' : `${heardAt.toFixed(2)} s after`);
  check(`${tag}: no hole before the time check`, longest < 0.5, `longest silence ${longest.toFixed(2)} s`);
  check(`${tag}: the song is out once the time check plays`, songAfter.every((g) => g < 0.05), songAfter.slice(0, 5).join());
  if (where === 'control room') {
    check(`${tag}: the song is faded down by the time check's time`, songAt.every((g) => g < 0.5), songAt.join());
  } else {
    // Informational: the Listen page pre-fades only when next_asset carries cut_at
    console.log(`info   ${tag}: song level at the due time ${songAt.join() || 'n/a'}`);
  }
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// ── PR 157: short items are never crossfaded, a short item a little late starts from
//    its first word, a hard item starts here at its time (next_asset.cut_at) and the
//    page goes back to the server if the server never says it started ──
async function startListening(browser, station) {
  const { ctx } = await listenContext(browser, { viewport: { width: 1280, height: 900 } }, station);
  const { page, errors } = await openListen(ctx, station.SID);
  await page.getByText('On Air', { exact: true }).waitFor();
  await listenBtn(page).click();
  return { ctx, page, errors };
}
const heardIn = (s, id, min = 0.05) => s.decks.some((d) => d.src === id && d.playing && d.gain > min);
const deckOf = (s, id) => s.decks.find((d) => d.src === id && d.playing);
function longestSilence(samples, from = -Infinity, rel = (s) => s.at / 1000) {
  let gap = 0, longest = 0, last = null;
  for (const s of samples) {
    if (rel(s) < from) continue;
    const step = last === null ? 0 : (s.at - last) / 1000;
    last = s.at;
    gap = s.decks.some((d) => d.playing && d.gain > 0.05) ? 0 : gap + step;
    longest = Math.max(longest, gap);
  }
  return longest;
}

// A 7 s station ID on air, a song next. The server moves on at the file's end, or
// early at the item's crossfade point (3 s before the end): either way the song comes
// in only when the station ID has finished, never faded over it.
async function shortItemHandover(browser, serverAt) {
  const tag = `short item (server moves on ${serverAt === 'end' ? 'at its end' : 'at its crossfade point, 3 s early'})`;
  const station = makeStation();
  const cur = station.playing();
  cur.asset_id = 'j1';
  const dur = station.byId.j1.duration;
  const start = Date.now() + 500; // started just as the listener arrives
  cur.started_at = new Date(start).toISOString();
  const nextId = station.pending()[0].asset_id;
  const { ctx, page, errors } = await startListening(browser, station);
  await timeUntil(page, `() => window.__audible().some(d => d.src === 'j1')`, 4000);
  const moveAt = serverAt === 'end' ? start + dur * 1000 + 300 : start + (dur - 3) * 1000;
  setTimeout(() => station.goOnAir(nextId, { started_at: new Date(serverAt === 'end' ? start + dur * 1000 : moveAt).toISOString() }), moveAt - Date.now());
  const samples = await watchDecks(page, start + dur * 1000 + 2500 - Date.now(), 25);
  const first = samples.find((s) => heardIn(s, nextId));
  const idAt = first ? deckOf(first, 'j1') : null;
  check(`${tag}: the song is not heard until the station ID has finished`,
    !!first && (!idAt || idAt.t >= dur - 0.4), !first ? 'song never heard' : idAt ? `song in with ${(dur - idAt.t).toFixed(2)} s of the ID left` : 'after the ID');
  let both = 0;
  for (let i = 1; i < samples.length; i++) if (heardIn(samples[i], 'j1', 0.2) && heardIn(samples[i], nextId, 0.2)) both += (samples[i].at - samples[i - 1].at) / 1000;
  check(`${tag}: never crossfaded (heard together at most 0.2 s)`, both <= 0.2, `${both.toFixed(2)} s together`);
  const hole = longestSilence(samples);
  check(`${tag}: no hole between them`, hole < 0.5, `longest silence ${hole.toFixed(2)} s`);
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// The song on air, then the server's message that the time check went on air arrives
// `late` seconds after it started (and it was not pre-loaded). Up to 3 s late it is
// heard from its first word; later than that the page joins it where it is now.
async function lateShortItem(browser, late) {
  const tag = `time check announced ${late} s late`;
  const station = makeStation();
  const songId = station.playing().asset_id;
  const { ctx, page, errors } = await startListening(browser, station);
  await timeUntil(page, `() => window.__audible().some(d => d.src === '${songId}')`, 4000);
  await sleep(1500);
  const tcStart = Date.now() - late * 1000;
  station.goOnAir('tc', { started_at: new Date(tcStart).toISOString() });
  const nextId = station.pending()[0].asset_id;
  // The server moves on when the time check ends by its clock (5 s after it started)
  const serverEnd = tcStart + station.byId.tc.duration * 1000;
  setTimeout(() => station.goOnAir(nextId, { started_at: new Date(serverEnd).toISOString() }), Math.max(0, serverEnd - Date.now()));
  const samples = await watchDecks(page, 6500, 25);
  const first = samples.find((s) => heardIn(s, 'tc'));
  const from = first ? deckOf(first, 'tc').t : null;
  if (late <= 3) {
    check(`${tag}: heard from its first word`, from !== null && from < 0.6, from === null ? 'never heard' : `first heard at ${from} s into it`);
    const cut = samples.find((s) => heardIn(s, nextId, 0.2) && deckOf(s, 'tc') && deckOf(s, 'tc').t < station.byId.tc.duration - 0.4);
    check(`${tag}: the next item waits for it to finish`, !cut, cut ? `next item in at ${deckOf(cut, 'tc').t} s of the time check` : '');
  } else {
    check(`${tag}: joined where it is now, not from the top`, from !== null && from >= 3, from === null ? 'never heard' : `first heard at ${from} s into it`);
  }
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
}

// A hard time check due at T, pre-loaded as next_asset with cut_at = T. The server's
// message that it started arrives 1.5 s after T (`confirm`), or never: then the page
// goes back to the song after 4 s.
async function prearmedTimeCheck(browser, confirm) {
  const tag = `hard time check started on the page (${confirm ? 'server confirms 1.5 s late' : 'server never confirms'})`;
  const station = makeStation();
  const T = Date.now() + 9000;
  station.pending().forEach((e) => { e.position += 1; });
  station.add({ asset_id: 'tc', position: 2, source: 'block', preempt_at: new Date(T).toISOString(), hard_timed: true });
  const songId = station.playing().asset_id;
  const sent = station.nowPlaying();
  check(`${tag}: next_asset carries cut_at`, sent.next_asset?.id === 'tc' && sent.next_asset?.cut_at === new Date(T).toISOString(), JSON.stringify(sent.next_asset));
  const { ctx, page, errors } = await startListening(browser, station);
  await timeUntil(page, `() => window.__audible().some(d => d.src === '${songId}')`, 4000);
  await timeUntil(page, `() => window.__decks().some(d => d.src === 'tc' && !d.playing)`, 6000);
  if (confirm) setTimeout(() => station.goOnAir('tc', { started_at: new Date(T).toISOString() }), T + 1500 - Date.now());
  const samples = await watchDecks(page, T + (confirm ? 4000 : 7500) - Date.now(), 25);
  const rel = (s) => (s.now - T) / 1000;
  const first = samples.find((s) => heardIn(s, 'tc'));
  const heardAt = first ? rel(first) : null;
  check(`${tag}: heard at its time, before the server's message`, heardAt !== null && heardAt >= -0.1 && heardAt <= 0.35,
    heardAt === null ? 'never' : `${heardAt.toFixed(2)} s after its time`);
  // 0.3 s fade from the moment it starts (its deck starts playing). The 2 s pre-fade on
  // its own reaches silence only 0.4 s after the time, still above 0.01 0.34 s in.
  // (its deck coming up: the silent warm-up a few seconds before does not count)
  const started = samples.find((s) => deckOf(s, 'tc')?.gain > 0.001);
  const t0 = started ? rel(started) : 0;
  const songLate = samples.filter((s) => rel(s) >= t0 + 0.34 && rel(s) <= t0 + 1).map((s) => deckOf(s, songId)?.gain ?? 0);
  check(`${tag}: the song is out 0.3 s after the time check starts`, songLate.length > 0 && songLate.every((g) => g < 0.01),
    `started ${t0.toFixed(2)} s after its time; song level then ${songLate.slice(0, 4).join()}`);
  const hole = longestSilence(samples, -2, rel);
  check(`${tag}: no hole`, hole < 0.5, `longest silence ${hole.toFixed(2)} s`);
  if (confirm) {
    const restarts = samples.filter((s, i) => i > 0 && deckOf(s, 'tc') && deckOf(samples[i - 1], 'tc') && deckOf(s, 'tc').t + 0.5 < deckOf(samples[i - 1], 'tc').t);
    const songBack = samples.filter((s) => rel(s) > 0.5 && heardIn(s, songId));
    check(`${tag}: the server's message does not restart it or bring the song back`, restarts.length === 0 && songBack.length === 0,
      `${restarts.length} restarts, song heard in ${songBack.length} samples`);
  } else {
    const songBefore4 = samples.filter((s) => rel(s) > 0.5 && rel(s) < 3.8 && heardIn(s, songId));
    check(`${tag}: stays on the time check until the 4 s are up`, songBefore4.length === 0, `song heard in ${songBefore4.length} samples`);
    const back = samples.find((s) => rel(s) > 3.8 && heardIn(s, songId, 0.2));
    const live = back ? (back.now - new Date(station.playing().started_at).getTime()) / 1000 : 0;
    const pos = back ? deckOf(back, songId).t : null;
    check(`${tag}: goes back to the song on air, at its live position`, !!back && rel(back) <= 6 && Math.abs(pos - live) < 2,
      back ? `back ${rel(back).toFixed(2)} s after the time, at ${pos} s (live ${live.toFixed(1)})` : 'song never came back');
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
  const stored = await page.evaluate(() => ({ v: localStorage.getItem('radio_volume'), m: localStorage.getItem('radio_muted') }));
  check(`${tag}: volume and mute saved as radio_volume / radio_muted (V1-127)`, stored.v === '0.35' && stored.m === 'true', JSON.stringify(stored));
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

// ── V1-133: the elapsed time the page keeps for the item on air ─────────────
// It is not shown; the player uses it to put a listener who fell behind back on the
// live position (checked every 10 s). So: a player put 8 s behind lands on the live
// position (the time ticked every second), and after ends_at it lands on ends_at, not
// beyond (an overdue item counts its full length only).
async function elapsedTime(browser) {
  const tag = 'listen elapsed';
  const station = makeStation({ startedAgo: 40 });
  const cur = station.playing();
  station.byId[cur.asset_id].duration = 600; // the file is long; the server says it ends at +80 s
  const ENDS = 80;
  const base = station.nowPlaying;
  station.nowPlaying = (extra) => {
    const d = base(extra);
    if (d.asset_id === cur.asset_id) d.ends_at = new Date(new Date(cur.started_at).getTime() + ENDS * 1000).toISOString();
    return d;
  };
  const { ctx } = await listenContext(browser, { viewport: { width: 1280, height: 900 } }, station);
  const { page, errors } = await openListen(ctx, station.SID);
  await page.getByText('On Air', { exact: true }).waitFor();
  await listenBtn(page).click();
  await timeUntil(page, `() => window.__audible().length === 1`, 3000);
  const live = () => (Date.now() - new Date(cur.started_at).getTime()) / 1000;
  const pos = () => page.evaluate(() => { const a = window.__deckEls().find((x) => !x.paused); return a ? a.currentTime : null; });
  const putAt = (t) => page.evaluate((t) => { const a = window.__deckEls().find((x) => !x.paused); a.currentTime = t; }, t);
  // Wait out the 15 s after a track change in which the player is left alone
  await sleep(15500);
  await putAt((await pos()) - 8);
  const setAt = Date.now();
  let landed = null;
  while (Date.now() - setAt < 11000) {
    const p = await pos();
    if (p !== null && p > live() - 4) { landed = { p, live: live() }; break; }
    await sleep(100);
  }
  check(`${tag}: elapsed ticks every second from started_at (player put 8 s behind lands on the live position) (V1-133)`,
    !!landed && Math.abs(landed.p - landed.live) < 1.5, landed ? `landed at ${landed.p.toFixed(1)}, live ${landed.live.toFixed(1)}` : 'not corrected');
  // Past ends_at: elapsed stops there
  while (live() < ENDS + 3) await sleep(200);
  await putAt(20);
  const set2 = Date.now();
  let landed2 = null;
  while (Date.now() - set2 < 11000) {
    const p = await pos();
    if (p !== null && p > 30) { landed2 = { p, live: live() }; break; }
    await sleep(100);
  }
  check(`${tag}: after ends_at the elapsed time stops at the item's length, not beyond (V1-133)`,
    !!landed2 && Math.abs(landed2.p - ENDS) < 1, landed2 ? `landed at ${landed2.p.toFixed(1)}, ends at ${ENDS}, live ${landed2.live.toFixed(1)}` : 'not corrected');
  check(`${tag}: no script errors`, errors.length === 0, errors.join(' | '));
  await ctx.close();
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
  const streamInClick = await page.evaluate(() => window.__playedInClick.filter((a) => a === document.querySelector('audio')).length);
  check(`${tag}: Listen Live starts the stream inside the click (F-LISTEN-2)`, streamInClick >= 1, `${streamInClick} play() calls on the stream element in the click`);
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
    // Chromium without MSE is only a stand-in for Safari: the real WebKit run is e2e/webkit/listener-webkit.py
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
  if (run('elapsed')) await elapsedTime(browser);
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
  if (run('handover')) {
    // PR 157: short items, late short items, hard items started here at cut_at
    await shortItemHandover(browser, 'end');
    await shortItemHandover(browser, 'cross');
    await lateShortItem(browser, 2);
    await lateShortItem(browser, 3.6);
    await prearmedTimeCheck(browser, true);
    await prearmedTimeCheck(browser, false);
  }
  if (run('stream')) {
    const stream = await startStreamServer(STREAM_PORT);
    try { await streamMode(browser, stream); } finally { await stream.close(); }
  }
} finally {
  await browser.close();
}
process.exit(done());
