"""KBR Studio's HS, Spots mode and Panic buttons (wiki/station-modes.md).

HS off: timed items wait for the song (Shabbos silence still starts on time); HS on
puts back exactly what it changed. Spots mode off: no sponsorship is queued. Panic:
cuts even a timed item that Next would leave on air.
"""
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.alert import Alert
from app.models.asset import Asset
from app.models.hs_softened import HsSoftened
from app.models.queue_entry import QueueEntry
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.services.spot_schedule_service import plan_for_station, reconcile_spot_slots
from app.services.station_modes import soften_hard_items
from tests.conftest import TestSessionLocal


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", nothing)
    queue_module._automatic_change.clear()
    yield
    queue_module._automatic_change.clear()


async def _row(db, entry_id):
    return (await db.execute(
        select(QueueEntry).where(QueueEntry.id == entry_id).execution_options(populate_existing=True)
    )).scalar_one()


@pytest.mark.asyncio
async def test_hs_off_makes_timed_items_wait_and_on_puts_them_back(client, auth_headers, db_session):
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0, file_path="s", asset_type="music")
    time_check = Asset(id=uuid.uuid4(), title="9 PM", duration=8.0, file_path="t", asset_type="jingle")
    silence = Asset(id=uuid.uuid4(), title="Shabbos", duration=3600.0, file_path="x", asset_type="silence")
    db_session.add_all([station, song, time_check, silence])
    await db_session.flush()
    at = datetime.now(UTC) + timedelta(hours=1)
    legacy = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=time_check.id, position=2,
                        status="pending", source="auto", preempt_at=at)  # old rule: hard
    hard = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=time_check.id, position=3,
                      status="pending", source="block", preempt_at=at, timing_mode="hard")
    limited = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=time_check.id, position=4,
                         status="pending", source="block", preempt_at=at, timing_mode="soft",
                         hard_by=at + timedelta(minutes=3))
    shabbos = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=silence.id, position=5,
                         status="pending", source="auto", preempt_at=at, timing_mode="hard")
    db_session.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1,
                   status="playing", source="auto", started_at=datetime.now(UTC)),
        legacy, hard, limited, shabbos,
    ])
    await db_session.commit()
    url = f"/api/v1/stations/{station.id}/queue"

    body = (await client.get(url, headers=auth_headers)).json()
    assert body["modes"] == {"hard_start": True, "spots": True}

    r = await client.post(f"{url}/modes", json={"hard_start": False}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["modes"]["hard_start"] is False
    for e in (legacy, hard, limited):
        row = await _row(db_session, e.id)
        assert row.timing_mode == "soft" and row.hard_by is None
    assert (await _row(db_session, shabbos.id)).timing_mode == "hard"

    # A timed item that arrives while HS is off waits too (the scheduler pass)
    late = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=time_check.id, position=6,
                      status="pending", source="block", preempt_at=at + timedelta(hours=1), timing_mode="hard")
    db_session.add(late)
    await db_session.commit()
    st = await db_session.get(Station, station.id)
    assert await soften_hard_items(db_session, st) == 1
    await db_session.commit()
    assert (await _row(db_session, late.id)).timing_mode == "soft"

    r = await client.post(f"{url}/modes", json={"hard_start": True}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["modes"]["hard_start"] is True
    assert (await _row(db_session, legacy.id)).timing_mode == "hard"
    assert (await _row(db_session, hard.id)).timing_mode == "hard"
    back = await _row(db_session, limited.id)
    assert back.timing_mode == "soft" and back.hard_by is not None
    assert (await _row(db_session, late.id)).timing_mode == "hard"


@pytest.mark.asyncio
async def test_spots_mode_off_queues_no_sponsorship(client, auth_headers, db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    sponsor = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, priority=1, audio_file_path="")
    spot = Asset(id=uuid.uuid4(), title="Bagel King", duration=30.0, file_path="b", asset_type="spot")
    db_session.add_all([station, sponsor, spot])
    await db_session.flush()
    db_session.add(SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=station.id,
                                dayparts="morning,afternoon,evening,overnight", plays_per_daypart=2,
                                start_date=date.today() - timedelta(days=1), end_date=date.today() + timedelta(days=5)))
    await db_session.commit()

    placements, _ = await plan_for_station(db_session, station)
    assert placements, "the schedule plans spots while Spots mode is on"
    await reconcile_spot_slots(db_session, station.id)
    await db_session.commit()
    count = lambda: db_session.execute(select(QueueEntry).where(  # noqa: E731
        QueueEntry.station_id == station.id, QueueEntry.source == "ad_slot", QueueEntry.status == "pending"))
    assert (await count()).scalars().all()

    r = await client.post(f"/api/v1/stations/{station.id}/queue/modes", json={"spots": False}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["modes"]["spots"] is False
    far = datetime.now(UTC) + timedelta(minutes=5)
    left = [e for e in (await count()).scalars().all() if e.preempt_at.replace(tzinfo=UTC) > far]
    assert left == []
    st = await db_session.get(Station, station.id)
    assert (await plan_for_station(db_session, st)) == ([], [])

    await client.post(f"/api/v1/stations/{station.id}/queue/modes", json={"spots": True}, headers=auth_headers)
    assert (await count()).scalars().all()


@pytest.mark.asyncio
async def test_panic_cuts_even_a_timed_item_and_logs_it(client, auth_headers, db_session):
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    weather = Asset(id=uuid.uuid4(), title="Broken weather", duration=40.0, file_path="w", asset_type="spot")
    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0, file_path="s", asset_type="music")
    db_session.add_all([station, weather, song])
    await db_session.flush()
    now = datetime.now(UTC)
    on_air = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=1, status="playing",
                        source="block", preempt_at=now - timedelta(seconds=3), timing_mode="hard",
                        started_at=now - timedelta(seconds=3))
    nxt = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=2,
                     status="pending", source="auto")
    db_session.add_all([on_air, nxt])
    await db_session.commit()
    url = f"/api/v1/stations/{station.id}/queue"

    # Next leaves a timed item on air ...
    assert (await client.post(f"{url}/skip", headers=auth_headers)).json().get("protected") is True
    # ... Panic does not
    body = (await client.post(f"{url}/panic", headers=auth_headers)).json()
    assert body["message"] == "Skipped"
    assert (await _row(db_session, on_air.id)).status == "skipped"
    assert (await _row(db_session, nxt.id)).status == "playing"
    alerts = (await db_session.execute(select(Alert).where(Alert.title == "Panic pressed in the control room"))).scalars().all()
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_viewer_cannot_switch_modes_or_panic(client, db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="UTC")
    db_session.add(station)
    await db_session.commit()
    for path in ("modes", "panic"):
        r = await client.post(f"/api/v1/stations/{station.id}/queue/{path}", json={})
        assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_hs_on_restores_every_softened_item_however_many(client, auth_headers, db_session):
    """No cap that drops saved timings: 2,500 timed items all come back hard."""
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    check = Asset(id=uuid.uuid4(), title="Time", duration=8.0, file_path="t", asset_type="jingle")
    db_session.add_all([station, check])
    await db_session.flush()
    at = datetime.now(UTC) + timedelta(hours=1)
    ids = [uuid.uuid4() for _ in range(2500)]
    db_session.add_all([
        QueueEntry(id=i, station_id=station.id, asset_id=check.id, position=n + 1, status="pending",
                   source="block", preempt_at=at + timedelta(minutes=n), timing_mode="hard")
        for n, i in enumerate(ids)
    ])
    await db_session.commit()
    url = f"/api/v1/stations/{station.id}/queue/modes"
    assert (await client.post(url, json={"hard_start": False}, headers=auth_headers)).status_code == 200
    # The scheduler pass runs again and again while HS is off: still one saved timing each
    st = await db_session.get(Station, station.id)
    await soften_hard_items(db_session, st)
    await db_session.commit()
    assert (await client.post(url, json={"hard_start": True}, headers=auth_headers)).status_code == 200
    modes = (await db_session.execute(
        select(QueueEntry.timing_mode).where(QueueEntry.station_id == station.id)
        .execution_options(populate_existing=True))).scalars().all()
    assert len(modes) == 2500 and set(modes) == {"hard"}
    assert (await db_session.execute(select(HsSoftened))).scalars().all() == []
    assert "hs_softened" not in ((await db_session.get(Station, station.id)).automation_config or {})


@pytest.mark.asyncio
async def test_hs_pass_never_rewrites_station_settings(db_session):
    """The scheduler's HS pass must not undo a setting saved meanwhile (loop, live show, HS itself)."""
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem",
                      automation_config={"hard_start": False})
    check = Asset(id=uuid.uuid4(), title="Time", duration=8.0, file_path="t", asset_type="jingle")
    db_session.add_all([station, check])
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=check.id, position=1,
                              status="pending", source="block", timing_mode="hard",
                              preempt_at=datetime.now(UTC) + timedelta(hours=1)))
    await db_session.commit()
    stale = await db_session.get(Station, station.id)
    assert stale.automation_config == {"hard_start": False}

    # Meanwhile another request sets a loop and switches HS on
    async with TestSessionLocal() as other:
        st = await other.get(Station, station.id)
        st.automation_config = {"hard_start": True, "loop_asset_id": str(check.id)}
        await other.commit()

    assert await soften_hard_items(db_session, stale) == 1  # read before the change
    await db_session.commit()
    async with TestSessionLocal() as fresh:
        cfg = (await fresh.get(Station, station.id)).automation_config
    assert cfg == {"hard_start": True, "loop_asset_id": str(check.id)}


@pytest.mark.asyncio
async def test_hs_on_restores_timings_saved_by_the_first_version(client, auth_headers, db_session):
    at = datetime.now(UTC) + timedelta(hours=1)
    station = Station(id=uuid.uuid4(), name="Main", timezone="UTC")
    check = Asset(id=uuid.uuid4(), title="Time", duration=8.0, file_path="t", asset_type="jingle")
    db_session.add_all([station, check])
    await db_session.flush()
    e = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=check.id, position=1, status="pending",
                   source="block", timing_mode="soft", preempt_at=at)
    db_session.add(e)
    station.automation_config = {"hard_start": False, "hs_softened": {str(e.id): ["hard", None]}}
    await db_session.commit()
    r = await client.post(f"/api/v1/stations/{station.id}/queue/modes", json={"hard_start": True}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert (await _row(db_session, e.id)).timing_mode == "hard"
