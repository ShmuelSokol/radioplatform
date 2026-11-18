"""Talkback: the screener or the host talks to a web caller off the air. Nothing of
it may reach listeners, and it ends the moment the caller goes on air."""
import json

import numpy as np
import pytest

from app.api.v1.live_shows_ws import CallerLine, StationLiveRelay


class FakeWS:
    def __init__(self):
        self.bytes: list[bytes] = []
        self.texts: list[str] = []
        self.closed: int | None = None

    async def send_bytes(self, b):
        self.bytes.append(b)

    async def send_text(self, t):
        self.texts.append(t)

    async def close(self, code=1000):
        self.closed = code


def pcm(value: int, n: int) -> bytes:
    return np.full(n, value, dtype="<i2").tobytes()


def samples(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype="<i2")


def _relay():
    relay = StationLiveRelay()
    listener, host, caller_ws, other_ws = FakeWS(), FakeWS(), FakeWS(), FakeWS()
    relay.listeners["st"] = {listener}
    relay.hosts["st"] = host
    caller = CallerLine(caller_ws, "c1", "show", "st")
    other = CallerLine(other_ws, "c2", "show", "st")
    relay.callers = {"c1": caller, "c2": other}
    return relay, listener, host, caller, other


@pytest.mark.asyncio
async def test_in_a_talkback_the_caller_stops_hearing_the_show_and_listeners_hear_nothing_of_it():
    relay, listener, host, caller, other = _relay()
    staff = FakeWS()
    assert await relay.talk_start("c1", staff) is True
    assert json.loads(caller.ws.texts[-1]) == {"type": "talk", "on": True}

    # The show goes on: listeners and the other caller get it, the caller in the talkback does not
    caller.add(pcm(500, 4000))     # their microphone is not on air, so it is not kept for the mix
    await relay.audio("st", pcm(1000, 2048))
    assert set(samples(listener.bytes[-1])) == {1000}
    assert set(samples(other.ws.bytes[-1])) == {1000}
    assert caller.ws.bytes == [] and staff.bytes == []

    await relay.talk_end("c1", staff)
    assert caller.talk is None and json.loads(caller.ws.texts[-1]) == {"type": "talk", "on": False}
    await relay.audio("st", pcm(1000, 2048))
    assert set(samples(caller.ws.bytes[-1])) == {1000}      # they hear the show again


@pytest.mark.asyncio
async def test_a_caller_on_air_cannot_be_taken_into_a_talkback():
    relay, _listener, _host, caller, _other = _relay()
    caller.on_air = True
    assert await relay.talk_start("c1", FakeWS()) is False
    assert await relay.talk_start("nobody", FakeWS()) is False
    assert caller.talk is None and caller.ws.texts == []


@pytest.mark.asyncio
async def test_going_on_air_ends_the_talkback_first():
    relay, listener, _host, caller, _other = _relay()
    staff = FakeWS()
    await relay.talk_start("c1", staff)
    await relay.caller_status("c1", "on_air")
    assert caller.talk is None and staff.closed == 4000
    kinds = [json.loads(t) for t in caller.ws.texts]
    assert kinds[-2:] == [{"type": "talk", "on": False}, {"type": "call_status", "status": "on_air"}]
    # and from here the caller is in the mix like any caller on air
    caller.add(pcm(200, 4000))
    await relay.audio("st", pcm(1000, 2048))
    assert set(samples(listener.bytes[-1])) == {1200}


@pytest.mark.asyncio
async def test_a_second_person_takes_the_talkback_over_and_the_first_cannot_end_it():
    relay, _l, _h, caller, _o = _relay()
    first, second = FakeWS(), FakeWS()
    await relay.talk_start("c1", first)
    await relay.talk_start("c1", second)
    assert first.closed == 4000 and caller.talk is second
    await relay.talk_end("c1", first)          # the first socket's clean-up
    assert caller.talk is second


def test_the_talkback_socket_refuses_without_a_sign_in():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from app.main import app

    with TestClient(app) as c:
        with pytest.raises(WebSocketDisconnect) as gone:
            with c.websocket_connect("/api/v1/ws/live/00000000-0000-4000-8000-000000000000/talk/x") as ws:
                ws.send_text(json.dumps({"type": "auth", "token": "nope"}))
                ws.receive_text()
        assert gone.value.code == 4001
