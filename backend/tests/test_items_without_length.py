"""Items with no length (duration NULL) must not break the station.

On KBR Jerusalem a hard Next onto such an item made every queue read fail for
minutes (`duration - elapsed` with None), so the control room showed an empty,
stopped queue while the station was playing.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.services.duration_backfill as backfill
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station


async def _station_playing_an_item_without_length(db):
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="America/New_York")
    no_length = Asset(id=uuid.uuid4(), title="Od Yishoma", duration=None,
                      file_path="https://cdn.example.com/od.mp3", asset_type="music")
    after = Asset(id=uuid.uuid4(), title="After", duration=None,
                  file_path="https://cdn.example.com/after.mp3", asset_type="music")
    db.add_all([station, no_length, after])
    await db.flush()
    db.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=no_length.id, position=1,
                   status="playing", source="auto", started_at=datetime.now(UTC) - timedelta(seconds=20)),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=after.id, position=2,
                   status="pending", source="auto", preempt_at=datetime.now(UTC) + timedelta(minutes=30),
                   timing_mode="hard"),
    ])
    await db.commit()
    return station, no_length


@pytest.mark.asyncio
async def test_queue_read_works_when_the_item_on_air_has_no_length(client, auth_headers, db_session):
    station, no_length = await _station_playing_an_item_without_length(db_session)
    res = await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["now_playing"]["asset_id"] == str(no_length.id)
    assert body["now_playing"]["remaining_seconds"] > 0  # timed with the default length
    assert len(body["entries"]) == 2


@pytest.mark.asyncio
async def test_missing_lengths_are_measured(db_session, monkeypatch):
    station, no_length = await _station_playing_an_item_without_length(db_session)

    async def fake_measure(file_path):
        return 0.0 if file_path.endswith("after.mp3") else 245.5

    monkeypatch.setattr(backfill, "measure", fake_measure)
    result = await backfill.backfill_missing_durations(db_session)
    assert result["fixed"] == 1
    assert len(result["failed"]) == 1
    assert result["still_missing"] == 1
    measured = (await db_session.execute(select(Asset.duration).where(Asset.id == no_length.id))).scalar_one()
    assert measured == 245.5


@pytest.mark.asyncio
async def test_a_restart_keeps_an_item_without_length_on_air(db_session, monkeypatch):
    """A restart used to treat an item with no length as finished and cut it."""
    import app.api.v1.queue as queue_module
    import app.db.engine as db_engine
    from app.main import _resume_playback_on_startup
    from tests.conftest import TestSessionLocal

    station, no_length = await _station_playing_an_item_without_length(db_session)

    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(db_engine, "async_session_factory", TestSessionLocal)
    monkeypatch.setattr(
        "app.services.queue_replenish_service.QueueReplenishService.replenish",
        lambda self: no_replenish(None, None),
    )
    await _resume_playback_on_startup()

    rows = (await db_session.execute(
        select(QueueEntry.asset_id, QueueEntry.status)
        .where(QueueEntry.station_id == station.id)
        .execution_options(populate_existing=True)
    )).all()
    assert (no_length.id, "playing") in rows
    # the item timed for later was not pulled forward
    assert sum(1 for _, status in rows if status == "playing") == 1
