# ruff: noqa: F811  (pytest fixtures imported from another test module are used by name)
"""V1-149: a second host audio socket for the same show (second tab or device)."""
import pytest

from app.api.v1 import live_shows_ws
from app.models.live_show import LiveShowStatus
from tests.test_live_shows_websockets import (  # noqa: F401  (fixtures used by name)
    WS,
    _authed,
    _clean_relay,
    _show,
    _token,
    _user,
    pcm,
    ws_app,
)


async def _two_hosts(ws_app, db_session):
    station, show = await _show(db_session, status=LiveShowStatus.LIVE)
    admin = await _user(db_session)
    listener = WS(ws_app, f"/api/v1/ws/live/station/{station.id}/listen")
    await listener.connect()
    await listener.receive_json()
    first = await _authed(ws_app, f"/api/v1/ws/live/{show.id}/audio", _token(admin))
    assert (await listener.receive_json())["live"] is True
    await first.send_bytes(pcm(100))
    assert await listener.receive_bytes() == pcm(100)
    second = await _authed(ws_app, f"/api/v1/ws/live/{show.id}/audio", _token(admin))
    while not listener.outbox.empty():  # any state message the second login caused
        await listener.receive()
    return station, show, listener, first, second


@pytest.mark.asyncio
async def test_second_host_socket_takes_over_the_show(ws_app, db_session):
    """V1-149: the newest socket is the show's host socket and its mic is heard."""
    station, show, listener, first, second = await _two_hosts(ws_app, db_session)
    assert live_shows_ws.live_relay.hosts[str(station.id)] is not None
    assert live_shows_ws.audio_manager.active_connections[str(show.id)] is not None
    await second.send_bytes(pcm(200))
    assert await listener.receive_bytes() == pcm(200)
    for ws in (second, first, listener):
        await ws.disconnect()


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="found 2026-11-19: a second host audio socket does not replace the first: the first tab's mic frames still go to listeners and it is never told it was taken over")
async def test_first_host_socket_is_no_longer_heard_and_is_told(ws_app, db_session):
    """V1-149: after the second tab opens, the first tab's frames are not heard and the
    first socket is told (a message or a close)."""
    station, show, listener, first, second = await _two_hosts(ws_app, db_session)
    await first.send_bytes(pcm(300))
    assert await listener.nothing(0.2), "the first host's mic is still heard"
    msg = await first.receive(timeout=0.5)
    assert msg["type"] in ("websocket.send", "websocket.close")
    for ws in (second, first, listener):
        await ws.disconnect()
