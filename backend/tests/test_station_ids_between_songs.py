"""Station IDs between songs (automation_config.station_ids): a short jingle
sprinkled after a song every min..max minutes, never close to a timed break,
never two spoken items together. See wiki/playlist-generation.md."""
import random
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.services.playlist_generator_service import (
    Clock,
    TimedItem,
    generate,
    id_settings_for,
)
from tests.test_playlist_generation import MONDAY_7AM, _setup

IDS = {"enabled": True, "min_minutes": 12, "max_minutes": 20}


def _jingle(n: int, category: str = "station_id") -> Asset:
    return Asset(
        id=uuid.uuid4(), title=f"Station ID {n}", duration=8.0, file_path=f"{uuid.uuid4().hex}.mp3",
        asset_type="jingle", category=category,
    )


async def _station_with_ids(db: AsyncSession, ids: dict | None, jingles: int = 6):
    station, songs, morning, evening = await _setup(db, {"station_ids": ids} if ids is not None else None)
    rows = [_jingle(n) for n in range(jingles)]
    db.add_all(rows)
    await db.commit()
    return station, rows


def test_settings_defaults_and_limits():
    assert id_settings_for(None).enabled is False
    s = id_settings_for({"station_ids": {"enabled": True, "min_minutes": 30, "max_minutes": 10}})
    assert s.enabled and s.min_minutes == 30 and s.max_minutes == 30  # max never under min
    s = id_settings_for({"station_ids": {"enabled": True, "min_minutes": "x", "category": "  "}})
    assert s.min_minutes == 12 and s.category == "station_id"
    assert id_settings_for({"station_ids": {"min_minutes": 0.5}}).min_minutes == 3


@pytest.mark.asyncio
async def test_off_by_default_no_ids(db_session: AsyncSession):
    station, _ = await _station_with_ids(db_session, None)
    plan = await generate(db_session, station, MONDAY_7AM, 3, artist_distance=0)
    assert all(p.asset_type == "music" for p in plan.picks)


@pytest.mark.asyncio
async def test_ids_are_spaced_rotated_and_only_after_songs(db_session: AsyncSession):
    random.seed(3)
    station, jingles = await _station_with_ids(db_session, IDS)
    plan = await generate(db_session, station, MONDAY_7AM, 4, artist_distance=0)
    ids = [p for p in plan.picks if p.asset_type == "jingle"]
    # four hours, one every 12 to 20 minutes after the song that passes the mark
    assert 10 <= len(ids) <= 21, len(ids)
    for a, b in zip(ids, ids[1:]):
        gap = (b.at - a.at).total_seconds() / 60
        assert 12 <= gap <= 20 + 4.5, gap  # plus at most one song
    # never two IDs together, never first
    for i, p in enumerate(plan.picks):
        if p.asset_type == "jingle":
            assert i > 0 and plan.picks[i - 1].asset_type == "music"
    # rotation: the six IDs are all used before any comes back
    first_six = [p.asset_id for p in ids[:6]]
    assert len(set(first_six)) == 6
    assert "Station ID" in {p.block_name for p in ids}


@pytest.mark.asyncio
async def test_ids_keep_clear_of_timed_breaks(db_session: AsyncSession):
    random.seed(5)
    station, _ = await _station_with_ids(db_session, {**IDS, "min_minutes": 5, "max_minutes": 6})
    # a break at every :00, :15, :30, :45 for three hours (60 s each)
    timed = [TimedItem(MONDAY_7AM + timedelta(minutes=15 * k), 60.0, k % 2 == 0) for k in range(1, 13)]
    plan = await generate(db_session, station, MONDAY_7AM, 3, artist_distance=0, timed=list(timed))
    ids = [p for p in plan.picks if p.asset_type == "jingle"]
    assert ids
    margin = timedelta(minutes=3)
    for p in ids:
        for t in timed:
            ends = t.at + timedelta(seconds=t.seconds)
            assert not (t.at - margin < p.at + timedelta(seconds=8) and p.at < ends + margin), (p.at, t.at)


@pytest.mark.asyncio
async def test_spacing_counts_from_the_last_id_already_queued(db_session: AsyncSession):
    random.seed(7)
    station, jingles = await _station_with_ids(db_session, IDS, jingles=2)
    # one ID waits at the end of the queue: the next one is at least 12 minutes later
    plan = await generate(
        db_session, station, MONDAY_7AM, 1, artist_distance=0,
        queued=[jingles[0].id], busy_ids={jingles[0].id},
    )
    first = next(p for p in plan.picks if p.asset_type == "jingle")
    assert first.at - MONDAY_7AM >= timedelta(minutes=12) - timedelta(seconds=240)
    assert first.asset_id == jingles[1].id  # the other one, not the one waiting


@pytest.mark.asyncio
async def test_no_id_right_after_a_spoken_item_or_a_break_before_the_fill(db_session: AsyncSession):
    random.seed(9)
    station, _ = await _station_with_ids(db_session, {**IDS, "min_minutes": 3, "max_minutes": 3})
    plan = await generate(db_session, station, MONDAY_7AM, 0.3, artist_distance=0,
                          after_type="jingle", timed_ended=MONDAY_7AM - timedelta(seconds=30))
    assert plan.picks[0].asset_type == "music"
    first = next((p for p in plan.picks if p.asset_type == "jingle"), None)
    assert first is None or first.at >= MONDAY_7AM + timedelta(seconds=150)


@pytest.mark.asyncio
async def test_on_with_no_jingles_says_so(db_session: AsyncSession):
    station, songs, *_ = await _setup(db_session, {"station_ids": IDS})
    plan = await generate(db_session, station, MONDAY_7AM, 1, artist_distance=0)
    assert all(p.asset_type == "music" for p in plan.picks)
    assert any("station_id" in n for n in plan.notes)


def test_clock_clear_of_timed():
    clock = Clock(MONDAY_7AM, [TimedItem(MONDAY_7AM + timedelta(minutes=10), 60.0, True)])
    m = timedelta(minutes=3)
    assert clock.clear_of_timed(MONDAY_7AM, 10, m)
    assert not clock.clear_of_timed(MONDAY_7AM + timedelta(minutes=7), 10, m)
    clock.at = MONDAY_7AM + timedelta(minutes=10)
    clock.start()
    assert not clock.clear_of_timed(clock.at, 10, m)  # right after the break (ends 11:00)
    assert clock.clear_of_timed(MONDAY_7AM + timedelta(minutes=14, seconds=1), 10, m)


@pytest.mark.asyncio
async def test_queue_fill_puts_ids_between_songs_and_a_top_up_keeps_the_spacing(
    db_session: AsyncSession, monkeypatch
):
    from datetime import datetime

    from sqlalchemy import select

    import app.services.queue_replenish_service as qrs
    from app.models.queue_entry import QueueEntry
    from app.services.queue_replenish_service import QueueReplenishService

    random.seed(11)
    station, jingles = await _station_with_ids(db_session, IDS)
    station.automation_config = {
        **station.automation_config, "playlist_generation": {"enabled": True, "hours": 2},
    }
    await db_session.commit()

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return MONDAY_7AM

    monkeypatch.setattr(qrs, "datetime", _Clock)
    await QueueReplenishService(db_session, station.id).replenish()
    jingle_ids = {j.id for j in jingles}

    async def queue():
        return (await db_session.execute(
            select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
            .order_by(QueueEntry.position)
        )).scalars().all()

    entries = await queue()
    marks = [i for i, e in enumerate(entries) if e.asset_id in jingle_ids]
    assert 4 <= len(marks) <= 11, marks
    assert all(entries[i].source == "auto" and entries[i].preempt_at is None for i in marks)
    assert all(b - a >= 3 for a, b in zip(marks, marks[1:]))  # 12+ minutes = 3+ songs
    assert marks[0] > 0 and all(entries[i - 1].asset_id not in jingle_ids for i in marks)

    # play 40 minutes off the top, then top up: the join keeps at least three songs between IDs
    for e in entries[:10]:
        e.status = "played"
    await db_session.commit()
    monkeypatch.setattr(qrs, "TOP_UP_SLACK_SECONDS", 0)
    await QueueReplenishService(db_session, station.id).replenish()
    entries = await queue()
    marks = [i for i, e in enumerate(entries) if e.asset_id in jingle_ids]
    assert all(b - a >= 3 for a, b in zip(marks, marks[1:])), marks
