"""A station can keep its built-in hourly time and weather beside its program blocks.

Shmuel, 2026-11-16: the Daf Yomi shiur (8 PM) and a schedule announcement after the
time and weather every hour go in program blocks, but any block used to switch the
station's hourly time and weather off. automation_config.blocks_keep_hourly keeps
them, and a block due on the hour plays right after them.
"""
import uuid
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
from app.services.program_block_service import blocks_keep_hourly, reconcile_block_entries
from app.services.queue_replenish_service import QueueReplenishService

ALL_HOURS = ",".join(f"{h:02d}:00" for h in range(24))


async def _station(db: AsyncSession, keep: bool):
    config = {"hourly_time_announcement": True, "weather_enabled": True, "weather_interval_minutes": 60}
    if keep:
        config["blocks_keep_hourly"] = True
    station = Station(id=uuid.uuid4(), name="KBR", timezone="UTC", is_active=True, automation_config=config)
    clock = Asset(id=uuid.uuid4(), title="Time Announcement - 15:00", duration=5.0, file_path="t.mp3",
                  asset_type="jingle", category="time_announcement")
    weather = Asset(id=uuid.uuid4(), title="Weather Report", duration=40.0, file_path="w.mp3",
                    asset_type="spot", category="weather_spot")
    sched = Asset(id=uuid.uuid4(), title="Schedule announcement", duration=8.0, file_path="s.mp3",
                  asset_type="announcement", category="block_only")
    db.add_all([station, clock, weather, sched])
    await db.flush()
    db.add(ProgramBlock(id=uuid.uuid4(), station_id=station.id, name="Schedule announcement",
                        times=ALL_HOURS, timing_mode="hard",
                        items=[{"kind": "asset", "asset_id": str(sched.id)}]))
    await db.flush()
    return station, clock, weather, sched


def test_setting_is_off_unless_set():
    assert blocks_keep_hourly(None) is False
    assert blocks_keep_hourly({}) is False
    assert blocks_keep_hourly({"blocks_keep_hourly": True}) is True


@pytest.mark.asyncio
@pytest.mark.parametrize("keep", [False, True])
async def test_blocks_leave_the_hourly_time_alone_only_when_kept(db_session: AsyncSession, keep: bool):
    station, clock, _, _ = await _station(db_session, keep)
    hour = (datetime.now(UTC) + timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    legacy = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clock.id, position=1,
                        status="pending", source="auto", preempt_at=hour)
    db_session.add(legacy)
    await db_session.commit()
    legacy_id = legacy.id

    await reconcile_block_entries(db_session, station.id)
    await db_session.commit()
    left = (await db_session.execute(select(QueueEntry).where(QueueEntry.id == legacy_id))).scalar_one_or_none()
    assert (left is not None) is keep


@pytest.mark.asyncio
async def test_kept_hour_plays_time_weather_then_the_block(db_session: AsyncSession, monkeypatch):
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    station, clock, weather, sched = await _station(db_session, keep=True)
    await db_session.commit()
    ids = {"clock": clock.id, "weather": weather.id, "sched": sched.id}

    with patch("app.services.weather_spot_service.get_or_create_weather_spot_assets",
               new=AsyncMock(return_value=(clock, weather))):
        svc = QueueReplenishService(db_session, station.id)
        svc.max_pos = 0
        await svc._load_automation_config()
        # the order replenish() uses for a station that keeps its hourly items
        await svc._schedule_hourly_announcements()
        await db_session.flush()
        await reconcile_block_entries(db_session, station.id)
        await db_session.commit()
        # a later refill does not skip hours that already have the block queued
        before = len((await db_session.execute(select(QueueEntry))).scalars().all())
        svc = QueueReplenishService(db_session, station.id)
        svc.max_pos = 1000
        await svc._load_automation_config()
        await svc._schedule_hourly_announcements()
        await db_session.commit()
        after = len((await db_session.execute(select(QueueEntry))).scalars().all())
    assert after == before  # nothing queued twice

    rows = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
    )).scalars().all()
    by_hour: dict = {}
    for e in rows:
        by_hour.setdefault(e.preempt_at, []).append(e.asset_id)
    # The block reaches one hour further than the hourly loop (48 h vs the next 47 tops of the
    # hour). Run in the last minutes before an hour, the loop has already left out the coming
    # hour (too close to queue) while the block still has it: a lone block at either end only.
    lone = [at for at, v in by_hour.items() if v == [ids["sched"]]]
    assert len(lone) <= 2 and all(at in (min(by_hour), max(by_hour)) for at in lone), lone
    full = [v for at, v in by_hour.items() if at not in lone]
    assert len(full) >= 46
    assert all(v == [ids["clock"], ids["weather"], ids["sched"]] for v in full), by_hour


@pytest.mark.asyncio
async def test_block_only_items_are_never_filler(db_session: AsyncSession):
    from app.models.asset import fit_for_random_pick

    a = Asset(id=uuid.uuid4(), title="Daf", duration=4000.0, file_path="d.mp3", asset_type="shiur",
              category="block_only")
    b = Asset(id=uuid.uuid4(), title="Other shiur", duration=900.0, file_path="o.mp3", asset_type="shiur",
              category="Torah")
    db_session.add_all([a, b])
    await db_session.commit()
    picked = (await db_session.execute(select(Asset.title).where(fit_for_random_pick()))).scalars().all()
    assert "Daf" not in picked and "Other shiur" in picked


@pytest.mark.asyncio
async def test_capabilities_say_blocks_can_keep_the_hourly_items(client, auth_headers):
    res = await client.get("/api/v1/program-blocks/capabilities", headers=auth_headers)
    assert res.status_code == 200 and res.json() == {"keep_hourly": True}
