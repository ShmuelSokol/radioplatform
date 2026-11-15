"""The precise timer advances at the crossfade point, not at the very end of the track."""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station


async def _station_with_two_tracks(db_session, elapsed_seconds: float):
    station = Station(id=uuid.uuid4(), name="Advance FM", timezone="America/New_York")
    db_session.add(station)
    assets = []
    for i in range(2):
        asset = Asset(
            id=uuid.uuid4(), title=f"Track {i}", duration=180.0,
            file_path=f"https://cdn.example.com/{i}.mp3", asset_type="music",
        )
        db_session.add(asset)
        assets.append(asset)
    await db_session.flush()
    playing = QueueEntry(
        id=uuid.uuid4(), station_id=station.id, asset_id=assets[0].id, position=1,
        status="playing", source="auto",
        started_at=datetime.now(UTC) - timedelta(seconds=elapsed_seconds),
    )
    pending = QueueEntry(
        id=uuid.uuid4(), station_id=station.id, asset_id=assets[1].id, position=2,
        status="pending", source="auto",
    )
    db_session.add_all([playing, pending])
    await db_session.commit()
    return station, playing, pending


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


@pytest.mark.asyncio
async def test_poll_does_not_advance_before_the_end(db_session):
    station, playing, _ = await _station_with_two_tracks(db_session, elapsed_seconds=177.5)
    entry = await queue_module._check_advance(db_session, station.id)
    assert entry.id == playing.id


@pytest.mark.asyncio
async def test_precise_timer_advances_at_crossfade_point(db_session):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=177.0)
    entry = await queue_module._check_advance(db_session, station.id, early_seconds=3.0)
    assert entry.id == pending.id
    assert entry.status == "playing"
    await db_session.refresh(playing)
    assert playing.status == "played"


@pytest.mark.asyncio
async def test_precise_timer_does_not_advance_too_early(db_session):
    station, playing, _ = await _station_with_two_tracks(db_session, elapsed_seconds=170.0)
    entry = await queue_module._check_advance(db_session, station.id, early_seconds=3.0)
    assert entry.id == playing.id


@pytest.mark.asyncio
async def test_two_advances_at_once_promote_only_one_entry(db_session):
    """The poll and the precise timer can fire together, each with its own session.
    Exactly one pending entry may be promoted; the other must stay pending."""
    from sqlalchemy import select

    from tests.conftest import TestSessionLocal

    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=177.5)
    third_asset = Asset(
        id=uuid.uuid4(), title="Track 2", duration=180.0,
        file_path="https://cdn.example.com/2.mp3", asset_type="music",
    )
    db_session.add(third_asset)
    await db_session.flush()
    third = QueueEntry(
        id=uuid.uuid4(), station_id=station.id, asset_id=third_asset.id, position=3,
        status="pending", source="auto",
    )
    db_session.add(third)
    await db_session.commit()

    async def advance():
        async with TestSessionLocal() as session:
            # Each caller has already seen the current entry as "playing"
            await session.execute(select(QueueEntry).where(QueueEntry.station_id == station.id))
            entry = await queue_module._check_advance(session, station.id, early_seconds=3.0)
            return entry.id

    first, second = await asyncio.gather(advance(), advance())
    assert first == second == pending.id

    async with TestSessionLocal() as session:
        rows = (await session.execute(
            select(QueueEntry.id, QueueEntry.status).where(QueueEntry.station_id == station.id)
        )).all()
    statuses = {row.id: row.status for row in rows}
    assert statuses[playing.id] == "played"
    assert statuses[pending.id] == "playing"
    assert statuses[third.id] == "pending"
