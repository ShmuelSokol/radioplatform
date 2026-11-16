"""A deploy's cold start must stay light.

The restart closed open play log rows with an unbounded "end_utc IS NULL" read of
all play history (6.5 s per container), and simultaneous requests from one user
each updated users.last_seen_at and waited on each other.
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.asset import Asset
from app.models.play_log import PlayLog, PlaySource
from app.models.station import Station


@pytest.mark.asyncio
async def test_restart_closes_only_recent_open_logs(db_session, monkeypatch):
    import app.api.v1.queue as queue_module
    import app.db.engine as db_engine
    from app.main import _resume_playback_on_startup
    from tests.conftest import TestSessionLocal

    station = Station(id=uuid.uuid4(), name="Cold FM", timezone="America/New_York")
    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0, asset_type="music",
                 file_path="https://cdn.example.com/song.mp3")
    db_session.add_all([station, song])
    await db_session.flush()
    now = datetime.now(UTC)
    recent = PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=song.id,
                     start_utc=now - timedelta(minutes=5), source=PlaySource.SCHEDULER)
    ancient = PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=song.id,
                      start_utc=now - timedelta(days=30), source=PlaySource.SCHEDULER)
    db_session.add_all([recent, ancient])
    await db_session.commit()

    async def no_replenish(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(db_engine, "async_session_factory", TestSessionLocal)
    monkeypatch.setattr(
        "app.services.queue_replenish_service.QueueReplenishService.replenish",
        lambda self: no_replenish(),
    )
    await _resume_playback_on_startup()

    rows = dict((await db_session.execute(
        select(PlayLog.id, PlayLog.end_utc).execution_options(populate_existing=True)
    )).all())
    assert rows[recent.id] is not None
    assert rows[ancient.id] is None  # outside the window: not read at all


@pytest.mark.asyncio
async def test_last_seen_written_at_most_once_a_minute(client, auth_headers, admin_user, db_session, monkeypatch):
    from app.core import dependencies

    monkeypatch.setattr(dependencies, "_last_seen_written", {})
    admin_user.last_seen_at = datetime.now(UTC) - timedelta(minutes=10)
    await db_session.commit()
    for _ in range(3):
        resp = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert resp.status_code == 200
    assert list(dependencies._last_seen_written) == [admin_user.id]
    first = dependencies._last_seen_written[admin_user.id]

    # Even with the stored time old, this process does not write again within the minute
    admin_user.last_seen_at = datetime.now(UTC) - timedelta(minutes=10)
    await db_session.commit()
    await client.get("/api/v1/auth/me", headers=auth_headers)
    assert dependencies._last_seen_written[admin_user.id] == first
