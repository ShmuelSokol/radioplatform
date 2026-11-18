"""How long a queue redraw holds its changes open.

Until 2026-11-18 a redraw deleted the automatic songs first and picked the new
ones afterwards, all in one transaction: about 800 statements on Main (7 to 8 s
on the live database, more on the first draw after a restart). The deleted rows
stayed locked all that time, and song changes, the playout engine's start
reports and library deletes waited behind them (one delete took 42 s). The count
of statements between the first change and its commit stands in for the time:
it does not depend on the machine.
"""

from datetime import time

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.queue_entry import QueueEntry
from app.services import schedule_refresh_service as refresh
from app.services.queue_replenish_service import QueueReplenishService
from tests.conftest import engine
from tests.test_block_on_time import _station_with_block

# Statements a redraw or a top-up may run between its first change and the commit
LIMIT = 30


class Spans:
    """Statements run between the first INSERT/UPDATE/DELETE and the commit."""

    def __init__(self):
        self.open: int | None = None
        self.longest = 0
        self.total = 0

    def execute(self, conn, cursor, statement, parameters, context, executemany):
        self.total += 1
        word = statement.lstrip().split(None, 1)[0].upper()
        if self.open is None and word in ("INSERT", "UPDATE", "DELETE"):
            self.open = 0
        if self.open is not None:
            self.open += 1
            self.longest = max(self.longest, self.open)

    def end(self, conn):
        self.open = None

    def __enter__(self):
        event.listen(engine.sync_engine, "before_cursor_execute", self.execute)
        event.listen(engine.sync_engine, "commit", self.end)
        event.listen(engine.sync_engine, "rollback", self.end)
        return self

    def __exit__(self, *exc):
        event.remove(engine.sync_engine, "before_cursor_execute", self.execute)
        event.remove(engine.sync_engine, "commit", self.end)
        event.remove(engine.sync_engine, "rollback", self.end)


async def _queue(db: AsyncSession, station_id) -> list:
    rows = await db.execute(
        select(QueueEntry.id, QueueEntry.asset_id)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending", QueueEntry.source == "auto")
        .order_by(QueueEntry.position)
    )
    return rows.all()


async def _filled_station(db: AsyncSession, name: str):
    station, _block, _songs = await _station_with_block(db, time(0), name)
    station.automation_config = {"playlist_generation": {"enabled": True, "hours": 24, "artist_distance": 2}}
    station_id = station.id
    await db.commit()
    await QueueReplenishService(db, station_id).replenish()
    await db.commit()
    return station_id


@pytest.mark.asyncio
async def test_a_redraw_picks_first_and_swaps_in_one_short_transaction(db_session: AsyncSession):
    station_id = await _filled_station(db_session, "Redraw locks")
    before = await _queue(db_session, station_id)
    assert len(before) > 300  # a day of four-minute songs

    with Spans() as spans:
        removed = await refresh.replace_automatic_songs(db_session, station_id)
    assert removed == len(before) - refresh.KEEP_NEXT
    # the picking itself is hundreds of statements; none of them inside a change
    assert spans.total > 300
    assert spans.longest <= LIMIT, f"{spans.longest} statements between a change and its commit"

    after = await _queue(db_session, station_id)
    assert after[0] == before[0], "the next song stays"
    assert len(after) > 300
    assert not {row[0] for row in before[1:]} & {row[0] for row in after}, "old songs are gone"


@pytest.mark.asyncio
async def test_a_top_up_after_a_song_holds_its_change_briefly(db_session: AsyncSession):
    station_id = await _filled_station(db_session, "Top-up locks")
    first = (await _queue(db_session, station_id))[0][0]
    await db_session.execute(QueueEntry.__table__.delete().where(QueueEntry.id == first))
    await db_session.commit()
    with Spans() as spans:
        await QueueReplenishService(db_session, station_id).replenish()
        await db_session.commit()
    assert spans.longest <= LIMIT, f"{spans.longest} statements between a change and its commit"


@pytest.mark.asyncio
async def test_a_song_that_started_during_the_picking_is_not_removed(db_session: AsyncSession, monkeypatch):
    """The swap happens after the picking: a song that went on air meanwhile,
    or that the playout engine took as its next track, must stay."""
    from app.services import playlist_generator_service as generator
    from app.services import playout_engine

    station_id = await _filled_station(db_session, "Redraw race")
    before = await _queue(db_session, station_id)
    started, handed = before[1][0], before[2][0]
    real = generator.generate

    async def slow_generate(db, *args, **kwargs):
        plan = await real(db, *args, **kwargs)
        # while the songs were being picked, the player moved on
        await db.execute(QueueEntry.__table__.update().where(QueueEntry.id == started).values(status="playing"))
        monkeypatch.setattr(playout_engine, "held_entry_ids", lambda _station: {handed})
        return plan

    monkeypatch.setattr(generator, "generate", slow_generate)
    removed = await refresh.replace_automatic_songs(db_session, station_id)
    assert removed == len(before) - 3
    db_session.expire_all()
    left = {
        row[0]: row[1]
        for row in (
            await db_session.execute(
                select(QueueEntry.id, QueueEntry.status).where(QueueEntry.id.in_([started, handed]))
            )
        ).all()
    }
    assert left == {started: "playing", handed: "pending"}


@pytest.mark.asyncio
async def test_a_failed_redraw_changes_nothing(db_session: AsyncSession, monkeypatch):
    from app.services import playlist_generator_service as generator

    station_id = await _filled_station(db_session, "Redraw fails")
    before = await _queue(db_session, station_id)

    async def broken(*args, **kwargs):
        raise RuntimeError("no database")

    monkeypatch.setattr(generator, "generate", broken)
    assert await refresh.replace_automatic_songs(db_session, station_id) == 0
    assert await _queue(db_session, station_id) == before
