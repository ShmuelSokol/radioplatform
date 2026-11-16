"""Up next is what follows the song on air, including a timed item due by its end.

Shmuel, 2026-11-16 12:28 PM: the :30 weather was due before the song ended, but
the control room showed the song after it as next.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station


@pytest.mark.asyncio
async def test_up_next_is_the_weather_due_before_the_song_ends(db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    db_session.add(station)
    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0, file_path="s", asset_type="music")
    after = Asset(id=uuid.uuid4(), title="Next song", duration=200.0, file_path="n", asset_type="music")
    weather = Asset(id=uuid.uuid4(), title="Weather", duration=40.0, file_path="w", asset_type="jingle")
    db_session.add_all([song, after, weather])
    await db_session.flush()
    now = datetime.now(UTC)
    w = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=3,
                   status="pending", preempt_at=now + timedelta(seconds=30), timing_mode="soft")
    n = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=after.id, position=2,
                   status="pending", source="auto")
    db_session.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1,
                   status="playing", source="auto", started_at=now - timedelta(seconds=140)),
        n, w,
    ])
    await db_session.commit()

    body = await queue_module._get_queue_impl(station.id, 50, db_session)
    assert body["next_entry_id"] == str(w.id)   # what really follows the song
    assert body["skip_entry_id"] == str(n.id)   # what Next plays now (weather not due yet)
