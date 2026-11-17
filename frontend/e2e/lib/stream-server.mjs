/**
 * A tiny stand-in for the playout engine's Icecast mount: an endless MP3 stream sent
 * at its real bitrate (with a short burst on connect, as Icecast does), on its own port.
 *   GET /live-<station>   the stream (CORS open, like the real mount)
 *   GET /missing          404, to test the fall-back to the browser player
 * stall(ms) holds the data back, then sends it all at once (a network stall).
 */
import http from 'node:http';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export async function startStreamServer(port) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kbr-stream-'));
  const file = path.join(dir, 'tone.mp3');
  execFileSync('ffmpeg', ['-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=523:duration=30:sample_rate=44100',
    '-ac', '1', '-b:a', '128k', '-codec:a', 'libmp3lame', file]);
  const mp3 = fs.readFileSync(file);
  // Skip an ID3 tag if any, and start on a frame boundary
  let start = 0;
  if (mp3.subarray(0, 3).toString() === 'ID3') start = 10 + ((mp3[6] << 21) | (mp3[7] << 14) | (mp3[8] << 7) | mp3[9]);
  while (start < mp3.length - 1 && !(mp3[start] === 0xff && (mp3[start + 1] & 0xe0) === 0xe0)) start++;
  const body = mp3.subarray(start);
  const BYTES_PER_SEC = 128000 / 8;
  const TICK = 100;
  const clients = new Set();
  let stalledUntil = 0;
  const stats = { connects: 0 };
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    const cors = { 'access-control-allow-origin': '*', 'cache-control': 'no-store' };
    if (req.method === 'OPTIONS') { res.writeHead(204, { ...cors, 'access-control-allow-headers': '*' }); res.end(); return; }
    if (!url.pathname.startsWith('/live-')) { res.writeHead(404, cors); res.end('no such mount'); return; }
    stats.connects++;
    res.writeHead(200, { ...cors, 'content-type': 'audio/mpeg' });
    let pos = 0;
    let owed = 0;
    const take = (n) => {
      const out = [];
      while (n > 0) { const k = Math.min(n, body.length - pos); out.push(body.subarray(pos, pos + k)); pos = (pos + k) % body.length; n -= k; }
      return Buffer.concat(out);
    };
    res.write(take(BYTES_PER_SEC * 0.5)); // burst on connect
    const c = { res, timer: null };
    c.timer = setInterval(() => {
      owed += (BYTES_PER_SEC * TICK) / 1000;
      if (Date.now() < stalledUntil) return;
      const n = Math.floor(owed);
      owed -= n;
      if (n > 0) res.write(take(n));
    }, TICK);
    clients.add(c);
    req.on('close', () => { clearInterval(c.timer); clients.delete(c); });
  });
  await new Promise((ok) => server.listen(port, '127.0.0.1', ok));
  return {
    url: (sid) => `http://localhost:${port}/live-${sid}`,
    missing: `http://localhost:${port}/missing`,
    origin: `http://localhost:${port}`,
    stats,
    stall: (ms) => { stalledUntil = Date.now() + ms; },
    /** The engine or Icecast restarts: every listener's connection is cut. */
    dropAll: () => { for (const c of clients) { clearInterval(c.timer); c.res.end(); } clients.clear(); },
    close: () => { for (const c of clients) { clearInterval(c.timer); c.res.destroy(); } return new Promise((ok) => server.close(ok)); },
  };
}
