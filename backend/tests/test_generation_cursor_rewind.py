"""Throwing away the drawn songs puts each block's playlist place back by as many steps.

Row: B-JOB-76."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.playlist_generator_service import CONFIG_KEY
from app.services.schedule_refresh_service import remove_automatic_songs


@pytest.mark.asyncio
async def test_removing_automatic_songs_rewinds_each_blocks_cursor(db_session):
    """B-JOB-76: removing the unplayed automatic songs moves each block's cursor back by the
    number of its songs removed; timed, manual, playing and other-block entries stay; a block
    with no removed songs keeps its place."""
    morning, evening, idle = (str(uuid.uuid4()) for _ in range(3))
    st = Station(id=uuid.uuid4(), name="Gen FM", timezone="UTC",
                 automation_config={CONFIG_KEY: {"enabled": True, "cursor": {morning: 10, evening: 4, idle: 7}}})
    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0, asset_type="music", file_path="s.mp3")
    db_session.add_all([st, song])
    await db_session.flush()

    def entry(pos, **kw):
        return QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, position=pos,
                          status=kw.pop("status", "pending"), source=kw.pop("source", "auto"), **kw)

    keep = [
        entry(1, status="playing", block_id=morning, started_at=datetime.now(UTC)),
        entry(2, source="manual", block_id=morning),
        entry(3, block_id=morning, preempt_at=datetime.now(UTC) + timedelta(hours=1)),
    ]
    drop = [entry(4, block_id=morning), entry(5, block_id=morning), entry(6, block_id=morning),
            entry(7, block_id=evening), entry(8)]
    db_session.add_all(keep + drop)
    await db_session.commit()

    removed = await remove_automatic_songs(db_session, st.id)
    await db_session.commit()
    assert removed == len(drop)
    left = set((await db_session.execute(
        select(QueueEntry.id).where(QueueEntry.station_id == st.id)
    )).scalars().all())
    assert left == {e.id for e in keep}
    await db_session.refresh(st)
    assert st.automation_config[CONFIG_KEY]["cursor"] == {morning: 7, evening: 3, idle: 7}
    assert st.automation_config[CONFIG_KEY]["enabled"] is True
