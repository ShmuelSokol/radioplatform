"""Timed items queued inside a blackout leave the queue.

KBR Jerusalem 2026-11-19: the 16:00 time check (Fri 14:00Z) was queued while candle
lighting was still 14:19Z; the window then moved to 13:57:14Z (40 minutes in Israel).
The engine is handed timed items ahead of time, so it would have aired on Shabbos.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.queue_entry import QueueEntry
from app.models.station import Station


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def no_replenish(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)


def _asset(db, title, kind, duration):
    a = Asset(id=uuid.uuid4(), title=title, duration=float(duration), asset_type=kind,
              file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3")
    db.add(a)
    return a


@pytest.mark.asyncio
async def test_a_time_check_inside_the_window_leaves_and_one_before_stays(db_session):
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    db_session.add(station)
    _asset(db_session, "Silence (5 min)", "silence", 300)
    time_check = _asset(db_session, "Time Announcement - 16:00 (Jerusalem)", "time", 5)
    before = _asset(db_session, "Time Announcement - 15:30 (Jerusalem)", "time", 5)
    shofar = _asset(db_session, "Shofar", "jingle", 20)
    song = _asset(db_session, "Song", "music", 200)
    await db_session.flush()
    now = datetime.now(UTC)
    start, end = now + timedelta(hours=2), now + timedelta(hours=27)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start,
                                 end_datetime=end, is_blackout=True, affected_stations=None))
    inside = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=time_check.id, position=5,
                        status="pending", source="auto", timing_mode="hard",
                        preempt_at=start + timedelta(minutes=3))
    earlier = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=before.id, position=3,
                         status="pending", source="auto", timing_mode="hard",
                         preempt_at=start - timedelta(minutes=27))
    at_lighting = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=shofar.id, position=4,
                             status="pending", source="shabbos_end", timing_mode="hard", preempt_at=start)
    music = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1,
                       status="pending", source="auto")
    db_session.add_all([inside, earlier, at_lighting, music])
    await db_session.commit()

    await queue_module.fill_blackout_queue(db_session, station.id)

    status = {
        e.id: e.status for e in (await db_session.execute(
            select(QueueEntry).where(QueueEntry.station_id == station.id)
            .execution_options(populate_existing=True))).scalars().all()
    }
    assert status[inside.id] == "skipped"
    assert status[earlier.id] == "pending"
    assert status[at_lighting.id] == "pending"
    assert status[music.id] == "pending"
    assert sum(1 for s in status.values() if s == "pending") > 300  # the silences
