"""
Leaving a page in real WebKit (Safari's engine) logs no "Fetch API cannot load
.../version.json due to access control checks".

The update check (lib/appUpdate.ts) also ran when the page turned hidden. Leaving a page
turns it hidden too, so a check started as the page went away; WebKit cut it off and
logged that line on every page change (seen by the PC's WebKit run on kbrlive.com
2026-11-18). The check itself works in WebKit; the line was noise that hides real errors.

Selenium cannot read WebKit's own console lines, so this runs MiniBrowser directly with
--enable-write-console-messages-to-stdout and reads its output. A harness page holds the
site in a frame and moves the frame through the public pages every 400 ms (each move
leaves a page, like a reload or a typed address).

Run (setup as in listener-webkit.py):
  cd frontend && npm run build && (npx vite preview --port 4183 --strictPort &)
  xvfb-run -a python3 e2e/webkit/version-check-webkit.py
"""
import http.server
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UPSTREAM = os.environ.get('UPSTREAM', 'http://localhost:4183')
PORT = int(os.environ.get('PORT', '4184'))
HARNESS_PORT = int(os.environ.get('HARNESS_PORT', '8099'))  # WebKit refuses some ports (e.g. 4190)
MINIBROWSER = os.environ.get('MINIBROWSER', '/usr/lib/x86_64-linux-gnu/webkit2gtk-4.1/MiniBrowser')
MOVES = 10

HARNESS = f"""<!doctype html><html><body>
<iframe id="f" width="450" height="800" src="http://localhost:{PORT}/stations"></iframe>
<script>
  const pages = ['/guide', '/hosts', '/stations', '/archives'];
  let n = 0;
  setTimeout(function go() {{
    if (n >= {MOVES}) {{ document.title = 'done'; return; }}
    document.getElementById('f').src = 'http://localhost:{PORT}' + pages[n % pages.length];
    n++;
    setTimeout(go, 400);
  }}, 4000);
</script></body></html>"""


def wait_http(url, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return True
        except Exception:
            time.sleep(0.3)
    return False


class Harness(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = HARNESS.encode()
        self.send_response(200)
        self.send_header('content-type', 'text/html')
        self.send_header('content-length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    if not os.path.exists(MINIBROWSER):
        print(f'WebKitGTK MiniBrowser not found at {MINIBROWSER}: apt-get install webkit2gtk-driver')
        return 2
    if not wait_http(UPSTREAM, 3):
        print(f'The built site is not served at {UPSTREAM}: npm run build && npx vite preview --port 4183')
        return 2
    server = subprocess.Popen(['node', os.path.join(HERE, 'fake-server.mjs')], stdout=subprocess.DEVNULL,
                              env={**os.environ, 'PORT': str(PORT), 'UPSTREAM': UPSTREAM, 'STREAM_PORT': os.environ.get('STREAM_PORT', '4185')})
    harness = http.server.ThreadingHTTPServer(('localhost', HARNESS_PORT), Harness)
    threading.Thread(target=harness.serve_forever, daemon=True).start()
    try:
        if not wait_http(f'http://localhost:{PORT}/api/v1/stations'):
            print('fake-server.mjs did not start')
            return 2
        log = tempfile.NamedTemporaryFile('w+', suffix='.log', delete=False)
        mb = subprocess.Popen([MINIBROWSER, '--enable-write-console-messages-to-stdout=true', f'http://localhost:{HARNESS_PORT}/'],
                              stdout=log, stderr=subprocess.STDOUT)
        time.sleep(4 + MOVES * 0.4 + 5)  # MiniBrowser does not quit by itself: stopped after the moves
        mb.terminate()
        mb.wait(10)
        log.seek(0)
        out = log.read()
    finally:
        harness.shutdown()
        server.terminate()
    lines = [x for x in out.splitlines() if 'CONSOLE' in x]
    loads = sum('localhost:%d' % PORT in x or 'preconnected' in x for x in lines)
    cut = [x for x in lines if 'version.json' in x and 'access control' in x]
    errors = [x for x in lines if 'CONSOLE JS ERROR' in x or 'CONSOLE NETWORK ERROR' in x]
    ok = True
    for name, good, detail in (
        ('(setup) the frame really moved through the pages', loads >= MOVES, f'{loads} page loads seen'),
        ('leaving a page logs no "version.json ... access control checks"', not cut, f'{len(cut)} lines: ' + ' | '.join(cut[:2])),
        ('no other console errors while moving between pages', not errors, ' | '.join(errors[:3])),
    ):
        ok &= good
        print(f"{'PASS' if good else 'FAIL':5}  {name}  ({detail})", flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
