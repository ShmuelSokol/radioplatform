"""Saving a block does not start it early, and never cuts the song on the air
(live test on KBR Jerusalem: a block saved at 06:40 to start at 06:52 started its
playlist at once, and the song on the air was replaced 21 s in)."""
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.station import Station

JERUSALEM = ZoneInfo("Asia/Jerusalem")


@pytest.mark.asyncio
async def test_block_saved_before_its_start_waits_and_the_song_on_air_plays_on(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem test", timezone="Asia/Jerusalem", is_active=True)
    db_session.add(station)
    await db_session.flush()
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    mix = PlaylistTemplate(id=uuid.uuid4(), name="Lively then relax", station_id=station.id, is_active=True)
    db_session.add_all([schedule, mix])
    await db_session.flush()
    for i, category in enumerate(["lively", "relax"]):
        db_session.add(TemplateSlot(id=uuid.uuid4(), template_id=mix.id, position=i, asset_type="music", category=category))
    songs = [
        Asset(
            id=uuid.uuid4(), title=f"{c} {n}", artist=f"{c} artist {n}", duration=200.0,
            file_path=f"{uuid.uuid4().hex}.mp3", asset_type="music", category=c,
        )
        for c in ("other", "lively", "relax")
        for n in range(30)
    ]
    db_session.add_all(songs)
    await db_session.flush()
    now = datetime.now(UTC)
    on_air = QueueEntry(
        id=uuid.uuid4(), station_id=station.id, asset_id=songs[0].id, position=1, status="playing",
        source="auto", started_at=now - timedelta(seconds=21),
    )
    db_session.add(on_air)
    for i in range(1, 8):
        db_session.add(
            QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=songs[i].id, position=i + 1,
                       status="pending", source="auto")
        )
    station_id, on_air_id = station.id, on_air.id
    await db_session.commit()

    # Saved 12 minutes before it starts, "runs until the next playlist starts"
    start = (now + timedelta(minutes=12)).astimezone(JERUSALEM).replace(second=0, microsecond=0)
    start_utc = start.astimezone(UTC)
    r = await client.post(
        "/api/v1/schedules/blocks",
        json={
            "schedule_id": str(schedule.id), "name": "Morning", "recurrence_type": "daily",
            "start_time": start.strftime("%H:%M:00"), "end_time": start.strftime("%H:%M:00"),
            "until_next": True, "playlist_template_id": str(mix.id),
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(QueueEntry)
            .where(QueueEntry.station_id == station_id, QueueEntry.status.in_(["pending", "playing"]))
            .order_by(QueueEntry.position)
        )
    ).scalars().all()
    # The song on the air is still on the air, and still first: nothing new was
    # placed at or before it
    playing = [e for e in rows if e.status == "playing"]
    assert [e.id for e in playing] == [on_air_id]
    pending = [e for e in rows if e.status == "pending"]
    assert all(e.position > playing[0].position for e in pending)

    # The block's playlist waits for its start: what plays before then is not
    # from the block, and the block's first song starts at or after its time
    first = next(i for i, e in enumerate(pending) if e.block_id)
    assert first >= 3
    at = now - timedelta(seconds=21) + timedelta(seconds=197)  # on-air song ends (crossfade 3 s)
    for e in pending[:first]:
        assert at < start_utc
        at += timedelta(seconds=197)
    assert at >= start_utc

    # The next advance keeps the song on the air
    from app.api.v1.queue import _check_advance

    current = await _check_advance(db_session, station_id)
    assert current is not None and current.id == on_air_id


@pytest.mark.asyncio
async def test_start_only_block_has_not_started_before_it_was_made(db_session: AsyncSession):
    from datetime import time

    from app.models.schedule_block import RecurrenceType, ScheduleBlock
    from app.services.scheduling import SchedulingService

    station = Station(id=uuid.uuid4(), name="S", timezone="Asia/Jerusalem", is_active=True)
    made = datetime(2026, 11, 16, 6, 40, 15, tzinfo=UTC)
    block = ScheduleBlock(
        id=uuid.uuid4(), schedule_id=uuid.uuid4(), name="B", start_time=time(8, 52), end_time=time(8, 52),
        recurrence_type=RecurrenceType.DAILY, until_next=True, created_at=made,
    )
    svc = SchedulingService(db_session)
    local = lambda dt: dt.astimezone(JERUSALEM)  # noqa: E731
    # 08:40 Jerusalem, just after it was saved: not started (yesterday's 08:52 was before it existed)
    assert svc.last_start(block, local(made + timedelta(seconds=10)), station) is None
    # 08:52: started
    assert svc.last_start(block, local(datetime(2026, 11, 16, 6, 52, tzinfo=UTC)), station) is not None
    # The next day before 08:52, it is still the one that started yesterday
    assert svc.last_start(block, local(datetime(2026, 11, 17, 5, 0, tzinfo=UTC)), station) is not None
