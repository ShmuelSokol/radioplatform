"""The local FM box: the plan the backend gives it, and the relay that keeps the engine
fed when the backend cannot be reached (infra/local/kbr_relay.py, wiki/local-fm-box.md)."""

import importlib.util
import json
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
        self.timed = []
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
                if not fake.up:
                    return self._out(503, b"down")
                fake.calls.append((self.command, path.split("/playout/sid", 1)[-1], body))
                if "/plan" in path:
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
    assert relay.status()["reports_kept"] == 0
    # and they are played from nowhere again
    assert set(relay.done) >= {"a", "b", "c"}


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
        relay.stop.set()
        return 200, b'{"commands": []}'

    relay._call = call
    relay.run_commands(wait=1.0)
    assert fake.timed == [{"uri": "u", "at": 5.0, "id": "x"}]
    assert polls[0]["answers"] == []
    assert polls[1]["answers"] == [
        {"id": "c1", "status": 200, "body": {"ok": True}},
        {"id": "c2", "status": 400, "body": None},
    ]
    assert relay.status()["commands_done"] == 2


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
