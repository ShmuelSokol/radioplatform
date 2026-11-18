"""The caller desk of a live show: Hold, Next caller, and the caller's note.
See wiki/live-shows.md, "Caller desk"."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.api.v1 import live_shows_ws
from app.models.call_in_request import CallInRequest, CallStatus
from app.models.live_show import LiveShowStatus
from app.services.live_air import RATE, LiveAir
from tests.test_live_delay import Clock, _admin_headers, _nowhere, _station, run, talker


@pytest.fixture(autouse=True)
def _clean():
    live_shows_ws.live_relay.airs.clear()
    yield
    live_shows_ws.live_relay.airs.clear()


async def _show_with_callers(db, names, config=None):
    station, show, *_ = await _station(db, config or {"live_delay_seconds": 0})
    show.status = LiveShowStatus.LIVE
    now = datetime.now(UTC)
    calls = []
    for i, (name, status) in enumerate(names):
        call = CallInRequest(id=uuid.uuid4(), live_show_id=show.id, caller_phone="Web caller", caller_name=name,
                             status=status, created_at=now - timedelta(seconds=100 - i),
                             air_start=now if status == CallStatus.ON_AIR else None)
        db.add(call)
        calls.append(call)
    await db.commit()
    return station, show, calls


async def _statuses(db, calls):
    out = []
    for c in calls:
        await db.refresh(c)
        out.append(c.status.value)
    return out


@pytest.mark.asyncio
async def test_hold_takes_the_caller_off_air_but_keeps_the_call(client, db_session):
    _s, show, (a, b) = await _show_with_callers(db_session, [("Avi", CallStatus.ON_AIR), ("Berel", CallStatus.APPROVED)])
    headers = await _admin_headers(db_session)
    r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{a.id}/hold", headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert await _statuses(db_session, [a, b]) == ["approved", "approved"]
    # Not on air: nothing to hold
    r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{b.id}/hold", headers=headers)
    assert r.status_code == 409
    # The held caller can go on air again
    r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{a.id}/on-air", headers=headers)
    assert r.status_code == 200 and (await _statuses(db_session, [a]))[0] == "on_air"
    assert (await client.post(f"/api/v1/live-shows/{show.id}/calls/{uuid.uuid4()}/hold", headers=headers)).status_code == 404
    assert (await client.post(f"/api/v1/live-shows/{show.id}/calls/{a.id}/hold")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_next_ends_the_call_on_air_and_takes_who_waited_longest(client, db_session):
    _s, show, (a, b, c, d) = await _show_with_callers(db_session, [
        ("Avi", CallStatus.ON_AIR), ("Berel", CallStatus.APPROVED), ("Chaim", CallStatus.APPROVED),
        ("Dovid", CallStatus.WAITING)])
    headers = await _admin_headers(db_session)
    url = f"/api/v1/live-shows/{show.id}/calls/next"
    r = await client.post(url, headers=headers)
    assert r.status_code == 200 and r.json() == {"ended": str(a.id), "on_air": str(b.id)}
    assert await _statuses(db_session, [a, b, c, d]) == ["completed", "on_air", "approved", "waiting"]
    await db_session.refresh(a)
    assert a.air_end is not None
    r = await client.post(url, headers=headers)
    assert r.json() == {"ended": str(b.id), "on_air": str(c.id)}
    # Nobody approved is left: Next only ends the call (a waiting caller is never taken unscreened)
    r = await client.post(url, headers=headers)
    assert r.json() == {"ended": str(c.id), "on_air": None}
    assert await _statuses(db_session, [c, d]) == ["completed", "waiting"]
    r = await client.post(url, headers=headers)
    assert r.status_code == 409 and "none is approved" in r.json()["detail"]
    assert (await client.post(url)).status_code in (401, 403)


@pytest.mark.asyncio
async def test_next_with_nobody_on_air_puts_the_first_approved_on(client, db_session):
    _s, show, (a, b) = await _show_with_callers(db_session, [("Avi", CallStatus.APPROVED), ("Berel", CallStatus.APPROVED)])
    headers = await _admin_headers(db_session)
    r = await client.post(f"/api/v1/live-shows/{show.id}/calls/next", headers=headers)
    assert r.json() == {"ended": None, "on_air": str(a.id)}


@pytest.mark.asyncio
async def test_next_is_refused_while_the_delay_is_not_safe_and_ends_nobody(client, db_session):
    station, show, (a, b) = await _show_with_callers(
        db_session, [("Avi", CallStatus.ON_AIR), ("Berel", CallStatus.APPROVED)], {"live_delay_seconds": 10})
    headers = await _admin_headers(db_session)
    clock = Clock()
    air = LiveAir(str(station.id), str(show.id), _nowhere, delay=10, clock=clock)
    live_shows_ws.live_relay.airs[str(station.id)] = air
    run(air, clock, 20.0, talker())
    air.dump()
    url = f"/api/v1/live-shows/{show.id}/calls/next"
    r = await client.post(url, headers=headers)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "delay_not_safe"
    assert await _statuses(db_session, [a, b]) == ["on_air", "approved"]      # the caller on air was not cut off
    r = await client.post(url + "?force=true", headers=headers)
    assert r.status_code == 200 and await _statuses(db_session, [a, b]) == ["completed", "on_air"]
    air.count = int(10 * RATE)


@pytest.mark.asyncio
async def test_the_callers_topic_is_listed_and_the_note_can_be_changed(client, db_session):
    _s, show, (a,) = await _show_with_callers(db_session, [("Avi", CallStatus.WAITING)])
    a.notes = "Question about the parsha"
    await db_session.commit()
    headers = await _admin_headers(db_session)
    calls = (await client.get(f"/api/v1/live-shows/{show.id}/calls", headers=headers)).json()["calls"]
    assert calls[0]["notes"] == "Question about the parsha"
    r = await client.patch(f"/api/v1/live-shows/{show.id}/calls/{a.id}", headers=headers,
                           json={"notes": "Parsha question. Calls every week, speaks well."})
    assert r.status_code == 200 and r.json()["notes"].endswith("speaks well.")
    assert r.json()["caller_name"] == "Avi"   # the name is untouched
