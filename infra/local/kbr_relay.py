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
import random
import re
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

log = logging.getLogger("kbr-relay")

TIMED_LEAD_SECONDS = 45.0  # same lead as the backend's own hand-over
MISSED_HARD_SECONDS = 300.0  # a hard item later than this is dropped, as on the backend
# A soft timed item (sponsorship, weather) later than this is not aired by the box on its
# own: after a power cut of hours they would otherwise all air in a row, hours old
MISSED_SOFT_SECONDS = 3600.0
# The computer's clock is this far behind the newest plan on disk: it was not set after a
# power cut (no internet, flat clock battery). Nothing timed is trusted until it is right.
CLOCK_BEHIND_SECONDS = 120.0
# Live show audio with nothing new from the backend for this long ends the pass-through;
# the engine's live lane goes quiet and opens it again.
LIVE_STALL_SECONDS = 10.0
DONE_KEEP = 8000  # a week offline hands out about 3,500 items
# Kept start reports sent per call on return (POST /started-batch)
SPOOL_BATCH = 200
# Days past the end of the queue the box keeps (GET /plan?days=): its timed items worked
# out ahead, the songs it plays at each hour of the day, a month of Shabbos and Yom Tov
OFFLINE_DAYS = 8.0
EXTENSION_EVERY_SECONDS = 600.0
# A song drawn by the box itself (the queue used up) is not repeated within this many
# hours when the hour's list allows it; the least recently aired goes first
SONG_REST_HOURS = 6.0
AIRED_KEEP = 4000
# A shiur or story paused for a hard item carries on from a little before where it
# stopped, unless it was nearly over (long_items.py on the site)
RESUME_REWIND_SECONDS = 3.0
MIN_REMAINING_TO_RESUME = 30.0
# A station ID is kept this far from any timed item (the site's clear_minutes default)
ID_CLEAR_SECONDS = 180.0
ID_MIN_GAP_SECONDS = 12 * 60.0  # and this far from the last ID (min_minutes default)
URL_AT_END = re.compile(r'(?:^|(?<=":))(https?://\S+)$')
CUE_IN = re.compile(r'liq_cue_in="[^"]*",?')


def with_cue_in(uri: str, seconds: float) -> str:
    """The engine URI starting `seconds` into its file (a paused shiur carrying on)."""
    if not uri.startswith("annotate:"):
        return uri
    body = CUE_IN.sub("", uri[len("annotate:"):])
    return f'annotate:liq_cue_in="{seconds:.3f}",' + body


def cue_in_of(uri: str) -> float:
    m = re.search(r'liq_cue_in="([0-9.]+)"', uri or "")
    return float(m.group(1)) if m else 0.0


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
        offline_days: float = OFFLINE_DAYS,
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
        self.batch_reports = True  # the backend takes kept reports many at a time
        self.last_break_end = 0.0  # when the last break offline ended (station IDs keep clear)
        self.plan: dict = self._read_json("plan.json", {"entries": [], "blackouts": []})
        # The days after the queue (see sync_extension); {} until the first fetch
        self.offline_days = offline_days
        self.extension: dict = self._read_json("extension.json", {})
        self.last_extension_at = 0.0
        state = self._read_json("state.json", {})
        # Entries that were handed to the engine or reported on air: never served again
        self.done: list[str] = list(state.get("done", []))
        self.timed_sent: set[str] = set(state.get("timed_sent", []))
        # entry id -> asset id of what this box handed out, so a kept start report can
        # name its item (the site logs it even if the entry is gone there)
        self.served: dict[str, str] = dict(state.get("served", {}))
        # asset id -> when it last aired here: songs the box draws itself rest
        self.aired: dict[str, float] = dict(state.get("aired", {}))
        # The music lane's item on air (from the start reports) and the one handed out
        # after it, for the pause of a shiur by a hard item while offline
        self.on_air: dict | None = state.get("on_air")
        self.cued_main: str | None = state.get("cued_main")
        # A paused shiur waiting to carry on, first thing in the music lane
        self.resume: dict | None = state.get("resume")
        self.paused_for: list[str] = list(state.get("paused_for", []))
        # Local items: things the box hands out that are not in the site's queue (songs
        # it drew, a shiur's resume), by entry id, for their start reports
        self.local_items: dict[str, dict] = dict(state.get("local_items", {}))
        self.last_id_at: float | None = state.get("last_id_at")
        self.online = True
        # The engine's own run id (X-Playout-Instance), passed on to the backend with every
        # call, so its one-engine-per-station rule also covers a box (a box set up with a
        # copied station id is refused, not given half of that station's queue)
        self.engine_instance: str | None = None
        self.last_plan_at = 0.0
        self.hello_owed = False
        self._catching = threading.Lock()
        self.spool_path = self.dir / "spool.jsonl"
        # Emergency alert reports (engine proto 6) made while the line was down, sent when
        # it is back: the alert itself never waits on us, the log on the site catches up
        self.alert_spool_path = self.dir / "alert-spool.jsonl"
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
        if len(self.served) > DONE_KEEP:
            self.served = dict(list(self.served.items())[-DONE_KEEP:])
        if len(self.aired) > AIRED_KEEP:
            self.aired = dict(sorted(self.aired.items(), key=lambda kv: kv[1])[-AIRED_KEEP:])
        if len(self.local_items) > 200:
            self.local_items = dict(list(self.local_items.items())[-200:])
        self._write_json("state.json", {
            "done": self.done[-DONE_KEEP:], "timed_sent": sorted(self.timed_sent)[-500:],
            "served": self.served, "aired": self.aired, "on_air": self.on_air,
            "cued_main": self.cued_main, "resume": self.resume, "paused_for": self.paused_for[-50:],
            "local_items": self.local_items, "last_id_at": self.last_id_at,
        })

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

    def pass_live(self, handler) -> None:
        """The live show for the engine's live lane (engine proto 5): the backend's
        live.mp3 passed through as it arrives, never held back. 204 while no show is
        heard and while the backend cannot be reached (the lane stays quiet and the
        engine asks again); a refusal (403, 409) goes back to the engine as it came."""
        headers = {"X-Playout-Token": self.token}
        if self.engine_instance:
            headers["X-Playout-Instance"] = self.engine_instance
        req = urllib.request.Request(f"{self.upstream}/playout/{self.station_id}/live.mp3", headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=LIVE_STALL_SECONDS)
        except urllib.error.HTTPError as e:
            return handler._send(e.code if e.code < 500 else 204, b"")
        except (OSError, ValueError) as e:
            log.info("live show audio: backend not reached (%s); lane stays quiet", e)
            return handler._send(204, b"")
        handler.close_connection = True
        try:
            if resp.status != 200:
                return handler._send(204 if resp.status == 204 else resp.status, b"")
            handler.send_response(200)
            handler.send_header("Content-Type", resp.headers.get("Content-Type") or "audio/mpeg")
            handler.send_header("Cache-Control", "no-store")
            handler.end_headers()
            log.info("live show audio: passing through to the engine")
            while True:
                chunk = resp.read1(16384)
                if not chunk:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
        except (OSError, ValueError) as e:  # engine closed it, or the backend stalled
            log.info("live show audio ended: %s", e)
        finally:
            resp.close()

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

    def reports_owed(self) -> bool:
        return self.hello_owed or (self.spool_path.is_file() and self.spool_path.stat().st_size > 0)

    def catch_up_soon(self) -> threading.Thread | None:
        """Send the kept start reports in the background (one catch-up at a time)."""
        if self._catching.locked():
            return None

        def run():
            try:
                self.catch_up()
            except Exception:
                log.exception("catch up")

        t = threading.Thread(target=run, daemon=True, name="catch_up")
        t.start()
        return t

    def next(self, query: str) -> tuple[int, bytes]:
        if self.reports_owed():
            # The line is back but the site does not yet know what aired offline: it would
            # hand out again what the box already played (the 6 PM time check aired again
            # at 6:24 PM, simulator 2026-11-19). Keep playing from the local plan until
            # the kept reports have gone up.
            self.catch_up_soon()
            return self.offline_next()
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

    def blackouts(self) -> list[dict]:
        return list(self.plan.get("blackouts", [])) + list(self.extension.get("blackouts", []))

    def in_blackout(self, now: float) -> bool:
        return any(b["start"] <= now < b["end"] for b in self.blackouts())

    def timed_entries(self) -> list[dict]:
        """Every timed item the box knows: the queue's, then those worked out for the days
        after it (the extension), less any at a minute the queue already has."""
        entries = self.plan.get("entries", [])
        mine = [e for e in entries if e.get("at") is not None]
        minutes = {round(e["at"] / 60) for e in mine}
        extra = []
        for i, e in enumerate(self.extension.get("timed", [])):
            if round(e["at"] / 60) in minutes:
                continue
            extra.append({**e, "position": e.get("position") or (10_000_000 + i)})
        return mine + extra

    def find_item(self, entry_id: str | None) -> dict | None:
        if not entry_id:
            return None
        if entry_id in self.local_items:
            return self.local_items[entry_id]
        for e in self.plan.get("entries", []):
            if e["entry_id"] == entry_id:
                return e
        for e in self.extension.get("timed", []):
            if e["entry_id"] == entry_id:
                return e
        return None

    def _local_hour(self, now: float) -> int:
        tz = self.plan.get("timezone") or "America/New_York"
        try:
            return datetime.fromtimestamp(now, ZoneInfo(tz)).hour if ZoneInfo else time.localtime(now).tm_hour
        except Exception:
            return time.localtime(now).tm_hour

    def _id_must_wait(self, now: float, timed_waiting: list[dict]) -> bool:
        """An automatic station ID is not put next to a break or a spoken item (the
        site's station_id_guard, in short): the item before it must be a song, and no
        timed item may be due within ID_CLEAR_SECONDS of when it would air."""
        cur = self.on_air
        if cur is None or (cur.get("kind") or "") != "music":
            return True
        if now - float(self.last_id_at or 0.0) < ID_MIN_GAP_SECONDS:
            return True
        ends = cur["at"] + float(cur.get("duration") or 0.0) - cue_in_of(cur.get("uri") or "")
        airs = max(now, ends)
        if airs - float(self.last_break_end or 0.0) < ID_CLEAR_SECONDS:
            return True
        # A soft item already due airs right after the song, so it counts as at `airs`
        # and a hard item due before the song ends cuts it: the ID would follow the break
        # (measured from the end of each item: a group runs a minute or more)
        def near(e: dict) -> bool:
            if e.get("mode") != "hard":
                return max(e["at"], airs) - airs < ID_CLEAR_SECONDS
            ends_at = e["at"] + float(e.get("duration") or 0.0)
            return (e["at"] - ID_CLEAR_SECONDS < airs < ends_at + ID_CLEAR_SECONDS + 10.0
                    or now <= e["at"] <= airs)
        return any(near(e) for e in timed_waiting)

    def draw_song(self, now: float) -> dict | None:
        """Past the end of the queue: a song the station plays at this hour of its day,
        the one that has rested longest (not one aired in the last SONG_REST_HOURS when
        the hour's list allows it)."""
        songs = self.extension.get("songs", {})
        if not songs:
            return None
        hour_list = self.extension.get("music", {}).get(str(self._local_hour(now)), [])
        cands = [a for a in hour_list if a in songs and self.have(songs[a]["url"])]
        if not cands:
            cands = [a for a in songs if not songs[a].get("station_id") and self.have(songs[a]["url"])]
        if not cands:
            return None
        rested = [a for a in cands if now - self.aired.get(a, 0.0) >= SONG_REST_HOURS * 3600] or cands
        rng = random.Random()
        pick = min(rested, key=lambda a: (self.aired.get(a, 0.0), rng.random()))
        song = songs[pick]
        eid = str(uuid.uuid4())
        template = self.extension.get("template_entry") or "00000000-0000-0000-0000-000000000000"
        item = {
            "entry_id": eid, "asset_id": pick, "title": song.get("title", ""), "kind": "music",
            "duration": song.get("duration"), "url": song["url"], "long": False,
            "uri_main": song["uri"].replace(template, eid), "local": "song",
        }
        self.local_items[eid] = item
        return item

    def draw_id(self, now: float, timed_waiting: list[dict]) -> dict | None:
        """Past the end of the queue: a station ID after a song, every min..max minutes of
        the station's setting, in its hours, clear of breaks (as the site's fill)."""
        ids = self.extension.get("ids") or {}
        songs = self.extension.get("songs", {})
        if not ids.get("enabled"):
            return None
        start, end, hour = int(ids.get("start_hour", 0)), int(ids.get("end_hour", 24)), self._local_hour(now)
        if not (start == end or (start, end) == (0, 24) or (start <= hour < end if start < end else (hour >= start or hour < end))):
            return None
        last = float(self.last_id_at or 0.0)
        low = float(ids.get("min_minutes") or 12.0) * 60
        high = max(low, float(ids.get("max_minutes") or 20.0) * 60)
        if last and now - last < random.Random(last).uniform(low, high):
            return None
        if self._id_must_wait(now, timed_waiting):
            return None
        cands = [a for a in ids.get("assets", []) if a in songs and self.have(songs[a]["url"])]
        if not cands:
            return None
        pick = min(cands, key=lambda a: (self.aired.get(a, 0.0), random.random()))
        song = songs[pick]
        eid = str(uuid.uuid4())
        template = self.extension.get("template_entry") or "00000000-0000-0000-0000-000000000000"
        item = {
            "entry_id": eid, "asset_id": pick, "title": song.get("title", ""), "kind": "jingle",
            "duration": song.get("duration"), "url": song["url"], "long": False, "station_id": True,
            "uri_main": song["uri"].replace(template, eid), "local": "id",
        }
        self.local_items[eid] = item
        return item

    def _serve(self, item: dict) -> tuple[int, bytes]:
        eid = item["entry_id"]
        self.mark_done(eid)
        with self.lock:
            if item.get("asset_id"):
                self.served[eid] = item["asset_id"]
                self.aired[item["asset_id"]] = self.now()
            self.cued_main = eid
            self._save_state()
        log.info("offline: next is %s (%s)", item.get("title"), eid)
        return 200, json.dumps({"uri": self.localize(item["uri_main"]), "entry_id": eid}).encode()

    def offline_next(self) -> tuple[int, bytes]:
        now = self.now()
        clock_ok = self.clock_ok()
        with self.lock:
            if self.in_blackout(now):
                # The file is a minute long: cut it where the blackout ends, so the station
                # is back on the second (a week offline: back 37 s late after Shabbos)
                left = max((b["end"] for b in self.blackouts() if b["start"] <= now < b["end"]), default=now) - now
                cut = f',liq_cue_out="{max(left, 1.0):.3f}"' if left < 60.0 else ""
                uri = f'annotate:kbr_entry="",kbr_lane="main",kbr_kind="silence",title="Off air"{cut}:{self.silence_file()}'
                return 200, json.dumps({"uri": uri, "entry_id": ""}).encode()
            done = set(self.done)
            if self.resume and self.resume["entry_id"] not in done:
                # A shiur paused for the hard item now in the timed lane carries on next
                item, self.resume = self.resume, None
                return self._serve(item)
            waiting = [
                e for e in self.plan.get("entries", [])
                if e["entry_id"] not in done and e["entry_id"] not in self.timed_sent
            ]
            timed_waiting = [
                e for e in self.timed_entries()
                if e["entry_id"] not in done and e["entry_id"] not in self.timed_sent
            ]
            # For a station ID's distance from a break, the hard items already in the
            # engine's timed lane count too: they air between this song and the ID
            breaks = [e for e in self.timed_entries() if e["entry_id"] not in done]
            chosen = None
            # Timed items whose time has come go first, by time then position (as /next)
            due = sorted((e for e in timed_waiting if clock_ok and e["at"] <= now),
                         key=lambda e: (e["at"], e["position"]))
            deferred = set(self.paused_for)
            for e in due:
                hard = e.get("mode") == "hard" and e["entry_id"] not in deferred
                limit = MISSED_HARD_SECONDS if hard else MISSED_SOFT_SECONDS
                if now - e["at"] > limit:
                    # A time check minutes late, or a sponsorship from before a long
                    # power cut, is not aired
                    self.mark_done(e["entry_id"])
                    continue
                if self.have(e["url"]) and e.get("kind") != "silence":
                    chosen = e
                    break
            if chosen is None:
                held_id = None
                for e in waiting:
                    if e.get("at") is None and self.have(e["url"]):
                        if e.get("station_id"):
                            if held_id is not None:
                                # Two IDs would end up together: the earlier one goes
                                self.mark_done(held_id["entry_id"])
                            if self._id_must_wait(now, breaks):
                                held_id = e
                                continue  # stays in the queue for after the next song
                        chosen = e
                        break
            if chosen is None:
                # The queue is used up (days offline): the box draws the station's songs,
                # with a station ID between them now and then
                chosen = self.draw_id(now, breaks) or self.draw_song(now)
            if chosen is None:
                # Nothing left that is on this disk: the engine plays its emergency folder
                return 204, b""
            return self._serve(chosen)

    def started(self, body: bytes) -> tuple[int, bytes]:
        try:
            report = json.loads(body or b"{}")
        except ValueError:
            report = {}
        self.mark_done(report.get("entry_id"))
        self._note_on_air(report)
        try:
            if self.spool_path.is_file() and self.spool_path.stat().st_size:
                raise Offline("earlier reports still wait; keep the order")
            return self._call("POST", "/started", body, timeout=4.0)
        except Offline:
            if report.get("entry_id"):  # an emergency or silence file is not in the queue
                report.setdefault("at", self.clock())
                report["kept"] = True  # the backend records it at `at`, not on arrival
                # So the site logs it even when the entry is not in its queue (removed
                # there meanwhile, or a song the box drew itself)
                asset = self.served.get(report["entry_id"]) or (self.find_item(report["entry_id"]) or {}).get("asset_id")
                if asset:
                    report["asset_id"] = asset
                with self.lock, open(self.spool_path, "a") as f:
                    f.write(json.dumps(report) + "\n")
            return 200, b'{"ok":true,"relay":"kept"}'

    def _note_on_air(self, report: dict) -> None:
        """What is on air now, for pausing a shiur offline."""
        eid = report.get("entry_id")
        item = self.find_item(eid)
        with self.lock:
            if eid and eid == self.cued_main:
                self.cued_main = None
            if not eid or item is None:
                self.on_air = None
            else:
                self.on_air = {
                    "entry_id": eid, "lane": report.get("lane"), "at": float(report.get("at") or self.clock()),
                    "asset_id": item.get("asset_id"), "title": item.get("title"),
                    "long": bool(item.get("long")), "duration": item.get("duration"),
                    "uri": item.get("uri_main"), "url": item.get("url"), "kind": item.get("kind"),
                }
                if item.get("asset_id"):
                    self.aired[item["asset_id"]] = self.on_air["at"]
                if item.get("station_id"):
                    self.last_id_at = self.on_air["at"]
                elif (item.get("kind") or "") not in ("music", "silence", ""):
                    # a break (time check, weather, sponsorship): an ID keeps clear of its end
                    self.last_break_end = self.on_air["at"] + float(item.get("duration") or 0.0)
            self._save_state()

    def _before_group(self, at: float, group: list[dict]) -> bool:
        """A hard group is about to be handed over while a shiur or story is on air:
        the station's rule (long_items) as the site applies it. pause: it carries on
        after the group, from a little before where it stopped. wait: the group airs
        when it ends (False: not handed over now). cut: nothing to do."""
        cur = self.on_air
        if not cur or not cur.get("long") or not cur.get("uri"):
            return True
        rule = self.plan.get("long_items") or "pause"
        elapsed = at - cur["at"]
        duration = float(cur.get("duration") or 0.0)
        cue = cue_in_of(cur["uri"])
        left = duration - cue - elapsed if duration else 1e9
        if elapsed <= 0 or left <= 1.0:
            return True
        if rule == "wait":
            with self.lock:
                self.paused_for.extend(e["entry_id"] for e in group)
                self._save_state()
            log.info("offline: hard group at %.0f waits for %s to end", at, cur.get("title"))
            return False
        if rule != "pause" or left < MIN_REMAINING_TO_RESUME:
            return True
        key = f'{cur["entry_id"]}@{at:.0f}'
        if key in self.paused_for or (self.resume and self.resume.get("asset_id") == cur.get("asset_id")):
            return True
        offset = cue + max(0.0, elapsed - RESUME_REWIND_SECONDS)
        eid = str(uuid.uuid4())
        item = {
            "entry_id": eid, "asset_id": cur.get("asset_id"), "title": cur.get("title"),
            "kind": cur.get("kind"), "long": True, "url": cur.get("url"),
            "duration": duration, "uri_main": with_cue_in(cur["uri"].replace(cur["entry_id"], eid), offset),
            "local": "resume",
        }
        with self.lock:
            self.local_items[eid] = item
            self.resume = item
            self.paused_for.append(key)
            dropped, self.cued_main = self.cued_main, None
            if dropped and dropped in self.done and dropped not in self.local_items:
                # The song the engine had pre-loaded is played later instead
                self.done.remove(dropped)
            self._save_state()
        log.info("offline: %s pauses at %.0f s for the hard group at %.0f; it carries on after",
                 cur.get("title"), offset, at)
        # The engine drops its pre-loaded song and asks again: it gets the resume
        self._post_engine("/flush", {})
        return True

    def alert(self, body: bytes) -> tuple[int, bytes]:
        """An emergency alert went on air or came off (its own alert line works with no
        internet). Passed on, or kept on disk and sent in order when the line is back."""
        try:
            if self.alert_spool_path.is_file() and self.alert_spool_path.stat().st_size:
                raise Offline("earlier alert reports still wait; keep the order")
            return self._call("POST", "/alert", body, timeout=4.0)
        except Offline:
            try:
                report = json.loads(body or b"{}")
            except ValueError:
                report = {}
            with self.lock, open(self.alert_spool_path, "a") as f:
                f.write(json.dumps(report) + "\n")
                f.flush()
                os.fsync(f.fileno())
            log.warning("alert report kept until the line is back: %s", report.get("phase"))
            return 200, b'{"ok":true,"relay":"kept"}'

    def send_kept_alerts(self) -> int:
        if not self.alert_spool_path.is_file():
            return 0
        with self.lock:
            lines = [ln for ln in self.alert_spool_path.read_text().splitlines() if ln.strip()]
        sent = 0
        for line in lines:
            try:
                self._call("POST", "/alert", line.encode(), timeout=6.0)
            except Offline:
                break
            sent += 1
        if sent:
            with self.lock:
                now_lines = [ln for ln in self.alert_spool_path.read_text().splitlines() if ln.strip()]
                part = self.dir / "alert-spool.jsonl.part"
                part.write_text("".join(ln + "\n" for ln in now_lines[sent:]))
                os.replace(part, self.alert_spool_path)
            log.info("sent %d kept alert reports", sent)
        return sent

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
            live = {e["entry_id"] for e in plan["entries"]} | {e["entry_id"] for e in self.extension.get("timed", [])}
            self.timed_sent &= live
        return True

    def sync_extension(self) -> bool:
        """The days after the queue (GET /plan?days=): fetched every few minutes, as it is
        a week of the station worked out on the site. Also takes the plan that comes with it."""
        try:
            status, data = self._call("GET", f"/plan?days={self.offline_days:g}", timeout=60.0)
        except Offline:
            return False
        if status != 200:
            log.warning("extension: backend answered %s", status)
            return False
        try:
            plan = json.loads(data)
            plan["entries"], plan["blackouts"]
            ext = plan.pop("extension")
            ext["timed"], ext["songs"], ext["music"]
        except (ValueError, KeyError, TypeError):
            return False
        with self.lock:
            self.extension = ext
            self.last_extension_at = self.clock()
            self._write_json("extension.json", ext)
            self.plan = plan
            self.last_plan_at = self.clock()
            self._write_json("plan.json", plan)
            live = {e["entry_id"] for e in plan["entries"]} | {e["entry_id"] for e in ext["timed"]}
            self.timed_sent &= live
        log.info("extension: %d timed items and %d songs for %.0f days past the queue",
                 len(ext["timed"]), len(ext["songs"]), self.offline_days)
        return True

    def extension_due(self) -> bool:
        return self.offline_days > 0 and self.clock() - self.last_extension_at >= EXTENSION_EVERY_SECONDS

    def catch_up(self) -> None:
        """Line is back: say hello if the engine restarted meanwhile, then send the kept
        start reports in order. One at a time: two catch-ups at once (the sync loop and
        an engine ask) sent every kept report twice, so every offline play was logged
        twice (simulator, 2026-11-19)."""
        if not self._catching.acquire(blocking=False):
            return
        try:
            self._catch_up()
        finally:
            self._catching.release()

    def _catch_up(self) -> None:
        if self.hello_owed:
            try:
                self._call("POST", "/hello", b'{"proto":2}')
                self.hello_owed = False
            except Offline:
                return
        self.send_kept_alerts()
        if not self.spool_path.is_file():
            return
        with self.lock:
            lines = [ln for ln in self.spool_path.read_text().splitlines() if ln.strip()]
        sent = 0
        # Many at a time (a week offline is thousands of reports); one by one with an
        # older backend that has no batch route
        while sent < len(lines) and self.batch_reports:
            chunk = lines[sent:sent + SPOOL_BATCH]
            reports = []
            for j, line in enumerate(chunk):
                try:
                    rep = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if sent + j < len(lines) - 1:
                    rep["more"] = True
                reports.append(rep)
            try:
                status, _ = self._call("POST", "/started-batch", json.dumps({"reports": reports}).encode(),
                                       timeout=120.0)
            except Offline:
                break
            if status >= 300:
                # An older backend (404) or a report it refuses: one by one from here
                self.batch_reports = False
                break
            sent += len(chunk)
            self._drop_sent(sent)
        if self.batch_reports or sent >= len(lines):
            if sent:
                log.info("sent %d kept start reports", sent)
            return
        for i, line in enumerate(lines[sent:], start=sent):
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
            self._drop_sent(sent)
            log.info("sent %d kept start reports", sent)

    def _drop_sent(self, sent: int) -> None:
        """The first `sent` lines of the spool went up: keep the rest (and anything the
        engine reported meanwhile, appended at the end)."""
        with self.lock:
            now_lines = [ln for ln in self.spool_path.read_text().splitlines() if ln.strip()]
            rest = now_lines[sent:]
            part = self.dir / "spool.jsonl.part"
            part.write_text("".join(ln + "\n" for ln in rest))
            os.replace(part, self.spool_path)

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
            later = [e["url"] for e in self.extension.get("timed", [])]
            later += [s["url"] for s in self.extension.get("songs", {}).values()]
        entries.sort(key=lambda e: (e.get("at") is None, e.get("at") or 0, e["position"]))
        missing = 0
        for url in [e["url"] for e in entries] + later:
            if self.stop.is_set():
                break
            if not self.download(url):
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
            deferred = set(self.paused_for)
            groups: dict[float, list[dict]] = {}
            for e in self.timed_entries():
                if e["entry_id"] in deferred:
                    continue  # waits for the shiur on air to end (station rule "wait")
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
            if not any(e["entry_id"] in self.timed_sent for e in self.timed_entries() if e.get("at") == at):
                if not self._before_group(at, items):
                    continue
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

    def end_blackout_silence(self) -> bool:
        """Offline, a blackout over while its silence still plays: drop the silence still
        waiting in the timed lane, flush, skip (the site's playout tick does this online;
        a week offline came back 37 s late after Shabbos, at the silence's own end)."""
        if self.online or not self.clock_ok():
            return False
        now = self.clock()
        with self.lock:
            cur = self.on_air
            if cur is None or (cur.get("kind") or "") != "silence" or self.in_blackout(now):
                return False
            if cur.get("ended_silence"):
                return False
            cur["ended_silence"] = True
            waiting = [
                e["entry_id"] for e in self.timed_entries()
                if e.get("kind") == "silence" and e["entry_id"] in self.timed_sent
                and e["entry_id"] not in self.done and e["entry_id"] != cur.get("entry_id")
            ]
            for eid in waiting:
                self.mark_done(eid)
            self._save_state()
        if waiting:
            self._post_engine("/timed_drop", {"ids": waiting})
        self._post_engine("/flush", {})
        self._post_engine("/skip", {})
        log.info("offline: blackout over, silence ended (%d waiting dropped)", len(waiting))
        return True

    def hard_item_soon(self, within: float = 60.0) -> bool:
        now = self.clock()
        with self.lock:
            return any(
                e.get("mode") == "hard" and e.get("at") is not None and 0 < e["at"] - now <= within
                for e in self.timed_entries()
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
                    self.end_blackout_silence()
                except Exception:
                    log.exception("timed loop")

        def sync_loop():
            last_library = 0.0
            while not self.stop.is_set():
                try:
                    if self.extension_due() and self.sync_extension():
                        self.catch_up()
                    elif self.sync_plan():
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
            "offline_days_ahead": st["offline_days_ahead"],
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
                "alert_reports_kept": sum(1 for _ in open(self.alert_spool_path)) if self.alert_spool_path.is_file() else 0,
                "in_blackout": self.in_blackout(self.now()),
                "clock_ok": self.clock_ok(),
                "commands_done": self.commands_done,
                **self.reach(),
            }

    def reach(self) -> dict:
        """How long the box can carry the station from what is on its disk."""
        now = self.clock()
        ext = self.extension
        songs = ext.get("songs", {})
        until = ext.get("until")
        timed = self.timed_entries()
        last_timed = max((e["at"] for e in timed if e.get("at")), default=None)
        return {
            "offline_days_ahead": round(max(0.0, (until - now)) / 86400, 1) if until else 0.0,
            "timed_until": last_timed,
            "songs_for_rotation": len(songs),
            "songs_on_disk": sum(1 for s in songs.values() if self.have(s["url"])),
            "extension_age_seconds": round(now - self.last_extension_at, 1) if self.last_extension_at else None,
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
            if tail == "/alert" and method == "POST":
                return self._send(*relay.alert(body))
            if tail == "/live.mp3" and method == "GET":
                return relay.pass_live(self)
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
        offline_days=float(env.get("KBR_OFFLINE_DAYS", OFFLINE_DAYS)),
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
