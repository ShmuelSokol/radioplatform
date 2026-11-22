"""Main, 11:00 AM ET on 2026-11-22: the time and the weather aired twice. Main keeps the
built-in hourly time and weather beside its blocks (blocks_keep_hourly) and its hourly
block had been given its own time and weather items. And when the half-hour time check
was switched off, two days of :30 time checks already queued stayed and aired.

These run the same order replenish() uses and look at what would air each minute."""
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.asset import Asset
from app.models.program_block import ProgramBlock
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.program_block_service import reconcile_block_entries
from app.services.queue_replenish_service import QueueReplenishService

ALL_HOURS = ",".join(f"{h:02d}:00" for h in range(24))


async def _setup(db: AsyncSession, keep: bool, **config):
    cfg = {"hourly_time_announcement": True, "weather_enabled": True, "weather_interval_minutes": 60, **config}
    if keep:
        cfg["blocks_keep_hourly"] = True
    station = Station(id=uuid.uuid4(), name="Main", timezone="UTC", is_active=True, automation_config=cfg)
    clock = Asset(id=uuid.uuid4(), title="Time Announcement", duration=5.0, file_path="t.mp3",
                  asset_type="jingle", category="time_announcement")
    weather = Asset(id=uuid.uuid4(), title="Weather Report", duration=40.0, file_path="w.mp3",
                    asset_type="spot", category="weather_spot")
    sched = Asset(id=uuid.uuid4(), title="Schedule announcement", duration=8.0, file_path="s.mp3",
                  asset_type="announcement", category="block_only")
    db.add_all([station, clock, weather, sched])
    await db.flush()
    db.add(ProgramBlock(id=uuid.uuid4(), station_id=station.id, name="Hourly schedule announcement",
                        times=ALL_HOURS, timing_mode="hard",
                        items=[{"kind": "time"}, {"kind": "weather"},
                               {"kind": "asset", "asset_id": str(sched.id)}]))
    await db.commit()
    return station, clock, weather, sched


async def _top_up(db: AsyncSession, station, clock, weather, block_first=False):
    """What replenish() does for the hour: built-in hourly (when kept), then blocks."""
    with patch("app.services.weather_spot_service.get_or_create_weather_spot_assets",
               new=AsyncMock(return_value=(clock, weather))), \
         patch("app.services.weather_spot_service.find_time_clip_asset", new=AsyncMock(return_value=clock)), \
         patch("app.services.weather_spot_service.find_weather_spot_asset", new=AsyncMock(return_value=weather)):
        svc = QueueReplenishService(db, station.id)
        svc.max_pos = 0
        await svc._load_automation_config()
        if block_first:
            await reconcile_block_entries(db, station.id)
            await db.flush()
        if svc.automation_config.get("blocks_keep_hourly"):
            await svc._schedule_hourly_announcements()
            await db.flush()
        await reconcile_block_entries(db, station.id)
        await db.commit()


async def _per_minute(db: AsyncSession, station):
    rows = (await db.execute(
        select(QueueEntry.preempt_at, Asset.category)
        .join(Asset, Asset.id == QueueEntry.asset_id)
        .where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
    )).all()
    out: dict = {}
    for at, category in rows:
        out.setdefault(at.replace(second=0, microsecond=0), Counter())[category] += 1
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("keep", [True, False])
@pytest.mark.parametrize("block_first", [False, True])
async def test_one_time_check_and_one_weather_each_hour(db_session, monkeypatch, keep, block_first):
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    station, clock, weather, _ = await _setup(db_session, keep)
    await _top_up(db_session, station, clock, weather, block_first=block_first)
    await _top_up(db_session, station, clock, weather)  # a later top-up changes nothing
    minutes = await _per_minute(db_session, station)
    assert len(minutes) >= 46
    for at, seen in minutes.items():
        assert seen["time_announcement"] <= 1 and seen["weather_spot"] <= 1, (at, seen)
    # and the hours do get them
    assert sum(1 for seen in minutes.values() if seen["time_announcement"] == 1) >= 46


@pytest.mark.asyncio
async def test_switching_blocks_keep_hourly_off_removes_the_built_in_pair(db_session, monkeypatch):
    """The purge matched only untimed entries, so the built-in pair (hard) stayed."""
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    station, clock, weather, _ = await _setup(db_session, keep=True)
    await _top_up(db_session, station, clock, weather)
    station.automation_config = {**station.automation_config, "blocks_keep_hourly": False}
    await db_session.commit()
    await _top_up(db_session, station, clock, weather)
    auto = (await db_session.execute(
        select(QueueEntry).join(Asset, Asset.id == QueueEntry.asset_id)
        .where(QueueEntry.station_id == station.id, QueueEntry.source == "auto",
               QueueEntry.preempt_at > datetime.now(UTC) + timedelta(minutes=3),
               Asset.category.in_(("time_announcement", "weather_spot")))
    )).scalars().all()
    assert auto == []
    minutes = await _per_minute(db_session, station)
    assert sum(1 for seen in minutes.values() if seen["time_announcement"] == 1) >= 46


@pytest.mark.asyncio
async def test_half_hour_time_check_off_clears_the_queued_ones(db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", is_active=True,
                      automation_config={"weather_enabled": True, "weather_interval_minutes": 60})
    clock = Asset(id=uuid.uuid4(), title="The time is 10:30", duration=5.0, file_path="t.mp3",
                  asset_type="jingle", category="time_announcement")
    weather = Asset(id=uuid.uuid4(), title="Weather", duration=40.0, file_path="w.mp3",
                    asset_type="spot", category="weather_spot")
    db_session.add_all([station, clock, weather])
    base = (datetime.now(UTC) + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    keep, gone = [], []
    pos = 0
    for h in range(4):
        for minute, asset in ((0, clock), (0, weather), (30, clock), (30, weather)):
            pos += 1
            e = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id, position=pos,
                           status="pending", source="auto", timing_mode="hard",
                           preempt_at=base + timedelta(hours=h, minutes=minute))
            db_session.add(e)
            (gone if minute == 30 else keep).append(e.id)
    await db_session.commit()

    svc = QueueReplenishService(db_session, station.id)
    await svc._load_automation_config()
    assert await svc._drop_unwanted_half_hours() == len(gone)
    await db_session.commit()
    left = set((await db_session.execute(select(QueueEntry.id))).scalars().all())
    assert left == set(keep)

    # with both wanted, nothing is taken
    station.automation_config = {"weather_enabled": True, "weather_interval_minutes": 30,
                                 "half_hour_time_check": True}
    await db_session.commit()
    svc = QueueReplenishService(db_session, station.id)
    await svc._load_automation_config()
    assert await svc._drop_unwanted_half_hours() == 0
