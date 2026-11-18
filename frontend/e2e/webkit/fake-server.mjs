/**
 * The fake backend of e2e/lib/fake-station.mjs as a plain HTTP + WebSocket server,
 * so a browser that Playwright cannot drive (WebKitGTK's MiniBrowser through
 * WebKitWebDriver) can use it. One origin serves everything, like kbrlive.com:
 *
 *   /api/v1/**              makeStation().handle(...)  (the same answers as the Playwright checks)
 *   /api/v1/ws/now-playing  the station's pushes; /ws/site the release policy;
 *   /api/v1/ws/live/call/*  a fake call socket (records the auth token, sends call_status)
 *   /__tone/<id>.wav        test tones with byte ranges
 *   /version.json           the published release (changed by /__ctl/release)
 *   everything else         proxied to the built site (`vite preview` on UPSTREAM), and
 *                           each HTML page gets AUDIO_PROBE, an error recorder
 *                           (window.__errs) and the ?kbr_no_mse=1 switch at the top of <head>
 *   /__ctl/*                test control, see `ctl` below
 * An MP3 stream server (e2e/lib/stream-server.mjs) runs on STREAM_PORT for the
 * server-stream station.
 *
 *   PORT=4184 UPSTREAM=http://localhost:4183 STREAM_PORT=4185 node e2e/webkit/fake-server.mjs
 */
import http from 'node:http';
import crypto from 'node:crypto';
import { makeStation, toneWav, AUDIO_PROBE } from '../lib/fake-station.mjs';
import { startStreamServer } from '../lib/stream-server.mjs';

const PORT = Number(process.env.PORT || 4184);
const UPSTREAM = new URL(process.env.UPSTREAM || 'http://localhost:4183');
const STREAM_PORT = Number(process.env.STREAM_PORT || 4185);

const stream = await startStreamServer(STREAM_PORT);
const HLS_MOUNT_ID = '0b5e7c2a-1d4f-4e8b-9a6c-3f2d1e0c9b8a';

// Records page errors (uncaught, rejected promises, console.error) before the app loads
function ERROR_RECORDER() {
  window.__errs = [];
  const keep = (m) => { try { window.__errs.push(String(m).slice(0, 300)); } catch { /* ignore */ } };
  window.addEventListener('error', (e) => keep(e.message || e));
  window.addEventListener('unhandledrejection', (e) => keep('unhandled rejection: ' + (e.reason && (e.reason.stack || e.reason.message) || e.reason)));
  const ce = console.error.bind(console);
  console.error = (...a) => { keep(a.map((x) => (x && x.message) || x).join(' ')); ce(...a); };
}
// iPhone Safari has no MSE for MP3. WebKitGTK has it and, under WebDriver, ignores
// --enable-mediasource=false and --features=-MediaSource, so a check that wants the
// iPhone's path opens the first page with ?kbr_no_mse=1: the tab (reloads included)
// then has no MediaSource.
function NO_MSE() {
  try { if (/[?&]kbr_no_mse=1/.test(location.search)) sessionStorage.setItem('kbr_no_mse', '1'); } catch { /* ignore */ }
  let off = false;
  try { off = sessionStorage.getItem('kbr_no_mse') === '1'; } catch { /* ignore */ }
  if (off) { window.MediaSource = undefined; window.ManagedMediaSource = undefined; }
}
// iPhone Safari plays HLS natively and the site then takes the engine's HLS copy
// (liveStream.ts hlsStream). WebKitGTK has no native HLS, so ?kbr_native_hls=1 makes the
// tab say it has (canPlayType), and the fake HLS address serves the MP3 stream itself:
// the site's iPhone code path runs in the real engine.
function NATIVE_HLS() {
  try { if (/[?&]kbr_native_hls=1/.test(location.search)) sessionStorage.setItem('kbr_native_hls', '1'); } catch { /* ignore */ }
  let on = false;
  try { on = sessionStorage.getItem('kbr_native_hls') === '1'; } catch { /* ignore */ }
  if (!on) return;
  const can = HTMLMediaElement.prototype.canPlayType;
  HTMLMediaElement.prototype.canPlayType = function (type) {
    return /mpegurl/i.test(type) ? 'maybe' : can.call(this, type);
  };
}
const INJECT = `<script>(${ERROR_RECORDER})();(${NO_MSE})();(${NATIVE_HLS})();(${AUDIO_PROBE})();</script>`;

// ── The scenario ─────────────
let station;
let version = null;           // null: whatever the build says; else the "published" release
let policy = { mode: 'auto', push: null };
const sockets = { np: new Set(), site: new Set(), call: new Set() };
const call = { auths: [], opened: 0 };
// A new release went out while the tab was open: the host no longer has the old
// release's code files and answers them with the site's page (200, text/html), as
// Vercel does. `keep`: the files the tab already loaded (it has them in memory).
let redeploy = null;

function reset({ mode = 'browser', stream: kind = 'ok', show = true } = {}) {
  station = makeStation({
    shows: show ? [{ id: 'ls5', station_id: 'st-1', title: 'Ask the Rav', status: 'live', calls_enabled: true }] : [],
    extra: (method, path, query, body) => {
      if (method === 'POST' && path === '/call-in/st-1') { station.callBody = body; return { call_id: 'call-1', token: 'tok-1' }; }
      if (path.startsWith('/schedules/epg/')) {
        return { station_id: path.split('/').pop(), date: query.date || new Date().toISOString().slice(0, 10), blocks: [
          { id: 'b1', name: 'Morning music', description: 'Lively songs', start_time: '06:00:00', end_time: '12:00:00', playback_mode: 'shuffle', schedule_name: 'Weekday' },
          { id: 'b2', name: 'Afternoon shiurim', description: null, start_time: '12:00:00', end_time: '18:00:00', playback_mode: 'sequential', schedule_name: 'Weekday' },
        ] };
      }
      if (path === '/users/public/hosts') return { hosts: [{ id: 'h1', display_name: 'Reb Moshe', title: 'Morning host', bio: 'Thirty years on the air with a very long biography that should wrap on a phone screen without pushing the page sideways.', photo_url: null, social_links: null }] };
      if (path === '/archives') {
        const t = new Date(Date.now() - 86400e3).toISOString();
        return { archives: [{ id: 'a1', station_id: 'st-1', title: 'Motzei Shabbos Live with a rather long title for a narrow screen', description: 'Last week', host_name: 'Reb Moshe', recorded_at: t, duration_seconds: 3600, audio_url: '/__tone/m1.wav', cover_image_url: null, is_published: true, download_count: 3, live_show_id: null, created_at: t, updated_at: t }], total: 1 };
      }
      return undefined;
    },
  });
  if (mode === 'server') {
    // The site takes the HLS copy only for a mount named /live-<uuid>, as the engine's are
    const url = kind === 'missing' ? stream.missing : kind === 'hls' ? stream.url(HLS_MOUNT_ID) : stream.url(station.SID);
    const np = station.nowPlaying;
    station.nowPlaying = (x) => ({ ...np(x), stream_url: url });
    station.playout = { stations: { [station.SID]: { mode: 'server', engine: true, engine_ok: true, in_sync: true } }, default: { mode: 'browser' } };
  }
  station.onPush((data) => sendAll(sockets.np, { type: 'now_playing', data }));
  version = null;
  redeploy = null;
  policy = { mode: 'auto', push: null };
  call.auths = [];
  call.opened = 0;
}
reset();

const ctl = {
  // { mode: 'browser' | 'server', stream: 'ok' | 'missing' | 'hls', show: true }
  reset: (b) => { reset(b); return { ok: true, sid: station.SID }; },
  // The scheduler puts the next item on air (pushed on the now-playing channel)
  onair: () => { const id = station.pending()[0].asset_id; station.goOnAir(id); return { id, title: station.byId[id].title }; },
  // A new release is published and pushed: { include_playing }
  release: (b) => {
    version = `webkit-test-${Date.now()}`;
    const at = new Date().toISOString();
    policy = { mode: 'manual', push: { id: `p-${Date.now()}`, version, at, include_playing: !!b.include_playing, pushed_at: at } };
    sendAll(sockets.site, { type: 'release', data: { ...policy, server_time: at } });
    return { version };
  },
  callStatus: (b) => { sendAll(sockets.call, { type: 'call_status', status: b.status }); return { ok: true }; },
  state: () => ({
    sid: station.SID,
    playing: station.playing() && { id: station.playing().asset_id, title: station.byId[station.playing().asset_id].title },
    nowPlaying: station.nowPlaying(),
    calls: station.calls.map(({ method, path, body }) => ({ method, path, body })),
    callBody: station.callBody || null,
    callAuths: call.auths, callSockets: call.opened, streamConnects: stream.stats.connects, hlsConnects: stream.stats.hlsConnects, streamUrl: stream.url(station.SID),
  }),
  stall: (b) => { stream.stall(b.ms || 4000); return { ok: true }; },
  // { keep: [paths the tab has loaded] }: every other /assets/ file is gone from now on,
  // and /version.json names the new release
  redeploy: (b) => {
    redeploy = { keep: new Set(b.keep || []), missed: [] };
    version = `webkit-redeploy-${Date.now()}`;
    return { version };
  },
  missed: () => ({ missed: redeploy ? redeploy.missed : [], reloaded: !!(redeploy && redeploy.reloaded) }),
};

// ── HTTP ─────────────
const readBody = (req) => new Promise((ok) => { const c = []; req.on('data', (d) => c.push(d)); req.on('end', () => ok(Buffer.concat(c).toString())); });
const json = (res, status, data) => { res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(data === null ? '' : JSON.stringify(data)); };

function tone(req, res, id) {
  const a = station.byId[id];
  const freq = 300 + ([...id].reduce((n, c) => n + c.charCodeAt(0), 0) % 40) * 15; // as routeApi
  const body = toneWav(Math.min(a?.duration ?? 60, 600), freq);
  const m = /bytes=(\d+)-(\d*)/.exec(req.headers.range || '');
  if (!m) { res.writeHead(200, { 'content-type': 'audio/wav', 'accept-ranges': 'bytes', 'content-length': body.length }); res.end(body); return; }
  const from = +m[1];
  const to = m[2] ? Math.min(+m[2], body.length - 1) : body.length - 1;
  res.writeHead(206, { 'content-type': 'audio/wav', 'accept-ranges': 'bytes', 'content-range': `bytes ${from}-${to}/${body.length}`, 'content-length': to - from + 1 });
  res.end(body.subarray(from, to + 1));
}

function proxy(req, res) {
  let upPath = req.url;
  const file = new URL(req.url, 'http://x').pathname;
  // A page load after the release gets the new release, whose files all exist: from
  // then on nothing is missing (the build here is the same, so stop hiding files)
  if (redeploy && !redeploy.reloaded && !file.startsWith('/assets/') && /text\/html/.test(req.headers.accept || '')) {
    redeploy.reloaded = true;
  }
  if (redeploy && !redeploy.reloaded && file.startsWith('/assets/') && !redeploy.keep.has(file)) {
    redeploy.missed.push(file);
    upPath = '/';
  }
  const up = http.request({ host: UPSTREAM.hostname, port: UPSTREAM.port, path: upPath, method: req.method, headers: { ...req.headers, host: UPSTREAM.host, 'accept-encoding': 'identity' } }, (r) => {
    const type = r.headers['content-type'] || '';
    if (!type.includes('text/html')) { res.writeHead(r.statusCode, r.headers); r.pipe(res); return; }
    const chunks = [];
    r.on('data', (d) => chunks.push(d));
    r.on('end', () => {
      const html = Buffer.concat(chunks).toString().replace(/<head>/i, `<head>${INJECT}`);
      const headers = { ...r.headers, 'cache-control': 'no-store' };
      delete headers['content-length']; delete headers.etag; delete headers['last-modified'];
      res.writeHead(r.statusCode, headers);
      res.end(html);
    });
  });
  up.on('error', (e) => { res.writeHead(502); res.end(String(e)); });
  req.pipe(up);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  const path = url.pathname;
  if (path.startsWith('/__ctl/')) {
    const fn = ctl[path.slice(7)];
    if (!fn) return json(res, 404, { detail: 'no such control' });
    const raw = await readBody(req);
    return json(res, 200, fn(raw ? JSON.parse(raw) : {}));
  }
  if (path.startsWith('/__tone/')) return tone(req, res, decodeURIComponent(path.split('/').pop().replace(/\.wav.*$/, '')));
  if (path === '/version.json' && version) return json(res, 200, { version });
  if (path.startsWith('/api/v1/')) {
    const p = path.replace(/^\/api\/v1/, '');
    const raw = await readBody(req);
    let body = null;
    try { body = raw ? JSON.parse(raw) : null; } catch { /* not json */ }
    if (p === '/site-release' && req.method === 'GET') return json(res, 200, { ...policy, server_time: new Date().toISOString() });
    const data = station.handle(req.method, p, Object.fromEntries(url.searchParams), body);
    const status = data && data.__status ? data.__status : data === null ? 204 : 200;
    return json(res, status, data);
  }
  return proxy(req, res);
});

// ── A minimal WebSocket server (text frames, close, ping) ─────────────
function frame(op, payload) {
  const len = payload.length;
  const head = len < 126 ? Buffer.from([0x80 | op, len])
    : len < 65536 ? Buffer.from([0x80 | op, 126, len >> 8, len & 255])
      : (() => { const b = Buffer.alloc(10); b[0] = 0x80 | op; b[1] = 127; b.writeBigUInt64BE(BigInt(len), 2); return b; })();
  return Buffer.concat([head, payload]);
}
function sendAll(set, msg) { for (const s of set) s.send(msg); }

server.on('upgrade', (req, sock) => {
  const path = new URL(req.url, 'http://x').pathname;
  const accept = crypto.createHash('sha1').update(req.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest('base64');
  sock.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
  const ws = {
    open: true,
    send: (msg) => { if (ws.open) { try { sock.write(frame(1, Buffer.from(typeof msg === 'string' ? msg : JSON.stringify(msg)))); } catch { /* gone */ } } },
    onText: () => {},
  };
  let set = null;
  if (path.includes('/ws/now-playing/')) {
    set = sockets.np;
    if (path.split('/').pop() === station.SID) setTimeout(() => ws.send({ type: 'now_playing', data: station.nowPlaying() }), 20);
  } else if (path.endsWith('/ws/site')) {
    set = sockets.site;
    ws.send({ type: 'release', data: { ...policy, server_time: new Date().toISOString() } });
  } else if (path.includes('/ws/live/call/')) {
    set = sockets.call;
    call.opened++;
    ws.onText = (t) => { try { const j = JSON.parse(t); if (j.type === 'auth') call.auths.push(j.token); } catch { /* ignore */ } };
  }
  set?.add(ws);
  let buf = Buffer.alloc(0);
  const close = () => { ws.open = false; set?.delete(ws); };
  sock.on('close', close);
  sock.on('error', close);
  sock.on('data', (d) => {
    buf = Buffer.concat([buf, d]);
    for (;;) {
      if (buf.length < 2) return;
      const op = buf[0] & 15;
      let len = buf[1] & 127;
      let at = 2;
      if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); at = 4; } else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); at = 10; }
      const masked = buf[1] & 128;
      const mask = masked ? buf.subarray(at, at + 4) : null;
      if (masked) at += 4;
      if (buf.length < at + len) return;
      const payload = Buffer.from(buf.subarray(at, at + len));
      if (mask) for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i & 3];
      buf = buf.subarray(at + len);
      if (op === 1) ws.onText(payload.toString());
      else if (op === 8) { try { sock.write(frame(8, payload.subarray(0, 2))); } catch { /* gone */ } sock.end(); close(); return; } else if (op === 9) sock.write(frame(10, payload));
    }
  });
});

server.listen(PORT, '127.0.0.1', () => console.log(`fake station on http://localhost:${PORT} (site from ${UPSTREAM.origin}, stream on ${stream.origin})`));
