/**
 * Shared pieces for the browser checks that run the built site against a fake,
 * in-memory backend (no real station is touched):
 *   - toneWav / serveRange: test audio files, served with byte ranges like the file store
 *   - AUDIO_PROBE: an init script that lets a check see what each player deck is doing
 *   - makeStation: a fake queue + now-playing that answers the control room's requests
 *   - routeApi / routeNowPlaying: wire a page to the fake backend and push channel
 *   - checker: PASS/FAIL lines, with a list of known site bugs that are expected to fail
 */
import { createRequire } from 'node:module';

export const { chromium, webkit, devices } = createRequire(import.meta.url)('playwright');

/** PASS/FAIL printing. `known` maps a check name to the bug it shows: those are
 *  expected to fail (printed XFAIL) and a pass there is an error (XPASS) so the
 *  list is kept honest, like pytest's xfail(strict=True). */
export function checker(known = {}) {
  const state = { failures: 0, passes: 0, xfail: 0, results: [] };
  const check = (name, ok, detail = '') => {
    const bug = Object.entries(known).find(([k]) => name.includes(k))?.[1];
    let tag;
    if (bug) {
      tag = ok ? 'XPASS' : 'XFAIL';
      if (ok) state.failures++; else state.xfail++;
    } else {
      tag = ok ? 'PASS' : 'FAIL';
      if (ok) state.passes++; else state.failures++;
    }
    state.results.push({ name, tag, detail });
    console.log(`${tag.padEnd(5)}  ${name}${detail ? `  (${detail})` : ''}${bug ? `  [BUG: ${bug}]` : ''}`);
  };
  const done = () => {
    console.log(`\n${state.passes} passed, ${state.xfail} known bugs (xfail), ${state.failures} failed`);
    return state.failures ? 1 : 0;
  };
  return { check, done, state };
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 8-bit mono WAV tone. 8 kHz keeps a 10-minute item under 5 MB. */
const toneCache = new Map();
export function toneWav(seconds = 600, freq = 440, rate = 8000, amp = 100) {
  const key = `${seconds}|${freq}|${rate}|${amp}`;
  if (toneCache.has(key)) return toneCache.get(key);
  const n = Math.round(seconds * rate);
  const buf = Buffer.alloc(44 + n);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n, 4); buf.write('WAVE', 8);
  buf.write('fmt ', 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate, 28); buf.writeUInt16LE(1, 32); buf.writeUInt16LE(8, 34);
  buf.write('data', 36); buf.writeUInt32LE(n, 40);
  for (let i = 0; i < n; i++) buf[44 + i] = 128 + Math.round(amp * Math.sin(2 * Math.PI * freq * i / rate));
  toneCache.set(key, buf);
  return buf;
}

/** Answer a Playwright route with `body`, honouring byte ranges (so players can seek). */
export function serveRange(route, body, contentType = 'audio/wav') {
  const m = /bytes=(\d+)-(\d*)/.exec(route.request().headers().range || '');
  if (!m) return route.fulfill({ status: 200, contentType, headers: { 'accept-ranges': 'bytes' }, body });
  const from = +m[1];
  const to = m[2] ? Math.min(+m[2], body.length - 1) : body.length - 1;
  return route.fulfill({
    status: 206, contentType, body: body.subarray(from, to + 1),
    headers: { 'accept-ranges': 'bytes', 'content-range': `bytes ${from}-${to}/${body.length}` },
  });
}

/**
 * Init script: records every Audio element and the gain right after its media source
 * (that is a player deck), so a check can ask what is audible:
 *   window.__decks() -> [{ src, playing, t, gain }]
 *   window.__audios  -> every Audio made (PFL, weather preview...)
 */
export function AUDIO_PROBE() {
  const w = window;
  w.__audios = [];
  w.__deckGain = new Map();
  w.__sinks = [];
  const Native = w.Audio;
  w.Audio = function (...args) { const a = new Native(...args); w.__audios.push(a); return a; };
  w.Audio.prototype = Native.prototype;
  const AC = w.AudioContext;
  if (AC) {
    let pendingEl = null;
    const cmes = AC.prototype.createMediaElementSource;
    AC.prototype.createMediaElementSource = function (el) { pendingEl = el; return cmes.call(this, el); };
    const cg = AC.prototype.createGain;
    AC.prototype.createGain = function () {
      const g = cg.call(this);
      if (pendingEl) { w.__deckGain.set(pendingEl, g); pendingEl = null; }
      return g;
    };
  }
  // Gains wired straight to the speakers: the page's output level (volume, mute)
  w.__outs = [];
  const connect = AudioNode.prototype.connect;
  AudioNode.prototype.connect = function (dest, ...rest) {
    if (typeof GainNode !== 'undefined' && this instanceof GainNode && typeof AudioDestinationNode !== 'undefined' && dest instanceof AudioDestinationNode) w.__outs.push(this);
    return connect.call(this, dest, ...rest);
  };
  // The player's own output: the speaker gain in the same audio context as the decks
  w.__outLevel = () => {
    const deck = [...w.__deckGain.values()][0];
    const mine = w.__outs.filter((g) => !deck || g.context === deck.context);
    return mine.length ? +mine[mine.length - 1].gain.value.toFixed(3) : null;
  };
  // Lock screen / car controls: keep the handlers the page sets so a check can press them
  w.__msHandlers = {};
  if (navigator.mediaSession) {
    const sah = navigator.mediaSession.setActionHandler.bind(navigator.mediaSession);
    navigator.mediaSession.setActionHandler = (action, fn) => { w.__msHandlers[action] = fn; try { sah(action, fn); } catch (e) { if (fn) throw e; } };
  }
  const ssid = HTMLMediaElement.prototype.setSinkId;
  if (ssid) HTMLMediaElement.prototype.setSinkId = function (id) { w.__sinks.push(id); return ssid.call(this, id).catch(() => undefined); };
  const name = (a) => decodeURIComponent((a.currentSrc || a.src || '').split('/').pop() || '').replace(/\.(wav|mp3)$/, '').replace(/\?.*$/, '');
  w.__decks = () => [...w.__deckGain.entries()].map(([a, g]) => ({
    src: name(a), playing: !a.paused && !a.ended && a.readyState >= 2, t: +a.currentTime.toFixed(2), gain: +g.gain.value.toFixed(3),
  }));
  w.__deckEls = () => [...w.__deckGain.keys()];
  w.__audible = () => w.__decks().filter((d) => d.playing && d.gain > 0.05);
}

/** Watch the decks for `ms`, sampling every `step` ms (on the page's own clock). */
export async function watchDecks(page, ms, step = 50) {
  return page.evaluate(([ms, step]) => new Promise((resolve) => {
    const t0 = Date.now();
    const out = [];
    const tick = () => {
      out.push({ at: Date.now() - t0, now: Date.now(), decks: window.__decks() });
      if (Date.now() - t0 >= ms) resolve(out); else setTimeout(tick, step);
    };
    tick();
  }), [ms, step]);
}

/**
 * A fake station: the queue, assets and the answers the control room expects.
 * opts.stations: list of {id,name,timezone}; opts.queueSize; opts.extra(path...) for overrides.
 */
export function makeStation(opts = {}) {
  const SID = opts.sid || 'st-1';
  const assets = [];
  const types = ['music', 'jingle', 'spot', 'shiur', 'zmanim'];
  for (let i = 1; i <= 140; i++) {
    assets.push({ id: `m${i}`, title: i % 7 === 0 ? `Nigun Simcha ${i}` : `Song number ${i} with a fairly long descriptive title`, artist: `Artist ${i % 9}`, album: `Album ${i % 5}`, duration: 150 + (i % 90), asset_type: 'music', category: i % 3 ? 'lively' : 'slow' });
  }
  for (let i = 1; i <= 9; i++) assets.push({ id: `j${i}`, title: `Station ID ${i}`, artist: null, album: null, duration: 6 + i, asset_type: 'jingle', category: 'ids', intro_seconds: i === 3 ? 6 : null });
  for (let i = 1; i <= 4; i++) assets.push({ id: `s${i}`, title: `Kol Berama sponsor business ${i}`, artist: null, album: null, duration: 30, asset_type: 'spot', category: null });
  assets.push({ id: 'tc', title: 'Time check', artist: null, album: null, duration: 5, asset_type: 'zmanim', category: 'time' });
  assets.push({ id: 'wx', title: 'Weather', artist: null, album: null, duration: 20, asset_type: 'spot', category: 'weather' });
  assets.push({ id: 'sil', title: 'Silence', artist: null, album: null, duration: 600, asset_type: 'silence', category: null });
  if (opts.noJingles) for (let i = assets.length - 1; i >= 0; i--) if (assets[i].asset_type === 'jingle') assets.splice(i, 1);
  const byId = Object.fromEntries(assets.map((a) => [a.id, a]));
  const stations = opts.stations || [
    { id: SID, name: 'Kol Berama', timezone: 'America/New_York', is_active: true, channels: [] },
    { id: 'st-2', name: 'Second Feed', timezone: 'Asia/Jerusalem', is_active: true, channels: [] },
  ];
  let seq = 0;
  let queue = [];
  const startedAgo = opts.startedAgo ?? 20;
  const queueSize = opts.queueSize ?? 40;
  for (let i = 1; i <= queueSize; i++) {
    queue.push({ id: `q${++seq}`, asset_id: `m${((i - 1) % 140) + 1}`, position: i, status: i === 1 && !opts.stopped ? 'playing' : 'pending', started_at: i === 1 && !opts.stopped ? new Date(Date.now() - startedAgo * 1000).toISOString() : null });
  }
  const calls = [];
  const fired = new Map();
  let loopId = null;
  const live = { shows: opts.shows ? [...opts.shows] : [{ id: 'ls1', station_id: SID, title: 'Thursday Night Live', status: 'scheduled', scheduled_start: new Date(Date.now() + 3600e3).toISOString() }] };
  const listeners = new Set(); // push(data) callbacks, set by routeNowPlaying
  const s = {
    SID, assets, byId, calls, live, stations,
    get queue() { return queue; },
    set queue(q) { queue = q; },
    skipReply: null,          // extra fields for the skip answer: { protected: true } ...
    panicReply: null,         // answer for /queue/panic instead of cutting, e.g. { message: 'The playout engine did not respond' }
    failNext: new Set(),      // paths that answer 500 once
    replyNext: new Map(),     // path -> [{ status, detail } | 'network', ...]: the next answers for that path, one per request
    playout: opts.playout || { stations: {}, default: { mode: 'browser' } },
    weather: opts.weather ?? { time_url: '/__tone/tc.wav', weather_url: '/__tone/wx.wav' },
    nextEntryId: undefined,   // override next_entry_id in the queue answer
    pushOnChange: opts.pushOnChange ?? true,
    modes: { hard_start: true, spots: true },
  };
  const pending = () => queue.filter((e) => e.status === 'pending').sort((a, b) => a.position - b.position);
  const playing = () => queue.find((e) => e.status === 'playing');
  s.pending = pending;
  s.playing = playing;
  s.add = (e) => { const x = { id: `q${++seq}`, status: 'pending', ...e }; queue.push(x); return x; };
  const row = (e, est) => {
    const a = byId[e.asset_id];
    return {
      id: e.id, station_id: SID, asset_id: e.asset_id, position: e.position, status: e.status, estimated_start: est,
      source: e.source ?? 'manual', preempt_at: e.preempt_at ?? null, hard_timed: e.hard_timed, hard_by: e.hard_by ?? null,
      blackout_name: e.blackout_name,
      asset: { id: a.id, title: a.title, artist: a.artist, duration: a.duration, asset_type: a.asset_type, category: a.category, intro_seconds: a.intro_seconds ?? null },
    };
  };
  // Rows of the queue an action sends back, as the real server (queue.py ACTION_QUEUE_ROWS)
  const ACTION_QUEUE_ROWS = 150;
  s.view = (limit = Infinity) => {
    const list = [playing(), ...pending()].filter(Boolean);
    let cursor = Date.now();
    const entries = list.map((e) => {
      const a = byId[e.asset_id];
      let est;
      if (e.status === 'playing') { est = e.started_at; cursor = new Date(e.started_at).getTime() + a.duration * 1000; }
      else { if (e.preempt_at) cursor = e.hard_timed === false ? Math.max(cursor, new Date(e.preempt_at).getTime()) : new Date(e.preempt_at).getTime(); est = new Date(cursor).toISOString(); cursor += a.duration * 1000; }
      return row(e, est);
    });
    const p = entries.find((e) => e.status === 'playing');
    const total = entries.reduce((n, e) => n + (e.asset.duration || 0), 0);
    const next = s.nextEntryId !== undefined ? s.nextEntryId : (pending()[0]?.id ?? null);
    return {
      loop_asset_id: loopId, entries: entries.slice(0, limit), total: Math.min(limit, entries.length), total_all: entries.length,
      now_playing: p ? { ...p, started_at: playing().started_at } : null,
      queue_duration_seconds: total, total_queue_duration_seconds: total, preempt_fade_ms: 2000, next_entry_id: next,
      modes: s.modes, // like the real queue body: HS and Spots mode
    };
  };
  /** When a queued timed entry cuts into the item on air, as the server's _cut_time:
   *  a hard item at its time, a soft one only at its limit (hard_by), else null. */
  const cutAt = (e) => {
    if (!e || !e.preempt_at) return null;
    if (e.hard_timed === false) {
      return e.hard_by ? new Date(Math.max(new Date(e.preempt_at).getTime(), new Date(e.hard_by).getTime())).toISOString() : null;
    }
    return new Date(e.preempt_at).toISOString();
  };
  /** What the server pushes on the now-playing channel for the item on air.
   *  Nothing on air: null, as the real server sends ({type:'now_playing', data:null}
   *  on the socket, null from GET /now-playing). s.legacyStopShape = true sends the
   *  shape this fake used before ({station_id, started_at:null}); pages handle both. */
  s.legacyStopShape = !!opts.legacyStopShape;
  s.nowPlaying = (extra = {}) => {
    const cur = playing();
    if (!cur) return s.legacyStopShape ? { station_id: SID, started_at: null } : null;
    const a = byId[cur.asset_id];
    const nxt = pending()[0];
    const na = nxt && byId[nxt.asset_id];
    return {
      station_id: SID, asset_id: a.id, started_at: cur.started_at,
      ends_at: new Date(new Date(cur.started_at).getTime() + a.duration * 1000).toISOString(),
      asset: { title: a.title, artist: a.artist, album: a.album, audio_url: `/__tone/${a.id}.wav`, cue_in: 0, cue_out: a.duration, cross_start: a.duration - 3, replay_gain_db: 0, intro_seconds: a.intro_seconds ?? null },
      next_asset: na ? { id: na.id, title: na.title, artist: na.artist, audio_url: `/__tone/${na.id}.wav`, cue_in: 0, replay_gain_db: 0, cut_at: cutAt(nxt) } : null,
      ...extra,
    };
  };
  s.onPush = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
  s.push = (data) => { const d = data === undefined ? s.nowPlaying() : data; listeners.forEach((fn) => fn(d)); };
  const announce = (extra) => { if (s.pushOnChange) setTimeout(() => s.push(s.nowPlaying(extra)), 0); };
  /** Put `assetId` on air now (as the scheduler would), and announce it. */
  s.goOnAir = (assetId, extra = {}) => {
    const cur = playing();
    if (cur) cur.status = 'played';
    const waiting = pending().find((e) => e.asset_id === assetId);
    const e = waiting || s.add({ asset_id: assetId, position: (cur?.position ?? 0) + 1 });
    e.status = 'playing';
    e.started_at = extra.started_at || new Date().toISOString();
    announce(extra.push);
    return e;
  };

  s.handle = (method, path, query, body) => {
    calls.push({ method, path, query, body, at: Date.now() });
    const q = `/stations/${SID}/queue`;
    if (opts.extra) { const r = opts.extra(method, path, query, body); if (r !== undefined) return r; }
    if (path === '/auth/me') return { id: 'u1', email: 'operator@example.com', role: 'admin', is_active: true };
    if (path === '/stations') return { stations, total: stations.length };
    if (/^\/stations\/[^/]+$/.test(path)) {
      const st = stations.find((x) => x.id === path.split('/').pop());
      return st ? { ...st, type: 'internet', description: st.description ?? 'Music all day' } : { __status: 404, detail: 'Not found' };
    }
    if (path === '/asset-types') return types.map((name, i) => ({ id: `t${i}`, name }));
    if (path === '/categories') return [];
    if (path === '/playout-status') return s.playout;
    if (path === '/site-release' && method === 'GET') return { mode: 'auto', push: null, server_time: new Date().toISOString() };
    if (/^\/assets\/[^/]+\/audio-url$/.test(path)) return { url: `/__tone/${path.split('/')[2]}.wav` };
    if (path === '/alerts/unresolved-count') return { unresolved_count: 0 };
    if (path === '/alerts') return { alerts: [], total: 0 };
    if (path === '/live-shows' && method === 'GET') return { shows: live.shows, total: live.shows.length };
    if (path === '/live-shows' && method === 'POST') {
      const show = { id: `ls${live.shows.length + 1}`, station_id: body.station_id, title: body.title, status: 'scheduled', scheduled_start: new Date().toISOString() };
      live.shows.push(show);
      return show;
    }
    const lsm = /^\/live-shows\/([^/]+)\/(start|end)$/.exec(path);
    if (lsm) { const sh = live.shows.find((x) => x.id === lsm[1]); sh.status = lsm[2] === 'start' ? 'live' : 'ended'; return sh; }
    if (/^\/live-shows\/station\/[^/]+\/active$/.test(path)) {
      const sh = live.shows.find((x) => x.status === 'live');
      return { active: !!sh, show: sh ? { description: null, broadcast_mode: 'webrtc', icecast_mount: null, actual_start: null, scheduled_end: null, calls_enabled: true, ...sh } : null };
    }
    if (path === '/schedules/timeline-preview') return { station_id: SID, at_time: query.at_time || new Date().toISOString(), is_blacked_out: !!query.at_time, active_block: { id: 'b', name: query.at_time ? 'Night shiurim' : 'Afternoon music', schedule_name: 'Weekday', start_time: '12:00', end_time: '18:00', playback_mode: 'shuffle' }, current_blackout: query.at_time ? { name: 'Shabbos', start_datetime: query.at_time, end_datetime: query.at_time } : null, next_blackout: null };
    if (path === '/assets') {
      let list = assets.filter((a) => a.asset_type !== 'silence' && a.id !== 'tc' && a.id !== 'wx');
      if (query.asset_type) list = list.filter((a) => a.asset_type === query.asset_type);
      if (query.search) { const t = query.search.toLowerCase(); list = list.filter((a) => a.title.toLowerCase().includes(t) || (a.artist || '').toLowerCase().includes(t)); }
      const skip = +(query.skip || 0), limit = +(query.limit || 50);
      return { assets: list.slice(skip, skip + limit), total: list.length };
    }
    if (/^\/assets\/[^/?]+$/.test(path)) { const a = byId[path.split('/')[2]]; return a ? { ...a, metadata_extra: {} } : { __status: 404 }; }
    if (/^\/now-playing\/[^/]+$/.test(path) || path === `/stations/${SID}/now-playing`) { const np = s.nowPlaying(); return np === null ? { __body: 'null' } : np; }
    if (path.startsWith('/stations/') && path.split('/')[2] !== SID && path.endsWith('/queue')) return { entries: [], total: 0, total_all: 0, now_playing: null, queue_duration_seconds: 0, next_entry_id: null };
    if (path.startsWith('/stations/') && path.split('/')[2] !== SID) return {};
    if (path === q && method === 'GET') return s.view(+(query.limit || Infinity));
    if (path === `${q}/log`) return { logs: [{ id: 'l1', start_utc: new Date(Date.now() - 300e3).toISOString(), title: 'Earlier song', artist: 'Artist 2', asset_type: 'music', source: 'auto' }] };
    if (path === `${q}/last-played`) return { last_played: { m3: new Date(Date.now() - 7200e3).toISOString() } };
    if (path === `${q}/preview-weather`) return s.weather === 'error' ? { __status: 500, detail: 'boom' } : s.weather;
    if (path === `${q}/add`) {
      const pos = Math.max(0, ...pending().map((e) => e.position)) + 1;
      const e = s.add({ asset_id: body.asset_id, position: pos });
      return { id: e.id, position: pos, status: 'pending' };
    }
    if (path === `${q}/play-next`) {
      pending().forEach((e) => { e.position += 1; });
      const cur = playing();
      const e = s.add({ asset_id: body.asset_id, position: cur ? cur.position + 1 : 1 });
      return { id: e.id, position: e.position };
    }
    if (path === `${q}/fire-now`) {
      if (body.client_request_id && fired.has(body.client_request_id)) return { ...fired.get(body.client_request_id), duplicate: true };
      pending().forEach((e) => { e.position += 1; });
      const cur = playing();
      const e = s.add({ asset_id: body.asset_id, position: cur ? cur.position + 1 : 1 });
      if (cur) cur.status = 'skipped';
      e.status = 'playing'; e.started_at = new Date().toISOString();
      loopId = null;
      const done = { id: e.id, position: e.position, on_air: true, queue: s.view(ACTION_QUEUE_ROWS) };
      if (body.client_request_id) fired.set(body.client_request_id, done);
      announce();
      return done;
    }
    if (path === `${q}/modes`) {
      s.modes = { hard_start: body?.hard_start ?? s.modes.hard_start, spots: body?.spots ?? s.modes.spots };
      return { modes: s.modes, queue: s.view(ACTION_QUEUE_ROWS) };
    }
    if (path === `${q}/loop`) { loopId = body.on ? (playing()?.asset_id ?? null) : null; return { loop: !!loopId, asset_id: loopId, queue: s.view(ACTION_QUEUE_ROWS) }; }
    if (path === `${q}/mix-over`) return { message: 'Playing over', asset_id: body.asset_id, on_air: true };
    if (path === `${q}/insert`) {
      const anchor = body.after_entry_id ? queue.find((x) => x.id === body.after_entry_id && x.status !== 'skipped') : null;
      const after = anchor ? anchor.position : (playing()?.position ?? 0);
      const ids = [...(body.asset_id ? [body.asset_id] : []), ...(body.asset_ids || [])];
      pending().forEach((x) => { if (x.position > after) x.position += ids.length; });
      const made = ids.map((asset_id, i) => s.add({ asset_id, position: after + 1 + i }).id);
      return { ids: made, queue: s.view(ACTION_QUEUE_ROWS) };
    }
    if (path === `${q}/start`) {
      const cur = playing();
      if (cur) return { now_playing: cur.asset_id };
      const next = pending()[0];
      if (!next) return { now_playing: null, message: 'The queue is empty. Add something from the library first.' };
      next.status = 'playing'; next.started_at = new Date().toISOString();
      announce();
      return { now_playing: next.asset_id };
    }
    if (path === `${q}/panic`) {
      if (s.panicReply) return { ...s.panicReply, queue: s.view(ACTION_QUEUE_ROWS) };
      const cur = playing();
      if (cur) cur.status = 'skipped';
      loopId = null;
      const next = pending()[0];
      if (next) { next.status = 'playing'; next.started_at = new Date().toISOString(); }
      announce({ change_fade_seconds: 0 });
      return { message: 'Skipped', now_playing: next ? next.asset_id : null, queue: s.view(ACTION_QUEUE_ROWS) };
    }
    if (path === `${q}/skip`) {
      if (s.skipReply) return { message: 'Kept', ...s.skipReply, queue: s.view(ACTION_QUEUE_ROWS) };
      const cur = playing();
      if (cur) cur.status = 'skipped';
      const fade = { hard: 0, fade: 0.5, soft: 3 }[query.fade ?? 'fade'];
      loopId = null;
      const next = pending()[0];
      if (next) { next.status = 'playing'; next.started_at = new Date().toISOString(); }
      announce({ change_fade_seconds: fade });
      return { message: 'Skipped', now_playing: next ? next.asset_id : null, queue: s.view(ACTION_QUEUE_ROWS) };
    }
    if (path === `${q}/move-up` || path === `${q}/move-down`) {
      const list = pending();
      const i = list.findIndex((x) => x.id === body.entry_id);
      const j = path.endsWith('up') ? i - 1 : i + 1;
      if (i >= 0 && j >= 0 && j < list.length) { const p = list[i].position; list[i].position = list[j].position; list[j].position = p; }
      return { message: 'Moved' };
    }
    if (path === `${q}/reorder-dnd`) {
      const e = queue.find((x) => x.id === body.entry_id);
      const oldPos = e.position, newPos = body.new_position;
      for (const x of pending()) {
        if (x === e) continue;
        if (oldPos < newPos && x.position > oldPos && x.position <= newPos) x.position -= 1;
        if (oldPos > newPos && x.position >= newPos && x.position < oldPos) x.position += 1;
      }
      e.position = newPos;
      return { message: 'Reordered', warnings: s.reorderWarnings || [] };
    }
    if (method === 'DELETE' && path.startsWith(`${q}/`)) { queue = queue.filter((x) => x.id !== path.split('/').pop()); return null; }
    // Public listener page
    if (path === '/song-requests' && method === 'POST') {
      if (!body.song_title) return { __status: 422 };
      return /fail/i.test(body.song_title) ? { __status: 500 } : { id: 'r1', status: 'approved', auto_approved: true, matched_asset_title: 'Song number 12', matched_asset_artist: 'Artist 3', songs_ahead: 2, estimated_wait_minutes: 7 };
    }
    if (path === '/listeners/heartbeat' || path === '/listeners/disconnect' || path === '/listeners/leave') return { ok: true };
    if (path === '/crm/login') return ['123456', '654321'].includes(body.pin) ? s.crmProfile() : { __status: 401, detail: 'Invalid PIN or inactive member' };
    if (path === '/crm/register') { if (!body.name) return { __status: 422 }; s.crmName = body.name; return { id: 'cm1', name: body.name, pin: '654321' }; }
    if (path === '/crm/rate') {
      // As the server: rating 1-5 is required, one row per song (upsert)
      if (!(body.rating >= 1 && body.rating <= 5)) return { __status: 422, detail: 'rating must be 1-5' };
      s.ratings.set(body.asset_id, { rating: body.rating, is_favorite: !!body.is_favorite });
      return { id: `r-${body.asset_id}`, asset_id: body.asset_id, rating: body.rating, is_favorite: !!body.is_favorite, asset_title: byId[body.asset_id]?.title ?? null, asset_artist: null, created_at: null };
    }
    if (path === '/crm/raffles/active') return s.raffles;
    if (/^\/crm\/raffles\/[^/]+\/enter$/.test(path)) {
      const id = path.split('/')[3];
      if (s.raffleEntered.has(id)) return { __status: 409, detail: 'Already entered this raffle' };
      s.raffleEntered.add(id);
      return { status: 'entered', raffle_id: id };
    }
    if (path === '/crm/my-ratings') return [...s.ratings.entries()].map(([asset_id, r]) => ({ id: `r-${asset_id}`, asset_id, ...r, asset_title: byId[asset_id]?.title ?? null, asset_artist: null, created_at: null }));
    return {};
  };
  s.ratings = new Map();          // CRM: asset id -> { rating, is_favorite } for the one member
  s.raffleEntered = new Set();    // CRM: raffles the member entered (a second entry answers 409)
  s.raffles = [{ id: 'rf1', title: 'Win a Shabbos package', prize: 'Wine and challah', entry_count: 12, status: 'open' }];
  s.crmProfile = () => ({ id: 'cm1', name: s.crmName || 'Test Listener', ratings_count: s.ratings.size, favorites_count: [...s.ratings.values()].filter((r) => r.is_favorite).length, taste_profile: { label: 'Kumzitz lover', description: 'Slow and soulful', top_category: null, stats: null } });
  return s;
}

/** Route /api/v1/** of a page (or context) to a fake station. delay(): ms per answer. */
export async function routeApi(target, station, { delay = () => 0, down = [] } = {}) {
  await target.route('**/api/v1/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/^.*\/api\/v1/, '');
    if (down.some((d) => path.startsWith(d))) return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"down"}' });
    let body = null;
    try { body = req.postDataJSON(); } catch { /* none */ }
    const queued = station.replyNext.get(path);
    if (queued && queued.length) {
      const r = queued.shift();
      station.calls.push({ method: req.method(), path, query: Object.fromEntries(url.searchParams), body, at: Date.now(), failed: true });
      const d = delay(path, req.method());
      if (d) await sleep(d);
      if (r === 'network') return route.abort('failed').catch(() => {});
      return route.fulfill({ status: r.status, contentType: 'application/json', body: JSON.stringify({ detail: r.detail ?? 'error' }) }).catch(() => {});
    }
    if (station.failNext.has(path)) {
      station.failNext.delete(path);
      station.calls.push({ method: req.method(), path, query: Object.fromEntries(url.searchParams), body, at: Date.now(), failed: true });
      const d = delay(path, req.method());
      if (d) await sleep(d);
      return route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"Server error"}' }).catch(() => {});
    }
    const data = station.handle(req.method(), path, Object.fromEntries(url.searchParams), body);
    const d = delay(path, req.method());
    if (d) await sleep(d);
    // { __body: 'null' }: a 200 whose JSON is null (GET /now-playing with nothing on air)
    if (data && typeof data.__body === 'string') return route.fulfill({ status: 200, contentType: 'application/json', body: data.__body }).catch(() => {});
    const status = data && data.__status ? data.__status : data === null ? 204 : 200;
    await route.fulfill({ status, contentType: 'application/json', body: data === null ? '' : JSON.stringify(data) }).catch(() => {});
  });
  // Test tones: /__tone/<asset id>.wav, as long as the asset, each its own pitch
  await target.route('**/__tone/*', (route) => {
    const id = decodeURIComponent(route.request().url().split('/').pop().replace(/\.wav.*$/, ''));
    const a = station.byId[id];
    const freq = 300 + ([...id].reduce((n, c) => n + c.charCodeAt(0), 0) % 40) * 15;
    return serveRange(route, toneWav(Math.min(a?.duration ?? 60, 600), freq));
  });
}

/** Stand in for /ws/now-playing/<station>: the page gets the station's pushes. */
export async function routeNowPlaying(target, station, { greet = true } = {}) {
  const sockets = [];
  const opened = [];
  await target.routeWebSocket(/\/ws\/now-playing\//, (ws) => {
    const sid = ws.url().split('/').pop();
    opened.push({ url: ws.url(), sid, at: Date.now() });
    const entry = { ws, sid, open: true };
    sockets.push(entry);
    ws.onClose(() => { entry.open = false; });
    if (sid === station.SID && greet) setTimeout(() => { try { ws.send(JSON.stringify({ type: 'now_playing', data: station.nowPlaying() })); } catch { /* closed */ } }, 20);
  });
  station.onPush((data) => sockets.forEach((e) => {
    if (e.open && e.sid === station.SID) { try { e.ws.send(JSON.stringify({ type: 'now_playing', data })); } catch { /* closed */ } }
  }));
  return {
    sockets, opened,
    raw: (msg) => sockets.forEach((e) => { if (e.open) { try { e.ws.send(JSON.stringify(msg)); } catch { /* closed */ } } }),
    dropAll: (code = 1006) => sockets.forEach((e) => { if (e.open) { e.open = false; e.ws.close({ code: code === 1006 ? 1011 : code }).catch(() => {}); } }),
  };
}

/** The site channel every tab keeps open: sends the release policy with the
 *  server's clock (as the backend does on connect), then stays quiet. */
export async function routeSiteSocket(target) {
  await target.routeWebSocket(/\/ws\/site/, (ws) => {
    ws.send(JSON.stringify({ type: 'release', data: { mode: 'auto', push: null, server_time: new Date().toISOString() } }));
  });
  await target.routeWebSocket(/\/ws\/(live|listeners)/, () => {});
}

/** Init script: this device's clock is `ms` off (a PC whose clock is 20 s fast). */
export function skewClock(ms) {
  const Real = Date;
  const realNow = Real.now.bind(Real);
  class Skewed extends Real {
    constructor(...a) { if (a.length === 0) super(realNow() + ms); else super(...a); }
    static now() { return realNow() + ms; }
  }
  window.Date = Skewed;
}

/** A new context signed in to the admin site, with the probe and the fake station wired. */
export async function adminContext(browser, contextOpts, station, { delay, down, storage = {}, ws = {} } = {}) {
  const ctx = await browser.newContext(contextOpts);
  await ctx.addInitScript((st) => {
    localStorage.setItem('access_token', 'test'); localStorage.setItem('refresh_token', 'test');
    for (const [k, v] of Object.entries(st)) { if (sessionStorage.getItem('__seeded_' + k)) continue; if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); sessionStorage.setItem('__seeded_' + k, '1'); }
  }, storage);
  await ctx.addInitScript(AUDIO_PROBE);
  await routeApi(ctx, station, { delay, down });
  const np = await routeNowPlaying(ctx, station, ws);
  return { ctx, np };
}

export async function newPage(ctx) {
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  return { page, errors };
}

/**
 * Press `locator` with a real click and time, on the page's clock, from the press
 * (pointerdown) until `cond` (a function run in the page) is true.
 * Returns ms, or -1 if it never happened within `timeout`.
 */
export async function timeClick(page, locator, cond, timeout = 5000, how = 'click') {
  await page.evaluate(() => {
    window.__t0 = null;
    const mark = () => { if (window.__t0 === null) window.__t0 = performance.now(); };
    window.addEventListener('pointerdown', mark, { capture: true, once: true });
    window.addEventListener('touchstart', mark, { capture: true, once: true });
  });
  const waiter = page.evaluate(([src, timeout]) => new Promise((resolve) => {
    const fn = new Function(`return (${src})();`);
    const begin = performance.now();
    const look = () => {
      let ok = false;
      try { ok = !!fn(); } catch { ok = false; }
      if (ok && window.__t0 !== null) resolve(Math.round(performance.now() - window.__t0));
      else if (performance.now() - begin > timeout) resolve(-1);
      else setTimeout(look, 5);
    };
    look();
  }), [String(cond), timeout]);
  await (how === 'tap' ? locator.tap() : locator.click());
  return waiter;
}

/** Time, on the page's clock, until `cond` (a function run in the page) holds. */
export async function timeUntil(page, cond, timeout = 5000) {
  return page.evaluate(([src, timeout]) => new Promise((resolve) => {
    const fn = new Function(`return (${src})();`);
    const begin = performance.now();
    const look = () => {
      let ok = false;
      try { ok = !!fn(); } catch { ok = false; }
      if (ok) resolve(Math.round(performance.now() - begin));
      else if (performance.now() - begin > timeout) resolve(-1);
      else setTimeout(look, 5);
    };
    look();
  }), [String(cond), timeout]);
}

/**
 * Init script: iPhone Safari's rule for sound, which Chromium does not have.
 * Sound may start only inside a real tap (touchend that was not a scroll, click,
 * keydown, mouseup); touchstart and pointerdown do NOT count. Outside one:
 *   - audio.play() is refused (NotAllowedError), unless that same element was once
 *     started inside a tap (it may then play again later, with any src)
 *   - a new AudioContext is suspended and resume() never settles
 * window.__iosPolicy = { refusedPlays, pendingResumes } counts what was refused.
 */
export function IOS_SOUND_POLICY() {
  const w = window;
  const stats = (w.__iosPolicy = { refusedPlays: 0, pendingResumes: 0 });
  let inTap = false;
  let moved = false;
  const open = () => { inTap = true; setTimeout(() => { inTap = false; }, 0); };
  w.addEventListener('touchstart', () => { moved = false; }, true);
  w.addEventListener('touchmove', () => { moved = true; }, true);
  w.addEventListener('touchend', () => { if (!moved) open(); }, true);
  for (const type of ['click', 'keydown', 'mouseup']) w.addEventListener(type, open, true);

  const blessed = new WeakSet();
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () {
    if (inTap) blessed.add(this);
    if (!blessed.has(this)) {
      stats.refusedPlays++;
      return Promise.reject(new DOMException('The request is not allowed by the user agent', 'NotAllowedError'));
    }
    return play.call(this);
  };

  const Native = w.AudioContext;
  if (!Native) return;
  const locked = new WeakSet();
  const nativeState = Object.getOwnPropertyDescriptor(BaseAudioContext.prototype, 'state').get;
  class Ctx extends Native {
    constructor(...a) {
      super(...a);
      if (!inTap) { locked.add(this); Native.prototype.suspend.call(this).catch(() => {}); }
    }
    get state() { return locked.has(this) ? 'suspended' : nativeState.call(this); }
    resume() {
      if (locked.has(this) && !inTap) { stats.pendingResumes++; return new Promise(() => {}); }
      locked.delete(this);
      return Native.prototype.resume.call(this);
    }
  }
  w.AudioContext = Ctx;
}
