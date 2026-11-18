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
import math
import os
import re
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
RELAY_PORT = 8193            # --local-box: the local relay between the engine and the backend proxy
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
    # longer than the 5 minutes a soft item needs to pause a shiur (Shmuel, 2026-11-17)
    "longshiur": (395, 420, "shiur", None),
}
EMERGENCY_FREQ = 5000
# An emergency alert sent through the override (a plain tone: never EAS tones on air)
ALERT_FREQ = 1000
EMERGENCY_TOKEN = "harness-emergency-token"
# The host's show as the backend's live.mp3 serves it (the harness stands in for it)
HOST_FREQ = 1200
# A shiur whose pitch rises steadily (300 Hz at 0 s to 600 Hz at 150 s): the pitch heard
# tells the position played, so a resume can be checked to the second
CHIRP = ("chirp", 300.0, 600.0, 150)


def chirp_position(freq: float) -> float:
    _, f0, f1, secs = CHIRP
    return (freq - f0) / ((f1 - f0) / secs)


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
        self.hls = workdir / "hls"
        self.wav_t0: float | None = None
        # seconds the engine's POST .../started waits before it reaches the backend
        self.slow_started = 0.0
        # Every call from the engine to the backend takes this long (a busy backend)
        self.slow_backend = 0.0
        # true = the backend cannot be reached from the engine (every call answers 502)
        self.backend_down = False
        # true = the backend's commands do not reach the engine (with backend_down: no line at all)
        self.control_down = False
        # the local FM box's relay (infra/local/kbr_relay.py) when run with --local-box
        self.relay = None
        self.local_box = False
        # file name -> seconds the audio server waits before sending it (a slow download)
        self.slow_files: dict[str, float] = {}
        # The host's show is heard: the proxy serves live.mp3 (a 1200 Hz tone in real time)
        # instead of the backend's 204
        self.live_audio = False

    # audio files
    def make_audio(self) -> None:
        self.audio.mkdir(parents=True, exist_ok=True)
        for name, (freq, secs, _, _) in TONES.items():
            self._tone(self.audio / f"{name}.mp3", freq, secs)
        emerg = self.dir / "emergency"
        emerg.mkdir(exist_ok=True)
        self._tone(emerg / "emergency.mp3", EMERGENCY_FREQ, 20)
        self._tone(self.dir / "host.mp3", HOST_FREQ, 300)
        for secs in (8, 14, 20):
            self._tone(self.dir / f"alert{secs}.mp3", ALERT_FREQ, secs)
        # A blackout's silence asset (60 s of nothing)
        silent = self.audio / "silence.mp3"
        if not silent.exists():
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                 "anullsrc=r=44100:cl=stereo", "-t", "60", "-b:a", "128k", str(silent)],
                check=True,
            )
        # A master hotter than anything in the library: full-scale noise and square wave
        loud = self.audio / "loud.mp3"
        if not loud.exists():
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                 "aevalsrc=0.6*sgn(sin(2*PI*900*t))+0.4*(random(0)*2-1):s=44100:d=40",
                 "-ac", "2", "-b:a", "128k", str(loud)],
                check=True,
            )
        name, f0, f1, secs = CHIRP
        path = self.audio / f"{name}.mp3"
        if not path.exists():
            k = (f1 - f0) / secs
            subprocess.run(
                ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                 f"aevalsrc=0.5*sin(2*PI*({f0}*t+{k / 2}*t*t)):s=44100:d={secs}",
                 "-ac", "2", "-b:a", "128k", str(path)],
                check=True,
            )

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
        stack = self

        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=audio_dir, **k)

            def do_GET(self):  # noqa: N802
                wait = stack.slow_files.get(self.path.strip("/").split("?")[0], 0.0)
                if wait:
                    time.sleep(wait)
                super().do_GET()

            def log_message(self, *a):
                pass

        self._serve(Handler, AUDIO_PORT)

    def serve_proxy(self) -> None:
        """nginx in production maps /control/<station id>/<cmd> to that engine's harbor."""
        stack = self

        class Proxy(SimpleHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                parts = self.path.strip("/").split("/", 1)
                target = f"http://127.0.0.1:{CONTROL_PORT}/" + (parts[1] if len(parts) > 1 else "")
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if stack.control_down:
                    target = "http://127.0.0.1:9/"  # nothing listens: as with the line cut
                req = urllib.request.Request(target, data=body, method="POST", headers={
                    "X-Playout-Token": self.headers.get("X-Playout-Token", ""),
                    "Content-Type": "application/json",
                })
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
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
            def _live_mp3(self):
                """Stands in for the backend's live.mp3: 204 while no show is heard, else
                the host tone in real time until the show stops being heard."""
                if not stack.live_audio or not self.headers.get("X-Playout-Token"):
                    self.send_response(204)
                    self.end_headers()
                    return
                data = (stack.dir / "host.mp3").read_bytes()
                rate = 128000 // 8
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                pos, t0, chunk = 0, time.time(), rate // 10
                try:
                    # a second ahead, as a real stream's first buffer
                    self.wfile.write(data[:rate])
                    pos = rate
                    while stack.live_audio and pos < len(data):
                        due = t0 + (pos - rate) / rate
                        if due > time.time():
                            time.sleep(due - time.time())
                        self.wfile.write(data[pos:pos + chunk])
                        self.wfile.flush()
                        pos += chunk
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _forward(self, method: str):
                if method == "GET" and self.path.split("?")[0].endswith("/live.mp3"):
                    return self._live_mp3()
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method != "GET" else None
                if stack.backend_down:
                    data = b"backend down (harness)"
                    self.send_response(502)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if method == "POST" and self.path.rstrip("/").endswith("/started") and stack.slow_started:
                    time.sleep(stack.slow_started)
                if stack.slow_backend:
                    time.sleep(stack.slow_backend)
                headers = {k: v for k, v in self.headers.items()
                           if k.lower() in ("x-playout-token", "x-playout-instance", "content-type", "accept")}
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

    def start_relay(self) -> None:
        """The local FM box: the engine talks to the relay, the relay to the backend."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("kbr_relay", REPO / "infra" / "local" / "kbr_relay.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        data = self.dir / "relay"
        data.mkdir(exist_ok=True)
        self.relay = mod.Relay(
            upstream=f"http://127.0.0.1:{BACKEND_PROXY_PORT}/api/v1", station_id=str(STATION_ID),
            token=TOKEN, data_dir=data, engine_control=f"http://127.0.0.1:{CONTROL_PORT}",
        )
        self.relay.silence_file()
        self.relay_server = mod.serve(self.relay, RELAY_PORT)
        self.servers.append(self.relay_server)
        self.relay.run_background(plan_every=5.0)

    def stop_relay(self) -> None:
        """The relay gone at once, as with the power: nothing is saved on the way out."""
        self.relay.stop.set()
        self.relay_server.shutdown()
        self.relay_server.server_close()
        self.servers.remove(self.relay_server)

    def kill_engine(self) -> None:
        """The engine gone at once (SIGKILL): no fade, no goodbye."""
        p = self.engine
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            pass
        p.wait(timeout=10)
        self.procs.remove(p)
        self.wav.unlink(missing_ok=True)

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
            # A full run takes longer than the default 30 min login: late scenarios got 401
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "600",
            "REDIS_URL": "",
            "S3_ENDPOINT_URL": "",
            "SUPABASE_URL": "",
            "ELEVENLABS_API_KEY": "",
            "OPENWEATHERMAP_API_KEY": "",
            "NTFY_TOPIC": "",
            "PLAYOUT_ENGINE_ENABLED": "true",
            "PLAYOUT_ENGINE_STATION_ID": str(STATION_ID),
            # --local-box: no inbound line to the engine; the backend's commands can only
            # travel through the relay's own outbound call (playout_tunnel)
            "PLAYOUT_ENGINE_URL": "http://127.0.0.1:9" if self.local_box else f"http://127.0.0.1:{PROXY_PORT}",
            "PLAYOUT_RELAY_STATION_ID": str(STATION_ID) if self.local_box else "",
            "PLAYOUT_ENGINE_TOKEN": TOKEN,
            "LIQUIDSOAP_ENABLED": "false",
            "EMERGENCY_API_TOKEN": EMERGENCY_TOKEN,
            # alert audio is served by the backend itself here (no Supabase)
            "BACKEND_PUBLIC_URL": f"http://127.0.0.1:{BACKEND_PORT}",
        })
        return env

    def start_backend(self) -> None:
        out = open(self.dir / "backend.log", "a")
        p = subprocess.Popen(
            ["uv", "run", "uvicorn", "app.main:app", "--port", str(BACKEND_PORT)],
            cwd=BACKEND, env=self.backend_env(), stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.procs.append(p)
        self.backend = p
        wait_for(lambda: http_ok(f"http://127.0.0.1:{BACKEND_PORT}/health"), 90, "backend /health")

    def restart_backend(self) -> None:
        """Stop the backend and start it again (a deploy): everything it kept in memory is lost."""
        p = getattr(self, "backend", None)
        if p is not None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=15)
            except Exception:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception:
                    pass
            self.procs.remove(p)
        self.start_backend()

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
            "KBR_BACKEND_URL": f"http://127.0.0.1:{RELAY_PORT if self.relay else BACKEND_PROXY_PORT}/api/v1",
            "KBR_STATION_ID": str(STATION_ID),
            "KBR_PLAYOUT_TOKEN": TOKEN,
            "KBR_CONTROL_PORT": str(CONTROL_PORT),
            "KBR_EMERGENCY_DIR": str(self.dir / "emergency"),
            "KBR_ICECAST_PORT": str(ICECAST_PORT),
            "KBR_ICECAST_SOURCE_PASSWORD": TOKEN,
            "KBR_ICECAST_MOUNT": "/harness",
            "KBR_DEBUG_WAV": str(self.wav),
            "KBR_HLS_DIR": str(self.hls),
            # A sound card for the local_box_sound_card scenario (see wiki/local-fm-box.md)
            "KBR_ALSA_DEVICE": os.environ.get("KBR_HARNESS_ALSA_DEVICE", ""),
            # The EAS box's audio line (emergency_alert_line; a PulseAudio null sink's monitor)
            "KBR_ALERT_ALSA_DEVICE": os.environ.get("KBR_HARNESS_ALERT_DEVICE", ""),
        })
        self.hls.mkdir(exist_ok=True)
        os.chmod(self.hls, 0o777)  # the engine runs as another user here
        out = open(self.dir / "engine.log", "a")
        cmd = ["liquidsoap", str(RADIO_LIQ)]
        if os.geteuid() == 0:  # Liquidsoap refuses to run as root
            cmd = ["runuser", "-u", "nobody", "--"] + cmd
        p = subprocess.Popen(cmd, env=env, stdout=out,
                             stderr=subprocess.STDOUT, start_new_session=True)
        self.procs.append(p)
        self.engine = p
        wait_for(lambda: self.wav.exists() and self.wav.stat().st_size > 0, 60, "engine output")
        self.wav_t0 = time.time()

    def restart_engine(self) -> None:
        """Kill the engine and start it again (a crash, a server reboot, setup.sh)."""
        p = getattr(self, "engine", None)
        if p is not None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(timeout=10)
            except Exception:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except Exception:
                    pass
            self.procs.remove(p)
        self.wav.unlink(missing_ok=True)
        self.start_engine()

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


def tone_db(path: Path, t0: float, start: float, end: float, freq: float) -> float:
    """Level (dBFS) of one frequency between start and end (Goertzel), whatever else plays."""
    import math
    with wave.open(str(path), "rb") as w:
        rate, ch = w.getframerate(), w.getnchannels()
        first = max(0, int((start - t0) * rate))
        last = min(w.getnframes(), int((end - t0) * rate))
        w.setpos(first)
        frames = array("h", w.readframes(last - first))
    left = frames[::ch]
    if not left:
        return -120.0
    coeff = 2 * math.cos(2 * math.pi * freq / rate)
    s1 = s2 = 0.0
    for x in left:
        s1, s2 = x + coeff * s1 - s2, s1
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    amp = 2 * math.sqrt(max(power, 0.0)) / len(left)  # sine amplitude
    rms = amp / math.sqrt(2)
    return 20 * math.log10(rms / 32768) if rms > 0 else -120.0


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
    if abs(HOST_FREQ - freq) < dist:
        best, dist = "HOST", abs(HOST_FREQ - freq)
    if abs(ALERT_FREQ - freq) < dist:
        best, dist = "ALERT", abs(ALERT_FREQ - freq)
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
            # No broadcast delay: no host audio is sent here, and with a delay the show
            # takes the station over only once listeners hear the host (PR 316). For the same
            # reason no hand-back to music when no host audio arrives (PR 321)
            db.add(Station(id=STATION_ID, name="Harness", timezone="America/New_York",
                           automation_config={"live_delay_seconds": 0, "live_host_gone_seconds": 0}))
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
            cname, _, _, csecs = CHIRP
            aid = uuid.uuid4()
            self.assets[cname] = aid
            db.add(Asset(id=aid, title="chirp shiur", artist="Harness", duration=float(csecs),
                         file_path=f"http://127.0.0.1:{AUDIO_PORT}/{cname}.mp3", asset_type="shiur",
                         review_status="approved",
                         metadata_extra={"audio_analysis": {
                             "version": 99, "cue_in_seconds": 0.0, "cue_out_seconds": float(csecs),
                             "cross_start_seconds": csecs - 3.0}}))
            # The same file with analysed cue points (lead-in and tail cut), as music
            aid = uuid.uuid4()
            self.assets["chirpcue"] = aid
            db.add(Asset(id=aid, title="chirp cued", artist="Harness", duration=float(csecs),
                         file_path=f"http://127.0.0.1:{AUDIO_PORT}/{cname}.mp3", asset_type="music",
                         review_status="approved",
                         metadata_extra={"audio_analysis": {
                             "version": 99, "cue_in_seconds": 10.0, "cue_out_seconds": 40.0,
                             "cross_start_seconds": 37.0}}))
            # song3's file with an analysed level of -6 dB (a loud master turned down)
            aid = uuid.uuid4()
            self.assets["song3quiet"] = aid
            db.add(Asset(id=aid, title="song3 at -6 dB", artist="Harness", duration=40.0,
                         file_path=f"http://127.0.0.1:{AUDIO_PORT}/song3.mp3", asset_type="music",
                         review_status="approved",
                         metadata_extra={"audio_analysis": {
                             "version": 99, "cue_in_seconds": 0.0, "cue_out_seconds": 40.0,
                             "cross_start_seconds": 37.0, "replay_gain_db": -6.0}}))
            # Full-scale file with +6 dB of level on top: the output limiter must hold it
            aid = uuid.uuid4()
            self.assets["loud"] = aid
            db.add(Asset(id=aid, title="loud master", artist="Harness", duration=40.0,
                         file_path=f"http://127.0.0.1:{AUDIO_PORT}/loud.mp3", asset_type="music",
                         review_status="approved",
                         metadata_extra={"audio_analysis": {
                             "version": 99, "cue_in_seconds": 0.0, "cue_out_seconds": 40.0,
                             "cross_start_seconds": 37.0, "replay_gain_db": 6.0}}))
            aid = uuid.uuid4()
            self.assets["silence"] = aid
            db.add(Asset(id=aid, title="Silence", artist="Harness", duration=60.0,
                         file_path=f"http://127.0.0.1:{AUDIO_PORT}/silence.mp3", asset_type="silence",
                         review_status="approved"))
            # song1's file at -6 dB, a jingle for mix-over (its own tone, so it can be told
            # apart from a timed song3quiet still playing)
            aid = uuid.uuid4()
            self.assets["song1quiet"] = aid
            db.add(Asset(id=aid, title="song1 at -6 dB", artist="Harness", duration=40.0,
                         file_path=f"http://127.0.0.1:{AUDIO_PORT}/song1.mp3", asset_type="jingle",
                         review_status="approved",
                         metadata_extra={"audio_analysis": {
                             "version": 99, "cue_in_seconds": 0.0, "cue_out_seconds": 40.0,
                             "cross_start_seconds": 37.0, "replay_gain_db": -6.0}}))
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

    async def drop_leftover_timed(self) -> list[str]:
        """Delete timed entries still pending and say what they were (run between scenarios)."""
        from sqlalchemy import delete

        from app.db.engine import async_session_factory
        from app.models.queue_entry import QueueEntry

        left = [e for e in await self.rows() if e.status == "pending" and e.preempt_at is not None]
        if not left:
            return []
        async with async_session_factory() as db:
            await db.execute(delete(QueueEntry).where(QueueEntry.id.in_([e.id for e in left])))
            await db.commit()
        return [f"{e.asset.title if e.asset else e.asset_id} ({e.timing_mode or 'soft'}, due {e.preempt_at:%H:%M:%S})" for e in left]

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


async def sc_operator_queue_then_next(h: Harness) -> None:
    """Everything through the site, as an operator does it, with the engine already holding
    its next song: Play next then Next plays that item; Play next, delete it, Next plays
    the one after and never the deleted one; three such changes inside 15 s (automatic
    flushes are held back 15 s, an operator's must not be)."""
    name = "operator_queue_then_next"
    await wait_music(h)
    await asyncio.sleep(8)  # the engine holds its next automatic song by now
    t_start = time.time()
    base = f"/stations/{STATION_ID}/queue"

    def play_next(asset: str) -> str:
        code, body = h.api("POST", f"{base}/play-next", {"asset_id": str(h.assets[asset])})
        return body.get("id", "") if code == 201 else ""

    async def next_expect(expect: str, why: str) -> None:
        h.api("POST", f"{base}/skip?fade=hard")
        t = await h.wait_on_air(expect, 6)
        cur = await h.on_air()
        h.check(name, t is not None, f"{why}: {expect} on air (site shows {cur.asset.title if cur else None})")

    h.check(name, bool(play_next("story")), "Play next accepted")
    await asyncio.sleep(1.0)
    await next_expect("story", "Play next, then Next 1 s later")
    await asyncio.sleep(3)
    play_next("shiur")
    deleted = play_next("song6")           # song6 is next, the shiur after it
    await asyncio.sleep(2.5)                # long enough for the engine to load song6
    code, _ = h.api("DELETE", f"{base}/{deleted}")
    h.check(name, code == 204, f"queued item deleted ({code})")
    await asyncio.sleep(0.5)
    await next_expect("shiur", "Play next, delete it, Next")
    await asyncio.sleep(3)
    play_next("song5")
    await asyncio.sleep(0.2)
    await next_expect("song5", "a third change inside 15 s, Next 0.2 s after Play next")
    await asyncio.sleep(5)
    heard = h.heard(t_start, time.time() - 1)
    names = [n for _, n in timeline(heard)]
    h.check(name, tone_name(TONES["song6"][0]) not in names, f"the deleted item was never heard: {sorted(set(names))}")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")
    await asyncio.to_thread(_control, "/skip", {"fade": 0.1})


async def sc_operator_next_slow_file(h: Harness) -> None:
    """Play next of an item whose file takes 5 s to arrive, then Next 0.3 s later. The
    engine has dropped its pre-loaded song for the new item and is still downloading it:
    the item airs next (not the automatic song), with no silence over 1 s, and the song
    on air plays on until the file is there (no emergency music)."""
    name = "operator_next_slow_file"
    await wait_music(h)
    await asyncio.sleep(8)
    base = f"/stations/{STATION_ID}/queue"
    h.stack.slow_files["story.mp3"] = 5.0
    t0 = time.time()
    try:
        code, _ = h.api("POST", f"{base}/play-next", {"asset_id": str(h.assets["story"])})
        h.check(name, code == 201, "Play next accepted")
        await asyncio.sleep(0.3)
        pressed = time.time()
        h.api("POST", f"{base}/skip?fade=hard")
        t = await h.wait_on_air("story", 20)
    finally:
        h.stack.slow_files.pop("story.mp3", None)
    h.check(name, t is not None, f"the slow item went on air after Next ({'never' if t is None else f'{t - pressed:.1f} s after the press'})")
    await asyncio.sleep(4)
    heard = h.heard(t0 - 2, time.time() - 1)
    h.print_timeline(heard, pressed)
    names = [n for _, n in timeline(heard)]
    tl = timeline(heard)
    on_air_before = next((n for ts, n in reversed(tl) if ts <= pressed), None)
    first_new = next((n for ts, n in tl
                      if ts > pressed and n not in ("EMERGENCY", "silence", on_air_before)), None)
    h.check(name, first_new == tone_name(TONES["story"][0]),
            f"after the press the next library item heard is the queued one: {names}")
    # The song on air plays on until the file is here (the engine's Next waits for it)
    h.check(name, "EMERGENCY" not in names, f"no emergency music while the file loads: {names}")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")
    await asyncio.to_thread(_control, "/skip", {"fade": 0.1})


async def sc_operator_next_overtakes_commit(h: Harness) -> None:
    """KBR Jerusalem 2026-11-18 03:23Z: Play next and Next sent 0.1 s apart. On production
    a commit takes 50 to 150 ms, so the Next ran before Play next was committed, saw the
    old queue head and played the automatic song the engine held. Here a database trigger
    holds every manual queue insert for 250 ms inside its transaction, and Next is sent
    0.1 s after Play next: the queued item must be what airs."""
    name = "operator_next_overtakes_commit"
    from sqlalchemy import text

    from app.db.engine import async_session_factory

    await wait_music(h)
    await asyncio.sleep(8)
    base = f"/stations/{STATION_ID}/queue"
    async with async_session_factory() as db:
        await db.execute(text(
            "CREATE OR REPLACE FUNCTION kbr_slow_insert() RETURNS trigger AS $$ "
            "BEGIN PERFORM pg_sleep(0.25); RETURN NEW; END $$ LANGUAGE plpgsql"))
        await db.execute(text("DROP TRIGGER IF EXISTS kbr_slow_manual ON queue_entries"))
        await db.execute(text(
            "CREATE TRIGGER kbr_slow_manual BEFORE INSERT ON queue_entries FOR EACH ROW "
            "WHEN (NEW.source = 'manual') EXECUTE FUNCTION kbr_slow_insert()"))
        await db.commit()
    t_start = time.time()
    try:
        for asset in ("story", "shiur", "song6"):
            body = {"asset_id": str(h.assets[asset])}
            queued = asyncio.create_task(asyncio.to_thread(h.api, "POST", f"{base}/play-next", body))
            await asyncio.sleep(0.1)
            pressed = asyncio.create_task(asyncio.to_thread(h.api, "POST", f"{base}/skip?fade=hard"))
            (code, _), (skip_code, _) = await queued, await pressed
            t = await h.wait_on_air(asset, 6)
            cur = await h.on_air()
            h.check(name, code == 201 and skip_code == 200 and t is not None,
                    f"Play next ({code}) and Next ({skip_code}) 0.1 s apart, commit held 250 ms: "
                    f"{asset} on air (site shows {cur.asset.title if cur else None})")
            await asyncio.sleep(6)
    finally:
        async with async_session_factory() as db:
            await db.execute(text("DROP TRIGGER IF EXISTS kbr_slow_manual ON queue_entries"))
            await db.commit()
    heard = h.heard(t_start, time.time() - 1)
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")
    await asyncio.to_thread(_control, "/skip", {"fade": 0.1})


async def sc_two_sources_same_second(h: Harness) -> None:
    """V3-5: hard items from two sources due on the same second (the hourly time check and
    weather, and a program block's announcement). Each airs once, whole, one after the
    other, none dropped, and two of them are never heard at once."""
    name = "two_sources_same_second"
    await wait_music(h)
    t1 = at(22)
    block = str(uuid.uuid4())
    await h.queue([("announce", {"preempt_at": t1, "timing_mode": "hard", "source": "block", "block_id": block})])
    await h.queue([(n, {"preempt_at": t1, "timing_mode": "hard", "source": "auto"}) for n in ("time", "weather")])
    await asyncio.sleep(max(0, t1.timestamp() - time.time()) + 6 + 8 + 6 + 12)
    items = ("time", "weather", "announce")
    # A song that ends 1 to 8 s before a hard item lets it start early instead of being
    # chopped (PR 232), so the group may begin up to 8 s before its second
    starts = {n: await h.started_times(n, t1.timestamp() - 10) for n in items}
    h.check(name, all(len(starts[n]) == 1 for n in items),
            f"each aired once: { {n: len(starts[n]) for n in items} }")
    first = min((starts[n][0][0] for n in items if starts[n]), default=None)
    late = None if first is None else first - t1.timestamp()
    h.check(name, late is not None and -8.5 <= late <= 2.0,
            f"the first of them {'never' if late is None else f'{late:+.1f} s'} from the due second")
    heard = h.heard(t1.timestamp() - 10, t1.timestamp() + 32)
    h.print_timeline(heard, t1.timestamp())
    names = [n for _, n in timeline(heard)]
    runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    for n in items:
        h.check(name, runs.count(tone_name(TONES[n][0])) == 1, f"{n} heard once, in one piece: {runs}")
    pairs = [("time", "weather"), ("time", "announce"), ("weather", "announce")]
    over = {f"{x}+{y}": both_audible(h, t1.timestamp() - 1, t1.timestamp() + 30, TONES[x][0], TONES[y][0])
            for x, y in pairs}
    h.check(name, all(v <= 0.25 for v in over.values()), f"no two of them at once (seconds together: {over})")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")
    cur = await h.on_air()
    h.check(name, bool(cur) and cur.asset.asset_type == "music", f"music back on air ({cur.asset.title if cur else None})")


async def sc_hard_group_overruns_next_hard(h: Harness) -> None:
    """V3-6: a hard group is still playing when the next hard item is due (time + weather
    + announcement, 20 s, at T; another announcement and weather at T+10). Nothing is
    lost, nothing airs twice, no two are heard at once, and the later group airs as soon
    as the first is done."""
    name = "hard_group_overruns_next_hard"
    await wait_music(h)
    t1 = at(22)
    t2 = t1 + timedelta(seconds=10)
    first_ids = await h.queue([(n, {"preempt_at": t1, "timing_mode": "hard", "source": "auto"})
                               for n in ("time", "weather", "announce")])
    block = str(uuid.uuid4())
    second_ids = await h.queue([("time", {"preempt_at": t2, "timing_mode": "hard", "source": "block", "block_id": block})])
    await asyncio.sleep(max(0, t2.timestamp() - time.time()) + 45)
    rows = {e.id: e for e in await h.rows()}
    got = [(rows[i].status, rows[i].started_at.timestamp() - t1.timestamp() if rows[i].started_at else None)
           for i in first_ids + second_ids if i in rows]
    h.check(name, all(st in ("played", "playing") and at_ is not None for st, at_ in got),
            f"all four aired (status, seconds after T): {[(st, None if a is None else round(a, 1)) for st, a in got]}")
    second = rows.get(second_ids[0])
    when = second.started_at.timestamp() - t1.timestamp() if second and second.started_at else None
    h.check(name, when is not None and 9.0 <= when <= 24.0,
            f"the later item aired {'never' if when is None else f'{when:.1f} s'} after T (due at +10, the first group lasts 20 s)")
    heard = h.heard(t1.timestamp() - 3, t1.timestamp() + 50)
    h.print_timeline(heard, t1.timestamp())
    names = [n for _, n in timeline(heard)]
    runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    h.check(name, runs.count(tone_name(TONES["time"][0])) == 2, f"the time tone heard twice (once per group): {runs}")
    over = {f"{x}+{y}": both_audible(h, t1.timestamp() - 1, t1.timestamp() + 45, TONES[x][0], TONES[y][0])
            for x, y in (("time", "weather"), ("time", "announce"), ("weather", "announce"))}
    h.check(name, all(v <= 0.25 for v in over.values()), f"no two of them at once (seconds together: {over})")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")


async def sc_engine_starts_every_time(h: Harness, times: int = 6) -> None:
    """V3-22, V3-23: the engine is started several times in a row. Each time /status
    answers within 20 s of the restart being asked for (stop, start, also before the first
    track), sound is on air within 30 s and it is the station's music, not the emergency files."""
    name = "engine_starts_every_time"
    await wait_music(h)
    status_s, sound_s, bad = [], [], []
    for i in range(times):
        await h.clear_pending()
        await h.queue([("song1", {}), ("song2", {}), ("song3", {})])
        asked = time.time()   # times below count from the moment the restart was asked for
        await asyncio.to_thread(h.stack.restart_engine)
        t0 = h.stack.wav_t0
        answered = None
        while time.time() - asked < 30:
            try:
                if await asyncio.to_thread(_control, "/status") == 200:
                    answered = time.time() - asked
                    break
            except OSError:
                pass  # the control port is not open yet
            await asyncio.sleep(0.2)
        status_s.append(answered)
        await asyncio.sleep(max(0, t0 + 16 - time.time()))
        heard = h.heard(t0, time.time() - 1)
        first = next((t for t, db, _ in heard if db > -45), None)
        sound_s.append(None if first is None else first - asked)
        names = {n for _, n in timeline(h.heard(t0 + 8, time.time() - 1))}
        if not names or not all(n.startswith("song") for n in names):
            bad.append((i + 1, sorted(names)))
    h.check(name, all(a is not None and a <= 20.0 for a in status_s),
            f"/status answered after each of {times} starts (s): {[None if a is None else round(a, 1) for a in status_s]}")
    h.check(name, all(x is not None and x <= 30.0 for x in sound_s),
            f"sound after each start (s): {[None if x is None else round(x, 1) for x in sound_s]}")
    h.check(name, not bad, f"station music 8 s after every start, no emergency files: {bad or 'all'}")


async def sc_live_show_over_hard_item(h: Harness, hard_stop: bool = False, restart: bool = False) -> None:
    """V3-52: a live show starts while a hard time check is on air, and a second time
    check comes due during the show. The stream goes quiet for the host, the time check
    that came due during the show is not aired late after it, and music is back after.
    `hard_stop` (V3-52b): nobody presses End; the scheduler ends the show at its
    planned end time. `restart` (V3-52c): the backend restarts (a deploy) 6 s into the
    show, before the second item is due, so what it kept in memory about the show and
    about what it handed to the engine is lost."""
    name = "live_show_over_hard_item" + ("_hard_stop" if hard_stop else "") + ("_restart" if restart else "")
    # No host microphone: run with no broadcast delay (PR 316), see sc_live_show_quiet
    before = set_live_settings(h, live_delay_seconds=0, live_host_gone_seconds=0)   # no microphone here (PR 321 would hand back)
    try:
        await _live_show_over_hard_item(h, name, hard_stop, restart)
    finally:
        h.api("PUT", f"/stations/{STATION_ID}", {"automation_config": before})


async def _live_show_over_hard_item(h: Harness, name: str, hard_stop: bool, restart: bool) -> None:
    await wait_music(h)
    t1 = at(20)
    t2 = t1 + timedelta(seconds=45 if restart else 20)
    await h.queue([("time", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"}),
                   ("weather", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"})])
    second = await h.queue([("announce", {"preempt_at": t2, "timing_mode": "hard", "source": "auto"})])
    body = {"station_id": str(STATION_ID), "title": "Harness show 2", "calls_enabled": False}
    planned_end = t2 + timedelta(seconds=12)
    if hard_stop:
        body["scheduled_start"] = (t1 - timedelta(seconds=30)).isoformat()
        body["scheduled_end"] = planned_end.isoformat()
    code, show = h.api("POST", "/live-shows", body)
    if code != 201:
        h.check(name, False, f"could not create the show ({code})")
        return
    await asyncio.sleep(max(0, t1.timestamp() + 3 - time.time()))
    t_start = time.time()
    code, _ = h.api("POST", f"/live-shows/{show['id']}/start")
    h.check(name, code == 200, f"show started 3 s into the time check ({code})")
    if restart:
        await asyncio.sleep(3)
        r0 = time.time()
        await asyncio.to_thread(h.stack.restart_backend)
        h.check(name, time.time() < t2.timestamp() - 3,
                f"the backend was back {time.time() - r0:.0f} s later, before the second item was due")
        _, cur_show = h.api("GET", f"/live-shows/{show['id']}")
        h.check(name, (cur_show or {}).get("status") == "live", f"the show is still live after the restart ({(cur_show or {}).get('status')})")
    if hard_stop:
        t_end = None
        deadline = planned_end.timestamp() + 15
        while time.time() < deadline:
            _, cur_show = h.api("GET", f"/live-shows/{show['id']}")
            if (cur_show or {}).get("status") not in ("live", "LIVE"):
                t_end = time.time()
                break
            await asyncio.sleep(0.5)
        late = None if t_end is None else t_end - planned_end.timestamp()
        h.check(name, late is not None and -1.0 <= late <= 5.0,
                f"the scheduler ended the show at its planned end ({'never' if late is None else f'{late:+.1f} s'})")
        if t_end is None:
            h.api("POST", f"/live-shows/{show['id']}/end")
            t_end = time.time()
        t_end -= 0.5   # seen up to one poll late
    else:
        await asyncio.sleep(max(0, t2.timestamp() + 12 - time.time()))
        t_end = time.time()
        code, _ = h.api("POST", f"/live-shows/{show['id']}/end")
        h.check(name, code == 200, f"show ended 12 s after the second item was due ({code})")
    # long enough for the song that comes back to end, so a stale item would get its turn
    await asyncio.sleep(18 + 45)
    heard = h.heard(t1.timestamp() - 3, time.time() - 1)
    h.print_timeline(heard, t1.timestamp())
    during = [w for w in heard if t_start + 2.0 <= w[0] <= t_end - 0.3]
    loud = sum(WINDOW for w in during if w[1] >= -45)
    what = [f"{w[0] - t_start:+.1f}s {tone_name(w[2])} {w[1]:.0f}dB" for w in during if w[1] >= -45][::4][:10]
    h.check(name, bool(during) and loud <= 0.2,
            f"the stream is quiet while the host is on air (sound for {loud:.1f} s of {len(during) * WINDOW:.0f} s) {what}")
    after = [n for t, n in timeline([w for w in heard if w[0] >= t_end]) if n != "silence"]
    late_tones = {tone_name(TONES[n][0]) for n in ("time", "weather", "announce")}
    h.check(name, not (set(after) & late_tones),
            f"no time check, weather or announcement aired late after the show: {after}")
    h.check(name, bool(after) and "EMERGENCY" not in after, f"station music after the show: {after}")
    rows = {e.id: e for e in await h.rows()}
    st = rows[second[0]].status if second[0] in rows else "gone"
    h.check(name, st != "pending" and st != "playing",
            f"the item that came due during the show is closed, not left waiting in the queue ({st})")
    cur = await h.on_air()
    h.check(name, bool(cur) and cur.asset.asset_type == "music", f"the site shows music ({cur.asset.title if cur else None})")


async def sc_next_while_soft_spot_waits(h: Harness, slow: float = 0.0) -> None:
    """Main 03:46Z 2026-11-18: a soft sponsorship was the queue's next item (waiting for
    the song to end) and the engine held the following song pre-loaded. Next dropped the
    spot, marked it played without airing it and put a second of emergency audio on air.
    Now Next plays the spot: it airs once, whole, is not closed before it airs, no
    emergency audio, and the song the engine held plays after it. `slow`: the spot's
    file takes this long to arrive."""
    name = "next_while_soft_spot_waits" + (f"_slow_{slow:g}s" if slow else "")
    await wait_music(h)
    await asyncio.sleep(8)   # the engine holds its next song
    t1 = at(2)
    if slow:
        h.stack.slow_files["weather.mp3"] = slow
    try:
        spot = (await h.queue([("weather", {"preempt_at": t1, "timing_mode": "soft", "source": "ad_slot"})]))[0]
        await asyncio.sleep(5)   # due and waiting for the song to end
        before = next((e for e in await h.rows() if e.id == spot), None)
        h.check(name, before is not None and before.status == "pending",
                f"the spot waits for the song (status {before.status if before else None})")
        pressed = time.time()
        code, _ = h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=fade")
        h.check(name, code == 200, f"Next answered {code} after {time.time() - pressed:.1f} s")
        t = await h.wait_on_air("weather", 8 + slow)
    finally:
        h.stack.slow_files.pop("weather.mp3", None)
    h.check(name, t is not None and t - pressed <= 3.0 + slow,
            f"the spot went on air {'never' if t is None else f'{t - pressed:.1f} s'} after Next")
    await asyncio.sleep(TONES["weather"][1] + 8)
    times = await h.started_times("weather", pressed - 1)
    h.check(name, len(times) == 1, f"the spot aired once ({len(times)})")
    heard = h.heard(pressed - 4, time.time() - 1)
    h.print_timeline(heard, pressed)
    tl = [(ts, n) for ts, n in timeline(heard)]
    names = [n for _, n in tl]
    wname = tone_name(TONES["weather"][0])
    h.check(name, "EMERGENCY" not in names, f"no emergency audio: {names}")
    spans = [w[0] for w in heard if w[0] > pressed and w[1] > -45 and tone_name(w[2]) == wname]
    whole = (spans[-1] - spans[0]) if spans else 0.0
    h.check(name, whole >= TONES["weather"][1] - 1.5, f"the spot was heard whole ({whole:.1f} s of {TONES['weather'][1]} s)")
    after = [n for ts, n in tl if spans and ts > spans[-1] - 0.5 and n not in (wname, "silence")]
    h.check(name, bool(after) and after[0].startswith("song"), f"a song follows the spot: {after}")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0 + slow, f"longest silence {gap:.1f} s")
    row = next((e for e in await h.rows() if e.id == spot), None)
    h.check(name, row is not None and row.status == "played", f"the spot is marked played after it aired ({row.status if row else None})")
    cur = await h.on_air()
    h.check(name, bool(cur) and cur.asset.asset_type == "music", f"the site shows a song ({cur.asset.title if cur else None})")


async def sc_resume_without_old_song(h: Harness) -> None:
    """KBR Jerusalem 2026-11-18 01:00Z: Main's Daf Yomi block cut a song at 00:45; at
    01:00 the shiur paused for the time check, and before it resumed 2.8 s of the song cut
    15 minutes earlier were heard. Here a long item (the story, 150 s) is on air when the
    block's shiur takes over; 40 s later the time check pauses the shiur. Nothing of the
    story may be heard between the block's start and the shiur's resume."""
    name = "resume_without_old_song"
    await h.set_config(timed_during_long_items="pause")
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("story", {}), ("song5", {}), ("song6", {})])
    await asyncio.sleep(16)  # let the engine drop the song it held before the queue changed
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
    if await h.wait_on_air("story", 60) is None:
        h.check(name, False, "the story never went on air")
        return
    t0 = at(20)
    block = str(uuid.uuid4())
    await h.queue([
        ("announce", {"preempt_at": t0, "timing_mode": "hard", "source": "block", "block_id": block}),
        ("shiur", {"preempt_at": t0, "timing_mode": "hard", "source": "block", "block_id": block}),
    ])
    shiur_start = await h.wait_on_air("shiur", 60)
    if shiur_start is None:
        h.check(name, False, "the block's shiur never went on air")
        return
    t1 = at(40)
    await h.queue([("time", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"}),
                   ("weather", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(max(0, t1.timestamp() - time.time()) + 30)
    times = await h.started_times("time", t1.timestamp() - 5)
    late = times[0][0] - t1.timestamp() if times else None
    h.check(name, late is not None and abs(late) <= 1.0,
            f"time check {'never' if late is None else f'{late:+.1f} s'}")
    windows = h.heard(shiur_start + 2, t1.timestamp() + 28)
    h.print_timeline(windows, t1.timestamp())
    story = tone_name(TONES["story"][0])
    old = sum(1 for _, db, f in windows if db > -45 and tone_name(f) == story)
    step = (windows[1][0] - windows[0][0]) if len(windows) > 1 else 0.0
    at_ = [round(t - t1.timestamp(), 1) for t, db, f in windows if db > -45 and tone_name(f) == story]
    h.check(name, old == 0, f"nothing of the interrupted story after the block took over "
                            f"(heard for about {old * step:.1f} s, at {at_[:8]} s from the time check)")
    names = [n for _, n in timeline(h.heard(t1.timestamp() - 3, t1.timestamp() + 28))]
    runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    h.check(name, runs[:4] == ["shiur", "time", "weather", "shiur"],
            f"heard shiur -> time -> weather -> shiur: {runs}")
    gap, _ = longest_silence(windows)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")
    await asyncio.to_thread(_control, "/skip", {"fade": 0.1})


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


async def _start_talk(h: Harness, name: str, talk: str) -> float | None:
    await wait_music(h)
    await h.clear_pending()
    await h.queue([(talk, {}), ("song5", {}), ("song6", {})])
    if await h.on_air():  # end the song on air so the talk starts now
        h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=fade")
    start = await h.wait_on_air(talk, 60)
    if start is None:
        h.check(name, False, f"the {talk} never went on air")
    return start


async def sc_soft_pauses_long_shiur(h: Harness) -> None:
    """Shmuel, 2026-11-17: Jerusalem's 7:30 weather aired at 7:49 behind a shiur. A soft
    item due while the shiur has more than 5 minutes left pauses it; it then carries on."""
    name = "soft_pauses_long_shiur"
    await h.set_config(timed_during_long_items="pause")
    start = await _start_talk(h, name, "longshiur")
    if start is None:
        return
    t1 = at(40)  # 420 - ~45 s in: about 6 minutes left
    await h.queue([("weather", {"preempt_at": t1, "timing_mode": "soft", "source": "block"})])
    due = t1.timestamp()
    await asyncio.sleep(max(0, due - time.time()) + 30)
    times = await h.started_times("weather", due - 5)
    started = times[0][0] if times else None
    late = None if started is None else started - due
    h.check(name, late is not None and -1.0 <= late <= 2.0,
            f"weather due {_fmt(due)}, started {_fmt(started)} ({'never' if late is None else f'{late:+.1f} s'})")
    talk_starts = await h.started_times("longshiur", start - 1)
    resumed = [t for t, src in talk_starts if src.startswith("resume@")]
    h.check(name, bool(resumed), f"the shiur carries on after the weather (starts: {[(_fmt(t), s) for t, s in talk_starts]})")
    if resumed:
        heard = h.heard(due - 3, resumed[0] + 6)
        h.print_timeline(heard, due)
        names = [n for _, n in timeline(heard)]
        h.check(name, names[:1] == ["longshiur"] and "weather" in names and names[-1] == "longshiur",
                f"heard shiur -> weather -> shiur: {names}")
        gap, gap_at = longest_silence(heard)
        h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s" + (f" at {_fmt(gap_at)}" if gap_at else ""))


async def sc_soft_waits_for_short_shiur(h: Harness) -> None:
    """Under 5 minutes left (the 150 s shiur), the soft item waits for the shiur's end."""
    name = "soft_waits_for_short_shiur"
    await h.set_config(timed_during_long_items="pause")
    start = await _start_talk(h, name, "shiur")
    if start is None:
        return
    t1 = at(15)
    await h.queue([("weather", {"preempt_at": t1, "timing_mode": "soft", "source": "block"})])
    talk_end = start + TONES["shiur"][1]
    await asyncio.sleep(max(0, talk_end - time.time()) + 20)
    times = await h.started_times("weather", t1.timestamp() - 5)
    started = times[0][0] if times else None
    h.check(name, started is not None and talk_end - 5 <= started <= talk_end + 4,
            f"shiur ends {_fmt(talk_end)}, weather started {_fmt(started)}")
    talk_starts = await h.started_times("shiur", start - 1)
    h.check(name, len(talk_starts) == 1, f"the shiur was not paused ({len(talk_starts)} starts)")
    if started:
        gap, _ = longest_silence(h.heard(started - 6, started + 6))
        h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")


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


async def sc_fit_before_hour(h: Harness) -> None:
    """PR 221 on the engine (Jerusalem 20:58:59Z: a 5-minute song started 61 s before the
    hard 21:00 time check). A long item due to start 38 s before a hard time check is
    swapped with a 40 s song further down, without touching the song the engine already
    holds; the time check still airs on its second and nothing is silent."""
    name = "fit_before_hour"
    await wait_music(h)
    await h.clear_pending()
    auto = {"source": "auto"}
    await h.queue([("song6", dict(auto)), ("song2", dict(auto)), ("story", dict(auto)),
                   ("song5", dict(auto)), ("song4", dict(auto))])
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
    t0 = await h.wait_on_air("song6", 30)
    if t0 is None:
        h.check(name, False, "song6 never went on air")
        return
    # song6 ends at +37, song2 at +74: the story would start there, 38 s before the hour
    due = datetime.fromtimestamp(t0 + 112, UTC)
    await h.queue([("time", {"preempt_at": due, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(max(0, due.timestamp() - time.time()) + 14)
    starts = await h.started_times("time", due.timestamp() - 5)
    late = starts[0][0] - due.timestamp() if starts else None
    h.check(name, late is not None and abs(late) <= 1.0,
            f"time check {'never' if late is None else f'{late:+.1f} s'}")
    heard = h.heard(t0 + 2, due.timestamp() + 12)
    h.print_timeline(heard, due.timestamp())
    names = [n for _, n in timeline(heard)]
    runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    clock = tone_name(TONES["time"][0])
    before = runs[:runs.index(clock)] if clock in runs else runs
    h.check(name, tone_name(TONES["story"][0]) not in before,
            f"the story did not start before the time check: {runs}")
    # Any 40 s song will do: since PR 248 the fit may pick one from the refill instead
    before = [n for n in before if n != "mix"]  # the fade into the time check
    h.check(name, bool(before) and before[-1].startswith("song"),
            f"a 40 s song was moved up and was on air at the hour: {before}")
    marks = timeline(heard)
    last = None
    for i, (t, n) in enumerate(marks):
        if n == clock and i > 0:
            last = marks[i - 1][0]
            break
    lost = (last + 40 - due.timestamp()) if last is not None else None
    h.check(name, lost is not None and lost <= 15.0,
            f"it lost {'?' if lost is None else f'{lost:.0f}'} s to the time check (the story would have lost 112 s)")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")
    await asyncio.to_thread(_control, "/skip", {"fade": 0.1})


async def sc_fit_preloaded_before_hour(h: Harness) -> None:
    """Jerusalem 04:00Z 2026-11-18: during a long item the engine had the next song
    pre-loaded for minutes, hour-fit would not touch it ("0 songs to choose from") and it
    was cut with 140 s left. A 150 s item is on air; the 150 s story pre-loaded behind it
    would start 38 s before a hard time check. It is exchanged for a 40 s song while the
    item on air still has more than 75 s to go; the engine drops its pre-loaded copy."""
    name = "fit_preloaded_before_hour"
    await wait_music(h)
    await h.clear_pending()
    auto = {"source": "auto"}
    await h.queue([("shiur", dict(auto)), ("story", dict(auto)), ("song5", dict(auto)),
                   ("song4", dict(auto)), ("song3", dict(auto))])
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
    t0 = await h.wait_on_air("shiur", 30)
    if t0 is None:
        h.check(name, False, "the long item never went on air")
        return
    # the long item ends at +150; the story would start there, 38 s before the hour
    due = datetime.fromtimestamp(t0 + 188, UTC)
    await h.queue([("time", {"preempt_at": due, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(max(0, due.timestamp() - time.time()) + 14)
    starts = await h.started_times("time", due.timestamp() - 5)
    late = starts[0][0] - due.timestamp() if starts else None
    h.check(name, late is not None and abs(late) <= 1.0,
            f"time check {'never' if late is None else f'{late:+.1f} s'}")
    heard = h.heard(t0 + 2, due.timestamp() + 12)
    h.print_timeline(heard, due.timestamp())
    names = [n for _, n in timeline(heard)]
    runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    clock = tone_name(TONES["time"][0])
    before = runs[:runs.index(clock)] if clock in runs else runs
    h.check(name, tone_name(TONES["story"][0]) not in before,
            f"the pre-loaded story did not start before the time check: {runs}")
    h.check(name, bool(before) and before[-1] in [tone_name(TONES[n][0]) for n in ("song3", "song4", "song5")],
            f"a 40 s song was moved up and was on air at the hour: {before}")
    # from what was heard: when the last song before the time check began
    marks = timeline(heard)
    last = None
    for i, (t, n) in enumerate(marks):
        if n == clock and i > 0:
            last = marks[i - 1][0]
            break
    lost = (last + 40 - due.timestamp()) if last is not None else None
    h.check(name, lost is not None and lost <= 15.0,
            f"it lost {'?' if lost is None else f'{lost:.0f}'} s to the time check (the story would have lost 112 s)")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")
    await asyncio.to_thread(_control, "/skip", {"fade": 0.1})
    await asyncio.sleep(5)  # the next scenario's own Next must not land on this one


async def _song_ends_before_hard(h: Harness, name: str, gap: float) -> None:
    """A song ends `gap` s before a hard time check, with songs queued behind it."""
    await h.set_config(fit_before_hard_items=False)
    try:
        await wait_music(h)
        await h.clear_pending()
        await h.queue([("song6", {"source": "auto"}), ("song5", {"source": "auto"}),
                       ("song2", {"source": "auto"}), ("song4", {"source": "auto"})])
        # Time for the backend to take back the song the engine already held
        await asyncio.sleep(8)
        cur = await h.on_air()
        if cur and cur.asset_id == h.assets["song6"] and cur.started_at:
            # song6 already followed the song on air by itself meanwhile: skipping now
            # would skip song6 and throw the timings off (harness run 2026-11-18)
            t0 = cur.started_at.timestamp()
        else:
            h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
            t0 = await h.wait_on_air("song6", 12)
        if t0 is None:
            # Right after the engine starts it can still hold an older song: once more
            h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
            t0 = await h.wait_on_air("song6", 20)
        if t0 is None:
            h.check(name, False, "song6 never went on air")
            return
        # Songs overlap 1 s: song5 (already with the engine) starts at +39 and ends at +79
        due = datetime.fromtimestamp(t0 + 79 + gap, UTC)
        await h.queue([("time", {"preempt_at": due, "timing_mode": "hard", "source": "auto"})])
        await asyncio.sleep(max(0, due.timestamp() - time.time()) + 16)
        starts = await h.started_times("time", due.timestamp() - 12)
        late = starts[0][0] - due.timestamp() if starts else None
        if gap <= 1:
            h.check(name, late is not None and abs(late) <= 1.0,
                    f"time check on its second: {'never' if late is None else f'{late:+.1f} s'}")
        else:
            h.check(name, late is not None and -gap - 1.0 <= late <= -gap + 2.0,
                    f"time check starts when the song ends, {gap:.0f} s early: "
                    f"{'never' if late is None else f'{late:+.1f} s'}")
        h.check(name, len(starts) == 1, f"the time check aired once ({len(starts)})")
        heard = h.heard(t0 + 45, due.timestamp() + 14)
        h.print_timeline(heard, due.timestamp())
        names = [n for _, n in timeline(heard)]
        runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
        clock = tone_name(TONES["time"][0])
        before = [n for n in (runs[:runs.index(clock)] if clock in runs else runs) if n != "silence"]
        after = [n for n in (runs[runs.index(clock) + 1:] if clock in runs else []) if n != "silence"]
        h.check(name, before == [tone_name(TONES["song5"][0])],
                f"only the song on air before the time check, no song started to be cut: {before}")
        h.check(name, runs.count(clock) == 1, f"the time check is heard whole, once: {runs}")
        h.check(name, "EMERGENCY" not in runs, f"no emergency music: {runs}")
        h.check(name, bool(after) and after[0] == tone_name(TONES["song2"][0]),
                f"the next song follows the time check: {after}")
        gap_s, gap_at = longest_silence(heard)
        h.check(name, gap_s <= 1.5, f"longest silence {gap_s:.1f} s at {_fmt(gap_at)}")
        # The 6 s time check must not be cut short when its own time arrives
        clock_secs = sum(WINDOW for t, n in ((w[0], "silence" if w[1] < -45 else tone_name(w[2])) for w in heard)
                         if n == clock)
        h.check(name, clock_secs >= 5.0, f"the time check played {clock_secs:.1f} s of its 6 s")
    finally:
        await h.set_config(fit_before_hard_items=None)


async def sc_song_ends_before_hour(h: Harness) -> None:
    """Jerusalem 23:00Z 2026-11-17: the song before the hour ended 1.9 s early, the engine
    was handed a 266 s song and it was chopped after 2.4 s by the time check. Now the
    backend hands out nothing for those seconds and the engine stays quiet (hold lane):
    no song blip, no emergency music, the time check on its second, music after it."""
    await _song_ends_before_hard(h, "song_ends_before_hour", 1.5)


async def sc_song_ends_6s_before_hour(h: Harness) -> None:
    """Jerusalem 00:00Z 2026-11-18: the song ended 6.6 s before the hour and the next one
    was cut after 6.8 s. More than 3 s is too long to leave quiet: the time check
    starts when the song ends, a few seconds early (as on the browser player), plays
    whole and once, and its own timer does not cut it when the hour arrives."""
    await _song_ends_before_hard(h, "song_ends_6s_before_hour", 6.0)


def both_audible(h: Harness, start: float, end: float, f1: float, f2: float, floor_db: float = -40.0) -> float:
    """Seconds between start and end in which both tones are heard at once."""
    total, t = 0.0, start
    while t + WINDOW <= end:
        a = tone_db(h.stack.wav, h.stack.wav_t0, t, t + WINDOW, f1)
        b = tone_db(h.stack.wav, h.stack.wav_t0, t, t + WINDOW, f2)
        if a > floor_db and b > floor_db:
            total += WINDOW
        t += WINDOW
    return total


async def sc_speech_never_overlaps(h: Harness) -> None:
    """Shmuel 2026-11-17: sponsorships played in a row ran into each other (the 3 s song
    crossfade was applied to speech). In the music lane two spoken items back to back
    are never heard together and leave no gap; the item after a spoken one still
    reports its start; song into song overlaps about 1 s (the station's setting), and
    not at all with the setting at 0."""
    name = "speech_never_overlaps"
    await wait_music(h)
    await h.clear_pending()
    auto = {"source": "auto"}
    await h.queue([("weather", dict(auto)), ("announce", dict(auto)), ("song5", dict(auto)),
                   ("song2", dict(auto)), ("song4", dict(auto))])
    await asyncio.sleep(8)
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
    t0 = await h.wait_on_air("weather", 30)
    if t0 is None:
        h.check(name, False, "the spoken item never went on air")
        return
    t_song2 = await h.wait_on_air("song2", 8 + 6 + 40 + 20)
    await asyncio.sleep(5)
    freq = {k: TONES[k][0] for k in ("weather", "announce", "song5", "song2", "song1", "song3")}
    heard = h.heard(t0 - 1, time.time() - 1)
    h.print_timeline(heard, t0)
    a_start = await h.started_times("announce", t0)
    s5 = await h.started_times("song5", t0)
    h.check(name, bool(a_start) and bool(s5),
            f"the item after each spoken item reported its start (announce {bool(a_start)}, song {bool(s5)})")
    if not a_start or not s5 or t_song2 is None:
        h.check(name, False, f"the items did not all air (song2 on air: {t_song2})")
        return
    ta, t5 = a_start[0][0], s5[0][0]
    both = both_audible(h, ta - 3, ta + 3, freq["weather"], freq["announce"])
    h.check(name, both <= 0.1, f"two spoken items heard together for {both:.1f} s")
    both = both_audible(h, t5 - 3, t5 + 3, freq["announce"], freq["song5"])
    h.check(name, both <= 0.1, f"a spoken item and the song after it heard together for {both:.1f} s")
    gap, gap_at = longest_silence([w for w in heard if t0 + 1 <= w[0] <= t5 + 3])
    h.check(name, gap <= 1.0, f"longest gap around the spoken items {gap:.1f} s")
    both = both_audible(h, t_song2 - 4, t_song2 + 4, freq["song5"], freq["song2"])
    h.check(name, 0.4 <= both <= 1.6, f"song into song overlaps {both:.1f} s (setting: 1 s)")

    # Shmuel 2026-11-17, "Song ends first, then speech": a song is not crossfaded into
    # a spoken item; it fades out and the item starts after it, with no gap
    await h.clear_pending()
    await h.queue([("song1", dict(auto)), ("weather", dict(auto)), ("song3", dict(auto))])
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
    t1 = await h.wait_on_air("song1", 30)
    tw = await h.wait_on_air("weather", 40 + 30) if t1 is not None else None
    if tw is None:
        h.check(name, False, "the spoken item after the song never went on air")
    else:
        await asyncio.sleep(4)
        both = both_audible(h, tw - 4, tw + 3, freq["song1"], freq["weather"])
        h.check(name, both <= 0.1, f"a song and the spoken item after it heard together for {both:.1f} s")
        gap, _ = longest_silence([w for w in h.heard(tw - 4, tw + 3)])
        h.check(name, gap <= 1.0, f"longest gap between the song and the spoken item {gap:.1f} s")

    # The station's song crossfade at 0: no overlap between songs either
    await h.set_config(crossfade_seconds=0)
    try:
        await asyncio.sleep(12)  # the scheduler picks the setting up on its next pass
        await h.clear_pending()
        await h.queue([("song1", dict(auto)), ("song3", dict(auto)), ("song4", dict(auto))])
        await asyncio.sleep(8)
        h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
        t1 = await h.wait_on_air("song1", 30)
        t3 = await h.wait_on_air("song3", 60) if t1 is not None else None
        if t3 is None:
            h.check(name, False, f"songs with crossfade 0 did not air (song1 {t1}, song3 {t3})")
            return
        await asyncio.sleep(5)
        both = both_audible(h, t3 - 4, t3 + 4, freq["song1"], freq["song3"])
        h.check(name, both <= 0.1, f"with the crossfade at 0, songs heard together for {both:.1f} s")
        gap, _ = longest_silence(h.heard(t3 - 4, t3 + 4))
        h.check(name, gap <= 1.0, f"and the gap between them is {gap:.1f} s")
    finally:
        await h.set_config(crossfade_seconds=None)
        await asyncio.sleep(12)


async def sc_live_show_quiet(h: Harness) -> None:
    """Jerusalem 23:06Z 2026-11-17: with a host on air the song on the engine played on
    for the whole show. Now the stream goes quiet within a second or two of the show's
    start (the host is heard through the site), stays quiet with no emergency music,
    and the station's music is back within a few seconds of its end."""
    name = "live_show_quiet"
    # No host microphone here: with a broadcast delay (PR 316) the show would never be
    # heard and the music rightly plays on, so this checks the take-over with no delay
    before = set_live_settings(h, live_delay_seconds=0, live_host_gone_seconds=0)   # no microphone here (PR 321 would hand back)
    try:
        await _live_show_quiet(h, name)
    finally:
        h.api("PUT", f"/stations/{STATION_ID}", {"automation_config": before})


async def _live_show_quiet(h: Harness, name: str) -> None:
    await wait_music(h)
    await asyncio.sleep(6)
    code, show = h.api("POST", "/live-shows", {"station_id": str(STATION_ID), "title": "Harness show",
                                               "calls_enabled": False})
    if code != 201:
        h.check(name, False, f"could not create the show ({code})")
        return
    t_start = time.time()
    code, _ = h.api("POST", f"/live-shows/{show['id']}/start")
    h.check(name, code == 200, f"show started ({code})")
    await asyncio.sleep(22)
    t_end = time.time()
    code, _ = h.api("POST", f"/live-shows/{show['id']}/end")
    h.check(name, code == 200, f"show ended ({code})")
    await asyncio.sleep(16)
    heard = h.heard(t_start - 3, time.time() - 1)
    h.print_timeline(heard, t_start)
    during = [w for w in heard if t_start + 2.0 <= w[0] <= t_end - 0.3]
    loud = sum(WINDOW for w in during if w[1] >= -45)
    what = [f"{w[0] - t_start:+.1f}s {tone_name(w[2])} {w[1]:.0f}dB" for w in during if w[1] >= -45][::4][:10]
    h.check(name, bool(during) and loud <= 0.2,
            f"the stream is quiet while the host is on air (sound for {loud:.1f} s of {len(during) * WINDOW:.0f} s) {what}")
    names = [n for _, n in timeline(heard)]
    h.check(name, "EMERGENCY" not in names, f"no emergency music during or after the show: {names}")
    after = [(t, n) for t, n in timeline([w for w in heard if w[0] >= t_end]) if n != "silence"]
    back = after[0][0] - t_end if after else None
    # The item after the show is loaded during it (engine proto 4): music back at once,
    # also with a slow backend (it was 2 s here, about 5 s on Main, 2026-11-18)
    h.check(name, back is not None and back <= 1.5,
            f"music back {'never' if back is None else f'{back:.1f} s'} after the show ended")
    h.check(name, len({n for _, n in after}) == 1,
            f"one item after the show, not a fragment of the old song first: {[n for _, n in after]}")
    st = await h.on_air()
    on_air = [k for k, v in h.assets.items() if st and v == st.asset_id and k in TONES]
    h.check(name, bool(after) and bool(on_air) and tone_name(TONES[on_air[0]][0]) == after[-1][1],
            f"the site shows what is heard ({on_air[0] if on_air else None} / {after[-1][1] if after else None})")


LIVE_RATE = 24000   # live_shows_ws.LIVE_SAMPLE_RATE


def set_live_settings(h: Harness, **values) -> dict:
    """Change the station's live-show settings (PR 316); returns the old automation_config."""
    _, st = h.api("GET", f"/stations/{STATION_ID}")
    before = dict((st or {}).get("automation_config") or {})
    h.api("PUT", f"/stations/{STATION_ID}", {"automation_config": {**before, **values}})
    return before


def _goertzel(samples, freq: float, rate: int = LIVE_RATE) -> float:
    k = 2 * math.cos(2 * math.pi * freq / rate)
    s1 = s2 = 0.0
    for x in samples:
        s0 = x / 32768 + k * s1 - s2
        s2, s1 = s1, s0
    return math.sqrt(max(0.0, s1 * s1 + s2 * s2 - k * s1 * s2)) / max(1, len(samples))


class LiveListener:
    """A listener page's live socket: every PCM frame with its arrival time."""

    def __init__(self):
        self.frames: list[tuple[float, array]] = []
        self.states: list[tuple[float, bool]] = []
        self.task = None

    async def run(self):
        import websockets
        url = f"ws://127.0.0.1:{BACKEND_PORT}/api/v1/ws/live/station/{STATION_ID}/listen"
        while True:   # reconnects like a listener page after a backend restart
            try:
                async with websockets.connect(url, max_size=None) as ws:
                    async for m in ws:
                        if isinstance(m, str):
                            msg = json.loads(m)
                            if msg.get("type") == "live_state":
                                self.states.append((time.time(), bool(msg.get("live"))))
                            continue
                        a = array("h")
                        a.frombytes(m[: len(m) // 2 * 2])
                        self.frames.append((time.time(), a))
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(1.0)

    def tone_at(self, freq: float, start: float, end: float) -> float:
        out = array("h")
        for t, a in self.frames:
            if start <= t <= end:
                out.extend(a)
        return _goertzel(out, freq) if len(out) else 0.0

    def tone_segments(self, freq: float, start: float, end: float, step: float = 0.5) -> list[float]:
        """Start times of the half-second steps where `freq` is clearly heard."""
        hits, t = [], start
        while t < end:
            if self.tone_at(freq, t, t + step) > 0.02:
                hits.append(t)
            t += step
        return hits


class HostMic:
    """The host's microphone: a steady tone sent in real time over the audio socket."""

    def __init__(self, show_id: str, token: str, freq: float = 440.0):
        self.show_id, self.token, self.freq = show_id, token, freq
        self.stop = False
        self.task = None

    async def run(self):
        """Sends until stopped; reconnects like the Host Console when the socket drops
        (a backend restart)."""
        import websockets
        url = f"ws://127.0.0.1:{BACKEND_PORT}/api/v1/ws/live/{self.show_id}/audio"
        self.connects = []
        while not self.stop:
            try:
                async with websockets.connect(url, max_size=None) as ws:
                    await ws.send(json.dumps({"type": "auth", "token": self.token}))
                    self.connects.append(time.time())
                    t0, sent = time.time(), 0
                    while not self.stop:
                        due = int((time.time() - t0) * LIVE_RATE) - sent
                        if due > 0:
                            a = array("h", (int(8000 * math.sin(2 * math.pi * self.freq * (sent + i) / LIVE_RATE))
                                            for i in range(due)))
                            await ws.send(a.tobytes())
                            sent += due
                        await asyncio.sleep(0.04)
            except Exception:
                await asyncio.sleep(1.0)


async def sc_live_show_no_host(h: Harness) -> None:
    """LD-18 (PR 316): a show is started with the default 15 s delay but the host's
    microphone never connects. The delay never fills, so listeners never hear the show and
    the music must play on untouched; the scheduler still ends the show at its planned end
    (a show left "live" for ever blocks the next one and keeps the console in a show)."""
    name = "live_show_no_host"
    before = set_live_settings(h, live_delay_seconds=15)
    try:
        await wait_music(h)
        await asyncio.sleep(4)
        now = datetime.now(UTC)
        planned_end = now + timedelta(seconds=25)
        code, show = h.api("POST", "/live-shows", {
            "station_id": str(STATION_ID), "title": "Harness show without a host", "calls_enabled": False,
            "scheduled_start": (now - timedelta(seconds=5)).isoformat(), "scheduled_end": planned_end.isoformat()})
        if code != 201:
            h.check(name, False, f"could not create the show ({code})")
            return
        t_start = time.time()
        code, _ = h.api("POST", f"/live-shows/{show['id']}/start")
        h.check(name, code == 200, f"show started ({code})")
        t_end = None
        while time.time() < planned_end.timestamp() + 15:
            _, cur_show = h.api("GET", f"/live-shows/{show['id']}")
            if (cur_show or {}).get("status") not in ("live", "LIVE"):
                t_end = time.time()
                break
            await asyncio.sleep(0.5)
        late = None if t_end is None else t_end - planned_end.timestamp()
        # PR 320 checks every 10 s; the music plays on meanwhile, so a few seconds late is harmless
        h.check(name, late is not None and -1.0 <= late <= 13.0,
                f"the scheduler ends the show at its planned end even though nobody heard it "
                f"({'still live 15 s after it' if late is None else f'{late:+.1f} s'})")
        if t_end is None:
            h.api("POST", f"/live-shows/{show['id']}/end")
            t_end = time.time()
        await asyncio.sleep(6)
    finally:
        h.api("PUT", f"/stations/{STATION_ID}", {"automation_config": before})
    heard = h.heard(t_start - 2, time.time() - 1)
    h.print_timeline(heard, t_start)
    gap, _ = longest_silence(heard)
    names = [n for _, n in timeline(heard)]
    h.check(name, gap <= 1.0 and "EMERGENCY" not in names,
            f"the music played on the whole time (longest silence {gap:.1f} s): {names}")
    _, st = h.api("GET", f"/stations/{STATION_ID}")
    h.check(name, not ((st or {}).get("automation_config") or {}).get("live_show_id"),
            "the station was never taken over by the show")


async def sc_live_show_delay_controls(h: Harness, restart: bool = False) -> None:
    """LD-24 / LD-19 (PR 316), engine station, 6 s delay, host microphone on.
    LD-24: Next pressed in the pre-roll moves the music on as usual; once listeners hear
    the host, Next and Panic must not put music back on the engine stream under the host.
    `restart` (LD-19): the backend restarts (a deploy) 4 s after listeners first hear the
    host. What was waiting in the delay is lost, but the engine must stay quiet (no music
    under the host), the host's page reconnects and listeners hear the host again within
    a delay of the reconnection, and End still brings the music back."""
    name = "live_show_delay_restart" if restart else "live_show_delay_controls"
    delay = 6.0
    before = set_live_settings(h, live_delay_seconds=int(delay), live_dump_seconds=0, live_timed_items="time")
    listener, mic = LiveListener(), None
    base = f"/stations/{STATION_ID}/queue"
    try:
        await wait_music(h)
        await asyncio.sleep(4)
        now = datetime.now(UTC)
        code, show = h.api("POST", "/live-shows", {
            "station_id": str(STATION_ID), "title": "Harness delay controls", "calls_enabled": False,
            "scheduled_start": now.isoformat(), "scheduled_end": (now + timedelta(minutes=10)).isoformat()})
        if code != 201:
            h.check(name, False, f"could not create the show ({code})")
            return
        listener.task = asyncio.ensure_future(listener.run())
        await asyncio.sleep(0.5)
        before_song = await h.on_air()
        t_start = time.time()
        h.api("POST", f"/live-shows/{show['id']}/start")
        mic = HostMic(show["id"], h.token)
        mic.task = asyncio.ensure_future(mic.run())
        t_next = None
        if not restart:
            await asyncio.sleep(2.0)
            t_next = time.time()
            code, _ = h.api("POST", f"{base}/skip?fade=hard")
            h.check(name, code == 200, f"Next in the pre-roll is answered ({code})")
        first = None
        for _ in range(40):
            first = next((t for t, _ in listener.frames if t >= t_start), None)
            if first is not None:
                break
            await asyncio.sleep(0.25)
        h.check(name, first is not None, "listeners hear the host")
        if first is None:
            return
        t_restart = t_back = None
        if restart:
            await asyncio.sleep(max(0, first + 4 - time.time()))
            t_restart = time.time()
            await asyncio.to_thread(h.stack.restart_backend)
            t_back = time.time()
            await asyncio.sleep(delay + 12)
        else:
            await asyncio.sleep(max(0, first + 3 - time.time()))
            code_n, _ = h.api("POST", f"{base}/skip?fade=hard")
            await asyncio.sleep(3)
            code_p, body_p = h.api("POST", f"{base}/panic")
            h.check(name, code_n < 500 and code_p < 500,
                    f"Next and Panic during the show are answered without a server error ({code_n}, {code_p}: "
                    f"{(body_p or {}).get('message', '')[:60]})")
            await asyncio.sleep(6)
        mic.stop = True
        t_end = time.time()
        h.api("POST", f"/live-shows/{show['id']}/end")
        await asyncio.sleep(delay + 20)
    finally:
        if mic is not None:
            mic.stop = True
        h.api("PUT", f"/stations/{STATION_ID}", {"automation_config": before})
        if listener.task:
            listener.task.cancel()
    heard = h.heard(t_start - 3, time.time() - 1)
    h.print_timeline(heard, t_start)
    if t_next is not None:
        after_next = [n for t, n in timeline([w for w in heard if t_next + 0.5 <= w[0] <= first])]
        old = [k for k, v in h.assets.items() if before_song and v == before_song.asset_id and k in TONES]
        old_tone = tone_name(TONES[old[0]][0]) if old else None
        h.check(name, bool(after_next) and after_next[-1] not in ("silence", old_tone, "EMERGENCY"),
                f"Next in the pre-roll moved the music on: {after_next} (was {old_tone})")
    # no new song may start in the moment before the take-over (it would be cut after a
    # fraction of a second: a blip on the stream, 2026-11-18 16:57Z after Next in the pre-roll)
    blips = [(round(t - first, 1), n) for t, n in timeline(heard) if first - 2.5 <= t <= first + 1.0 and n not in ("silence", "HOST")]
    h.check(name, not blips, f"no song starts just before the host is heard on the stream ({blips} s from first air)")
    quiet_from = first + 2.0
    during = [w for w in heard if quiet_from <= w[0] <= t_end - 0.3]
    loud = sum(WINDOW for w in during if w[1] >= -45)
    what = "through the backend restart" if restart else "after Next and Panic"
    h.check(name, bool(during) and loud <= 0.3,
            f"the engine stream stays quiet under the host {what} (sound for {loud:.1f} s of {len(during) * WINDOW:.0f} s)")
    if restart:
        reconnect = next((c for c in (mic.connects if mic else []) if c > t_restart), None)
        h.check(name, reconnect is not None, "the host's page reconnected after the restart")
        again = next((t for t, _ in listener.frames if t_back and t > t_back + 0.5), None)
        wait = None if again is None or reconnect is None else again - reconnect
        h.check(name, wait is not None and wait <= delay + 3.0,
                f"listeners hear the host again within a delay of the reconnection "
                f"({'never' if wait is None else f'{wait:.1f} s'})")
        _, st = h.api("GET", f"/stations/{STATION_ID}")
    after = [(t, n) for t, n in timeline([w for w in heard if w[0] >= t_end]) if n != "silence"]
    h.check(name, bool(after) and "EMERGENCY" not in [n for _, n in after],
            f"station music after the show: {[n for _, n in after]}")
    _, st = h.api("GET", f"/stations/{STATION_ID}")
    h.check(name, not ((st or {}).get("automation_config") or {}).get("live_show_id"),
            "the station is handed back after the show")


async def sc_live_show_host_gone(h: Harness) -> None:
    """LD-30 (PR 321): mid-show the host's page goes away (tab closed, connection lost).
    Listeners hear what was waiting in the delay; then, `live_host_gone_seconds` after the
    last frame, the station goes back to its queue and the engine's music returns with no
    long silence. The show stays live: when the host's page comes back the host is on air
    again through the delay and the engine is quiet again; End brings the music back."""
    name = "live_show_host_gone"
    delay, gone_after = 8.0, 8.0
    before = set_live_settings(h, live_delay_seconds=int(delay), live_dump_seconds=0,
                               live_timed_items="time", live_host_gone_seconds=int(gone_after))
    listener, mic, mic2 = LiveListener(), None, None
    t_end = None
    try:
        await wait_music(h)
        await asyncio.sleep(4)
        now = datetime.now(UTC)
        code, show = h.api("POST", "/live-shows", {
            "station_id": str(STATION_ID), "title": "Harness host gone", "calls_enabled": False,
            "scheduled_start": now.isoformat(), "scheduled_end": (now + timedelta(minutes=10)).isoformat()})
        if code != 201:
            h.check(name, False, f"could not create the show ({code})")
            return
        listener.task = asyncio.ensure_future(listener.run())
        await asyncio.sleep(0.5)
        t_start = time.time()
        h.api("POST", f"/live-shows/{show['id']}/start")
        mic = HostMic(show["id"], h.token)
        mic.task = asyncio.ensure_future(mic.run())
        first = None
        for _ in range(60):
            first = next((t for t, _ in listener.frames if t >= t_start), None)
            if first is not None:
                break
            await asyncio.sleep(0.25)
        h.check(name, first is not None, "listeners hear the host")
        if first is None:
            return
        await asyncio.sleep(max(0, first + 6 - time.time()))
        mic.stop = True                     # the host's tab closes
        t_gone = time.time()
        released = None
        while time.time() < t_gone + gone_after + delay + 20:
            _, st = h.api("GET", f"/stations/{STATION_ID}")
            if not ((st or {}).get("automation_config") or {}).get("live_show_id"):
                released = time.time()
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(10)
        _, cur = h.api("GET", f"/live-shows/{show['id']}")
        show_live = (cur or {}).get("status") in ("live", "LIVE")
        t_back = time.time()                # the host's page comes back
        mic2 = HostMic(show["id"], h.token, freq=440.0)
        mic2.task = asyncio.ensure_future(mic2.run())
        await asyncio.sleep(delay + 10)
        _, st = h.api("GET", f"/stations/{STATION_ID}")
        retaken = bool(((st or {}).get("automation_config") or {}).get("live_show_id"))
        mic2.stop = True
        t_end = time.time()
        h.api("POST", f"/live-shows/{show['id']}/end")
        await asyncio.sleep(delay + 15)
    finally:
        for m in (mic, mic2):
            if m is not None:
                m.stop = True
        h.api("PUT", f"/stations/{STATION_ID}", {"automation_config": before})
        if listener.task:
            listener.task.cancel()
    heard = h.heard(t_start - 3, time.time() - 1)
    h.print_timeline(heard, t_start)
    wait = None if released is None else released - t_gone
    h.check(name, wait is not None and gone_after - 1.0 <= wait <= gone_after + 4.0,
            f"the station is handed back {gone_after:.0f} s after the host's last audio "
            f"({'never' if wait is None else f'{wait:.1f} s'})")
    last_host = max((t for t, _ in listener.frames if t < t_back), default=None)
    music = [(t, n) for t, n in timeline([w for w in heard if t_gone <= w[0] <= t_back]) if n != "silence"]
    if last_host is not None and music:
        gap = music[0][0] - last_host
        h.check(name, gap <= 1.5,
                f"no long silence between the host's last delayed words and the music "
                f"({gap:+.1f} s; host last heard {last_host - t_gone:+.1f} s, music {music[0][0] - t_gone:+.1f} s after the tab closed)")
    else:
        h.check(name, False, f"music came back after the host left ({[n for _, n in music]})")
    h.check(name, show_live, "the show itself stays live while the host is away")
    again = next((t for t, _ in listener.frames if t > t_back + 0.5), None)
    h.check(name, again is not None and again - t_back <= delay + 4.0,
            f"the host's page comes back and listeners hear the host again "
            f"({'never' if again is None else f'{again - t_back:.1f} s'} after it reconnected)")
    h.check(name, retaken, "the station is the show's again once the host is back")
    if again is not None:
        during = [w for w in heard if again + 2.0 <= w[0] <= t_end - 0.3]
        loud = sum(WINDOW for w in during if w[1] >= -45)
        h.check(name, bool(during) and loud <= 0.3,
                f"the engine is quiet under the host again (sound for {loud:.1f} s of {len(during) * WINDOW:.0f} s)")
    after = [n for _, n in timeline([w for w in heard if w[0] >= t_end]) if n != "silence"]
    h.check(name, bool(after) and "EMERGENCY" not in after, f"station music after the show: {after}")


async def sc_live_show_delay_engine(h: Harness, preroll_item: bool = False) -> None:
    """LD-4b/11b/14b (PR 316): a live show with a 6 s broadcast delay on an engine station,
    with a real host microphone (440 Hz). The engine keeps the music through the pre-roll
    and goes quiet once listeners hear the host; a time check due during the show is heard
    once, on the listener page over the delay, never also on the engine stream; after End
    the delay plays out and then the music is back. `preroll_item` (LD-17/LD-26): the time
    check falls in the pre-roll instead, so the engine (still playing music) airs it once
    on its second and it is not aired again over the delay."""
    name = "live_show_delay_engine" + ("_preroll_item" if preroll_item else "")
    delay = 6.0
    before = set_live_settings(h, live_delay_seconds=int(delay), live_dump_seconds=0, live_timed_items="time")
    listener, mic = LiveListener(), None
    try:
        await wait_music(h)
        await asyncio.sleep(4)
        due = at(14 if preroll_item else 26)
        ids = await h.queue([("time", {"preempt_at": due, "timing_mode": "hard", "source": "auto"})])
        # 18 s: due 12 s after the delay fills, past the 8 s the pre-roll waits for (PR 336)
        start_at = due.timestamp() - (3 if preroll_item else 18)
        now = datetime.now(UTC)
        code, show = h.api("POST", "/live-shows", {
            "station_id": str(STATION_ID), "title": "Harness delay show", "calls_enabled": False,
            "scheduled_start": now.isoformat(), "scheduled_end": (now + timedelta(minutes=10)).isoformat()})
        if code != 201:
            h.check(name, False, f"could not create the show ({code})")
            return
        listener.task = asyncio.ensure_future(listener.run())
        await asyncio.sleep(max(0, start_at - time.time()))
        t_start = time.time()
        code, _ = h.api("POST", f"/live-shows/{show['id']}/start")
        h.check(name, code == 200, f"show started ({code})")
        mic = HostMic(show["id"], h.token)
        mic.task = asyncio.ensure_future(mic.run())
        await asyncio.sleep(max(0, due.timestamp() + 14 - time.time()))
        # What is waiting in the delay now plays out after End: more than the set delay
        # after a time check during non-stop talk (the clip lengthened it; it shrinks
        # back only about 3 % a second while the host talks)
        _, air = h.api("GET", f"/live-shows/{show['id']}/air")
        waiting = float((air or {}).get("delay") or delay)
        mic.stop = True
        t_end = time.time()
        code, _ = h.api("POST", f"/live-shows/{show['id']}/end")
        h.check(name, code == 200, f"show ended ({code})")
        await asyncio.sleep(delay + 30)
    finally:
        if mic is not None:
            mic.stop = True
        h.api("PUT", f"/stations/{STATION_ID}", {"automation_config": before})
        if listener.task:
            listener.task.cancel()

    first = next((t for t, _ in listener.frames if t >= t_start), None)
    last = max((t for t, _ in listener.frames), default=None)
    heard_at = None if first is None else first - t_start
    # Since PR 320 the pre-roll also waits for a time check on air, so the host comes in after it
    latest = delay + (6.0 if preroll_item else 0.0) + 1.5
    h.check(name, heard_at is not None and delay - 0.8 <= heard_at <= latest,
            f"listeners hear the host one delay later, or after a time check in the pre-roll "
            f"({'never' if heard_at is None else f'{heard_at:.1f} s'}, delay {delay:.0f} s)")
    live_on = next((t for t, on in listener.states if on and t >= t_start), None)
    h.check(name, live_on is not None and first is not None and abs(live_on - first) <= 1.0,
            f"listener pages are told LIVE when the host is first heard, not at Start "
            f"({'never' if live_on is None else f'{live_on - t_start:.1f} s after Start'})")
    heard = h.heard(t_start - 3, time.time() - 1)
    h.print_timeline(heard, t_start)
    pre = [w for w in heard if t_start + 0.5 <= w[0] <= t_start + delay - 1.0]
    pre_loud = sum(WINDOW for w in pre if w[1] >= -45)
    h.check(name, bool(pre) and pre_loud >= (len(pre) * WINDOW) - 0.6,
            f"the engine keeps the music through the pre-roll ({pre_loud:.1f} s of {len(pre) * WINDOW:.1f} s)")
    if first is not None:
        during = [w for w in heard if first + 2.0 <= w[0] <= t_end - 0.3]
        loud = sum(WINDOW for w in during if w[1] >= -45)
        h.check(name, bool(during) and loud <= 0.3,
                f"the engine stream is quiet once listeners hear the host (sound for {loud:.1f} s of {len(during) * WINDOW:.0f} s)")
    clock = tone_name(TONES["time"][0])
    engine_clock = [t for t, n in timeline(heard) if n == clock]
    page_clock = listener.tone_segments(TONES["time"][0], t_start, time.time())
    if preroll_item:
        late = engine_clock[0] - due.timestamp() if engine_clock else None
        # A song ending 1 to 8 s before a hard item lets the item start early instead of
        # chopping the next song (accepted rule, see MEMORY "Playout engine")
        h.check(name, late is not None and -8.5 <= late <= 2.0,
                f"the engine airs the time check in the pre-roll on its second, or up to 8 s early "
                f"after a song that ended then ({'never' if late is None else f'{late:+.1f} s'})")
        h.check(name, len(engine_clock) <= 1, f"once on the engine ({len(engine_clock)} times)")
        clock_secs = sum(WINDOW for w in heard if due.timestamp() - 9 <= w[0] <= due.timestamp() + 8
                         and w[1] >= -45 and tone_name(w[2]) == clock)
        h.check(name, clock_secs >= TONES["time"][1] - 1.0,
                f"the time check is heard to its end before the host takes over ({clock_secs:.1f} s of {TONES['time'][1]} s)")
        h.check(name, not page_clock, f"and not again over the delay on the listener page (at {[round(t - due.timestamp(), 1) for t in page_clock]} s)")
    else:
        late = page_clock[0] - due.timestamp() if page_clock else None
        h.check(name, late is not None and -1.0 <= late <= 1.5,
                f"the time check is heard on the listener page on its second over the delay ({'never' if late is None else f'{late:+.1f} s'})")
        h.check(name, not [t for t in engine_clock if t_start <= t <= t_end + delay + 5],
                f"and never also on the engine stream ({[round(t - due.timestamp(), 1) for t in engine_clock]})")
        host_after = listener.tone_at(440, due.timestamp() + TONES["time"][1] + 0.5, due.timestamp() + TONES["time"][1] + 2.5)
        h.check(name, host_after > 0.02, f"the host is heard again after the time check ({host_after:.3f})")
    rows = {e.id: e for e in await h.rows()}
    st = rows[ids[0]].status if ids[0] in rows else "gone"
    h.check(name, st not in ("pending", "playing"), f"the time check's queue entry is closed ({st})")
    tail = None if last is None else last - t_end
    h.check(name, tail is not None and waiting - 1.5 <= tail <= waiting + 2.0,
            f"after End what was waiting in the delay ({waiting:.1f} s) plays out to listeners "
            f"({'nothing' if tail is None else f'{tail:.1f} s'} after End)")
    after = [(t, n) for t, n in timeline([w for w in heard if w[0] >= t_end]) if n != "silence"]
    back = after[0][0] - (last or t_end) if after else None
    h.check(name, back is not None and -1.0 <= back <= 6.0,
            f"the engine's music is back after the delay played out ({'never' if back is None else f'{back:+.1f} s after the last delayed audio'})")
    h.check(name, bool(after) and "EMERGENCY" not in [n for _, n in after] and clock not in [n for _, n in after],
            f"station music after the show, no emergency files and no late time check: {[n for _, n in after]}")
    h.check(name, len({n for _, n in after}) == 1,
            f"one item after the show, not a fragment of the paused song first: {[n for _, n in after]}")
    cur = await h.on_air()
    h.check(name, bool(cur) and cur.asset.asset_type == "music", f"the site shows music ({cur.asset.title if cur else None})")


async def sc_live_show_on_the_stream(h: Harness) -> None:
    """Engine proto 5: the host's show (the backend's live.mp3, here a 1200 Hz tone) is
    heard on the stream while the show is on, with no music under it; when the show's
    audio stalls mid-show the stream is quiet, not music or emergency; after the show
    the station's music is back within a moment."""
    name = "live_show_on_the_stream"
    await wait_music(h)
    await asyncio.sleep(4)
    code, show = h.api("POST", "/live-shows", {"station_id": str(STATION_ID), "title": "Harness show",
                                               "calls_enabled": False})
    if code != 201:
        h.check(name, False, f"could not create the show ({code})")
        return
    h.stack.live_audio = True
    t_start = time.time()
    code, _ = h.api("POST", f"/live-shows/{show['id']}/start")
    h.check(name, code == 200, f"show started ({code})")
    await asyncio.sleep(16)
    t_stall = time.time()
    h.stack.live_audio = False          # the show's audio stops coming (backend stall)
    await asyncio.sleep(8)
    h.stack.live_audio = True           # and comes back
    await asyncio.sleep(10)
    t_end = time.time()
    h.stack.live_audio = False
    code, _ = h.api("POST", f"/live-shows/{show['id']}/end")
    h.check(name, code == 200, f"show ended ({code})")
    await asyncio.sleep(14)
    heard = h.heard(t_start - 3, time.time() - 1)
    h.print_timeline(heard, t_start)
    first = [w for w in heard if t_start + 4.0 <= w[0] <= t_stall]
    host = sum(WINDOW for w in first if w[1] >= -45 and tone_name(w[2]) == "HOST")
    h.check(name, bool(first) and host >= len(first) * WINDOW - 0.5,
            f"the host is heard on the stream from 4 s after the start ({host:.1f} s of {len(first) * WINDOW:.0f} s)")
    show_names = [n for t, n in timeline([w for w in heard if t_start + 2.0 <= w[0] <= t_end])]
    h.check(name, not [n for n in show_names if n not in ("HOST", "silence", "mix")],
            f"nothing but the host or quiet during the show, also while its audio stalled: {show_names}")
    back = [w for w in heard if t_stall + 9.0 <= w[0] <= t_end]
    h.check(name, any(tone_name(w[2]) == "HOST" and w[1] >= -45 for w in back),
            "the host is heard again after the stall")
    after = [(t, n) for t, n in timeline([w for w in heard if w[0] >= t_end]) if n not in ("silence", "HOST")]
    gap = after[0][0] - t_end if after else None
    h.check(name, gap is not None and gap <= 2.0,
            f"music back {'never' if gap is None else f'{gap:.1f} s'} after the show ended")
    names = [n for _, n in timeline(heard)]
    h.check(name, "EMERGENCY" not in names, f"no emergency music: {names}")


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


async def sc_next_on_a_busy_server(h: Harness) -> None:
    """Main 13:27:42Z 2026-11-18: Shmuel pressed Next by hand; the next item was
    "prepared" with nothing decoded yet (the droplet's one CPU was busy), the crossfade
    had 0.00 s to cross into and the emergency lane played for 1.7 s. Here the engine
    shares one CPU with a busy process while Next is pressed right after items start
    (fresh prepares): never any emergency music, and no silence over 3 s."""
    name = "next_on_a_busy_server"
    await wait_music(h)
    t_start = time.time()
    pids = subprocess.run(["pgrep", "-f", "liquidsoap .*radio"], capture_output=True, text=True).stdout.split()
    for pid in pids:
        subprocess.run(["taskset", "-a", "-p", "-c", "0", pid], stdout=subprocess.DEVNULL, check=False)
    hogs = [subprocess.Popen(["taskset", "-c", "0", sys.executable, "-c", "while True: pass"])
            for _ in range(2)]
    try:
        await h.queue([(f"song{i}", {}) for i in (5, 6, 1, 2, 3, 4, 5, 6)])
        await asyncio.sleep(3)
        t_start = time.time()
        for delay in (0.2, 0.5, 0.1, 1.0, 0.3, 0.7):
            h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
            await asyncio.sleep(delay)
            h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
            await asyncio.sleep(3)
        await asyncio.sleep(6)
    finally:
        for p in hogs:
            p.kill()
        for pid in pids:
            subprocess.run(["taskset", "-a", "-p", "-c", f"0-{os.cpu_count() - 1}", pid],
                           stdout=subprocess.DEVNULL, check=False)
    heard = h.heard(t_start - 1, time.time() - 1)
    h.print_timeline(heard, t_start)
    names = [n for _, n in timeline(heard)]
    h.check(name, "EMERGENCY" not in names, f"no emergency music after any Next: {names}")
    gap, gap_at = longest_silence(heard)
    h.check(name, gap <= 3.0, f"longest silence {gap:.1f} s")


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


async def sc_slow_backend_no_gap(h: Harness) -> None:
    """Jerusalem, 2026-11-18 05:30Z to 06:02Z: 2.6 to 3.8 s of digital silence after Next
    and when a paused shiur came back, each time while the backend answered the engine
    in 3 to 4 s. The engine brings its level back up from short tasks, and those waited
    behind its calls to the backend (start report, next item, hello), which filled its
    two task queues. Here every call to the backend takes 4 s: Next twice, then a Play
    Now, and no gap over 1 s."""
    name = "slow_backend_no_gap"
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("song5", {}), ("song6", {}), ("song1", {}), ("song2", {}), ("song3", {})])
    await asyncio.sleep(8)
    h.stack.slow_backend = 4.0
    try:
        await asyncio.sleep(6)
        t_start = time.time()
        for _ in range(3):
            h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=fade")
            await asyncio.sleep(9)
    finally:
        h.stack.slow_backend = 0.0
    await asyncio.sleep(6)
    heard = h.heard(t_start - 1, time.time() - 1)
    h.print_timeline(heard, t_start)
    gap, gap_at = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s" + (f" at {_fmt(gap_at)}" if gap_at else ""))


async def sc_next_while_held(h: Harness) -> None:
    """The song on air ends 4 s before a hard time check, so the backend hands the engine
    no song for after it (the hold rule) and nothing is pre-loaded. Next pressed then
    skipped into nothing: a second of emergency music, then the next song (FM thread's
    harness, 2026-11-18; the same on Jerusalem 05:55:39Z when the next file was still
    downloading). Now the song on air plays on until the next one is loaded: no
    emergency music, the next song within 4 s of the press, the time check on time."""
    name = "next_while_held"
    await wait_music(h)
    await h.clear_pending()
    auto = {"source": "auto"}
    await h.queue([("song6", dict(auto)), ("song2", dict(auto)), ("song5", dict(auto)), ("song1", dict(auto))])
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
    # song6 may have gone on air through the timed lane (the engine held another song):
    # the case here is a song in the music lane, so wait for the one after it
    # (song6 first: song2 may be the song on air from the scenario before)
    t0 = await h.wait_on_air("song2", 80) if await h.wait_on_air("song6", 30) else None
    if t0 is None:
        h.check(name, False, "song2 never went on air")
        return
    due = datetime.fromtimestamp(t0 + 41, UTC)
    await h.queue([("time", {"preempt_at": due, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(max(0, t0 + 12 - time.time()))
    pressed = time.time()
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=fade")
    t2 = await h.wait_on_air("song5", 10)
    h.check(name, t2 is not None and t2 - pressed <= 4.0,
            f"the next song after Next: {'never' if t2 is None else f'{t2 - pressed:.1f} s after the press'}")
    await asyncio.sleep(max(0, due.timestamp() - time.time()) + 12)
    starts = await h.started_times("time", due.timestamp() - 9)
    late = starts[0][0] - due.timestamp() if starts else None
    h.check(name, late is not None and abs(late) <= 1.0,
            f"time check {'never' if late is None else f'{late:+.1f} s'}")
    heard = h.heard(t0 + 2, time.time() - 1)
    h.print_timeline(heard, pressed)
    names = [n for _, n in timeline(heard)]
    h.check(name, "EMERGENCY" not in names, f"no emergency music: {names}")
    gap, gap_at = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s" + (f" at {_fmt(gap_at)}" if gap_at else ""))


async def sc_three_item_group(h: Harness) -> None:
    """V1-9: a hard group of three (time, weather, announcement) takes over on the
    second, plays all three in order, then music comes back with the NEXT song: the
    song it cut is not reported or heard again."""
    name = "three_item_timed_group"
    await wait_music(h)
    cut = await h.on_air()
    # The songs after the group must be other songs: scenarios share one rotation, and the
    # next song being the same one as the cut song read as "the cut song came back"
    await h.clear_pending()
    await h.queue([(n, {}) for n in ("song1", "song2", "song3", "song4", "song5", "song6")
                   if h.assets[n] != cut.asset_id][:4])
    t1 = at(20)
    await h.queue([(n, {"preempt_at": t1, "timing_mode": "hard", "source": "auto"})
                   for n in ("time", "weather", "announce")])
    await asyncio.sleep(max(0, t1.timestamp() - time.time()) + 6 + 8 + 6 + 12)
    starts = [(await h.started_times(n, t1.timestamp() - 5)) for n in ("time", "weather", "announce")]
    firsts = [s[0][0] if s else None for s in starts]
    late = firsts[0] - t1.timestamp() if firsts[0] else None
    h.check(name, late is not None and -1 <= late <= 2.0, f"group due {_fmt(t1.timestamp())}, time check {'never' if late is None else f'{late:+.1f} s'}")
    h.check(name, all(firsts) and firsts[0] < firsts[1] < firsts[2], f"all three played in order: {[_fmt(t) for t in firsts]}")
    h.check(name, all(len(s) == 1 for s in starts), f"each played once: {[len(s) for s in starts]}")
    again = [e for e in await h.rows() if e.asset_id == cut.asset_id and e.started_at
             and e.started_at.timestamp() > t1.timestamp()]
    h.check(name, not again, f"the cut song was not started again ({len(again)})")
    cur = await h.on_air()
    h.check(name, bool(cur) and cur.asset.asset_type == "music", f"music back on air ({cur.asset.title if cur else None})")
    heard = h.heard(t1.timestamp() - 3, (firsts[2] or t1.timestamp() + 20) + 10)
    h.print_timeline(heard, t1.timestamp())
    names = [n for _, n in timeline(heard)]
    runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    # The cut song can be one of the extra assets that reuse a song's file (song3quiet)
    cut_key = [k for k, v in h.assets.items() if v == cut.asset_id][0]
    cut_key = cut_key if cut_key in TONES else cut_key.removesuffix("quiet")
    cut_tone = tone_name(TONES[cut_key][0]) if cut_key in TONES else cut_key
    h.check(name, cut_tone not in runs[runs.index(tone_name(TONES["time"][0])) + 1:] if tone_name(TONES["time"][0]) in runs else False,
            f"heard {runs}; the cut song ({cut_tone}) not heard after the group")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")


async def sc_two_groups(h: Harness, apart: float) -> None:
    """V1-10: two hard groups `apart` seconds apart (0.3 s: one take-over; 1 s: two in
    order). Either way both items air once, in order, the first on its second."""
    name = f"two_groups_{apart:g}s_apart"
    await wait_music(h)
    t1 = at(20)
    t2 = t1 + timedelta(seconds=apart)
    await h.queue([("time", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"}),
                   ("weather", {"preempt_at": t2, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(max(0, t1.timestamp() - time.time()) + 6 + 8 + 12 + (apart if apart > 5 else 0))
    a = await h.started_times("time", t1.timestamp() - 5)
    b = await h.started_times("weather", t1.timestamp() - 5)
    if apart > 8:
        # Both groups are loaded in the engine before the first starts (hand-over is 45 s
        # ahead). The first lost its start to the second and aired at the second's time
        # (2026-11-18); each keeps its own second.
        late2 = b[0][0] - t2.timestamp() if b else None
        h.check(name, late2 is not None and -1 <= late2 <= 2.0,
                f"second group on its own time ({'never' if late2 is None else f'{late2:+.1f} s'})")
    late = a[0][0] - t1.timestamp() if a else None
    h.check(name, late is not None and -1 <= late <= 2.0, f"first group {'never' if late is None else f'{late:+.1f} s'}")
    h.check(name, len(a) == 1 and len(b) == 1, f"each item played once ({len(a)}, {len(b)})")
    h.check(name, bool(a and b) and a[0][0] < b[0][0], f"in order: time {_fmt(a[0][0] if a else None)}, weather {_fmt(b[0][0] if b else None)}")
    # V1-10b: a hard group due while another timed item plays follows it (the backend
    # moves it to the end of the first and hands it over as part of that take-over)
    played = (b[0][0] - a[0][0]) if a and b else 0.0
    h.check(name, played >= TONES["time"][1] - 0.5,
            f"time check played whole before the weather ({played:.1f} s of {TONES['time'][1]} s)")
    cur = await h.on_air()
    h.check(name, bool(cur) and cur.asset.asset_type == "music", f"music back on air ({cur.asset.title if cur else None})")
    heard = h.heard(t1.timestamp() - 3, t1.timestamp() + 22 + (apart if apart > 5 else 0))
    h.print_timeline(heard, t1.timestamp())
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")
    if apart > 8:
        names = [n for _, n in timeline(heard)]
        h.check(name, "EMERGENCY" not in names, f"no emergency music: {names}")


async def sc_play_now_before_hard_item(h: Harness) -> None:
    """V3-52l: a hard time check is loaded in the engine (it is handed over 45 s ahead)
    when the operator presses Play now on a short item. The engine's timed lane played
    its items back to back, so the time check followed the Play now item at once,
    seconds early. It stays in the waiting room until its own time."""
    name = "play_now_before_hard_item"
    await wait_music(h)
    t1 = at(34)
    await h.queue([("time", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(max(0, t1.timestamp() - 22 - time.time()))
    t_fire = time.time()
    code, _ = h.api("POST", f"/stations/{STATION_ID}/queue/fire-now", {"asset_id": str(h.assets["announce"])})
    h.check(name, code in (200, 201), f"Play now 22 s before the time check ({code})")
    await asyncio.sleep(max(0, t1.timestamp() - time.time()) + 6 + 10)
    a = await h.started_times("announce", t_fire - 2)
    b = await h.started_times("time", t_fire - 2)
    took = a[0][0] - t_fire if a else None
    h.check(name, took is not None and took <= 3.0, f"Play now item on air {'never' if took is None else f'after {took:.1f} s'}")
    late = b[0][0] - t1.timestamp() if b else None
    h.check(name, late is not None and -1 <= late <= 2.0,
            f"time check on its own second, not behind the Play now item ({'never' if late is None else f'{late:+.1f} s'})")
    h.check(name, len(b) == 1, f"time check played once ({len(b)})")
    heard = h.heard(t_fire - 2, t1.timestamp() + 12)
    h.print_timeline(heard, t1.timestamp())
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.5, f"longest silence {gap:.1f} s")
    names = [n for _, n in timeline(heard)]
    h.check(name, "EMERGENCY" not in names, f"no emergency music: {names}")
    cur = await h.on_air()
    h.check(name, bool(cur) and cur.asset.asset_type == "music", f"music back on air ({cur.asset.title if cur else None})")



def _control(path: str, body: dict | None = None, token: str = TOKEN) -> int:
    """POST straight to the engine's control port (what nginx forwards to)."""
    req = urllib.request.Request(f"http://127.0.0.1:{CONTROL_PORT}{path}", method="POST",
                                 data=json.dumps(body or {}).encode(),
                                 headers={"X-Playout-Token": token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


async def sc_backend_unreachable(h: Harness) -> None:
    """B-MODE-24 / B-MODE-26 / V1-3: the backend cannot be reached. When the song on air
    ends the engine plays its emergency files (no silence) and keeps asking every 2 s;
    once the backend is back it returns to the station's queue by itself."""
    name = "backend_unreachable"
    await wait_music(h)
    cur = await h.on_air()
    song_end = cur.started_at.timestamp() + (cur.asset.duration or 40)
    h.stack.backend_down = True
    try:
        # the next song was pre-loaded before the outage: let it and the one on air run out
        await asyncio.sleep(max(0, song_end - time.time()) + 40 + 12)
        down_heard = h.heard(song_end - 3, time.time() - 1)
    finally:
        h.stack.backend_down = False
    back_at = time.time()
    await asyncio.sleep(30)
    heard = h.heard(song_end - 3, time.time() - 1)
    h.print_timeline(heard, back_at)
    names = [n for _, n in timeline(down_heard)]
    h.check(name, "EMERGENCY" in names, f"emergency files played while the backend was down: {sorted(set(names))}")
    gap, gap_at = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s (outage and return)")
    tail = [n for _, n in timeline(h.heard(time.time() - 6, time.time() - 1))]
    h.check(name, bool(tail) and tail[-1].startswith("song"),
            f"back on the station's music within 30 s of the backend returning: {tail}")
    st = await h.on_air()
    h.check(name, st is not None and st.started_at is not None and st.started_at.timestamp() >= back_at - 1,
            f"the site shows what started after the return ({st.asset.title if st else None})")


async def sc_local_box_commands(h: Harness) -> None:
    """The local FM box has no inbound line (needs --local-box; the backend's engine
    address leads nowhere): Next and Play Now from the site still act at once, carried
    by the relay's own outbound call, and the hourly hand-over arrives the same way."""
    name = "local_box_commands"
    relay = h.stack.relay
    if relay is None:
        h.check(name, False, "run with --local-box")
        return
    await wait_music(h)
    await h.queue([("song5", {}), ("song6", {})])
    await asyncio.sleep(4)
    before = relay.status()["commands_done"]
    cur = await h.on_air()
    pressed = time.time()
    code = h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")[0]
    answered = time.time() - pressed
    await asyncio.sleep(5)
    line = timeline(h.heard(pressed - 2, time.time() - 1))
    h.print_timeline(h.heard(pressed - 2, time.time() - 1), pressed)
    changes = [t for t, n in line[1:] if n.startswith("song")]
    h.check(name, code == 200 and answered < 1.5, f"Next answered {code} in {answered:.2f} s")
    h.check(name, bool(changes) and changes[0] - pressed <= 2.0,
            f"song changed {changes[0] - pressed:+.2f} s after Next" if changes else f"no song change heard: {line}")
    now = await h.on_air()
    h.check(name, now is not None and cur is not None and now.id != cur.id, "the site moved to the next song")
    h.check(name, relay.status()["commands_done"] > before,
            f"the relay carried the command ({before} -> {relay.status()['commands_done']})")
    # The box tells the site how it is doing with the same call
    _, shown = h.api("GET", "/playout-status")
    box = (shown.get("stations", {}).get(str(STATION_ID)) or {}).get("box") or {}
    h.check(name, box.get("clock_ok") is True and box.get("engine_ok") is True
            and (box.get("disk_free_gb") or 0) > 0 and box.get("seen_seconds_ago", 99) < 40,
            f"the site holds the box's own report: {box}")
    # A hard time check handed over by the backend, through the relay
    due_dt = at(55)
    due = due_dt.timestamp()
    ids = await h.queue([("time", {"preempt_at": due_dt, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(55 + 6 + 6)
    line = timeline(h.heard(due - 5, time.time() - 1))
    starts = [t for t, n in line if n == "time"]
    h.check(name, bool(starts) and abs(starts[0] - due) <= 1.0,
            f"time check heard {starts[0] - due:+.2f} s from its second" if starts else f"time check never aired: {line}")
    rows = [e for e in await h.rows() if e.id == ids[0]]
    h.check(name, bool(rows) and rows[0].started_at is not None
            and abs(rows[0].started_at.timestamp() - due) <= 2.0, "the site recorded it at its time")
    gap, _ = longest_silence(h.heard(pressed - 2, time.time() - 1))
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")


def record_card(seconds: float, dest: Path) -> tuple[subprocess.Popen, float]:
    """Record what the sound card receives (PulseAudio monitor of the test sink)."""
    monitor = os.environ.get("KBR_HARNESS_ALSA_MONITOR", "fm.monitor")
    p = subprocess.Popen(
        ["timeout", str(seconds), "parec", "-d", monitor, "--rate=44100", "--channels=2",
         "--format=s16le", "--latency-msec=50", str(dest)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return p, time.time()


def raw_to_wav(raw: Path, dest: Path) -> None:
    with wave.open(str(dest), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        data = raw.read_bytes()
        w.writeframes(data[:len(data) // 4 * 4])


async def sc_local_box_sound_card(h: Harness) -> None:
    """The local FM box's sound card output (needs KBR_HARNESS_ALSA_DEVICE, a PulseAudio
    null sink in tests): the card gets the same items in the same order as the stream,
    within a second of it, through a Next and a hard time check, with no gaps; and the
    stream's own timing is what it is without a card."""
    name = "local_box_sound_card"
    if not os.environ.get("KBR_HARNESS_ALSA_DEVICE"):
        h.check(name, False, "set KBR_HARNESS_ALSA_DEVICE (and start a PulseAudio null sink)")
        return
    await wait_music(h)
    await h.queue([("song5", {}), ("song6", {})])
    due_dt = at(40)
    due = due_dt.timestamp()
    await h.queue([("time", {"preempt_at": due_dt, "timing_mode": "hard", "source": "auto"})])
    raw = h.stack.dir / "card.raw"
    rec, card_t0 = record_card(62, raw)
    start = time.time() + 1
    await asyncio.sleep(12)
    pressed = time.time()
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
    await asyncio.sleep(due - time.time() + 16)
    end = time.time() - 1
    stream = timeline(h.heard(start, end))
    rec.wait(timeout=30)
    card_wav = h.stack.dir / "card.wav"
    raw_to_wav(raw, card_wav)
    card_windows = analyse_wav(card_wav, card_t0, start, end)
    card = timeline(card_windows)
    for t, n in stream:
        log(f"    stream {t - due:+7.1f}s  {n}")
    for t, n in card:
        log(f"    card   {t - due:+7.1f}s  {n}")

    def runs(line):
        names = [n for _, n in line if n not in ("mix", "silence")]
        return [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    h.check(name, len(runs(card)) >= 4 and runs(card) == runs(stream),
            f"the card got the same items in order: {runs(card)} (stream {runs(stream)})")
    s_time = [t for t, n in stream if n == "time"]
    c_time = [t for t, n in card if n == "time"]
    h.check(name, bool(s_time) and abs(s_time[0] - due) <= 1.0,
            f"stream: time check {s_time[0] - due:+.2f} s from its second (as without a card)"
            if s_time else "time check not on the stream")
    h.check(name, bool(c_time) and bool(s_time) and -0.5 <= c_time[0] - s_time[0] <= 1.5,
            f"card: time check {c_time[0] - s_time[0]:+.2f} s after the stream"
            if c_time and s_time else "time check not on the card")
    s_next = [t for t, n in stream[1:] if n.startswith("song") and t >= pressed - 0.5]
    c_next = [t for t, n in card[1:] if n.startswith("song") and t >= pressed - 0.5]
    h.check(name, bool(s_next) and bool(c_next) and c_next[0] - pressed <= 3.0,
            f"Next: card changed song {c_next[0] - pressed:+.2f} s after the press (stream {s_next[0] - pressed:+.2f} s)"
            if s_next and c_next else "no song change on the card after Next")
    gap, _ = longest_silence(card_windows)
    h.check(name, gap <= 1.0, f"longest silence on the card {gap:.1f} s")


async def sc_local_box_power_loss(h: Harness) -> None:
    """The local FM box loses power with its line down (needs --local-box): the engine and
    the relay die at once, 10 s after a hard time check was handed to the engine, and
    both start again from what is on the disk, still with no internet. Station music is
    back within seconds of the engine starting, the song that was on air is not played
    again, the time check airs once on its second, nothing from the emergency folder
    after the first seconds; when the line returns the site learns what aired."""
    name = "local_box_power_loss"
    if h.stack.relay is None:
        h.check(name, False, "run with --local-box")
        return
    await wait_music(h)
    await h.queue([("song5", {}), ("song6", {})])
    due_dt = at(110)
    due = due_dt.timestamp()
    ids = await h.queue([("time", {"preempt_at": due_dt, "timing_mode": "hard", "source": "auto"})])
    time_id = str(ids[0])
    relay = h.stack.relay

    def copied() -> bool:
        st = relay.status()
        timed = [e for e in relay.plan.get("entries", []) if e.get("at") and abs(e["at"] - due) < 1]
        return st["plan_audio_missing"] == 0 and bool(timed) and st["plan_entries"] >= 5
    try:
        wait_for(copied, 30, "the relay's copy of the plan and its audio")
    except Exception as e:
        h.check(name, False, f"plan not copied: {e!r} {relay.status()}")
        return
    cut = time.time()
    h.stack.backend_down = h.stack.control_down = True
    try:
        # The relay hands the time check to the engine 45 s ahead; the power goes after that
        try:
            wait_for(lambda: time_id in relay.timed_sent, due - time.time() - 30, "the time check handed over")
        except Exception as e:
            h.check(name, False, f"time check not handed to the engine before the cut: {e!r}")
            return
        before = timeline(h.heard(cut, time.time() - 1))
        on_air_before = [n for _, n in before if n.startswith("song")][-1:]
        planned = [str(e.get("title") or "").split(" ")[0] for e in relay.plan.get("entries", [])
                   if not e.get("at") and e["entry_id"] not in set(relay.done)]
        await asyncio.sleep(max(0, due - 35 - time.time()))
        power_off = time.time()
        h.stack.kill_engine()
        h.stack.stop_relay()
        # What a cut in the middle of a write leaves behind
        (h.stack.dir / "relay" / "state.json.part").write_text('{"done": [')
        await asyncio.sleep(3)
        h.stack.start_relay()   # reads plan.json and state.json, nothing else
        h.stack.start_engine()  # returns when the engine writes sound
        relay = h.stack.relay
        power_on = time.time()
        log(f"    power off {power_off - due:+.1f}s, engine writing again {power_on - due:+.1f}s")
        await asyncio.sleep(max(0, due - time.time()) + 6 + 40)
        heard = h.heard(power_on, time.time() - 1)
    finally:
        h.stack.backend_down = h.stack.control_down = False
    back = time.time()
    h.print_timeline(heard, due)
    line = timeline(heard)
    names = [n for _, n in line]
    runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    h.check(name, power_on < due - 3, f"the box was up again {due - power_on:.0f} s before the time check")
    music = [t for t, n in line if n.startswith("song")]
    h.check(name, bool(music) and music[0] - power_on <= 8.0,
            f"station music {music[0] - power_on:.1f} s after the engine started" if music else "no station music")
    late_emergency = [round(t - power_on, 1) for t, n in line if n == "EMERGENCY" and t - power_on > 8.0]
    h.check(name, not late_emergency, f"no emergency files after the first seconds: {late_emergency or runs}")
    starts = [t for t, n in line if n == "time"]
    h.check(name, bool(starts) and abs(starts[0] - due) <= 1.0,
            f"time check heard {starts[0] - due:+.2f} s from its second" if starts else "time check never aired")
    h.check(name, runs.count("time") == 1, f"time check aired once: {runs}")
    songs = [n for n in runs if n.startswith("song")]
    h.check(name, bool(songs) and songs[0] not in on_air_before,
            f"the song on air at the cut ({on_air_before}) did not start again: {songs}")
    def follows_plan(seq: list[str]) -> bool:
        return any(planned[i:i + len(seq)] == seq for i in range(len(planned)))
    h.check(name, len(songs) >= 2 and follows_plan(songs), f"songs in the plan's order: {songs} (plan {planned[:6]})")
    gap, _ = longest_silence(h.heard(power_on + 8, back - 1))
    h.check(name, gap <= 1.0, f"longest silence once it was up {gap:.1f} s")
    try:
        wait_for(lambda: relay.status()["online"] and relay.status()["reports_kept"] == 0, 40, "reports sent")
    except Exception as e:
        h.check(name, False, f"start reports not delivered: {e!r} {relay.status()}")
    await asyncio.sleep(20)
    times = await h.started_times("time", cut)
    h.check(name, len(times) == 1 and abs(times[0][0] - due) <= 2.0,
            f"the site recorded the time check once, at its time: {[round(t[0] - due, 1) for t in times]}")
    late = [n for _, n in timeline(h.heard(back, time.time() - 1))]
    h.check(name, "time" not in late, f"the time check did not air again when the line came back: {late[:4]}")


async def sc_local_box_offline(h: Harness) -> None:
    """The local FM box with its line cut both ways (needs --local-box): the queue plays
    on in order from the box's own disk, a hard time check airs on the second, nothing
    from the emergency folder, no silence; when the line returns the site catches up."""
    name = "local_box_offline"
    relay = h.stack.relay
    if relay is None:
        h.check(name, False, "run with --local-box")
        return
    await wait_music(h)
    await h.queue([("song5", {}), ("song6", {})])
    due_dt = at(100)
    due = due_dt.timestamp()
    await h.queue([("time", {"preempt_at": due_dt, "timing_mode": "hard", "source": "auto"})])

    def copied() -> bool:
        st = relay.status()
        timed = [e for e in relay.plan.get("entries", []) if e.get("at") and abs(e["at"] - due) < 1]
        return st["plan_audio_missing"] == 0 and bool(timed) and st["plan_entries"] >= 5
    try:
        wait_for(copied, 30, "the relay's copy of the plan and its audio")
    except Exception as e:
        h.check(name, False, f"plan not copied: {e!r} {relay.status()}")
        return
    cut = time.time()
    # The order the box holds on its disk at the cut. Titles are compared against this,
    # not against "no title twice": the automatic fill may queue the song that is on air
    # again as a later entry, which is the queue's order and not a repeat.
    planned = [str(e.get("title") or "").split(" ")[0] for e in relay.plan.get("entries", []) if not e.get("at")]
    h.stack.backend_down = h.stack.control_down = True
    try:
        await asyncio.sleep(due - time.time() + 6 + 45)
        heard = h.heard(cut, time.time() - 1)
    finally:
        h.stack.backend_down = h.stack.control_down = False
    back = time.time()
    h.print_timeline(heard, due)
    line = timeline(heard)
    names = [n for _, n in line]
    runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    h.check(name, "EMERGENCY" not in names, f"no emergency files while the line was cut: {runs}")
    starts = [t for t, n in line if n == "time"]
    # 1 s: the recording's own clock is only that exact (as in fit_before_hour)
    h.check(name, bool(starts) and abs(starts[0] - due) <= 1.0,
            f"time check heard {starts[0] - due:+.2f} s from its second" if starts else "time check never aired")
    h.check(name, runs.count("time") == 1, f"time check aired once: {runs}")
    songs = [n for n in runs if n.startswith("song")]
    def follows_plan(seq: list[str]) -> bool:
        return any(planned[i:i + len(seq)] == seq for i in range(len(planned)))
    # The first song heard was already on air (or already with the engine) at the cut
    in_order = len(songs) >= 3 and (follows_plan(songs) or follows_plan(songs[1:]))
    h.check(name, in_order, f"songs carried on in the plan's order, none twice: {songs} (plan {planned[:6]})")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s over {back - cut:.0f} s offline")
    after = [n for _, n in timeline(h.heard(due + 7, back - 1))]
    h.check(name, bool(after) and after[0].startswith("song"), f"music after the time check: {after[:3]}")
    # Line back: kept start reports reach the site, and the time check is not aired again
    try:
        wait_for(lambda: relay.status()["online"] and relay.status()["reports_kept"] == 0, 40, "reports sent")
    except Exception as e:
        h.check(name, False, f"start reports not delivered: {e!r} {relay.status()}")
    await asyncio.sleep(20)
    times = await h.started_times("time", cut)
    h.check(name, len(times) == 1, f"the site recorded the time check as aired: {times}")
    # Recorded when it aired (the kept report carries its time), not when the line came back
    h.check(name, bool(times) and abs(times[0][0] - due) <= 2.0,
            f"the site has it at its real time ({times[0][0] - due:+.1f} s from due, "
            f"reported {back - due:.0f} s later)" if times else "no time recorded")
    late = [n for _, n in timeline(h.heard(back, time.time() - 1))]
    h.check(name, "time" not in late and "EMERGENCY" not in late, f"after the return: {sorted(set(late))}")
    st = await h.on_air()
    h.check(name, st is not None and st.asset.asset_type == "music",
            f"the site shows a song on air again ({st.asset.title if st else None})")


async def sc_control_endpoints(h: Harness) -> None:
    """B-MODE-25: control endpoints refuse a wrong or missing token; /timed with the same
    entry id twice airs it once (an id is remembered, the last 50)."""
    name = "control_endpoints"
    await wait_music(h)
    for path in ("/skip", "/flush", "/timed", "/overlay", "/status"):
        code = _control(path, {}, token="wrong")
        h.check(name, code == 403, f"{path} with a wrong token -> {code}")
    h.check(name, _control("/status", {}, token="") == 403, "no token -> 403")
    entry = str(uuid.uuid4())
    uri = (f'annotate:kbr_entry="{entry}",kbr_lane="timed",title="dup":'
           f"http://127.0.0.1:{AUDIO_PORT}/time.mp3")
    due = time.time() + 12
    codes = [_control("/timed", {"uri": uri, "at": due, "id": entry}) for _ in range(2)]
    h.check(name, codes == [200, 200], f"/timed twice answered {codes}")
    await asyncio.sleep(12 + 6 + 10)
    heard = h.heard(due - 3, due + 20)
    h.print_timeline(heard, due)
    names = [n for _, n in timeline(heard)]
    runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    h.check(name, runs.count("time") == 1, f"the item handed twice aired once: {runs}")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")


async def sc_engine_starts_backend_down(h: Harness) -> None:
    """V1-1 / B-MODE-16: the engine (re)starts while the backend cannot be reached. Its
    /hello fails, it still starts and plays its emergency files (no silence); when the
    backend is back the next /next works without another restart."""
    name = "engine_starts_backend_down"
    await wait_music(h)
    # A time check the backend hands over before the restart: the restarted engine no
    # longer holds it, so the backend must hand it over again once it hears from it
    # 60 s ahead: the engine's restart takes 5 to 15 s and the backend stays unreachable
    # for 20 s after it, which must still leave the backend 15 s to hand the item over again
    due = at(60)
    await h.queue([("time", {"preempt_at": due, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(6)
    h.stack.backend_down = True
    try:
        h.stack.restart_engine()
        started = h.stack.wav_t0
        await asyncio.sleep(20)
        down = h.heard(started + 2, time.time() - 1)
    finally:
        h.stack.backend_down = False
    back_at = time.time()
    await asyncio.sleep(30)
    heard = h.heard(started + 2, time.time() - 1)
    h.print_timeline(heard, back_at)
    names = sorted({n for _, n in timeline(down)})
    h.check(name, "EMERGENCY" in names, f"emergency files while the backend is down after the restart: {names}")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s from 2 s after the start")
    tail = [n for _, n in timeline(h.heard(time.time() - 6, time.time() - 1))]
    h.check(name, bool(tail) and tail[-1].startswith("song"),
            f"station music within 30 s of the backend returning, without a restart: {tail}")
    st = await h.on_air()
    h.check(name, st is not None and st.started_at is not None and st.started_at.timestamp() >= back_at - 1,
            f"the site shows the item that started after the return ({st.asset.title if st else None})")
    playing = [e for e in await h.rows() if e.status == "playing"]
    h.check(name, len(playing) == 1, f"one item on air ({len(playing)})")
    # The backend keeps one connection to the engine open (PR 212); the restart killed
    # it. The first command after it must still arrive: Next changes the song
    took = await _next_changes_song(h)
    h.check(name, took is not None and took <= 4.0,
            f"Next right after the restart: {'no change' if took is None else f'{took:.1f} s'}")
    await asyncio.sleep(max(0, due.timestamp() - time.time()) + 10)
    starts = await h.started_times("time", due.timestamp() - 5)
    late = starts[0][0] - due.timestamp() if starts else None
    h.check(name, late is not None and abs(late) <= 2.0,
            f"time check handed over before the restart still aired on time: due {_fmt(due.timestamp())}, "
            f"{'never' if late is None else f'{late:+.1f} s'}")


async def sc_slow_timed_file(h: Harness) -> None:
    """V1-8: the time check's file takes longer to arrive than its lead (it is handed over
    about 20 s ahead and its download takes 23 s). It must still air as soon as it is
    there, not wait for the next group, and the music must not fall silent meanwhile."""
    name = "slow_timed_file"
    await wait_music(h)
    due = at(20)
    h.stack.slow_files["time.mp3"] = 23.0
    try:
        await h.queue([("time", {"preempt_at": due, "timing_mode": "hard", "source": "auto"})])
        await asyncio.sleep(max(0, due.timestamp() - time.time()) + 20)
    finally:
        h.stack.slow_files.pop("time.mp3", None)
    starts = await h.started_times("time", due.timestamp() - 5)
    late = starts[0][0] - due.timestamp() if starts else None
    h.check(name, late is not None and late <= 8.0,
            f"slow time check aired once its file arrived: due {_fmt(due.timestamp())}, "
            f"{'never' if late is None else f'{late:+.1f} s'}")
    heard = h.heard(due.timestamp() - 3, due.timestamp() + 18)
    h.print_timeline(heard, due.timestamp())
    names = [n for _, n in timeline(heard)]
    h.check(name, "time" in names, f"the time check was heard: {names}")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")


async def sc_queue_change_drops_preloaded(h: Harness) -> None:
    """B-MODE-7 / V1-2: the engine pre-loads the next song as soon as one starts. When the
    queue changes (operator insert, redraw) the backend flushes it; the song that plays next
    must be the new head of the queue, not the one pre-loaded before the change."""
    name = "queue_change_drops_preloaded"
    await wait_music(h)
    await asyncio.sleep(12)  # long enough for the next song to be pre-loaded
    cur = await h.on_air()
    await h.clear_pending()  # deletes the old queue and flushes the engine
    new_head = next(n for n in ("song6", "song5", "song4") if h.assets[n] != cur.asset_id)
    await h.queue([(new_head, {}), ("song3", {}) if new_head != "song3" else ("song2", {})])
    song_end = cur.started_at.timestamp() + (cur.asset.duration or 40)
    await asyncio.sleep(max(0, song_end - time.time()) + 8)
    nxt = await h.on_air()
    h.check(name, bool(nxt) and nxt.asset_id == h.assets[new_head],
            f"after the change the new head aired ({new_head}); on air: {nxt.asset.title if nxt else None}")
    tl = [n for _, n in timeline(h.heard(song_end + 2, song_end + 6))]
    h.check(name, bool(tl) and tl[-1] == new_head, f"and is what is heard: {tl}")


def _mean_freq(windows) -> float | None:
    loud = [f for _, db, f in windows if db > -30]
    return sum(loud) / len(loud) if loud else None


async def sc_resume_position(h: Harness) -> None:
    """V1-5: a shiur paused for the hour's time check resumes where it stopped (3 s
    rewound), not from the top and not further on. The shiur's pitch rises with time, so
    the pitch heard after the resume gives the position actually played."""
    name = "resume_position"
    await h.set_config(timed_during_long_items="pause")
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("chirp", {}), ("song5", {}), ("song6", {})])
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=fade")
    start = await h.wait_on_air("chirp", 60)
    if start is None:
        h.check(name, False, "the shiur never went on air")
        return
    first = _mean_freq(h.heard(start + 1.0, start + 2.0))
    h.check(name, first is not None and abs(chirp_position(first) - 1.5) <= 3.0,
            f"the shiur starts at its beginning (heard position {chirp_position(first) if first else None:.1f} s at 1.5 s)")
    t1 = at(40)
    await h.queue([("time", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"}),
                   ("weather", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(max(0, t1.timestamp() - time.time()) + 6 + 8 + 10)
    # A resumed entry's started_at is back-dated by its offset (the progress bar shows the
    # position): the real start is started_at + offset
    starts = [(t + float(src.split("@", 1)[1]), src) for t, src in await h.started_times("chirp", start + 1)
              if src.startswith("resume@")]
    h.check(name, bool(starts) and starts[0][0] >= t1.timestamp(),
            f"the shiur resumed after the time check ({[(_fmt(t), s) for t, s in starts]})")
    if not starts:
        return
    resumed_at, source = starts[0]
    offset = float(source.split("@", 1)[1])
    stopped = t1.timestamp() - start
    h.check(name, abs(offset - (stopped - 3.0)) <= 2.0,
            f"resume offset {offset:.0f} s for a shiur stopped at {stopped:.1f} s (3 s rewound)")
    heard = _mean_freq(h.heard(resumed_at + 1.5, resumed_at + 2.5))
    pos = chirp_position(heard) if heard else None
    h.check(name, pos is not None and abs(pos - (offset + 2.0)) <= 3.0,
            f"the engine played from the offset: heard position {pos if pos is None else round(pos, 1)} s, "
            f"expected about {offset + 2.0:.0f} s")


async def sc_cue_points(h: Harness) -> None:
    """V1-5: the engine honours the analysed cue points: an item with cue_in 10 s and
    cue_out 40 s starts 10 s into its file and ends at 40 s (the next song follows then)."""
    name = "cue_points"
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("chirpcue", {}), ("song5", {}), ("song6", {})])
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=fade")
    start = await h.wait_on_air("chirpcue", 60)
    if start is None:
        h.check(name, False, "the item never went on air")
        return
    f = _mean_freq(h.heard(start + 1.0, start + 2.0))
    pos = chirp_position(f) if f else None
    h.check(name, pos is not None and abs(pos - 11.5) <= 3.0,
            f"starts at cue_in: heard position {pos if pos is None else round(pos, 1)} s 1.5 s in (expected 11.5)")
    nxt = await h.wait_on_air("song5", 60)
    length = (nxt - start) if nxt else None
    h.check(name, length is not None and 25.0 <= length <= 33.0,
            f"ends at cue_out: next item {length if length is None else round(length, 1)} s after the start "
            "(30 s between the cue points, crossfade 3 s)")


def _icecast_source() -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{ICECAST_PORT}/status-json.xsl", timeout=4) as r:
        src = json.loads(r.read().decode("utf-8", "replace")).get("icestats", {}).get("source", {})
    if isinstance(src, list):
        src = next((x for x in src if str(x.get("listenurl", "")).endswith("/harness")), src[0] if src else {})
    return src or {}


async def sc_icecast_output(h: Harness) -> None:
    """V1-7: the stream is 320 kbps / 44.1 kHz stereo MP3 with the station's name, genre and
    url, and listeners see the title of the item on air."""
    name = "icecast_output"
    await wait_music(h)
    await asyncio.sleep(3)
    src = _icecast_source()
    h.check(name, str(src.get("server_type", "")) == "audio/mpeg", f"MP3 stream ({src.get('server_type')})")
    br = src.get("bitrate") or src.get("audio_bitrate") or src.get("ice-bitrate")
    h.check(name, str(br).startswith("320"), f"320 kbps ({br})")
    # What a listener actually receives: a few seconds of the stream through ffprobe
    sample = h.stack.dir / "stream-sample.mp3"
    with urllib.request.urlopen(f"http://127.0.0.1:{ICECAST_PORT}/harness", timeout=10) as r:
        sample.write_bytes(r.read(200_000))
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=codec_name,sample_rate,channels,bit_rate", "-of", "json", str(sample)],
                           capture_output=True, text=True)
    st = (json.loads(probe.stdout or "{}").get("streams") or [{}])[0]
    h.check(name, st.get("codec_name") == "mp3" and st.get("sample_rate") == "44100"
            and st.get("channels") == 2 and str(st.get("bit_rate", "")).startswith("320"),
            f"listeners get MP3 44.1 kHz stereo 320 kbps: {st}")
    h.check(name, src.get("server_name") == "Kol B'ramah" and src.get("genre") == "Jewish Radio"
            and src.get("server_url") == "https://kbrlive.com",
            f"name/genre/url: {src.get('server_name')!r} / {src.get('genre')!r} / {src.get('server_url')!r}")
    cur = await h.on_air()
    title = str(src.get("title") or "")
    h.check(name, bool(cur) and (cur.asset.title or "") in title,
            f"stream title shows the item on air: {title!r} (site: {cur.asset.title if cur else None})")
    # The iPhone copy: the same encode cut into 1 s segments, each starting with the
    # timestamp tag Apple's packed-audio HLS needs and then a whole MP3 frame
    playlist = h.stack.hls / "mp3.m3u8"
    lines = playlist.read_text().splitlines() if playlist.exists() else []
    segs = [ln for ln in lines if ln.endswith(".mp3")]
    h.check(name, len(segs) >= 5 and "#EXT-X-TARGETDURATION:1" in lines,
            f"HLS playlist lists 1 s segments ({len(segs)} listed)")
    if segs:
        seg = h.stack.hls / segs[-2 if len(segs) > 1 else -1]
        head = seg.read_bytes()[:200]
        tag = b"com.apple.streaming.transportStreamTimestamp"
        at = head.find(tag)
        frame = head[at + len(tag) + 9:at + len(tag) + 11] if at >= 0 else b""
        h.check(name, head[:3] == b"ID3" and at >= 0, "HLS segment starts with Apple's timestamp tag")
        h.check(name, len(frame) == 2 and frame[0] == 0xFF and frame[1] & 0xE0 == 0xE0,
                f"HLS segment's audio starts on an MP3 frame ({frame.hex()})")
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                                "stream=codec_name,sample_rate,channels,bit_rate", "-of", "json", str(seg)],
                               capture_output=True, text=True)
        st = (json.loads(probe.stdout or "{}").get("streams") or [{}])[0]
        h.check(name, st.get("codec_name") == "mp3" and st.get("sample_rate") == "44100"
                and st.get("channels") == 2 and str(st.get("bit_rate", "")).startswith("320"),
                f"HLS segment is MP3 44.1 kHz stereo 320 kbps: {st}")


def _mean_db(windows) -> float | None:
    vals = [db for _, db, _ in windows if db > -60]
    return sum(vals) / len(vals) if vals else None


async def sc_track_level(h: Harness) -> None:
    """V1-4: the per-track level from the analysis (replay_gain_db, sent as liq_amplify) is
    applied: the same file with -6 dB plays 6 dB quieter; an item without one at unity."""
    name = "track_level"
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("song3", {}), ("song3quiet", {}), ("song5", {})])
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=fade")
    a = await h.wait_on_air("song3", 60)
    b = await h.wait_on_air("song3quiet", 60)
    if a is None or b is None:
        h.check(name, False, f"items never went on air ({a}, {b})")
        return
    await asyncio.sleep(12)
    loud = _mean_db(h.heard(a + 8, a + 18))
    quiet = _mean_db(h.heard(b + 4, b + 10))
    diff = (loud - quiet) if loud is not None and quiet is not None else None
    if diff is None or not 4.5 <= diff <= 7.5:
        for label, t0 in (("unity", a), ("-6 dB", b)):
            ws = h.heard(t0 - 1, t0 + 20)
            log(f"    {label} item from its start: " + " ".join(
                f"{t - t0:+.1f}:{db:.0f}dB/{f:.0f}Hz" for t, db, f in ws[::5]))
    h.check(name, diff is not None and 4.5 <= diff <= 7.5,
            f"-6 dB applied: unity {loud if loud is None else round(loud, 1)} dBFS, "
            f"-6 dB item {quiet if quiet is None else round(quiet, 1)} dBFS (difference "
            f"{diff if diff is None else round(diff, 1)} dB)")


def wav_peak_db(path: Path, t0: float, start: float, end: float) -> float:
    """Highest sample (dBFS) between start and end."""
    import math
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        first = max(0, int((start - t0) * rate))
        last = min(w.getnframes(), int((end - t0) * rate))
        w.setpos(first)
        frames = array("h", w.readframes(last - first))
    peak = max((abs(x) for x in frames), default=0)
    return 20 * math.log10(peak / 32768) if peak else -120.0


async def sc_output_limiter(h: Harness) -> None:
    """V1-7: nothing leaves the engine above -3 dBFS (sample peak), so the MP3 stream's
    true peaks stay under -2 dBFS, even for a full-scale master given +6 dB (Jerusalem
    2026-11-17: true peaks of -0.6 dBFS with limit(threshold=-2)). The stream keeps its
    titles through the FFmpeg limiter."""
    name = "output_limiter"
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("loud", {}), ("song5", {}), ("song6", {})])
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=fade")
    a = await h.wait_on_air("loud", 60)
    if a is None:
        h.check(name, False, "the loud item never went on air")
        return
    await asyncio.sleep(5)
    # What a listener receives while it plays: its true peak after MP3 encoding
    sample = h.stack.dir / "loud-sample.mp3"

    def grab() -> bytes:
        with urllib.request.urlopen(f"http://127.0.0.1:{ICECAST_PORT}/harness", timeout=15) as r:
            return r.read(450_000)

    data = await asyncio.to_thread(grab)
    sample.write_bytes(data[180_000:])  # past the burst of older audio sent on connect
    meter = subprocess.run(["ffmpeg", "-nostats", "-i", str(sample), "-af", "ebur128=peak=true",
                            "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", meter.stderr)
    true_peak = float(m[-1]) if m else None
    await asyncio.sleep(6)
    peak = wav_peak_db(h.stack.wav, h.stack.wav_t0, a + 4, a + 15)
    level = _mean_db(h.heard(a + 4, a + 15))
    h.check(name, peak <= -2.9, f"sample peak {peak:.2f} dBFS (limit -3)")
    h.check(name, true_peak is not None and true_peak <= -2.0, f"stream true peak {true_peak} dBFS")
    h.check(name, level is not None and level > -12, f"still loud, not crushed: {level} dBFS")
    title = str(_icecast_source().get("title") or "")
    h.check(name, "loud master" in title, f"stream title after the limiter: {title!r}")


async def sc_level_song_muted(h: Harness) -> None:
    """V1-4b: a song with a level of its own (liq_amplify) is still muted under a hard time
    check: the mute and fade gains are not replaced by the track's level."""
    name = "song_with_level_muted_under_time_check"
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("song3quiet", {}), ("song5", {}), ("song6", {})])
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=fade")
    b = await h.wait_on_air("song3quiet", 60)
    if b is None:
        h.check(name, False, "song3quiet never went on air")
        return
    t1 = at(12)
    await h.queue([("time", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(max(0, t1.timestamp() - time.time()) + TONES["time"][1] + 3)
    time.sleep(1.5)
    song_hz = TONES["song3"][0]
    before = tone_db(h.stack.wav, h.stack.wav_t0, t1.timestamp() - 6, t1.timestamp() - 2, song_hz)
    under = tone_db(h.stack.wav, h.stack.wav_t0, t1.timestamp() + 1, t1.timestamp() + 5, song_hz)
    clock = tone_db(h.stack.wav, h.stack.wav_t0, t1.timestamp() + 1, t1.timestamp() + 5, TONES["time"][0])
    h.check(name, clock > -40, f"time check heard ({clock:.1f} dBFS)")
    h.check(name, before > -45 and under < before - 30,
            f"song at {before:.1f} dBFS before, {under:.1f} dBFS under the time check")


async def sc_lane_levels(h: Harness) -> None:
    """V1-4: the per-track level applies on the timed lane and on mix-over too, once.
    A file with -6 dB plays at about -30.5 dBFS (unity tones play at -24.5)."""
    name = "timed_and_mix_over_levels"
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("song5", {}), ("song6", {}), ("song4", {})])
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=fade")
    s5 = await h.wait_on_air("song5", 60)
    if s5 is None:
        h.check(name, False, "song5 never went on air")
        return
    w, t0 = h.stack.wav, h.stack.wav_t0
    # Mix-over: song1's file at -6 dB on top of song5, which carries on
    t_mix = time.time()
    code, body = h.api("POST", f"/stations/{STATION_ID}/queue/mix-over",
                       {"asset_id": str(h.assets["song1quiet"])})
    h.check(name, code == 200, f"mix-over answered {code} {body if code != 200 else ''}")
    # Timed lane: song3's file at -6 dB as a hard timed item, 12 s later
    t1 = at(12)
    await h.queue([("song3quiet", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(max(0, t1.timestamp() - time.time()) + 9)
    time.sleep(1.5)
    over = tone_db(w, t0, t_mix + 3, t_mix + 8, TONES["song1"][0])
    under = tone_db(w, t0, t_mix + 3, t_mix + 8, TONES["song5"][0])
    h.check(name, -32.5 <= over <= -28.5, f"mix-over item at {over:.1f} dBFS (expected about -30.5)")
    h.check(name, under > -27, f"song on air carries on under it ({under:.1f} dBFS)")
    started = await h.started_times("song3quiet", t1.timestamp() - 5)
    h.check(name, bool(started), f"timed item started ({[_fmt(x[0]) for x in started]})")
    timed = tone_db(w, t0, t1.timestamp() + 2, t1.timestamp() + 7, TONES["song3"][0])
    h.check(name, -32.5 <= timed <= -28.5, f"timed item at {timed:.1f} dBFS (expected about -30.5)")
    # The 40 s timed item would still be on air for the next scenario, whose own timed
    # item would then be moved to follow it (the site leaves a timed item alone on Next)
    await asyncio.to_thread(_control, "/skip", {"fade": 0.1})
    await asyncio.sleep(2)

async def _next_changes_song(h: Harness, limit: float = 8.0) -> float | None:
    """Press Next on the site; seconds until another item is on air (None = never)."""
    before = await h.on_air()
    t0 = time.time()
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
    while time.time() - t0 < limit:
        cur = await h.on_air()
        if cur is not None and (before is None or cur.id != before.id):
            return time.time() - t0
        await asyncio.sleep(0.1)
    return None


async def sc_next_after_idle(h: Harness) -> None:
    """PR 212: the backend's kept connection to the engine expires after 30 s without
    use. A Next after 35 s with no operator command still changes the song at once."""
    name = "next_after_idle"
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("story", {}), ("song5", {}), ("song6", {})])
    took = await _next_changes_song(h)
    h.check(name, took is not None, "story on air")
    await asyncio.sleep(35)
    took = await _next_changes_song(h)
    h.check(name, took is not None and took <= 3.0,
            f"Next after 35 s idle: {'no change' if took is None else f'{took:.1f} s'}")


async def sc_next_twice(h: Harness) -> None:
    """A second Next shortly after the first also changes the item (an operator stepping
    through items). Found 2026-11-17: 0.3 s after a Next the second one answered
    "Skipped" and nothing changed."""
    name = "next_twice"
    for gap in (5.0, 3.5, 2.0, 0.5):
        await wait_music(h)
        await h.clear_pending()
        await h.queue([("song2", {}), ("song3", {}), ("song4", {}), ("song5", {})])
        await asyncio.sleep(3)
        first = await _next_changes_song(h)
        await asyncio.sleep(gap)
        second = await _next_changes_song(h)
        h.check(name, first is not None and second is not None and second <= 3.0,
                f"second Next {gap} s after the first changed the item: "
                f"{'no change in 8 s' if second is None else f'{second:.1f} s'}")


async def sc_next_twice_slow_file(h: Harness) -> None:
    """V3-52k: Next, then Next again 1 s later while the engine is still waiting for the
    next item's file (5 s to arrive). An impatient operator pressing twice gets one
    skip: the slow item airs and stays, the item after it is not skipped to, and the
    song on air plays on meanwhile (no emergency music, no silence over 1 s)."""
    name = "next_twice_slow_file"
    await wait_music(h)
    await asyncio.sleep(5)
    await h.clear_pending()
    await asyncio.sleep(1.5)
    h.stack.slow_files["story.mp3"] = 5.0
    t0 = time.time()
    try:
        await h.queue([("story", {}), ("song2", {}), ("song3", {})])
        await asyncio.sleep(0.5)
        base = f"/stations/{STATION_ID}/queue"
        pressed = time.time()
        h.api("POST", f"{base}/skip?fade=hard")
        await asyncio.sleep(1.0)
        code, _ = h.api("POST", f"{base}/skip?fade=hard")
        h.check(name, code < 500, f"the second Next is answered without a server error ({code})")
        t = await h.wait_on_air("story", 20)
    finally:
        h.stack.slow_files.pop("story.mp3", None)
    h.check(name, t is not None,
            f"the slow item went on air ({'never' if t is None else f'{t - pressed:.1f} s after the first press'})")
    await asyncio.sleep(6)
    cur = await h.on_air()
    h.check(name, cur is not None and cur.asset_id == h.assets["story"],
            f"and stays on air 6 s later: one skip, not two (site shows {cur.asset.title if cur else None})")
    heard = h.heard(t0 - 2, time.time() - 1)
    h.print_timeline(heard, pressed)
    tl = timeline(heard)
    names = [n for _, n in tl]
    after = [n for ts, n in tl if ts > pressed]
    h.check(name, tone_name(TONES["song2"][0]) not in after and tone_name(TONES["song3"][0]) not in after,
            f"the items after the slow one were not heard: {after}")
    h.check(name, tone_name(TONES["story"][0]) in after, f"the slow item was heard: {after}")
    h.check(name, "EMERGENCY" not in names, f"no emergency music while the file loads: {names}")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"no silence over 1 s (longest {gap:.1f} s)")


async def sc_next_skips_one(h: Harness) -> None:
    """PR 224: every Next moves on by exactly one item: single, twice 0.5 s apart, within
    2 s of a natural song change, and with nothing queued. The item after the skipped one
    airs, never the one after that."""
    name = "next_skips_one"
    await wait_music(h)
    # Not in the second of a song change: the refill that runs then is still writing, and
    # this test's direct queue writes landed among its songs (2026-11-18, run right after
    # next_twice's last Next: automatic songs aired between the queued ones)
    await asyncio.sleep(5)
    await h.clear_pending()
    await asyncio.sleep(1.5)
    order = ["song2", "song3", "song4", "song5", "song6", "story", "shiur"]
    ids = await h.queue([(n, {}) for n in order])
    await asyncio.sleep(3)
    t_start = time.time()

    async def press_expect(expect: str, why: str) -> None:
        h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
        t = await h.wait_on_air(expect, 6)
        cur = await h.on_air()
        h.check(name, t is not None,
                f"{why}: {expect} on air (site shows {cur.asset.title if cur else None})")

    await press_expect("song2", "Next")
    await asyncio.sleep(1.0)
    await press_expect("song3", "Next 1 s later")
    await asyncio.sleep(5)
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
    await asyncio.sleep(0.5)
    await press_expect("song5", "two Nexts 0.5 s apart move on two items")
    await asyncio.sleep(4)
    cur = await h.on_air()
    h.check(name, cur is not None and cur.asset_id == h.assets["song5"],
            f"and no further: still {cur.asset.title if cur else None} 4 s later")
    t6 = await h.wait_on_air("song6", 60)
    h.check(name, t6 is not None, "song5 ended by itself and song6 followed")
    await asyncio.sleep(max(0, (t6 or time.time()) + 1.5 - time.time()))
    await press_expect("story", "Next 1.5 s after a natural song change")
    await asyncio.sleep(4)
    cur = await h.on_air()
    h.check(name, cur is not None and cur.asset_id == h.assets["story"],
            f"and no further: still {cur.asset.title if cur else None} 4 s later")
    last = next((e.status for e in await h.rows() if e.id == ids[-1]), None)
    h.check(name, last == "pending", f"the item after it was not touched (shiur {last})")
    # nothing queued: Next still changes the item, once, with no silence
    await h.clear_pending()
    await asyncio.sleep(3)
    before = await h.on_air()
    took = await _next_changes_song(h)
    h.check(name, took is not None and took <= 3.0,
            f"Next with nothing queued: {'no change' if took is None else f'{took:.1f} s'}")
    after = await h.on_air()
    await asyncio.sleep(6)
    later = await h.on_air()
    h.check(name, after is not None and later is not None and after.id == later.id and after.id != before.id,
            "and it moved on once only")
    heard = h.heard(t_start, time.time() - 1)
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence in the whole run {gap:.1f} s")
    names = [n for _, n in timeline(heard)]
    h.check(name, "EMERGENCY" not in names, "no emergency files")


async def sc_next_right_after_change(h: Harness) -> None:
    """A Next pressed a moment after an item started (0.1 to 1 s after a natural song change,
    and 0.1 / 0.3 s after another Next) still brings the next item. Found 2026-11-17: a
    Next 0.2 s after a song start, while its crossfade ran, changed nothing."""
    name = "next_right_after_change"
    for delay in (0.1, 0.3, 0.6, 1.0):
        await wait_music(h)
        await h.clear_pending()
        await h.queue([("song2", {}), ("song3", {}), ("song4", {})])
        # 16 s: an automatic flush is held back 15 s after the last one, and until it
        # happens the engine still holds a song from before the queue was replaced
        await asyncio.sleep(16)
        h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
        if await h.wait_on_air("song2", 8) is None:
            h.check(name, False, f"[{delay}] song2 never went on air")
            continue
        t3 = await h.wait_on_air("song3", 60)   # natural change, 3 s crossfade
        if t3 is None:
            h.check(name, False, f"[{delay}] song3 did not follow by itself")
            continue
        await asyncio.sleep(max(0, t3 + delay - time.time()))
        h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
        t4 = await h.wait_on_air("song4", 8)
        h.check(name, t4 is not None,
                f"Next {delay} s after a natural song change: "
                f"{'no change in 8 s' if t4 is None else f'song4 after {t4 - t3 - delay:.1f} s'}")
    for delay in (0.1, 0.3):
        await wait_music(h)
        await h.clear_pending()
        await h.queue([("song2", {}), ("song3", {}), ("song4", {})])
        await asyncio.sleep(5)
        h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
        await asyncio.sleep(delay)
        h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
        t = await h.wait_on_air("song3", 8)
        h.check(name, t is not None,
                f"two Nexts {delay} s apart: {'song3 never on air' if t is None else 'song3 on air'}")
        await asyncio.sleep(4)
        cur = await h.on_air()
        h.check(name, cur is not None and cur.asset_id == h.assets["song3"],
                f"and no further ({cur.asset.title if cur else None})")


async def sc_site_matches_stream(h: Harness) -> None:
    """F-HANDOVER-14: on a stream-server station the site's "now playing" follows what the
    engine really plays. At natural song changes (3 s crossfade) and at a hard time check
    (no crossfade) the time the site shows an item starting is within 3 s of the moment it
    is the loudest thing on the stream, and never after it by more than 1 s."""
    name = "site_matches_stream"
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("song2", {}), ("song3", {}), ("song4", {}), ("song5", {})])
    h.api("POST", f"/stations/{STATION_ID}/queue/skip?fade=hard")
    t0 = await h.wait_on_air("song2", 60)  # the engine may first play the song it held
    if t0 is None:
        h.check(name, False, "song2 never went on air")
        return
    due = datetime.fromtimestamp(t0 + 95, UTC)
    await h.queue([("time", {"preempt_at": due, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(max(0, due.timestamp() - time.time()) + 14)
    heard = h.heard(t0 + 3, time.time() - 1)
    h.print_timeline(heard, due.timestamp())
    tl = timeline(heard)
    worst = 0.0
    for item in ("song3", "song4", "time"):
        site = await h.started_times(item, t0 + 5)  # not the song that was on air before
        aud = next((t for t, n in tl if n == tone_name(TONES[item][0])), None)
        if not site or aud is None:
            h.check(name, False, f"{item}: site {'no start' if not site else 'ok'}, stream {'not heard' if aud is None else 'ok'}")
            continue
        diff = aud - site[0][0]
        worst = max(worst, abs(diff))
        limit = 1.5 if item == "time" else 3.0
        h.check(name, -1.0 <= diff <= limit,
                f"{item}: heard as the loudest item {diff:+.1f} s after the site showed it start (limit {limit} s)")
    playing = [e for e in await h.rows() if e.status == "playing"]
    h.check(name, len(playing) == 1, f"one item on air on the site ({len(playing)})")


async def sc_second_engine_refused(h: Harness) -> None:
    """One engine per station (local FM box safety): a second engine started with the same
    station id (a box set up with a copied id) is refused at hello and at every ask, takes
    nothing from the queue, and a critical alert is raised; the station's own engine plays
    on without a gap and the site keeps showing what it plays."""
    from sqlalchemy import select

    from app.db.engine import async_session_factory
    from app.models.alert import Alert
    from app.services import playout_engine

    name = "second_engine_refused"
    await wait_music(h)
    t_start = time.time()
    second_dir = h.stack.dir / "second"
    (second_dir / "emergency").mkdir(parents=True, exist_ok=True)
    os.chmod(second_dir, 0o777)
    os.chmod(second_dir / "emergency", 0o777)
    env = dict(os.environ)
    env.update({
        "KBR_BACKEND_URL": f"http://127.0.0.1:{BACKEND_PROXY_PORT}/api/v1",
        "KBR_STATION_ID": str(STATION_ID), "KBR_PLAYOUT_TOKEN": TOKEN,
        "KBR_CONTROL_PORT": str(CONTROL_PORT + 1),
        "KBR_EMERGENCY_DIR": str(second_dir / "emergency"),
        "KBR_ICECAST_PORT": str(ICECAST_PORT), "KBR_ICECAST_SOURCE_PASSWORD": TOKEN,
        "KBR_ICECAST_MOUNT": "/harness-second", "KBR_HLS_DIR": str(second_dir),
        "KBR_DEBUG_WAV": "", "KBR_ALSA_DEVICE": "",
    })
    log_path = second_dir / "engine.log"
    cmd = ["liquidsoap", str(RADIO_LIQ)]
    if os.geteuid() == 0:
        cmd = ["runuser", "-u", "nobody", "--"] + cmd
    p = subprocess.Popen(cmd, env=env, stdout=open(log_path, "a"), stderr=subprocess.STDOUT,
                         start_new_session=True)
    try:
        await asyncio.sleep(25)
    finally:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=10)
        except Exception:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except Exception:
                pass
    log = log_path.read_text(errors="replace")
    h.check(name, "hello: REFUSED" in log, "the second engine's hello was refused")
    h.check(name, "next: backend gave entry" not in log, "the second engine got nothing from the queue")
    async with async_session_factory() as db:
        alerts = (await db.execute(select(Alert).where(
            Alert.station_id == STATION_ID, Alert.title == playout_engine.SECOND_ENGINE_ALERT_TITLE,
        ))).scalars().all()
    h.check(name, len(alerts) >= 1, f"a critical alert was raised ({len(alerts)})")
    heard = h.heard(t_start, time.time() - 1)
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"the station's own engine played on: longest silence {gap:.1f} s")
    names = [n for _, n in timeline(heard) if n != "silence"]
    h.check(name, bool(names) and all(n.startswith("song") for n in names),
            f"station music only on the station's engine: {names}")
    st = await h.on_air()
    def engine_status() -> dict | None:
        req = urllib.request.Request(f"http://127.0.0.1:{CONTROL_PORT}/status", method="POST", data=b"{}",
                                     headers={"X-Playout-Token": TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read())
        except Exception:
            return None

    status = await asyncio.to_thread(engine_status)
    h.check(name, st is not None and status is not None and status.get("entry_id") == str(st.id),
            "the site shows what the station's engine plays")


async def sc_engine_crash_restart(h: Harness) -> None:
    """V1-25 / H-10: the engine crashes mid-song and starts again (systemd restarts it) with
    the backend up. It says hello, plays station music (not emergency files) within a few
    seconds, the site follows it, and a time check handed over before the crash still airs
    on time."""
    name = "engine_crash_restart"
    await wait_music(h)
    due = at(40)
    await h.queue([("time", {"preempt_at": due, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(6)
    before = await h.on_air()
    h.stack.restart_engine()
    started = h.stack.wav_t0
    await asyncio.sleep(15)
    heard = h.heard(started, time.time() - 1)
    first = next((t for t, db, _ in heard if db > -45), None)
    h.check(name, first is not None and first - started <= 5.0,
            f"sound {'never' if first is None else f'{first - started:.1f} s'} after the restart")
    # From the start: it used to open with about 2 s of an emergency song before the
    # queue's first item (both stations at the 08:23Z rerun, 2026-11-18)
    names = [n for _, n in timeline(heard) if n != "silence"]
    h.check(name, bool(names) and all(n.startswith("song") for n in names),
            f"station music after the restart, no emergency files: {names}")
    st = await h.on_air()
    h.check(name, st is not None and st.started_at is not None and st.started_at.timestamp() >= started - 1,
            f"the site shows the item the restarted engine plays ({st.asset.title if st else None}; "
            f"before the crash {before.asset.title if before else None})")
    playing = [e for e in await h.rows() if e.status == "playing"]
    h.check(name, len(playing) == 1, f"one item on air ({len(playing)})")
    await asyncio.sleep(max(0, due.timestamp() - time.time()) + 10)
    starts = await h.started_times("time", due.timestamp() - 5)
    late = starts[0][0] - due.timestamp() if starts else None
    h.check(name, late is not None and abs(late) <= 2.0,
            f"time check handed over before the crash aired on time: "
            f"{'never' if late is None else f'{late:+.1f} s'}")
    gap, _ = longest_silence(h.heard(started + 6, time.time() - 1))
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s after the restart")


async def _restart_near_hour(h: Harness, name: str, what: str, lead: float, allowed: float,
                             due: datetime | None = None, sound_within: float | None = None) -> None:
    if due is None:
        await wait_music(h)
        due = at(60)
    await h.queue([("time", {"preempt_at": due, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(max(0, due.timestamp() - time.time() - lead))
    t_kill = time.time()
    if what == "engine":
        await asyncio.to_thread(h.stack.restart_engine)
        t_from = h.stack.wav_t0
    else:
        await asyncio.to_thread(h.stack.restart_backend)
        t_from = t_kill - 20
    up = time.time()
    await asyncio.sleep(max(0, due.timestamp() - time.time()) + TONES["time"][1] + 14)
    heard = h.heard(max(t_from, due.timestamp() - 20), time.time() - 1)
    h.print_timeline(heard, due.timestamp())
    clock = tone_name(TONES["time"][0])
    tl = timeline(heard)
    names = [n for _, n in tl]
    runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    first = next((t for t, n in tl if n == clock), None)
    if sound_within is not None:
        # The engine is back at the start of its new recording (wav_t0): station sound then
        back = [(t, n) for t, n in timeline(h.heard(t_from, due.timestamp())) if n != "silence"]
        h.check(name, bool(back) and back[0][0] - t_from <= sound_within and back[0][1].startswith("song"),
                f"a song as soon as the engine is back: "
                f"{'nothing before the time check' if not back else f'{back[0][1]} after {back[0][0] - t_from:.1f} s'}"
                f" (allowed {sound_within:.0f} s)")
    late = None if first is None else first - due.timestamp()
    h.check(name, late is not None and -1.0 <= late <= allowed,
            f"{what} restarted {due.timestamp() - t_kill:.0f} s before the time check and was back "
            f"{due.timestamp() - up:+.1f} s before it: time check "
            f"{'never' if late is None else f'{late:+.1f} s'} (allowed {allowed:.0f} s)")
    h.check(name, runs.count(clock) == 1, f"time check heard once: {runs}")
    h.check(name, "EMERGENCY" not in runs[runs.index(clock):] if clock in runs else False,
            f"station items after it, no emergency files: {runs}")
    gap, _ = longest_silence(h.heard(max(t_from + 6, due.timestamp() - 5), time.time() - 1))
    h.check(name, gap <= 1.5, f"longest silence {gap:.1f} s")
    playing = [e for e in await h.rows() if e.status == "playing"]
    h.check(name, len(playing) == 1, f"one item on air on the site ({len(playing)})")


async def sc_engine_restart_15s_before_hour(h: Harness) -> None:
    """V1-30: the engine restarts 15 s before a hard time check that was already handed over.
    The backend hands it over again and it airs once, on its second."""
    await _restart_near_hour(h, "engine_restart_15s_before_hour", "engine", 15, 2.0)


async def sc_engine_restart_song_ends_before_hour(h: Harness) -> None:
    """PR 308: the engine restarts 30 s before a hard time check while the song the site
    still shows as playing was due to end 4 s before it. That stale entry must not make the
    backend hold the restarted engine back (hold-before-hard-item): a song starts as soon as
    the engine is back, no emergency music meanwhile, and the time check airs once, on its
    second."""
    await wait_music(h)
    end = None
    for _ in range(90):
        cur = await h.on_air()
        if cur is not None and cur.started_at is not None:
            end = cur.started_at.timestamp() + float(cur.asset.duration or TONES["song1"][1])
            if 28.0 <= end - time.time() <= 39.0:
                break
        end = None
        await asyncio.sleep(1)
    h.check("engine_restart_song_ends_before_hour", end is not None,
            "found a song on air with 28 to 39 s left to set the time check behind")
    if end is None:
        return
    due = datetime.fromtimestamp(end + 4.0, UTC)
    await _restart_near_hour(h, "engine_restart_song_ends_before_hour", "engine", 30, 2.0, due=due,
                             sound_within=3.0)


async def sc_engine_restart_across_hour(h: Harness) -> None:
    """V1-30: the engine is down across the second of a hard time check. It airs once as soon
    as the engine is back (late, but not dropped and not twice)."""
    await _restart_near_hour(h, "engine_restart_across_hour", "engine", 3, 20.0)


async def sc_backend_restart_before_hour(h: Harness) -> None:
    """V1-31: the backend restarts (a deploy) after it handed the time check over. It hands it
    over again with the same id; the engine airs it once, on its second."""
    await _restart_near_hour(h, "backend_restart_before_hour", "backend", 30, 2.0)


async def sc_duplicate_timed_id(h: Harness) -> None:
    """H-10: the same timed item handed over twice (a retried /timed after a lost answer, a
    backend restart) airs once, on time."""
    name = "duplicate_timed_id"
    await wait_music(h)
    due = at(20)
    ids = await h.queue([("time", {"preempt_at": due, "timing_mode": "hard", "source": "auto"})])
    entry_id = str(ids[0])
    await asyncio.sleep(max(0, due.timestamp() - time.time() - 12))
    uri = (f'annotate:kbr_entry="{entry_id}",kbr_lane="timed",kbr_kind="jingle",title="time":'
           f"http://127.0.0.1:{AUDIO_PORT}/time.mp3")
    codes = [_control("/timed", {"uri": uri, "at": due.timestamp(), "id": entry_id}) for _ in range(2)]
    h.check(name, codes == [200, 200], f"/timed sent twice more: {codes}")
    await asyncio.sleep(max(0, due.timestamp() - time.time()) + TONES["time"][1] + 8)
    starts = await h.started_times("time", due.timestamp() - 5)
    late = starts[0][0] - due.timestamp() if starts else None
    h.check(name, late is not None and abs(late) <= 2.0,
            f"time check {'never' if late is None else f'{late:+.1f} s'}")
    heard = h.heard(due.timestamp() - 3, due.timestamp() + TONES["time"][1] * 3 + 4)
    names = [n for _, n in timeline(heard)]
    runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    clock = tone_name(TONES["time"][0])
    h.check(name, runs.count(clock) == 1, f"time check heard once: {runs}")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")


async def sc_blackout_ended_early(h: Harness) -> None:
    """H-10 / B-JOB: a blackout (Shabbos, Yom Tov) puts silence on air; when it is ended
    early (its end moved to now) the silence is dropped and station music is back within a
    few seconds, with no silence left queued."""
    from sqlalchemy import delete, update

    from app.db.engine import async_session_factory
    from app.models.holiday_window import HolidayWindow

    name = "blackout_ended_early"
    await wait_music(h)
    wid = uuid.uuid4()
    now = datetime.now(UTC)
    async with async_session_factory() as db:
        db.add(HolidayWindow(id=wid, name="Harness blackout", start_datetime=now - timedelta(seconds=1),
                             end_datetime=now + timedelta(minutes=10), is_blackout=True,
                             affected_stations={"station_ids": [str(STATION_ID)]}))
        await db.commit()
    try:
        silent_from = None
        for _ in range(60):
            cur = await h.on_air()
            if cur and cur.asset and cur.asset.asset_type == "silence":
                silent_from = time.time()
                break
            await asyncio.sleep(1)
        h.check(name, silent_from is not None, "silence on air once the blackout starts")
        if silent_from is None:
            return
        await asyncio.sleep(8)
        quiet = h.heard(silent_from + 2, time.time() - 1)
        loud = [t for t, db, _ in quiet if db > -45]
        h.check(name, not loud, f"nothing heard during the blackout ({len(loud)} loud windows)")
        ended = time.time()
        async with async_session_factory() as db:
            await db.execute(update(HolidayWindow).where(HolidayWindow.id == wid)
                             .values(end_datetime=datetime.now(UTC)))
            await db.commit()
        back = None
        for _ in range(40):
            cur = await h.on_air()
            if cur and cur.asset and cur.asset.asset_type == "music":
                back = time.time()
                break
            await asyncio.sleep(0.5)
        h.check(name, back is not None and back - ended <= 10.0,
                f"music on air {'never' if back is None else f'{back - ended:.1f} s'} after the blackout ended")
        await asyncio.sleep(10)
        tail = [n for _, n in timeline(h.heard(time.time() - 6, time.time() - 1))]
        h.check(name, bool(tail) and all(n.startswith("song") for n in tail), f"station music heard: {tail}")
        left = [e for e in await h.rows() if e.status in ("pending", "playing")
                and e.asset_id == h.assets["silence"]]
        h.check(name, not left, f"no silence left in the queue ({len(left)})")
    finally:
        async with async_session_factory() as db:
            await db.execute(delete(HolidayWindow).where(HolidayWindow.id == wid))
            await db.commit()


async def sc_block_with_slow_shiur(h: Harness, delay: float = 15.0) -> None:
    """Jerusalem 18:00Z 2026-11-17: a block of time check, announcement and the day's Daf
    Yomi shiur (one hard group). The shiur is a big file that takes a while to arrive. After
    the announcement the shiur must follow, not a song from the music lane."""
    name = f"block_with_slow_shiur_{delay:g}s"
    await wait_music(h)
    due = at(25)
    h.stack.slow_files["shiur.mp3"] = delay
    try:
        await h.queue([(n, {"preempt_at": due, "timing_mode": "hard", "source": "block"})
                       for n in ("time", "announce", "shiur")])
        await asyncio.sleep(max(0, due.timestamp() - time.time()) + 12 + delay + 8)
    finally:
        h.stack.slow_files.pop("shiur.mp3", None)
    starts = {n: await h.started_times(n, due.timestamp() - 5) for n in ("time", "announce", "shiur")}
    h.check(name, bool(starts["time"]) and abs(starts["time"][0][0] - due.timestamp()) <= 2.0,
            f"time check on time ({[_fmt(x[0]) for x in starts['time']]})")
    h.check(name, bool(starts["shiur"]), f"shiur aired ({[_fmt(x[0]) for x in starts['shiur']]})")
    cur = await h.on_air()
    h.check(name, bool(cur) and cur.asset_id == h.assets["shiur"],
            f"shiur on air at the end ({cur.asset.title if cur else None})")
    heard = h.heard(due.timestamp() - 3, time.time() - 1)
    h.print_timeline(heard, due.timestamp())
    names = [n for _, n in timeline(heard)]
    runs = [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n]
    after = runs[runs.index("announce") + 1:] if "announce" in runs else []
    if delay < 20:
        h.check(name, bool(after) and after[0] == "shiur", f"after the announcement: {after}; heard {runs}")
    else:
        # Its file arrives ~20 s after the announcement: a song fills in, then the shiur
        h.check(name, "shiur" in after and after[-1] == "shiur", f"after the announcement: {after}; heard {runs}")
        arrived = due.timestamp() - 25 + delay
        if starts["shiur"]:
            h.check(name, starts["shiur"][0][0] - arrived <= 3.0,
                    f"shiur on air {starts['shiur'][0][0] - arrived:+.1f} s after its file arrived")
    gap, _ = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s")
    # Next during the shiur moves on to music (and the hour-long shiur would otherwise
    # still be on air for the next scenario)
    t0 = time.time()
    try:
        code = await asyncio.to_thread(_control, "/skip", {"fade": 0.1})
    except Exception as e:  # noqa: BLE001
        code = repr(e)
    h.check(name, code == 200, f"engine answers /skip during the shiur ({code}, {time.time() - t0:.1f} s)")
    await asyncio.sleep(6)
    tail = [n for _, n in timeline(h.heard(time.time() - 3, time.time() - 0.5))]
    h.check(name, bool(tail) and all(n.startswith("song") for n in tail), f"music after Next: {tail}")


async def sc_hour_change_slow_backend(h: Harness, seconds: float = 4.0) -> None:
    """The hour change while every answer the backend gives the engine takes 4 s
    (Jerusalem 2026-11-18). Judged by what is heard, because the backend learns of a
    start only when the slow report arrives: the time check within 2 s of its time,
    then the weather, then music, no silence over 1 s, no emergency music."""
    name = "hour_change_slow_backend"
    await wait_music(h)
    await h.clear_pending()
    await h.queue([("song5", {}), ("song6", {}), ("song1", {})])
    h.stack.slow_backend = seconds
    try:
        t1 = at(25)
        await h.queue([("time", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"}),
                       ("weather", {"preempt_at": t1, "timing_mode": "hard", "source": "auto"})])
        due = t1.timestamp()
        await asyncio.sleep(max(0, due - time.time()) + 30)
    finally:
        h.stack.slow_backend = 0.0
    heard = h.heard(due - 5, due + 26)
    h.print_timeline(heard, due)
    line = [(t - due, n) for t, n in timeline(heard)]
    first_time = next((t for t, n in line if n == "time"), None)
    h.check(name, first_time is not None and -1.0 <= first_time <= 2.0,
            f"time check heard {'never' if first_time is None else f'{first_time:+.1f} s'} from its time")
    names = [n for _, n in line if n != "silence"]
    after = names[names.index("time"):] if "time" in names else []
    h.check(name, after[:2] == ["time", "weather"] and len(after) > 2 and after[2].startswith("song"),
            f"time check, weather, then music: {names}")
    h.check(name, "EMERGENCY" not in names, f"no emergency music: {names}")
    h.check(name, names.count("time") == 1 and names.count("weather") == 1, "each aired once")
    gap, gap_at = longest_silence(heard)
    h.check(name, gap <= 1.0, f"longest silence {gap:.1f} s" + (f" at {_fmt(gap_at)}" if gap_at else ""))
    times = await h.started_times("time", due - 5)
    rec = times[0][0] - due if times else None
    log(f"    note: the backend recorded the time check at {'never' if rec is None else f'{rec:+.1f} s'} (the report itself was held {seconds:.0f} s)")
    await asyncio.sleep(6)


def with_slow_backend(scenario, seconds: float = 4.0):
    """The same scenario while every answer the backend gives the engine takes
    `seconds` (engine thread, 2026-11-18: slow answers were behind 2.6 to 3.8 s gaps
    on Jerusalem). Timed items must still start on time and nothing may go silent."""
    async def run(h: Harness) -> None:
        h.stack.slow_backend = seconds
        try:
            await scenario(h)
        finally:
            h.stack.slow_backend = 0.0
    return run



# ── Emergency alerts (wiki/emergency-alerts.md) ──────────────────────────────


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = b""
    for k, v in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for k, (fname, data) in files.items():
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                f'Content-Type: audio/mpeg\r\n\r\n').encode() + data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"


def send_alert(h: Harness, clip: Path, *, device: bool = False, kind: str | None = None,
               title: str = "Harness alert") -> tuple[int, dict]:
    """Staff test (manager login) or the EAS box's call (emergency token)."""
    fields = {"station_ids": str(STATION_ID), "title": title}
    if kind:
        fields["kind"] = kind
    body, ctype = _multipart(fields, {"file": (clip.name, clip.read_bytes())})
    path = "/emergency/device/activate" if device else "/emergency/activate"
    headers = {"Content-Type": ctype}
    headers.update({"X-Emergency-Token": EMERGENCY_TOKEN} if device else {"Authorization": f"Bearer {h.token}"})
    req = urllib.request.Request(f"http://127.0.0.1:{BACKEND_PORT}/api/v1{path}", data=body,
                                 method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:400]}


async def alert_row(alert_id: str):
    from app.db.engine import async_session_factory
    from app.models.emergency_alert import EmergencyAlert

    async with async_session_factory() as db:
        return await db.get(EmergencyAlert, uuid.UUID(alert_id))


def runs_of(windows, name: str) -> list[tuple[float, float]]:
    """(start, end) of each stretch where `name` was heard (gaps under 0.3 s bridged)."""
    out: list[list[float]] = []
    for t, db, f in windows:
        if db >= -45 and tone_name(f) == name:
            if out and t - out[-1][1] <= 0.3:
                out[-1][1] = t + WINDOW
            else:
                out.append([t, t + WINDOW])
    return [(a, b) for a, b in out if b - a >= 0.3]


async def sc_emergency_alert_over_music(h: Harness) -> None:
    """A staff test of the override over a song: on air within a moment, played whole,
    the same song carries on afterwards, no gap, no emergency music, and the log says
    when and how long."""
    name = "emergency_alert_over_music"
    await wait_music(h)
    await asyncio.sleep(5)
    sent = time.time()
    code, body = send_alert(h, h.stack.dir / "alert8.mp3", title="Override test over music")
    h.check(name, code == 201 and body.get("sent") == 1, f"alert sent ({code} {body.get('error', '')})")
    if code != 201:
        return
    alert_id = body["alerts"][0]["id"]
    await asyncio.sleep(3)
    code, st = h.api("GET", "/emergency/status")
    mine = next((x for x in st.get("stations", []) if x["station_id"] == str(STATION_ID)), {})
    h.check(name, bool(mine.get("on_air")) and mine["on_air"]["alert_id"] == alert_id,
            f"the status page shows the alert on air: {mine.get('on_air')}")
    await asyncio.sleep(14)
    heard = h.heard(sent - 4, time.time() - 1)
    h.print_timeline(heard, sent)
    alert = runs_of(heard, "ALERT")
    h.check(name, len(alert) == 1, f"one alert heard: {[(round(a - sent, 1), round(b - sent, 1)) for a, b in alert]}")
    if not alert:
        return
    a0, a1 = alert[0]
    h.check(name, a0 - sent <= 2.0, f"on air {a0 - sent:.1f} s after it was sent")
    h.check(name, abs((a1 - a0) - 8.0) <= 0.6, f"played whole: {a1 - a0:.1f} s of 8 s")
    before = [n for t, n in timeline([w for w in heard if w[0] < a0 - 0.2]) if n not in ("silence", "mix")]
    after = [n for t, n in timeline([w for w in heard if a1 + 0.2 < w[0] < a1 + 4]) if n not in ("silence", "mix")]
    h.check(name, bool(before) and bool(after) and before[-1] == after[0],
            f"the song carries on after the alert ({before[-1:] } then {after[:1]})")
    gap, _ = longest_silence([w for w in heard if a0 - 3 <= w[0] <= a1 + 3])
    h.check(name, gap <= 0.5, f"longest silence around the alert {gap:.1f} s")
    names = [n for _, n in timeline(heard)]
    h.check(name, "EMERGENCY" not in names, f"no emergency music: {names}")
    row = await alert_row(alert_id)
    h.check(name, row is not None and row.status == "done" and row.seconds_on_air is not None
            and abs(row.seconds_on_air - 8.0) <= 1.0,
            f"logged: {row.status if row else None}, {row.seconds_on_air if row else None} s")


async def sc_emergency_alert_over_time_check(h: Harness) -> None:
    """An alert on air when a hard time check is due: the time check does not air late
    (it is dropped), the alert plays whole, then music."""
    name = "emergency_alert_over_time_check"
    await wait_music(h)
    due_dt = at(14)
    due = due_dt.timestamp()
    [tid] = await h.queue([("time", {"preempt_at": due_dt, "timing_mode": "hard", "source": "auto"})])
    await asyncio.sleep(8)
    sent = time.time()
    code, body = send_alert(h, h.stack.dir / "alert14.mp3", title="Alert across a time check")
    h.check(name, code == 201, f"alert sent ({code})")
    await asyncio.sleep(due - time.time() + 30)
    heard = h.heard(sent - 3, time.time() - 1)
    h.print_timeline(heard, due)
    alert = runs_of(heard, "ALERT")
    h.check(name, len(alert) == 1 and abs((alert[0][1] - alert[0][0]) - 14.0) <= 0.6,
            f"the alert played whole over the time check's second: {[(round(a - due, 1), round(b - due, 1)) for a, b in alert]}")
    times = runs_of(heard, "time")
    h.check(name, not times, f"no late time check: {[(round(a - due, 1)) for a, _ in times]}")
    row = next((e for e in await h.rows() if e.id == tid), None)
    h.check(name, row is not None and row.status == "skipped", f"time check closed: {row.status if row else None}")
    after = [n for _, n in timeline([w for w in heard if alert and w[0] > alert[0][1] + 0.3]) if n != "silence"]
    h.check(name, bool(after) and after[0].startswith("song"), f"music after the alert: {after[:3]}")
    names = [n for _, n in timeline(heard)]
    h.check(name, "EMERGENCY" not in names, f"no emergency music: {names}")


async def sc_emergency_alert_over_live_show(h: Harness) -> None:
    """A host on air: the alert takes the air over the show, and the show is heard again
    after it."""
    name = "emergency_alert_over_live_show"
    before = set_live_settings(h, live_delay_seconds=0, live_host_gone_seconds=0)
    try:
        await wait_music(h)
        code, show = h.api("POST", "/live-shows", {"station_id": str(STATION_ID), "title": "Harness show",
                                                   "calls_enabled": False})
        if code != 201:
            h.check(name, False, f"could not create the show ({code})")
            return
        h.stack.live_audio = True
        code, _ = h.api("POST", f"/live-shows/{show['id']}/start")
        await asyncio.sleep(8)
        sent = time.time()
        code, body = send_alert(h, h.stack.dir / "alert8.mp3", title="Alert over a live show")
        h.check(name, code == 201, f"alert sent ({code})")
        await asyncio.sleep(16)
        t_end = time.time()
        h.stack.live_audio = False
        h.api("POST", f"/live-shows/{show['id']}/end")
        await asyncio.sleep(6)
        heard = h.heard(sent - 4, t_end)
        h.print_timeline(heard, sent)
        alert = runs_of(heard, "ALERT")
        h.check(name, len(alert) == 1 and alert[0][0] - sent <= 2.0 and abs(alert[0][1] - alert[0][0] - 8.0) <= 0.6,
                f"the alert took the air over the host, whole: {[(round(a - sent, 1), round(b - sent, 1)) for a, b in alert]}")
        host_during = sum(WINDOW for w in heard if alert and alert[0][0] + 0.3 <= w[0] <= alert[0][1] - 0.3
                          and w[1] >= -45 and tone_name(w[2]) == "HOST")
        h.check(name, host_during <= 0.2, f"the host is not heard under the alert ({host_during:.1f} s)")
        back = runs_of([w for w in heard if alert and w[0] > alert[0][1]], "HOST")
        h.check(name, bool(back) and back[0][0] - alert[0][1] <= 2.0,
                f"the host is heard again {back[0][0] - alert[0][1]:.1f} s after the alert" if back and alert
                else "the host is not heard after the alert")
    finally:
        h.stack.live_audio = False
        h.api("PUT", f"/stations/{STATION_ID}", {"automation_config": before})


async def sc_emergency_alert_stop(h: Harness) -> None:
    """Stop on the page: the alert ends at once and the music is back; logged as stopped."""
    name = "emergency_alert_stop"
    await wait_music(h)
    await asyncio.sleep(3)
    code, body = send_alert(h, h.stack.dir / "alert20.mp3", title="Alert to stop")
    h.check(name, code == 201, f"alert sent ({code})")
    if code != 201:
        return
    alert_id = body["alerts"][0]["id"]
    await asyncio.sleep(5)
    pressed = time.time()
    code, res = h.api("POST", f"/emergency/{alert_id}/stop")
    h.check(name, code == 200 and res.get("status") == "cancelled", f"stopped ({code} {res})")
    await asyncio.sleep(8)
    heard = h.heard(pressed - 6, time.time() - 1)
    h.print_timeline(heard, pressed)
    alert = runs_of(heard, "ALERT")
    h.check(name, bool(alert) and alert[-1][1] - pressed <= 1.2,
            f"alert off air {alert[-1][1] - pressed:.1f} s after Stop" if alert else "alert never heard")
    after = [n for _, n in timeline([w for w in heard if w[0] > pressed + 1.5]) if n != "silence"]
    h.check(name, bool(after) and after[0].startswith("song"), f"music back: {after[:2]}")
    row = await alert_row(alert_id)
    h.check(name, row is not None and row.status == "cancelled", f"logged as stopped: {row.status if row else None}")


async def sc_emergency_alert_two_in_a_row(h: Harness) -> None:
    """Two alerts sent a second apart (say English, then Spanish) play one after the
    other, whole, never over each other."""
    name = "emergency_alert_two_in_a_row"
    await wait_music(h)
    await asyncio.sleep(3)
    sent = time.time()
    c1, b1 = send_alert(h, h.stack.dir / "alert8.mp3", title="First")
    await asyncio.sleep(1)
    c2, b2 = send_alert(h, h.stack.dir / "alert8.mp3", title="Second")
    h.check(name, c1 == 201 and c2 == 201, f"both sent ({c1}, {c2})")
    await asyncio.sleep(22)
    heard = h.heard(sent - 2, time.time() - 1)
    h.print_timeline(heard, sent)
    alert = runs_of(heard, "ALERT")
    total = sum(b - a for a, b in alert)
    h.check(name, abs(total - 16.0) <= 1.0, f"16 s of alert in all, one after the other: {total:.1f} s in {len(alert)} stretch(es)")
    if c1 == 201 and c2 == 201:
        r1, r2 = await alert_row(b1["alerts"][0]["id"]), await alert_row(b2["alerts"][0]["id"])
        h.check(name, r1.status == "done" and r2.status == "done" and r1.started_at < r2.started_at,
                f"both logged, in order: {r1.status} {r2.status}")


async def sc_emergency_alert_tones(h: Harness) -> None:
    """EAS tones: a staff upload holding them is refused and nothing airs. With
    KBR_HARNESS_EAS_RECORDING (a real alert recording, this offline harness only: its
    tones must never reach a public stream), the EAS box's own call is accepted and the
    recording plays whole, then the music."""
    name = "emergency_alert_tones"
    await wait_music(h)
    att = h.stack.dir / "attention.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc=0.4*sin(2*PI*853*t)+0.4*sin(2*PI*960*t):s=44100:d=4", str(att)], check=True)
    t0 = time.time()
    code, body = send_alert(h, att, title="Tones from staff")
    h.check(name, code == 422 and "11.45" in body.get("error", ""), f"staff upload with EAS tones refused ({code})")
    rec = os.environ.get("KBR_HARNESS_EAS_RECORDING")
    if not rec:
        log("    (no KBR_HARNESS_EAS_RECORDING: the real recording part is skipped)")
        await asyncio.sleep(3)
        heard = h.heard(t0, time.time() - 0.5)
        h.check(name, not runs_of(heard, "ALERT"), "nothing aired")
        return
    clip = Path(rec)
    code, body = send_alert(h, clip, title="Staff upload of a real recording")
    h.check(name, code == 422, f"staff upload of the real recording refused ({code})")
    secs = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(clip)], capture_output=True, text=True).stdout.strip())
    sent = time.time()
    code, body = send_alert(h, clip, device=True, kind="alert", title="EAS box relay (harness only)")
    h.check(name, code == 201 and body["alerts"][0]["has_tones"], f"the EAS box's call accepted, tones noted ({code})")
    if code != 201:
        return
    await asyncio.sleep(secs + 8)
    heard = h.heard(sent - 3, time.time() - 1)
    songs = [(t, n) for t, n in timeline(heard) if n.startswith("song")]
    before = [t for t, n in songs if t < sent + 0.5]
    after = [t for t, n in songs if t > sent + 2]
    h.check(name, bool(before) and bool(after) and secs - 1.0 <= after[0] - sent <= secs + 3.0,
            f"the recording ({secs:.0f} s) played whole: music back {after[0] - sent:.1f} s after it was sent"
            if after else "music never came back")
    row = await alert_row(body["alerts"][0]["id"])
    h.check(name, row.status == "done" and abs((row.seconds_on_air or 0) - secs) <= 2.0,
            f"logged {row.status}, {row.seconds_on_air} s")


async def sc_emergency_alert_line(h: Harness) -> None:
    """The EAS box's audio line on a local FM box (needs KBR_HARNESS_ALERT_DEVICE and a
    PulseAudio null sink whose monitor it records; KBR_HARNESS_ALERT_SINK names the sink):
    sound there takes the air within a second, the music is back about 4 s after it ends,
    and the site logs it. Run with --local-box it also cuts the line both ways for the
    whole alert: the takeover needs no internet, and the log arrives when the line is back."""
    name = "emergency_alert_line"
    if not os.environ.get("KBR_HARNESS_ALERT_DEVICE"):
        h.check(name, False, "set KBR_HARNESS_ALERT_DEVICE (and a PulseAudio null sink, see wiki/emergency-alerts.md)")
        return
    sink = os.environ.get("KBR_HARNESS_ALERT_SINK", "alertin")
    await wait_music(h)
    await asyncio.sleep(4)
    offline = h.stack.local_box
    if offline:
        h.stack.backend_down = True
        h.stack.control_down = True
        await asyncio.sleep(2)
    played = time.time()
    subprocess.run(["paplay", "-d", sink, str(h.stack.dir / "alert8.mp3")], check=False, timeout=30)
    ended = time.time()
    await asyncio.sleep(10)
    if offline:
        h.stack.backend_down = False
        h.stack.control_down = False
        await asyncio.sleep(70)  # the relay's next plan sync sends the kept reports
    heard = h.heard(played - 3, ended + 9)
    h.print_timeline(heard, played)
    alert = runs_of(heard, "ALERT")
    # (includes paplay's own start-up, a few tenths of a second)
    h.check(name, bool(alert) and alert[0][0] - played <= 2.0,
            f"the line took the air {alert[0][0] - played:.1f} s after its sound began" if alert else "the line was never heard")
    h.check(name, bool(alert) and sum(b - a for a, b in alert) >= 7.0,
            f"heard {sum(b - a for a, b in alert):.1f} s of the 8 s on the line")
    songs = [t for t, n in timeline([w for w in heard if w[0] > ended]) if n.startswith("song")]
    h.check(name, bool(songs) and songs[0] - ended <= 6.0,
            f"music back {songs[0] - ended:.1f} s after the line went quiet" if songs else "music never came back")
    from sqlalchemy import select

    from app.db.engine import async_session_factory
    from app.models.emergency_alert import EmergencyAlert

    async with async_session_factory() as db:
        rows = (await db.execute(select(EmergencyAlert).where(
            EmergencyAlert.source == "line", EmergencyAlert.station_id == STATION_ID)
            .order_by(EmergencyAlert.requested_at.desc()).limit(1))).scalars().all()
    row = rows[0] if rows else None
    h.check(name, row is not None and row.status == "done" and row.seconds_on_air and row.seconds_on_air >= 8,
            f"logged on the site{' after the line came back' if offline else ''}: "
            f"{row.status if row else None}, {row.seconds_on_air if row else None} s")

SCENARIOS = {
    "hour_change_slow_backend": sc_hour_change_slow_backend,
    "soft_timed_waits_for_song_slow_backend": with_slow_backend(sc_soft_waits_for_song),
    "live_show_over_hard_item_slow_backend": with_slow_backend(sc_live_show_over_hard_item),
    "live_show_quiet_slow_backend": with_slow_backend(sc_live_show_quiet),
    "block_shiur_spans_hour": lambda h: sc_block_shiur_spans_hour(h, "pause"),
    "block_shiur_spans_hour_wait": lambda h: sc_block_shiur_spans_hour(h, "wait"),
    "block_shiur_spans_hour_cut": lambda h: sc_block_shiur_spans_hour(h, "cut"),
    "queued_shiur_spans_hour": lambda h: sc_music_lane_shiur_spans_hour(h, "pause"),
    "queued_shiur_spans_hour_wait": lambda h: sc_music_lane_shiur_spans_hour(h, "wait"),
    "queued_shiur_spans_hour_cut": lambda h: sc_music_lane_shiur_spans_hour(h, "cut"),
    "song_spans_hour": sc_song_spans_hour,
    "soft_timed_waits_for_song": sc_soft_waits_for_song,
    "soft_pauses_long_shiur": sc_soft_pauses_long_shiur,
    "soft_waits_for_short_shiur": sc_soft_waits_for_short_shiur,
    "rapid_next": sc_rapid_next,
    "empty_queue": sc_empty_queue,
    "slow_started": sc_slow_started,
    "slow_backend_no_gap": sc_slow_backend_no_gap,
    "next_while_held": sc_next_while_held,
    "three_item_timed_group": sc_three_item_group,
    "two_groups_0.3s_apart": lambda h: sc_two_groups(h, 0.3),
    "two_groups_1s_apart": lambda h: sc_two_groups(h, 1.0),
    "two_groups_20s_apart": lambda h: sc_two_groups(h, 20.0),
    "play_now_before_hard_item": sc_play_now_before_hard_item,
    "backend_unreachable": sc_backend_unreachable,
    "local_box_commands": sc_local_box_commands,
    "local_box_offline": sc_local_box_offline,
    "local_box_power_loss": sc_local_box_power_loss,
    "local_box_sound_card": sc_local_box_sound_card,
    "control_endpoints": sc_control_endpoints,
    "slow_timed_file": sc_slow_timed_file,
    "queue_change_drops_preloaded": sc_queue_change_drops_preloaded,
    "resume_position": sc_resume_position,
    "cue_points": sc_cue_points,
    "icecast_output": sc_icecast_output,
    "track_level": sc_track_level,
    "song_with_level_muted_under_time_check": sc_level_song_muted,
    "timed_and_mix_over_levels": sc_lane_levels,
    "duplicate_timed_id": sc_duplicate_timed_id,
    "block_with_slow_shiur": sc_block_with_slow_shiur,
    # the shiur's file arrives only after the announcement has ended: music fills in
    "block_with_very_slow_shiur": lambda h: sc_block_with_slow_shiur(h, 45.0),
    "blackout_ended_early": sc_blackout_ended_early,
    "output_limiter": sc_output_limiter,
    "fit_before_hour": sc_fit_before_hour,
    "fit_preloaded_before_hour": sc_fit_preloaded_before_hour,
    "song_ends_before_hour": sc_song_ends_before_hour,
    "song_ends_6s_before_hour": sc_song_ends_6s_before_hour,
    "live_show_quiet": sc_live_show_quiet,
    "live_show_delay_engine": sc_live_show_delay_engine,
    "live_show_delay_controls": sc_live_show_delay_controls,
    "live_show_delay_restart": lambda h: sc_live_show_delay_controls(h, restart=True),
    "live_show_no_host": sc_live_show_no_host,
    "live_show_host_gone": sc_live_show_host_gone,
    "live_show_delay_engine_preroll_item": lambda h: sc_live_show_delay_engine(h, preroll_item=True),
    "live_show_on_the_stream": sc_live_show_on_the_stream,
    "speech_never_overlaps": sc_speech_never_overlaps,
    "engine_restart_15s_before_hour": sc_engine_restart_15s_before_hour,
    "engine_restart_song_ends_before_hour": sc_engine_restart_song_ends_before_hour,
    "engine_restart_across_hour": sc_engine_restart_across_hour,
    "backend_restart_before_hour": sc_backend_restart_before_hour,
    "next_after_idle": sc_next_after_idle,
    "next_right_after_change": sc_next_right_after_change,
    "site_matches_stream": sc_site_matches_stream,
    "operator_queue_then_next": sc_operator_queue_then_next,
    "operator_next_slow_file": sc_operator_next_slow_file,
    "next_twice_slow_file": sc_next_twice_slow_file,
    "live_show_over_hard_item_hard_stop": lambda h: sc_live_show_over_hard_item(h, hard_stop=True),
    "live_show_over_hard_item_restart": lambda h: sc_live_show_over_hard_item(h, restart=True),
    "operator_next_overtakes_commit": sc_operator_next_overtakes_commit,
    "two_sources_same_second": sc_two_sources_same_second,
    "hard_group_overruns_next_hard": sc_hard_group_overruns_next_hard,
    "engine_starts_every_time": sc_engine_starts_every_time,
    "live_show_over_hard_item": sc_live_show_over_hard_item,
    "next_while_soft_spot_waits": sc_next_while_soft_spot_waits,
    "next_while_soft_spot_waits_slow": lambda h: sc_next_while_soft_spot_waits(h, 3.0),
    "resume_without_old_song": sc_resume_without_old_song,
    "next_twice": sc_next_twice,
    "next_on_a_busy_server": sc_next_on_a_busy_server,
    "next_skips_one": sc_next_skips_one,
    "engine_crash_restart": sc_engine_crash_restart,
    "second_engine_refused": sc_second_engine_refused,
    "emergency_alert_over_music": sc_emergency_alert_over_music,
    "emergency_alert_over_time_check": sc_emergency_alert_over_time_check,
    "emergency_alert_over_live_show": sc_emergency_alert_over_live_show,
    "emergency_alert_stop": sc_emergency_alert_stop,
    "emergency_alert_two_in_a_row": sc_emergency_alert_two_in_a_row,
    "emergency_alert_tones": sc_emergency_alert_tones,
    "emergency_alert_line": sc_emergency_alert_line,
    # restarts the engine: keep it last
    "engine_starts_backend_down": sc_engine_starts_backend_down,
}



def _fmt(t: float | None) -> str:
    return "never" if t is None else datetime.fromtimestamp(t).strftime("%H:%M:%S.%f")[:-4]


async def main_async(names: list[str], keep: bool, local_box: bool = False) -> int:
    workdir = Path(tempfile.mkdtemp(prefix="kbr-harness-"))
    log(f"work dir {workdir}")
    stack = Stack(workdir)
    stack.local_box = local_box
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
        if local_box:
            stack.start_relay()
        stack.start_engine()
        log("engine on air")
        prev = None
        for n in names:
            log(f"=== {n}")
            # A timed item one scenario leaves waiting airs in the middle of the next one and
            # reads there as "aired twice" (2026-11-18). Say whose it was, loudly, and remove it.
            if prev:
                left = await h.drop_leftover_timed()
                if left:
                    h.check(prev, False, f"left timed items waiting in the queue for the next scenario: {left}")
            prev = n
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
    ap.add_argument("--local-box", action="store_true",
                    help="run the engine behind the local FM box relay (infra/local/kbr_relay.py)")
    args = ap.parse_args()
    names = args.scenarios or [
        n for n in SCENARIOS
        if (args.local_box or not n.startswith("local_box_"))
        and (n != "local_box_sound_card" or os.environ.get("KBR_HARNESS_ALSA_DEVICE"))
        and (n != "emergency_alert_line" or os.environ.get("KBR_HARNESS_ALERT_DEVICE"))
    ]
    for n in names:
        if n not in SCENARIOS:
            sys.exit(f"unknown scenario {n}")
    sys.exit(asyncio.run(main_async(names, args.keep, args.local_box)))


if __name__ == "__main__":
    main()
