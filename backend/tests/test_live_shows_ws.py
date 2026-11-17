"""Live show sockets end to end through Starlette's TestClient (B-LIVESHOWSW-1, 2, 5)
and a web caller dropping off (V2-74).

The sockets read the database through get_async_session, not the get_db override,
so each test points that at the test database. TestClient runs the app in its own
thread and event loop, hence a NullPool engine (no connection shared across loops)."""
import asyncio
import json
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.db.session as session_mod
from app.api.v1 import live_shows_ws
from app.core.security import create_access_token, create_refresh_token
from app.models.call_in_request import CallInRequest, CallStatus
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.station import Station
from app.models.user import User, UserRole
from app.services import call_in
from app.services.call_in import call_token
from tests.conftest import TEST_DATABASE_URL, TestSessionLocal

pytestmark = pytest.mark.asyncio


@pytest.fixture
def ws_db(monkeypatch, db_session):
    """Sockets get sessions on the test database, safe from TestClient's own loop."""
    eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    async def sessions():
        async with factory() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    monkeypatch.setattr(session_mod, "get_async_session", sessions)
    monkeypatch.setattr(live_shows_ws, "get_async_session", sessions)
    yield
    # NullPool keeps nothing open; the engine needs no disposal on this loop


@pytest.fixture(autouse=True)
def _clean_relay():
    def clear():
        live_shows_ws.live_relay.listeners.clear()
        live_shows_ws.live_relay.on_air.clear()
        live_shows_ws.live_relay.hosts.clear()
        live_shows_ws.live_relay.callers.clear()
        live_shows_ws.live_show_manager.active_connections.clear()
        live_shows_ws.audio_manager.active_connections.clear()

    clear()
    yield
    clear()


@contextmanager
def ws_client():
    app = FastAPI()
    app.include_router(live_shows_ws.router, prefix="/api/v1")
    with TestClient(app) as tc:
        yield tc


async def _show(db, status=LiveShowStatus.LIVE, host=None):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    db.add(station)
    await db.flush()
    now = datetime.now(UTC)
    show = LiveShow(id=uuid.uuid4(), station_id=station.id, title="Live", status=status,
                    host_user_id=host.id if host else None,
                    scheduled_start=now, scheduled_end=now + timedelta(hours=1), calls_enabled=True)
    db.add(show)
    await db.commit()
    return station, show


async def _user(db, role=UserRole.ADMIN, active=True):
    u = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@t.com", hashed_password="x", role=role, is_active=active)
    db.add(u)
    await db.commit()
    return u


async def _call(db, show, status=CallStatus.WAITING):
    c = CallInRequest(id=uuid.uuid4(), live_show_id=show.id, caller_phone=call_in.WEB_CALLER_PHONE,
                      caller_name="Moshe", status=status)
    db.add(c)
    await db.commit()
    return c


def _auth(token):
    return json.dumps({"type": "auth", "token": token})


def _closed_with(ws) -> int:
    with pytest.raises(WebSocketDisconnect) as e:
        ws.receive_text()
    return e.value.code


def _wait_until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# ── B-LIVESHOWSW-1: /ws/live/station/{id}/listen ──

async def test_listener_hears_the_host_while_the_show_is_on_air(db_session, ws_db):
    admin = await _user(db_session)
    station, show = await _show(db_session)
    token = create_access_token(str(admin.id))
    frame = np.arange(480, dtype="<i2").tobytes()
    with ws_client() as tc:
        with tc.websocket_connect(f"/api/v1/ws/live/station/{station.id}/listen") as listener:
            assert json.loads(listener.receive_text()) == {
                "type": "live_state", "live": False, "show_id": None, "sample_rate": 24000}
            with tc.websocket_connect(f"/api/v1/ws/live/{show.id}/audio") as host:
                host.send_text(_auth(token))
                assert json.loads(listener.receive_text()) == {
                    "type": "live_state", "live": True, "show_id": str(show.id), "sample_rate": 24000}
                host.send_bytes(frame)
                assert listener.receive_bytes() == frame  # no callers: the host frame, unchanged
            # The host stopped: listeners are told at once, so the music comes back
            assert json.loads(listener.receive_text())["live"] is False
        assert _wait_until(lambda: not live_shows_ws.live_relay.listeners.get(str(station.id)))


async def test_listener_joining_mid_show_is_told_it_is_live(db_session, ws_db):
    station_id = str(uuid.uuid4())
    show_id = str(uuid.uuid4())
    live_shows_ws.live_relay.on_air[station_id] = show_id
    with ws_client() as tc:
        with tc.websocket_connect(f"/api/v1/ws/live/station/{station_id}/listen") as listener:
            msg = json.loads(listener.receive_text())
            assert msg["live"] is True and msg["show_id"] == show_id
            # Another station's listeners hear nothing of it
            with tc.websocket_connect(f"/api/v1/ws/live/station/{uuid.uuid4()}/listen") as other:
                assert json.loads(other.receive_text())["live"] is False


# ── B-LIVESHOWSW-2: /ws/live/{show_id}/events ──

async def test_events_socket_sends_state_pong_and_broadcasts(db_session, ws_db, monkeypatch):
    admin = await _user(db_session)
    _, show = await _show(db_session)
    call = await _call(db_session, show)
    real_wait_for = asyncio.wait_for

    async def quick(aw, timeout=None):
        return await real_wait_for(aw, 0.3 if timeout == 10.0 else timeout)  # the 10 s tick, sped up

    monkeypatch.setattr(live_shows_ws.asyncio, "wait_for", quick)
    with ws_client() as tc:
        with tc.websocket_connect(f"/api/v1/ws/live/{show.id}/events") as ws:
            ws.send_text(_auth(create_access_token(str(admin.id))))
            state = ws.receive_json()
            assert state["type"] == "show_state"
            assert state["data"]["show"]["id"] == str(show.id)
            assert [c["id"] for c in state["data"]["callers"]] == [str(call.id)]
            assert 3500 < state["data"]["seconds_remaining"] <= 3600
            ws.send_text("hello")
            assert ws.receive_json() == {"type": "pong", "data": "hello"}
            ws.send_text("pong")  # the client's own keep-alive answer gets no reply
            tick = ws.receive_json()
            assert tick["type"] == "time_remaining" and tick["data"]["seconds"] > 3500
            tc.portal.call(live_shows_ws.broadcast_show_event, str(show.id), "caller_added", {"call_id": "c1"})
            got = ws.receive_json()
            while got["type"] == "time_remaining":
                got = ws.receive_json()
            assert got == {"type": "caller_added", "data": {"call_id": "c1"}}
        assert _wait_until(lambda: str(show.id) not in live_shows_ws.live_show_manager.active_connections)


async def test_events_socket_refuses_strangers(db_session, ws_db):
    host = await _user(db_session, UserRole.DJ)
    other_dj = await _user(db_session, UserRole.DJ)
    inactive = await _user(db_session, active=False)
    _, show = await _show(db_session, host=host)
    url = f"/api/v1/ws/live/{show.id}/events"
    with ws_client() as tc:
        for first, code in [
            (json.dumps({"type": "hello"}), 4003),                         # not an auth message
            ("not json", 4001),
            (_auth("garbage"), 4001),                                      # bad token
            (_auth(create_refresh_token(str(host.id))), 4001),            # refresh token, not access
            (_auth(create_access_token(str(other_dj.id))), 4001),         # a DJ who is not the host
            (_auth(create_access_token(str(inactive.id))), 4001),         # switched-off login
        ]:
            with tc.websocket_connect(url) as ws:
                ws.send_text(first)
                assert _closed_with(ws) == code, first
        # The show's own host is let in
        with tc.websocket_connect(url) as ws:
            ws.send_text(_auth(create_access_token(str(host.id))))
            assert ws.receive_json()["type"] == "show_state"


# ── B-LIVESHOWSW-5: /ws/live/call/{call_id} ──

async def test_web_caller_auth_flow(db_session, ws_db, monkeypatch):
    monkeypatch.setattr(call_in, "RECONNECT_GRACE_SECONDS", 30.0)  # nobody is ended during this test
    _, show = await _show(db_session)
    call = await _call(db_session, show)
    done = await _call(db_session, show, CallStatus.COMPLETED)
    url = f"/api/v1/ws/live/call/{call.id}"
    with ws_client() as tc:
        # Wrong token, another call's token, no auth message: refused
        for first in (_auth("nope"), _auth(call_token(done.id)), json.dumps({"type": "hi", "token": call_token(call.id)})):
            with tc.websocket_connect(url) as ws:
                ws.send_text(first)
                assert _closed_with(ws) == 4001
        # A finished call or one that does not exist: 4004
        with tc.websocket_connect(f"/api/v1/ws/live/call/{done.id}") as ws:
            ws.send_text(_auth(call_token(done.id)))
            assert _closed_with(ws) == 4004
        missing = uuid.uuid4()
        with tc.websocket_connect(f"/api/v1/ws/live/call/{missing}") as ws:
            ws.send_text(_auth(call_token(missing)))
            assert _closed_with(ws) == 4004
        # The right token: told its status, and its microphone joins the relay
        with tc.websocket_connect(url) as ws:
            ws.send_text(_auth(call_token(call.id)))
            assert json.loads(ws.receive_text()) == {"type": "call_status", "status": "waiting", "sample_rate": 24000}
            assert _wait_until(lambda: str(call.id) in live_shows_ws.live_relay.callers)
            line = live_shows_ws.live_relay.callers[str(call.id)]
            assert line.show_id == str(show.id) and line.on_air is False
            # A reconnect (reload) takes over the line and closes the old socket
            with tc.websocket_connect(url) as ws2:
                ws2.send_text(_auth(call_token(call.id)))
                assert json.loads(ws2.receive_text())["status"] == "waiting"
                assert _closed_with(ws) == 1000
                assert _wait_until(lambda: live_shows_ws.live_relay.callers.get(str(call.id)) is not line)


# ── V2-74: the caller's page drops ──

async def test_dropped_caller_is_ended_after_the_grace_period(db_session, ws_db, monkeypatch):
    monkeypatch.setattr(call_in, "RECONNECT_GRACE_SECONDS", 0.2)
    removed = []

    async def capture(show_id, event_type, data):
        removed.append((show_id, event_type, data))

    monkeypatch.setattr(live_shows_ws, "broadcast_show_event", capture)
    _, show = await _show(db_session)
    waiting = await _call(db_session, show)
    on_air = await _call(db_session, show, CallStatus.ON_AIR)
    with ws_client() as tc:
        for c in (waiting, on_air):
            with tc.websocket_connect(f"/api/v1/ws/live/call/{c.id}") as ws:
                ws.send_text(_auth(call_token(c.id)))
                ws.receive_text()
        assert _wait_until(lambda: len(removed) == 2)

    async with TestSessionLocal() as s:
        rows = {c.id: c for c in (await s.execute(select(CallInRequest))).scalars()}
    assert rows[waiting.id].status == CallStatus.ABANDONED and rows[waiting.id].air_end is None
    assert rows[on_air.id].status == CallStatus.COMPLETED and rows[on_air.id].air_end is not None
    assert sorted(d["call_id"] for _, _, d in removed) == sorted([str(waiting.id), str(on_air.id)])
    assert all(e == "caller_removed" and d["reason"] == "hung_up" and sid == str(show.id) for sid, e, d in removed)


async def test_caller_who_comes_back_in_time_keeps_the_call(db_session, ws_db, monkeypatch):
    removed = []

    async def capture(show_id, event_type, data):
        removed.append(data)

    monkeypatch.setattr(live_shows_ws, "broadcast_show_event", capture)
    _, show = await _show(db_session)
    call = await _call(db_session, show, CallStatus.APPROVED)
    live_shows_ws.live_relay.callers[str(call.id)] = object()  # back on a new socket
    await call_in._end_if_gone(str(call.id), str(show.id), 0)
    async with TestSessionLocal() as s:
        assert (await s.get(CallInRequest, call.id)).status == CallStatus.APPROVED
    assert removed == []

    # A call the screener already ended is left as it is
    live_shows_ws.live_relay.callers.clear()
    rejected = await _call(db_session, show, CallStatus.REJECTED)
    await call_in._end_if_gone(str(rejected.id), str(show.id), 0)
    async with TestSessionLocal() as s:
        assert (await s.get(CallInRequest, rejected.id)).status == CallStatus.REJECTED
    assert removed == []


async def test_default_grace_is_15_seconds_and_token_is_an_hmac(monkeypatch):
    import hashlib
    import hmac

    from app.config import settings

    assert call_in.RECONNECT_GRACE_SECONDS == 15.0
    cid = str(uuid.uuid4())
    expected = hmac.new(settings.JWT_SECRET_KEY.encode(), f"web-call:{cid}".encode(), hashlib.sha256).hexdigest()
    assert call_token(cid) == expected
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", settings.JWT_SECRET_KEY + "-other")
    assert call_token(cid) != expected  # a token is only good with this server's secret
    assert not call_in.call_token_ok(cid, expected)
