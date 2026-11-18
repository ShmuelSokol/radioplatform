"""
The bug reporter's screenshot in a real WebKit engine (WebKitGTK, the engine inside Safari).

Bug 8 (2026-11-18): reports sent from Safari on iPhone and Mac came with no screenshot,
and nothing told us. This checks, on a public page and a staff page:
  - each way of taking the picture (modern-screenshot, then html2canvas) gives a real
    picture by itself (the other switched off with window.__kbrShotSkip), not a blank one
  - with both switched off the form still opens at once, says why there is no picture,
    offers "Add a picture", and the report carries the reasons (client_context.screenshot)
  - a picture added by hand shows in the form and goes with the report
  - each picture looks like the screen: compared with WebKit's own screenshot of the moment
    before the 🐞 (WebKitGTK counts as Safari here, so the bevels are drawn as borders as on
    an iPhone: Shmuel's picture had the qnet button edges shifted off their buttons)
  - no console errors, and the capture takes under 8 s

WebKitGTK is not Apple Safari: html2canvas works here but did not on Shmuel's iPhone, so
this proves the fallback and the "no picture" path, not the iPhone itself.

Run (setup as in listener-webkit.py):
  cd frontend && npm run build && (npx vite preview --port 4183 --strictPort &)
  xvfb-run -a python3 e2e/webkit/bug-screenshot-webkit.py
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

from selenium import webdriver
from selenium.webdriver.common.by import By

HERE = os.path.dirname(os.path.abspath(__file__))
UPSTREAM = os.environ.get('UPSTREAM', 'http://localhost:4183')
PORT = int(os.environ.get('PORT', '4184'))
BASE = f'http://localhost:{PORT}'
MINIBROWSER = os.environ.get('MINIBROWSER', '/usr/lib/x86_64-linux-gnu/webkit2gtk-4.1/MiniBrowser')
state = {'passed': 0, 'failed': 0}


def check(name, ok, detail=''):
    state['passed' if ok else 'failed'] += 1
    print(f"{'PASS' if ok else 'FAIL':5}  {name}" + (f'  ({detail})' if detail != '' else ''), flush=True)


def wait_http(url, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return True
        except Exception:
            time.sleep(0.3)
    return False


def browser():
    o = webdriver.WebKitGTKOptions()
    o.binary_location = MINIBROWSER
    o.add_argument('--automation')
    d = webdriver.WebKitGTK(options=o)
    d.set_window_size(450, 900)
    d.set_script_timeout(30)
    return d


# How many distinct colours a picture has (a blank or one-colour picture has 1-3)
COLOURS_JS = """
const done = arguments[arguments.length - 1];
const img = new Image();
img.onload = () => {
  const c = document.createElement('canvas'); c.width = 60; c.height = 100;
  const g = c.getContext('2d'); g.drawImage(img, 0, 0, 60, 100);
  const d = g.getImageData(0, 0, 60, 100).data; const seen = new Set();
  for (let i = 0; i < d.length; i += 4) seen.add((d[i] >> 4) + ',' + (d[i+1] >> 4) + ',' + (d[i+2] >> 4));
  done({colours: seen.size, w: img.naturalWidth, h: img.naturalHeight});
};
img.onerror = () => done({colours: 0, w: 0, h: 0});
img.src = arguments[0];
"""


# How far the capture is from the real screen (same method as e2e/lib/shot-compare.mjs)
COMPARE_JS = """
const [a, b, done] = arguments; const W = 225;
const load = (src) => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = src; });
Promise.all([load(a), load(b)]).then(([ia, ib]) => {
  const H = Math.round(W * ia.naturalHeight / ia.naturalWidth);
  const px = (img) => { const c = document.createElement('canvas'); c.width = W; c.height = H;
    const g = c.getContext('2d'); g.drawImage(img, 0, 0, W, H); return g.getImageData(0, 0, W, H).data; };
  const da = px(ia), db = px(ib); let off = 0, sum = 0;
  for (let i = 0; i < da.length; i += 4) {
    const v = Math.abs(da[i] - db[i]) + Math.abs(da[i + 1] - db[i + 1]) + Math.abs(da[i + 2] - db[i + 2]);
    sum += v; if (v > 60) off++;
  }
  const n = da.length / 4;
  done({ off: Math.round(off / n * 1000) / 10, mean: Math.round(sum / n * 10) / 10 });
}, (e) => done({ off: 100, error: String(e) }));
"""
LIMIT = 16  # % of pixels; see LIMIT in e2e/bug-screenshot-faithful.mjs for the known small differences


def open_reporter(d, path, skip):
    d.get(BASE + path)
    d.execute_script("localStorage.setItem('access_token','test');localStorage.setItem('refresh_token','test')")
    d.get(BASE + path)
    time.sleep(2.5)
    d.execute_script(f"window.__kbrShotSkip = {json.dumps(skip)}; window.__posted = null;"
                     "const f = window.fetch; const x = XMLHttpRequest.prototype.send;"
                     "XMLHttpRequest.prototype.send = function (b) { try { if (String(this.__url||'').includes('/bug-reports')) window.__posted = JSON.parse(b); } catch (e) {} return x.call(this, b); };"
                     "const o = XMLHttpRequest.prototype.open; XMLHttpRequest.prototype.open = function (m, u) { this.__url = u; return o.apply(this, arguments); };")
    d.real = 'data:image/png;base64,' + d.get_screenshot_as_base64()  # the screen just before the 🐞
    t0 = time.time()
    d.execute_script("window.dispatchEvent(new CustomEvent('kbr:report-bug', {detail: {delay: 0}}))")
    while time.time() - t0 < 25:
        if d.execute_script("return !!document.querySelector('[role=dialog]')"):
            break
        time.sleep(0.2)
    return time.time() - t0


def shown_shot(d):
    return d.execute_script("const i = document.querySelector('img[alt=\"Screenshot of the page\"]'); return i ? i.src : null")


def send(d):
    d.find_element(By.ID, 'bug-what').send_keys('test report from bug-screenshot-webkit.py')
    for b in d.find_elements(By.TAG_NAME, 'button'):
        if b.text.strip() == 'Send report':
            b.click()
            break
    t0 = time.time()
    while time.time() - t0 < 10 and not d.execute_script('return window.__posted'):
        time.sleep(0.2)
    return d.execute_script('return window.__posted')


def run(path, label):
    for skip, how in ((['modern-screenshot'], 'html2canvas'), (['html2canvas'], 'modern-screenshot')):
        d = browser()
        try:
            secs = open_reporter(d, path, skip)
            src = shown_shot(d)
            check(f'{label} {how}: form opens within 8 s', secs < 8, f'{secs:.1f} s')
            check(f'{label} {how}: a screenshot is shown', bool(src))
            if src:
                pic = d.execute_async_script(COLOURS_JS, src)
                check(f'{label} {how}: the picture is not blank', pic['colours'] >= 8, json.dumps(pic))
                check(f'{label} {how}: the picture is the window, not the whole page', abs(pic['h'] / max(pic['w'], 1) - 2.0) < 0.35, json.dumps(pic))
                cmp = d.execute_async_script(COMPARE_JS, src, d.real)
                check(f'{label} {how}: the picture looks like the screen (at most {LIMIT}% of pixels differ)', cmp['off'] <= LIMIT, json.dumps(cmp))
            body = send(d)
            if os.environ.get('SAVE_DIR') and body and body.get('screenshot'):  # to look at the pictures
                name = f"{label.replace(' ', '-')}-{how}.jpg"
                with open(os.path.join(os.environ['SAVE_DIR'], name), 'wb') as fh:
                    fh.write(base64.b64decode(body['screenshot'].split(',', 1)[1]))
            check(f'{label} {how}: the screenshot goes with the report', bool(body and body.get('screenshot')))
            check(f'{label} {how}: the report says how it was taken',
                  bool(body) and (body.get('context') or {}).get('screenshot', {}).get('how') == how,
                  json.dumps((body or {}).get('context', {}).get('screenshot')))
            errs = d.execute_script('return window.__errs || []')
            check(f'{label} {how}: no console errors', not errs, ' | '.join(errs))
        finally:
            d.quit()

    # Both ways off: the form says so, offers a picture of one's own, and the reasons go along
    d = browser()
    try:
        secs = open_reporter(d, path, ['html2canvas', 'modern-screenshot'])
        check(f'{label} no picture: form opens at once', secs < 3, f'{secs:.1f} s')
        txt = d.execute_script("return document.querySelector('[data-testid=bug-no-shot]')?.innerText || ''")
        check(f'{label} no picture: says why and how to add one', 'would not let the site take a picture' in txt, txt[:120])
        pick = d.find_elements(By.CSS_SELECTOR, '[data-testid=bug-pick]')
        check(f'{label} no picture: "Add a picture" is offered', bool(pick))
        if pick:
            png = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=1170x2532', '-frames:v', '1', png.name], check=True)
            pick[0].send_keys(png.name)
            t0 = time.time()
            while time.time() - t0 < 5 and not shown_shot(d):
                time.sleep(0.2)
            src = shown_shot(d)
            check(f'{label} no picture: the added picture shows in the form', bool(src))
            if src:
                pic = d.execute_async_script(COLOURS_JS, src)
                check(f'{label} no picture: added picture is made smaller (long side 1600)', max(pic['w'], pic['h']) == 1600, json.dumps(pic))
        body = send(d)
        shot = (body or {}).get('context', {}).get('screenshot', {})
        check(f'{label} no picture: the report carries why each way failed', len(shot.get('failed', [])) == 2, json.dumps(shot))
        check(f'{label} no picture: the added picture goes with the report', bool(body and body.get('screenshot')) and shot.get('picked_by_person') is True)
        if body and body.get('screenshot'):
            raw = base64.b64decode(body['screenshot'].split(',', 1)[1])
            check(f'{label} no picture: added picture is under the 4 MB limit', len(raw) < 4 * 1024 * 1024, f'{len(raw)} bytes')
    finally:
        d.quit()


def main():
    if not os.path.exists(MINIBROWSER):
        print(f'WebKitGTK MiniBrowser not found at {MINIBROWSER}: apt-get install webkit2gtk-driver')
        return 2
    if not wait_http(UPSTREAM, 3):
        print(f'The built site is not served at {UPSTREAM}: npm run build && npx vite preview --port 4183')
        return 2
    server = subprocess.Popen(['node', os.path.join(HERE, 'fake-server.mjs')],
                              env={**os.environ, 'PORT': str(PORT), 'UPSTREAM': UPSTREAM, 'STREAM_PORT': os.environ.get('STREAM_PORT', '4185')})
    try:
        if not wait_http(f'{BASE}/api/v1/stations'):
            print('fake-server.mjs did not start')
            return 2
        run('/stations', 'public page')
        run('/admin/requests', 'staff page')
    finally:
        server.terminate()
    print(f"\n{state['passed']} passed, {state['failed']} failed")
    return 1 if state['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
