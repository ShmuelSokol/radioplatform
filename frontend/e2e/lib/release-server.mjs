/**
 * A stand-in for the website host across a release: serves one built site (a `dist`
 * folder) and can be switched to another build while pages are open, the way a
 * publish replaces the files under an open tab.
 * Like the real host (vercel.json rewrite), a path that does not exist is answered
 * with the site's own page: 200, text/html. So an old tab asking for a code file of
 * the build it runs gets HTML back, not a 404.
 *   const host = await startReleaseHost(port, distA);  host.publish(distB);  await host.close();
 *   host.requests: every path asked for, with the build that answered and whether the file existed
 * Checks that run side by side publish for one browser context only: start with
 * startReleaseHost(port, distA, distB) and send the header x-kbr-build: 2 from that
 * context (ctx.setExtraHTTPHeaders) when its release "comes out".
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.woff2': 'font/woff2', '.wav': 'audio/wav', '.mp3': 'audio/mpeg' };

export async function startReleaseHost(port, dist, ...later) {
  const builds = [dist, ...later];
  let build = 1;
  const requests = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    const asked = Number(req.headers['x-kbr-build'] || 0);
    const root = builds[(asked || build) - 1] || builds[build - 1];
    let file = path.join(root, decodeURIComponent(url.pathname));
    let found = file.startsWith(root) && fs.existsSync(file) && fs.statSync(file).isFile();
    if (!found) file = path.join(root, 'index.html');
    requests.push({ path: url.pathname, build: asked || build, found });
    const type = TYPES[path.extname(file)] || 'application/octet-stream';
    // Code files are cached for good on the real host; the page and version.json never
    const cache = found && url.pathname.startsWith('/assets/') ? 'public, max-age=31536000, immutable' : 'no-store';
    res.writeHead(200, { 'content-type': type, 'cache-control': cache });
    fs.createReadStream(file).pipe(res);
  });
  await new Promise((ok) => server.listen(port, ok));
  return {
    origin: `http://localhost:${port}`,
    requests,
    get build() { return build; },
    publish(next) { builds.push(next); build = builds.length; },
    close: () => new Promise((ok) => { server.closeAllConnections?.(); server.close(ok); }),
  };
}
