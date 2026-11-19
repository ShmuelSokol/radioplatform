"""The local FM box: the plan the backend gives it, and the relay that keeps the engine
fed when the backend cannot be reached (infra/local/kbr_relay.py, wiki/local-fm-box.md)."""

import importlib.util
import json
import time
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.config import settings
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.queue_entry import QueueEntry
from app.models.station import Station

TOKEN = "test-playout-token"
HEADERS = {"X-Playout-Token": TOKEN}

_spec = importlib.util.spec_from_file_location(
    "kbr_relay", Path(__file__).resolve().parents[2] / "infra" / "local" / "kbr_relay.py"
)
kbr_relay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kbr_relay)


# ── The backend's side ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_lists_queue_with_engine_uris_and_blackouts(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", TOKEN)
    station = Station(id=uuid.uuid4(), name="FM", timezone="America/New_York")
    db_session.add(station)
    now = datetime.now(UTC)
    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0, file_path="https://cdn.example.com/a.mp3", asset_type="music")
    check = Asset(id=uuid.uuid4(), title="Time 3 PM", duration=6.0, file_path="https://cdn.example.com/t.mp3", asset_type="time")
    spot = Asset(id=uuid.uuid4(), title="Sponsor", duration=30.0, file_path="https://cdn.example.com/s.mp3", asset_type="spot")
    far = Asset(id=uuid.uuid4(), title="Next week", duration=6.0, file_path="https://cdn.example.com/w.mp3", asset_type="time")
    db_session.add_all([song, check, spot, far])
    await db_session.flush()
    at = now + timedelta(minutes=30)
    db_session.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1, status="pending", source="auto"),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=check.id, position=2, status="pending",
                   source="auto", preempt_at=at, timing_mode="hard"),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=3, status="pending",
                   source="ad_slot", preempt_at=at),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=far.id, position=4, status="pending",
                   source="auto", preempt_at=now + timedelta(days=5), timing_mode="hard"),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=0, status="played", source="auto"),
        HolidayWindow(id=uuid.uuid4(), name="Shabbos", is_blackout=True,
                      start_datetime=now + timedelta(hours=20), end_datetime=now + timedelta(hours=45)),
    ])
    await db_session.commit()

    assert (await client.get(f"/api/v1/playout/{station.id}/plan")).status_code == 403
    resp = await client.get(f"/api/v1/playout/{station.id}/plan", headers=HEADERS)
    assert resp.status_code == 200
    plan = resp.json()
    assert [e["title"] for e in plan["entries"]] == ["Song", "Time 3 PM", "Sponsor"]
    s, t, a = plan["entries"]
    assert s["at"] is None and s["mode"] is None and s["uri_timed"] is None
    assert s["uri_main"].startswith("annotate:") and s["uri_main"].endswith(":https://cdn.example.com/a.mp3")
    assert t["mode"] == "hard" and abs(t["at"] - at.timestamp()) < 1
    assert 'kbr_lane="timed"' in t["uri_timed"]
    assert a["mode"] == "soft" and a["uri_timed"] is None
    assert [b["name"] for b in plan["blackouts"]] == ["Shabbos"]


@pytest.mark.asyncio
async def test_library_pages_through_every_file(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", TOKEN)
    sid = uuid.uuid4()
    for i in range(5):
        db_session.add(Asset(id=uuid.uuid4(), title=f"S{i}", file_path=f"https://cdn.example.com/{i}.mp3", asset_type="music"))
    await db_session.commit()
    assert (await client.get(f"/api/v1/playout/{sid}/library")).status_code == 403
    seen, after = [], None
    for _ in range(10):
        resp = await client.get(f"/api/v1/playout/{sid}/library", headers=HEADERS, params={"limit": 2, **({"after": after} if after else {})})
        assert resp.status_code == 200
        page = resp.json()
        seen += [i["url"] for i in page["items"]]
        after = page["next"]
        if not after:
            break
    assert sorted(seen) == [f"https://cdn.example.com/{i}.mp3" for i in range(5)]


# ── The relay ────────────────────────────────────────────────────────────────


class Fake:
    """A stand-in backend, file server and engine control port in one."""

    def __init__(self):
        self.up = True
        self.calls = []
        self.next_answer = None
        self.plan = {"entries": [], "blackouts": []}
        self.extension = None
        self.flushes = 0
        self.timed = []
        self.instances = []
        self.refuse_instance = None
        self.live_chunks = None  # None = no show (204); else pieces sent one by one
        self.live_gate = threading.Event()
        fake = self

        class H(BaseHTTPRequestHandler):
            def _go(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                path = self.path
                if path.startswith("/files/"):
                    return self._out(200, b"AUDIO" + path.encode())
                if path == "/timed":
                    fake.timed.append(json.loads(body))
                    return self._out(200, b'{"ok":true}')
                if path == "/flush":
                    fake.flushes += 1
                    return self._out(200, b'{"ok":true}')
                if not fake.up:
                    return self._out(503, b"down")
                fake.calls.append((self.command, path.split("/playout/sid", 1)[-1], body))
                fake.instances.append(self.headers.get("X-Playout-Instance"))
                if fake.refuse_instance and self.headers.get("X-Playout-Instance") == fake.refuse_instance:
                    return self._out(409, b'{"detail":"Another playout engine plays this station"}')
                if path.endswith("/live.mp3"):
                    if fake.live_chunks is None:
                        return self._out(204, b"")
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/mpeg")
                    self.end_headers()
                    for i, chunk in enumerate(fake.live_chunks):
                        if i:
                            fake.live_gate.wait(5)
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    return None
                if "/plan" in path:
                    if "days=" in path and fake.extension is not None:
                        return self._out(200, json.dumps({**fake.plan, "extension": fake.extension}).encode())
                    return self._out(200, json.dumps(fake.plan).encode())
                if "/next" in path:
                    if fake.next_answer is None:
                        return self._out(204, b"")
                    return self._out(200, json.dumps(fake.next_answer).encode())
                return self._out(200, b'{"ok":true}')

            def _out(self, status, data):
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _go  # noqa: N815

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, name):
        return f"{self.base}/files/{name}.mp3"

    def entry(self, name, position, at=None, mode=None, kind="music"):
        url = self.url(name)
        return {
            "entry_id": name, "position": position, "at": at, "mode": mode, "hard_by": None, "kind": kind,
            "title": name, "duration": 10.0, "url": url,
            "uri_main": f'annotate:kbr_entry="{name}",kbr_lane="main",title="a \\":http x":{url}',
            "uri_timed": f'annotate:kbr_entry="{name}",kbr_lane="timed":{url}' if mode == "hard" else None,
        }


@pytest.fixture
def rig(tmp_path):
    fake = Fake()
    clock = {"now": 1_000_000.0}
    relay = kbr_relay.Relay(
        upstream=fake.base + "/api/v1", station_id="sid", token=TOKEN, data_dir=tmp_path,
        engine_control=fake.base, clock=lambda: clock["now"],
    )
    yield fake, relay, clock
    fake.server.shutdown()


def _next(relay):
    status, data = relay.next("given=&proto=2")
    return status, (json.loads(data) if data else None)


def test_online_passes_through_and_plays_the_local_copy(rig):
    fake, relay, _ = rig
    url = fake.url("a")
    fake.next_answer = {"uri": f'annotate:kbr_entry="a",title="t":{url}', "entry_id": "a"}
    status, item = _next(relay)
    assert status == 200 and item["uri"].endswith(url)  # not on disk yet: the engine fetches it
    assert relay.download(url)
    status, item = _next(relay)
    assert item["uri"] == f'annotate:kbr_entry="a",title="t":{relay.local_path(url)}'
    assert relay.local_path(url).read_bytes().startswith(b"AUDIO")
    assert fake.calls[0][1].startswith("/next?given=&proto=2")
    fake.next_answer = None
    assert _next(relay) == (204, None)


def test_the_engine_instance_id_reaches_the_backend_and_a_refusal_reaches_the_engine(rig):
    """One engine per station also for a box: the relay passes the engine's run id on, and
    the backend's 409 for a second engine goes back to that engine (no plan fallback)."""
    import socket
    import urllib.error
    import urllib.request

    fake, relay, _ = rig
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = kbr_relay.serve(relay, port)
    try:
        def call(path, instance, method="GET"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/v1/playout/sid{path}", method=method,
                data=b"{}" if method == "POST" else None,
                headers={"X-Playout-Token": TOKEN, "X-Playout-Instance": instance})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status
            except urllib.error.HTTPError as e:
                return e.code

        assert call("/hello", "box-1", "POST") == 200
        assert call("/next?given=", "box-1") == 204
        assert fake.instances == ["box-1", "box-1"]
        fake.refuse_instance = "copied"
        assert call("/hello", "copied", "POST") == 409
        assert call("/next?given=", "copied") == 409
    finally:
        server.shutdown()


def test_live_show_audio_reaches_the_engine_through_the_box(rig):
    """Engine proto 5 plays a live show from live.mp3. On a box the engine asks the relay,
    which passes the show through as it arrives (not after it ends); with no show, or
    with the line down, the answer is 204 and the engine's live lane stays quiet."""
    import socket
    import urllib.error
    import urllib.request

    fake, relay, _ = rig
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = kbr_relay.serve(relay, port)
    url = f"http://127.0.0.1:{port}/api/v1/playout/sid/live.mp3"

    def open_live(token=TOKEN):
        req = urllib.request.Request(url, headers={"X-Playout-Token": token, "X-Playout-Instance": "box-1"})
        try:
            return urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as e:
            return e

    try:
        assert open_live("wrong").code == 403
        assert open_live().status == 204  # no show on air
        fake.live_chunks = [b"ID3first", b"second", b"third"]
        resp = open_live()
        assert resp.status == 200 and resp.headers["Content-Type"] == "audio/mpeg"
        assert resp.read1(64) == b"ID3first"  # the show's start, before the backend sent more
        fake.live_gate.set()
        assert resp.read() == b"secondthird"
        resp.close()
        assert fake.instances[-1] == "box-1"
        fake.up = False
        fake.live_chunks = None
        fake.server.shutdown()
        fake.server.server_close()
        assert open_live().status == 204  # backend not reached: quiet, never an error
    finally:
        server.shutdown()


def test_offline_plays_the_plan_in_order_and_nothing_twice(rig):
    fake, relay, clock = rig
    now = clock["now"]
    fake.plan = {"blackouts": [], "entries": [
        fake.entry("a", 1), fake.entry("b", 2), fake.entry("soft", 3, at=now + 100, mode="soft", kind="spot"),
        fake.entry("c", 4), fake.entry("notlocal", 5),
    ]}
    assert relay.sync_plan()
    assert relay.fetch_plan_audio() == 0
    relay.local_path(fake.url("notlocal")).unlink()
    # The backend handed "a" over while the line was still up
    fake.next_answer = {"uri": fake.plan["entries"][0]["uri_main"], "entry_id": "a"}
    assert _next(relay)[1]["entry_id"] == "a"

    fake.up = False
    status, item = _next(relay)
    assert status == 200 and item["entry_id"] == "b"
    assert item["uri"].endswith(str(relay.local_path(fake.url("b")))) and 'title="a \\":http x"' in item["uri"]
    assert _next(relay)[1]["entry_id"] == "c"  # the sponsorship waits for its time
    clock["now"] = now + 101
    assert _next(relay)[1]["entry_id"] == "soft"
    assert _next(relay)[0] == 204  # only an item with no local file is left: emergency folder
    assert not relay.online


def test_offline_hard_item_goes_to_the_timed_lane_once_and_on_time(rig):
    fake, relay, clock = rig
    now = clock["now"]
    at = now + 120
    fake.plan = {"blackouts": [], "entries": [
        fake.entry("song", 1),
        fake.entry("weather", 3, at=at, mode="hard", kind="weather"),
        fake.entry("time", 2, at=at, mode="hard", kind="time"),
    ]}
    relay.sync_plan()
    relay.fetch_plan_audio()
    assert relay.hand_over_timed() == 0  # line is up: the backend does it
    fake.up = False
    relay.sync_plan()
    assert relay.hand_over_timed() == 0  # 120 s ahead: too early
    clock["now"] = at - 44
    assert relay.hand_over_timed() == 2
    assert [(t["id"], t["at"], t["seq"], t["of"]) for t in fake.timed] == [("time", at, 0, 2), ("weather", at, 1, 2)]
    assert fake.timed[0]["uri"].endswith(str(relay.local_path(fake.url("time"))))
    assert relay.hand_over_timed() == 0
    # The music lane never gets them as well
    clock["now"] = at + 5
    assert _next(relay)[1]["entry_id"] == "song"
    assert _next(relay)[0] == 204
    # Kept across a restart of the relay
    again = kbr_relay.Relay(upstream=fake.base + "/api/v1", station_id="sid", token=TOKEN,
                            data_dir=relay.dir, engine_control=fake.base, clock=lambda: clock["now"])
    again.online = False
    assert again.hand_over_timed() == 0 and again.offline_next()[0] == 204


def test_offline_time_check_five_minutes_late_is_dropped(rig):
    fake, relay, clock = rig
    now = clock["now"]
    fake.plan = {"blackouts": [], "entries": [fake.entry("time", 1, at=now + 10, mode="hard", kind="time"), fake.entry("song", 2)]}
    relay.sync_plan()
    relay.fetch_plan_audio()
    fake.up = False
    clock["now"] = now + 10 + 301
    relay.online = False
    assert relay.hand_over_timed() == 0
    assert _next(relay)[1]["entry_id"] == "song"


def test_offline_blackout_is_silence_and_music_returns_after(rig):
    fake, relay, clock = rig
    now = clock["now"]
    fake.plan = {"blackouts": [{"start": now + 50, "end": now + 500, "name": "Shabbos"}], "entries": [
        fake.entry("song", 1), fake.entry("time", 2, at=now + 100, mode="hard", kind="time"),
    ]}
    relay.sync_plan()
    relay.fetch_plan_audio()
    fake.up = False
    relay.online = False
    clock["now"] = now + 101
    status, item = _next(relay)
    assert status == 200 and item["entry_id"] == "" and item["uri"].endswith("silence-60s.wav")
    assert Path(item["uri"].rsplit(":", 1)[1]).stat().st_size > 1_000_000
    assert relay.hand_over_timed() == 0  # the missed time check stays off the air
    clock["now"] = now + 501
    assert _next(relay)[1]["entry_id"] == "song"


def test_box_setup_script_writes_only_settings_the_engine_and_relay_read():
    """infra/local/setup-box.sh: a setting it writes under a name nothing reads would be
    silently ignored on the box (a sound card that never plays, a relay on the wrong port)."""
    import re
    import subprocess

    infra = Path(__file__).resolve().parents[2] / "infra"
    script = infra / "local" / "setup-box.sh"
    text = script.read_text()
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    engine_reads = set(re.findall(r'"(KBR_[A-Z_]+)"', (infra / "streaming" / "radio.liq").read_text()))
    relay_reads = set(re.findall(r'"(KBR_[A-Z_]+)"', (infra / "local" / "kbr_relay.py").read_text()))

    def written(start: str, end: str) -> set[str]:
        block = text[text.index(start):]
        return set(re.findall(r"^(KBR_[A-Z_]+)=", block[:block.index(end)], re.M))
    engine_env = written('cat > "$ENV_FILE" <<ENV', "\nENV\n") | written('cat > "$STATION_ENV" <<SENV', "\nSENV\n")
    # Icecast's own two passwords are kept there between runs; the engine does not read them
    assert engine_env - engine_reads == {"KBR_ICECAST_RELAY_PASSWORD", "KBR_ICECAST_ADMIN_PASSWORD"}
    assert {"KBR_ALSA_DEVICE", "KBR_BACKEND_URL", "KBR_STATION_ID", "KBR_PLAYOUT_TOKEN"} <= engine_env
    relay_env = written('cat > "$RELAY_ENV" <<RENV', "\nRENV\n")
    assert relay_env <= relay_reads and {"KBR_UPSTREAM_URL", "KBR_STATION_ID", "KBR_PLAYOUT_TOKEN"} <= relay_env
    # The engine asks the relay on the box, on the relay's own port
    assert "KBR_BACKEND_URL=http://127.0.0.1:${RELAY_PORT}/api/v1" in text
    # The engine is ordered after the relay by the box's drop-in, not by the shared unit
    assert "After=kbr-relay.service sound.target" in text
    assert "kbr-relay" not in (infra / "streaming" / "kbr-engine@.service").read_text()
    # Updates: an engine on air is restarted only in a quiet time or when told "now", and
    # then between two songs; the hourly look never runs across the top of the hour
    assert 'if [ "$BY_TIMER" = 1 ] && [ "$APPLY" != now ] && ! quiet_now; then' in text
    waits = text.index('[ "$APPLY" != now ] && ! quiet_now; then\n  # On air and not a quiet time')
    assert waits < text.index("wait_song_end ||") < text.index('systemctl restart "kbr-engine@$STATION"')
    assert "OnCalendar=*:35" in text and "-ge 305 ]" in text and "-le 350 ]" in text
    box_env = written('cat > "$BOX_ENV" <<BENV', "\nBENV\n")
    assert box_env == {"KBR_BOX_STATION", "KBR_BOX_REF", "KBR_BOX_TZ", "KBR_BOX_AUTO_UPDATE"}
    assert all(f"${name}" in text or f'"{name}"' in text or f"{name} " in text for name in box_env)


def _again(fake, relay, clock):
    """The relay after the box was switched off and on: only what is on the disk."""
    again = kbr_relay.Relay(upstream=fake.base + "/api/v1", station_id="sid", token=TOKEN,
                            data_dir=relay.dir, engine_control=fake.base, clock=lambda: clock["now"])
    again.online = False
    return again


def test_power_cut_after_the_time_check_was_handed_over_hands_it_again(rig):
    """The engine lost its timed lane with the power. The relay's note "already sent"
    survived on disk, so without the engine's hello clearing it the time check never aired."""
    fake, relay, clock = rig
    at = clock["now"] + 40
    fake.plan = {"blackouts": [], "entries": [
        fake.entry("a", 1), fake.entry("time", 2, at=at, mode="hard", kind="time"), fake.entry("b", 3)]}
    relay.sync_plan()
    relay.fetch_plan_audio()
    fake.up = False
    relay.sync_plan()
    assert _next(relay)[1]["entry_id"] == "a"
    assert relay.hand_over_timed() == 1
    # Power cut. Half-written files of the last moment lie about; the box starts again
    (relay.dir / "state.json.part").write_text('{"done": ["a"')
    (relay.dir / "plan.json.part").write_text("")
    clock["now"] += 20
    again = _again(fake, relay, clock)
    assert again.hand_over_timed() == 0  # nothing yet: as far as the disk says it was sent
    assert again.hello(b'{"proto":2}')[0] == 200  # the engine starts and says so
    assert again.hand_over_timed() == 1
    assert [t["id"] for t in fake.timed] == ["time", "time"]
    assert json.loads(again.offline_next()[1])["entry_id"] == "b"
    assert again.offline_next()[0] == 204
    assert "a" in again.done  # what aired before the cut is not played again


def test_power_cut_with_unreadable_files_starts_empty_not_broken(rig):
    fake, relay, clock = rig
    fake.plan = {"blackouts": [], "entries": [fake.entry("a", 1)]}
    relay.sync_plan()
    (relay.dir / "plan.json").write_text('{"entries": [{"entry_')
    (relay.dir / "state.json").write_bytes(b"\x00" * 64)
    (relay.dir / "spool.jsonl").write_text('{"entry_id": "x", "lane": "main", "at": 5, "kept": true}\n{"entry_id": "y", "la')
    again = _again(fake, relay, clock)
    assert again.offline_next() == (204, b"")  # the engine plays its emergency folder
    assert again.status()["reports_kept"] == 2
    fake.up = True
    assert again.sync_plan()
    again.catch_up()  # the torn line goes up as it is and is refused there; it does not block the rest
    assert again.status()["reports_kept"] == 0
    again.fetch_plan_audio()
    again.online = False
    assert json.loads(again.offline_next()[1])["entry_id"] == "a"


def test_sponsorships_from_before_a_long_power_cut_do_not_air_in_a_row(rig):
    fake, relay, clock = rig
    now = clock["now"]
    fake.plan = {"blackouts": [], "entries": [
        fake.entry("spot1", 1, at=now + 900, mode="soft", kind="spot"),
        fake.entry("spot2", 2, at=now + 2700, mode="soft", kind="spot"),
        fake.entry("spot3", 3, at=now + 3 * 3600 - 600, mode="soft", kind="spot"),
        fake.entry("song", 4),
    ]}
    relay.sync_plan()
    relay.fetch_plan_audio()
    fake.up = False
    clock["now"] = now + 3 * 3600  # three hours without power
    again = _again(fake, relay, clock)
    assert json.loads(again.offline_next()[1])["entry_id"] == "spot3"  # ten minutes late: still airs
    assert json.loads(again.offline_next()[1])["entry_id"] == "song"
    assert {"spot1", "spot2"} <= set(again.done)


def test_clock_not_set_after_a_power_cut_plays_music_only(rig):
    """No internet and a flat clock battery: the computer wakes up in the past. A time
    check would say the wrong hour, so nothing timed airs until the clock is right; a
    blackout that had begun when the plan was fetched is still kept."""
    fake, relay, clock = rig
    now = clock["now"]
    fake.plan = {"generated_at": now, "blackouts": [], "entries": [
        fake.entry("time", 1, at=now - 86400 + 30, mode="hard", kind="time"),
        fake.entry("spot", 2, at=now - 86400 - 60, mode="soft", kind="spot"),
        fake.entry("song", 3),
    ]}
    relay.sync_plan()
    relay.fetch_plan_audio()
    fake.up = False
    clock["now"] = now - 86400  # a day behind
    again = _again(fake, relay, clock)
    assert not again.clock_ok() and again.status()["clock_ok"] is False
    assert again.hand_over_timed() == 0 and fake.timed == []
    assert json.loads(again.offline_next()[1])["entry_id"] == "song"
    assert "spot" not in again.done and "time" not in again.done
    # A minute or two behind is an ordinary clock
    clock["now"] = now - 60
    assert again.clock_ok()
    # Shabbos had begun when the plan was fetched: silence, whatever the clock says
    clock["now"] = now
    fake.up = True
    fake.plan = {"generated_at": now, "blackouts": [{"start": now - 10, "end": now + 9000, "name": "Shabbos"}],
                 "entries": [fake.entry("song2", 1)]}
    relay.sync_plan()
    relay.fetch_plan_audio()
    fake.up = False
    clock["now"] = now - 86400
    again = _again(fake, relay, clock)
    assert again.offline_next()[1].endswith(b'silence-60s.wav", "entry_id": ""}')


def test_start_reports_are_kept_and_sent_in_order_when_the_line_returns(rig):
    fake, relay, clock = rig
    fake.up = False
    assert relay.hello(b'{"proto":2}')[0] == 200
    for name in ("a", "b"):
        assert relay.started(json.dumps({"entry_id": name, "lane": "main"}).encode())[0] == 200
    relay.started(json.dumps({"entry_id": "", "lane": "emergency"}).encode())
    assert relay.status()["reports_kept"] == 2
    fake.up = True
    # A report made before the kept ones went out queues behind them
    relay.started(json.dumps({"entry_id": "c", "lane": "main"}).encode())
    assert [c for c in fake.calls if "/started" in c[1]] == []
    assert relay.sync_plan()
    relay.catch_up()
    posts = [(c[1], json.loads(c[2])) for c in fake.calls if c[0] == "POST"]
    assert posts[0][0] == "/hello"
    assert [p[1]["entry_id"] for p in posts[1:]] == ["a", "b", "c"]
    assert all(p[1]["at"] == clock["now"] for p in posts[1:])
    # Marked as kept (recorded at their own time); all but the newest go up quietly
    assert all(p[1]["kept"] for p in posts[1:])
    assert [bool(p[1].get("more")) for p in posts[1:]] == [True, True, False]
    assert relay.status()["reports_kept"] == 0
    # and they are played from nowhere again
    assert set(relay.done) >= {"a", "b", "c"}


def test_line_back_the_kept_reports_go_up_before_the_site_is_asked_again(rig):
    """Simulator 2026-11-19: the box came back online and asked the site for its next
    item before its kept start reports were sent, so the site still held what aired
    offline as waiting: the 6 PM time check aired again at 6:24 PM (and 17 songs after a
    16-hour outage). Until the reports are up, the box keeps to its local plan."""
    fake, relay, _ = rig
    fake.plan = {"blackouts": [], "entries": [fake.entry("a", 1), fake.entry("b", 2), fake.entry("c", 3)]}
    assert relay.sync_plan()
    relay.fetch_plan_audio()
    fake.up = False
    status, item = _next(relay)
    assert status == 200 and item["entry_id"] == "a"
    relay.started(json.dumps({"entry_id": "a", "lane": "main"}).encode())
    assert relay.status()["reports_kept"] == 1

    fake.up = True
    # The site, not yet told, would hand "a" again
    fake.next_answer = {"uri": fake.plan["entries"][0]["uri_main"], "entry_id": "a"}
    status, item = _next(relay)
    assert status == 200 and item["entry_id"] == "b"  # from the plan, not the site
    relay.catch_up_soon()
    for _ in range(50):
        if relay.status()["reports_kept"] == 0 and not relay._catching.locked():
            break
        time.sleep(0.05)
    assert relay.status()["reports_kept"] == 0
    assert [json.loads(c[2])["entry_id"] for c in fake.calls if c[1] == "/started"] == ["a"]
    # Reports up: the site is asked again
    fake.next_answer = {"uri": fake.plan["entries"][2]["uri_main"], "entry_id": "c"}
    assert _next(relay)[1]["entry_id"] == "c"


def test_http_front_door_needs_the_token(rig):
    import urllib.error
    import urllib.request

    fake, relay, _ = rig
    server = kbr_relay.serve(relay, 0)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/api/v1/playout/sid/next")
        assert err.value.code == 403
        url = fake.url("a")
        fake.next_answer = {"uri": url, "entry_id": "a"}
        req = urllib.request.Request(base + "/api/v1/playout/sid/next?given=x", headers=HEADERS)
        assert json.loads(urllib.request.urlopen(req).read())["entry_id"] == "a"
        assert json.loads(urllib.request.urlopen(base + "/relay/status").read())["online"] is True
    finally:
        server.shutdown()


# ── Commands to a box with no inbound line ───────────────────────────────────


@pytest.fixture
def tunnel_station(monkeypatch):
    from app.services import playout_engine, playout_tunnel

    sid = uuid.uuid4()
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", str(sid))
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "PLAYOUT_RELAY_STATION_ID", f" {sid} ")
    playout_engine.reset_state()
    playout_tunnel.reset()
    yield sid
    playout_tunnel.reset()


@pytest.mark.asyncio
async def test_command_goes_through_the_relay_when_one_is_polling(client, tunnel_station):
    import asyncio

    from app.services import playout_engine, playout_tunnel

    sid = tunnel_station
    url = f"/api/v1/playout/{sid}/commands/poll"
    assert (await client.post(url, json={"wait": 0})).status_code == 403
    assert not playout_tunnel.connected(sid)
    assert (await client.post(url, json={"wait": 0}, headers=HEADERS)).json() == {"commands": []}
    assert playout_tunnel.connected(sid)

    async def relay_once(status=200, body=None):
        got = (await client.post(url, json={"wait": 5}, headers=HEADERS)).json()["commands"]
        answers = [{"id": c["id"], "status": status, "body": body or {"ok": True}} for c in got]
        await client.post(url, json={"wait": 0, "answers": answers}, headers=HEADERS)
        return got

    poller = asyncio.create_task(relay_once())
    await asyncio.sleep(0.05)  # the relay's call is being held open
    started = asyncio.get_running_loop().time()
    assert await playout_engine.engine_skip(sid, fade_seconds=0.5) is True
    assert asyncio.get_running_loop().time() - started < 1.0
    got = await poller
    assert [(c["path"], c["payload"]) for c in got] == [("/skip", {"fade": 0.5})]

    poller = asyncio.create_task(relay_once(body={"ok": True, "lane": "main", "entry": "e1"}))
    assert (await playout_engine.engine_status(sid))["entry"] == "e1"
    await poller

    # The engine refused it: the command reports failure
    poller = asyncio.create_task(relay_once(status=502))
    assert await playout_engine.engine_flush(sid) is False
    await poller


@pytest.mark.asyncio
async def test_command_nobody_fetched_is_withdrawn_not_delivered_late(client, tunnel_station, monkeypatch):
    from app.services import playout_tunnel

    sid = tunnel_station
    url = f"/api/v1/playout/{sid}/commands/poll"
    await client.post(url, json={"wait": 0}, headers=HEADERS)
    assert await playout_tunnel.send(sid, "/skip", {}, timeout=0.1) is None
    assert (await client.post(url, json={"wait": 0}, headers=HEADERS)).json() == {"commands": []}
    # A relay that stopped polling: commands go by HTTPS again
    monkeypatch.setattr(playout_tunnel, "CONNECTED_SECONDS", 0.0)
    assert not playout_tunnel.connected(sid)


def test_relay_fetches_commands_gives_them_to_the_engine_and_answers(rig):
    import time as _time

    fake, relay, _ = rig
    polls = []

    def call(method, path, body=None, timeout=5.0):
        assert (method, path) == ("POST", "/commands/poll")
        polls.append(json.loads(body))
        if len(polls) == 1:
            return 200, json.dumps({"commands": [
                {"id": "c1", "path": "/timed", "payload": {"uri": "u", "at": 5.0, "id": "x"}},
                {"id": "c2", "path": "/../etc", "payload": {}},
            ]}).encode()
        if sum(len(p["answers"]) for p in polls) >= 2:
            relay.stop.set()
        return 200, b'{"commands": []}'

    relay._call = call
    relay.run_commands(wait=1.0)
    deadline = _time.time() + 5
    while relay.status()["commands_done"] < 2 and _time.time() < deadline:
        _time.sleep(0.02)
    assert fake.timed == [{"uri": "u", "at": 5.0, "id": "x"}]
    assert polls[0]["answers"] == []
    answers = sorted((a for p in polls for a in p["answers"]), key=lambda a: a["id"])
    assert answers == [
        {"id": "c1", "status": 200, "body": {"ok": True}},
        {"id": "c2", "status": 400, "body": None},
    ]
    # Each answer went back in a short call of its own, not held for the next long poll
    assert all(p["wait"] == 0 for p in polls if p["answers"])
    assert relay.status()["commands_done"] == 2


def test_slow_timed_does_not_hold_the_command_behind_it(tmp_path):
    """A /timed waits for its file (up to the caller's wait); a flush or Next made
    meanwhile reaches the engine at once and is answered first."""
    import http.server
    import time as _time

    seen = []

    class Engine(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path == "/timed":
                _time.sleep(1.0)
            seen.append((self.path, _time.monotonic()))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Engine)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        relay = kbr_relay.Relay(
            "http://127.0.0.1:9/api/v1", "s", "t", tmp_path,
            f"http://127.0.0.1:{srv.server_address[1]}",
        )
        order = []

        def call(method, path, body=None, timeout=5.0):
            got = json.loads(body)
            order.extend(a["id"] for a in got["answers"])
            if got["wait"] and not order:
                return 200, json.dumps({"commands": [
                    {"id": "slow", "path": "/timed", "payload": {}, "wait": 6.0},
                    {"id": "quick", "path": "/flush", "payload": {}, "wait": 4.0},
                ]}).encode()
            return 200, b'{"commands": []}'

        relay._call = call
        started = _time.monotonic()
        assert relay._poll(1.0) is True
        while len(order) < 2 and _time.monotonic() - started < 5:
            _time.sleep(0.02)
        assert order == ["quick", "slow"]
        assert dict(seen)["/flush"] - started < 0.5
    finally:
        srv.shutdown()


@pytest.mark.asyncio
async def test_station_not_named_for_the_relay_keeps_its_commands(client, tunnel_station, monkeypatch):
    """A box set up with the wrong station id (say Main's) cannot take that station's
    commands away from its engine on the stream server."""
    from app.services import playout_engine, playout_tunnel

    sid = tunnel_station
    url = f"/api/v1/playout/{sid}/commands/poll"
    monkeypatch.setattr(settings, "PLAYOUT_RELAY_STATION_ID", "")
    resp = await client.post(url, json={"wait": 0}, headers=HEADERS)
    assert resp.status_code == 403
    assert not playout_tunnel.connected(sid)
    sent = []

    # the command still leaves by PLAYOUT_ENGINE_URL
    class Resp:
        status_code = 200

    class Client:
        async def post(self, u, **kw):
            sent.append(u)
            return Resp()

    monkeypatch.setattr(playout_engine, "_client", lambda: Client())
    assert await playout_engine.engine_flush(sid) is True
    assert sent == [f"http://engine.invalid/{sid}/flush"]

    # Named: the switch is written to the log, both ways
    monkeypatch.setattr(settings, "PLAYOUT_RELAY_STATION_ID", str(sid))
    lines = []
    monkeypatch.setattr(playout_tunnel.logger, "warning", lambda msg, *a: lines.append(msg % a))
    assert (await client.post(url, json={"wait": 0}, headers=HEADERS)).status_code == 200
    assert playout_tunnel.connected(sid)
    monkeypatch.setattr(playout_tunnel, "CONNECTED_SECONDS", 0.0)
    assert not playout_tunnel.connected(sid)
    text = " ".join(lines)
    assert len(lines) == 2
    assert "now go to its local box's relay" in text and "stopped polling" in text


@pytest.mark.asyncio
async def test_slow_command_by_relay_is_taken_only_when_it_was_fetched(client, tunnel_station):
    """Same rules as a command sent by HTTPS: `wait` is the whole wait, and a /timed whose
    file is still loading counts as taken (slow_is_ok), not refused. A command the relay
    never fetched was never given to the engine: that stays a failure."""
    import asyncio

    from app.services import playout_engine, playout_tunnel

    sid = tunnel_station
    url = f"/api/v1/playout/{sid}/commands/poll"
    await client.post(url, json={"wait": 0}, headers=HEADERS)
    assert playout_tunnel.connected(sid)

    async def timed(**kw):
        return await playout_engine.engine_timed(sid, "http://x/a.mp3", 0.0, "e1", **kw)

    # Nobody fetched it within the wait: not taken, whatever slow_is_ok says
    started = asyncio.get_running_loop().time()
    assert await timed(wait=0.3, slow_is_ok=True) is False
    assert 0.25 < asyncio.get_running_loop().time() - started < 1.0

    # The relay fetched it and the engine is still loading the file when the wait ends
    async def fetch_only():
        return (await client.post(url, json={"wait": 5}, headers=HEADERS)).json()["commands"]

    poller = asyncio.create_task(fetch_only())
    await asyncio.sleep(0.05)
    assert await timed(wait=0.3, slow_is_ok=True) is True
    got = await poller
    assert got[0]["path"] == "/timed" and 0.2 < got[0]["wait"] <= 0.5  # the relay's wait for its engine

    poller = asyncio.create_task(fetch_only())
    await asyncio.sleep(0.05)
    assert await timed(wait=0.3) is False  # a caller that needs the answer does not get a yes
    await poller

    # The relay's own wait for the engine ran out first: it answers "still working"
    async def relay_says_working():
        got = await fetch_only()
        await client.post(url, json={"wait": 0, "answers": [
            {"id": got[0]["id"], "status": playout_tunnel.STILL_WORKING}]}, headers=HEADERS)

    poller = asyncio.create_task(relay_says_working())
    await asyncio.sleep(0.05)
    assert await timed(wait=2.0, slow_is_ok=True) is True
    await poller
    poller = asyncio.create_task(relay_says_working())
    await asyncio.sleep(0.05)
    assert await timed(wait=2.0) is False
    await poller


def test_relay_reports_an_engine_still_loading_as_working_not_failed(tmp_path):
    """The relay waits for its engine as long as the backend waits (the command's `wait`);
    an engine that took the command and has not answered yet is 202, a dead one 502."""
    import http.server
    import threading
    import time as _time

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            _time.sleep(0.2 if self.path == "/quick" else 1.5)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
            except OSError:
                pass

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        relay = kbr_relay.Relay(
            "http://127.0.0.1:9/api/v1", "s", "t", tmp_path,
            f"http://127.0.0.1:{srv.server_address[1]}",
        )
        assert relay._engine_call("/quick", {}, wait=1.0) == (200, {"ok": True})
        assert relay._engine_call("/timed", {}, wait=0.5) == (202, None)
        relay.engine_control = "http://127.0.0.1:9"
        assert relay._engine_call("/timed", {}, wait=0.5)[0] == 502
    finally:
        srv.shutdown()


def test_relay_tells_the_site_how_the_box_is_doing(rig):
    fake, relay, clock = rig
    now = clock["now"]
    fake.plan = {"generated_at": now, "blackouts": [], "entries": [fake.entry("a", 1), fake.entry("b", 2)]}
    relay.sync_plan()
    relay.download(fake.url("a"))
    assert relay._poll(0) is True
    sent = json.loads([c for c in fake.calls if "/commands/poll" in c[1]][-1][2])["health"]
    assert sent["clock_ok"] is True and sent["plan_entries"] == 2 and sent["plan_audio_missing"] == 1
    assert sent["reports_kept"] == 0 and sent["disk_free_gb"] > 0 and sent["up_seconds"] >= 0
    assert sent["engine_ok"] is True  # the fake engine answers /status
    # The clock falls behind the plan (started with no internet and a flat battery)
    clock["now"] = now - 3600
    assert relay.health(max_age=0)["clock_ok"] is False


@pytest.mark.asyncio
async def test_box_health_is_kept_shown_and_alerted(client, db_session, tunnel_station, monkeypatch):
    from app.services import playout_engine, playout_tunnel
    from app.services.air_watch import SERIOUS, AirWatch

    sid = tunnel_station
    st = Station(id=sid, name="FM", timezone="America/New_York", automation_config={})
    db_session.add(st)
    await db_session.commit()
    url = f"/api/v1/playout/{sid}/commands/poll"

    async def engine_status(_sid):
        return {"ok": True, "entry_id": None}  # in step with the site: nothing on air
    monkeypatch.setattr(playout_engine, "engine_status", engine_status)

    assert playout_tunnel.box_health(sid) is None
    watch = AirWatch()
    _check = watch.check_station

    async def box_only(*a, **kw):  # the station has nothing on air here: other findings are not the point
        return [f for f in await _check(*a, **kw) if f.kind.startswith("box_")]
    watch.check_station = box_only
    assert [f.kind for f in await watch.check_station(db_session, st, datetime.now(UTC), mono=0.0)] == []

    good = {"clock_ok": True, "engine_ok": True, "plan_age_seconds": 12.5, "plan_entries": 800,
            "plan_audio_missing": 0, "reports_kept": 0, "disk_free_gb": 210.4, "up_seconds": 99}
    body = {"wait": 0, "health": {**good, "password": "x", "plan_entries": "800"}}
    assert (await client.post(url, json=body, headers=HEADERS)).status_code == 200
    kept = playout_tunnel.box_health(sid)
    assert {k: kept[k] for k in good} == good and "password" not in kept and kept["seen_seconds_ago"] < 5
    assert playout_tunnel.box_problems(kept) == []
    assert [f.kind for f in await watch.check_station(db_session, st, datetime.now(UTC), mono=1.0)] == []

    # Public status carries it for the station list and the control room
    from app.api.v1 import playout_status
    playout_status._cache.clear()
    shown = (await client.get("/api/v1/playout-status")).json()["stations"][str(sid)]
    assert shown["box"]["disk_free_gb"] == 210.4 and shown["box"]["clock_ok"] is True

    # Clock wrong and disk nearly full: reported at once, once per spell; missing audio
    # only when it has been missing for 15 minutes
    bad = {**good, "clock_ok": False, "disk_free_gb": 1.2, "plan_audio_missing": 3}
    await client.post(url, json={"wait": 0, "health": bad}, headers=HEADERS)
    found = await watch.check_station(db_session, st, datetime.now(UTC), mono=10.0)
    assert sorted(f.kind for f in found) == ["box_clock", "box_disk"]
    assert "box_clock" in SERIOUS and "music only" in [f for f in found if f.kind == "box_clock"][0].detail
    again = await watch.check_station(db_session, st, datetime.now(UTC), mono=20.0)
    assert {f.key for f in again} == {f.key for f in found}  # same spell, same alert
    late = await watch.check_station(db_session, st, datetime.now(UTC), mono=10.0 + 15 * 60 + 1)
    assert sorted(f.kind for f in late) == ["box_audio", "box_clock", "box_disk"]
    # Also during a live show: the box's clock does not wait for the show to end
    st.automation_config = {"live_show_id": "x"}
    assert "box_clock" in [f.kind for f in await watch.check_station(db_session, st, datetime.now(UTC), mono=30.0)]
    st.automation_config = {}
    # Put right, then wrong again: a new alert
    await client.post(url, json={"wait": 0, "health": good}, headers=HEADERS)
    assert [f.kind for f in await watch.check_station(db_session, st, datetime.now(UTC), mono=40.0)] == []
    await client.post(url, json={"wait": 0, "health": bad}, headers=HEADERS)
    new = await watch.check_station(db_session, st, datetime.now(UTC), mono=50.0)
    assert {f.key for f in new if f.kind == "box_clock"} != {f.key for f in found if f.kind == "box_clock"}
    # A station with no box reports nothing of the kind
    monkeypatch.setattr(settings, "PLAYOUT_RELAY_STATION_ID", "")
    assert playout_tunnel.box_health(sid) is None
    assert [f.kind for f in await watch.check_station(db_session, st, datetime.now(UTC), mono=60.0)] == []


# ── A week without the internet (wiki/local-fm-box.md, "Days offline") ───────


def _ext(fake, now, timed=(), songs=("s1", "s2", "s3"), blackouts=()):
    """An extension as GET /plan?days= gives it: timed items past the queue, the songs
    for each hour of the day, Shabbos windows."""
    template = "00000000-0000-0000-0000-000000000000"
    lib = {
        name: {"title": name, "duration": 200.0, "kind": "music", "url": fake.url(name),
               "uri": f'annotate:kbr_entry="{template}",kbr_kind="music",kbr_asset="{name}":{fake.url(name)}'}
        for name in songs
    }
    return {
        "days": 8, "until": now + 8 * 86400, "generated_at": now, "timed": list(timed),
        "music": {str(h): list(songs) for h in range(24)}, "songs": lib,
        "template_entry": template, "blackouts": list(blackouts),
    }


def test_offline_past_the_end_of_the_queue_the_box_carries_on_for_days(rig):
    """The queue holds about two days. Past it the box hands over the timed items worked
    out for the days after (time checks, blocks), keeps Shabbos from its month of windows
    and draws the station's songs for the hour, the longest-rested first, each with an
    id of its own and the song's item named for the site's log."""
    fake, relay, clock = rig
    now = clock["now"]
    tc = fake.entry("tc-day5", None, at=now + 5 * 86400, mode="hard", kind="jingle")
    tc["asset_id"] = "asset-tc"
    dup = fake.entry("dup", None, at=now + 600, mode="hard", kind="jingle")  # the queue has this minute
    fake.plan = {"blackouts": [], "entries": [
        fake.entry("a", 1), fake.entry("q-tc", 2, at=now + 600, mode="hard", kind="jingle"),
    ]}
    fake.extension = _ext(fake, now, timed=[tc, dup], blackouts=[
        {"start": now + 3 * 86400, "end": now + 3 * 86400 + 90000, "name": "Shabbos"}])
    assert relay.sync_extension()
    assert relay.extension["timed"][0]["entry_id"] == "tc-day5"
    assert relay.fetch_plan_audio() == 0
    assert [e["entry_id"] for e in relay.timed_entries()] == ["q-tc", "tc-day5"]
    fake.up = False
    assert _next(relay)[1]["entry_id"] == "a"
    titles = []
    for _ in range(3):
        status, item = _next(relay)
        assert status == 200
        relay.started(json.dumps({"entry_id": item["entry_id"], "lane": "main"}).encode())
        titles.append(relay.local_items[item["entry_id"]]["asset_id"])
        assert 'kbr_entry="00000000' not in item["uri"] and item["entry_id"] in item["uri"]
        clock["now"] += 200
    assert sorted(titles) == ["s1", "s2", "s3"]  # none twice while another has rested longer
    kept = [json.loads(line) for line in relay.spool_path.read_text().splitlines()]
    assert [k["asset_id"] for k in kept] == titles
    # Shabbos from the extension's windows: silence
    clock["now"] = now + 3 * 86400 + 100
    assert relay.in_blackout(relay.now())
    assert '"Off air"' in _next(relay)[1]["uri"] or "silence" in _next(relay)[1]["uri"]
    # Day five's time check reaches the engine's timed lane on its second
    clock["now"] = now + 5 * 86400 - 40
    assert relay.hand_over_timed() == 1
    assert fake.timed[-1]["id"] == "tc-day5" and fake.timed[-1]["at"] == now + 5 * 86400
    st = relay.status()
    assert st["songs_for_rotation"] == 3 and st["songs_on_disk"] == 3


def test_offline_a_song_rests_before_it_comes_back(rig):
    fake, relay, clock = rig
    now = clock["now"]
    fake.plan = {"blackouts": [], "entries": []}
    fake.extension = _ext(fake, now, songs=("s1", "s2"))
    relay.sync_extension()
    relay.fetch_plan_audio()
    fake.up = False
    first = relay.local_items[_next(relay)[1]["entry_id"]]["asset_id"]
    clock["now"] += 200
    second = relay.local_items[_next(relay)[1]["entry_id"]]["asset_id"]
    assert first != second
    clock["now"] += 300
    # Both aired in the last 6 hours: the one aired longest ago comes first
    assert relay.local_items[_next(relay)[1]["entry_id"]]["asset_id"] == first


def test_offline_a_shiur_paused_by_the_time_check_carries_on_after_it(rig):
    """With the line down a hard item cut the shiur on air and it never came back
    (simulator, 16 h offline, 2026-11-19). Now the box does what the site does: the
    shiur carries on after the time check from 3 s before where it stopped, and the
    song the engine had pre-loaded is played later instead."""
    fake, relay, clock = rig
    now = clock["now"]
    at = now + 1200
    shiur = fake.entry("shiur", 1, kind="shiur")
    shiur.update(duration=3600.0, long=True, asset_id="asset-shiur")
    shiur["uri_main"] = f'annotate:kbr_entry="shiur",liq_cue_in="2.000",title="Daf":{fake.url("shiur")}'
    fake.plan = {"blackouts": [], "long_items": "pause", "entries": [
        shiur, fake.entry("song", 2), fake.entry("tc", 3, at=at, mode="hard", kind="jingle"),
    ]}
    relay.sync_plan()
    relay.fetch_plan_audio()
    fake.up = False
    assert _next(relay)[1]["entry_id"] == "shiur"
    relay.started(json.dumps({"entry_id": "shiur", "lane": "main", "at": now}).encode())
    assert _next(relay)[1]["entry_id"] == "song"  # pre-loaded
    clock["now"] = at - 44
    assert relay.hand_over_timed() == 1
    resume = relay.resume
    assert resume and resume["asset_id"] == "asset-shiur"
    _, item = _next(relay)
    assert item["entry_id"] == resume["entry_id"]
    assert 'liq_cue_in="1199.000"' in item["uri"]  # 2 s cue-in + 1200 s - 3 s rewind
    assert item["uri"].count("liq_cue_in") == 1
    assert _next(relay)[1]["entry_id"] == "song"  # the pre-loaded song plays after all
    # Only one resume however often the hand-over runs
    assert relay.hand_over_timed() == 0 and relay.resume is None


def test_offline_station_id_waits_for_a_song_and_stays_clear_of_breaks(rig):
    fake, relay, clock = rig
    now = clock["now"]
    sid_entry = fake.entry("id", 2, kind="jingle")
    sid_entry["station_id"] = True
    fake.plan = {"blackouts": [], "entries": [
        fake.entry("song1", 1), sid_entry, fake.entry("song2", 3), fake.entry("song3", 4),
        fake.entry("brk", 5, at=now + 2000, mode="soft", kind="spot"),
    ]}
    relay.sync_plan()
    relay.fetch_plan_audio()
    fake.up = False
    assert _next(relay)[1]["entry_id"] == "song1"
    relay.started(json.dumps({"entry_id": "song1", "lane": "main", "at": now}).encode())
    assert _next(relay)[1]["entry_id"] == "id"  # after a song, nothing timed near
    relay.started(json.dumps({"entry_id": "id", "lane": "main", "at": now + 10}).encode())
    # an ID never follows a spoken item: the next song
    assert _next(relay)[1]["entry_id"] == "song2"


def test_two_catch_ups_at_once_send_each_kept_report_once(rig):
    """The sync loop and an engine ask both started a catch-up when the line came back:
    every play of the outage reached the site twice (two play log rows each)."""
    fake, relay, _ = rig
    fake.up = False
    relay.started(b'{"entry_id":"x","lane":"main"}')
    fake.up = True
    relay._catching.acquire()
    try:
        relay.catch_up()  # another catch-up is running: this one does nothing
        assert not [c for c in fake.calls if c[1] == "/started"]
    finally:
        relay._catching.release()
    relay.catch_up()
    assert len([c for c in fake.calls if c[1] == "/started"]) == 1


@pytest.mark.asyncio
async def test_plan_extension_works_out_the_week_from_recordings_that_exist(client, db_session, monkeypatch):
    """GET /plan?days=8: past the queue's two days, the hourly time check from the
    hour's own clip, weather only for days that have a report (no report written now
    for a day five days away), the songs by hour, and a month of Shabbos."""
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", TOKEN)
    from app.services import playout_plan

    playout_plan._extension_cache.clear()
    station = Station(id=uuid.uuid4(), name="FM", timezone="America/New_York",
                      automation_config={"hourly_time_announcement": True, "weather_enabled": True,
                                         "weather_interval_minutes": 60})
    db_session.add(station)
    now = datetime.now(UTC)
    for hour in range(24):
        db_session.add(Asset(id=uuid.uuid4(), title=f"The time is {hour}", asset_type="jingle",
                             category="time_announcement", duration=6.0,
                             metadata_extra={"time_of_day": f"{hour:02d}", "source": "upload"},
                             file_path=f"https://cdn.example.com/t{hour}.mp3"))
    from zoneinfo import ZoneInfo

    day3 = (now + timedelta(days=3)).astimezone(ZoneInfo("America/New_York")).date()
    weather = Asset(id=uuid.uuid4(), title=f"Weather {day3}", asset_type="spot", category="weather_spot",
                    duration=30.0, metadata_extra={"slot_key": day3.isoformat(), "source": "upload"},
                    file_path="https://cdn.example.com/w3.mp3")
    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0, file_path="https://cdn.example.com/a.mp3",
                 asset_type="music")
    db_session.add_all([weather, song])
    await db_session.flush()
    db_session.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1, status="pending", source="auto"),
        HolidayWindow(id=uuid.uuid4(), name="Far Shabbos", is_blackout=True,
                      start_datetime=now + timedelta(days=20), end_datetime=now + timedelta(days=21)),
    ])
    await db_session.commit()

    resp = await client.get(f"/api/v1/playout/{station.id}/plan", headers=HEADERS, params={"days": 8})
    assert resp.status_code == 200
    ext = resp.json()["extension"]
    timed = ext["timed"]
    assert timed and all(now.timestamp() + 47 * 3600 < e["at"] <= now.timestamp() + 8 * 86400 + 60 for e in timed)
    checks = [e for e in timed if e["title"].startswith("The time is")]
    assert len(checks) >= 6 * 24 - 2
    assert all(e["mode"] == "hard" and 'kbr_lane="timed"' in e["uri_timed"] for e in checks)
    weathers = [e for e in timed if e["title"].startswith("Weather")]
    assert weathers and {datetime.fromtimestamp(e["at"], ZoneInfo("America/New_York")).date() for e in weathers} == {day3}
    assert len({e["entry_id"] for e in timed}) == len(timed)
    assert ext["songs"][str(song.id)]["uri"].startswith('annotate:kbr_entry="00000000-0000-0000-0000-000000000000"')
    assert any(str(song.id) in ids for ids in ext["music"].values())
    assert [b["name"] for b in ext["blackouts"]] == ["Far Shabbos"]
    # The same ids on the next fetch, so the box knows what already aired
    playout_plan._extension_cache.clear()
    again = (await client.get(f"/api/v1/playout/{station.id}/plan", headers=HEADERS, params={"days": 8})).json()
    assert [e["entry_id"] for e in again["extension"]["timed"][:5]] == [e["entry_id"] for e in timed[:5]]


@pytest.mark.asyncio
async def test_kept_report_of_an_item_not_in_the_queue_is_logged_by_its_asset(client, db_session, monkeypatch):
    """A song removed on the site while the box was offline still aired: its kept start
    report names the item and the site logs it at the time it aired."""
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", TOKEN)
    from sqlalchemy import select

    from app.models.play_log import PlayLog

    station = Station(id=uuid.uuid4(), name="FM", timezone="America/New_York")
    a = Asset(id=uuid.uuid4(), title="Removed", duration=200.0, file_path="https://cdn.example.com/r.mp3", asset_type="music")
    b = Asset(id=uuid.uuid4(), title="Drawn", duration=200.0, file_path="https://cdn.example.com/d.mp3", asset_type="music")
    db_session.add_all([station, a, b])
    await db_session.commit()
    from app.services import playout_engine

    sid, aid, bid = station.id, str(a.id), str(b.id)
    t0 = time.time() - 3600
    await playout_engine.handle_started(db_session, sid, str(uuid.uuid4()), "main", at=t0,
                                        kept=True, quiet=True, asset_id=aid)
    await playout_engine.handle_started(db_session, sid, str(uuid.uuid4()), "main", at=t0 + 200,
                                        kept=True, quiet=True, asset_id=bid)
    db_session.expire_all()
    logs = (await db_session.execute(select(PlayLog).where(PlayLog.station_id == sid))).scalars().all()
    assert [(str(p.asset_id), round(p.start_utc.replace(tzinfo=UTC).timestamp() - t0)) for p in logs] == [(aid, 0)]
