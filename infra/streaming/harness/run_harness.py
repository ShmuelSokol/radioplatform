"""Run the real playout engine (infra/streaming/radio.liq) against a real backend, locally.

Everything that decides what is heard on an engine station runs for real: Liquidsoap 2.2
with the production script, Icecast, the FastAPI backend with its scheduler, and Postgres.
Audio is sine tones (one frequency per item), so the recorded output says exactly which
item was on air when, and where there was silence.

    sudo apt-get install -y liquidsoap icecast2 ffmpeg     # Ubuntu 24.04: Liquidsoap 2.2
    cd backend && uv run python ../infra/streaming/harness/run_harness.py            # all scenarios
    cd backend && uv run python ../infra/streaming/harness/run_harness.py block_shiur_spans_hour

Needs a local Postgres where user radio/radio may create databases (the harness makes
and drops its own database, kbr_harness). Exit code 0 = every scenario passed.
See wiki/engine-harness.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import wave
from array import array
from datetime import UTC, datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
RADIO_LIQ = REPO / "infra" / "streaming" / "radio.liq"

PG_ADMIN = os.environ.get("HARNESS_PG_ADMIN", "postgresql://radio:radio@localhost:5432/postgres")
DB_NAME = "kbr_harness"
DB_URL = f"postgresql+asyncpg://radio:radio@localhost:5432/{DB_NAME}"
BACKEND_PORT = 8100
CONTROL_PORT = 8181          # Liquidsoap harbor (one engine)
PROXY_PORT = 8190            # stands in for nginx: /<station id>/<cmd> -> harbor /<cmd>
AUDIO_PORT = 8191
BACKEND_PROXY_PORT = 8192    # the engine reaches the backend through this, so a scenario can slow it
ICECAST_PORT = 8010
TOKEN = "harness-token"
STATION_ID = uuid.UUID("0e0e0e0e-0000-4000-8000-00000000a001")
ADMIN_EMAIL = "harness-admin"
ADMIN_PASSWORD = "harness-only-password"

# Tone per item: frequency in Hz identifies the item in the recording
TONES = {
    # name: (freq, seconds, asset_type, category)
    "song1": (220, 40, "music", None),
    "song2": (330, 40, "music", None),
    "song3": (440, 40, "music", None),
    "song4": (550, 40, "music", None),
    "song5": (660, 40, "music", None),
    "song6": (770, 40, "music", None),
    "time": (1500, 6, "jingle", "time_announcement"),
    "announce": (1700, 6, "jingle", "block_only"),
    "weather": (2000, 8, "spot", "weather_spot"),
    "shiur": (300, 150, "shiur", "block_only"),
    "story": (350, 150, "story", None),
}
EMERGENCY_FREQ = 5000


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}", flush=True)


# ── Processes ────────────────────────────────────────────────────────────────


class Stack:
    def __init__(self, workdir: Path):
        self.dir = workdir
        self.audio = workdir / "audio"
        self.procs: list[subprocess.Popen] = []
        self.servers: list[ThreadingHTTPServer] = []
        self.wav = workdir / "out.wav"
        self.wav_t0: float | None = None
        # seconds the engine's POST .../started waits before it reaches the backend
        self.slow_started = 0.0

    # audio files
    def make_audio(self) -> None:
        self.audio.mkdir(parents=True, exist_ok=True)
        for name, (freq, secs, _, _) in TONES.items():
            self._tone(self.audio / f"{name}.mp3", freq, secs)
        emerg = self.dir / "emergency"
        emerg.mkdir(exist_ok=True)
        self._tone(emerg / "emergency.mp3", EMERGENCY_FREQ, 20)

    @staticmethod
    def _tone(path: Path, freq: int, secs: int) -> None:
        if path.exists():
            return
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
             f"sine=frequency={freq}:sample_rate=44100:duration={secs}",
             "-ac", "2", "-b:a", "128k", str(path)],
            check=True,
        )

    def serve_audio(self) -> None:
        audio_dir = str(self.audio)

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=audio_dir, **k)

            def log_message(self, *a):
                pass

        self._serve(Handler, AUDIO_PORT)

    def serve_proxy(self) -> None:
        """nginx in production maps /control/<station id>/<cmd> to that engine's harbor."""

        class Proxy(SimpleHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                parts = self.path.strip("/").split("/", 1)
                target = f"http://127.0.0.1:{CONTROL_PORT}/" + (parts[1] if len(parts) > 1 else "")
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                req = urllib.request.Request(target, data=body, method="POST", headers={
                    "X-Playout-Token": self.headers.get("X-Playout-Token", ""),
                    "Content-Type": "application/json",
                })
                try:
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        data, code = resp.read(), resp.status
                except Exception as e:  # engine down
                    data, code = json.dumps({"ok": False, "error": str(e)}).encode(), 502
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self._serve(Proxy, PROXY_PORT)

    def serve_backend_proxy(self) -> None:
        """Between the engine and the backend, like Railway's edge. `slow_started` holds
        back the engine's start reports (Main, 02:57 2026-11-16: a slow start report let
        the engine be handed the same item twice)."""
        stack = self

        class BackendProxy(SimpleHTTPRequestHandler):
            def _forward(self, method: str):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method != "GET" else None
                if method == "POST" and self.path.rstrip("/").endswith("/started") and stack.slow_started:
                    time.sleep(stack.slow_started)
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() in ("x-playout-token", "content-type", "accept")}
                req = urllib.request.Request(f"http://127.0.0.1:{BACKEND_PORT}{self.path}", data=body,
                                             method=method, headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data, code, ctype = resp.read(), resp.status, resp.headers.get("Content-Type", "")
                except urllib.error.HTTPError as e:
                    data, code, ctype = e.read(), e.code, e.headers.get("Content-Type", "")
                except Exception as e:
                    data, code, ctype = str(e).encode(), 502, "text/plain"
                self.send_response(code)
                self.send_header("Content-Type", ctype or "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802
                self._forward("GET")

            def do_POST(self):  # noqa: N802
                self._forward("POST")

            def log_message(self, *a):
                pass

        self._serve(BackendProxy, BACKEND_PROXY_PORT)

    def _serve(self, handler, port: int) -> None:
        srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)

    def fresh_database(self) -> None:
        for sql in (f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)", f"CREATE DATABASE {DB_NAME}"):
            subprocess.run(["psql", PG_ADMIN, "-qc", sql], check=True)

    def backend_env(self) -> dict:
        env = dict(os.environ)
        env.update({
            "DATABASE_URL": DB_URL,
            "APP_ENV": "development",
            "APP_DEBUG": "false",
            "JWT_SECRET_KEY": "harness-jwt-secret",
            "REDIS_URL": "",
            "S3_ENDPOINT_URL": "",
            "SUPABASE_URL": "",
            "ELEVENLABS_API_KEY": "",
            "OPENWEATHERMAP_API_KEY": "",
            "NTFY_TOPIC": "",
            "PLAYOUT_ENGINE_ENABLED": "true",
            "PLAYOUT_ENGINE_STATION_ID": str(STATION_ID),
            "PLAYOUT_ENGINE_URL": f"http://127.0.0.1:{PROXY_PORT}",
            "PLAYOUT_ENGINE_TOKEN": TOKEN,
            "LIQUIDSOAP_ENABLED": "false",
        })
        return env

    def start_backend(self) -> None:
        out = open(self.dir / "backend.log", "w")
        p = subprocess.Popen(
            ["uv", "run", "uvicorn", "app.main:app", "--port", str(BACKEND_PORT)],
            cwd=BACKEND, env=self.backend_env(), stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.procs.append(p)
        wait_for(lambda: http_ok(f"http://127.0.0.1:{BACKEND_PORT}/health"), 90, "backend /health")

    def start_icecast(self) -> None:
        conf = self.dir / "icecast.xml"
        logs = self.dir / "icecast-logs"
        logs.mkdir(exist_ok=True)
        os.chmod(self.dir, 0o777)
        os.chmod(logs, 0o777)
        conf.write_text(f"""<icecast>
  <limits><sources>4</sources></limits>
  <authentication><source-password>{TOKEN}</source-password>
    <admin-user>admin</admin-user><admin-password>{TOKEN}</admin-password></authentication>
  <listen-socket><port>{ICECAST_PORT}</port><bind-address>127.0.0.1</bind-address></listen-socket>
  <paths><basedir>/usr/share/icecast2</basedir><logdir>{logs}</logdir>
    <webroot>/usr/share/icecast2/web</webroot><adminroot>/usr/share/icecast2/admin</adminroot></paths>
  <logging><accesslog>access.log</accesslog><errorlog>error.log</errorlog><loglevel>2</loglevel></logging>
  <security><chroot>0</chroot></security>
</icecast>""")
        os.chmod(conf, 0o644)
        cmd = ["icecast2", "-c", str(conf)]
        if os.geteuid() == 0:  # icecast refuses to run as root
            cmd = ["runuser", "-u", "nobody", "--"] + cmd
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        self.procs.append(p)
        wait_for(lambda: http_ok(f"http://127.0.0.1:{ICECAST_PORT}/status-json.xsl"), 15, "icecast")

    def start_engine(self) -> None:
        env = dict(os.environ)
        env.update({
            "KBR_BACKEND_URL": f"http://127.0.0.1:{BACKEND_PROXY_PORT}/api/v1",
            "KBR_STATION_ID": str(STATION_ID),
            "KBR_PLAYOUT_TOKEN": TOKEN,
            "KBR_CONTROL_PORT": str(CONTROL_PORT),
            "KBR_EMERGENCY_DIR": str(self.dir / "emergency"),
            "KBR_ICECAST_PORT": str(ICECAST_PORT),
            "KBR_ICECAST_SOURCE_PASSWORD": TOKEN,
            "KBR_ICECAST_MOUNT": "/harness",
            "KBR_DEBUG_WAV": str(self.wav),
        })
        out = open(self.dir / "engine.log", "w")
        cmd = ["liquidsoap", str(RADIO_LIQ)]
        if os.geteuid() == 0:  # Liquidsoap refuses to run as root
            cmd = ["runuser", "-u", "nobody", "--"] + cmd
        p = subprocess.Popen(cmd, env=env, stdout=out,
                             stderr=subprocess.STDOUT, start_new_session=True)
        self.procs.append(p)
        wait_for(lambda: self.wav.exists() and self.wav.stat().st_size > 0, 60, "engine output")
        self.wav_t0 = time.time()

    def stop(self) -> None:
        for p in reversed(self.procs):
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except Exception:
                pass
        for p in self.procs:
            try:
                p.wait(timeout=10)
            except Exception:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception:
                    pass
        for s in self.servers:
            s.shutdown()


def http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def wait_for(cond, seconds: float, what: str) -> None:
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return
        time.sleep(0.3)
    raise RuntimeError(f"timed out waiting for {what}")


# ── Recording analysis ───────────────────────────────────────────────────────

WINDOW = 0.1  # seconds


def analyse_wav(path: Path, t0: float, start: float, end: float) -> list[tuple[float, float, float]]:
    """(wall time, dBFS, dominant frequency estimate) per 100 ms window between start and end."""
    out = []
    with wave.open(str(path), "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        if width != 2:
            raise RuntimeError("expected 16-bit WAV")
        n = w.getnframes()
        first = max(0, int((start - t0) * rate))
        last = min(n, int((end - t0) * rate))
        w.setpos(first)
        frames = array("h", w.readframes(last - first))
    step = int(rate * WINDOW)
    for i in range(0, len(frames) // ch - step, step):
        left = frames[i * ch:(i + step) * ch:ch]
        if not left:
            break
        rms = (sum(x * x for x in left) / len(left)) ** 0.5
        db = 20 * __import__("math").log10(rms / 32768) if rms > 0 else -120.0
        crossings = sum(1 for a, b in zip(left, left[1:]) if (a < 0) != (b < 0))
        freq = crossings / 2 / WINDOW
        out.append((t0 + (first / rate) + i / rate, db, freq))
    return out


def longest_silence(windows, threshold_db: float = -45.0) -> tuple[float, float | None]:
    run, best, at, run_from = 0.0, 0.0, None, None
    for t, db, _ in windows:
        if db < threshold_db:
            if run == 0:
                run_from = t
            run += WINDOW
            if run > best:
                best, at = run, run_from
        else:
            run = 0.0
    return best, at


def tone_name(freq: float) -> str:
    best, dist = "?", 1e9
    for name, (f, *_rest) in TONES.items():
        if abs(f - freq) < dist:
            best, dist = name, abs(f - freq)
    if abs(EMERGENCY_FREQ - freq) < dist:
        best, dist = "EMERGENCY", abs(EMERGENCY_FREQ - freq)
    return best if dist < max(25, 0.08 * freq) else "mix"


def timeline(windows) -> list[tuple[float, str]]:
    """Which tone was heard, as (start wall time, name) segments of at least 0.5 s."""
    segs: list[list] = []
    for t, db, f in windows:
        name = "silence" if db < -45 else tone_name(f)
        if segs and segs[-1][1] == name:
            segs[-1][2] = t
        else:
            segs.append([t, name, t])
    return [(s[0], s[1]) for s in segs if s[2] - s[0] >= 0.4]


# ── Backend access ───────────────────────────────────────────────────────────


class Harness:
    def __init__(self, stack: Stack):
        self.stack = stack
        self.assets: dict[str, uuid.UUID] = {}
        self.token = ""
        self.failures: list[str] = []
        self.results: list[tuple[str, bool, str]] = []

    async def setup_data(self) -> None:
        from app.core.security import hash_password
        from app.db.engine import async_session_factory
        from app.models.asset import Asset
        from app.models.station import Station
        from app.models.user import User, UserRole

        async with async_session_factory() as db:
            db.add(Station(id=STATION_ID, name="Harness", timezone="America/New_York",
                           automation_config={}))
            db.add(User(id=uuid.uuid4(), email=ADMIN_EMAIL, hashed_password=hash_password(ADMIN_PASSWORD),
                        role=UserRole.ADMIN, is_active=True))
            for name, (freq, secs, kind, category) in TONES.items():
                aid = uuid.uuid4()
                self.assets[name] = aid
                db.add(Asset(id=aid, title=f"{name} {freq} Hz", artist="Harness", duration=float(secs),
                             file_path=f"http://127.0.0.1:{AUDIO_PORT}/{name}.mp3", asset_type=kind,
                             category=category, review_status="approved",
                             metadata_extra={"audio_analysis": {
                                 "version": 99, "cue_in_seconds": 0.0, "cue_out_seconds": float(secs),
                                 "cross_start_seconds": max(0.0, secs - 3.0)}}))
            await db.commit()

    def api(self, method: str, path: str, body=None) -> tuple[int, dict]:
        url = f"http://127.0.0.1:{BACKEND_PORT}/api/v1{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Content-Type": "application/json", **({"Authorization": f"Bearer {self.token}"} if self.token else {})})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read().decode()[:300]}

    def login(self) -> None:
        code, body = self.api("POST", "/auth/login", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        if code != 200:
            raise RuntimeError(f"login failed {code} {body}")
        self.token = body["access_token"]

    async def set_config(self, **values) -> None:
        from app.db.engine import async_session_factory
        from app.models.station import Station

        async with async_session_factory() as db:
            st = await db.get(Station, STATION_ID)
            cfg = dict(st.automation_config or {})
            for k, v in values.items():
                if v is None:
                    cfg.pop(k, None)
                else:
                    cfg[k] = v
            st.automation_config = cfg
            await db.commit()

    async def rows(self):
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

        from app.db.engine import async_session_factory
        from app.models.queue_entry import QueueEntry

        async with async_session_factory() as db:
            res = await db.execute(select(QueueEntry).options(joinedload(QueueEntry.asset))
                                   .where(QueueEntry.station_id == STATION_ID))
            return list(res.unique().scalars().all())

    async def on_air(self):
        for e in await self.rows():
            if e.status == "playing":
                return e
        return None

    async def clear_pending(self) -> None:
        from sqlalchemy import delete

        from app.db.engine import async_session_factory
        from app.models.queue_entry import QueueEntry

        async with async_session_factory() as db:
            await db.execute(delete(QueueEntry).where(QueueEntry.station_id == STATION_ID,
                                                      QueueEntry.status == "pending"))
            await db.commit()
        # make the engine drop anything it already pre-loaded from the old queue
        urllib.request.urlopen(urllib.request.Request(
            f"http://127.0.0.1:{PROXY_PORT}/{STATION_ID}/flush", data=b"{}", method="POST",
            headers={"X-Playout-Token": TOKEN}), timeout=4).read()

    async def queue(self, items: list[tuple[str, dict]]) -> list[uuid.UUID]:
        """Put items at the head of the queue, in order: (asset name, QueueEntry fields)."""
        from sqlalchemy import func, select, update

        from app.db.engine import async_session_factory
        from app.models.queue_entry import QueueEntry

        ids = []
        async with async_session_factory() as db:
            first = (await db.execute(select(func.min(QueueEntry.position)).where(
                QueueEntry.station_id == STATION_ID, QueueEntry.status == "pending"))).scalar()
            if first is not None:
                await db.execute(update(QueueEntry).where(
                    QueueEntry.station_id == STATION_ID, QueueEntry.status == "pending")
                    .values(position=QueueEntry.position + len(items) + 1))
            base = (await db.execute(select(func.max(QueueEntry.position)).where(
                QueueEntry.station_id == STATION_ID, QueueEntry.status != "pending"))).scalar() or 0
            for i, (name, fields) in enumerate(items):
                eid = uuid.uuid4()
                ids.append(eid)
                db.add(QueueEntry(id=eid, station_id=STATION_ID, asset_id=self.assets[name],
                                  position=base + 1 + i, status="pending",
                                  source=fields.pop("source", "manual"), **fields))
            await db.commit()
        return ids

    async def wait_on_air(self, name: str, seconds: float) -> float | None:
        """Wall time the backend recorded the item as started, or None."""
        end = time.time() + seconds
        aid = self.assets[name]
        while time.time() < end:
            e = await self.on_air()
            if e and e.asset_id == aid and e.started_at:
                return e.started_at.timestamp()
            await asyncio.sleep(0.25)
        return None

    async def started_times(self, name: str, since: float) -> list[tuple[float, str]]:
        aid = self.assets[name]
        out = []
        for e in await self.rows():
            if e.asset_id == aid and e.started_at and e.started_at.timestamp() >= since - 1:
                out.append((e.started_at.timestamp(), e.source))
        return sorted(out)

    def check(self, scenario: str, ok: bool, detail: str) -> None:
        self.results.append((scenario, ok, detail))
        log(("PASS " if ok else "FAIL ") + f"{scenario}: {detail}")

    def heard(self, start: float, end: float):
        time.sleep(1.5)  # let the recording catch up
        return analyse_wav(self.stack.wav, self.stack.wav_t0, start, end)

    def print_timeline(self, windows, t_ref: float) -> None:
        for t, name in timeline(windows):
            log(f"    heard {t - t_ref:+7.1f}s  {name}")


# ── Scenarios ────────────────────────────────────────────────────────────────


def at(seconds: float) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


async def wait_music(h: Harness) -> None:
    """Make sure a song is on air and the queue holds songs."""
    await h.clear_pending()
    await h.queue([("song1", {}), ("song2", {}), ("song3", {}), ("song4", {})])
    end = time.time() + 60
    pressed = 0.0
    while time.time() < end:
        e = await h.on_air()
        if e and e.asset and e.asset.asset_type == "music":
            return
        # a talk or timed item left over from the last scenario: move on to the songs
        if time.time() - pressed > 10:
            h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
            pressed = time.time()
        await asyncio.sleep(0.5)
    raise RuntimeError("no song on air")


async def sc_block_shiur_spans_hour(h: Harness, policy: str = "pause") -> None:
    """Shmuel, 2026-11-16 9 PM: the Daf Yomi block (time + announcement + shiur, hard) played
    the shiur; the next hour's time check aired when the shiur ended, 9 minutes late."""
    name = f"block_shiur_spans_hour[{policy}]"
    await h.set_config(timed_during_long_items=policy)
    await wait_music(h)
    t0 = at(20)
    block = str(uuid.uuid4())
    await h.queue([
        ("time", {"preempt_at": t0, "timing_mode": "hard", "source": "auto"}),
        ("announce", {"preempt_at": t0, "timing_mode": "hard", "source": "block", "block_id": block}),
        ("shiur", {"preempt_at": t0, "timing_mode": "hard", "source": "block", "block_id": block}),
    ])
    shiur_start = await h.wait_on_air("shiur", 60)
    if shiur_start is None:
        h.check(name, False, "the block's shiur never went on air")
        return
    t1 = at(40)  # "the next hour", 40 s into the shiur
    await h.queue([("time", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"}),
                   ("weather", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"})])
    await _expect_hour(h, name, policy, t1, shiur_start, "shiur")


async def sc_music_lane_shiur_spans_hour(h: Harness, policy: str = "pause") -> None:
    """A shiur queued as an ordinary item (not in a block) when the hour comes."""
    name = f"queued_shiur_spans_hour[{policy}]"
    await h.set_config(timed_during_long_items=policy)
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("shiur", {}), ("song5", {}), ("song6", {})])
    e = await h.on_air()
    if e:  # end the song on air so the shiur starts now
        h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=fade")
    shiur_start = await h.wait_on_air("shiur", 60)
    if shiur_start is None:
        h.check(name, False, "the shiur never went on air")
        return
    t1 = at(40)
    await h.queue([("time", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"}),
                   ("weather", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"})])
    await _expect_hour(h, name, policy, t1, shiur_start, "shiur")


async def _expect_hour(h: Harness, name: str, policy: str, t1: datetime, talk_start: float, talk: str) -> None:
    due = t1.timestamp()
    talk_len = TONES[talk][1]
    talk_end = talk_start + talk_len
    limit = (talk_end + 30) if policy == "wait" else (due + 30)
    started = await h.wait_on_air("time", limit - time.time() + 5)
    times = await h.started_times("time", due - 5)
    started = times[0][0] if times else started
    if policy == "wait":
        ok = started is not None and talk_end - 3 <= started <= talk_end + 6
        h.check(name, ok, f"time check waited for the {talk}: due {_fmt(due)}, {talk} ends {_fmt(talk_end)}, "
                          f"time check started {_fmt(started)}")
    else:
        late = None if started is None else started - due
        ok = late is not None and -1.0 <= late <= 2.0
        h.check(name, ok, f"time check on the hour: due {_fmt(due)}, started {_fmt(started)} "
                          f"({'never' if late is None else f'{late:+.1f} s'})")
    # what happens to the talk afterwards
    await asyncio.sleep(25)
    talk_starts = await h.started_times(talk, talk_start - 1)
    resumed = [t for t, src in talk_starts if src.startswith("resume@")]
    if policy == "pause":
        h.check(name, bool(resumed), f"the {talk} carries on after the time check and weather "
                                     f"(starts: {[(_fmt(t), s) for t, s in talk_starts]})")
        if resumed:
            heard = h.heard(due - 3, resumed[0] + 6)
            h.print_timeline(heard, due)
            names = [n for _, n in timeline(heard)]
            ok = "time" in names and "weather" in names and names[-1] == talk
            h.check(name, ok, f"heard {talk} -> time -> weather -> {talk}: {names}")
            gap, gap_at = longest_silence(heard)
            h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s" + (f" at {_fmt(gap_at)}" if gap_at else ""))
    elif policy == "cut":
        h.check(name, not resumed, f"cut: the {talk} does not come back ({len(resumed)} resumes)")


async def sc_song_spans_hour(h: Harness) -> None:
    name = "song_spans_hour"
    await wait_music(h)
    t1 = at(20)
    await h.queue([("time", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"}),
                   ("weather", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(max(0, t1.timestamp() - time.time()) + 20)
    times = await h.started_times("time", t1.timestamp() - 5)
    late = times[0][0] - t1.timestamp() if times else None
    h.check(name, late is not None and -1 <= late <= 2.0,
            f"time check due {_fmt(t1.timestamp())}, started {'never' if late is None else f'{late:+.1f} s'}")
    heard = h.heard(t1.timestamp() - 3, t1.timestamp() + 18)
    h.print_timeline(heard, t1.timestamp())
    gap, gap_at = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")


async def sc_soft_waits_for_song(h: Harness) -> None:
    name = "soft_timed_waits_for_song"
    await wait_music(h)
    cur = await h.on_air()
    t1 = at(5)
    await h.queue([("weather", {"preempt_at": t1, "timing_mode": "soft", "source": "ad_slot"})])
    song_end = cur.started_at.timestamp() + (cur.asset.duration or 40)
    await asyncio.sleep(max(0, song_end - time.time()) + 15)
    times = await h.started_times("weather", t1.timestamp() - 5)
    started = times[0][0] if times else None
    ok = started is not None and song_end - 5 <= started <= song_end + 3
    h.check(name, ok, f"song ends {_fmt(song_end)}, soft item started {_fmt(started)}")
    if started:
        heard = h.heard(started - 6, started + 6)
        gap, _ = longest_silence(heard)
        h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")


async def sc_rapid_next(h: Harness) -> None:
    name = "rapid_next"
    await wait_music(h)
    await h.queue([("song5", {}), ("song6", {}), ("song1", {}), ("song2", {}), ("song3", {}), ("song4", {})])
    await asyncio.sleep(3)
    t_start = time.time()
    codes = []
    for fade in ("hard", "fade", "soft", "fade", "hard", "fade"):
        codes.append(h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade={fade}")[0])
        await asyncio.sleep(1.5)
    await asyncio.sleep(8)
    rows = [e for e in await h.rows() if e.started_at and e.started_at.timestamp() >= t_start - 1]
    playing = [e for e in rows if e.status == "playing"]
    h.check(name, all(c == 200 for c in codes), f"6 Next presses answered {codes}")
    h.check(name, len(playing) == 1, f"exactly one item on air after the presses ({len(playing)})")
    heard = h.heard(t_start - 1, time.time() - 1)
    h.print_timeline(heard, t_start)
    gap, gap_at = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")
    st = await h.on_air()
    tl = timeline(h.heard(time.time() - 4, time.time() - 1))
    h.check(name, bool(st) and tl and tone_name(TONES[[k for k, v in h.assets.items() if v == st.asset_id][0]][0])
            == tl[-1][1], f"what the site says is on air ({st.asset.title if st else None}) is what is heard ({tl[-1][1] if tl else None})")


async def sc_empty_queue(h: Harness) -> None:
    name = "empty_queue"
    await wait_music(h)
    await h.clear_pending()
    cur = await h.on_air()
    song_end = cur.started_at.timestamp() + (cur.asset.duration or 40)
    await asyncio.sleep(max(0, song_end - time.time()) + 20)
    heard = h.heard(song_end - 5, song_end + 18)
    h.print_timeline(heard, song_end)
    gap, gap_at = longest_silence(heard)
    names = [n for _, n in timeline(heard)]
    h.check(name, gap <= 1.0, f"queue ran out: longest silence {gap:.1f} s")
    h.check(name, "EMERGENCY" not in names, f"the engine kept to the station's library, not its emergency files: {names}")
    h.check(name, await h.on_air() is not None, "something is on air on the site")


async def sc_slow_started(h: Harness) -> None:
    """Main, 02:57 2026-11-16: the engine asked for its next track while the start report
    of the item on air was still being written, was handed that same item again, and
    stalled. Here every start report takes 3 s to reach the backend; across two Next
    presses and a natural song change no item may air twice and nothing may stall."""
    name = "slow_started"
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("song5", {}), ("song6", {}), ("song1", {}), ("song2", {}), ("song3", {})])
    h.stack.slow_started = 3.0
    try:
        await asyncio.sleep(2)
        t_start = time.time()
        for _ in range(2):
            h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=fade")
            await asyncio.sleep(8)
        cur = await h.on_air()
        song_end = cur.started_at.timestamp() + (cur.asset.duration or 40) if cur and cur.started_at else time.time() + 40
        await asyncio.sleep(max(0, song_end - time.time()) + 15)
    finally:
        h.stack.slow_started = 0.0
    rows = [e for e in await h.rows() if e.started_at and e.started_at.timestamp() >= t_start - 1]
    by_asset: dict = {}
    for e in rows:
        by_asset.setdefault(e.asset_id, []).append(e)
    twice = [es[0].asset.title for es in by_asset.values() if len(es) > 1]
    h.check(name, not twice, f"no item started twice ({len(rows)} starts; twice: {twice})")
    heard = h.heard(t_start - 1, time.time() - 1)
    h.print_timeline(heard, t_start)
    segs = [n for _, n in timeline(heard)]
    runs = [n for i, n in enumerate(segs) if i == 0 or segs[i - 1] != n]
    repeats = sorted({n for n in runs if runs.count(n) > 1 and n.startswith("song")})
    h.check(name, not repeats, f"heard in order {runs}; heard twice: {repeats}")
    gap, gap_at = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")
    playing = [e for e in await h.rows() if e.status == "playing"]
    h.check(name, len(playing) == 1, f"one item on air at the end ({len(playing)})")


SCENARIOS = {
    "block_shiur_spans_hour": lambda h: sc_block_shiur_spans_hour(h, "pause"),
    "block_shiur_spans_hour_wait": lambda h: sc_block_shiur_spans_hour(h, "wait"),
    "block_shiur_spans_hour_cut": lambda h: sc_block_shiur_spans_hour(h, "cut"),
    "queued_shiur_spans_hour": lambda h: sc_music_lane_shiur_spans_hour(h, "pause"),
    "queued_shiur_spans_hour_wait": lambda h: sc_music_lane_shiur_spans_hour(h, "wait"),
    "queued_shiur_spans_hour_cut": lambda h: sc_music_lane_shiur_spans_hour(h, "cut"),
    "song_spans_hour": sc_song_spans_hour,
    "soft_timed_waits_for_song": sc_soft_waits_for_song,
    "rapid_next": sc_rapid_next,
    "empty_queue": sc_empty_queue,
    "slow_started": sc_slow_started,
}


def _fmt(t: float | None) -> str:
    return "never" if t is None else datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-4]


async def main_async(names: list[str], keep: bool) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="kbr-harness-"))
    log(f"work dir {workdir}")
    stack = Stack(workdir)
    stack.make_audio()
    stack.serve_audio()
    stack.serve_proxy()
    stack.serve_backend_proxy()
    stack.fresh_database()
    os.environ.update(stack.backend_env())
    sys.path.insert(0, str(BACKEND))
    h = Harness(stack)
    try:
        stack.start_backend()
        await h.setup_data()
        h.login()
        stack.start_icecast()
        stack.start_engine()
        log("engine on air")
        for n in names:
            log(f"=== {n}")
            try:
                await SCENARIOS[n](h)
            except Exception as e:
                h.check(n, False, f"scenario crashed: {e!r}")
    finally:
        stack.stop()
        if not keep:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            log(f"logs kept in {workdir}")
    failed = [r for r in h.results if not r[1]]
    log(f"{len(h.results) - len(failed)} passed, {len(failed)} failed")
    for s, _, d in failed:
        log(f"  FAILED {s}: {d}")
    return 1 if failed else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenarios", nargs="*", help=f"default: all of {', '.join(SCENARIOS)}")
    ap.add_argument("--keep", action="store_true", help="keep the work dir (logs, recording)")
    args = ap.parse_args()
    names = args.scenarios or list(SCENARIOS)
    for n in names:
        if n not in SCENARIOS:
            sys.exit(f"unknown scenario {n}")
    sys.exit(asyncio.run(main_async(names, args.keep)))


if __name__ == "__main__":
    main()
