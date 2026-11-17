"""V2-109: song request daily caps count the station's own day, not the UTC day."""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.play_log import PlayLog, PlaySource
from app.models.station import Station
from app.services import song_request_service as srs

# 01:00 UTC on 15 Jan = 20:00 on 14 Jan in New York
NOW = datetime(2030, 1, 15, 1, 0, tzinfo=timezone.utc)


async def _world(db, tz):
    st = Station(id=uuid.uuid4(), name=f"S-{tz}", timezone=tz)
    song = Asset(id=uuid.uuid4(), title="Song", file_path="s.mp3", duration=180, asset_type="music",
                 metadata_extra={"auto_approve_requests": True, "max_requests_per_day": 1})
    db.add_all([st, song])
    await db.flush()
    # played at 17:00 New York time on 14 Jan (22:00 UTC): the same New York day as NOW
    db.add(PlayLog(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, source=PlaySource.SCHEDULER,
                   start_utc=datetime(2030, 1, 14, 22, 0, tzinfo=timezone.utc)))
    await db.commit()
    return st, song


@pytest.mark.asyncio
async def test_station_day_starts_at_local_midnight(db_session: AsyncSession):
    ny, _ = await _world(db_session, "America/New_York")
    start = await srs.station_day_start(db_session, str(ny.id), now=NOW)
    assert start == datetime(2030, 1, 14, 5, 0, tzinfo=timezone.utc)
    jlm = Station(id=uuid.uuid4(), name="J", timezone="Asia/Jerusalem")
    db_session.add(jlm)
    await db_session.commit()
    assert await srs.station_day_start(db_session, jlm.id, now=NOW) == datetime(2030, 1, 14, 22, 0, tzinfo=timezone.utc)
    # unknown station or zone: UTC midnight
    assert await srs.station_day_start(db_session, uuid.uuid4(), now=NOW) == datetime(2030, 1, 15, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_new_york_cap_does_not_reset_at_utc_midnight(db_session: AsyncSession, monkeypatch):
    """Fixed: the cap was counted from UTC midnight, so at 8 PM in New York the song's play
    from 5 PM no longer counted and a second request was auto-approved."""
    monkeypatch.setattr(srs, "_now", lambda: NOW)
    ny, song = await _world(db_session, "America/New_York")
    assert await srs.check_auto_approve(db_session, song, str(ny.id)) is False
    # the same play seen from a UTC station is yesterday's: the cap is free again
    utc, song2 = await _world(db_session, "UTC")
    assert await srs.check_auto_approve(db_session, song2, str(utc.id)) is True
