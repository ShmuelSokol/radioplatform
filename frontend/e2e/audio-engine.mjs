/**
 * Browser check for the web player (useAudioEngine) around song changes.
 * Mounts the real hook in a bare page, feeds it now-playing updates the way the
 * server would, and watches the two audio decks. No backend, no real station.
 *
 *   cd frontend && node e2e/audio-engine.mjs        # optional: one scenario name
 *
 * Exits non-zero if any check fails. Takes about two minutes.
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { createServer } from 'vite';

const { chromium } = createRequire(import.meta.url)('playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
  if (!ok) failures++;
};

// Test tones: asset id -> [frequency, seconds]
const TONES = { a: [440, 20], b: [660, 12], c: [880, 12] };
function wav(freq, seconds) {
  const rate = 8000, n = rate * seconds, buf = Buffer.alloc(44 + n * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVEfmt ', 8);
  buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(8000 * Math.sin((2 * Math.PI * freq * i) / rate)), 44 + i * 2);
  return buf;
}

const A = { id: 'a', dur: 20, cross: 17 };
const B = { id: 'b', dur: 12, cross: 9 };
const C = { id: 'c', dur: 12, cross: 9 };
const now = () => new Date().toISOString();

// Each scenario starts with song "a" on air, then calls change() and is watched for `watch` seconds.
const SCENARIOS = {
  // The same item queued twice in a row, second play pre-loaded on the idle deck
  'same-item-twice': {
    start: { ...A, nextId: 'a' }, wait: 15, watch: 9,
    change: (set) => setTimeout(() => set({ ...A, nextId: 'b', startedAt: now() }), 2000),
    end: { src: 'a', minT: 4, maxT: 8 },
  },
  // Same, but the player was never told what is next, so nothing is pre-loaded
  'same-item-twice-cold': {
    start: { ...A, nextId: null }, wait: 15, watch: 9,
    change: (set) => setTimeout(() => set({ ...A, nextId: 'b', startedAt: now() }), 2000),
    end: { src: 'a', minT: 4, maxT: 8 },
  },
  // Next pressed mid-song: the new now-playing arrives before its start time, URL lookups are slow
  'next-with-stale-start-time': {
    start: { ...A, nextId: 'b' }, wait: 12, watch: 8, urlDelay: 2500,
    change: (set, startA) => {
      set({ ...B, nextId: 'b', startedAt: startA });
      setTimeout(() => set({ ...B, nextId: 'c', startedAt: now() }), 700);
    },
    audibleWithin: 1, end: { src: 'b', minT: 6.5, maxT: 9 },
  },
  // Next pressed one second into a running natural crossfade, to a third song
  'next-during-crossfade': {
    start: { ...A, nextId: 'b' }, wait: 18, watch: 6,
    change: (set) => set({ ...C, nextId: 'a', startedAt: now() }),
    audibleWithin: 1, end: { src: 'c', minT: 5, maxT: 7 },
  },
  // Next pressed just before the automatic crossfade point: the skip is announced first
  'next-just-before-crossfade': {
    start: { ...A, nextId: 'b' }, wait: 16.8, watch: 8,
    change: (set) => {
      const at = now();
      setTimeout(() => set({ ...B, nextId: 'b', startedAt: at }), 150);
      setTimeout(() => set({ ...B, nextId: 'c', startedAt: at }), 600);
    },
    audibleWithin: 1, end: { src: 'b', minT: 7, maxT: 8.5 },
  },
  // Same click, but the player's own crossfade has already begun when the skip is announced
  'next-just-after-crossfade-began': {
    start: { ...A, nextId: 'b' }, wait: 16.8, watch: 8,
    change: (set) => {
      const at = now();
      setTimeout(() => set({ ...B, nextId: 'b', startedAt: at }), 600);
      setTimeout(() => set({ ...B, nextId: 'c', startedAt: at }), 1000);
    },
    audibleWithin: 1, end: { src: 'b', minT: 7, maxT: 8.5 },
  },
  // The server advanced to "b" by itself a moment before Next arrived, so Next lands on "c"
  'next-right-after-automatic-advance': {
    start: { ...A, nextId: 'b' }, wait: 17, watch: 8,
    change: (set) => {
      set({ ...B, nextId: 'c', startedAt: now() });
      setTimeout(() => set({ ...C, nextId: 'a', startedAt: now() }), 400);
    },
    audibleWithin: 1.4, end: { src: 'c', minT: 6.8, maxT: 8.2 },
  },
  // Next with the quick fade pressed a few seconds before the crossfade point (a short
  // looped jingle is nearly always there): the operator's fade wins, not the 3 s crossfade
  'next-quick-fade-near-the-end': {
    start: { ...A, nextId: 'b' }, wait: 14.5, watch: 5,
    change: (set) => set({ ...C, nextId: 'a', startedAt: now(), fade: 0.5 }),
    audibleWithin: 1, outGoneWithin: 1.2, end: { src: 'c', minT: 4, maxT: 6 },
  },
  // A sponsorship that waits for the song (soft) is due in 3 s: the song must keep playing
  'soft-timed-item-does-not-fade-the-song': {
    start: { ...A, nextId: 'b' }, wait: 4, watch: 6,
    change: (set, startA) => set({ ...A, nextId: 'b', startedAt: startA, queue: [timed({ hard_timed: false })] }),
    end: { src: 'a', minT: 9, maxT: 11 },
  },
  // Same, from a server too old to send hard_timed (sponsor slots were always soft)
  'soft-sponsor-slot-from-older-server-does-not-fade': {
    start: { ...A, nextId: 'b' }, wait: 4, watch: 6,
    change: (set, startA) => set({ ...A, nextId: 'b', startedAt: startA, queue: [timed({ source: 'ad_slot' })] }),
    end: { src: 'a', minT: 9, maxT: 11 },
  },
  // An item set to start on the second: the song fades out by its time
  'hard-timed-item-fades-the-song': {
    start: { ...A, nextId: 'b' }, wait: 4, watch: 5,
    change: (set, startA) => set({ ...A, nextId: 'b', startedAt: startA, queue: [timed({ hard_timed: true })] }),
    fadesBy: 3,
  },
  // A soft item with a limit: the song fades at the limit, not at the item's time
  'soft-item-fades-only-at-its-limit': {
    start: { ...A, nextId: 'b' }, wait: 4, watch: 7,
    change: (set, startA) => set({
      ...A, nextId: 'b', startedAt: startA,
      queue: [timed({ hard_timed: false, preempt_at: inSeconds(1), hard_by: inSeconds(5) })],
    }),
    fadesBy: 5, notBefore: 2.5,
  },
  // Next mid-song with the next item pre-loaded, announced straight away: the new item
  // starts from its top with no seek (a small seek repeated the opening in Safari)
  'next-starts-clean': {
    start: { ...A, nextId: 'b' }, wait: 6, watch: 4,
    change: (set) => set({ ...B, nextId: 'c', startedAt: now(), fade: 0.6 }),
    audibleWithin: 1, noSeekOn: 'b', end: { src: 'b', minT: 3, maxT: 5 },
  },
  // Same, cold: the next item was never pre-loaded
  'next-starts-clean-cold': {
    start: { ...A, nextId: null }, wait: 6, watch: 4,
    change: (set) => set({ ...C, nextId: 'a', startedAt: now(), fade: 0.6 }),
    audibleWithin: 1.5, noSeekOn: 'c', end: { src: 'c', minT: 3, maxT: 5 },
  },
};

function inSeconds(s) { return new Date(Date.now() + s * 1000).toISOString(); }
// A queued timed item due in 3 s (rows as the queue endpoint sends them)
function timed(extra) {
  return { id: 'q-timed', asset_id: 'c', position: 2, status: 'pending', source: 'block', preempt_at: inSeconds(3), ...extra };
}

const only = process.argv[2];
const server = await createServer({ root, logLevel: 'error', server: { port: 5197, strictPort: false } });
await server.listen();
const base = server.resolvedUrls.local[0];
const browser = await chromium.launch({ args: ['--autoplay-policy=no-user-gesture-required'] });

async function run(name, sc) {
  const page = await browser.newPage();
  let urlDelay = 30;
  await page.route('**/__tone/*.wav', (r) => {
    const id = r.request().url().split('/').pop().replace('.wav', '');
    return r.fulfill({ contentType: 'audio/wav', body: wav(...TONES[id]) });
  });
  await page.route('**/assets/*/audio-url', async (r) => {
    const id = r.request().url().split('/').slice(-2)[0];
    await sleep(urlDelay);
    await r.fulfill({ contentType: 'application/json', body: JSON.stringify({ url: `/__tone/${id}.wav` }) });
  });
  await page.goto(`${base}e2e/audio-harness/index.html`);
  await page.waitForFunction(() => typeof window.__set === 'function');
  const set = (s) => page.evaluate((x) => window.__set(x), s);
  const startA = now();
  await set({ ...sc.start, startedAt: startA });
  await sleep(sc.wait * 1000);
  if (sc.urlDelay) urlDelay = sc.urlDelay;

  const t0 = Date.now();
  await page.evaluate(() => { window.__seeks.length = 0; });
  sc.change(set, startA);
  let silentFor = 0, longestSilence = 0, firstNew = null, outGone = null, firstSilent = null, last = [];
  const target = sc.end?.src;
  while (Date.now() - t0 < sc.watch * 1000) {
    last = await page.evaluate(() => window.__snap());
    const audible = last.filter((d) => d.playing && d.gain > 0.05);
    silentFor = audible.length ? 0 : silentFor + 0.2;
    if (firstSilent === null && !audible.length) firstSilent = (Date.now() - t0) / 1000;
    longestSilence = Math.max(longestSilence, silentFor);
    if (firstNew === null && audible.some((d) => d.src === target && d.t < 4)) firstNew = (Date.now() - t0) / 1000;
    if (outGone === null && !audible.some((d) => d.src === sc.start.id)) outGone = (Date.now() - t0) / 1000;
    await sleep(200);
  }
  if (sc.fadesBy) {
    check(`${name}: song faded out by ${sc.fadesBy} s`, firstSilent !== null && firstSilent <= sc.fadesBy + 0.5,
      firstSilent === null ? 'never' : `${firstSilent.toFixed(2)} s`);
    if (sc.notBefore) {
      check(`${name}: not before ${sc.notBefore} s`, firstSilent === null || firstSilent >= sc.notBefore,
        firstSilent === null ? 'never' : `${firstSilent.toFixed(2)} s`);
    }
    await page.close();
    return;
  }
  const audible = last.filter((d) => d.playing && d.gain > 0.05);
  const state = last.map((d) => `${d.src || '-'}${d.playing ? '' : '(stopped)'}@${d.t}`).join(' | ');
  check(`${name}: never silent`, longestSilence < 0.5, `longest gap ${longestSilence.toFixed(1)} s`);
  check(`${name}: ends with only "${target}" on air, at the right position`,
    audible.length === 1 && audible[0].src === target && audible[0].t >= sc.end.minT && audible[0].t <= sc.end.maxT, state);
  if (sc.audibleWithin) {
    check(`${name}: new song audible within ${sc.audibleWithin} s`, firstNew !== null && firstNew <= sc.audibleWithin,
      firstNew === null ? 'never' : `${firstNew.toFixed(2)} s`);
  }
  if (sc.noSeekOn) {
    const seeks = (await page.evaluate(() => window.__seeks)).filter((x) => x.src === sc.noSeekOn);
    check(`${name}: "${sc.noSeekOn}" starts from the top without a seek`, seeks.length === 0,
      seeks.map((x) => `seek to ${x.to} at +${((x.at - t0) / 1000).toFixed(2)} s`).join(', '));
  }
  if (sc.outGoneWithin) {
    check(`${name}: outgoing song gone within ${sc.outGoneWithin} s`, outGone !== null && outGone <= sc.outGoneWithin,
      outGone === null ? 'never' : `${outGone.toFixed(2)} s`);
  }
  await page.close();
}

// A new release must never reload a tab whose audio is really playing (the
// browser would not restart it without a click), but a control room that only
// shows the station, with sound never switched on, must still pick it up.
async function releaseHold() {
  for (const [label, gesture, expectHeld] of [
    ['audio playing', true, true],
    ['station on air, sound never switched on', false, false],
  ]) {
    const page = await browser.newPage();
    // Headless Chromium lets audio start without a click, so act out the real
    // rule: with no click, the page's audio context stays suspended.
    if (!gesture) {
      await page.addInitScript(() => {
        const Real = window.AudioContext;
        window.AudioContext = class extends Real {
          constructor(...a) { super(...a); void this.suspend(); }
          resume() { return Promise.resolve(); }
        };
      });
    }
    await page.route('**/__tone/*.wav', (r) => r.fulfill({ contentType: 'audio/wav', body: wav(...TONES.a) }));
    await page.route('**/assets/*/audio-url', (r) => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ url: '/__tone/a.wav' }) }));
    await page.goto(`${base}e2e/audio-harness/index.html`);
    await page.waitForFunction(() => typeof window.__set === 'function');
    await page.evaluate((x) => window.__set(x), { ...A, startedAt: now() });
    await sleep(3000);
    const held = await page.evaluate(() => window.__held());
    const sounding = (await page.evaluate(() => window.__snap())).some((d) => d.playing);
    check(`release hold: ${label}`, held === expectHeld, `held=${held}, deck playing=${sounding}`);
    await page.close();
  }
}

// Safari: until the person clicks, resume() never settles and media will not play.
// Pressing Listen mid-item must bring up the item already on air, at its position.
async function listenMidItem() {
  const page = await browser.newPage();
  // Act out Safari: media plays only when play() is called inside a click, or on an
  // element that has already played that way; resume() made before any click
  // settles only once the person clicks.
  await page.addInitScript(() => {
    let clicked = false, inGesture = false;
    const unlocked = new WeakSet();
    const waiting = [];
    const Real = window.AudioContext;
    window.AudioContext = class extends Real {
      constructor(...a) { super(...a); void Real.prototype.suspend.call(this); }
      resume() {
        if (!clicked && !inGesture) {
          return new Promise((ok) => waiting.push(() => Real.prototype.resume.call(this).then(ok)));
        }
        return Real.prototype.resume.call(this);
      }
    };
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () {
      if (inGesture) unlocked.add(this);
      if (!unlocked.has(this)) return Promise.reject(new DOMException('not allowed', 'NotAllowedError'));
      return play.call(this);
    };
    window.__click = () => {
      clicked = true;
      inGesture = true;
      try { window.__listen(); } finally { inGesture = false; }
      setTimeout(() => waiting.splice(0).forEach((go) => go()), 300);
    };
  });
  await page.route('**/__tone/*.wav', (r) => {
    const id = r.request().url().split('/').pop().replace('.wav', '');
    return r.fulfill({ contentType: 'audio/wav', body: wav(...TONES[id]) });
  });
  await page.route('**/assets/*/audio-url', (r) => {
    const id = r.request().url().split('/').slice(-2)[0];
    return r.fulfill({ contentType: 'application/json', body: JSON.stringify({ url: `/__tone/${id}.wav` }) });
  });
  await page.goto(`${base}e2e/audio-harness/index.html`);
  await page.waitForFunction(() => typeof window.__set === 'function');
  await page.evaluate((x) => window.__set(x), { ...A, nextId: 'b', startedAt: now() });
  await sleep(4000);
  await page.evaluate(() => window.__click());
  await sleep(2000);
  const audible = (await page.evaluate(() => window.__snap())).filter((d) => d.playing && d.gain > 0.05);
  // (The test server sends whole files without ranges, so the position itself is not checked here)
  check('listen mid-item: the item on air is heard at once, not after the next item',
    audible.length === 1 && audible[0].src === 'a',
    audible.map((d) => `${d.src}@${d.t}`).join(' | ') || 'silent');
  // and the items after it play without another click
  await page.evaluate((x) => window.__set(x), { ...B, nextId: 'c', startedAt: now() });
  await sleep(2500);
  const after = (await page.evaluate(() => window.__snap())).filter((d) => d.playing && d.gain > 0.05);
  check('listen mid-item: the next item plays too', after.length === 1 && after[0].src === 'b',
    after.map((d) => `${d.src}@${d.t}`).join(' | ') || 'silent');
  await page.close();
}

try {
  if (!only || only === 'listen-mid-item') await listenMidItem();
  for (const [name, sc] of Object.entries(SCENARIOS)) {
    if (!only || only === name) await run(name, sc);
  }
  if (!only || only === 'release-hold') await releaseHold();
} finally {
  await browser.close();
  await server.close();
}
process.exit(failures ? 1 : 0);
