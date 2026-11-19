"""A station ID is passed over at air time when it would follow a break or another
spoken item, or land next to a break (services/station_id_guard.py). The fill only
predicts air times; a soft break waits for the song, so the simulator heard an ID
0.9 minutes after a :30 break (2026-11-19)."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.queue import _pick_next_pending
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import playout_engine, station_id_guard


async def _setup(db: AsyncSession, before: str, break_in_minutes: float | None, song_left: float = 60.0,
                 source: str = "auto"):
    """On air: `before` ("song", "break" or "id"); then an ID, then a song; maybe a
    soft break due `break_in_minutes` from now."""
    now = datetime.now(UTC)
    station = Station(id=uuid.uuid4(), name=f"S {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      automation_config={"station_ids": {"enabled": True}})
    db.add(station)
    await db.flush()

    def asset(kind, category, seconds):
        a = Asset(id=uuid.uuid4(), title=f"{kind} {category}", file_path=f"{uuid.uuid4().hex}.mp3",
                  asset_type=kind, category=category, duration=seconds)
        db.add(a)
        return a

    song = asset("music", "lively", 200.0)
    after = asset("music", "lively", 210.0)
    sponsor = asset("spot", "sponsorship", 30.0)
    jingle = asset("jingle", "station_id", 8.0)
    jingle2 = asset("jingle", "station_id", 8.0)
    await db.flush()
    on_air = {"song": song, "break": sponsor, "id": jingle2}[before]
    started = now - timedelta(seconds=on_air.duration - song_left) if before == "song" else now - timedelta(seconds=5)
    db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=on_air.id, position=1,
                      status="playing", started_at=started,
                      preempt_at=now - timedelta(minutes=1) if before == "break" else None,
                      timing_mode="soft" if before == "break" else None,
                      source="block" if before == "break" else "auto"))
    the_id = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=jingle.id, position=2,
                        status="pending", source=source)
    nxt = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=after.id, position=3,
                     status="pending", source="auto")
    db.add_all([the_id, nxt])
    if break_in_minutes is not None:
        db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=sponsor.id, position=4,
                          status="pending", source="block", timing_mode="soft",
                          preempt_at=now + timedelta(minutes=break_in_minutes)))
    await db.commit()
    return station.id, the_id.id, nxt.id


async def _status(db, entry_id):
    db.expire_all()
    return (await db.execute(select(QueueEntry.status).where(QueueEntry.id == entry_id))).scalar()


@pytest.mark.asyncio
@pytest.mark.parametrize("before,break_in,song_left,airs", [
    ("song", None, 60.0, True),     # between songs, no break near: airs
    ("song", 20.0, 60.0, True),     # break well ahead: airs
    ("break", None, 60.0, False),   # right after a break: never spoken next to spoken
    ("id", None, 60.0, False),      # never two IDs
    ("song", 3.0, 60.0, False),     # the song ends in 1 min, a break 2 min after the ID
    ("song", 6.0, 200.0, False),    # the song has 3.3 min left: the ID would start 2.7 min before the break
])
async def test_browser_player_pick(db_session: AsyncSession, before, break_in, song_left, airs):
    station_id, id_entry, song_entry = await _setup(db_session, before, break_in, song_left)
    picked = await _pick_next_pending(db_session, station_id, datetime.now(UTC))
    await db_session.commit()
    if airs:
        assert picked.id == id_entry
    else:
        assert picked.id == song_entry
        assert await _status(db_session, id_entry) == "skipped"


@pytest.mark.asyncio
async def test_an_operators_id_is_never_second_guessed(db_session: AsyncSession):
    station_id, id_entry, _ = await _setup(db_session, "break", None, source="manual")
    picked = await _pick_next_pending(db_session, station_id, datetime.now(UTC))
    assert picked.id == id_entry


@pytest.mark.asyncio
async def test_engine_pick_passes_over_and_the_start_report_skips_it(db_session: AsyncSession):
    station_id, id_entry, song_entry = await _setup(db_session, "break", None)
    picked = await playout_engine.pick_next(db_session, station_id)
    assert picked.id == song_entry
    assert await _status(db_session, id_entry) == "pending"  # the pick only reads
    # asked again (the tick's check) it stays passed over
    assert (await playout_engine.pick_next(db_session, station_id)).id == song_entry
    assert str(id_entry) in station_id_guard.passed(station_id)
    assert await station_id_guard.take_passed(db_session, station_id) == 1
    await db_session.commit()
    assert await _status(db_session, id_entry) == "skipped"
    assert not station_id_guard.passed(station_id)


@pytest.mark.asyncio
async def test_engine_pick_between_songs_gives_the_id(db_session: AsyncSession):
    station_id, id_entry, _ = await _setup(db_session, "song", 20.0)
    assert (await playout_engine.pick_next(db_session, station_id)).id == id_entry


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes_ago,airs", [(10, False), (13, True)])
async def test_not_sooner_than_the_shortest_gap_after_the_last_id(db_session: AsyncSession, minutes_ago, airs):
    """Simulator: IDs 10.2 min apart after a hard :30 cut the song between them."""
    station_id, id_entry, song_entry = await _setup(db_session, "song", None)
    jingle = (await db_session.execute(select(Asset).where(Asset.category == "station_id"))).scalars().first()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station_id, asset_id=jingle.id, position=0,
                              status="played", source="auto",
                              started_at=datetime.now(UTC) - timedelta(minutes=minutes_ago)))
    await db_session.commit()
    picked = await _pick_next_pending(db_session, station_id, datetime.now(UTC))
    assert picked.id == (id_entry if airs else song_entry)
