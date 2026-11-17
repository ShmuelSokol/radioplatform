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
import { serveRange } from './lib/fake-station.mjs';

const { chromium } = createRequire(import.meta.url)('playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
  if (!ok) failures++;
};
// A known site bug: prints XFAIL while it stands, XPASS (and fails) once fixed, so the
// mark is taken off
const xfail = (name, ok, bug, detail = '') => {
  console.log(`${ok ? 'XPASS' : 'XFAIL'}  ${name}  [BUG ${bug}]${detail ? `  (${detail})` : ''}`);
  if (ok) failures++;
};

// Test tones: asset id -> [frequency, seconds]
const TONES = { a: [440, 20], b: [660, 12], c: [880, 12], d: [550, 40], t: [1000, 5], p: [750, 22], q: [950, 22] };
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
const D = { id: 'd', dur: 40, cross: 37 };
// A 5 s time check: short items hand over at their end (0.3 s before), not by crossfade
const T = { id: 't', dur: 5, cross: 4.7 };
// Two 22 s sponsorships: spoken items never overlap what follows (the server sends
// overlap 0 and hands over 0.3 s before the end)
const P = { id: 'p', dur: 22, cross: 21.7, overlap: 0 };
const Q = { id: 'q', dur: 22, cross: 21.7, overlap: 0 };
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
    fadesBy: 3, firstHeard: { src: 'c', from: 2.8, to: 3.5 },
  },
  // A soft item with a limit: the song fades at the limit, not at the item's time
  'soft-item-fades-only-at-its-limit': {
    start: { ...A, nextId: 'b' }, wait: 4, watch: 7,
    change: (set, startA) => set({
      ...A, nextId: 'b', startedAt: startA,
      queue: [timed({ hard_timed: false, preempt_at: inSeconds(1), hard_by: inSeconds(5) })],
    }),
    fadesBy: 5, notBefore: 2.5, firstHeard: { src: 'c', from: 4.8, to: 5.5 },
  },
  // A hard block due in 3 s, announced 0.25 s after its time, with slow URL lookups
  // (a cold load): the block item must already be loaded, so there is no hole
  'hard-timed-item-has-no-hole': {
    start: { ...A, nextId: 'b' }, wait: 4, watch: 6, urlDelay: 700,
    change: (set, startA) => {
      const q = [timed({ hard_timed: true })];
      set({ ...A, nextId: 'b', startedAt: startA, queue: q });
      setTimeout(() => set({ ...C, nextId: 'b', startedAt: q[0].preempt_at }), 3250);
    },
    end: { src: 'c', minT: 2, maxT: 3.5 },
  },
  // Control room Next: the next item is heard at once, before the server answers
  // 0.6 s later with the same item, which must not restart or jump it
  'next-heard-before-the-server-answers': {
    start: { ...A, nextId: 'b' }, wait: 6, watch: 5,
    change: (set, startA, nextNow) => {
      nextNow('b', 0.5);
      setTimeout(() => set({ ...B, nextId: 'c', startedAt: now(), fade: 0.5 }), 600);
    },
    audibleWithin: 0.45, noSeekOn: 'b', end: { src: 'b', minT: 4.3, maxT: 5.6 },
  },
  // The server moved somewhere else (another operator, a timed item): follow it
  'next-ahead-then-server-differs': {
    start: { ...A, nextId: 'b' }, wait: 6, watch: 5,
    change: (set, startA, nextNow) => {
      nextNow('b', 0.5);
      setTimeout(() => set({ ...C, nextId: 'a', startedAt: now(), fade: 0.5 }), 600);
    },
    end: { src: 'c', minT: 3.5, maxT: 5 },
  },
  // Next mid-song with the next item pre-loaded, announced straight away: the new item
  // starts from its top with no seek (a small seek repeated the opening in Safari)
  'next-starts-clean': {
    start: { ...A, nextId: 'b' }, wait: 6, watch: 4,
    change: (set) => set({ ...B, nextId: 'c', startedAt: now(), fade: 0.6 }),
    audibleWithin: 1, noSeekOn: 'b', end: { src: 'b', minT: 3, maxT: 5 },
  },
  // The stored length and crossfade point say 180 s / 177 s, but the file is 20 s long
  // (the old analysis measured every item as 180 s): the player hands over 3 s before
  // the file's real end by itself, before the server announces anything
  'stored-length-longer-than-file': {
    start: { ...A, dur: 180, cross: 177, nextId: 'b' }, wait: 15, watch: 9,
    change: () => {},
    end: { src: 'b', minT: 5.5, maxT: 8 },
  },
  // Same, cold: the next item was never pre-loaded
  'next-starts-clean-cold': {
    start: { ...A, nextId: null }, wait: 6, watch: 4,
    change: (set) => set({ ...C, nextId: 'a', startedAt: now(), fade: 0.6 }),
    audibleWithin: 1.5, noSeekOn: 'c', end: { src: 'c', minT: 3, maxT: 5 },
  },
  // Switching the control room to another station: the other station's item has been
  // on air for a while. Only it may be heard (heard live: both stations at once)
  'switch-station': {
    start: { ...A, nextId: 'b' }, wait: 5, watch: 4,
    change: (set) => set({ ...C, nextId: 'a', startedAt: secondsAgo(3) }),
    // (the test server sends whole files without ranges, so the joining position is not checked)
    audibleWithin: 1.5, end: { src: 'c', minT: 0, maxT: 8 },
  },
  // After the switch, the first station's own song change (its crossfade point and
  // its pre-loaded next item) must not start under the new station
  'switch-station-then-old-crossfade-time': {
    start: { ...A, nextId: 'b' }, wait: 5, watch: 16,
    change: (set) => set({ ...D, nextId: 'c', startedAt: now() }),
    end: { src: 'd', minT: 14, maxT: 17.5 },
  },
  // Switching on, then straight back before the first switch has settled
  'switch-station-and-back': {
    start: { ...A, nextId: 'b' }, wait: 5, watch: 4,
    change: (set) => {
      set({ ...C, nextId: 'a', startedAt: secondsAgo(3) });
      setTimeout(() => set({ ...B, nextId: 'c', startedAt: secondsAgo(2) }), 300);
    },
    end: { src: 'b', minT: 0, maxT: 7 },
  },
  // All Day Stories, 10 PM 2026-11-16: the server moved on to the paused story 3.3 s into
  // a 5 s time check and the story was heard over the last 1.8 s of it. The page lets a
  // short item finish; the story follows it with no gap.
  // Shmuel, 2026-11-17: sponsorships played one after the other faded into each other
  // (the 3 s song crossfade). The second starts when the first has said its last word.
  'sponsorship-after-sponsorship-no-overlap': {
    start: { ...P, nextId: 'q', nextOverlap: 0 }, wait: 20, watch: 5,
    change: (set) => setTimeout(() => set({ ...Q, nextId: 'a', startedAt: now() }), 1700),
    overlapOf: ['p', 'q'], end: { src: 'q', minT: 1.5, maxT: 4 },
  },
  // The server moves on 3 s before the end (an old backend, or its clock ahead): the
  // sponsorship on air is still let finish
  'sponsorship-not-talked-over-when-the-server-is-early': {
    start: { ...P, nextId: 'q', nextOverlap: 0 }, wait: 19, watch: 6,
    change: (set) => set({ ...Q, nextId: 'a', startedAt: now() }),
    overlapOf: ['p', 'q'], end: { src: 'q', minT: 1.5, maxT: 4.5 },
  },
  // A song after a sponsorship: the song waits for the last word too
  'song-after-sponsorship-no-overlap': {
    start: { ...P, nextId: 'b', nextOverlap: 3 }, wait: 20, watch: 5,
    change: (set) => setTimeout(() => set({ ...B, overlap: 3, nextId: 'a', startedAt: now() }), 1700),
    overlapOf: ['p', 'b'], end: { src: 'b', minT: 1.5, maxT: 4 },
  },
  // Station setting "Song crossfade" 0: songs do not overlap either
  'songs-with-crossfade-zero-do-not-overlap': {
    start: { ...A, cross: 19.7, overlap: 0, nextId: 'b', nextOverlap: 0 }, wait: 18, watch: 5,
    change: (set) => setTimeout(() => set({ ...B, overlap: 0, nextId: 'a', startedAt: now() }), 1700),
    overlapOf: ['a', 'b'], end: { src: 'b', minT: 1.5, maxT: 4 },
  },
  // A station with a 3 s crossfade: songs overlap
  'songs-still-crossfade-by-default': {
    start: { ...A, overlap: 3, nextId: 'b', nextOverlap: 3 }, wait: 15.5, watch: 6,
    change: (set) => setTimeout(() => set({ ...B, overlap: 3, nextId: 'a', startedAt: now() }), 1500),
    minOverlapOf: ['a', 'b'], end: { src: 'b', minT: 2.5, maxT: 6 },
  },
  'time-check-not-talked-over': {
    start: { ...T, nextId: 'd' }, wait: 3.3, watch: 6,
    change: (set) => set({ ...D, nextId: 'a', startedAt: now() }),
    overlapOf: ['t', 'd'], end: { src: 'd', minT: 3, maxT: 5.5 },
  },
  // The same with the server's own hand-over 0.3 s before the end: no overlap either
  'time-check-hands-over-at-its-end': {
    start: { ...T, nextId: 'd' }, wait: 4.7, watch: 5,
    change: (set) => set({ ...D, nextId: 'a', startedAt: now() }),
    overlapOf: ['t', 'd'], end: { src: 'd', minT: 3.5, maxT: 5.5 },
  },
  // All Day Stories, 03:00Z 2026-11-17: the story on air is paused for the 5 s time check
  // (pre-loaded as the next item), the server hands back to the story 4.8 s later with
  // its start time moved back to where it stopped. No overlap, no gap.
  'story-resumes-after-time-check': {
    start: { ...D, nextId: 't' }, wait: 3, watch: 9, ranges: true,
    change: (set) => {
      // the server sends the paused story as next with its resume point as cue-in
      set({ ...T, nextId: 'd', nextCueIn: 3, startedAt: now() });
      setTimeout(() => set({ ...D, nextId: 'a', startedAt: secondsAgo(3 + 4.8) }), 4800);
    },
    overlapOf: ['t', 'd'], end: { src: 'd', minT: 9, maxT: 13 },
  },
  // The same with the time check not pre-loaded and slow to arrive (1.2 s late here)
  'story-resumes-after-late-time-check': {
    start: { ...D, nextId: 'a' }, wait: 3, watch: 9, urlDelay: 1200, ranges: true,
    change: (set) => {
      // the server sends the paused story as next with its resume point as cue-in
      set({ ...T, nextId: 'd', nextCueIn: 3, startedAt: now() });
      setTimeout(() => set({ ...D, nextId: 'a', startedAt: secondsAgo(3 + 4.8) }), 4800);
    },
    overlapOf: ['t', 'd'], end: { src: 'd', minT: 9, maxT: 13 },
  },
  // The page starts a pre-loaded hard time check at its time itself: the server's
  // message arrives 1.5 s late (pages were 1.1-1.8 s late, 13:00Z 2026-11-17)
  'hard-time-check-starts-on-time-before-the-message': {
    start: { ...D, nextId: 't' }, wait: 1, watch: 11, ranges: true,
    change: (set, startA) => {
      const due = inSeconds(3);
      set({ ...D, nextId: 't', startedAt: startA, queue: [timed({ asset_id: 't', preempt_at: due })] });
      setTimeout(() => set({ ...T, nextId: 'd', nextCueIn: 4, startedAt: due }), 4500);
      // the server resumes the story at the clip's end (+8 s) from 4 s, started_at moved back
      setTimeout(() => set({ ...D, nextId: 'a', startedAt: secondsAgo(1.2 + 4) }), 3000 + 5000 + 1200);
    },
    firstHeard: { src: 't', from: 2.8, to: 3.4 },
    overlapOf: ['t', 'd'], end: { src: 'd', minT: 6, maxT: 8.5 },
  },
  // Listen page, a hard group at hh:00 (11:00 2026-11-17, Main): time check then the
  // Daf Yomi announcement, both queued with the same preempt_at. Once the time check is
  // on air, next_asset is the announcement with cut_at = that same (past) time. The
  // announcement must still wait for the time check and be heard from its first word.
  'second-item-of-a-timed-group-from-the-top': {
    start: { ...D, nextId: 't' }, wait: 1, watch: 13, ranges: true,
    change: (set, startA) => {
      const due = inSeconds(3);
      set({ ...D, nextId: 't', startedAt: startA, cut: { preempt_at: due, asset_id: 't' } });
      setTimeout(() => set({ ...T, nextId: 'c', startedAt: due, cut: { preempt_at: due, asset_id: 'c' } }), 3350);
      // the server moves on 0.3 s before the clip's end; its message lands 0.4 s later
      setTimeout(() => set({ ...C, nextId: 'a', startedAt: inSeconds(-0.4), cut: null }), 3000 + 4700 + 400);
    },
    firstHeard: { src: 't', from: 2.8, to: 3.4 },
    overlapOf: ['t', 'c'], noSeekOn: 'c', end: { src: 'c', minT: 3.5, maxT: 5.5 },
  },
  // Same, but the announcement's file is slow to arrive (it is only fetched once the
  // time check is on air): it still starts from its first word, a moment late
  'second-item-of-a-timed-group-slow-file': {
    start: { ...D, nextId: 't' }, wait: 1, watch: 15, ranges: true, urlDelayFor: { c: 5000 }, maxSilence: 5.6,
    change: (set, startA) => {
      const due = inSeconds(3);
      set({ ...D, nextId: 't', startedAt: startA, cut: { preempt_at: due, asset_id: 't' } });
      setTimeout(() => set({ ...T, nextId: 'c', startedAt: due, cut: { preempt_at: due, asset_id: 'c' } }), 3350);
      setTimeout(() => set({ ...C, nextId: 'a', startedAt: inSeconds(-0.4), cut: null }), 3000 + 4700 + 400);
    },
    firstHeard: { src: 't', from: 2.8, to: 3.4 },
    overlapOf: ['t', 'c'], noSeekOn: 'c', end: { src: 'c', minT: 1, maxT: 2.5 },
  },
  // A time check with a cue-in: positioned at its cue-in while pre-loading, so starting
  // it at its time needs no seek and no fetch (16:00Z 2026-11-17: pages called play()
  // on time but sound came 1 s later, the song playing on meanwhile)
  'hard-time-check-with-cue-in-starts-at-once': {
    start: { ...D, nextId: 't', nextCueIn: 0.6 }, wait: 1, watch: 7, ranges: true, slowAfterStart: { t: 1000 },
    change: (set, startA) => {
      const due = inSeconds(3);
      set({ ...D, nextId: 't', nextCueIn: 0.6, startedAt: startA, cut: { preempt_at: due, asset_id: 't' } });
      setTimeout(() => set({ ...T, cueIn: 0.6, nextId: 'a', startedAt: due }), 3400);
    },
    firstHeard: { src: 't', from: 2.8, to: 3.4 },
    overlapOf: ['d', 't'], end: { src: 't', minT: 3.5, maxT: 4.9 },
  },
  // Same, with a file that takes 1 s to start sounding on its first play: the page warms
  // it up silently a few seconds ahead, so it still sounds at its time
  'hard-time-check-slow-starting-file-on-time': {
    start: { ...D, nextId: 't' }, wait: 1, watch: 9, ranges: true, coldStartMs: 1000,
    change: (set, startA) => {
      const due = inSeconds(5);
      set({ ...D, nextId: 't', startedAt: startA, cut: { preempt_at: due, asset_id: 't' } });
      setTimeout(() => set({ ...T, nextId: 'a', startedAt: due }), 5400);
    },
    firstHeard: { src: 't', from: 4.8, to: 5.4 },
    overlapOf: ['d', 't'], end: { src: 't', minT: 2.5, maxT: 4.2 },
  },
  // A hard time check announced only 2 s before it is due (less than the 3 s warm-up),
  // with a file slow to start: no warm-up happens, but it must still sound within about
  // a second of its time and the song must not go quiet before it does
  'hard-time-check-short-notice-slow-start': {
    start: { ...D, nextId: 'a' }, wait: 1, watch: 6.5, ranges: true, coldStartMs: 1000,
    change: (set, startA) => {
      const due = inSeconds(2);
      set({ ...D, nextId: 't', startedAt: startA, cut: { preempt_at: due, asset_id: 't' } });
      setTimeout(() => set({ ...T, nextId: 'a', startedAt: due }), 2400);
    },
    firstHeard: { src: 't', from: 1.8, to: 3.2 },
    overlapOf: ['d', 't'], end: { src: 't', minT: 2.5, maxT: 5 },
  },
  // Operator Next to "c" while the time check is warming up (due in 5 s, warm-up from
  // 2 s): "c" comes in at once, nothing of the warming deck is heard, and the time check
  // still sounds at its time, cutting "c"
  'next-during-timed-warm-up': {
    start: { ...D, nextId: 't' }, wait: 1, watch: 9, ranges: true, coldStartMs: 1000,
    change: (set, startA) => {
      const due = inSeconds(5);
      set({ ...D, nextId: 't', startedAt: startA, cut: { preempt_at: due, asset_id: 't' } });
      setTimeout(() => set({ ...C, nextId: 't', startedAt: now(), fade: 0.5, cut: { preempt_at: due, asset_id: 't' } }), 3000);
      setTimeout(() => set({ ...T, nextId: 'a', startedAt: due }), 5400);
    },
    firstHeard: { src: 't', from: 4.8, to: 5.4 },
    // Next takes the deck the time check was warming on; the old song's deck frees only
    // once "c" sounds (1 s here), 0.3 s before the time, too late to warm: the time
    // check is 1.1 s late (no silence: "c" stays low until it sounds). Needs a third deck.
    firstHeardBug: 'next-during-warm-up: time check 1.1 s late, needs a deck of its own',
    overlapOf: ['c', 't'], end: { src: 't', minT: 2.5, maxT: 4.2 },
  },
  // Switching while the station's own crossfade is under way
  'switch-station-during-crossfade': {
    start: { ...A, nextId: 'b' }, wait: 17.5, watch: 4,
    change: (set) => set({ ...C, nextId: 'a', startedAt: secondsAgo(3) }),
    end: { src: 'c', minT: 0, maxT: 8 },
  },
};

function secondsAgo(s) { return new Date(Date.now() - s * 1000).toISOString(); }
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
  let changed = false;
  // sc.coldStartMs: the first play() after an element loads a file sounds only this much
  // later (as real MP3s from the file store did live, 16:00Z 2026-11-17); later plays of
  // the same file start at once
  if (sc.coldStartMs) {
    await page.addInitScript((ms) => {
      const P = HTMLMediaElement.prototype;
      const realPlay = P.play, realLoad = P.load;
      P.load = function () { this.__warm = false; return realLoad.call(this); };
      P.play = function () {
        if (this.__warm) return realPlay.call(this);
        this.__warm = true;
        return new Promise((resolve, reject) => setTimeout(() => realPlay.call(this).then(resolve, reject), ms));
      };
    }, sc.coldStartMs);
  }
  let urlDelay = 30;
  await page.route('**/__tone/*.wav', (r) => {
    const id = r.request().url().split('/').pop().replace('.wav', '');
    // sc.ranges: byte ranges like the real file store, so positions can be checked
    // sc.slowAfterStart: each request for that item once the scenario runs waits this long
    // (a seek or a re-fetch at play time on a real file store)
    const slow = sc.slowAfterStart?.[id];
    if (slow && changed) return sleep(slow).then(() => serveRange(r, wav(...TONES[id])));
    if (sc.ranges) return serveRange(r, wav(...TONES[id]));
    return r.fulfill({ contentType: 'audio/wav', body: wav(...TONES[id]) });
  });
  await page.route('**/assets/*/audio-url', async (r) => {
    const id = r.request().url().split('/').slice(-2)[0];
    await sleep(sc.urlDelayFor?.[id] ?? urlDelay);
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
  changed = true;
  await page.evaluate(() => { window.__seeks.length = 0; });
  const nextNow = (id, fade) => page.evaluate(([i, f]) => window.__nextNow(i, f), [id, fade]);
  sc.change(set, startA, nextNow);
  let silentFor = 0, longestSilence = 0, firstNew = null, outGone = null, firstSilent = null, last = [];
  let overlap = 0, firstHeard = null;
  const target = sc.end?.src;
  while (Date.now() - t0 < sc.watch * 1000) {
    last = await page.evaluate(() => window.__snap());
    if (process.env.TIMELINE) console.log(((Date.now() - t0) / 1000).toFixed(1), last.map((d) => `${d.src}${d.playing ? '' : '-'}@${d.t}/${d.gain}`).join(' '));
    const audible = last.filter((d) => d.playing && d.gain > 0.05);
    silentFor = audible.length ? 0 : silentFor + 0.2;
    if (firstSilent === null && !audible.length) firstSilent = (Date.now() - t0) / 1000;
    longestSilence = Math.max(longestSilence, silentFor);
    if (sc.minOverlapOf && sc.minOverlapOf.every((id) => audible.some((d) => d.src === id && d.gain > 0.2))) overlap += 0.2;
    if (sc.overlapOf && sc.overlapOf.every((id) => audible.some((d) => d.src === id && d.gain > 0.2))) overlap += 0.2;
    if (firstNew === null && audible.some((d) => d.src === target && d.t < 4)) firstNew = (Date.now() - t0) / 1000;
    if (sc.firstHeard && firstHeard === null && audible.some((d) => d.src === sc.firstHeard.src && d.gain > 0.5)) firstHeard = (Date.now() - t0) / 1000;
    if (outGone === null && !audible.some((d) => d.src === sc.start.id)) outGone = (Date.now() - t0) / 1000;
    await sleep(200);
  }
  if (sc.fadesBy) {
    // the song is gone (silence, or the timed item the page started itself at its time)
    check(`${name}: song faded out by ${sc.fadesBy} s`, outGone !== null && outGone <= sc.fadesBy + 0.5,
      outGone === null ? 'never' : `${outGone.toFixed(2)} s`);
    if (sc.notBefore) {
      check(`${name}: not before ${sc.notBefore} s`, outGone === null || outGone >= sc.notBefore,
        outGone === null ? 'never' : `${outGone.toFixed(2)} s`);
    }
    if (sc.firstHeard) {
      const { src, from, to } = sc.firstHeard;
      check(`${name}: "${src}" heard from ${from} to ${to} s`, firstHeard !== null && firstHeard >= from && firstHeard <= to,
        firstHeard === null ? 'never' : `${firstHeard.toFixed(2)} s`);
    }
    await page.close();
    return;
  }
  const audible = last.filter((d) => d.playing && d.gain > 0.05);
  const state = last.map((d) => `${d.src || '-'}${d.playing ? '' : '(stopped)'}@${d.t}`).join(' | ');
  // sc.maxSilence: a file held back on purpose leaves that much quiet, no more
  check(`${name}: ${sc.maxSilence ? `silent no longer than ${sc.maxSilence} s` : 'never silent'}`,
    longestSilence < (sc.maxSilence ?? 0.5), `longest gap ${longestSilence.toFixed(1)} s`);
  check(`${name}: ends with only "${target}" on air, at the right position`,
    audible.length === 1 && audible[0].src === target && audible[0].t >= sc.end.minT && audible[0].t <= sc.end.maxT, state);
  if (sc.audibleWithin) {
    check(`${name}: new song audible within ${sc.audibleWithin} s`, firstNew !== null && firstNew <= sc.audibleWithin,
      firstNew === null ? 'never' : `${firstNew.toFixed(2)} s`);
  }
  if (sc.firstHeard) {
    const { src, from, to } = sc.firstHeard;
    // sc.firstHeardBug: a known, recorded bug (XFAIL until fixed)
    (sc.firstHeardBug ? (n, ok, d) => xfail(n, ok, sc.firstHeardBug, d) : check)(
      `${name}: "${src}" heard from ${from} to ${to} s`, firstHeard !== null && firstHeard >= from && firstHeard <= to,
      firstHeard === null ? 'never' : `${firstHeard.toFixed(2)} s`);
  }
  if (sc.overlapOf) {
    check(`${name}: "${sc.overlapOf[1]}" is not heard over "${sc.overlapOf[0]}"`, overlap <= 0.4,
      `heard together for about ${overlap.toFixed(1)} s`);
  }
  if (sc.minOverlapOf) {
    check(`${name}: "${sc.minOverlapOf[1]}" comes in over the end of "${sc.minOverlapOf[0]}"`, overlap >= 1.0,
      `heard together for about ${overlap.toFixed(1)} s`);
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

// The listener page passes the item on air only after Listen is pressed, so the
// decks are empty during the click. Heard live (2026-11-16): one press of Listen
// gave no sound until the volume slider was moved.
async function listenFromCold() {
  const page = await gesturePage();
  const startedAt = new Date(Date.now() - 4000).toISOString();
  await page.evaluate((x) => window.__clickThen(x), { ...A, nextId: 'b', startedAt });
  await sleep(2500);
  const audible = (await page.evaluate(() => window.__snap())).filter((d) => d.playing && d.gain > 0.05);
  check('listen from cold: one press of Listen is heard at once',
    audible.length === 1 && audible[0].src === 'a',
    audible.map((d) => `${d.src}@${d.t}`).join(' | ') || JSON.stringify(await page.evaluate(() => window.__snap())));
  await page.close();
}

// Stop, then Listen again: heard live from the car, the item went on from where it
// was stopped, like a pause button. It must join where the station is now.
async function stopThenListen() {
  const page = await gesturePage();
  const t0 = Date.now() - 1000;
  const startedAt = new Date(t0).toISOString();
  await page.evaluate((x) => window.__clickThen(x), { ...D, nextId: 'b', startedAt });
  await sleep(3000);
  await page.evaluate(() => window.__set(null)); // Stop
  await sleep(500);
  const stopped = (await page.evaluate(() => window.__snap())).filter((d) => d.playing && d.gain > 0.05);
  check('stop then listen: Stop silences the player', stopped.length === 0, stopped.map((d) => `${d.src}@${d.t}`).join(' | '));
  await sleep(7000);
  await page.evaluate((x) => window.__clickThen(x), { ...D, nextId: 'b', startedAt });
  const heard = [];
  for (let i = 0; i < 10; i++) {
    await sleep(250);
    const a = (await page.evaluate(() => window.__snap())).filter((d) => d.playing && d.gain > 0.05);
    heard.push(...a.map((d) => ({ ...d, live: (Date.now() - t0) / 1000 })));
  }
  const behind = heard.filter((d) => d.src === 'd' && d.live - d.t > 2.5);
  check('stop then listen: never heard from where it was stopped', behind.length === 0,
    behind.slice(0, 3).map((d) => `${d.src}@${d.t} while live ${d.live.toFixed(1)}`).join(' | '));
  const last = heard.filter((d) => d.src === 'd').pop();
  check('stop then listen: joins the station where it is now', !!last && Math.abs(last.live - last.t) < 2.5,
    last ? `at ${last.t}, live ${last.live.toFixed(1)}` : JSON.stringify(await page.evaluate(() => window.__snap())));
  await page.close();
}

// A page where play() and the AudioContext work only inside a click, like Safari
async function gesturePage() {
  const page = await browser.newPage();
  await page.addInitScript(() => {
    let inGesture = false;
    const unlocked = new WeakSet();
    const Real = window.AudioContext;
    window.AudioContext = class extends Real {
      constructor(...a) { super(...a); if (!inGesture) void Real.prototype.suspend.call(this); }
    };
    const play = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function () {
      if (inGesture) unlocked.add(this);
      if (!unlocked.has(this)) return Promise.reject(new DOMException('not allowed', 'NotAllowedError'));
      return play.call(this);
    };
    window.__clickThen = (state) => {
      inGesture = true;
      try { window.__listen(); } finally { inGesture = false; }
      window.__set(state);
    };
  });
  await page.route('**/__tone/*.wav', (r) => {
    // With byte ranges, as the real file store sends them, so the player can seek
    const id = r.request().url().split('/').pop().replace('.wav', '');
    const body = wav(...TONES[id]);
    const m = /bytes=(\d+)-(\d*)/.exec(r.request().headers().range || '');
    if (!m) return r.fulfill({ contentType: 'audio/wav', body, headers: { 'accept-ranges': 'bytes' } });
    const from = +m[1], to = m[2] ? Math.min(+m[2], body.length - 1) : body.length - 1;
    return r.fulfill({
      status: 206, contentType: 'audio/wav', body: body.subarray(from, to + 1),
      headers: { 'accept-ranges': 'bytes', 'content-range': `bytes ${from}-${to}/${body.length}` },
    });
  });
  await page.route('**/assets/*/audio-url', (r) => {
    const id = r.request().url().split('/').slice(-2)[0];
    return r.fulfill({ contentType: 'application/json', body: JSON.stringify({ url: `/__tone/${id}.wav` }) });
  });
  await page.goto(`${base}e2e/audio-harness/index.html`);
  await page.waitForFunction(() => typeof window.__set === 'function');
  return page;
}

// Both speakers: heard live, the player sent everything to the left channel only.
// Plays a stereo file with a different tone on each side and a mono file, and
// reads the engine's left and right meters.
async function stereo() {
  for (const [label, body] of [['stereo file', wavStereo(440, 660, 6)], ['mono file', wav(440, 6)]]) {
    const page = await browser.newPage();
    await page.route('**/__tone/*.wav', (r) => r.fulfill({ contentType: 'audio/wav', body }));
    await page.route('**/assets/*/audio-url', (r) => r.fulfill({ contentType: 'application/json', body: JSON.stringify({ url: '/__tone/a.wav' }) }));
    await page.goto(`${base}e2e/audio-harness/index.html`);
    await page.waitForFunction(() => typeof window.__set === 'function');
    await page.evaluate((x) => window.__set(x), { ...A, startedAt: now() });
    await sleep(2500);
    const [l, r] = await page.evaluate(() => window.__levels());
    check(`stereo: ${label} reaches both speakers`, l > 0.01 && r > 0.01 && Math.abs(l - r) / Math.max(l, r) < 0.5, `left ${l}, right ${r}`);
    await page.close();
  }
}
function wavStereo(fl, fr, seconds) {
  const rate = 8000, n = rate * seconds, buf = Buffer.alloc(44 + n * 4);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 4, 4); buf.write('WAVEfmt ', 8);
  buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(2, 22);
  buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 4, 28); buf.writeUInt16LE(4, 32);
  buf.writeUInt16LE(16, 34); buf.write('data', 36); buf.writeUInt32LE(n * 4, 40);
  for (let i = 0; i < n; i++) {
    buf.writeInt16LE(Math.round(8000 * Math.sin((2 * Math.PI * fl * i) / rate)), 44 + i * 4);
    buf.writeInt16LE(Math.round(8000 * Math.sin((2 * Math.PI * fr * i) / rate)), 46 + i * 4);
  }
  return buf;
}

// F-PLAYER-14: an item starts at its cue-in point and plays at its replay gain; the
// next item, pre-loaded, starts at its own cue-in with its own gain
async function cueInAndReplayGain() {
  const page = await browser.newPage();
  await page.route('**/__tone/*.wav', (r) => {
    const id = r.request().url().split('/').pop().replace('.wav', '');
    return serveRange(r, wav(...TONES[id])); // byte ranges, so the player can seek
  });
  await page.route('**/assets/*/audio-url', (r) => {
    const id = r.request().url().split('/').slice(-2)[0];
    return r.fulfill({ contentType: 'application/json', body: JSON.stringify({ url: `/__tone/${id}.wav` }) });
  });
  await page.goto(`${base}e2e/audio-harness/index.html`);
  await page.waitForFunction(() => typeof window.__set === 'function');
  const set = (s) => page.evaluate((x) => window.__set(x), s);
  await set({ ...A, cross: 9, cueIn: 5, rg: -6, nextId: 'b', nextCueIn: 2, nextRg: -12, startedAt: now() });
  await sleep(1500);
  let snap = await page.evaluate(() => window.__snap());
  const a = snap.find((d) => d.src === 'a' && d.playing);
  check('cue-in: the item starts at its cue-in point (5 s), not at 0', !!a && a.t >= 5 && a.t <= 7.5, JSON.stringify(snap));
  check('replay gain: -6 dB plays at half level', !!a && Math.abs(a.gain - 0.5) < 0.03, a ? `gain ${a.gain}` : 'not playing');
  // The server counts an item's time from the top of the file (it hands over at
  // started_at + cross_start, and a resumed shiur is started with started_at moved back),
  // so a listener who joins later is put at the file's elapsed time. A listener who was
  // there when the item started is cue_in seconds ahead of that.
  xfail('cue-in: a listener there from the start is at the same point as one joining now', !!a && Math.abs(a.t - 1.5) < 1,
    'cue-in-ahead: player starts at cue_in but the server counts from 0, so listeners differ by cue_in and the hand-over waits up to cue_in - 1 s past the crossfade point',
    a ? `deck at ${a.t} s, station at 1.5 s` : '');
  // The player's own crossfade brings the next item in at its own cue-in (2 s)
  let x = null;
  for (let i = 0; i < 60 && !x; i++) {
    await sleep(200);
    snap = await page.evaluate(() => window.__snap());
    x = snap.find((d) => d.src === 'b' && d.playing);
  }
  check('cue-in: at the crossfade the next item comes in from its own cue-in (2 s)', !!x && x.t >= 2 && x.t <= 2.6, JSON.stringify(snap));
  await set({ ...B, cueIn: 2, rg: -12, nextId: 'c', startedAt: now() });
  await sleep(3000);
  snap = await page.evaluate(() => window.__snap());
  const b = snap.find((d) => d.src === 'b' && d.playing && d.gain > 0.05);
  check('cue-in: the next item plays on from its cue-in point', !!b && b.t >= 4.5 && b.t <= 8, JSON.stringify(snap));
  check('replay gain: the next item plays at its own gain (-12 dB = 0.25)', !!b && Math.abs(b.gain - 0.25) < 0.03, b ? `gain ${b.gain}` : 'not playing');
  await page.close();
}

try {
  if (!only || only === 'cue-gain') await cueInAndReplayGain();
  if (!only || only === 'stereo') await stereo();
  if (!only || only === 'listen-mid-item') await listenMidItem();
  if (!only || only === 'listen-cold') await listenFromCold();
  if (!only || only === 'stop-then-listen') await stopThenListen();
  for (const [name, sc] of Object.entries(SCENARIOS)) {
    if (!only || only === name) await run(name, sc);
  }
  if (!only || only === 'release-hold') await releaseHold();
} finally {
  await browser.close();
  await server.close();
}
process.exit(failures ? 1 : 0);
