/**
 * Browser checks for checklist rows of the web player (useAudioEngine) that
 * e2e/audio-engine.mjs does not cover: drift correction (V1-126), the crossfade
 * timer in a background tab (V1-128), mix-over (V1-130), a device clock 30 s off
 * (V2-121), starting from idle (V1-51) and the hand-over after a short item (F-HANDOVER-8, -12, -13, -19).
 * Mounts the real hook in e2e/audio-harness (no backend), feeds it now-playing
 * states and samples the two decks inside the page every 25 ms.
 *
 *   cd frontend && node e2e/player-rows.mjs        # optional: one part name
 *
 * Parts: fromidle drift background mixover clock handover letfinish notletfinish fromtop.
 * Exits non-zero if any check fails. Takes about a minute and a half.
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createServer } from 'vite';
import { serveRange } from './lib/fake-station.mjs';

const { chromium } = createRequire(import.meta.url)('playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
  if (!ok) failures++;
};

// Test tones: asset id -> [frequency, seconds]
const TONES = { l: [500, 60], a: [440, 20], b: [660, 12], c: [880, 12], d: [550, 40], t: [1000, 5], j: [1200, 2] };
function wav(freq, seconds) {
  const rate = 8000, n = rate * seconds, buf = Buffer.alloc(44 + n * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8);
  buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(8000 * Math.sin((2 * Math.PI * freq * i) / rate)), 44 + i * 2);
  return buf;
}
const L = { id: 'l', dur: 60, cross: 57 };
const A = { id: 'a', dur: 20, cross: 17 };
const C = { id: 'c', dur: 12, cross: 9 };
const D = { id: 'd', dur: 40, cross: 37 };
// A 5 s time check (short: plays to its end, never crossfaded)
const T = { id: 't', dur: 5, cross: 4.7 };
const iso = (offsetS = 0) => new Date(Date.now() + offsetS * 1000).toISOString();

const only = process.argv[2];
const server = await createServer({ root, logLevel: 'error', server: { port: 5198, strictPort: false } });
await server.listen();
const base = server.resolvedUrls.local[0];
const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });

/** A harness page. opts.init: init scripts; opts.jingle: how the jingle request is answered. */
async function open(opts = {}) {
  const page = await browser.newPage();
  for (const [fn, arg] of opts.init ?? []) await page.addInitScript(fn, arg);
  page.__fetches = {};
  await page.route('**/__tone/*.wav', (r) => {
    const id = r.request().url().split('/').pop().replace('.wav', '');
    page.__fetches[id] = (page.__fetches[id] ?? 0) + 1;
    if (id === 'j' && opts.jingle === 'fail') return r.abort('accessdenied');
    return serveRange(r, wav(...TONES[id]));
  });
  await page.route('**/assets/*/audio-url', (r) => {
    const id = r.request().url().split('/').slice(-2)[0];
    return r.fulfill({ contentType: 'application/json', body: JSON.stringify({ url: `/__tone/${id}.wav` }) });
  });
  await page.goto(`${base}e2e/audio-harness/index.html`);
  await page.waitForFunction(() => typeof window.__set === 'function');
  page.set = (s) => page.evaluate((x) => window.__set(x), s);
  return page;
}

/** Start sampling the decks inside the page every `step` ms (page's monotonic clock). */
async function startSampling(page, step = 25) {
  await page.evaluate((step) => {
    const t0 = performance.now();
    window.__samples = [];
    window.__sampler = setInterval(() => {
      window.__samples.push({ at: (performance.now() - t0) / 1000, decks: window.__snap() });
    }, step);
  }, step);
  return performance.now();
}
async function samples(page) {
  return page.evaluate(() => { clearInterval(window.__sampler); return window.__samples; });
}
const heard = (s, id, min = 0.05) => s.decks.some((d) => d.src === id && d.playing && d.gain > min);
const deck = (s, id) => s.decks.find((d) => d.src === id && d.playing);
const firstAt = (ss, pred) => ss.find(pred)?.at ?? null;
const lastAt = (ss, pred) => [...ss].reverse().find(pred)?.at ?? null;
const together = (ss, a, b, min) => {
  let n = 0;
  for (let i = 1; i < ss.length; i++) if (heard(ss[i], a, min) && heard(ss[i], b, min)) n += ss[i].at - ss[i - 1].at;
  return n;
};
const audibleNow = async (page) => (await page.evaluate(() => window.__snap())).filter((d) => d.playing && d.gain > 0.05);

// V1-126: every 10 s a player more than 5 s behind the server jumps forward, never
// within 15 s of a track change; a player ahead of the server is not pulled back
async function drift() {
  const [behind, ahead] = await Promise.all([open(), open()]);
  await Promise.all([behind.set({ ...L, startedAt: iso() }), ahead.set({ ...L, startedAt: iso() })]);
  await sleep(3000);
  // the server's elapsed time: 40 s (player 37 s behind) and 1 s (player ahead)
  await Promise.all([behind.set({ ...L, startedAt: iso(-3), elapsed: 40 }), ahead.set({ ...L, startedAt: iso(-3), elapsed: 1 })]);
  await sleep(10500);
  const at13 = (await audibleNow(behind))[0];
  check('drift: no jump within 15 s of the track change (sync tick at 10 s skipped)', !!at13 && at13.src === 'l' && at13.t < 16,
    at13 ? `deck at ${at13.t} s, 13.5 s after the start` : 'silent');
  await sleep(10000);
  const at23 = (await audibleNow(behind))[0];
  check('drift: a player 5 s+ behind jumps to the server position at the next sync (20 s)', !!at23 && at23.src === 'l' && at23.t >= 39 && at23.t < 46,
    at23 ? `deck at ${at23.t} s, server said 40 s` : 'silent');
  const ahead23 = (await audibleNow(ahead))[0];
  check('drift: a player ahead of the server is not pulled back', !!ahead23 && ahead23.t > 20,
    ahead23 ? `deck at ${ahead23.t} s, server said 1 s` : 'silent');
  await Promise.all([behind.close(), ahead.close()]);
}

// V1-128: the crossfade check is a 100 ms timer, so it still runs when animation
// frames stop (background tab, locked screen)
async function background() {
  const page = await open({
    init: [[() => {
      window.requestAnimationFrame = () => 0; // frames never come, as in a background tab
      Object.defineProperty(document, 'visibilityState', { get: () => 'hidden' });
      Object.defineProperty(document, 'hidden', { get: () => true });
    }]],
  });
  await page.set({ ...A, nextId: 'b', startedAt: iso() });
  await startSampling(page, 50);
  await sleep(19500);
  const ss = await samples(page);
  const bIn = firstAt(ss, (s) => heard(s, 'b'));
  check('background tab: the crossfade into the next item starts on time with no animation frames', bIn !== null && bIn >= 15.8 && bIn <= 17.6,
    bIn === null ? 'never' : `next item in at ${bIn.toFixed(2)} s, crossfade point 17 s`);
  const last = ss[ss.length - 1];
  check('background tab: only the next item on air after the crossfade', heard(last, 'b') && !heard(last, 'a'),
    JSON.stringify(last.decks));
  await page.close();
}

// V1-130: mix-over plays once per message even when several listeners see it, is
// skipped when this tab's audio is not running, and a jingle that cannot be fetched
// leaves the item on air alone
const COUNT_STARTS = () => {
  window.__bufferStarts = 0;
  const start = AudioBufferSourceNode.prototype.start;
  AudioBufferSourceNode.prototype.start = function (...a) { window.__bufferStarts++; return start.apply(this, a); };
};
const SUSPENDED = () => {
  const Real = window.AudioContext;
  window.AudioContext = class extends Real {
    constructor(...a) { super(...a); void this.suspend(); }
    resume() { return Promise.resolve(); }
  };
};
async function mixOver() {
  const fire = (page, twice) => page.evaluate((twice) => {
    // one message, picked up by the engine; a second listener on the same message
    const detail = { url: '/__tone/j.wav', replay_gain_db: 0 };
    window.dispatchEvent(new CustomEvent('kbr-mix-over', { detail }));
    if (twice) window.dispatchEvent(new CustomEvent('kbr-mix-over', { detail }));
  }, twice);
  {
    const page = await open({ init: [[COUNT_STARTS]] });
    await page.set({ ...D, startedAt: iso() });
    await sleep(2000);
    const before = (await audibleNow(page))[0];
    await fire(page, true);
    await sleep(1500);
    const starts = await page.evaluate(() => window.__bufferStarts);
    check('mix-over: one message handled twice plays the jingle once', page.__fetches.j === 1 && starts === 1,
      `jingle fetched ${page.__fetches.j ?? 0}x, started ${starts}x`);
    const after = (await audibleNow(page))[0];
    check('mix-over: the item on air carries on under the jingle', !!before && !!after && after.src === 'd' && after.t > before.t + 1 && Math.abs(after.gain - before.gain) < 0.02,
      `${before?.src}@${before?.t}/${before?.gain} -> ${after?.src}@${after?.t}/${after?.gain}`);
    await page.close();
  }
  {
    const page = await open({ init: [[COUNT_STARTS], [SUSPENDED]] });
    await page.set({ ...D, startedAt: iso() });
    await sleep(1500);
    const state = await page.evaluate(() => window.__snap().length);
    await fire(page, false);
    await sleep(1500);
    const starts = await page.evaluate(() => window.__bufferStarts);
    check('mix-over: skipped when this tab is not listening (audio context not running)', !page.__fetches.j && starts === 0,
      `jingle fetched ${page.__fetches.j ?? 0}x, started ${starts}x, ${state} decks`);
    await page.close();
  }
  {
    const page = await open({ init: [[COUNT_STARTS]], jingle: 'fail' });
    await page.set({ ...D, startedAt: iso() });
    await sleep(2000);
    const before = (await audibleNow(page))[0];
    await fire(page, false);
    await sleep(2000);
    const after = await audibleNow(page);
    const starts = await page.evaluate(() => window.__bufferStarts);
    check('mix-over: a jingle that cannot be fetched leaves the item on air untouched',
      page.__fetches.j >= 1 && starts === 0 && after.length === 1 && after[0].src === 'd' && after[0].t > before.t + 1.5 && Math.abs(after[0].gain - before.gain) < 0.02,
      `jingle requested ${page.__fetches.j ?? 0}x, started ${starts}x; ${before?.src}@${before?.t}/${before?.gain} -> ${after.map((d) => `${d.src}@${d.t}/${d.gain}`).join(' ')}`);
    await page.close();
  }
}

// V2-121: a device clock 30 s fast or slow. The page notes the server's time (the
// release policy does this live); positions and the hard time check follow the server.
const SKEW = (ms) => {
  const Real = Date;
  const realNow = Real.now.bind(Real);
  class Skewed extends Real {
    constructor(...a) { if (a.length === 0) super(realNow() + ms); else super(...a); }
    static now() { return realNow() + ms; }
  }
  window.Date = Skewed;
};
async function clock() {
  for (const off of [30000, -30000]) {
    const tag = `device clock ${off > 0 ? '+' : '-'}30 s`;
    const page = await open({ init: [[SKEW, off]] });
    await page.evaluate((s) => window.__noteServerTime(s), iso());
    const due = iso(5);
    await page.set({ ...D, nextId: 't', startedAt: iso(-6), cut: { preempt_at: due, asset_id: 't' } });
    await startSampling(page);
    setTimeout(() => page.set({ ...T, nextId: 'd', startedAt: due }), 5400);
    await sleep(7000);
    const ss = await samples(page);
    const s1 = ss.find((s) => s.at >= 1.5);
    const d = s1 && deck(s1, 'd');
    // live position 6 s + 1.5 s; whole files sent without a seek start at 0 here, so
    // only a position near the server's counts
    check(`${tag}: the song plays where the station is, not ${off / 1000} s off`, !!d && Math.abs(d.t - 7.5) < 2,
      d ? `deck at ${d.t} s, station at 7.5 s` : JSON.stringify(s1?.decks));
    const tIn = firstAt(ss, (s) => heard(s, 't', 0.5));
    check(`${tag}: the hard time check is heard at its time (5 s)`, tIn !== null && tIn >= 4.7 && tIn <= 5.6,
      tIn === null ? 'never' : `heard at ${tIn.toFixed(2)} s`);
    const dOut = firstAt(ss, (s) => s.at > 1 && !heard(s, 'd', 0.5));
    check(`${tag}: the song is not faded early for it`, dOut !== null && dOut >= 2.5,
      dOut === null ? 'never faded' : `song below half level at ${dOut.toFixed(2)} s`);
    await page.close();
  }
}

/** Time check "t" on air from now; returns the samples of `watch` s after `change` runs. */
async function shortItem(change, watch = 7, init = []) {
  const page = await open({ init });
  const start = iso();
  await page.set({ ...T, nextId: 'd', startedAt: start });
  await startSampling(page);
  await change(page, start);
  await sleep(watch * 1000);
  const ss = await samples(page);
  const seeks = await page.evaluate(() => window.__seeks);
  await page.close();
  return { ss, seeks };
}

// F-HANDOVER-8: a 5 s time check is heard whole; the next item starts within 0.3 s
// of its end; overlap under 0.1 s. Server hands over at 4.7 s, and (late short item)
// at 3.3 s.
async function handover() {
  for (const [label, at] of [['server hands over at 4.7 s', 4.7], ['server moves on at 3.3 s', 3.3]]) {
    const { ss } = await shortItem(async (page) => {
      setTimeout(() => page.set({ ...D, nextId: 'a', startedAt: iso() }), at * 1000);
    });
    const tFirst = ss.find((s) => heard(s, 't'));
    const tLast = [...ss].reverse().find((s) => heard(s, 't'));
    const tMax = Math.max(...ss.map((s) => deck(s, 't')?.t ?? 0));
    check(`handover (${label}): the time check is heard whole`, !!tFirst && deck(tFirst, 't').t < 0.4 && tMax >= 4.75,
      `from ${tFirst ? deck(tFirst, 't').t : '-'} s to ${tMax} s of 5 s`);
    const dIn = firstAt(ss, (s) => heard(s, 'd'));
    const tOut = lastAt(ss, (s) => heard(s, 't'));
    check(`handover (${label}): the next item starts within 0.3 s of its end`, dIn !== null && tOut !== null && dIn - tOut <= 0.3,
      `time check last heard ${tOut?.toFixed(2)} s, next item in ${dIn?.toFixed(2)} s`);
    const both = together(ss, 't', 'd', 0.2);
    check(`handover (${label}): overlap under 0.1 s`, both < 0.1, `heard together ${both.toFixed(3)} s`);
  }
}

// F-HANDOVER-12: the let-finish wait ends at the item's end event or after its time
// left plus 0.1 s; a Next or another change during the wait cancels it
const NO_ENDED = () => {
  const add = HTMLMediaElement.prototype.addEventListener;
  HTMLMediaElement.prototype.addEventListener = function (type, ...rest) {
    if (type === 'ended') return undefined; // the end event never reaches the page
    return add.call(this, type, ...rest);
  };
};
async function letFinish() {
  for (const [label, init] of [['end event', []], ['no end event: time left + 0.1 s', [[NO_ENDED]]]]) {
    let sent = 0;
    const { ss } = await shortItem(async (page) => {
      await sleep(1000);
      sent = (await page.evaluate(() => window.__samples.at(-1)?.at)) ?? 1;
      await page.set({ ...D, nextId: 'a', startedAt: iso() });
    }, 6, init);
    const dIn = firstAt(ss, (s) => heard(s, 'd'));
    const tEnd = lastAt(ss, (s) => heard(s, 't'));
    check(`let-finish (${label}): the next item waits for the time check, then comes in at its end`,
      dIn !== null && tEnd !== null && dIn - sent > 3 && dIn - tEnd <= 0.3,
      `message at ${sent.toFixed(2)} s, time check last heard ${tEnd?.toFixed(2)} s, next in ${dIn?.toFixed(2)} s`);
  }
  for (const [label, fade] of [['Next', 0.5], ['another change', null]]) {
    let nextAt = 0;
    const { ss } = await shortItem(async (page) => {
      await sleep(1000);
      await page.set({ ...D, nextId: 'a', startedAt: iso() });
      await sleep(1000);
      nextAt = (await page.evaluate(() => window.__samples.at(-1)?.at)) ?? 2;
      await page.set({ ...C, nextId: 'a', startedAt: iso(), fade });
    }, 6);
    const dHeard = firstAt(ss, (s) => heard(s, 'd'));
    const cIn = firstAt(ss, (s) => heard(s, 'c'));
    check(`let-finish: ${label} during the wait cancels it (the waited item never comes in)`, dHeard === null,
      dHeard === null ? '' : `"d" heard at ${dHeard.toFixed(2)} s`);
    if (fade != null) {
      check(`let-finish: ${label} during the wait is heard at once`, cIn !== null && cIn - nextAt <= 1,
        cIn === null ? 'never' : `${(cIn - nextAt).toFixed(2)} s after Next`);
    } else {
      check(`let-finish: ${label} during the wait still lets the time check finish`, cIn !== null && cIn - nextAt > 2,
        cIn === null ? 'never' : `${(cIn - nextAt).toFixed(2)} s after the change`);
    }
  }
}

// F-HANDOVER-13: no let-finish when rejoining (a new play of the item: replay), when
// switching station, or when the deck is paused
async function notLetFinish() {
  {
    // a 12 s item (short: played to its end) 6 s in; a new play of it is announced.
    // (Within 4 s of its start the same item counts as the play already on air.)
    const page = await open();
    await page.set({ ...C, nextId: 'd', startedAt: iso() });
    await startSampling(page);
    await sleep(6000);
    const sent = (await page.evaluate(() => window.__samples.at(-1)?.at)) ?? 6;
    await page.set({ ...C, nextId: 'd', startedAt: iso() });
    await sleep(3000);
    const ss = await samples(page);
    await page.close();
    const fresh = firstAt(ss, (s) => s.at > sent && s.decks.some((d) => d.src === 'c' && d.playing && d.gain > 0.3 && d.t < 0.6));
    check('not let-finish: a new play of the item on air (replay) starts at once', fresh !== null && fresh - sent < 1,
      fresh === null ? 'never' : `${(fresh - sent).toFixed(2)} s after the message`);
  }
  {
    let sent = 0;
    const { ss } = await shortItem(async (page) => {
      await sleep(1500);
      // the control room's socket clears now-playing when the station changes
      await page.set(null);
      await sleep(150);
      sent = (await page.evaluate(() => window.__samples.at(-1)?.at)) ?? 1.65;
      await page.set({ ...C, nextId: 'a', startedAt: iso(-3) });
    }, 3.5);
    const cIn = firstAt(ss, (s) => heard(s, 'c'));
    const tLate = firstAt(ss, (s) => s.at > sent + 0.3 && heard(s, 't'));
    check('not let-finish: switching station brings the other station in at once', cIn !== null && cIn - sent < 1.5,
      cIn === null ? 'never' : `${(cIn - sent).toFixed(2)} s after the switch`);
    check('not let-finish: the old station\'s time check is not let finish', tLate === null,
      tLate === null ? '' : `time check still heard at ${tLate.toFixed(2)} s`);
  }
  {
    let sent = 0;
    const { ss } = await shortItem(async (page) => {
      await sleep(1500);
      await page.evaluate(() => window.__audios.find((a) => a.src.endsWith('/t.wav') && !a.paused)?.pause());
      sent = (await page.evaluate(() => window.__samples.at(-1)?.at)) ?? 1.5;
      await page.set({ ...D, nextId: 'a', startedAt: iso() });
    }, 3);
    const dIn = firstAt(ss, (s) => heard(s, 'd'));
    check('not let-finish: with the deck paused the next item comes in at once', dIn !== null && dIn - sent < 1,
      dIn === null ? 'never' : `${(dIn - sent).toFixed(2)} s after the message`);
  }
}

// F-HANDOVER-19: after a let-finish wait the next item starts from its first word,
// not at its live position (which would cut the seconds waited from its start)
async function fromTop() {
  const { ss, seeks } = await shortItem(async (page) => {
    await sleep(1000);
    await page.set({ ...D, nextId: 'a', startedAt: iso() });
  }, 6);
  const first = ss.find((s) => heard(s, 'd'));
  const pos = first ? deck(first, 'd').t : null;
  check('from top: after the wait the next item starts from its first word', pos !== null && pos < 0.4,
    pos === null ? 'never heard' : `first heard ${pos} s into it, ~4 s after its server start`);
  const dSeeks = seeks.filter((x) => x.src === 'd' && x.to > 0.5);
  check('from top: no seek into the next item', dSeeks.length === 0, dSeeks.map((x) => `seek to ${x.to}`).join(', '));
}

// V1-51: fire-now with nothing on air: the item the server puts on air starts here,
// and the queue's next item follows it
async function fromIdle() {
  const page = await open();
  await page.set(null); // nothing on air
  await sleep(1000);
  await startSampling(page);
  await page.set({ ...A, nextId: 'b', startedAt: iso() });
  await sleep(18500);
  const ss = await samples(page);
  const cIn = firstAt(ss, (s) => heard(s, 'a'));
  check('from idle: a fired item starts when nothing was on air', cIn !== null && cIn < 1.5,
    cIn === null ? 'never' : `heard after ${cIn.toFixed(2)} s`);
  const bIn = firstAt(ss, (s) => heard(s, 'b'));
  check('from idle: the queue continues after it (next item at its crossfade point, 17 s)', bIn !== null && bIn >= 15.8 && bIn <= 17.8,
    bIn === null ? 'never' : `next item in at ${bIn.toFixed(2)} s`);
  await page.close();
}

try {
  const parts = {
    fromidle: fromIdle, drift, background, mixover: mixOver, clock, handover, letfinish: letFinish, notletfinish: notLetFinish, fromtop: fromTop };
  for (const [name, fn] of Object.entries(parts)) if (!only || only === name) await fn();
} finally {
  await browser.close();
  await server.close();
}
process.exit(failures ? 1 : 0);
