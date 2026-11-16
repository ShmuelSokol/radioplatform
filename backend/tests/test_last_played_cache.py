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
