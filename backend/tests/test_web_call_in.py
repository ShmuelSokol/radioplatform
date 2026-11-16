"""Web call-ins (Shmuel, 2026-11-16: "for now web call ins"): a listener calls the
live show from a page with their microphone. Their voice reaches listeners only
while the screener has them on air, mixed with the host's; the host hears the
callers and each caller hears everyone but themselves."""
import json
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.api.v1 import live_shows_ws
from app.api.v1.live_shows_ws import CALLER_PRIME_SAMPLES, CallerLine, StationLiveRelay
from app.models.call_in_request import CallInRequest, CallStatus
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.station import Station
from app.services.call_in import call_token, call_token_ok


class FakeWS:
    def __init__(self):
        self.bytes: list[bytes] = []
        self.texts: list[str] = []
        self.closed = False

    async def send_bytes(self, b):
        self.bytes.append(b)

    async def send_text(self, t):
        self.texts.append(t)

    async def close(self, code=1000):
        self.closed = True


def pcm(value: int, n: int) -> bytes:
    return np.full(n, value, dtype="<i2").tobytes()


def samples(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype="<i2")


@pytest.mark.asyncio
async def test_caller_on_air_is_mixed_with_the_host():
    relay = StationLiveRelay()
    listener, host, caller_ws, waiting_ws = FakeWS(), FakeWS(), FakeWS(), FakeWS()
    relay.listeners["st"] = {listener}
    relay.hosts["st"] = host
    caller = CallerLine(caller_ws, "c1", "show", "st")
    waiting = CallerLine(waiting_ws, "c2", "show", "st")
    relay.callers = {"c1": caller, "c2": waiting}

    # Waiting: nothing of theirs is heard, but they hear the host
    waiting.add(pcm(500, 4000))
    await relay.audio("st", pcm(1000, 2048))
    assert set(samples(listener.bytes[-1])) == {1000}
    assert set(samples(waiting_ws.bytes[-1])) == {1000}

    # On air: listeners hear host + caller; the host hears the caller; the caller hears the host
    await relay.caller_status("c1", "on_air")
    assert json.loads(caller_ws.texts[-1]) == {"type": "call_status", "status": "on_air"}
    caller.add(pcm(300, CALLER_PRIME_SAMPLES + 100))
    await relay.audio("st", pcm(1000, 2048))
    assert set(samples(listener.bytes[-1])) == {1300}
    assert set(samples(host.bytes[-1])) == {300}
    assert set(samples(caller_ws.bytes[-1])) == {1000}
    assert set(samples(waiting_ws.bytes[-1])) == {1300}

    # Loud voices are clipped, not wrapped round
    caller.add(pcm(30000, 4000))
    caller.buf = caller.buf[-4000:]
    await relay.audio("st", pcm(10000, 2048))
    assert samples(listener.bytes[-1]).max() == 32767

    # Taken off air: gone from the mix at once, and their page is told
    await relay.caller_status("c1", "completed")
    await relay.audio("st", pcm(1000, 2048))
    assert set(samples(listener.bytes[-1])) == {1000}
    assert caller_ws.closed


@pytest.mark.asyncio
async def test_no_callers_passes_the_host_frame_unchanged():
    relay = StationLiveRelay()
    listener = FakeWS()
    relay.listeners["st"] = {listener}
    frame = pcm(1234, 100)
    await relay.audio("st", frame)
    assert listener.bytes == [frame]


def test_call_token_is_tied_to_the_call():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    assert call_token_ok(a, call_token(a))
    assert not call_token_ok(b, call_token(a))
    assert not call_token_ok(a, "")
    assert not call_token_ok("not-a-uuid", call_token(a))


async def _live_show(db, calls_enabled=True):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    db.add(station)
    await db.flush()
    now = datetime.now(UTC)
    show = LiveShow(id=uuid.uuid4(), station_id=station.id, title="Live", status=LiveShowStatus.LIVE,
                    scheduled_start=now, scheduled_end=now + timedelta(hours=1), calls_enabled=calls_enabled)
    db.add(show)
    await db.commit()
    return station, show


@pytest.mark.asyncio
async def test_listener_calls_in_and_screener_puts_them_on_air(client, db_session, admin_token, monkeypatch):
    station, show = await _live_show(db_session)
    r = await client.post(f"/api/v1/call-in/{station.id}", json={"name": "Moshe", "note": "About the shiur"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["show_id"] == str(show.id) and call_token_ok(body["call_id"], body["token"])
    call = await db_session.get(CallInRequest, uuid.UUID(body["call_id"]))
    assert call.status == CallStatus.WAITING and call.caller_name == "Moshe"

    # The screener's on-air button reaches the caller's line
    told = []

    async def caller_status(call_id, status):
        told.append((call_id, status))

    monkeypatch.setattr(live_shows_ws.live_relay, "caller_status", caller_status)
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{body['call_id']}/on-air", headers=headers)
    assert r.status_code == 200, r.text
    assert told == [(body["call_id"], "on_air")]


@pytest.mark.asyncio
async def test_no_call_without_a_live_show_taking_calls(client, db_session):
    station, show = await _live_show(db_session, calls_enabled=False)
    r = await client.post(f"/api/v1/call-in/{station.id}", json={"name": "Moshe"})
    assert r.status_code == 409
    r = await client.post(f"/api/v1/call-in/{uuid.uuid4()}", json={"name": "Moshe"})
    assert r.status_code == 409
