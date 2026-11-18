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
    reads = [s for s in statements if s.lstrip().startswith("SELECT") and "play_logs" in s]
    assert reads and all("start_utc >=" in s and "GROUP BY" not in s for s in reads)

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
    lp._cache[str(station.id)].full_at -= lp.FULL_RELOAD_SECONDS + 1  # a day has passed

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
    reads = [s for s in statements if "play_logs" in s]
    # never the whole history, and no GROUP BY (it can make the planner walk it all)
    assert len(reads) == 2 and all("start_utc >=" in s and "GROUP BY" not in s for s in reads)
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


class _Sessions:
    """Stands in for the app's session factory: hands out the test session."""

    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_the_first_read_is_bounded_and_the_rest_arrives_in_the_background(db_session, monkeypatch):
    """A queue refill at 09:00Z waited 16.4 s for the whole-history read of one
    station (2026-11-18). As the site runs, the first read covers the last
    FIRST_READ_DAYS only; older plays come from the background read."""
    import asyncio

    from sqlalchemy import event

    import app.db.engine as db_engine

    monkeypatch.setattr(lp, "BACKGROUND_FULL_READ", True)
    monkeypatch.setattr(db_engine, "async_session_factory", _Sessions(db_session))
    station = Station(id=uuid.uuid4(), name="Bounded FM", timezone="America/New_York")
    recent = Asset(id=uuid.uuid4(), title="Recent", duration=200.0, asset_type="music", file_path="r.mp3")
    old = Asset(id=uuid.uuid4(), title="Old", duration=200.0, asset_type="music", file_path="o.mp3")
    db_session.add_all([station, recent, old])
    await db_session.flush()
    now = datetime.now(UTC)
    long_ago = now - timedelta(days=lp.FIRST_READ_DAYS + 60)
    for asset, when in ((recent, now - timedelta(days=2)), (recent, now - timedelta(days=9)), (old, long_ago)):
        db_session.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id,
                               start_utc=when, source=PlaySource.SCHEDULER))
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=None,
                           start_utc=now, source=PlaySource.SCHEDULER))
    await db_session.commit()

    statements = []

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    engine = db_session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        first = await lp.last_played(db_session, station.id)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    reads = [s for s in statements if "play_logs" in s]
    assert len(reads) == 1 and "start_utc >=" in reads[0] and "GROUP BY" not in reads[0]
    assert set(first) == {str(recent.id)}  # the newest play of the recent song; no "None" key
    assert abs((first[str(recent.id)] - (now - timedelta(days=2))).total_seconds()) < 1

    for _ in range(50):  # the background read, started by the first call
        if not lp._tasks:
            break
        await asyncio.sleep(0.01)
    known = await lp.last_played(db_session, station.id)
    assert set(known) == {str(recent.id), str(old.id)}
    assert abs((known[str(old.id)] - long_ago).total_seconds()) < 1
    # and it is not started again by the next calls
    started = []

    async def reload(station_id):
        started.append(station_id)

    monkeypatch.setattr(lp, "_reload", reload)
    await lp.last_played(db_session, station.id)
    assert started == []


@pytest.mark.asyncio
async def test_whole_history_reads_take_turns(db_session, monkeypatch):
    """Three stations starting together must not run three long reads side by side."""
    import asyncio

    import app.db.engine as db_engine

    running = 0
    most = 0

    class Slow(_Sessions):
        async def __aenter__(self):
            nonlocal running, most
            running += 1
            most = max(most, running)
            await asyncio.sleep(0.02)
            return self.session

        async def __aexit__(self, *exc):
            nonlocal running
            running -= 1
            return False

    monkeypatch.setattr(db_engine, "async_session_factory", Slow(db_session))
    stations = [Station(id=uuid.uuid4(), name=f"Turn {n}", timezone="America/New_York") for n in range(3)]
    db_session.add_all(stations)
    await db_session.commit()
    ids = [s.id for s in stations]
    await asyncio.gather(*(lp._reload(i) for i in ids))
    assert most == 1
    assert all(str(i) in lp._cache for i in ids)
