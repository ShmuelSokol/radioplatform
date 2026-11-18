"""The live-show WebSockets end to end, through the real app (B-LIVESHOWSW-1/2/3/5, V2-74).

The sockets are driven in the test's own event loop by a small ASGI client, so they
share the test database session with the HTTP client (the screener's buttons)."""
import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
import pytest_asyncio

import app.api.v1.queue as queue_module
from app.api.v1 import call_in as call_in_api
from app.api.v1 import live_shows_ws
from app.config import settings
from app.core.security import create_access_token, hash_password
from app.main import create_app
from app.models.call_in_request import CallInRequest, CallStatus
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.station import Station
from app.models.user import User, UserRole
from app.services import call_in as call_in_service


class WS:
    """A WebSocket client talking straight to the ASGI app."""

    def __init__(self, app, path: str):
        self.inbox: asyncio.Queue = asyncio.Queue()   # to the app
        self.outbox: asyncio.Queue = asyncio.Queue()  # from the app
        self.closed_code: int | None = None
        scope = {
            "type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "path": path,
            "raw_path": path.encode(), "root_path": "", "query_string": b"",
            "headers": [(b"host", b"test")], "client": ("127.0.0.1", 50000),
            "server": ("test", 80), "subprotocols": [],
        }
        self.task = asyncio.create_task(app(scope, self.inbox.get, self.outbox.put))

    async def connect(self) -> None:
        await self.inbox.put({"type": "websocket.connect"})
        msg = await self._next()
        assert msg["type"] == "websocket.accept", msg

    async def _next(self, timeout: float = 3.0) -> dict:
        return await asyncio.wait_for(self.outbox.get(), timeout)

    async def send_text(self, text: str) -> None:
        await self.inbox.put({"type": "websocket.receive", "text": text})

    async def send_json(self, data: dict) -> None:
        await self.send_text(json.dumps(data))

    async def send_bytes(self, data: bytes) -> None:
        await self.inbox.put({"type": "websocket.receive", "bytes": data})

    async def receive(self, timeout: float = 3.0) -> dict:
        msg = await self._next(timeout)
        if msg["type"] == "websocket.close":
            self.closed_code = msg.get("code", 1000)
        return msg

    async def receive_json(self, timeout: float = 3.0) -> dict:
        while True:
            msg = await self.receive(timeout)
            assert msg["type"] == "websocket.send", msg
            if msg.get("text") is not None:
                return json.loads(msg["text"])

    async def receive_bytes(self, timeout: float = 3.0) -> bytes:
        while True:
            msg = await self.receive(timeout)
            assert msg["type"] == "websocket.send", msg
            if msg.get("bytes") is not None:
                return msg["bytes"]

    async def expect_close(self, timeout: float = 3.0) -> int:
        while True:
            msg = await self.receive(timeout)
            if msg["type"] == "websocket.close":
                return msg.get("code", 1000)

    async def nothing(self, wait: float = 0.15) -> bool:
        await asyncio.sleep(wait)
        return self.outbox.empty()

    async def disconnect(self) -> None:
        await self.inbox.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(self.task, 3.0)
        except Exception:
            pass


def pcm(value: int, n: int = 2040) -> bytes:
    return np.full(n, value, dtype="<i2").tobytes()


def samples(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype="<i2")


@pytest.fixture(autouse=True)
def _clean_relay(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    relay = live_shows_ws.live_relay
    for d in (relay.listeners, relay.on_air, relay.hosts, relay.callers,
              live_shows_ws.live_show_manager.active_connections):
        d.clear()
    call_in_api._recent.clear()
    yield
    for d in (relay.listeners, relay.on_air, relay.hosts, relay.callers,
              live_shows_ws.live_show_manager.active_connections):
        d.clear()


@pytest_asyncio.fixture
async def ws_app(db_session, monkeypatch):
    """The app, with the sockets' own database sessions being the test session."""
    import app.db.session as session_module

    async def shared():
        yield db_session

    monkeypatch.setattr(session_module, "get_async_session", shared)
    monkeypatch.setattr(live_shows_ws, "get_async_session", shared)
    return create_app()


async def _show(db, status=LiveShowStatus.LIVE, calls_enabled=True):
    station = Station(id=uuid.uuid4(), name=f"Main {uuid.uuid4().hex[:6]}", timezone="America/New_York")
    db.add(station)
    await db.flush()
    now = datetime.now(UTC)
    show = LiveShow(id=uuid.uuid4(), station_id=station.id, title="Live", status=status,
                    scheduled_start=now, scheduled_end=now + timedelta(hours=1), calls_enabled=calls_enabled,
                    actual_start=now if status == LiveShowStatus.LIVE else None)
    db.add(show)
    await db.commit()
    return station, show


async def _user(db, role=UserRole.ADMIN) -> User:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@t.com", hashed_password=hash_password("x"),
                role=role, is_active=True)
    db.add(user)
    await db.commit()
    return user


def _token(user: User) -> str:
    return create_access_token(str(user.id))


async def _authed(app, path: str, token: str) -> WS:
    ws = WS(app, path)
    await ws.connect()
    await ws.send_json({"type": "auth", "token": token})
    return ws


# ---------- B-LIVESHOWSW-1 + 3: host mic -> station listeners ----------

@pytest.mark.asyncio
async def test_host_audio_reaches_the_station_listeners(ws_app, db_session):
    station, show = await _show(db_session, status=LiveShowStatus.SCHEDULED)
    _, other_show = await _show(db_session)
    admin = await _user(db_session)

    listener = WS(ws_app, f"/api/v1/ws/live/station/{station.id}/listen")
    await listener.connect()
    assert await listener.receive_json() == {"type": "live_state", "live": False, "show_id": None,
                                             "sample_rate": 24000}
    elsewhere = WS(ws_app, f"/api/v1/ws/live/station/{other_show.station_id}/listen")
    await elsewhere.connect()
    await elsewhere.receive_json()

    # The host opens the mic: a scheduled show goes on air and listeners are told
    host = await _authed(ws_app, f"/api/v1/ws/live/{show.id}/audio", _token(admin))
    state = await listener.receive_json()
    assert state["live"] is True and state["show_id"] == str(show.id)
    await db_session.refresh(show)
    assert show.status == LiveShowStatus.LIVE
    station_row = await db_session.get(Station, station.id)
    await db_session.refresh(station_row)
    assert (station_row.automation_config or {}).get("live_show_id") == str(show.id)

    frame = pcm(1234)
    await host.send_bytes(frame)
    assert await listener.receive_bytes() == frame
    assert await elsewhere.nothing()  # another station's listeners hear nothing

    # A listener page that closes is dropped
    await elsewhere.disconnect()
    assert str(other_show.station_id) not in live_shows_ws.live_relay.listeners or \
        not live_shows_ws.live_relay.listeners[str(other_show.station_id)]

    # The host leaves: listeners are told the show is off air (the music comes back)
    await host.disconnect()
    assert (await listener.receive_json())["live"] is False
    await listener.disconnect()


@pytest.mark.asyncio
async def test_host_audio_socket_refuses_strangers(ws_app, db_session):
    station, show = await _show(db_session)
    admin = await _user(db_session)
    dj = await _user(db_session, role=UserRole.DJ)  # not this show's host

    ws = await _authed(ws_app, f"/api/v1/ws/live/{show.id}/audio", "not-a-token")
    assert await ws.expect_close() == 4001
    ws = WS(ws_app, f"/api/v1/ws/live/{show.id}/audio")
    await ws.connect()
    await ws.send_json({"type": "hello"})
    assert await ws.expect_close() == 4003
    ws = await _authed(ws_app, f"/api/v1/ws/live/{show.id}/audio", _token(dj))
    assert await ws.expect_close() == 4001
    ws = await _authed(ws_app, f"/api/v1/ws/live/{uuid.uuid4()}/audio", _token(admin))
    assert await ws.expect_close() == 4004  # no such show
    assert live_shows_ws.live_relay.on_air == {}


# ---------- B-LIVESHOWSW-2: show events for host console and screener ----------

@pytest.mark.asyncio
async def test_show_events_socket_sends_state_and_caller_updates(ws_app, client, db_session):
    station, show = await _show(db_session)
    admin = await _user(db_session)

    ws = await _authed(ws_app, f"/api/v1/ws/live/{show.id}/events", "bad")
    assert await ws.expect_close() == 4001

    events = await _authed(ws_app, f"/api/v1/ws/live/{show.id}/events", _token(admin))
    first = await events.receive_json()
    assert first["type"] == "show_state"
    assert first["data"]["show"]["id"] == str(show.id) and first["data"]["callers"] == []
    assert 3500 <= first["data"]["seconds_remaining"] <= 3600

    await events.send_text("hello")
    assert await events.receive_json() == {"type": "pong", "data": "hello"}

    r = await client.post(f"/api/v1/call-in/{station.id}", json={"name": "Moshe", "note": "Question"})
    assert r.status_code == 201, r.text
    queued = await events.receive_json()
    assert queued["type"] == "caller_queued"
    assert queued["data"]["call_id"] == r.json()["call_id"] and queued["data"]["caller_name"] == "Moshe"
    await events.disconnect()
    assert str(show.id) not in live_shows_ws.live_show_manager.active_connections


# ---------- B-LIVESHOWSW-5: a web caller ----------

@pytest.mark.asyncio
async def test_web_caller_waits_hearing_the_host_then_goes_on_air(ws_app, client, db_session):
    station, show = await _show(db_session)
    admin = await _user(db_session)
    headers = {"Authorization": f"Bearer {_token(admin)}"}

    r = await client.post(f"/api/v1/call-in/{station.id}", json={"name": "Moshe"})
    call_id, token = r.json()["call_id"], r.json()["token"]

    bad = await _authed(ws_app, f"/api/v1/ws/live/call/{call_id}", "0" * 64)
    assert await bad.expect_close() == 4001

    listener = WS(ws_app, f"/api/v1/ws/live/station/{station.id}/listen")
    await listener.connect()
    await listener.receive_json()
    host = await _authed(ws_app, f"/api/v1/ws/live/{show.id}/audio", _token(admin))
    await listener.receive_json()  # live

    caller = await _authed(ws_app, f"/api/v1/ws/live/call/{call_id}", token)
    assert await caller.receive_json() == {"type": "call_status", "status": "waiting", "sample_rate": 24000}

    # Waiting: hears the host; their microphone is not on air
    await caller.send_bytes(pcm(500))
    await asyncio.sleep(0.05)
    await host.send_bytes(pcm(1000))
    assert set(samples(await caller.receive_bytes())) == {1000}
    assert set(samples(await listener.receive_bytes())) == {1000}

    # The screener puts them on air: the caller is told and mixed in for listeners
    r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{call_id}/approve", headers=headers)
    assert r.status_code == 200, r.text
    assert (await caller.receive_json())["status"] == "approved"
    r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{call_id}/on-air", headers=headers)
    assert r.status_code == 200, r.text
    assert (await caller.receive_json())["status"] == "on_air"
    await caller.send_bytes(pcm(500, 4000))
    await asyncio.sleep(0.05)
    await host.send_bytes(pcm(1000))
    assert set(samples(await listener.receive_bytes())) == {1500}   # host + caller
    assert set(samples(await caller.receive_bytes())) == {1000}     # not themselves
    assert set(samples(await host.receive_bytes())) == {500}        # the host hears the caller

    # End call: told, socket closed, call completed
    r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{call_id}/end-call", headers=headers)
    assert r.status_code == 200, r.text
    assert (await caller.receive_json())["status"] == "completed"
    assert await caller.expect_close() == 1000
    await asyncio.sleep(0.05)
    call = await db_session.get(CallInRequest, uuid.UUID(call_id))
    await db_session.refresh(call)
    assert call.status == CallStatus.COMPLETED

    # A finished call cannot connect again
    again = await _authed(ws_app, f"/api/v1/ws/live/call/{call_id}", token)
    assert await again.expect_close() == 4004
    await host.disconnect()
    await listener.disconnect()


@pytest.mark.asyncio
async def test_rejected_web_caller_is_told_and_closed(ws_app, client, db_session):
    station, show = await _show(db_session)
    admin = await _user(db_session)
    r = await client.post(f"/api/v1/call-in/{station.id}", json={"name": "Moshe"})
    call_id, token = r.json()["call_id"], r.json()["token"]
    caller = await _authed(ws_app, f"/api/v1/ws/live/call/{call_id}", token)
    await caller.receive_json()
    r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{call_id}/reject",
                          headers={"Authorization": f"Bearer {_token(admin)}"})
    assert r.status_code == 200, r.text
    assert (await caller.receive_json())["status"] == "rejected"
    assert await caller.expect_close() == 1000


# ---------- V2-74: a web caller who drops has 15 s to come back ----------

def test_call_token_is_an_hmac_of_the_call_id_and_grace_is_15_seconds():
    call_id = str(uuid.uuid4())
    want = hmac.new(settings.JWT_SECRET_KEY.encode(), f"web-call:{call_id}".encode(), hashlib.sha256).hexdigest()
    assert call_in_service.call_token(call_id) == want
    assert call_in_service.call_token_ok(call_id, want)
    assert not call_in_service.call_token_ok(str(uuid.uuid4()), want)
    assert not call_in_service.call_token_ok("not-a-uuid", want)
    assert call_in_service.RECONNECT_GRACE_SECONDS == 15.0


@pytest.mark.asyncio
async def test_web_caller_drop_reconnect_and_abandon(ws_app, client, db_session, monkeypatch):
    monkeypatch.setattr(call_in_service, "RECONNECT_GRACE_SECONDS", 0.3)  # 15 s in production
    station, show = await _show(db_session)
    admin = await _user(db_session)
    headers = {"Authorization": f"Bearer {_token(admin)}"}
    events = await _authed(ws_app, f"/api/v1/ws/live/{show.id}/events", _token(admin))
    await events.receive_json()  # show_state

    async def join(name):
        r = await client.post(f"/api/v1/call-in/{station.id}", json={"name": name})
        await events.receive_json()  # caller_queued
        return r.json()["call_id"], r.json()["token"]

    async def status_of(call_id):
        call = await db_session.get(CallInRequest, uuid.UUID(call_id))
        await db_session.refresh(call)
        return call

    # Drops and comes back inside the grace: the call is held
    held_id, held_token = await join("Held")
    caller = await _authed(ws_app, f"/api/v1/ws/live/call/{held_id}", held_token)
    await caller.receive_json()
    await caller.disconnect()
    await asyncio.sleep(0.1)
    caller = await _authed(ws_app, f"/api/v1/ws/live/call/{held_id}", held_token)
    assert (await caller.receive_json())["status"] == "waiting"
    await asyncio.sleep(0.5)
    assert (await status_of(held_id)).status == CallStatus.WAITING
    assert await events.nothing(0.05)

    # A reconnect replaces the earlier socket, which is closed
    second = await _authed(ws_app, f"/api/v1/ws/live/call/{held_id}", held_token)
    await second.receive_json()
    assert await caller.expect_close() == 1000
    await asyncio.sleep(0.5)
    assert (await status_of(held_id)).status == CallStatus.WAITING

    # Gone for good while waiting: abandoned, the screener is told
    await second.disconnect()
    removed = await events.receive_json(timeout=3)
    assert removed == {"type": "caller_removed", "data": {"call_id": held_id, "reason": "hung_up"}}
    assert (await status_of(held_id)).status == CallStatus.ABANDONED

    # Gone for good while on air: completed, with air_end
    air_id, air_token = await join("On air")
    caller = await _authed(ws_app, f"/api/v1/ws/live/call/{air_id}", air_token)
    await caller.receive_json()
    for action in ("approve", "on-air"):
        r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{air_id}/{action}", headers=headers)
        assert r.status_code == 200, r.text
    await caller.disconnect()
    removed = None
    while removed is None or removed["type"] != "caller_removed":
        removed = await events.receive_json(timeout=3)
    assert removed["data"] == {"call_id": air_id, "reason": "hung_up"}
    call = await status_of(air_id)
    assert call.status == CallStatus.COMPLETED and call.air_end is not None
    await events.disconnect()


# ---------- V2-70: phone callers are not bridged to the show ----------

@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="BUG (V2-70): a phone caller put on air is only moved to the Twilio conference "
                   "live-show-<id>; nothing joins it to the host or the listeners (live_audio_mixer is a stub), so "
                   "nobody hears a phone caller. Shmuel chose web call-ins for now")
async def test_phone_caller_on_air_is_heard(ws_app, client, db_session):
    from sqlalchemy import select

    station, show = await _show(db_session)
    admin = await _user(db_session)
    headers = {"Authorization": f"Bearer {_token(admin)}"}
    r = await client.post("/api/v1/live-shows/twilio/inbound", data={"From": "+15551112222", "CallSid": "CAphone"})
    assert "Please hold" in r.text
    call = (await db_session.execute(select(CallInRequest))).scalar_one()
    for action in ("approve", "on-air"):
        r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{call.id}/{action}", headers=headers)
        assert r.status_code == 200, r.text
    # On air, the caller's voice should reach the show the way a web caller's does
    assert str(call.id) in live_shows_ws.live_relay.callers
