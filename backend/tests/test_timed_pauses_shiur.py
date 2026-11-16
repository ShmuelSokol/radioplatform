"""The hourly time check pauses a shiur and the shiur carries on where it stopped.

Heard live on KBR Jerusalem: the time check cut a shiur 14 minutes in and the rest
of it was lost. Each station chooses pause (default), wait or cut.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry, resume_offset
from app.models.station import Station
from app.services import long_items


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


async def _shiur_at_the_hour(db, policy=None, kind="shiur", elapsed=14 * 60, duration=40 * 60):
    config = {"timed_during_long_items": policy} if policy else {}
    station = Station(id=uuid.uuid4(), name="Jerusalem", timezone="America/New_York",
                      automation_config=config)
    db.add(station)
    talk = Asset(id=uuid.uuid4(), title="Shiur", duration=float(duration),
                 file_path="https://cdn.example.com/shiur.mp3", asset_type=kind)
    time_check = Asset(id=uuid.uuid4(), title="Time", duration=8.0,
                       file_path="https://cdn.example.com/t.mp3", asset_type="jingle")
    weather = Asset(id=uuid.uuid4(), title="Weather", duration=30.0,
                    file_path="https://cdn.example.com/w.mp3", asset_type="jingle")
    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0,
                 file_path="https://cdn.example.com/s.mp3", asset_type="music")
    db.add_all([talk, time_check, weather, song])
    await db.flush()
    now = datetime.now(UTC)
    hour = now - timedelta(seconds=1)
    db.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=talk.id, position=1,
                   status="playing", source="auto", started_at=now - timedelta(seconds=elapsed)),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=time_check.id, position=2,
                   status="pending", preempt_at=hour, timing_mode="hard"),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=3,
                   status="pending", preempt_at=hour, timing_mode="hard"),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=4,
                   status="pending", source="auto"),
    ])
    await db.commit()
    return station, talk, time_check, weather, song


async def _finish_current(db, station_id):
    """Let the item on air reach its end, then advance."""
    current = (await db.execute(select(QueueEntry).where(
        QueueEntry.station_id == station_id, QueueEntry.status == "playing"))).scalar_one()
    current.started_at = datetime.now(UTC) - timedelta(seconds=current.asset.duration + 1)
    await db.commit()
    return await queue_module._check_advance(db, station_id)


@pytest.mark.asyncio
async def test_time_check_pauses_the_shiur_and_it_carries_on(db_session):
    station, talk, time_check, weather, song = await _shiur_at_the_hour(db_session)

    started = await queue_module._check_advance(db_session, station.id)
    assert started.asset_id == time_check.id  # on the hour, as before

    # The weather follows the time check; it does not cut it
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == time_check.id
    assert (await _finish_current(db_session, station.id)).asset_id == weather.id

    resumed = await _finish_current(db_session, station.id)
    assert resumed.asset_id == talk.id
    offset = resume_offset(resumed)
    assert 14 * 60 - long_items.RESUME_REWIND_SECONDS - 2 <= offset <= 14 * 60
    # It is on air from that point: started_at is set back by the offset
    played_for = (datetime.now(UTC) - resumed.started_at.replace(tzinfo=UTC)).total_seconds()
    assert abs(played_for - offset) < 2

    assert (await _finish_current(db_session, station.id)).asset_id == song.id


@pytest.mark.asyncio
async def test_resume_is_preloaded_from_where_it_stopped(db_session):
    from app.services.scheduler_engine import SchedulerEngine

    station, talk, *_ = await _shiur_at_the_hour(db_session)
    await queue_module._check_advance(db_session, station.id)
    weather_on_air = await _finish_current(db_session, station.id)
    payload = await SchedulerEngine().queue_entry_payload(db_session, station.id, weather_on_air)
    assert payload["next_asset"]["id"] == str(talk.id)
    assert payload["next_asset"]["cue_in"] >= 14 * 60 - 5


@pytest.mark.asyncio
async def test_wait_lets_the_shiur_finish_first(db_session):
    station, talk, time_check, weather, song = await _shiur_at_the_hour(db_session, policy="wait")

    assert (await queue_module._check_advance(db_session, station.id)).asset_id == talk.id
    timed = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.asset_id == time_check.id))).scalar_one()
    assert timed.timing_mode == "soft"  # so no player fades the shiur for it

    assert (await _finish_current(db_session, station.id)).asset_id == time_check.id
    assert (await _finish_current(db_session, station.id)).asset_id == weather.id
    assert (await _finish_current(db_session, station.id)).asset_id == song.id


@pytest.mark.asyncio
async def test_cut_keeps_the_old_behaviour(db_session):
    station, talk, time_check, weather, song = await _shiur_at_the_hour(db_session, policy="cut")
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == time_check.id
    await _finish_current(db_session, station.id)
    assert (await _finish_current(db_session, station.id)).asset_id == song.id


@pytest.mark.asyncio
async def test_songs_are_still_cut(db_session):
    station, talk, time_check, weather, song = await _shiur_at_the_hour(
        db_session, kind="music", elapsed=60, duration=240)
    assert (await queue_module._check_advance(db_session, station.id)).asset_id == time_check.id
    await _finish_current(db_session, station.id)
    assert (await _finish_current(db_session, station.id)).asset_id == song.id


@pytest.mark.asyncio
async def test_nearly_finished_shiur_is_not_resumed(db_session):
    station, talk, time_check, weather, song = await _shiur_at_the_hour(
        db_session, elapsed=40 * 60 - 10)
    await queue_module._check_advance(db_session, station.id)
    await _finish_current(db_session, station.id)
    assert (await _finish_current(db_session, station.id)).asset_id == song.id


def test_policy_reads_the_station_setting():
    class S:
        automation_config = {"timed_during_long_items": "WAIT"}
    assert long_items.policy(S()) == "wait"
    S.automation_config = {"timed_during_long_items": "nonsense"}
    assert long_items.policy(S()) == "pause"
    S.automation_config = None
    assert long_items.policy(S()) == "pause"
