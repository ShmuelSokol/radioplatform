"""The caller desk of a live show: Hold, Next caller, and the caller's note.
See wiki/live-shows.md, "Caller desk"."""
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.api.v1 import live_shows_ws
from app.models.call_in_request import CallInRequest, CallStatus
from app.models.live_show import LiveShowStatus
from app.services.live_air import RATE, LiveAir
from tests.test_live_delay import Clock, _admin_headers, _nowhere, _station, run, talker


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    from app.api.v1 import live_shows
    live_shows_ws.live_relay.airs.clear()
    live_shows._next_last.clear()
    # These tests press Next again at once on purpose; the double-press rule has its own test
    monkeypatch.setattr(live_shows, "NEXT_REPEAT_SECONDS", 0.0)
    yield
    live_shows_ws.live_relay.airs.clear()
    live_shows._next_last.clear()


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


@pytest.mark.asyncio
async def test_several_callers_on_air_together_up_to_four(client, db_session):
    names = [(n, CallStatus.APPROVED) for n in ("Avi", "Berel", "Chaim", "Dovid", "Eli", "Fishel")]
    _s, show, calls = await _show_with_callers(db_session, names)
    headers = await _admin_headers(db_session)
    for c in calls[:4]:
        r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{c.id}/on-air", headers=headers)
        assert r.status_code == 200, r.text
    assert await _statuses(db_session, calls) == ["on_air"] * 4 + ["approved"] * 2
    # A fifth is refused, and the answer says what to do
    r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{calls[4].id}/on-air", headers=headers)
    assert r.status_code == 409 and "4 callers are on air already" in r.text
    # Hold one: there is room again
    assert (await client.post(f"/api/v1/live-shows/{show.id}/calls/{calls[0].id}/hold", headers=headers)).status_code == 200
    assert (await client.post(f"/api/v1/live-shows/{show.id}/calls/{calls[4].id}/on-air", headers=headers)).status_code == 200
    assert await _statuses(db_session, calls) == ["approved", "on_air", "on_air", "on_air", "on_air", "approved"]
    # Next caller ends everyone on air and brings on who waited longest
    r = await client.post(f"/api/v1/live-shows/{show.id}/calls/next", headers=headers)
    assert r.status_code == 200 and r.json()["on_air"] == str(calls[0].id)
    assert await _statuses(db_session, calls) == ["on_air", "completed", "completed", "completed", "completed", "approved"]


@pytest.mark.asyncio
async def test_callers_on_air_together_hear_each_other_and_not_themselves():
    import numpy as np

    from app.api.v1.live_shows_ws import CallerLine, StationLiveRelay
    from tests.test_live_talkback import FakeWS, pcm, samples

    relay = StationLiveRelay()
    listener, host = FakeWS(), FakeWS()
    relay.listeners["st"] = {listener}
    relay.hosts["st"] = host
    lines = [CallerLine(FakeWS(), f"c{i}", "show", "st") for i in range(3)]
    relay.callers = {c.call_id: c for c in lines}
    for c, level in zip(lines, (100, 20, 3)):
        c.on_air = True
        c.add(pcm(level, 4000))
    await relay.audio("st", pcm(1000, 2048))
    assert set(samples(listener.bytes[-1])) == {1123}                    # host + all three
    assert set(samples(host.bytes[-1])) == {123}                         # the host hears the three callers
    assert [int(np.unique(samples(c.ws.bytes[-1]))[0]) for c in lines] == [1023, 1103, 1120]   # everyone but themselves


@pytest.mark.asyncio
async def test_a_locked_call_is_left_alone_by_end_hold_reject_and_next(client, db_session):
    _s, show, (guest, a, b, w) = await _show_with_callers(db_session, [
        ("Guest", CallStatus.ON_AIR), ("Avi", CallStatus.ON_AIR), ("Berel", CallStatus.APPROVED), ("Wolf", CallStatus.WAITING)])
    headers = await _admin_headers(db_session)
    base = f"/api/v1/live-shows/{show.id}/calls"
    r = await client.post(f"{base}/{guest.id}/lock", json={"locked": True}, headers=headers)
    assert r.status_code == 200 and r.json()["locked"] is True
    listed = {c["id"]: c["locked"] for c in (await client.get(base, headers=headers)).json()["calls"]}
    assert listed[str(guest.id)] is True and listed[str(a.id)] is False
    for action in ("end-call", "hold"):
        r = await client.post(f"{base}/{guest.id}/{action}", headers=headers)
        assert r.status_code == 409 and r.json()["detail"] == "This call is locked. Unlock it first."
    await client.post(f"{base}/{w.id}/lock", json={"locked": True}, headers=headers)
    assert (await client.post(f"{base}/{w.id}/reject", headers=headers)).status_code == 409
    # Next ends the unlocked caller only; the guest stays on air with the next caller
    r = await client.post(f"{base}/next", headers=headers)
    assert r.status_code == 200 and r.json() == {"ended": str(a.id), "on_air": str(b.id)}
    assert await _statuses(db_session, [guest, a, b, w]) == ["on_air", "completed", "on_air", "waiting"]
    await client.post(f"{base}/{b.id}/end-call", headers=headers)
    r = await client.post(f"{base}/next", headers=headers)
    assert r.status_code == 409 and "locked" in r.json()["detail"]
    # Unlocked: everything works again
    r = await client.post(f"{base}/{guest.id}/lock", json={"locked": False}, headers=headers)
    assert r.json()["locked"] is False
    assert (await client.post(f"{base}/{guest.id}/end-call", headers=headers)).status_code == 200
    assert (await client.post(f"{base}/{guest.id}/lock", json={"locked": True})).status_code in (401, 403)
    assert (await client.post(f"{base}/{guest.id}/lock", json={}, headers=headers)).status_code == 422


@pytest.mark.asyncio
async def test_dump_and_drop_takes_a_locked_caller_off_too(client, db_session):
    station, show, (guest,) = await _show_with_callers(db_session, [("Guest", CallStatus.ON_AIR)], {"live_delay_seconds": 2})
    headers = await _admin_headers(db_session)
    await client.post(f"/api/v1/live-shows/{show.id}/calls/{guest.id}/lock", json={"locked": True}, headers=headers)
    relay = live_shows_ws.live_relay
    await relay.set_on_air(str(station.id), str(show.id), delay=2.0)
    air = relay.airs[str(station.id)]
    air.feed(np.zeros(RATE * 2, dtype=np.int16) + 1000)
    r = await client.post(f"/api/v1/live-shows/{show.id}/dump", json={"drop_caller": True}, headers=headers)
    assert r.status_code == 200, r.text
    assert await _statuses(db_session, [guest]) == ["completed"]
    await air.stop()


@pytest.mark.asyncio
async def test_next_pressed_twice_takes_one_caller_and_loses_none(client, db_session, monkeypatch):
    """LD-37: a double click on Next (or the host and the screener together). The
    second press ended the caller the first had just put on air and took the one after."""
    import asyncio

    from app.api.v1 import live_shows
    monkeypatch.setattr(live_shows, "NEXT_REPEAT_SECONDS", 1.0)
    _s, show, (a, b, c) = await _show_with_callers(db_session, [
        ("Avi", CallStatus.ON_AIR), ("Berel", CallStatus.APPROVED), ("Chaim", CallStatus.APPROVED)])
    headers = await _admin_headers(db_session)
    url = f"/api/v1/live-shows/{show.id}/calls/next"
    # One after the other here (the tests share one database session); two at the same
    # moment wait for each other on the show's lock and then read the same answer
    first = await client.post(url, headers=headers)
    second = await client.post(url, headers=headers)
    assert isinstance(live_shows._next_locks[str(show.id)], asyncio.Lock)
    answers = sorted([first.json(), second.json()], key=lambda r: bool(r.get("repeat")))
    assert first.status_code == second.status_code == 200
    assert answers[0] == {"ended": str(a.id), "on_air": str(b.id)}
    assert answers[1] == {"ended": str(a.id), "on_air": str(b.id), "repeat": True}
    assert await _statuses(db_session, [a, b, c]) == ["completed", "on_air", "approved"]
    # A third press right after is the same double click; a press a second later is a new one
    assert (await client.post(url, headers=headers)).json().get("repeat") is True
    live_shows._next_last[str(show.id)] = (live_shows._next_last[str(show.id)][0] - 2.0, {})
    r = await client.post(url, headers=headers)
    assert r.json() == {"ended": str(b.id), "on_air": str(c.id)}
