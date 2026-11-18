#!/usr/bin/env python3
"""KBR Live local relay: keeps a local FM box on air when the internet is down.

The box beside the transmitter runs the same playout engine as the stream server
(infra/streaming/radio.liq). The engine's KBR_BACKEND_URL points at this relay instead of
the website's backend:

    engine  --/next, /started, /hello-->  relay  --when the line is up-->  backend

While the backend answers, the relay only passes calls on, and swaps each audio address
for the copy on this machine's disk when it has one. In the background it keeps

  * the plan: the pending queue with each item's engine URI and the blackout windows
    (GET /playout/{station}/plan), fetched every minute;
  * the audio: every file in the plan first, then the whole library.

When the backend cannot be reached, the relay answers the engine itself from the last
plan: the queue in order, soft timed items when their time has come, hard timed items
handed to the engine's timed lane 45 s ahead so they start on the second, silence inside
a blackout. Start reports are kept on disk and sent when the line is back.

Standard library only, so the box needs nothing installed beyond Python 3.
See wiki/local-fm-box.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("kbr-relay")

TIMED_LEAD_SECONDS = 45.0  # same lead as the backend's own hand-over
MISSED_HARD_SECONDS = 300.0  # a hard item later than this is dropped, as on the backend
# A soft timed item (sponsorship, weather) later than this is not aired by the box on its
# own: after a power cut of hours they would otherwise all air in a row, hours old
MISSED_SOFT_SECONDS = 3600.0
# The computer's clock is this far behind the newest plan on disk: it was not set after a
# power cut (no internet, flat clock battery). Nothing timed is trusted until it is right.
CLOCK_BEHIND_SECONDS = 120.0
DONE_KEEP = 5000
URL_AT_END = re.compile(r'(?:^|(?<=":))(https?://\S+)$')


class Offline(Exception):
    """The backend could not be reached, or answered with a server error."""


class Relay:
    def __init__(
        self,
        upstream: str,
        station_id: str,
        token: str,
        data_dir: str | os.PathLike,
        engine_control: str = "http://127.0.0.1:8081",
        timed_mode: str = "offline",
        mirror_library: bool = True,
        clock=time.time,
    ):
        self.upstream = upstream.rstrip("/")
        self.station_id = station_id
        self.token = token
        self.dir = Path(data_dir)
        self.library_dir = self.dir / "library"
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.engine_control = engine_control.rstrip("/")
        # "offline": hand timed items to the engine only while the backend is away.
        # "always": the backend cannot reach this engine at all (no inbound line).
        self.timed_mode = timed_mode
        self.mirror_library = mirror_library
        self.clock = clock
        self.lock = threading.RLock()
        self.plan: dict = self._read_json("plan.json", {"entries": [], "blackouts": []})
        state = self._read_json("state.json", {})
        # Entries that were handed to the engine or reported on air: never served again
        self.done: list[str] = list(state.get("done", []))
        self.timed_sent: set[str] = set(state.get("timed_sent", []))
        self.online = True
        # The engine's own run id (X-Playout-Instance), passed on to the backend with every
        # call, so its one-engine-per-station rule also covers a box (a box set up with a
        # copied station id is refused, not given half of that station's queue)
        self.engine_instance: str | None = None
        self.last_plan_at = 0.0
        self.hello_owed = False
        self.spool_path = self.dir / "spool.jsonl"
        self.answers: list[dict] = []  # engine answers not yet handed to the backend
        self.commands_done = 0
        self.started_at = time.monotonic()
        self._health: tuple[float, dict] | None = None
        self.stop = threading.Event()

    # ── disk ────────────────────────────────────────────────────────────────
    def _read_json(self, name: str, default):
        try:
            return json.loads((self.dir / name).read_text())
        except (OSError, ValueError):
            return default

    def _write_json(self, name: str, data) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        part = self.dir / (name + ".part")
        # On the disk before it takes the old file's place: a power cut must leave the
        # old file or the new one, never an empty one
        with open(part, "w") as f:
            f.write(json.dumps(data))
            f.flush()
            os.fsync(f.fileno())
        os.replace(part, self.dir / name)

    def _save_state(self) -> None:
        self._write_json("state.json", {"done": self.done[-DONE_KEEP:], "timed_sent": sorted(self.timed_sent)[-500:]})

    def local_path(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode()).hexdigest()
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1][:8]
        return self.library_dir / digest[:2] / (digest + ext)

    def have(self, url: str) -> bool:
        p = self.local_path(url)
        return p.is_file() and p.stat().st_size > 0

    def localize(self, uri: str) -> str:
        """The engine URI with its audio address swapped for the local copy, if there is one."""
        m = URL_AT_END.search(uri)
        if not m or not self.have(m.group(1)):
            return uri
        return uri[: m.start(1)] + str(self.local_path(m.group(1)))

    def silence_file(self) -> Path:
        path = self.dir / "silence-60s.wav"
        if not path.is_file():
            self.dir.mkdir(parents=True, exist_ok=True)
            part = self.dir / "silence-60s.wav.part"
            with wave.open(str(part), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(b"\x00" * (44100 * 4 * 60))
            os.replace(part, path)
        return path

    # ── backend ─────────────────────────────────────────────────────────────
    def _call(self, method: str, path: str, body: bytes | None = None, timeout: float = 5.0):
        """(status, bytes) from the backend. Raises Offline when it cannot be reached or
        fails on its side; a refusal (403, 404) is an answer and is passed on."""
        headers = {"X-Playout-Token": self.token, "Content-Type": "application/json"}
        if self.engine_instance:
            headers["X-Playout-Instance"] = self.engine_instance
        req = urllib.request.Request(
            f"{self.upstream}/playout/{self.station_id}{path}", data=body, method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                self._set_online(False)
                raise Offline(f"backend answered {e.code}") from None
            result = e.code, e.read()
        except (OSError, ValueError) as e:
            self._set_online(False)
            raise Offline(str(e)) from None
        self._set_online(True)
        return result

    def _set_online(self, value: bool) -> None:
        if value != self.online:
            log.warning("backend is %s", "reachable again" if value else "unreachable: running from the local plan")
        self.online = value

    def mark_done(self, entry_id: str | None) -> None:
        if not entry_id:
            return
        with self.lock:
            if entry_id not in self.done:
                self.done.append(entry_id)
                del self.done[:-DONE_KEEP]
                self._save_state()

    # ── calls from the engine ───────────────────────────────────────────────
    def clock_ok(self) -> bool:
        """False when the computer's clock is behind the newest plan on disk: the box came
        up after a power cut with no internet to set it. Then the time is at least the
        plan's (used for blackouts) and nothing timed goes on air."""
        made = self.plan.get("generated_at")
        return not isinstance(made, (int, float)) or self.clock() >= made - CLOCK_BEHIND_SECONDS

    def now(self) -> float:
        made = self.plan.get("generated_at")
        return max(self.clock(), made) if isinstance(made, (int, float)) else self.clock()

    def hello(self, body: bytes) -> tuple[int, bytes]:
        # The engine has just started (a crash, a power cut): its timed lane is empty, so
        # what was handed to it before must be handed again. It ignores an id it holds.
        with self.lock:
            if self.timed_sent:
                self.timed_sent.clear()
                self._save_state()
        try:
            return self._call("POST", "/hello", body or b"{}")
        except Offline:
            # The engine must not wait on us; the backend hears it when the line is back
            self.hello_owed = True
            return 200, b'{"ok":true,"relay":"offline"}'

    def next(self, query: str) -> tuple[int, bytes]:
        try:
            status, data = self._call("GET", "/next" + (f"?{query}" if query else ""))
        except Offline:
            return self.offline_next()
        if status == 200:
            try:
                item = json.loads(data)
                self.mark_done(item.get("entry_id"))
                item["uri"] = self.localize(item["uri"])
                data = json.dumps(item).encode()
            except (ValueError, KeyError, TypeError):
                pass
        return status, data

    def in_blackout(self, now: float) -> bool:
        return any(b["start"] <= now < b["end"] for b in self.plan.get("blackouts", []))

    def offline_next(self) -> tuple[int, bytes]:
        now = self.now()
        clock_ok = self.clock_ok()
        with self.lock:
            if self.in_blackout(now):
                uri = f'annotate:kbr_entry="",kbr_lane="main",kbr_kind="silence",title="Off air":{self.silence_file()}'
                return 200, json.dumps({"uri": uri, "entry_id": ""}).encode()
            done = set(self.done)
            waiting = [
                e for e in self.plan.get("entries", [])
                if e["entry_id"] not in done and e["entry_id"] not in self.timed_sent
            ]
            chosen = None
            # Timed items whose time has come go first, by time then position (as /next)
            due = sorted((e for e in waiting if clock_ok and e.get("at") is not None and e["at"] <= now),
                         key=lambda e: (e["at"], e["position"]))
            for e in due:
                limit = MISSED_HARD_SECONDS if e.get("mode") == "hard" else MISSED_SOFT_SECONDS
                if now - e["at"] > limit:
                    # A time check minutes late, or a sponsorship from before a long
                    # power cut, is not aired
                    self.mark_done(e["entry_id"])
                    continue
                if self.have(e["url"]) and e.get("kind") != "silence":
                    chosen = e
                    break
            if chosen is None:
                for e in waiting:
                    if e.get("at") is None and self.have(e["url"]):
                        chosen = e
                        break
            if chosen is None:
                # Nothing left that is on this disk: the engine plays its emergency folder
                return 204, b""
            self.mark_done(chosen["entry_id"])
            log.info("offline: next is %s (%s)", chosen["title"], chosen["entry_id"])
            return 200, json.dumps({"uri": self.localize(chosen["uri_main"]), "entry_id": chosen["entry_id"]}).encode()

    def started(self, body: bytes) -> tuple[int, bytes]:
        try:
            report = json.loads(body or b"{}")
        except ValueError:
            report = {}
        self.mark_done(report.get("entry_id"))
        try:
            if self.spool_path.is_file() and self.spool_path.stat().st_size:
                raise Offline("earlier reports still wait; keep the order")
            return self._call("POST", "/started", body, timeout=4.0)
        except Offline:
            if report.get("entry_id"):  # an emergency or silence file is not in the queue
                report.setdefault("at", self.clock())
                report["kept"] = True  # the backend records it at `at`, not on arrival
                with self.lock, open(self.spool_path, "a") as f:
                    f.write(json.dumps(report) + "\n")
            return 200, b'{"ok":true,"relay":"kept"}'

    # ── background work ─────────────────────────────────────────────────────
    def sync_plan(self) -> bool:
        try:
            status, data = self._call("GET", "/plan", timeout=8.0)
        except Offline:
            return False
        if status != 200:
            log.warning("plan: backend answered %s", status)
            return False
        try:
            plan = json.loads(data)
            plan["entries"], plan["blackouts"]
        except (ValueError, KeyError, TypeError):
            return False
        with self.lock:
            self.plan = plan
            self.last_plan_at = self.clock()
            self._write_json("plan.json", plan)
            live = {e["entry_id"] for e in plan["entries"]}
            self.timed_sent &= live
        return True

    def catch_up(self) -> None:
        """Line is back: say hello if the engine restarted meanwhile, then send the kept
        start reports in order."""
        if self.hello_owed:
            try:
                self._call("POST", "/hello", b'{"proto":2}')
                self.hello_owed = False
            except Offline:
                return
        if not self.spool_path.is_file():
            return
        with self.lock:
            lines = [ln for ln in self.spool_path.read_text().splitlines() if ln.strip()]
        sent = 0
        for i, line in enumerate(lines):
            if i < len(lines) - 1:
                # Not the newest: recorded without a now-playing change on the pages
                try:
                    line = json.dumps({**json.loads(line), "more": True})
                except (ValueError, TypeError):
                    pass
            try:
                self._call("POST", "/started", line.encode(), timeout=6.0)
            except Offline:
                break
            sent += 1
        if sent:
            with self.lock:
                now_lines = [ln for ln in self.spool_path.read_text().splitlines() if ln.strip()]
                rest = now_lines[sent:]
                part = self.dir / "spool.jsonl.part"
                part.write_text("".join(ln + "\n" for ln in rest))
                os.replace(part, self.spool_path)
            log.info("sent %d kept start reports", sent)

    def download(self, url: str) -> bool:
        if self.have(url):
            return True
        dest = self.local_path(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        try:
            with urllib.request.urlopen(url, timeout=60) as resp, open(part, "wb") as f:
                while chunk := resp.read(1 << 16):
                    f.write(chunk)
                f.flush()
                os.fsync(f.fileno())  # a power cut must not leave half a song under its name
            if part.stat().st_size == 0:
                part.unlink()
                return False
            os.replace(part, dest)
            return True
        except (OSError, ValueError) as e:
            log.warning("download failed: %s: %s", url, e)
            part.unlink(missing_ok=True)
            return False

    def fetch_plan_audio(self) -> int:
        """Audio for the plan, timed items first (they cannot wait). Returns files still missing."""
        with self.lock:
            entries = list(self.plan.get("entries", []))
        entries.sort(key=lambda e: (e.get("at") is None, e.get("at") or 0, e["position"]))
        missing = 0
        for e in entries:
            if self.stop.is_set():
                break
            if not self.download(e["url"]):
                missing += 1
        return missing

    def mirror_whole_library(self) -> tuple[int, int]:
        """Copy every library file not yet on this disk. Returns (have, missing)."""
        after, have, missing = None, 0, 0
        while not self.stop.is_set():
            q = "?limit=500" + (f"&after={after}" if after else "")
            try:
                status, data = self._call("GET", "/library" + q, timeout=20.0)
            except Offline:
                break
            if status != 200:
                break
            page = json.loads(data)
            for item in page.get("items", []):
                if self.stop.is_set():
                    break
                if self.download(item["url"]):
                    have += 1
                else:
                    missing += 1
            after = page.get("next")
            if not after:
                break
        return have, missing

    def run_commands(self, wait: float = 25.0) -> None:
        """Fetch the backend's commands for the engine (Next, Play Now, flush, timed
        hand-over) over a call this box makes itself, so nothing has to reach in from
        outside. The call is held open until a command is made. Each command runs on
        its own thread and its answer goes back at once in a short call of its own, so
        a /timed that waits for a big file does not hold a flush or a Next behind it."""
        while not self.stop.is_set():
            got = self._poll(wait)
            if got is None:
                self.stop.wait(2.0)
            elif got is False:
                # 403/404: this station's commands do not go by relay; nothing to fetch
                self.stop.wait(15.0)

    def _poll(self, wait: float):
        """One call: hands in the answers that are ready, starts the commands it brings.
        None when the backend cannot be reached, False when it refuses."""
        with self.lock:
            answers, self.answers = self.answers, []
        try:
            status, data = self._call(
                "POST", "/commands/poll",
                json.dumps({"wait": wait, "answers": answers, "health": self.health()}).encode(),
                timeout=wait + 10.0,
            )
        except Offline:
            # The backend gave up on these long ago by the time the line is back
            return None
        if status != 200:
            return False
        try:
            commands = json.loads(data).get("commands", [])
        except (ValueError, AttributeError):
            commands = []
        for cmd in commands:
            threading.Thread(target=self._run_command, args=(cmd,), daemon=True).start()
        return True

    def _run_command(self, cmd: dict) -> None:
        code, body = self._engine_call(
            cmd.get("path", ""), cmd.get("payload") or {}, wait=cmd.get("wait") or 4.0)
        with self.lock:
            self.answers.append({"id": cmd.get("id", ""), "status": code, "body": body})
            self.commands_done += 1
        self._poll(0)

    def _engine_call(self, path: str, payload: dict, wait: float = 4.0) -> tuple[int, dict | None]:
        """`wait`: how long the backend waits for this answer. 202 = the engine took the
        command and is still working on it (a /timed answers once its file is loaded)."""
        if not path.startswith("/") or ".." in path or "?" in path:
            return 400, None
        try:
            wait = min(max(float(wait), 0.5), 30.0)
        except (TypeError, ValueError):
            wait = 4.0
        req = urllib.request.Request(
            self.engine_control + path, data=json.dumps(payload).encode(), method="POST",
            headers={"X-Playout-Token": self.token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=wait) as resp:
                code, raw = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read()
        except TimeoutError:
            # Connected and sent; the engine has not answered yet (connection refused or
            # a failed connect is a URLError, below)
            log.warning("engine %s: no answer after %.0f s (still working)", path, wait)
            return 202, None
        except (OSError, ValueError) as e:
            log.warning("engine %s failed: %s", path, e)
            return 502, None
        try:
            body = json.loads(raw)
        except ValueError:
            body = None
        return code, body if isinstance(body, dict) else None

    def _post_engine(self, path: str, payload: dict) -> bool:
        req = urllib.request.Request(
            self.engine_control + path, data=json.dumps(payload).encode(), method="POST",
            headers={"X-Playout-Token": self.token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=4) as resp:
                return resp.status == 200
        except (OSError, ValueError) as e:
            log.warning("engine %s failed: %s", path, e)
            return False

    def hand_over_timed(self) -> int:
        """Give the engine's timed lane the hard items due within 45 s, a group (same time)
        in play order. The engine ignores an id it already holds, so an item the backend
        handed over just before the line dropped does not air twice. Returns items sent."""
        if self.timed_mode != "always" and self.online:
            return 0
        if not self.clock_ok():
            return 0
        now = self.clock()
        with self.lock:
            done = set(self.done)
            groups: dict[float, list[dict]] = {}
            for e in self.plan.get("entries", []):
                at = e.get("at")
                if e.get("mode") != "hard" or at is None or not e.get("uri_timed"):
                    continue
                if e["entry_id"] in done or e["entry_id"] in self.timed_sent:
                    continue
                if at - now > TIMED_LEAD_SECONDS or now - at > MISSED_HARD_SECONDS:
                    continue
                if at <= now and self.in_blackout(now) and e.get("kind") != "silence":
                    continue  # a missed item does not go on air during Shabbos / Yom Tov
                if not self.have(e["url"]):
                    continue
                groups.setdefault(at, []).append(e)
        sent = 0
        for at in sorted(groups):
            items = sorted(groups[at], key=lambda e: e["position"])
            for seq, e in enumerate(items):
                payload = {"uri": self.localize(e["uri_timed"]), "at": at, "id": e["entry_id"]}
                if len(items) > 1:
                    payload.update(seq=seq, of=len(items))
                if self._post_engine("/timed", payload):
                    with self.lock:
                        self.timed_sent.add(e["entry_id"])
                        self._save_state()
                    sent += 1
                    log.info("timed: handed %s to the engine for %.0f", e["title"], at)
        return sent

    def hard_item_soon(self, within: float = 60.0) -> bool:
        now = self.clock()
        with self.lock:
            return any(
                e.get("mode") == "hard" and e.get("at") is not None and 0 < e["at"] - now <= within
                for e in self.plan.get("entries", [])
            )

    def run_background(self, plan_every: float = 60.0, library_every: float = 6 * 3600.0) -> None:
        def timed_loop():
            while not self.stop.wait(1.0):
                try:
                    # Just before a hard item, make sure we know whether the line is up
                    # (and hold the newest plan): the minute-old answer is not enough
                    if self.hard_item_soon() and self.clock() - self.last_plan_at > 20.0:
                        self.sync_plan()
                    self.hand_over_timed()
                except Exception:
                    log.exception("timed loop")

        def sync_loop():
            last_library = 0.0
            while not self.stop.is_set():
                try:
                    if self.sync_plan():
                        self.catch_up()
                    self.fetch_plan_audio()
                    if self.mirror_library and self.online and time.time() - last_library > library_every:
                        last_library = time.time()
                        have, missing = self.mirror_whole_library()
                        log.info("library copy: %d files here, %d could not be fetched", have, missing)
                except Exception:
                    log.exception("sync loop")
                self.stop.wait(plan_every)

        def command_loop():
            while not self.stop.is_set():
                try:
                    self.run_commands()
                except Exception:
                    log.exception("command loop")
                    self.stop.wait(2.0)

        for target in (timed_loop, sync_loop, command_loop):
            threading.Thread(target=target, daemon=True, name=target.__name__).start()

    def health(self, max_age: float = 30.0) -> dict:
        """How the box is doing, sent to the site with every poll (it raises the alerts):
        clock, plan, audio still to copy, start reports waiting, free disk, engine."""
        hit = self._health
        if hit and time.monotonic() - hit[0] < max_age:
            return hit[1]
        st = self.status()
        try:
            free = round(shutil.disk_usage(self.dir).free / 1e9, 1)
        except OSError:
            free = None
        out = {
            "clock_ok": st["clock_ok"],
            "plan_age_seconds": st["plan_age_seconds"],
            "plan_entries": st["plan_entries"],
            "plan_audio_missing": st["plan_audio_missing"],
            "reports_kept": st["reports_kept"],
            "disk_free_gb": free,
            "engine_ok": self._engine_call("/status", {}, wait=2.0)[0] == 200,
            "up_seconds": int(time.monotonic() - self.started_at),
        }
        self._health = (time.monotonic(), out)
        return out

    def status(self) -> dict:
        with self.lock:
            entries = self.plan.get("entries", [])
            return {
                "online": self.online,
                "plan_age_seconds": round(self.clock() - self.last_plan_at, 1) if self.last_plan_at else None,
                "plan_entries": len(entries),
                "plan_audio_missing": sum(1 for e in entries if not self.have(e["url"])),
                "reports_kept": sum(1 for _ in open(self.spool_path)) if self.spool_path.is_file() else 0,
                "in_blackout": self.in_blackout(self.now()),
                "clock_ok": self.clock_ok(),
                "commands_done": self.commands_done,
            }


def make_handler(relay: Relay):
    prefix = f"/api/v1/playout/{relay.station_id}"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, data: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _route(self, method: str) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if parsed.path == "/relay/status" and method == "GET":
                return self._send(200, json.dumps(relay.status()).encode())
            if self.headers.get("X-Playout-Token") != relay.token or not relay.token:
                return self._send(403, b'{"ok":false,"error":"forbidden"}')
            if not parsed.path.startswith(prefix):
                return self._send(404, b'{"ok":false}')
            if self.headers.get("X-Playout-Instance"):
                relay.engine_instance = self.headers["X-Playout-Instance"]
            tail = parsed.path[len(prefix):]
            if tail == "/next" and method == "GET":
                return self._send(*relay.next(parsed.query))
            if tail == "/started" and method == "POST":
                return self._send(*relay.started(body))
            if tail == "/hello" and method == "POST":
                return self._send(*relay.hello(body))
            return self._send(404, b'{"ok":false}')

        def do_GET(self):  # noqa: N802
            self._route("GET")

        def do_POST(self):  # noqa: N802
            self._route("POST")

        def log_message(self, *_):
            pass

    return Handler


def serve(relay: Relay, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(relay))
    threading.Thread(target=server.serve_forever, daemon=True, name="relay-http").start()
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env = os.environ
    relay = Relay(
        upstream=env["KBR_UPSTREAM_URL"],
        station_id=env["KBR_STATION_ID"],
        token=env["KBR_PLAYOUT_TOKEN"],
        data_dir=env.get("KBR_RELAY_DIR", "/var/lib/kbr-local"),
        engine_control=env.get("KBR_ENGINE_CONTROL", "http://127.0.0.1:" + env.get("KBR_CONTROL_PORT", "8081")),
        timed_mode=env.get("KBR_RELAY_TIMED", "offline"),
        mirror_library=env.get("KBR_RELAY_MIRROR_LIBRARY", "1") not in ("0", "false", ""),
    )
    relay.silence_file()
    serve(relay, int(env.get("KBR_RELAY_PORT", "8090")))
    relay.run_background()
    log.info("relay for station %s listening on 127.0.0.1:%s", relay.station_id, env.get("KBR_RELAY_PORT", "8090"))
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        relay.stop.set()


if __name__ == "__main__":
    main()
