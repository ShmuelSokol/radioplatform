"""A block's playlist starts at the block's time, not early, and each station
announces its own time (live tests: a block started 39 s early; Jerusalem said
New York's 1 AM at 8 AM)."""
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock
from app.models.station import Station
from app.services.queue_replenish_service import QueueReplenishService

NY = ZoneInfo("America/New_York")
JERUSALEM = ZoneInfo("Asia/Jerusalem")
MONDAY_7AM = datetime(2026, 11, 16, 7, 0, tzinfo=NY).astimezone(UTC)


def _pin_clock(monkeypatch, at: datetime = MONDAY_7AM):
    import app.services.queue_replenish_service as qrs

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at

    monkeypatch.setattr(qrs, "datetime", _Clock)


def _song(category: str, n: int, cross_start: float = 225.0) -> Asset:
    # Four minutes long; the next song crossfades in 15 s before the end
    return Asset(
        id=uuid.uuid4(),
        title=f"{category} {n}",
        artist=f"{category} artist {n}",
        duration=240.0,
        file_path=f"{uuid.uuid4().hex}.mp3",
        asset_type="music",
        category=category,
        metadata_extra={"audio_analysis": {"cross_start_seconds": cross_start}},
    )


async def _station_with_block(db: AsyncSession, start: time, name: str):
    station = Station(id=uuid.uuid4(), name=name, timezone="America/New_York", is_active=True)
    db.add(station)
    await db.flush()
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    mix = PlaylistTemplate(id=uuid.uuid4(), name="Mix", station_id=station.id, is_active=True)
    db.add_all([schedule, mix])
    await db.flush()
    for i, category in enumerate(["Fast", "Relax"]):
        db.add(TemplateSlot(id=uuid.uuid4(), template_id=mix.id, position=i, asset_type="music", category=category))
    block = ScheduleBlock(
        id=uuid.uuid4(), schedule_id=schedule.id, name="Mix", start_time=start, end_time=time(18),
        recurrence_type=RecurrenceType.DAILY, playlist_template_id=mix.id,
    )
    db.add(block)
    songs = [_song(c, n) for c in ("Any", "Fast", "Relax") for n in range(30)]
    db.add_all(songs)
    await db.flush()
    return station, block, songs


async def _auto(db: AsyncSession, station_id) -> list[QueueEntry]:
    rows = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == station_id,
            QueueEntry.status == "pending",
            QueueEntry.source == "auto",
            QueueEntry.preempt_at.is_(None),
        )
        .order_by(QueueEntry.position)
    )
    return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_block_starts_after_its_time_allowing_for_crossfades_and_timed_items(
    db_session: AsyncSession, monkeypatch
):
    _pin_clock(monkeypatch)
    station, block, songs = await _station_with_block(db_session, time(7, 10), "On time 1")
    # A time check queued for 7:05 that fades the song on the air
    clip = Asset(
        id=uuid.uuid4(), title="Station ID", duration=10.0, file_path="id.mp3",
        asset_type="jingle", category="station_id",
    )
    db_session.add(clip)
    await db_session.flush()
    db_session.add(
        QueueEntry(
            id=uuid.uuid4(), station_id=station.id, asset_id=clip.id, position=1, status="pending",
            source="auto", preempt_at=MONDAY_7AM + timedelta(minutes=5), timing_mode="hard",
        )
    )
    station_id, block_id = station.id, str(block.id)
    category = {s.id: s.category for s in songs}
    await db_session.commit()

    await QueueReplenishService(db_session, station_id).replenish()
    await db_session.commit()

    auto = await _auto(db_session, station_id)
    # On the air: 7:00:00, 7:03:45 (faded at 7:05 for the 10 s clip), 7:05:10,
    # 7:08:55, then 7:12:40, the first start at or after 7:10. Counting full
    # lengths, or ignoring the clip, put the block's first song before 7:10.
    first = next(i for i, e in enumerate(auto) if e.block_id)
    assert first == 4
    assert all(e.block_id == block_id for e in auto[first : first + 6])
    assert [category[e.asset_id] for e in auto[first : first + 4]] == ["Fast", "Relax"] * 2


@pytest.mark.asyncio
async def test_queue_is_drawn_again_shortly_before_the_block(db_session: AsyncSession, monkeypatch):
    """Songs drawn hours earlier drift; 15 minutes before the block they are drawn
    again from the real time on air, and the playlist starts from its first step."""
    _pin_clock(monkeypatch)
    station, block, songs = await _station_with_block(db_session, time(7, 10), "On time 2")
    by_cat: dict[str, list[Asset]] = {}
    for s in songs:
        by_cat.setdefault(s.category, []).append(s)
    # Drawn earlier: two plain songs, then the block's first three steps (too early)
    earlier = [
        (by_cat["Any"][0], None), (by_cat["Any"][1], None),
        (by_cat["Fast"][0], str(block.id)), (by_cat["Relax"][0], str(block.id)),
        (by_cat["Fast"][1], str(block.id)),
    ]
    for i, (song, drawn_for) in enumerate(earlier):
        db_session.add(
            QueueEntry(
                id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=i + 1,
                status="pending", source="auto", block_id=drawn_for,
            )
        )
    station.automation_config = {"playlist_generation": {"cursor": {str(block.id): 3}}}
    station_id, block_id = station.id, str(block.id)
    category = {s.id: s.category for s in songs}
    await db_session.commit()
    old_ids = {e.id for e in await _auto(db_session, station_id)}

    await QueueReplenishService(db_session, station_id).replenish()
    await db_session.commit()
    auto = await _auto(db_session, station_id)
    assert not old_ids & {e.id for e in auto}
    # 7:00, 7:03:45, 7:07:30 (before 7:10), then 7:11:15: the block from step one
    first = next(i for i, e in enumerate(auto) if e.block_id)
    assert first == 3
    assert [category[e.asset_id] for e in auto[first : first + 4]] == ["Fast", "Relax"] * 2

    # Once is enough: the next pass leaves the queue alone
    ids = [e.id for e in auto]
    await QueueReplenishService(db_session, station_id).replenish()
    await db_session.commit()
    assert [e.id for e in await _auto(db_session, station_id)][: len(ids)] == ids
    assert all(e.block_id == block_id for e in auto[first:])


@pytest.mark.asyncio
async def test_each_station_announces_its_own_time(db_session: AsyncSession, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    now = datetime(2026, 11, 16, 5, 40, tzinfo=UTC)
    _pin_clock(monkeypatch, now)
    station = Station(
        id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem", is_active=True,
        automation_config={"hourly_time_announcement": True},
    )
    clips = {
        hour: Asset(
            id=uuid.uuid4(), title=f"Time Announcement - {hour:02d}:00", duration=6.0,
            file_path=f"time/{hour:02d}.mp3", asset_type="jingle", category="time_announcement",
            metadata_extra={"time_of_day": f"{hour:02d}"},
        )
        for hour in range(24)
    }
    db_session.add_all([station, *clips.values()])
    await db_session.flush()
    six = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)
    # Queued before the fix: New York's hour (1 AM) for 06:00 UTC, which is 8 AM in Jerusalem
    wrong = QueueEntry(
        id=uuid.uuid4(), station_id=station.id, asset_id=clips[1].id, position=1,
        status="pending", source="auto", preempt_at=six,
    )
    db_session.add(wrong)
    station_id, wrong_id = station.id, wrong.id
    hour_of = {a.id: h for h, a in clips.items()}
    await db_session.commit()

    await QueueReplenishService(db_session, station_id).replenish()
    await db_session.commit()

    rows = (
        await db_session.execute(
            select(QueueEntry)
            .join(Asset, QueueEntry.asset_id == Asset.id)
            .where(QueueEntry.station_id == station_id, Asset.category == "time_announcement")
        )
    ).scalars().all()
    assert wrong_id not in {e.id for e in rows}
    assert len(rows) >= 24
    for e in rows:
        at = e.preempt_at if e.preempt_at.tzinfo else e.preempt_at.replace(tzinfo=UTC)
        assert hour_of[e.asset_id] == at.astimezone(JERUSALEM).hour
    at_six = [e for e in rows if (e.preempt_at.replace(tzinfo=UTC) if not e.preempt_at.tzinfo else e.preempt_at) == six]
    assert [hour_of[e.asset_id] for e in at_six] == [8]
