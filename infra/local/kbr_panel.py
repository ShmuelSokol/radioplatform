#!/usr/bin/env python3
"""The local FM box's own control room: run the station at BMG with no internet.

Shmuel, 2026-11-19: "it needs to have some sort of visual interface for someone to
physically go there and operate the station without any internet." The relay
(kbr_relay.py) serves this page on the box itself: open it in a browser on a screen
and keyboard plugged into the box (http://localhost:8080), or on a laptop plugged into
its network port (http://<box address>:8080, password from setup-box.sh).

What it shows and does:

* the clock, whether the internet line is up, what is on air with the time left, and
  what comes next (the box's own plan: queue, timed items, songs it draws);
* Next (ends the item on air), Play now (on air within seconds, through the engine's
  timed lane), Play next, schedule a recording at a time, take an item off, move it up;
* search the library copy on the disk, and load a recording from a USB stick (a song,
  spot, shiur, weather report...) to play it.

While the internet is up the site is in charge (its control room at kbrlive.com):
this page then shows what is on air and offers Next only, so the two never disagree.
What was done here offline goes to the site when the line is back
(kbr_relay.Relay.catch_up, app/services/box_sync.py): plays are logged, loaded
recordings join the library, additions still to come join the site's queue, and
anything taken off is listed for staff in an alert. See wiki/local-fm-box.md.

Standard library only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("kbr-panel")

COMING_ITEMS = 40
UPLOAD_MAX_BYTES = 300 * 1024 * 1024
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".mp2"}
KINDS = ("music", "spot", "shiur", "jingle", "story", "weather")
PLAY_NOW_LEAD = 3.0  # seconds: the engine loads a local file in well under that
COOKIE = "kbr_panel"


def _quote(value) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def cue_in_of(uri: str) -> float:
    m = re.search(r'liq_cue_in="([0-9.]+)"', uri or "")
    return float(m.group(1)) if m else 0.0


def audio_seconds(path: Path) -> float | None:
    """Length of a file loaded from a USB stick (ffprobe, which comes with the engine's
    packages); None when it cannot be read (it still plays to its end)."""
    probe = shutil.which("ffprobe")
    if not probe:
        return None
    try:
        out = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, timeout=30, text=True,
        )
        return round(float(out.stdout.strip()), 2)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


class Panel:
    def __init__(self, relay, password: str = ""):
        self.relay = relay
        self.password = password
        self.secret = secrets.token_bytes(32)
        self.uploads_dir = relay.dir / "uploads"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    # ── sign-in (only when the page is reachable from the network) ──────────────
    def token(self) -> str:
        return hmac.new(self.secret, b"panel", hashlib.sha256).hexdigest()

    def signed_in(self, cookie_header: str | None) -> bool:
        if not self.password:
            return True
        for part in (cookie_header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and hmac.compare_digest(value, self.token()):
                return True
        return False

    def login(self, password: str) -> bool:
        return bool(self.password) and hmac.compare_digest(password.encode(), self.password.encode())

    # ── what the page shows ────────────────────────────────────────────────────
    def state(self) -> dict:
        r = self.relay
        now = r.clock()
        st = r.status()
        cur = r.on_air
        on_air = None
        if cur:
            duration = float(cur.get("duration") or 0.0)
            length = max(0.0, duration - cue_in_of(cur.get("uri") or "")) if duration else None
            elapsed = max(0.0, now - cur["at"])
            on_air = {
                "title": cur.get("title") or "", "kind": cur.get("kind") or "", "lane": cur.get("lane"),
                "started": cur["at"], "elapsed": round(elapsed, 1),
                "remaining": round(length - elapsed, 1) if length else None, "length": length,
                "entry_id": cur.get("entry_id"),
            }
        return {
            "now": now,
            "online": bool(r.online),
            "timezone": r.plan.get("timezone") or "America/New_York",
            "station": r.plan.get("station_name") or "",
            "in_blackout": st["in_blackout"],
            "clock_ok": st["clock_ok"],
            "plan_age_seconds": st["plan_age_seconds"],
            "offline_days_ahead": st.get("offline_days_ahead"),
            "reports_kept": st["reports_kept"],
            "on_air": on_air,
            "coming": self.coming(now),
            "changes_waiting": len(r.added_local) + len(r.removed_site),
            "uploads_waiting": sum(1 for u in r.uploads.values() if not u.get("asset_id")),
            "can_change": not r.online,
        }

    def coming(self, now: float) -> list[dict]:
        """What the box will play next, as offline_next would choose it, with expected
        times. Read-only: nothing is marked done."""
        r = self.relay
        with r.lock:
            done = set(r.done) | set(r.timed_sent)
            timed = sorted(
                (e for e in r.timed_entries() if e["entry_id"] not in done and e["at"] >= now - 300),
                key=lambda e: (e["at"], e["position"]),
            )
            untimed: list[tuple[dict, str]] = []
            if r.resume and r.resume["entry_id"] not in done:
                untimed.append((r.resume, "resume"))
            for eid in r.front:
                item = r.find_item(eid)
                if item and eid not in done:
                    untimed.append((item, "box"))
            for e in r.plan.get("entries", []):
                if e.get("at") is None and e["entry_id"] not in done:
                    untimed.append((e, "queue"))
                    if len(untimed) >= COMING_ITEMS:
                        break
        cur = r.on_air
        t = now
        if cur and cur.get("duration"):
            t = max(now, cur["at"] + float(cur["duration"]))
        out: list[dict] = []
        ti = 0
        for item, source in untimed:
            # timed items whose time comes before this one go first
            while ti < len(timed) and timed[ti]["at"] <= t:
                e = timed[ti]
                src = "box" if e.get("local") else ("ahead" if e.get("projected") else "queue")
                if e.get("mode") == "hard":
                    out.append(self._row(e, e["at"], src))
                else:
                    # a soft item waits for the item before it to end
                    out.append(self._row(e, max(t, e["at"]), src))
                    t = max(t, e["at"]) + float(e.get("duration") or 30.0)
                ti += 1
            out.append(self._row(item, t, source))
            t += float(item.get("duration") or 180.0)
            if len(out) >= COMING_ITEMS:
                return out
        while ti < len(timed) and len(out) < COMING_ITEMS:
            e = timed[ti]
            out.append(self._row(e, e["at"], "box" if e.get("local") else ("ahead" if e.get("projected") else "queue")))
            ti += 1
        if len(out) < COMING_ITEMS and r.extension.get("songs"):
            out.append({"entry_id": "", "title": "Then songs for the hour, drawn by the box", "kind": "music",
                        "time": max([t] + [o["time"] for o in out]), "source": "box", "fixed": False, "mode": None, "duration": None})
        return out

    @staticmethod
    def _row(item: dict, t: float, source: str) -> dict:
        return {
            "entry_id": item["entry_id"], "title": item.get("title") or "", "kind": item.get("kind") or "",
            "time": t, "fixed": item.get("at") is not None, "mode": item.get("mode"),
            "duration": item.get("duration"), "source": source,
        }

    def library(self, q: str, kind: str = "") -> list[dict]:
        r = self.relay
        words = [w for w in q.lower().split() if w]
        found = []
        for uid, up in r.uploads.items():
            text = f"{up.get('title', '')} usb".lower()
            if all(w in text for w in words) and (not kind or up.get("kind") == kind):
                found.append({"id": f"upload:{uid}", "title": up.get("title"), "artist": "Loaded at the box (USB)",
                              "kind": up.get("kind"), "duration": up.get("duration"), "on_disk": True})
        for aid, a in r.library.items():
            text = f"{a.get('title', '')} {a.get('artist', '')}".lower()
            if words and not all(w in text for w in words):
                continue
            if kind and (a.get("kind") or "") != kind:
                continue
            found.append({"id": aid, "title": a.get("title"), "artist": a.get("artist"), "kind": a.get("kind"),
                          "duration": a.get("duration"), "on_disk": r.have(a["url"])})
            if len(found) >= 400:
                break
        # on this disk first (playable), then by title
        found.sort(key=lambda i: (not i["on_disk"], str(i["id"]).startswith("upload:") is False, (i["title"] or "").lower()))
        return found[:200]

    # ── what the page does ────────────────────────────────────────────────────
    def _item_for(self, ref: str, mode: str | None = None, at: float | None = None) -> dict | None:
        """A local item for a library item or a USB recording."""
        r = self.relay
        if ref.startswith("upload:"):
            up = r.uploads.get(ref[7:])
            if not up:
                return None
            target, title, kind, duration = up["path"], up.get("title") or "", up.get("kind") or "music", up.get("duration")
            asset = ref
        else:
            a = r.library.get(ref)
            if not a or not r.have(a["url"]):
                return None
            target, title, kind, duration = a["url"], a.get("title") or "", a.get("kind") or "music", a.get("duration")
            asset = ref
        eid = str(uuid.uuid4())

        def uri(lane):
            return (f"annotate:kbr_entry={_quote(eid)},kbr_lane={_quote(lane)},kbr_kind={_quote(kind)},"
                    f"kbr_asset={_quote(asset)},kbr_overlap=\"0.00\",liq_cross_duration=\"0.20\","
                    f"title={_quote(title)}:{target}")

        item = {
            "entry_id": eid, "asset_id": asset, "title": title, "kind": kind, "duration": duration,
            "url": target, "long": kind in ("shiur", "story") or (duration or 0) >= 12 * 60,
            "uri_main": uri("main"), "uri_timed": uri("timed") if mode == "hard" or at is None else None,
            "at": at, "mode": mode if at is not None else None,
            "local": "upload" if ref.startswith("upload:") else "box",
        }
        with r.lock:
            r.local_items[eid] = item
        return item

    def play(self, ref: str, when: str, at: float | None = None, mode: str = "hard") -> dict:
        r = self.relay
        if when == "now":
            now = r.clock()
            item = self._item_for(ref, "hard", now + PLAY_NOW_LEAD)
            if item is None:
                return {"ok": False, "error": "That recording is not on this box's disk"}
            ok = r._post_engine("/timed", {"uri": r.localize(item["uri_timed"]), "at": now + PLAY_NOW_LEAD,
                                           "id": item["entry_id"]})
            with r.lock:
                if ok:
                    r.timed_sent.add(item["entry_id"])
                    r.served[item["entry_id"]] = item["asset_id"]
                r.added_local.append(item["entry_id"])
                r._save_state()
            return {"ok": ok, "error": None if ok else "The engine did not take it"}
        if when == "at":
            if at is None or at <= r.clock():
                return {"ok": False, "error": "Pick a time still to come"}
            item = self._item_for(ref, "soft" if mode == "soft" else "hard", float(at))
            if item is None:
                return {"ok": False, "error": "That recording is not on this box's disk"}
            with r.lock:
                r.local_timed.append(item["entry_id"])
                r.added_local.append(item["entry_id"])
                r._save_state()
            return {"ok": True, "entry_id": item["entry_id"]}
        item = self._item_for(ref)
        if item is None:
            return {"ok": False, "error": "That recording is not on this box's disk"}
        with r.lock:
            if when == "end":
                r.front.append(item["entry_id"])
            else:
                r.front.insert(0, item["entry_id"])
            r.added_local.append(item["entry_id"])
            r._save_state()
        self._refresh_cued()
        return {"ok": True, "entry_id": item["entry_id"]}

    def _refresh_cued(self) -> None:
        """The engine has already loaded its next item: drop it so it asks again and
        gets what the operator put first (the dropped one comes back later)."""
        r = self.relay
        with r.lock:
            dropped, r.cued_main = r.cued_main, None
            if dropped and dropped in r.done:
                r.done.remove(dropped)
                if dropped not in r.front:
                    r.front.insert(1 if r.front else 0, dropped)
            r._save_state()
        r._post_engine("/flush", {})

    def remove(self, entry_id: str) -> dict:
        r = self.relay
        item = r.find_item(entry_id)
        if item is None:
            return {"ok": False, "error": "Not found"}
        with r.lock:
            if entry_id in r.front:
                r.front.remove(entry_id)
            if entry_id in r.local_timed:
                r.local_timed.remove(entry_id)
            if entry_id in r.added_local:
                r.added_local.remove(entry_id)
            elif not item.get("local"):
                # an item of the site's queue (or worked out from its schedule): listed for staff
                r.removed_site.append({"entry_id": entry_id, "title": item.get("title"), "at": item.get("at")})
            r._save_state()
        r.mark_done(entry_id)
        if entry_id == r.cued_main:
            self._refresh_cued()
        return {"ok": True}

    def move(self, entry_id: str, to: str) -> dict:
        """'next': this item plays after the one on air. 'up'/'down': one place, among
        the items put first here."""
        r = self.relay
        item = r.find_item(entry_id)
        if item is None or item.get("at") is not None:
            return {"ok": False, "error": "Only items without a set time can be moved"}
        with r.lock:
            if to == "next":
                if entry_id in r.front:
                    r.front.remove(entry_id)
                r.front.insert(0, entry_id)
            elif entry_id in r.front:
                i = r.front.index(entry_id)
                j = i - 1 if to == "up" else i + 1
                if 0 <= j < len(r.front):
                    r.front[i], r.front[j] = r.front[j], r.front[i]
            else:
                r.front.append(entry_id) if to == "down" else r.front.insert(0, entry_id)
            r._save_state()
        self._refresh_cued()
        return {"ok": True}

    def next(self) -> dict:
        return {"ok": bool(self.relay._post_engine("/skip", {}))}

    def upload(self, name: str, kind: str, title: str, data: bytes) -> dict:
        ext = os.path.splitext(name)[1].lower()
        if ext not in AUDIO_EXT:
            return {"ok": False, "error": f"Not an audio file ({ext or 'no extension'})"}
        if not data:
            return {"ok": False, "error": "The file is empty"}
        kind = kind if kind in KINDS else "music"
        uid = uuid.uuid4().hex
        path = self.uploads_dir / f"{uid}{ext}"
        part = path.with_name(path.name + ".part")
        with open(part, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(part, path)
        r = self.relay
        clean = re.sub(r"\s+", " ", title.strip())[:200] or os.path.splitext(os.path.basename(name))[0][:200]
        with r.lock:
            r.uploads[uid] = {"path": str(path), "title": clean, "kind": kind, "duration": audio_seconds(path),
                              "loaded_at": r.clock()}
            r._write_json("uploads.json", r.uploads)
        log.info("loaded %s from the control room as %s (%s)", name, uid, kind)
        return {"ok": True, "id": f"upload:{uid}", "title": clean, "duration": r.uploads[uid]["duration"]}


def make_handler(panel: Panel):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, data: bytes, ctype="application/json", extra=None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, status=200, extra=None):
            self._send(status, json.dumps(obj).encode(), extra=extra)

        def _body(self, limit=64 * 1024) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                raise ValueError("too big")
            return self.rfile.read(length) if length else b""

        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path in ("/", "/index.html"):
                return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            if not panel.signed_in(self.headers.get("Cookie")):
                return self._json({"ok": False, "error": "sign in"}, 401)
            q = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/api/state":
                return self._json(panel.state())
            if parsed.path == "/api/library":
                return self._json({"items": panel.library(q.get("q", [""])[0], q.get("kind", [""])[0])})
            return self._json({"ok": False}, 404)

        def do_POST(self):  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            # Only this page's own script sends this header: another site cannot make a
            # browser post here (no simple cross-site request carries it)
            if self.headers.get("X-Panel") != "1":
                return self._json({"ok": False, "error": "forbidden"}, 403)
            if parsed.path == "/api/login":
                try:
                    body = json.loads(self._body() or b"{}")
                except ValueError:
                    body = {}
                if panel.login(str(body.get("password") or "")):
                    return self._json({"ok": True}, extra={
                        "Set-Cookie": f"{COOKIE}={panel.token()}; HttpOnly; SameSite=Strict; Path=/"})
                time.sleep(1.0)
                return self._json({"ok": False, "error": "Wrong password"}, 401)
            if not panel.signed_in(self.headers.get("Cookie")):
                return self._json({"ok": False, "error": "sign in"}, 401)
            if parsed.path == "/api/next":
                self._body()
                return self._json(panel.next())
            if parsed.path == "/api/upload":
                if panel.relay.online:
                    self.close_connection = True
                    return self._json({"ok": False, "error": "The internet is up: load it on kbrlive.com"}, 409)
                try:
                    data = self._body(UPLOAD_MAX_BYTES)
                except ValueError:
                    self.close_connection = True
                    return self._json({"ok": False, "error": "The file is too big (300 MB at most)"}, 413)
                return self._json(panel.upload(
                    urllib.parse.unquote(self.headers.get("X-Filename") or "file.mp3"),
                    self.headers.get("X-Kind") or "music",
                    urllib.parse.unquote(self.headers.get("X-Title") or ""), data))
            try:
                body = json.loads(self._body() or b"{}")
            except ValueError:
                return self._json({"ok": False, "error": "bad request"}, 400)
            if panel.relay.online:
                return self._json({"ok": False, "error": "The internet is up: the site's control room is in charge"}, 409)
            if parsed.path == "/api/play":
                at = body.get("at")
                return self._json(panel.play(str(body.get("id") or ""), str(body.get("when") or "next"),
                                             float(at) if at not in (None, "") else None,
                                             str(body.get("mode") or "hard")))
            if parsed.path == "/api/remove":
                return self._json(panel.remove(str(body.get("entry_id") or "")))
            if parsed.path == "/api/move":
                return self._json(panel.move(str(body.get("entry_id") or ""), str(body.get("to") or "next")))
            return self._json({"ok": False}, 404)

        def log_message(self, *_):
            pass

    return Handler


def serve(panel: Panel, port: int, bind: str) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((bind, port), make_handler(panel))
    threading.Thread(target=server.serve_forever, daemon=True, name="panel-http").start()
    log.info("control room on http://%s:%d/", bind, port)
    return server


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KBR box control room</title>
<style>
:root{--face:#c0c0c0;--light:#fff;--shadow:#808080;--dark:#000;--black:#000;--yellow:#ffff00;--green:#00ff40;--red:#ff3030;--blue:#0000ff;--magenta:#ff40ff;}
*{box-sizing:border-box}
html,body{margin:0;background:var(--face);color:#000;font-family:Tahoma,Verdana,"Segoe UI",system-ui,sans-serif;font-size:15px}
button{font:inherit;background:var(--face);border:0;padding:8px 14px;min-height:44px;cursor:pointer;
 box-shadow:inset -1px -1px 0 var(--dark),inset 1px 1px 0 var(--light),inset -2px -2px 0 var(--shadow),inset 2px 2px 0 #dfdfdf}
button:active{box-shadow:inset 1px 1px 0 var(--dark),inset -1px -1px 0 var(--light),inset 2px 2px 0 var(--shadow)}
button:disabled{color:#808080;cursor:default}
button.red{background:#ff8080}
input,select{font:inherit;padding:8px;min-height:44px;border:0;background:#fff;
 box-shadow:inset 1px 1px 0 var(--shadow),inset -1px -1px 0 var(--light),inset 2px 2px 0 var(--dark)}
.top{background:#000;color:#fff;display:flex;flex-wrap:wrap;gap:18px;align-items:flex-end;padding:8px 12px}
.lbl{font-size:12px;color:#ddd}
.big{font-family:"Courier New",monospace;font-weight:bold;font-size:44px;line-height:1}
.rem{color:var(--red)}.clk{color:var(--green);margin-left:auto}
.line{padding:4px 10px;font-weight:bold}
.line.up{background:#0a0;color:#fff}.line.down{background:#c00;color:#fff}
.now{background:#000;padding:4px 10px 10px}
.now div{border:2px solid #fff;color:var(--green);font-size:24px;font-weight:bold;padding:4px 8px;min-height:40px;margin-top:6px}
.now div.next{color:var(--yellow)}
.bar{display:flex;flex-wrap:wrap;gap:6px;padding:8px;align-items:center}
.note{padding:6px 10px;background:#ffffe0;border:1px solid #808080;margin:6px 8px}
.note.bad{background:#ffd0d0}
.grid{display:grid;grid-template-columns:1fr;gap:0}
@media(min-width:1000px){.grid{grid-template-columns:3fr 2fr}}
table{width:100%;border-collapse:collapse;background:#000;color:var(--yellow)}
th{background:var(--face);color:#000;text-align:left;font-weight:normal;font-size:13px;padding:2px 6px;
 box-shadow:inset -1px -1px 0 var(--shadow),inset 1px 1px 0 var(--light)}
td{padding:4px 6px;font-weight:bold;vertical-align:middle}
td.t{white-space:nowrap;width:1%}
tr.sel td{background:var(--blue)}
tr.fixed td{color:#80ffff}
tr.box td{color:var(--magenta)}
.acts button{min-height:36px;padding:4px 8px;font-size:13px}
h2{font-size:15px;margin:8px;font-weight:bold}
.pane{padding:0 0 8px}
.scroll{max-height:60vh;overflow:auto}
@media(max-width:640px){.hide-s{display:none}.big{font-size:32px}.acts button{padding:2px 6px;font-size:12px;min-height:36px}.acts{display:flex;flex-wrap:nowrap;gap:2px;justify-content:flex-end}td{font-size:14px}}
#login{max-width:360px;margin:15vh auto;padding:16px;background:var(--face);
 box-shadow:inset -1px -1px 0 var(--dark),inset 1px 1px 0 var(--light),inset -2px -2px 0 var(--shadow)}
.hide{display:none}
dialog{border:0;padding:16px;background:var(--face);box-shadow:inset -1px -1px 0 var(--dark),inset 1px 1px 0 var(--light),inset -2px -2px 0 var(--shadow),0 4px 20px #0008;max-width:95vw}
</style></head><body>
<div id="login" class="hide">
 <h2>KBR box control room</h2>
 <p>Password (on the sheet kept with the box):</p>
 <form id="lf"><input id="pw" type="password" autocomplete="current-password" style="width:100%"><p><button>Sign in</button> <span id="lerr"></span></p></form>
</div>
<div id="app" class="hide">
 <div class="top">
  <div><div class="lbl">Remaining</div><div class="big rem" id="rem">-:--</div></div>
  <div><div class="lbl">On air</div><div class="big" style="color:var(--green)" id="ela">0:00</div></div>
  <div><div class="lbl">Internet line</div><div class="line" id="line">…</div></div>
  <div class="clk"><div class="lbl">Clock</div><div class="big" id="clock">--:--:--</div></div>
 </div>
 <div class="now"><div id="onair">—</div><div class="next" id="upnext">—</div></div>
 <div id="notes"></div>
 <div class="bar">
  <button id="bnext" class="red" title="End the item on air now (N)"><u>N</u>ext</button>
  <span class="lbl" id="mode" style="color:#000"></span>
 </div>
 <div class="grid">
  <div class="pane"><h2>Coming up</h2><div class="scroll"><table>
   <thead><tr><th>Time</th><th class="hide-s">Length</th><th>Title</th><th class="hide-s">Type</th><th></th></tr></thead>
   <tbody id="coming"></tbody></table></div></div>
  <div class="pane"><h2>Library on this box</h2>
   <div class="bar"><input id="q" placeholder="Search title or artist (/)" style="flex:1;min-width:160px">
    <select id="kind"><option value="">All</option><option>music</option><option>spot</option><option>shiur</option><option>jingle</option><option>story</option><option>weather</option></select></div>
   <div class="bar"><label><button id="bload" type="button">Load from USB stick…</button></label>
    <input id="file" type="file" accept="audio/*" class="hide"><span id="upmsg"></span></div>
   <div class="scroll"><table><thead><tr><th>Title</th><th class="hide-s">Length</th><th></th></tr></thead><tbody id="lib"></tbody></table></div>
  </div>
 </div>
</div>
<dialog id="dlg"><form method="dialog" id="df">
 <h2 id="dtitle"></h2>
 <div class="bar"><button value="now" class="red">Play now</button><button value="next">Play next</button><button value="end">After the box's list</button></div>
 <div class="bar">At <input id="dat" type="time" step="60"> <select id="dmode"><option value="hard">on the second (cuts the song)</option><option value="soft">after the song</option></select><button value="at">Schedule</button></div>
 <div class="bar"><button value="cancel">Cancel</button></div>
</form></dialog>
<dialog id="udlg"><form method="dialog">
 <h2>Load from USB stick</h2><p id="ufile"></p>
 <p>Title <input id="utitle" style="width:100%"></p>
 <p>Type <select id="ukind"><option>music</option><option>spot</option><option>shiur</option><option>jingle</option><option>story</option><option>weather</option></select></p>
 <div class="bar"><button value="ok">Load</button><button value="cancel">Cancel</button></div>
</form></dialog>
<script>
"use strict";
const $=s=>document.querySelector(s);
let S=null, tz="America/New_York", libTimer=null, chosen=null;
const H={"X-Panel":"1","Content-Type":"application/json"};
function fmt(sec){if(sec==null||isNaN(sec))return"-:--";sec=Math.max(0,Math.round(sec));const h=Math.floor(sec/3600),m=Math.floor(sec%3600/60),s=sec%60;return(h?h+":"+String(m).padStart(2,"0"):m)+":"+String(s).padStart(2,"0")}
function clock(t,secs){return new Date(t*1000).toLocaleTimeString("en-US",{timeZone:tz,hour:"numeric",minute:"2-digit",second:secs?"2-digit":undefined})}
function esc(s){return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
async function api(path,body){const r=await fetch(path,body===undefined?{}:{method:"POST",headers:H,body:JSON.stringify(body)});
 if(r.status===401){show(false);throw new Error("sign in")}return r.json()}
function show(app){$("#app").classList.toggle("hide",!app);$("#login").classList.toggle("hide",app)}
function note(msg,bad){const d=document.createElement("div");d.className="note"+(bad?" bad":"");d.textContent=msg;$("#notes").prepend(d);setTimeout(()=>d.remove(),8000)}
async function tick(){
 try{S=await api("/api/state")}catch(e){return}
 show(true); tz=S.timezone||tz;
 $("#clock").textContent=clock(S.now,true);
 const a=S.on_air; $("#onair").textContent=a?a.title:(S.in_blackout?"Off the air (Shabbos / Yom Tov)":"—");
 $("#rem").textContent=a&&a.remaining!=null?fmt(a.remaining):"-:--"; $("#ela").textContent=a?fmt(a.elapsed):"0:00";
 const n=S.coming[0]; $("#upnext").textContent=n?n.title:"—";
 const line=$("#line"); line.textContent=S.online?"UP":"DOWN"; line.className="line "+(S.online?"up":"down");
 $("#mode").textContent=S.online?"The internet is up: the site (kbrlive.com control room) runs the station. This screen shows it and offers Next only.":
  "No internet: this box runs the station. Changes made here go to the site when the line is back."+(S.offline_days_ahead?" Planned "+S.offline_days_ahead+" days ahead.":"");
 if(!S.clock_ok) note("This computer's clock is behind: only music plays until it is set.",true);
 renderComing();
}
function renderComing(){
 const tb=$("#coming"); const rows=S.coming.map(c=>{
  const cls=(c.fixed?"fixed ":"")+(c.source==="box"?"box":"");
  const acts=S.can_change&&c.entry_id?`<span class="acts">${c.fixed?"":`<button data-a="next" data-id="${c.entry_id}">Play next</button><button data-a="up" data-id="${c.entry_id}">▲</button>`}<button data-a="remove" data-id="${c.entry_id}">Take off</button></span>`:"";
  return `<tr class="${cls}"><td class="t">${c.fixed?"⏰ ":""}${esc(clock(c.time,false))}</td><td class="t hide-s">${fmt(c.duration)}</td><td>${esc(c.title)}</td><td class="t hide-s">${esc(c.kind)}</td><td class="t">${acts}</td></tr>`});
 tb.innerHTML=rows.join("");
}
$("#coming").addEventListener("click",async e=>{const b=e.target.closest("button");if(!b)return;
 const id=b.dataset.id,a=b.dataset.a; let r;
 if(a==="remove"){if(!confirm("Take this item off?"))return; r=await api("/api/remove",{entry_id:id})}
 else r=await api("/api/move",{entry_id:id,to:a});
 if(!r.ok)note(r.error||"Not done",true); tick()});
$("#bnext").onclick=async()=>{const r=await api("/api/next",{}); if(!r.ok)note("The engine did not answer",true); setTimeout(tick,500)};
async function search(){const q=$("#q").value,k=$("#kind").value;
 const r=await api("/api/library?q="+encodeURIComponent(q)+"&kind="+encodeURIComponent(k));
 $("#lib").innerHTML=r.items.map(i=>`<tr><td>${esc(i.title)}<div style="font-weight:normal;color:#ddd;font-size:12px">${esc(i.artist||"")} · ${esc(i.kind||"")}${i.on_disk?"":" · not on this box"}</div></td><td class="t hide-s">${fmt(i.duration)}</td><td class="t acts">${i.on_disk&&S&&S.can_change?`<button data-id="${esc(i.id)}" data-t="${esc(i.title)}">Play…</button>`:""}</td></tr>`).join("")}
$("#q").addEventListener("input",()=>{clearTimeout(libTimer);libTimer=setTimeout(search,250)});
$("#kind").onchange=search;
$("#lib").addEventListener("click",e=>{const b=e.target.closest("button");if(!b)return;chosen=b.dataset.id;$("#dtitle").textContent=b.dataset.t;$("#dlg").showModal()});
$("#dlg").addEventListener("close",async()=>{const v=$("#dlg").returnValue;if(!chosen||v==="cancel"||!v)return;
 let body={id:chosen,when:v};
 if(v==="at"){const t=$("#dat").value;if(!t){note("Pick a time",true);return}
  const [hh,mm]=t.split(":").map(Number); const at=atLocal(hh,mm); body.at=at; body.mode=$("#dmode").value}
 const r=await api("/api/play",body); note(r.ok?"Done":(r.error||"Not done"),!r.ok); tick()});
function atLocal(hh,mm){ // the next time the station's clock reads hh:mm
 const now=S.now; for(let k=0;k<26*60;k++){const t=Math.floor(now/60+k)*60;
  const p=new Intl.DateTimeFormat("en-US",{timeZone:tz,hour:"numeric",minute:"numeric",hourCycle:"h23"}).formatToParts(new Date(t*1000));
  const h=+p.find(x=>x.type==="hour").value,m=+p.find(x=>x.type==="minute").value; if(h===hh&&m===mm&&t>now)return t}
 return now+60}
$("#bload").onclick=()=>$("#file").click();
$("#file").onchange=()=>{const f=$("#file").files[0];if(!f)return;$("#ufile").textContent=f.name;$("#utitle").value=f.name.replace(/\.[^.]+$/,"");$("#udlg").showModal()};
$("#udlg").addEventListener("close",async()=>{const f=$("#file").files[0];if($("#udlg").returnValue!=="ok"||!f)return;
 $("#upmsg").textContent="Loading "+f.name+"…";
 const r=await fetch("/api/upload",{method:"POST",headers:{"X-Panel":"1","X-Filename":encodeURIComponent(f.name),"X-Kind":$("#ukind").value,"X-Title":encodeURIComponent($("#utitle").value)},body:f}).then(x=>x.json()).catch(()=>({ok:false,error:"Not loaded"}));
 $("#upmsg").textContent=r.ok?"Loaded: "+r.title:(r.error||"Not loaded"); $("#file").value=""; $("#q").value=r.ok?r.title:$("#q").value; search()});
$("#lf").onsubmit=async e=>{e.preventDefault();const r=await fetch("/api/login",{method:"POST",headers:H,body:JSON.stringify({password:$("#pw").value})}).then(x=>x.json());
 if(r.ok){show(true);tick();search()}else $("#lerr").textContent=r.error||"Wrong password"};
document.addEventListener("keydown",e=>{if(e.target.matches("input,select"))return;
 if(e.key==="n"||e.key==="N"){e.preventDefault();$("#bnext").click()} if(e.key==="/"){e.preventDefault();$("#q").focus()}});
tick().then(()=>{if(S)search()}); setInterval(tick,1000);
</script></body></html>
"""
