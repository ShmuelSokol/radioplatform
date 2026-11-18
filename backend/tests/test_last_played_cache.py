"""Last-played per item is read in full once, then only new plays are fetched."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.asset import Asset
from app.models.play_log import PlayLog, PlaySource
from app.models.station import Station
from app.services import last_played as lp


@pytest.mark.asyncio
async def test_new_plays_are_merged_and_old_ones_kept(db_session, monkeypatch):
    station = Station(id=uuid.uuid4(), name="Cache FM", timezone="America/New_York")
    a = Asset(id=uuid.uuid4(), title="A", duration=200.0, asset_type="music", file_path="a.mp3")
    b = Asset(id=uuid.uuid4(), title="B", duration=200.0, asset_type="music", file_path="b.mp3")
    db_session.add_all([station, a, b])
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=a.id,
                           start_utc=now - timedelta(days=3), source=PlaySource.SCHEDULER))
    await db_session.commit()

    first = await lp.last_played(db_session, station.id)
    assert set(first) == {str(a.id)}

    # Only plays since the last read are fetched from now on
    statements = []
    from sqlalchemy import event

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    engine = db_session.bind.sync_engine if hasattr(db_session.bind, "sync_engine") else db_session.bind
    event.listen(engine, "before_cursor_execute", capture)
    try:
        db_session.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=b.id,
                               start_utc=now, source=PlaySource.SCHEDULER))
        await db_session.commit()
        second = await lp.last_played(db_session, station.id)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert set(second) == {str(a.id), str(b.id)}  # the old play is still known
    assert any("start_utc >=" in s for s in statements if "GROUP BY" in s)

    # A caller changing its copy does not change the cache
    second.clear()
    assert len(await lp.last_played(db_session, station.id)) == 2


@pytest.mark.asyncio
async def test_the_hourly_full_read_does_not_hold_the_request(db_session, monkeypatch):
    """The full read of a station's play history took 7.35 s on the live database
    (2026-11-18, inside a Regenerate). Once an hour it was repeated inside
    whichever request asked first. Now that request gets what is known plus the
    new plays, and the full read runs in the background."""
    station = Station(id=uuid.uuid4(), name="Hourly FM", timezone="America/New_York")
    a = Asset(id=uuid.uuid4(), title="A", duration=200.0, asset_type="music", file_path="a.mp3")
    db_session.add_all([station, a])
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=a.id,
                           start_utc=now - timedelta(days=3), source=PlaySource.SCHEDULER))
    await db_session.commit()
    await lp.last_played(db_session, station.id)

    reloads = []

    async def reload(station_id):
        reloads.append(station_id)

    monkeypatch.setattr(lp, "_reload", reload)
    lp._cache[str(station.id)].full_at -= lp.FULL_RELOAD_SECONDS + 1  # an hour has passed

    statements = []
    from sqlalchemy import event

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    engine = db_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        again = await lp.last_played(db_session, station.id)
        await lp.last_played(db_session, station.id)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    import asyncio
    await asyncio.sleep(0)

    assert set(again) == {str(a.id)}
    grouped = [s for s in statements if "GROUP BY" in s]
    assert len(grouped) == 2 and all("start_utc >=" in s for s in grouped)  # never the whole history
    assert reloads == [station.id]  # one background re-read, not one per request


@pytest.mark.asyncio
async def test_a_background_reread_keeps_plays_merged_meanwhile(db_session, monkeypatch):
    station = Station(id=uuid.uuid4(), name="Reload FM", timezone="America/New_York")
    a = Asset(id=uuid.uuid4(), title="A", duration=200.0, asset_type="music", file_path="a.mp3")
    b = Asset(id=uuid.uuid4(), title="B", duration=200.0, asset_type="music", file_path="b.mp3")
    db_session.add_all([station, a, b])
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=a.id,
                           start_utc=now - timedelta(days=3), source=PlaySource.SCHEDULER))
    await db_session.commit()
    await lp.last_played(db_session, station.id)
    # a play the cache knows that the background read will not see (written after it began)
    just_now = datetime.now(UTC) + timedelta(seconds=5)
    lp._cache[str(station.id)].last[str(b.id)] = just_now

    class Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    import app.db.engine as db_engine
    monkeypatch.setattr(db_engine, "async_session_factory", Factory())
    await lp._reload(station.id)

    known = await lp.last_played(db_session, station.id)
    assert set(known) == {str(a.id), str(b.id)}
    assert known[str(b.id)] == just_now
