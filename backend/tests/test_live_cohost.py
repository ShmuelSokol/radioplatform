"""A co-host joins the show from another place: mixed in like a caller on air, hears
everyone but themselves, and leaves with the show."""
import pytest

from app.api.v1.live_shows_ws import CallerLine, StationLiveRelay
from tests.test_live_talkback import FakeWS, pcm, samples


def _relay():
    relay = StationLiveRelay()
    listener, host = FakeWS(), FakeWS()
    relay.listeners["st"] = {listener}
    relay.hosts["st"] = host
    co = CallerLine(FakeWS(), "cohost:u1", "show", "st")
    co.on_air, co.cohost = True, "Rabbi Cohen"
    caller = CallerLine(FakeWS(), "c1", "show", "st")
    caller.on_air = True
    relay.callers = {"cohost:u1": co, "c1": caller}
    return relay, listener, host, co, caller


@pytest.mark.asyncio
async def test_listeners_hear_the_cohost_and_everyone_hears_all_but_themselves():
    relay, listener, host, co, caller = _relay()
    co.add(pcm(300, 8000))
    caller.add(pcm(50, 8000))
    await relay.audio("st", pcm(1000, 2048))
    assert set(samples(listener.bytes[-1])) == {1350}          # host + co-host + caller
    assert set(samples(host.bytes[-1])) == {350}               # the host: co-host and caller
    assert set(samples(co.ws.bytes[-1])) == {1050}             # the co-host: host and caller, not themselves
    assert set(samples(caller.ws.bytes[-1])) == {1300}         # the caller: host and co-host


@pytest.mark.asyncio
async def test_cohosts_are_named_per_show_and_leave_with_the_show():
    relay, _listener, _host, co, caller = _relay()
    other = CallerLine(FakeWS(), "cohost:u2", "another", "st2")
    other.on_air, other.cohost = True, "Someone Else"
    relay.callers["cohost:u2"] = other
    assert relay.cohosts("show") == ["Rabbi Cohen"] and relay.cohosts("another") == ["Someone Else"]
    await relay.drop_cohosts("show")
    assert co.ws.closed == 4000 and "cohost:u1" not in relay.callers
    assert other.ws.closed is None and caller.ws.closed is None and set(relay.callers) == {"c1", "cohost:u2"}
    assert relay.cohosts("show") == []


@pytest.mark.asyncio
async def test_ending_a_show_with_a_cohost_still_ends_its_callers(client, db_session):
    """The co-host's line has no call behind it: looking one up used to be an error
    that left the web callers of the show connected."""
    from app.api.v1 import live_shows_ws
    from app.models.call_in_request import CallStatus
    from tests.test_live_caller_desk import _show_with_callers, _statuses
    from tests.test_live_delay import _admin_headers

    station, show, (a,) = await _show_with_callers(db_session, [("Avi", CallStatus.ON_AIR)])
    headers = await _admin_headers(db_session)
    relay = live_shows_ws.live_relay
    co = CallerLine(FakeWS(), "cohost:u1", str(show.id), str(station.id))
    co.on_air, co.cohost = True, "Rabbi Cohen"
    line = CallerLine(FakeWS(), str(a.id), str(show.id), str(station.id))
    relay.callers.update({"cohost:u1": co, str(a.id): line})
    try:
        r = await client.get(f"/api/v1/live-shows/{show.id}/air", headers=headers)
        assert r.json()["cohosts"] == ["Rabbi Cohen"]
        r = await client.post(f"/api/v1/live-shows/{show.id}/end", headers=headers)
        assert r.status_code == 200, r.text
        assert co.ws.closed == 4000 and "cohost:u1" not in relay.callers
        assert await _statuses(db_session, [a]) == ["completed"]
        assert any('"completed"' in t for t in line.ws.texts)
    finally:
        relay.callers.pop("cohost:u1", None)
        relay.callers.pop(str(a.id), None)
