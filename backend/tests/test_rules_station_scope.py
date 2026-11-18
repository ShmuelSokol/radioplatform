"""A station is judged only by its own rules.

2026-11-17, 10:24 PM ET: Shmuel moved a song up the queue of Kol B'ramah Main and the
control room warned: Daypart rule "All Day Stories (Stories Station)" expects stories
during 0:00-24:00, but this asset is music. The check on a queue move read every
station's rules, and compared their hours with the UTC hour instead of the station's
own clock. The fill already used the right rules (the station's own, or the global
ones when it has none); the move check and the Rules preview now read the same list.
"""
import uuid
from datetime import datetime, timezone

import pytest

import app.api.v1.queue as queue_module
from app.models.queue_entry import QueueEntry
from app.models.schedule_rule import ScheduleRule
from app.services.queue_replenish_service import rule_covers, rules_for_station
from tests.test_queue_endpoints import _no_replenish, _pending_ids, _station_with_queue, _url  # noqa: F401


def _rule(name, station=None, asset_type="shiur", start=0, end=24, **more):
    return ScheduleRule(
        id=uuid.uuid4(), name=name, rule_type="daypart", asset_type=asset_type,
        hour_start=start, hour_end=end, is_active=more.pop("is_active", True),
        station_id=station.id if station is not None else None, **more,
    )


async def _move_to_top(client, auth_headers, db_session, station):
    ids = await _pending_ids(db_session, station.id)
    top = (await db_session.get(QueueEntry, uuid.UUID(ids[0]), populate_existing=True)).position
    res = await client.post(_url(station, "/reorder-dnd"), headers=auth_headers,
                            json={"entry_id": ids[2], "new_position": top})
    assert res.status_code == 200
    return ids, res.json()["warnings"]


@pytest.mark.asyncio
async def test_another_stations_rule_does_not_warn_about_a_move_here(client, auth_headers, db_session):
    """The live case: the Stories station's all-day rule and a move on Main."""
    main, _, _ = await _station_with_queue(db_session, name="Main")
    stories, _, _ = await _station_with_queue(db_session, name="Stories")
    db_session.add(_rule("All Day Stories (Stories Station)", stories, asset_type="stories"))
    await db_session.commit()

    ids, warnings = await _move_to_top(client, auth_headers, db_session, main)
    assert warnings == []
    # The move itself is done
    assert await _pending_ids(db_session, main.id) == [ids[2], ids[0], ids[1], ids[3]]

    # On the station the rule belongs to, the same move is warned about (and still done)
    ids, warnings = await _move_to_top(client, auth_headers, db_session, stories)
    assert len(warnings) == 1 and "All Day Stories (Stories Station)" in warnings[0]
    assert await _pending_ids(db_session, stories.id) == [ids[2], ids[0], ids[1], ids[3]]


@pytest.mark.asyncio
async def test_a_station_with_its_own_rules_ignores_the_global_ones(client, auth_headers, db_session):
    own, _, _ = await _station_with_queue(db_session, name="Own rules")
    bare, _, _ = await _station_with_queue(db_session, name="No rules")
    db_session.add(_rule("Global shiurim", None))
    db_session.add(_rule("Music here", own, asset_type="music"))
    await db_session.commit()

    _, warnings = await _move_to_top(client, auth_headers, db_session, own)
    assert warnings == []
    # A station with no rules of its own follows the global ones
    _, warnings = await _move_to_top(client, auth_headers, db_session, bare)
    assert any("Global shiurim" in w for w in warnings)

    assert [r.name for r in await rules_for_station(db_session, own.id)] == ["Music here"]
    assert [r.name for r in await rules_for_station(db_session, bare.id)] == ["Global shiurim"]


@pytest.mark.asyncio
async def test_a_switched_off_rule_of_the_station_does_not_hide_the_global_rules(db_session):
    station, _, _ = await _station_with_queue(db_session, name="Off rule")
    db_session.add(_rule("Global", None))
    db_session.add(_rule("Off", station, is_active=False))
    await db_session.commit()
    assert [r.name for r in await rules_for_station(db_session, station.id)] == ["Global"]


@pytest.mark.asyncio
async def test_rule_hours_are_the_stations_clock_not_utc(client, auth_headers, db_session, monkeypatch):
    """03:24 UTC on Wednesday is 10:24 PM on Tuesday in Lakewood: an evening rule
    (20-24) applies, a rule for 3-4 in the morning does not, and Tuesday is the day."""
    class At(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 18, 3, 24, tzinfo=timezone.utc)

    station, _, _ = await _station_with_queue(db_session, name="Lakewood")
    station.timezone = "America/New_York"
    db_session.add(_rule("Evening shiurim", station, start=20, end=24))
    db_session.add(_rule("Small hours stories", station, asset_type="stories", start=3, end=4))
    db_session.add(_rule("Wednesday only", station, asset_type="jingle", days_of_week="2"))
    db_session.add(_rule("Tuesday only", station, asset_type="spot", days_of_week="1"))
    await db_session.commit()
    monkeypatch.setattr(queue_module, "datetime", At)

    _, warnings = await _move_to_top(client, auth_headers, db_session, station)
    named = {w.split('"')[1] for w in warnings}
    assert named == {"Evening shiurim", "Tuesday only"}


def test_rule_hours_past_midnight():
    rule = _rule("Night", start=22, end=2)
    assert rule_covers(rule, datetime(2026, 11, 17, 23, 0))
    assert rule_covers(rule, datetime(2026, 11, 18, 1, 59))
    assert not rule_covers(rule, datetime(2026, 11, 18, 2, 0))
    assert not rule_covers(rule, datetime(2026, 11, 18, 12, 0))


@pytest.mark.asyncio
async def test_rules_preview_for_a_station_shows_only_its_rules(client, auth_headers, db_session):
    main, _, _ = await _station_with_queue(db_session, name="Main")
    stories, _, _ = await _station_with_queue(db_session, name="Stories")
    db_session.add(_rule("Stories all day", stories, asset_type="stories", priority=50))
    db_session.add(_rule("Main music", main, asset_type="music"))
    await db_session.commit()

    def names(res):
        assert res.status_code == 200
        return {slot["rule_name"] for slot in res.json()["slots"]}

    base = "/api/v1/rules/preview?date=2026-11-17"
    assert names(await client.get(f"{base}&station_id={main.id}", headers=auth_headers)) == {"Main music"}
    assert names(await client.get(f"{base}&station_id={stories.id}", headers=auth_headers)) == {"Stories all day"}


@pytest.mark.asyncio
async def test_every_way_of_moving_gives_the_same_reminder(client, auth_headers, db_session):
    """Arrows, a typed position and drag-and-drop answer alike (2026-11-18: a drag on the
    Stories station warned about a music item, the arrows said nothing)."""
    main, _, _ = await _station_with_queue(db_session, name="Main")
    stories, _, _ = await _station_with_queue(db_session, name="Stories")
    db_session.add(_rule("All Day Stories (Stories Station)", stories, asset_type="stories"))
    await db_session.commit()

    async def answers(station):
        ids = await _pending_ids(db_session, station.id)
        out = {}
        res = await client.post(_url(station, "/move-up"), headers=auth_headers,
                                json={"entry_id": ids[2], "new_position": 0})
        assert res.status_code == 200
        out["up"] = res.json()["warnings"]
        res = await client.post(_url(station, "/move-down"), headers=auth_headers,
                                json={"entry_id": ids[2], "new_position": 0})
        assert res.status_code == 200
        out["down"] = res.json()["warnings"]
        res = await client.put(_url(station, "/reorder"), headers=auth_headers,
                               json={"entry_id": ids[3], "new_position": 1})
        assert res.status_code == 200
        out["typed"] = res.json()["warnings"]
        after = await _pending_ids(db_session, station.id)
        top = (await db_session.get(QueueEntry, uuid.UUID(after[0]), populate_existing=True)).position
        res = await client.post(_url(station, "/reorder-dnd"), headers=auth_headers,
                                json={"entry_id": after[2], "new_position": top})
        out["drag"] = res.json()["warnings"]
        # The moves themselves are done: up then down puts it back, then the last goes
        # first, then the third goes first
        assert after == [ids[3], ids[0], ids[1], ids[2]]
        assert await _pending_ids(db_session, station.id) == [after[2], after[0], after[1], after[3]]
        return out

    on_main = await answers(main)
    assert on_main == {"up": [], "down": [], "typed": [], "drag": []}
    on_stories = await answers(stories)
    assert all(len(w) == 1 and "All Day Stories (Stories Station)" in w[0] for w in on_stories.values())
    assert len({w[0] for w in on_stories.values()}) == 1  # word for word the same

    # An entry already first: the arrow does nothing and says nothing
    first = (await _pending_ids(db_session, stories.id))[0]
    res = await client.post(_url(stories, "/move-up"), headers=auth_headers,
                            json={"entry_id": first, "new_position": 0})
    assert res.status_code == 200 and res.json()["warnings"] == []
