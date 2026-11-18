"""Renumbering the queue at a song change is one statement, however long the queue.

The browser player's advance used to load every pending row and write each new
position by itself: hundreds of UPDATEs on a two-day queue, all before the next
item was saved and announced. The playout engine's start report had the same
cost until it was fixed; both (and the blackout fill) now share
app/services/queue_positions.py."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.queue_positions import compact_positions
from tests.conftest import engine


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)


async def _long_queue(db, pending: int, playing_position: int = 1, gap: int = 1):
    station = Station(id=uuid.uuid4(), name=f"Long Queue FM {uuid.uuid4().hex[:6]}", timezone="America/New_York")
    asset = Asset(id=uuid.uuid4(), title="Song", duration=180.0,
                  file_path="https://cdn.example.com/song.mp3", asset_type="music")
    db.add_all([station, asset])
    await db.flush()
    db.add(QueueEntry(
        id=uuid.uuid4(), station_id=station.id, asset_id=asset.id, position=playing_position,
        status="playing", source="auto", started_at=datetime.now(UTC) - timedelta(seconds=200),
    ))
    ids = []
    for i in range(pending):
        entry_id = uuid.uuid4()
        ids.append(entry_id)
        db.add(QueueEntry(
            id=entry_id, station_id=station.id, asset_id=asset.id, position=10 + i * gap,
            status="pending", source="auto",
        ))
    await db.commit()
    return station, ids


async def _positions(db, station_id):
    rows = (await db.execute(
        select(QueueEntry.id, QueueEntry.status, QueueEntry.position)
        .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
        .order_by(QueueEntry.position)
    )).all()
    return rows


class _Statements:
    def __init__(self):
        self.updates = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE QUEUE_ENTRIES"):
            self.updates += len(parameters) if executemany else 1


async def test_compact_keeps_order_and_puts_the_item_on_air_first(db_session):
    station, ids = await _long_queue(db_session, pending=5, playing_position=99, gap=7)
    await compact_positions(db_session, station.id)
    await db_session.commit()
    rows = await _positions(db_session, station.id)
    assert [r.position for r in rows] == [1, 2, 3, 4, 5, 6]
    assert rows[0].status == "playing"
    assert [r.id for r in rows[1:]] == ids  # pending items keep their order


async def test_compact_without_playing_first_keeps_plain_position_order(db_session):
    station, ids = await _long_queue(db_session, pending=3, playing_position=99)
    await compact_positions(db_session, station.id, playing_first=False)
    await db_session.commit()
    rows = await _positions(db_session, station.id)
    assert [r.position for r in rows] == [1, 2, 3, 4]
    assert rows[-1].status == "playing"


async def test_compact_leaves_other_stations_and_played_rows_alone(db_session):
    station, _ = await _long_queue(db_session, pending=3)
    other, other_ids = await _long_queue(db_session, pending=2, gap=50)
    played = QueueEntry(
        id=uuid.uuid4(), station_id=station.id, position=500, status="played", source="auto",
        asset_id=(await db_session.execute(select(Asset.id).limit(1))).scalar_one(),
    )
    db_session.add(played)
    await db_session.commit()
    await compact_positions(db_session, station.id)
    await db_session.commit()
    assert [r.position for r in await _positions(db_session, other.id)] == [1, 10, 60]
    assert (await db_session.execute(
        select(QueueEntry.position).where(QueueEntry.id == played.id)
    )).scalar_one() == 500


async def test_a_song_change_on_a_long_queue_renumbers_in_one_statement(db_session):
    """600 queued songs: the advance writes the old and new item, then ONE renumbering."""
    station, ids = await _long_queue(db_session, pending=600)
    counter = _Statements()
    event.listen(engine.sync_engine, "before_cursor_execute", counter)
    try:
        started = await queue_module._check_advance(db_session, station.id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", counter)
    assert started is not None and started.id == ids[0] and started.status == "playing"
    # Was 600+ (one per row). A handful: the two status changes, twins, renumbering.
    assert counter.updates <= 6, f"{counter.updates} UPDATE statements for one song change"
    rows = await _positions(db_session, station.id)
    assert [r.position for r in rows] == list(range(1, 601))
    assert rows[0].id == ids[0] and rows[0].status == "playing"
    assert [r.id for r in rows[1:]] == ids[1:]
