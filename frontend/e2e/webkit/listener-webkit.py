"""
The public listener pages in a real WebKit engine (WebKitGTK's MiniBrowser, the engine
inside Safari) instead of Chromium dressed up as an iPhone. Runs against the fake
backend of e2e/lib/fake-station.mjs, served by e2e/webkit/fake-server.mjs (started here).

What WebKitGTK can and cannot stand in for:
  - same engine as Safari (WebCore, JavaScriptCore, media element, Web Audio, WebSocket);
    media goes through GStreamer instead of AVFoundation
  - it has MSE for MP3 (Safari on iPhone does not). Under WebDriver it ignores
    --enable-mediasource=false and --features=-MediaSource, so the iPhone's no-MSE path
    is had by removing window.MediaSource before the app loads (?kbr_no_mse=1,
    see fake-server.mjs): the rest (media element, GStreamer, MP3 over HTTP) is real WebKit
  - like Safari, it refuses sound without a tap by default (play() -> NotAllowedError,
    AudioContext suspended); --autoplay-policy=allow makes it behave like Chrome.
    It is not iOS: no audio session, lock screen, background tabs or CarPlay
  - the narrowest window is about 447 px (an iPhone is 390), so phone layout is checked at 450

Setup (Ubuntu 24.04, once):
  apt-get install -y webkit2gtk-driver xvfb gstreamer1.0-plugins-good gstreamer1.0-libav ffmpeg
  pip install selenium
Run:
  cd frontend && npm run build && (npx vite preview --port 4183 --strictPort &)
  xvfb-run -a python3 e2e/webkit/listener-webkit.py        # ONLY=listen,stream,bar,taken,stale,callin,release,pages

Exits non-zero if a check fails. KNOWN_BUGS lists checks expected to fail (XFAIL); a pass
there prints XPASS and fails, like pytest's xfail(strict=True).
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

from selenium import webdriver
from selenium.webdriver.common.by import By

HERE = os.path.dirname(os.path.abspath(__file__))
UPSTREAM = os.environ.get('UPSTREAM', 'http://localhost:4183')
PORT = int(os.environ.get('PORT', '4184'))
STREAM_PORT = os.environ.get('STREAM_PORT', '4185')
BASE = f'http://localhost:{PORT}'
ONLY = [x for x in os.environ.get('ONLY', '').split(',') if x]
MINIBROWSER = os.environ.get('MINIBROWSER', '/usr/lib/x86_64-linux-gnu/webkit2gtk-4.1/MiniBrowser')
WIDTH = 450

# No sound without a tap, as Safari (WebKitGTK's default too, made explicit)
SAFARI = ['--autoplay-policy=deny', '--media-playback-requires-user-gesture=true']
# Sound may start without a tap, as Chrome after a visit
ALLOW = ['--autoplay-policy=allow']

_PLAIN = ('plain <audio> fallback for browsers without MSE: WebKitGTK (GStreamer) starts a 128 kbps MP3 stream '
          'only after about 2.1 s, every run. iPhone Safari takes the HLS copy instead (liveStream.ts hlsStream), '
          'which WebKitGTK cannot play (no native HLS), so this is the fallback path only, not the iPhone default')
KNOWN_BUGS = {
    'no MSE (iPhone path): stream heard within 2 s': _PLAIN,
    'no MSE (iPhone path): Listen again plays within 2 s': _PLAIN,
}
state = {'passed': 0, 'failed': 0, 'xfail': 0}


def check(name, ok, detail=''):
    bug = next((v for k, v in KNOWN_BUGS.items() if k in name), None)
    if bug:
        tag = 'XPASS' if ok else 'XFAIL'
        state['failed' if ok else 'xfail'] += 1
    else:
        tag = 'PASS' if ok else 'FAIL'
        state['passed' if ok else 'failed'] += 1
    print(f'{tag:5}  {name}' + (f'  ({detail})' if detail != '' else '') + (f'  [BUG: {bug}]' if bug else ''), flush=True)


def info(msg):
    print(f'info   {msg}', flush=True)


def ctl(name, body=None):
    req = urllib.request.Request(f'{BASE}/__ctl/{name}', data=json.dumps(body or {}).encode(), method='POST',
                                 headers={'content-type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or b'null')


def browser(args=(), no_mse=False, native_hls=False):
    o = webdriver.WebKitGTKOptions()
    o.binary_location = MINIBROWSER
    o.add_argument('--automation')
    for a in args:
        o.add_argument(a)
    d = webdriver.WebKitGTK(options=o)
    d.set_window_size(WIDTH, 900)
    d.set_script_timeout(30)
    d.no_mse = no_mse
    d.native_hls = native_hls
    return d


def open_page(d, path):
    q = [x for x, on in (('kbr_no_mse=1', d.no_mse), ('kbr_native_hls=1', getattr(d, 'native_hls', False))) if on]
    d.get(BASE + path + ('?' + '&'.join(q) if q else ''))
    if d.no_mse:
        check(f'(setup) {path}: MediaSource removed for this tab', js(d, 'return window.MediaSource === undefined'))


def js(d, src, *args):
    return d.execute_script(src, *args)


def wait_for(d, cond_js, timeout=5.0):
    """Ms (page clock) until `cond_js` (an expression) holds, or -1."""
    return d.execute_async_script(f"""
      const done = arguments[arguments.length - 1];
      const t0 = performance.now();
      const look = () => {{
        let ok = false;
        try {{ ok = !!({cond_js}); }} catch (e) {{ ok = false; }}
        if (ok) done(Math.round(performance.now() - t0));
        else if (performance.now() - t0 > {timeout * 1000}) done(-1);
        else setTimeout(look, 10);
      }};
      look();""")


def press(d, cond_js, el, timeout=5.0):
    """Click `el` for real and time, on the page clock, from the press until `cond_js` holds (-1: never)."""
    js(d, """window.__t0 = null; window.__hit = null;
      const mark = () => { if (window.__t0 === null) window.__t0 = performance.now(); };
      for (const ev of ['pointerdown', 'mousedown', 'click']) window.addEventListener(ev, mark, { capture: true, once: true });""")
    el.click()
    return d.execute_async_script(f"""
      const done = arguments[arguments.length - 1];
      const begin = performance.now();
      const look = () => {{
        let ok = false;
        try {{ ok = !!({cond_js}); }} catch (e) {{ ok = false; }}
        if (ok && window.__t0 !== null) done(Math.round(performance.now() - window.__t0));
        else if (performance.now() - begin > {timeout * 1000}) done(-1);
        else setTimeout(look, 5);
      }};
      look();""")


def button(d, text):
    for b in d.find_elements(By.TAG_NAME, 'button'):
        if b.text.strip() == text or b.get_attribute('aria-label') == text:
            return b
    return None


def body_text(d):
    return js(d, 'return document.body.innerText;')


def errors(d):
    return js(d, 'return window.__errs || [];')


def sideways(d):
    return js(d, 'return [document.documentElement.scrollWidth, window.innerWidth];')


# The built-in player is heard: a deck plays with its gain up, and its AudioContext runs
AUDIBLE = "window.__audible().length > 0 && [...window.__deckGain.values()][0].context.state === 'running'"
# The stream element plays and its clock moves
STREAM_PLAYING = "(() => { const a = (document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')); return a && !a.paused && a.currentTime > 0.05; })()"


def set_volume(d, v):
    js(d, """const el = document.querySelector('input[type=range]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, String(arguments[0]));
      el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true }));""", v)


# ── /listen/:id, browser-player station ─────────────
def listen_browser(tag, args, no_mse=False):
    np = ctl('reset', {'mode': 'browser', 'show': False})
    d = browser(args, no_mse)
    try:
        open_page(d, f'/listen/{np["sid"]}')
        title = ctl('state')['playing']['title']
        shown = wait_for(d, f'document.body.innerText.includes({json.dumps(title)})', 8)
        check(f'{tag}: now-playing title shown', shown >= 0, title)
        check(f'{tag}: nothing heard before Listen', js(d, 'return window.__audible().length') == 0)
        ms = press(d, AUDIBLE, button(d, 'Listen Live'), 5)
        check(f'{tag}: Listen Live heard within 1.5 s (deck playing, AudioContext running)', 0 <= ms <= 1500, f'{ms} ms')
        t1 = js(d, 'return window.__audible()[0]?.t ?? null')
        time.sleep(3.0)  # GStreamer reports currentTime in steps of about 0.5 s
        t2 = js(d, 'return window.__audible()[0]?.t ?? null')
        check(f'{tag}: the deck clock moves (really playing)', t1 is not None and t2 is not None and t2 - t1 > 2.0, f'{t1} -> {t2} in 3 s')
        check(f'{tag}: Stop button replaces Listen Live', button(d, 'Stop') is not None)

        # A change on air: title within 1 s, and the new item is heard
        nxt = ctl('onair')
        shown = wait_for(d, f'document.body.innerText.includes({json.dumps(nxt["title"])})', 3)
        check(f'{tag}: a change on air shows within 1 s', 0 <= shown <= 1000, f'{shown} ms')
        heard = wait_for(d, f"window.__audible().some(x => x.src === '{nxt['id']}')", 3)
        check(f'{tag}: the new item is heard', heard >= 0, f'{heard} ms')

        # Volume and mute (the player's output gain)
        set_volume(d, 0.4)
        time.sleep(0.4)
        lvl = js(d, 'return window.__outLevel()')
        check(f'{tag}: volume slider sets the level', lvl is not None and abs(lvl - 0.4) < 0.05 and '40%' in body_text(d), f'level {lvl}')
        button(d, 'Mute').click()
        time.sleep(0.4)
        lvl = js(d, 'return window.__outLevel()')
        check(f'{tag}: Mute silences', lvl is not None and lvl < 0.01 and button(d, 'Unmute') is not None, f'level {lvl}')
        button(d, 'Unmute').click()
        time.sleep(0.4)
        lvl = js(d, 'return window.__outLevel()')
        check(f'{tag}: Unmute brings it back', lvl is not None and abs(lvl - 0.4) < 0.05, f'level {lvl}')

        button(d, 'Stop').click()
        quiet = wait_for(d, 'window.__audible().length === 0', 2)
        check(f'{tag}: Stop stops the sound', quiet >= 0 and button(d, 'Listen Live') is not None, f'{quiet} ms')
        time.sleep(1.0)
        ms = press(d, AUDIBLE, button(d, 'Listen Live'), 5)
        check(f'{tag}: Listen again is heard within 1.5 s', 0 <= ms <= 1500, f'{ms} ms')
        w = sideways(d)
        check(f'{tag}: no sideways scroll at {WIDTH} px', w[0] <= w[1], f'{w[0]} > {w[1]}' if w[0] > w[1] else '')
        check(f'{tag}: no script errors', not errors(d), ' | '.join(errors(d)))
    finally:
        d.quit()


# ── /listen/:id, server-stream station ─────────────
def listen_stream(tag, args, expect_mode, no_mse=False):
    np = ctl('reset', {'mode': 'server', 'show': False})
    d = browser(args, no_mse)
    try:
        open_page(d, f'/listen/{np["sid"]}')
        wait_for(d, "document.body.innerText.includes('Live stream')", 8)
        check(f'{tag}: badge says Live stream (listeners see no engine detail)', 'Live stream' in body_text(d))
        mse = js(d, "return !!window.MediaSource && MediaSource.isTypeSupported('audio/mpeg')")
        hls = js(d, "return document.createElement('audio').canPlayType('application/vnd.apple.mpegurl')")
        info(f'{tag}: MediaSource for audio/mpeg: {mse}; native HLS canPlayType: {hls!r}')
        connects = ctl('state')['streamConnects']
        ms = press(d, STREAM_PLAYING, button(d, 'Listen Live'), 8)
        src = js(d, "return (document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')).src")
        mode = 'mse' if src.startswith('blob:') else 'plain' if src == ctl('state')['streamUrl'] else f'other ({src})'
        info(f'{tag}: liveStream.ts picked {mode}; first sound after {ms} ms')
        check(f'{tag}: plays through the {expect_mode} path', mode == expect_mode, mode)
        check(f'{tag}: stream heard within 2 s (S-11)', 0 <= ms <= 2000, f'{ms} ms')
        check(f'{tag}: the stream server was asked for the mount', ctl('state')['streamConnects'] > connects)
        check(f'{tag}: built-in player stays silent', js(d, 'return window.__audible().length') == 0)
        # Never silent for 10 s: sample the clock for 10 s, it must keep moving
        samples = d.execute_async_script("""
          const done = arguments[arguments.length - 1];
          const a = (document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')); const out = [];
          const t0 = performance.now();
          const tick = () => { out.push([Math.round(performance.now() - t0), +a.currentTime.toFixed(2), a.paused]);
            if (performance.now() - t0 >= 10000) done(out); else setTimeout(tick, 250); };
          tick();""")
        worst = 0.0
        last_move = samples[0][0]
        for i in range(1, len(samples)):
            if samples[i][1] > samples[i - 1][1]:
                last_move = samples[i][0]
            worst = max(worst, (samples[i][0] - last_move) / 1000)
        advanced = samples[-1][1] - samples[0][1]
        check(f'{tag}: keeps playing for 10 s, no stall (S-11)', advanced >= 8.5 and worst < 1.0 and not samples[-1][2],
              f'clock moved {advanced:.1f} s in 10 s, longest still {worst:.2f} s')
        lag = js(d, "const a = (document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')); const b = a.buffered; return b.length ? +(b.end(b.length - 1) - a.currentTime).toFixed(2) : null;")
        info(f'{tag}: {lag} s behind the newest audio received')
        set_volume(d, 0.3)
        time.sleep(0.3)
        vol = js(d, "return (document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')).volume")
        check(f'{tag}: volume slider sets the stream level', abs(vol - 0.3) < 0.02, vol)
        button(d, 'Mute').click()
        time.sleep(0.3)
        vol = js(d, "return (document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')).volume")
        check(f'{tag}: Mute silences the stream', vol == 0, vol)
        button(d, 'Unmute').click()
        button(d, 'Stop').click()
        time.sleep(0.4)
        st = js(d, "const a = (document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')); return [a.paused, a.getAttribute('src')];")
        check(f'{tag}: Stop stops the stream', st[0] and not st[1], st)
        ms = press(d, STREAM_PLAYING, button(d, 'Listen Live'), 8)
        check(f'{tag}: Listen again plays within 2 s', 0 <= ms <= 2000, f'{ms} ms')
        check(f'{tag}: no script errors', not errors(d), ' | '.join(errors(d)))
    finally:
        d.quit()


# ── The station keeps playing on the other listener pages (player bar) ─────────────
GO = "history.pushState({}, '', arguments[0]); dispatchEvent(new PopStateEvent('popstate'));"


def bar(tag, args, mode, no_mse=False, hls=False):
    """One tap on Listen Live, then the guide, the DJs and the station list with no
    further tap, and back to the station page: WebKit refuses sound a tap did not start,
    so a player that was rebuilt on the way would be silent here. `hls`: the tab says it
    plays HLS natively, as iPhone Safari does, so the site takes the engine's HLS copy
    (the path a real iPhone takes on a server-stream station such as Main)."""
    np = ctl('reset', {'mode': mode, 'show': False, **({'stream': 'hls'} if hls else {})})
    d = browser(args, no_mse, native_hls=hls)
    stream = mode == 'server'
    clock = ("return (document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')) ? +(document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')).currentTime.toFixed(2) : null"
             if stream else 'return window.__audible()[0]?.t ?? null')
    try:
        open_page(d, f'/listen/{np["sid"]}')
        if stream:
            wait_for(d, "document.body.innerText.includes('Live stream')", 8)
        wait_for(d, "[...document.querySelectorAll('button')].some((b) => b.textContent.trim() === 'Listen Live')", 8)
        ms = press(d, STREAM_PLAYING if stream else AUDIBLE, button(d, 'Listen Live'), 8)
        check(f'{tag}: Listen Live heard on the station page', ms >= 0, f'{ms} ms')
        if hls:
            src = js(d, "return (document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')) && (document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')).src")
            check(f'{tag}: plays the HLS copy, as an iPhone does', bool(src) and src.endswith('/mp3.m3u8'), src)
        # every other move by the router; the last walk below by real clicks on the menu
        for path, ready in (('/guide', 'Schedule'), ('/hosts', 'DJs'), ('/stations', 'Stations')):
            t1 = js(d, clock)
            js(d, GO, path)
            shown = wait_for(d, "!!document.querySelector('[data-testid=\"player-bar\"]') && location.pathname === " + json.dumps(path), 4)
            time.sleep(3.0)
            t2 = js(d, clock)
            check(f'{tag}: {path} shows the bar', shown >= 0, f'{shown} ms')
            check(f'{tag}: still playing on {path} with no new tap (clock moved, no restart)',
                  t1 is not None and t2 is not None and t2 - t1 > 2.0, f'{t1} -> {t2} in 3 s')
        # the same moves by real clicks on the site's own menu (the burger menu when the window is narrow)
        find_link = """
            const label = arguments[0];
            const shown = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
            return [...document.querySelectorAll('nav a')].find((a) => a.textContent.trim() === label && shown(a)) || null;"""
        for label, path in (('Schedule', '/guide'), ('Archives', '/archives'), ('DJs', '/hosts'), ('Stations', '/stations')):
            t1 = js(d, clock)
            link = js(d, find_link, label)
            if link is None:
                burger = js(d, "return document.querySelector('nav button[aria-label=\"Menu\"]')")
                if burger is not None:
                    burger.click()
                    time.sleep(0.3)
                    link = js(d, find_link, label)
            if link is None:
                check(f'{tag}: menu link "{label}" found', False, 'no visible link')
                continue
            link.click()
            shown = wait_for(d, "!!document.querySelector('[data-testid=\"player-bar\"]') && location.pathname === " + json.dumps(path), 4)
            time.sleep(3.0)
            t2 = js(d, clock)
            check(f'{tag}: a real click on "{label}" in the menu: bar shown, still playing, no restart',
                  shown >= 0 and t1 is not None and t2 is not None and t2 - t1 > 2.0, f'{shown} ms, {t1} -> {t2} in 3 s')
        # Back to where listening started, as a person does: tap the station in the list,
        # then the guide from the menu and the browser's Back. Found by Shmuel on his iPhone
        # 2026-11-18: the sound stopped on the DJs page or the guide and did not come back
        # on the station page. No new tap here either, so a player rebuilt on the way back
        # (or stopped by the station page as it opens) is silent in WebKit as on an iPhone.
        here = f'/listen/{np["sid"]}'
        on_station = "location.pathname === " + json.dumps(here)
        not_offering_listen = "![...document.querySelectorAll('button')].some((b) => b.textContent.trim() === 'Listen Live')"

        def back_on_station(how):
            shown = wait_for(d, on_station, 4)
            time.sleep(3.0)
            t2 = js(d, clock)
            offers = not js(d, 'return ' + not_offering_listen)
            check(f'{tag}: back on the station page by {how}: still playing with no new tap, no restart',
                  shown >= 0 and t1 is not None and t2 is not None and t2 - t1 > 2.0, f'{shown} ms, {t1} -> {t2} in 3 s')
            check(f'{tag}: back on the station page by {how}: it shows the station as playing (no "Listen Live" offered)',
                  not offers, body_text(d)[:160].replace('\n', ' | ') if offers else '')
            if not stream:
                n = js(d, 'return window.__audible().length')
                check(f'{tag}: back on the station page by {how}: one player heard, not two', n == 1, n)

        t1 = js(d, clock)
        card = js(d, "return document.querySelector('a[href=\"' + arguments[0] + '\"]')", here)
        if card is None:
            check(f'{tag}: the station list links to the station', False, 'no link to ' + here)
        else:
            card.click()
            back_on_station('a tap on it in the station list')
        for label, path in (('DJs', '/hosts'), ('Schedule', '/guide')):
            link = js(d, find_link, label)
            if link is None:
                burger = js(d, "return document.querySelector('nav button[aria-label=\"Menu\"]')")
                if burger is not None:
                    burger.click()
                    time.sleep(0.3)
                    link = js(d, find_link, label)
            if link is None:
                check(f'{tag}: menu link "{label}" found from the station page', False, 'no visible link')
                continue
            t1 = js(d, clock)
            link.click()
            wait_for(d, "location.pathname === " + json.dumps(path), 4)
            time.sleep(2.0)
            d.back()
            back_on_station(f'the browser\'s Back from {path}')
        js(d, GO, '/stations')
        wait_for(d, "!!document.querySelector('[data-testid=\"player-bar\"]')", 4)
        bar_text = js(d, "return document.querySelector('[data-testid=\"player-bar\"]').innerText")
        check(f'{tag}: the bar names the station', np.get('name', "Kol B'ramah") in bar_text or "Kol B'ramah" in bar_text, bar_text)
        js(d, "document.querySelector('[data-testid=\"player-bar\"] button').click()")
        time.sleep(0.8)
        if stream:
            st = js(d, "const a = (document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')); return !a || a.paused;")
        else:
            st = js(d, 'return window.__audible().length === 0')
        check(f'{tag}: Stop in the bar stops the sound and the bar goes',
              st and not js(d, "return !!document.querySelector('[data-testid=\"player-bar\"]')"), st)
        check(f'{tag}: no script errors', not errors(d), ' | '.join(errors(d)))
    finally:
        d.quit()


FIND_LINK = """
    const label = arguments[0];
    const shown = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
    return [...document.querySelectorAll('nav a')].find((a) => a.textContent.trim() === label && shown(a)) || null;"""


def menu_click(d, label):
    """Tap `label` in the site's menu as a person does (opening the burger menu when the
    window is narrow). False when there is no such visible link."""
    link = js(d, FIND_LINK, label)
    if link is None:
        burger = js(d, "return document.querySelector('nav button[aria-label=\"Menu\"]')")
        if burger is not None:
            burger.click()
            time.sleep(0.3)
            link = js(d, FIND_LINK, label)
    if link is None:
        return False
    link.click()
    return True


def stale_release(tag, args, mode, no_mse=False, hls=False):
    """A new release goes out while a listener plays the station (a merge does that
    many times a day). The old release's code files are gone, so the first visit to the
    guide or the DJs page cannot load that screen's code. The station must keep playing
    all the same, there and back on the station page, with no new tap: a reload or a
    player taken down by the error screen is silent for good on an iPhone. Found by
    Shmuel on his iPhone 2026-11-18 (sound stopped on the DJs page or the guide and did
    not come back on the station page)."""
    np = ctl('reset', {'mode': mode, 'show': False, **({'stream': 'hls'} if hls else {})})
    d = browser(args, no_mse, native_hls=hls)
    stream = mode == 'server'
    clock = ("return (document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')) && !(document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')).paused ? +(document.querySelector('[data-testid=station-stream]') || document.querySelector('audio')).currentTime.toFixed(2) : null"
             if stream else 'return window.__audible ? (window.__audible()[0]?.t ?? null) : null')
    try:
        open_page(d, f'/listen/{np["sid"]}')
        if stream:
            wait_for(d, "document.body.innerText.includes('Live stream')", 8)
        wait_for(d, "[...document.querySelectorAll('button')].some((b) => b.textContent.trim() === 'Listen Live')", 8)
        ms = press(d, STREAM_PLAYING if stream else AUDIBLE, button(d, 'Listen Live'), 8)
        check(f'{tag}: Listen Live heard', ms >= 0, f'{ms} ms')
        keep = js(d, "return performance.getEntriesByType('resource').map((e) => new URL(e.name).pathname).filter((p) => p.startsWith('/assets/'))")
        ctl('redeploy', {'keep': keep})
        js(d, 'window.__loads = performance.getEntriesByType("navigation").length; window.__mark = Math.random();')
        mark = js(d, 'return window.__mark')
        for label, path in (('DJs', '/hosts'), ('Schedule', '/guide')):
            t1 = js(d, clock)
            if not menu_click(d, label):
                err = js(d, "return !!document.querySelector('[data-testid=\"error-boundary\"]')")
                check(f'{tag}: after a new release, the menu is still there for "{label}"', False,
                      'the error screen covers the whole page, menu included' if err else 'no visible link')
                continue
            time.sleep(3.5)
            same_tab = js(d, 'return window.__mark === arguments[0]', mark)
            t2 = js(d, clock)
            err = js(d, "return !!document.querySelector('[data-testid=\"error-boundary\"]')")
            check(f'{tag}: after a new release, "{label}" from the menu: the station still plays (no reload, no new tap)',
                  same_tab and t1 is not None and t2 is not None and t2 - t1 > 2.0,
                  f'{"same page" if same_tab else "the page reloaded"}, clock {t1} -> {t2} in 3.5 s'
                  + (', error screen shown' if err else '') + f', at {js(d, "return location.pathname")}')
        t1 = js(d, clock)
        here = f'/listen/{np["sid"]}'
        for _ in range(3):
            if js(d, 'return location.pathname') == here:
                break
            d.back()
            wait_for(d, "location.pathname === " + json.dumps(here), 3)
        time.sleep(3.0)
        t2 = js(d, clock)
        check(f'{tag}: after a new release, back on the station page: still playing with no new tap',
              t1 is not None and t2 is not None and t2 - t1 > 2.0, f'{t1} -> {t2} in 3 s at {js(d, "return location.pathname")}')
        m = ctl('missed')
        info(f'{tag}: old code files asked for after the release: {m["missed"]}; the page reloaded: {m["reloaded"]}')
    finally:
        d.quit()


def stream_missing(tag, args, no_mse=False):
    np = ctl('reset', {'mode': 'server', 'stream': 'missing', 'show': False})
    d = browser(args, no_mse)
    try:
        open_page(d, f'/listen/{np["sid"]}')
        wait_for(d, "document.body.innerText.includes('Live stream')", 8)
        ms = press(d, AUDIBLE, button(d, 'Listen Live'), 8)
        check(f'{tag}: a broken stream falls back to the built-in player', ms >= 0, f'{ms} ms')
        check(f'{tag}: no script errors (broken stream)', not errors(d), ' | '.join(errors(d)))
    finally:
        d.quit()


# ── The phone takes the sound just after it started (Shmuel, 2026-11-20) ─────────────
def taken(tag, args, no_mse=False, hls=False):
    """A second after Listen the stream element is paused by something other than the page
    (iPhone Safari does this): it must play again by itself, with no new tap. This runs the
    part that only a real engine can say: play() on the element, outside any click, after a
    click once started it, is allowed (WebKit keeps that per element, as iPhone Safari does)."""
    np = ctl('reset', {'mode': 'server', 'stream': 'hls' if hls else 'ok', 'show': False})
    d = browser(args, no_mse, native_hls=hls)
    try:
        open_page(d, f'/listen/{np["sid"]}')
        wait_for(d, "document.body.innerText.includes('Live stream')", 8)
        ms = press(d, STREAM_PLAYING, button(d, 'Listen Live'), 8)
        check(f'{tag}: (set up) Listen Live heard', ms >= 0, f'{ms} ms')
        time.sleep(1.0)
        js(d, "document.querySelector('[data-testid=station-stream]').pause();")
        back = wait_for(d, "(() => { const a = document.querySelector('[data-testid=station-stream]'); "
                           "if (!a || a.paused) return false; if (window.__tk === undefined) window.__tk = a.currentTime; "
                           "return a.currentTime > window.__tk + 0.5; })()", 8)
        st = js(d, "const a = document.querySelector('[data-testid=station-stream]'); return [a.paused, a.dataset.streamState];")
        check(f'{tag}: paused by the phone a second after the start: plays again by itself, no tap', back >= 0, f'{back} ms, {st}')
        check(f'{tag}: no "tap to resume" asked', js(d, "return !document.querySelector('[data-testid=listen-resume]')"))
        check(f'{tag}: no script errors', not errors(d), ' | '.join(errors(d)))
    finally:
        d.quit()


# ── /call/:id ─────────────
def call_in(tag, args):
    np = ctl('reset', {'mode': 'browser', 'show': True})
    d = browser(args)
    try:
        d.get(f'{BASE}/call/{np["sid"]}')
        ok = wait_for(d, "document.body.innerText.includes('Live now: Ask the Rav')", 8)
        check(f'{tag}: page loads with the live show', ok >= 0)
        name = d.find_element(By.CSS_SELECTOR, 'input[placeholder=Name]')
        call = button(d, 'Call in')
        check(f'{tag}: Call in disabled until a name is typed', call is not None and not call.is_enabled())
        name.send_keys('Yaakov')
        time.sleep(0.2)
        check(f'{tag}: name field takes typing, Call in enabled', name.get_attribute('value') == 'Yaakov' and button(d, 'Call in').is_enabled())
        info(f'{tag}: navigator.mediaDevices: {js(d, "return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)")}, secure context: {js(d, "return window.isSecureContext")}')
        button(d, 'Call in').click()
        wait_for(d, "document.body.innerText.includes('Hang up') || /microphone|allow/i.test(document.body.innerText)", 8)
        txt = body_text(d)
        in_call = 'Hang up' in txt
        check(f'{tag}: Call in asks for the mic and gets it (mock capture device)', in_call, '' if in_call else txt[-200:])
        if not in_call:
            return
        time.sleep(0.8)
        s = ctl('state')
        check(f'{tag}: call sent with the name, call socket authenticated', (s['callBody'] or {}).get('name') == 'Yaakov' and s['callAuths'][:1] == ['tok-1'], json.dumps(s['callBody']))
        ctl('callStatus', {'status': 'waiting'})
        w = wait_for(d, "document.body.innerText.includes(\"You're in line\")", 3)
        check(f'{tag}: status line follows the screener (waiting)', w >= 0)
        lvl = wait_for(d, "[...document.querySelectorAll('.bg-green-500')].some(b => parseFloat(b.style.width) > 0)", 4)
        check(f'{tag}: mic level meter moves with the (mock) microphone', lvl >= 0, f'{lvl} ms')
        ctl('callStatus', {'status': 'on_air'})
        w = wait_for(d, "document.body.innerText.includes(\"You're on the air!\")", 3)
        check(f'{tag}: on air shown', w >= 0)
        button(d, 'Hang up').click()
        w = wait_for(d, "document.body.innerText.includes('Your call has ended')", 3)
        check(f'{tag}: Hang up ends the call', w >= 0)
        wd = sideways(d)
        check(f'{tag}: no sideways scroll at {WIDTH} px', wd[0] <= wd[1], f'{wd[0]} > {wd[1]}' if wd[0] > wd[1] else '')
        check(f'{tag}: no script errors', not errors(d), ' | '.join(errors(d)))
    finally:
        d.quit()


# ── Release while listening ─────────────
def release(tag, args, expect_prompt, no_mse=False):
    np = ctl('reset', {'mode': 'browser', 'show': False})
    d = browser(args, no_mse)
    try:
        open_page(d, f'/listen/{np["sid"]}')
        wait_for(d, "document.body.innerText.includes('Listen Live')", 8)
        time.sleep(1.0)  # the site channel is open
        ms = press(d, AUDIBLE, button(d, 'Listen Live'), 5)
        check(f'{tag}: listening before the release', ms >= 0, f'{ms} ms')
        time.sleep(9)  # starting to listen counts as an item change for 8 s (appUpdate.ts)
        js(d, 'window.__beforeRelease = true;')
        ctl('release', {'include_playing': True})
        time.sleep(3)
        check(f'{tag}: a push that includes playing tabs waits for the next item', js(d, 'return window.__beforeRelease === true'))
        ctl('onair')
        reloaded = -1
        t0 = time.time()
        while time.time() - t0 < 15:
            try:
                if js(d, 'return window.__beforeRelease !== true && document.readyState === "complete"'):
                    reloaded = round((time.time() - t0) * 1000)
                    break
            except Exception:
                pass
            time.sleep(0.2)
        check(f'{tag}: the tab reloads onto the release after the item change', reloaded >= 0, f'{reloaded} ms')
        if reloaded < 0:
            return
        if expect_prompt:
            seen = wait_for(d, "!!document.querySelector('[data-testid=resume-after-release]')", 6)
            check(f'{tag}: "Tap to keep listening" shown, no sound yet', seen >= 0 and js(d, 'return window.__audible().length') == 0, f'{seen} ms')
            if seen < 0:
                return
            ms = press(d, AUDIBLE, d.find_element(By.CSS_SELECTOR, '[data-testid=resume-after-release]'), 5)
            check(f'{tag}: one tap and the sound is back (within 1.5 s)', 0 <= ms <= 1500, f'{ms} ms')
            check(f'{tag}: the prompt is gone', not js(d, "return !!document.querySelector('[data-testid=resume-after-release]')"))
        else:
            ms = wait_for(d, AUDIBLE, 6)
            check(f'{tag}: plays on by itself, no prompt', ms >= 0 and not js(d, "return !!document.querySelector('[data-testid=resume-after-release]')"), f'{ms} ms')
        check(f'{tag}: no script errors after the reload', not errors(d), ' | '.join(errors(d)))
    finally:
        d.quit()


# ── Other public pages at phone width ─────────────
def pages(tag, args):
    ctl('reset', {'mode': 'browser', 'show': False})
    d = browser(args)
    try:
        for path, expect in [('/stations', "Kol B'ramah"), ('/guide', 'Morning music'), ('/hosts', 'Reb Moshe'), ('/archives', 'Motzei Shabbos Live')]:
            d.get(BASE + path)
            shown = wait_for(d, f'document.body.innerText.includes({json.dumps(expect)})', 8)
            time.sleep(0.8)
            w = sideways(d)
            errs = errors(d)
            check(f'{tag} {path}: renders ("{expect}")', shown >= 0, '' if shown >= 0 else body_text(d)[:200])
            check(f'{tag} {path}: no console errors', not errs, ' | '.join(errs))
            check(f'{tag} {path}: no sideways scroll at {WIDTH} px', w[0] <= w[1], f'{w[0]} > {w[1]}' if w[0] > w[1] else '')
    finally:
        d.quit()


def wait_http(url, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2).read()
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    if not os.path.exists(MINIBROWSER):
        print(f'WebKitGTK MiniBrowser not found at {MINIBROWSER}: apt-get install webkit2gtk-driver')
        return 2
    if not wait_http(UPSTREAM, 3):
        print(f'The built site is not served at {UPSTREAM}: npm run build && npx vite preview --port 4183')
        return 2
    if wait_http(f'{BASE}/api/v1/stations', 1):
        print(f'Port {PORT} is taken (an old fake-server.mjs?): stop it or set PORT')
        return 2
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # so the server below is stopped too
    server = subprocess.Popen(['node', os.path.join(HERE, 'fake-server.mjs')],
                              env={**os.environ, 'PORT': str(PORT), 'UPSTREAM': UPSTREAM, 'STREAM_PORT': STREAM_PORT})
    try:
        if not wait_http(f'{BASE}/api/v1/stations'):
            print('fake-server.mjs did not start')
            return 2
        run = lambda part: not ONLY or part in ONLY  # noqa: E731
        if run('listen'):
            listen_browser('listen WebKit', SAFARI)
            listen_browser('listen WebKit no MSE', SAFARI, no_mse=True)
        if run('stream'):
            listen_stream('stream WebKit (MSE)', SAFARI, 'mse')
            listen_stream('stream WebKit no MSE (iPhone path)', SAFARI, 'plain', no_mse=True)
            stream_missing('stream WebKit no MSE', SAFARI, no_mse=True)
        if run('bar'):
            bar('bar WebKit', SAFARI, 'browser')
            bar('bar WebKit no MSE (iPhone path), server stream', SAFARI, 'server', no_mse=True)
            bar('bar WebKit as iPhone (no MSE, native HLS), server stream', SAFARI, 'server', no_mse=True, hls=True)
        if run('taken'):
            taken('taken WebKit no MSE (iPhone path)', SAFARI, no_mse=True)
            taken('taken WebKit as iPhone (no MSE, native HLS)', SAFARI, no_mse=True, hls=True)
        if run('stale'):
            stale_release('stale WebKit, built-in player', SAFARI, 'browser')
            stale_release('stale WebKit as iPhone (no MSE, native HLS), server stream', SAFARI, 'server', no_mse=True, hls=True)
        if run('callin'):
            call_in('call-in WebKit', SAFARI + ['--enable-media-stream=true', '--enable-mock-capture-devices=true'])
        if run('release'):
            release('release WebKit (no sound without a tap)', SAFARI, True)
            release('release WebKit (autoplay allowed)', ALLOW, False)
        if run('pages'):
            pages('pages WebKit', SAFARI)
    finally:
        server.terminate()
    print(f"\n{state['passed']} passed, {state['xfail']} known bugs (xfail), {state['failed']} failed")
    return 1 if state['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
