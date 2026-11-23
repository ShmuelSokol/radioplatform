"""The queue page's times behind a shiur or story that a hard item pauses.

Simulator, Main 2026-11-19: a 55-minute shiur started at 00:47. The 1:00 time check
paused it and it carried on afterwards (long_items, "pause"), but the queue page read
at 00:30 counted it as cut at 1:00: the songs after it were shown 43 minutes early.
A "wait" station lets the shiur finish and the time check follows it.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import long_items


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def nothing(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)


def _asset(db, title, kind, duration):
    a = Asset(id=uuid.uuid4(), title=title, duration=float(duration), asset_type=kind,
              file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3")
    db.add(a)
    return a


def _shown(body, title):
    (t,) = [datetime.fromisoformat(r["estimated_start"]) for r in body["entries"]
            if (r.get("asset") or {}).get("title") == title]
    return t


async def _setup(db, *, policy: str | None, playing: bool):
    config = {"crossfade_seconds": 0}
    if policy:
        config[long_items.SETTING] = policy
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", automation_config=config)
    db.add(station)
    shiur = _asset(db, "Shiur", "shiur", 3000)
    check = _asset(db, "Time check", "time", 40)
    song = _asset(db, "Song after", "music", 200)
    await db.flush()
    now = datetime.now(UTC)
    if playing:
        # on air for 5 minutes already: 2,700 s left, the check is due in 10 minutes
        db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=shiur.id, position=1,
                          status="playing", source="auto", started_at=now - timedelta(seconds=300)))
        shiur_start = now - timedelta(seconds=300)
    else:
        lead = _asset(db, "Lead-in", "music", 120)
        await db.flush()
        db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=lead.id, position=1,
                          status="playing", source="auto", started_at=now))
        db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=shiur.id, position=2,
                          status="pending", source="auto"))
        shiur_start = now + timedelta(seconds=120)
    due = now + timedelta(minutes=10)
    db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=check.id, position=3,
                      status="pending", source="time", preempt_at=due, timing_mode="hard"))
    db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=4,
                      status="pending", source="auto"))
    await db.commit()
    return station, shiur_start, due


@pytest.mark.asyncio
@pytest.mark.parametrize("playing", [False, True], ids=["shiur-waiting", "shiur-on-air"])
async def test_a_paused_shiur_carries_on_after_the_hard_item(client, auth_headers, db_session, playing):
    station, shiur_start, due = await _setup(db_session, policy=None, playing=playing)  # pause is the default
    body = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    assert abs((_shown(body, "Time check") - due).total_seconds()) < 3
    # the whole shiur, the check and the 3 s heard again on resuming come before the song
    expected = shiur_start + timedelta(seconds=3000 + 40 + long_items.RESUME_REWIND_SECONDS)
    assert abs((_shown(body, "Song after") - expected).total_seconds()) < 10, (_shown(body, "Song after"), expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("playing", [False, True], ids=["shiur-waiting", "shiur-on-air"])
async def test_on_a_wait_station_the_hard_item_follows_the_shiur(client, auth_headers, db_session, playing):
    station, shiur_start, due = await _setup(db_session, policy="wait", playing=playing)
    body = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    end = shiur_start + timedelta(seconds=3000)
    assert abs((_shown(body, "Time check") - end).total_seconds()) < 10
    assert abs((_shown(body, "Song after") - end - timedelta(seconds=40)).total_seconds()) < 10


@pytest.mark.asyncio
async def test_on_a_cut_station_the_song_follows_the_hard_item(client, auth_headers, db_session):
    station, shiur_start, due = await _setup(db_session, policy="cut", playing=False)
    body = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    assert abs((_shown(body, "Song after") - due - timedelta(seconds=40)).total_seconds()) < 10


@pytest.mark.asyncio
async def test_times_past_the_run_up_to_the_next_break_are_marked_approximate(client, auth_headers, db_session):
    """Simulator 2026-11-23: the fit may still swap the last songs before a hard break and
    bring later ones forward, so their times are shown "≈" (estimate_firm false); the
    songs well before it, the break itself and the item on air are firm."""
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York",
                      automation_config={"crossfade_seconds": 0})
    db_session.add(station)
    now = datetime.now(UTC)
    on_air = _asset(db_session, "On air", "music", 240)
    songs = [_asset(db_session, f"Song {i}", "music", 300) for i in range(12)]
    check = _asset(db_session, "Time check", "time", 40)
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=on_air.id, position=1,
                              status="playing", source="auto", started_at=now))
    for i, a in enumerate(songs):
        db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=a.id, position=2 + i,
                                  status="pending", source="auto"))
    due = now + timedelta(minutes=40)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=check.id, position=99,
                              status="pending", source="time", preempt_at=due, timing_mode="hard"))
    await db_session.commit()
    body = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    firm = {(r.get("asset") or {}).get("title"): r["estimate_firm"] for r in body["entries"]}
    assert firm["On air"] and firm["Time check"]
    for r in body["entries"]:
        title = (r.get("asset") or {}).get("title")
        if r["status"] != "pending" or not title.startswith("Song"):
            continue
        before_zone = datetime.fromisoformat(r["estimated_start"]) < due - queue_module.FIRM_BEFORE_BREAK
        assert r["estimate_firm"] is before_zone, (title, r["estimated_start"])
    assert firm["Song 0"] and not firm["Song 11"]


@pytest.mark.asyncio
@pytest.mark.parametrize("wait", [True, False], ids=["sponsorships-wait", "sponsorship-pauses"])
async def test_a_sponsorship_is_shown_after_the_talk_where_sponsorships_wait(client, auth_headers, db_session, wait):
    """Simulator, Main 2026-11-23: the 12:30 sponsorship was shown at 00:30:08 and aired at
    00:40:11, after a 12-minute track, because the page counted it as pausing the track;
    on a station with sponsorships_wait_for_talks it waits for the end."""
    config = {"crossfade_seconds": 0, long_items.SPONSORSHIPS_WAIT: wait}
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", automation_config=config)
    db_session.add(station)
    shiur = _asset(db_session, "Shiur", "shiur", 3000)
    spot = _asset(db_session, "Sponsor", "spot", 30)
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=shiur.id, position=1,
                              status="playing", source="auto", started_at=now - timedelta(seconds=300)))
    due = now + timedelta(minutes=10)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=2,
                              status="pending", source="ad_slot", preempt_at=due, timing_mode="soft"))
    await db_session.commit()
    body = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    shown = _shown(body, "Sponsor")
    expected = now + timedelta(seconds=2700) if wait else due
    assert abs((shown - expected).total_seconds()) < 5, (shown, expected)
