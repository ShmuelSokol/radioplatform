"""Checklist rows on live-show backend logic: V1-150, V3-53.

No wall clock is read for any decision here: the caller buffer is fed sample counts,
and the show tests look only at statuses and the presence of times.
"""
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.api.v1 import live_shows_ws
from app.api.v1.live_shows_ws import (
    CALLER_MAX_SAMPLES,
    CALLER_PRIME_SAMPLES,
    LIVE_SAMPLE_RATE,
    CallerLine,
)
from app.models.call_in_request import CallInRequest, CallStatus
from app.models.live_show import LiveShow, LiveShowStatus
from tests.test_live_delay import _admin_headers, _station
from tests.test_live_talkback import FakeWS


def _frame(level: int, n: int) -> bytes:
    return np.full(n, level, dtype="<i2").tobytes()


# ── V1-150: caller audio latency bounded ─────────────────────────────────────


def test_caller_is_mixed_in_only_after_0_12_s_buffered():
    """V1-150: nothing of a caller is mixed in until 0.12 s of their audio is waiting;
    after running dry it waits for 0.12 s again."""
    assert CALLER_PRIME_SAMPLES == int(LIVE_SAMPLE_RATE * 0.12) == 2880
    line = CallerLine(FakeWS(), "c1", "show", "st")
    line.on_air = True
    line.add(_frame(500, CALLER_PRIME_SAMPLES - 1))
    assert not line.take(480).any(), "mixed in before 0.12 s was buffered"
    line.add(_frame(500, 1))
    out = line.take(480)
    assert (out == 500).all()
    # drain it all: then it waits for a fresh 0.12 s
    line.take(CALLER_PRIME_SAMPLES)
    line.add(_frame(700, 100))
    assert not line.take(100).any()


def test_more_than_0_6_s_queued_drops_the_oldest_audio():
    """V1-150: a bad connection that delivers a burst never builds up more than 0.6 s:
    the oldest samples are dropped, the newest kept, so the delay does not grow."""
    assert CALLER_MAX_SAMPLES == int(LIVE_SAMPLE_RATE * 0.6) == 14400
    line = CallerLine(FakeWS(), "c1", "show", "st")
    line.on_air = True
    old, new = _frame(100, CALLER_MAX_SAMPLES), _frame(900, LIVE_SAMPLE_RATE // 2)  # 0.6 s then 0.5 s
    line.add(old)
    line.add(new)
    assert len(line.buf) == CALLER_MAX_SAMPLES
    # what is left is the newest: 0.1 s of the old, then all of the new
    assert (line.buf[: CALLER_MAX_SAMPLES - LIVE_SAMPLE_RATE // 2] == 100).all()
    assert (line.buf[-(LIVE_SAMPLE_RATE // 2):] == 900).all()
    # a minute of bursts still stays at 0.6 s
    for _ in range(120):
        line.add(_frame(5, LIVE_SAMPLE_RATE // 2))
    assert len(line.buf) == CALLER_MAX_SAMPLES


def test_a_caller_not_on_air_buffers_nothing():
    line = CallerLine(FakeWS(), "c1", "show", "st")
    line.add(_frame(500, CALLER_MAX_SAMPLES))
    assert len(line.buf) == 0 and not line.take(480).any()


# ── V3-53: a caller on air when the show ends; two shows on one station ──────


@pytest.fixture
def _clean_relay():
    relay = live_shows_ws.live_relay
    saved = dict(relay.callers)
    relay.callers.clear()
    relay.airs.clear()
    yield relay
    relay.callers.clear()
    relay.callers.update(saved)
    relay.airs.clear()


@pytest.mark.asyncio
async def test_caller_on_air_when_the_show_ends_is_ended_and_told(client, db_session, _clean_relay):
    """V3-53: ending a show with a web caller on air ends that call (completed), tells the
    caller's page, and the waiting ones too; the show is ended."""
    station, show, *_ = await _station(db_session, {"live_delay_seconds": 0})
    show.status = LiveShowStatus.LIVE
    now = datetime.now(UTC)
    on_air = CallInRequest(id=uuid.uuid4(), live_show_id=show.id, caller_phone="Web caller", caller_name="Avi",
                           status=CallStatus.ON_AIR, created_at=now - timedelta(seconds=60), air_start=now)
    waiting = CallInRequest(id=uuid.uuid4(), live_show_id=show.id, caller_phone="Web caller", caller_name="Berel",
                            status=CallStatus.WAITING, created_at=now - timedelta(seconds=30))
    db_session.add_all([on_air, waiting])
    await db_session.commit()
    lines = {}
    for call in (on_air, waiting):
        line = CallerLine(FakeWS(), str(call.id), str(show.id), str(station.id))
        line.on_air = call.status == CallStatus.ON_AIR
        _clean_relay.callers[str(call.id)] = line
        lines[call.id] = line
    headers = await _admin_headers(db_session)
    r = await client.post(f"/api/v1/live-shows/{show.id}/end", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "ended"
    for call in (on_air, waiting):
        await db_session.refresh(call)
        assert call.status == CallStatus.COMPLETED
        assert any("completed" in t for t in lines[call.id].ws.texts), "the caller's page was not told"


@pytest.mark.asyncio
async def test_caller_on_air_when_the_show_ends_keeps_their_air_time(client, db_session, _clean_relay):
    """V3-53: the call history keeps when the caller left the air (found 2026-11-19:
    end_all_calls_for_show marked the call completed first, so air_end was never set)."""
    station, show, *_ = await _station(db_session, {"live_delay_seconds": 0})
    show.status = LiveShowStatus.LIVE
    now = datetime.now(UTC)
    call = CallInRequest(id=uuid.uuid4(), live_show_id=show.id, caller_phone="Web caller", caller_name="Avi",
                         status=CallStatus.ON_AIR, created_at=now - timedelta(seconds=60), air_start=now)
    db_session.add(call)
    await db_session.commit()
    line = CallerLine(FakeWS(), str(call.id), str(show.id), str(station.id))
    line.on_air = True
    _clean_relay.callers[str(call.id)] = line
    headers = await _admin_headers(db_session)
    assert (await client.post(f"/api/v1/live-shows/{show.id}/end", headers=headers)).status_code == 200
    await db_session.refresh(call)
    assert call.air_end is not None


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="found 2026-11-19: a second show can be started on a station while "
                   "another show is live there; nothing refuses it")
async def test_second_show_on_a_station_with_a_live_show_is_refused(client, db_session, _clean_relay):
    """V3-53: two people start a show on one station: the second start is refused while
    the first is live, and the first stays on air."""
    station, first, *_ = await _station(db_session, {"live_delay_seconds": 0})
    now = datetime.now(UTC)
    second = LiveShow(id=uuid.uuid4(), station_id=station.id, title="Another", status=LiveShowStatus.SCHEDULED,
                      scheduled_start=now, scheduled_end=now + timedelta(hours=1))
    db_session.add(second)
    await db_session.commit()
    headers = await _admin_headers(db_session)
    assert (await client.post(f"/api/v1/live-shows/{first.id}/start", headers=headers)).status_code == 200
    r = await client.post(f"/api/v1/live-shows/{second.id}/start", headers=headers)
    assert r.status_code == 409
    await db_session.refresh(second)
    assert second.status != LiveShowStatus.LIVE


@pytest.mark.asyncio
async def test_same_show_started_twice_stays_one_show(client, db_session, _clean_relay):
    """V3-53: two people pressing Start on the same show: both answers are the one show,
    live, and the station is not taken twice."""
    station, show, *_ = await _station(db_session, {"live_delay_seconds": 0})
    headers = await _admin_headers(db_session)
    url = f"/api/v1/live-shows/{show.id}/start"
    a = await client.post(url, headers=headers)
    b = await client.post(url, headers=headers)
    assert a.status_code == b.status_code == 200
    assert a.json()["id"] == b.json()["id"] == str(show.id)
    await db_session.refresh(show)
    assert show.status == LiveShowStatus.LIVE
