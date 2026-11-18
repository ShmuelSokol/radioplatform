"""Caller history: repeat callers, priority and blocked callers."""
import pytest

from app.api.v1 import call_in as call_in_api
from app.api.v1 import live_shows as live_shows_api
from app.models.live_show import LiveShowStatus
from app.services import caller_history
from tests.test_live_caller_desk import _show_with_callers
from tests.test_live_delay import _admin_headers

BROWSER = "abcdefghij0123456789"
OTHER = "zyxwvutsrq9876543210"


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    call_in_api._recent.clear()
    said = []

    async def say(show_id, event, data):
        said.append((event, data))

    async def tell(call_id, status):
        said.append(("tell", {"call_id": str(call_id), "status": status}))

    monkeypatch.setattr(live_shows_api, "_say", say)
    monkeypatch.setattr(live_shows_api, "_tell_web_caller", tell)
    return said


async def _live_show(db_session):
    station, show, _ = await _show_with_callers(db_session, [])
    show.status = LiveShowStatus.LIVE
    show.calls_enabled = True
    await db_session.commit()
    return station, show


async def _call(client, station, browser=BROWSER, name="Moshe"):
    return await client.post(f"/api/v1/call-in/{station.id}", json={"name": name, "caller": browser})


def test_keys():
    assert caller_history.web_key(BROWSER) == f"web:{BROWSER}"
    assert caller_history.web_key("short") is None and caller_history.web_key(None) is None
    assert caller_history.web_key("has spaces and so on!!") is None
    assert caller_history.phone_key("+1 (732) 555-0101") == "tel:+17325550101"
    assert caller_history.phone_key("unknown") is None


@pytest.mark.asyncio
async def test_a_repeat_caller_is_listed_with_their_earlier_calls(client, db_session):
    station, show = await _live_show(db_session)
    headers = await _admin_headers(db_session)
    first = (await _call(client, station)).json()
    await client.post(f"/api/v1/live-shows/{show.id}/calls/{first['call_id']}/approve", headers=headers)
    r = await client.post(f"/api/v1/live-shows/{show.id}/calls/{first['call_id']}/on-air?force=true", headers=headers)
    assert r.status_code == 200, r.text
    await client.post(f"/api/v1/live-shows/{show.id}/calls/{first['call_id']}/end-call", headers=headers)
    second = (await _call(client, station)).json()
    stranger = (await _call(client, station, OTHER, "Dovid")).json()
    old_page = (await client.post(f"/api/v1/call-in/{station.id}", json={"name": "No id"})).json()
    calls = {c["id"]: c for c in (await client.get(f"/api/v1/live-shows/{show.id}/calls", headers=headers)).json()["calls"]}
    h = calls[second["call_id"]]["history"]
    assert h["calls"] == 1 and h["on_air"] == 1 and h["last_at"] and h["standing"] == "normal"
    assert calls[first["call_id"]]["history"]["calls"] == 1  # the later call counts for the earlier card too
    assert calls[stranger["call_id"]]["history"] == {"calls": 0, "on_air": 0, "last_at": None, "standing": "normal", "note": None}
    assert calls[old_page["call_id"]]["history"] is None
    # A call that does not say who is calling cannot be marked
    r = await client.put(f"/api/v1/live-shows/{show.id}/calls/{old_page['call_id']}/caller", json={"standing": "priority"}, headers=headers)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_priority_and_note_show_on_the_callers_cards_and_on_later_calls(client, db_session, _quiet):
    station, show = await _live_show(db_session)
    headers = await _admin_headers(db_session)
    call = (await _call(client, station)).json()
    url = f"/api/v1/live-shows/{show.id}/calls/{call['call_id']}/caller"
    r = await client.put(url, json={"standing": "priority", "note": " Rabbi of the shul "}, headers=headers)
    assert r.status_code == 200 and r.json() == {"standing": "priority", "note": "Rabbi of the shul", "removed": 0}
    updates = [d for e, d in _quiet if e == "caller_updated"]
    assert updates and updates[-1]["call_id"] == call["call_id"] and updates[-1]["history"]["standing"] == "priority"
    # A note alone keeps the standing
    await client.put(url, json={"note": "Knows the host"}, headers=headers)
    again = (await _call(client, station)).json()
    calls = {c["id"]: c for c in (await client.get(f"/api/v1/live-shows/{show.id}/calls", headers=headers)).json()["calls"]}
    assert calls[again["call_id"]]["history"]["standing"] == "priority"
    assert calls[again["call_id"]]["history"]["note"] == "Knows the host"
    listed = (await client.get("/api/v1/caller-profiles", headers=headers)).json()["callers"]
    assert [(c["name"], c["standing"], c["kind"]) for c in listed] == [("Moshe", "priority", "web")]
    assert (await client.put(url, json={"standing": "loud"}, headers=headers)).status_code == 422
    assert (await client.put(url, json={"standing": "priority"})).status_code in (401, 403)
    assert (await client.get("/api/v1/caller-profiles")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_blocking_removes_their_waiting_calls_refuses_new_ones_and_can_be_lifted(client, db_session, _quiet):
    station, show = await _live_show(db_session)
    headers = await _admin_headers(db_session)
    a = (await _call(client, station)).json()
    b = (await _call(client, station)).json()
    other = (await _call(client, station, OTHER, "Dovid")).json()
    r = await client.put(f"/api/v1/live-shows/{show.id}/calls/{a['call_id']}/caller", json={"standing": "blocked"}, headers=headers)
    assert r.status_code == 200 and r.json()["removed"] == 2
    assert {d["call_id"] for e, d in _quiet if e == "caller_removed"} == {a["call_id"], b["call_id"]}
    assert {d["call_id"] for e, d in _quiet if e == "tell" and d["status"] == "rejected"} == {a["call_id"], b["call_id"]}
    calls = {c["id"]: c["status"] for c in (await client.get(f"/api/v1/live-shows/{show.id}/calls", headers=headers)).json()["calls"]}
    assert calls[other["call_id"]] == "waiting"
    assert calls.get(a["call_id"], "rejected") == "rejected" and calls.get(b["call_id"], "rejected") == "rejected"
    refused = await _call(client, station)
    assert refused.status_code == 403 and "cannot take your call" in refused.text
    assert (await _call(client, station, OTHER, "Dovid")).status_code == 201
    # Refused calls do not use up the caller's rate limit
    assert sum(len(q) for q in call_in_api._recent.values()) == 4
    listed = (await client.get("/api/v1/caller-profiles", headers=headers)).json()["callers"]
    assert len(listed) == 1 and listed[0]["standing"] == "blocked"
    r = await client.put(f"/api/v1/caller-profiles/{listed[0]['id']}", json={"standing": "normal"}, headers=headers)
    assert r.status_code == 200 and r.json()["standing"] == "normal"
    assert (await client.get("/api/v1/caller-profiles", headers=headers)).json()["callers"] == []
    assert (await _call(client, station)).status_code == 201


@pytest.mark.asyncio
async def test_blocking_leaves_a_caller_who_is_on_air(client, db_session, _quiet):
    station, show = await _live_show(db_session)
    headers = await _admin_headers(db_session)
    a = (await _call(client, station)).json()
    await client.post(f"/api/v1/live-shows/{show.id}/calls/{a['call_id']}/approve", headers=headers)
    assert (await client.post(f"/api/v1/live-shows/{show.id}/calls/{a['call_id']}/on-air?force=true", headers=headers)).status_code == 200
    r = await client.put(f"/api/v1/live-shows/{show.id}/calls/{a['call_id']}/caller", json={"standing": "blocked"}, headers=headers)
    assert r.json()["removed"] == 0
    calls = {c["id"]: c for c in (await client.get(f"/api/v1/live-shows/{show.id}/calls", headers=headers)).json()["calls"]}
    assert calls[a["call_id"]]["status"] == "on_air" and calls[a["call_id"]]["history"]["standing"] == "blocked"
