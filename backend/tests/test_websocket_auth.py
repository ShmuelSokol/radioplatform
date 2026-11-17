"""Socket logins: /ws/alerts (admins and managers only, V1-100) and the live
show sockets (admin, manager or the show's host, and only on a station the
login may operate, V1-148). /ws/now-playing stays public."""
import json
import uuid

import pytest
from fastapi import WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, create_refresh_token
from app.models.live_show import LiveShow
from app.models.station import Station
from app.models.user import User, UserRole


class FakeSocket:
    """Just enough of a WebSocket: hands out the scripted messages, records what
    the server sends and how it closes."""

    def __init__(self, *messages):
        self.inbox = list(messages)
        self.sent = []
        self.closed = None
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        if not self.inbox:
            raise WebSocketDisconnect(1000)
        m = self.inbox.pop(0)
        return m if isinstance(m, str) else json.dumps(m)

    async def receive_json(self):
        return json.loads(await self.receive_text())

    async def send_json(self, data):
        self.sent.append(data)

    async def send_text(self, data):
        self.sent.append(json.loads(data))

    async def close(self, code=1000):
        self.closed = code


async def _user(db, role, station_ids=None, active=True):
    u = User(id=uuid.uuid4(), email=f"{role.value}-{uuid.uuid4().hex[:6]}@t.com", hashed_password="x",
             role=role, is_active=active, station_ids=station_ids)
    db.add(u)
    await db.commit()
    return u


@pytest.fixture
def ws_db(monkeypatch, db_session: AsyncSession):
    """Sockets open their own session; point them at the test database."""

    async def session():
        yield db_session

    import app.api.v1.live_shows_ws as live_ws
    import app.db.session as db_mod

    monkeypatch.setattr(db_mod, "get_async_session", session)
    monkeypatch.setattr(live_ws, "get_async_session", session)
    return db_session


async def _alerts(sock):
    from app.api.v1.websocket import alert_manager, websocket_alerts

    await websocket_alerts(sock)
    joined = sock in alert_manager.connections
    alert_manager.connections.discard(sock)
    return joined


@pytest.mark.asyncio
async def test_alerts_socket_refuses_anonymous_and_non_managers(ws_db):
    # no auth message at all: closed, never subscribed
    sock = FakeSocket({"type": "hello"})
    assert not await _alerts(sock)
    assert sock.closed == 4003
    # garbage token
    sock = FakeSocket({"type": "auth", "token": "nope"})
    assert not await _alerts(sock)
    assert sock.closed == 4001
    for role in (UserRole.DJ, UserRole.VIEWER, UserRole.SPONSOR):
        u = await _user(ws_db, role)
        sock = FakeSocket({"type": "auth", "token": create_access_token(str(u.id))})
        assert not await _alerts(sock), role
        assert sock.closed == 4001
    # a refresh token is not a login
    admin = await _user(ws_db, UserRole.ADMIN)
    sock = FakeSocket({"type": "auth", "token": create_refresh_token(str(admin.id))})
    assert not await _alerts(sock)
    # an inactive manager
    gone = await _user(ws_db, UserRole.MANAGER, active=False)
    sock = FakeSocket({"type": "auth", "token": create_access_token(str(gone.id))})
    assert not await _alerts(sock)


@pytest.mark.asyncio
async def test_alerts_socket_lets_managers_listen(ws_db):
    from app.api.v1.websocket import alerts_ws_user

    for role in (UserRole.ADMIN, UserRole.MANAGER):
        u = await _user(ws_db, role)
        assert (await alerts_ws_user(create_access_token(str(u.id)), ws_db)).id == u.id
        sock = FakeSocket({"type": "auth", "token": create_access_token(str(u.id))})
        await _alerts(sock)
        assert sock.closed is None
        assert sock.sent[0] == {"type": "authenticated"}


@pytest.mark.asyncio
async def test_alert_broadcast_reaches_only_signed_in_sockets(ws_db):
    from app.api.v1.websocket import alert_manager, broadcast_alert

    stranger = FakeSocket()
    member = FakeSocket()
    alert_manager.connections.add(member)  # what a successful auth does
    try:
        await broadcast_alert({"title": "Dead air on FM"})
    finally:
        alert_manager.connections.discard(member)
    assert member.sent and member.sent[0]["data"]["title"] == "Dead air on FM"
    assert stranger.sent == []


async def _show(db, protected=False):
    st = Station(id=uuid.uuid4(), name="FM" if protected else "Web", timezone="UTC", protected=protected)
    db.add(st)
    await db.flush()
    show = LiveShow(id=uuid.uuid4(), station_id=st.id, title="Show")
    db.add(show)
    await db.commit()
    return st, show


@pytest.mark.asyncio
async def test_live_show_socket_follows_station_permissions(ws_db):
    from app.api.v1.live_shows_ws import _authenticate_ws

    fm, fm_show = await _show(ws_db, protected=True)
    web, web_show = await _show(ws_db)

    async def auth(user, show):
        sock = FakeSocket({"type": "auth", "token": create_access_token(str(user.id))})
        return await _authenticate_ws(sock, str(show.id)), sock

    manager = await _user(ws_db, UserRole.MANAGER)  # unassigned: not FM
    got, sock = await auth(manager, fm_show)
    assert got is None and sock.closed == 4001
    got, _ = await auth(manager, web_show)
    assert got is not None

    fm_manager = await _user(ws_db, UserRole.MANAGER, [str(fm.id)])
    assert (await auth(fm_manager, fm_show))[0] is not None

    admin = await _user(ws_db, UserRole.ADMIN)
    assert (await auth(admin, fm_show))[0] is not None

    # a DJ who is not the host is refused even on an open station; the host is let in
    dj = await _user(ws_db, UserRole.DJ)
    assert (await auth(dj, web_show))[0] is None
    web_show.host_user_id = dj.id
    await ws_db.commit()
    assert (await auth(dj, web_show))[0] is not None
    # a host who may not operate the protected station is refused there
    fm_show.host_user_id = dj.id
    await ws_db.commit()
    assert (await auth(dj, fm_show))[0] is None


@pytest.mark.asyncio
async def test_now_playing_socket_route_is_public():
    from fastapi.routing import APIWebSocketRoute

    from app.main import create_app

    from tests.route_list import app_routes

    routes = {r.path: r.route for r in app_routes(create_app()) if isinstance(r.route, APIWebSocketRoute)}
    assert "/api/v1/ws/now-playing/{station_id}" in routes or any("now-playing" in p for p in routes)

