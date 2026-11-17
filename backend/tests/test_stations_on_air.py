"""F-STLIST-5: the public station list's "On Air" pill follows whether something is
really on air. GET /now-playing answers, in one call, which stations have a queue item
playing (not long past its end) or a live show that is live."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.asset import Asset
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.queue_entry import QueueEntry
from app.models.station import Station


@pytest.mark.asyncio
async def test_on_air_lists_only_stations_with_something_playing(client, db_session):
    playing, idle, stale, live, paused = (
        Station(id=uuid.uuid4(), name=n, timezone="America/New_York")
        for n in ("Playing", "Idle", "Stale", "Live", "Pending only")
    )
    db_session.add_all([playing, idle, stale, live, paused])
    song = Asset(id=uuid.uuid4(), title="Song", duration=240.0, file_path="s", asset_type="music")
    no_len = Asset(id=uuid.uuid4(), title="No length", duration=None, file_path="n", asset_type="music")
    db_session.add_all([song, no_len])
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=playing.id, asset_id=song.id, position=1,
                   status="playing", source="auto", started_at=now - timedelta(seconds=30)),
        # A "playing" row an hour past its end: the player stopped, nothing is on air
        QueueEntry(id=uuid.uuid4(), station_id=stale.id, asset_id=song.id, position=1,
                   status="playing", source="auto", started_at=now - timedelta(hours=1)),
        # Only pending items, no live show: nothing on air (the live station is on air by its show)
        QueueEntry(id=uuid.uuid4(), station_id=live.id, asset_id=no_len.id, position=1,
                   status="pending", source="auto"),
        QueueEntry(id=uuid.uuid4(), station_id=paused.id, asset_id=song.id, position=1,
                   status="pending", source="auto"),
        LiveShow(id=uuid.uuid4(), station_id=live.id, title="Ask the Rav", status=LiveShowStatus.LIVE),
        LiveShow(id=uuid.uuid4(), station_id=idle.id, title="Later", status=LiveShowStatus.SCHEDULED),
    ])
    await db_session.commit()

    r = await client.get("/api/v1/now-playing")
    assert r.status_code == 200
    assert set(r.json()["playing"]) == {str(playing.id), str(live.id)}


@pytest.mark.asyncio
async def test_on_air_item_without_length_counts(client, db_session):
    st = Station(id=uuid.uuid4(), name="S", timezone="UTC")
    a = Asset(id=uuid.uuid4(), title="?", duration=None, file_path="x", asset_type="music")
    db_session.add_all([st, a])
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=a.id, position=1, status="playing",
                              source="auto", started_at=datetime.now(UTC) - timedelta(hours=5)))
    await db_session.commit()
    assert (await client.get("/api/v1/now-playing")).json()["playing"] == [str(st.id)]


@pytest.mark.asyncio
async def test_station_now_playing_route_still_works(client, db_session):
    st = Station(id=uuid.uuid4(), name="S", timezone="UTC")
    db_session.add(st)
    await db_session.commit()
    r = await client.get(f"/api/v1/now-playing/{st.id}")
    assert r.status_code == 200
