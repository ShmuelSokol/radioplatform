"""Back that returns to where you were, in a real WebKit engine (Safari's; WebKitGTK's
MiniBrowser through Selenium). Same real backend as e2e/back-navigation.mjs.

  apt-get install webkit2gtk-driver xvfb && pip install selenium
  cd frontend && BASE_URL=http://localhost:3000 API=http://localhost:8000 \
    KBR_ADMIN_EMAIL=... KBR_ADMIN_PASSWORD=... xvfb-run -a python3 e2e/webkit/back-navigation-webkit.py

Checks, at the narrowest width WebKitGTK allows (about 450 px, close to an iPhone):
  1. Sponsors list (searched, scrolled) -> client -> the Back line: same search, the
     client's row lit, the Back line stays on screen while the page scrolls.
  2. Control room on each station -> Sponsors -> a play -> Sponsor page -> Back: the
     Sponsor panel opens again and the play's row is lit.
  3. The browser's own back (Safari's back button / swipe) does the same.
Console errors and toasts are covered by the Chromium run (the guard); WebKitGTK's driver
cannot read the console.
"""
import json
import os
import sys
import time
import urllib.request

from selenium import webdriver
from selenium.webdriver.common.by import By

MINIBROWSER = '/usr/lib/x86_64-linux-gnu/webkit2gtk-4.1/MiniBrowser'
BASE = os.environ.get('BASE_URL', 'http://localhost:3000')
API = os.environ.get('API', 'http://localhost:8000') + '/api/v1'
WIDTH = 450

failures = []


def fail(msg):
    failures.append(msg)
    print('FAIL', msg)


def api(method, path, body=None, token=None):
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={'content-type': 'application/json', **({'Authorization': f'Bearer {token}'} if token else {})})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b'null')


def wait(d, fn, what, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = fn()
            if v:
                return v
        except Exception:  # noqa: BLE001 - element not there yet
            pass
        time.sleep(0.15)
    fail(what)
    return None


def lit(d):
    return d.execute_script("return Array.from(document.querySelectorAll('[data-nav-returned]')).map(e => (e.textContent||'').replace(/\\s+/g,' ').trim().slice(0,60))")


def main():
    if not os.path.exists(MINIBROWSER):
        print(f'WebKitGTK MiniBrowser not found at {MINIBROWSER}: apt-get install webkit2gtk-driver')
        return 2
    login = api('POST', '/auth/login', {'email': os.environ.get('KBR_ADMIN_EMAIL', 'admin'), 'password': os.environ['KBR_ADMIN_PASSWORD']})
    tok = login['access_token']
    stations = api('GET', '/stations', token=tok)['stations']
    clients = [c for c in api('GET', '/sponsors/clients', token=tok) if c['contracts_total'] > 0]
    target = clients[0]

    o = webdriver.WebKitGTKOptions()
    o.binary_location = MINIBROWSER
    o.add_argument('--automation')
    d = webdriver.WebKitGTK(options=o)
    d.set_window_size(WIDTH, 900)
    try:
        d.get(BASE + '/admin/login')
        d.execute_script('localStorage.setItem("access_token", arguments[0]); localStorage.setItem("refresh_token", arguments[1]);',
                         tok, login['refresh_token'])

        # 1. list -> client -> Back
        d.get(BASE + '/admin/sponsors')
        box = wait(d, lambda: d.find_element(By.CSS_SELECTOR, 'input[aria-label="Find a client"]'), '1: no search box')
        box.send_keys(target['name'][:4])
        time.sleep(0.5)
        row = wait(d, lambda: d.find_element(By.CSS_SELECTOR, f'[data-nav-id="client-{target["id"]}"]'), '1: client row missing')
        row.click()
        wait(d, lambda: f'/admin/sponsors/{target["id"]}' in d.current_url, '1: client page did not open')
        back = wait(d, lambda: d.find_element(By.CSS_SELECTOR, '[data-testid="nav-back"]'), '1: no Back line')
        shots = os.environ.get('SHOTS_DIR')
        if shots:
            d.save_screenshot(f'{shots}/webkit-1-client.png')
        if back and 'Back to Sponsors' not in back.get_attribute('textContent'):
            fail(f'1: Back line says {back.text!r}')
        # the Back line stays on screen when the page is scrolled
        d.execute_script('window.scrollTo(0, 600)')
        time.sleep(0.3)
        top = d.execute_script("return document.querySelector('[data-testid=nav-back]').getBoundingClientRect().top")
        if top is None or top < 0 or top > 120:
            fail(f'1: Back line scrolled away (top {top})')
        d.find_element(By.CSS_SELECTOR, '[data-testid="nav-back"]').click()
        wait(d, lambda: d.current_url.rstrip('/').split('?')[0].endswith('/admin/sponsors'), '1: Back did not go to the list')
        value = d.execute_script("return document.querySelector('input[aria-label=\"Find a client\"]').value")
        if value != target['name'][:4]:
            fail(f'1: search after Back is {value!r}')
        wait(d, lambda: any(target['name'] in t for t in lit(d)), f'1: {target["name"]} not lit after Back')
        print('ok   1 list -> client -> Back')

        # 2/3. control room per station
        for st in stations:
            d.execute_script('localStorage.setItem("radio_last_station", arguments[0])', st['id'])
            d.get(BASE + '/admin/dashboard')
            wait(d, lambda: d.find_element(By.CSS_SELECTOR, '[data-testid="control-room"]'), f'2 {st["name"]}: no control room')
            more = [b for b in d.find_elements(By.CSS_SELECTOR, 'nav[aria-label="Control room sections"] button') if 'More' in b.text]
            if more:
                more[-1].click()
                btn = wait(d, lambda: [b for b in d.find_elements(By.TAG_NAME, 'button') if b.text.strip() == 'Sponsors'], f'2 {st["name"]}: no Sponsors button')
                btn and btn[0].click()
            else:
                tab = [b for b in d.find_elements(By.CSS_SELECTOR, '[role=tab]') if b.text.strip() == 'Sponsors']
                tab and tab[0].click()
            play = wait(d, lambda: d.find_elements(By.CSS_SELECTOR, '[data-testid="sponsors-today-row"]'), f'2 {st["name"]}: no plays listed')
            if not play:
                continue
            nav_id = play[0].get_attribute('data-nav-id')
            play[0].click()
            link = wait(d, lambda: d.find_element(By.CSS_SELECTOR, '[data-testid="link-sponsors"]'), f'2 {st["name"]}: no Sponsor page link')
            link.click()
            wait(d, lambda: '/admin/sponsors' in d.current_url, f'2 {st["name"]}: Sponsor page did not open')
            back = wait(d, lambda: d.find_element(By.CSS_SELECTOR, '[data-testid="nav-back"]'), f'2 {st["name"]}: no Back line')
            if back and 'Back to Dashboard' not in back.get_attribute('textContent'):
                fail(f'2 {st["name"]}: Back line says {back.text!r}')
            back.click()
            wait(d, lambda: d.find_elements(By.CSS_SELECTOR, '[data-testid="link-sponsors"]'), f'2 {st["name"]}: panel not open after Back')
            wait(d, lambda: d.find_elements(By.CSS_SELECTOR, f'[data-nav-id="{nav_id}"][data-nav-returned]'), f'2 {st["name"]}: play row not lit after Back')
            if os.environ.get('SHOTS_DIR'):
                d.save_screenshot(f'{os.environ["SHOTS_DIR"]}/webkit-2-{st["name"][:8].replace(" ", "")}-back.png')
            # Safari's own back
            d.execute_script("document.querySelector('[data-testid=link-sponsors]').scrollIntoView({block: 'center'})")
            time.sleep(0.2)
            d.find_element(By.CSS_SELECTOR, '[data-testid="link-sponsors"]').click()
            wait(d, lambda: '/admin/sponsors' in d.current_url, f'3 {st["name"]}: Sponsor page did not open again')
            d.back()
            wait(d, lambda: '/admin/dashboard' in d.current_url, f'3 {st["name"]}: browser back did not return')
            wait(d, lambda: d.find_elements(By.CSS_SELECTOR, '[data-testid="link-sponsors"]'), f'3 {st["name"]}: panel not open after browser back')
            print(f'ok   2/3 {st["name"]}')

    finally:
        d.quit()
    print(f'\n{len(failures)} FAILED' if failures else '\nALL PASSED')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
