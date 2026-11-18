"""Song request auto-approval (B-SONGREQUES-3): a matched song whose asset allows it
is queued at once while the song has aired or is lined up fewer than
max_requests_per_day times today; past that the request waits for staff."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.song_request import SongRequest
from app.models.station import Station, StationType

API = "/api/v1/song-requests"


@pytest.fixture(autouse=True)
def _no_rate_limit():
    from app.api.v1.song_requests import limiter

    limiter.reset()
    yield
    limiter.reset()


async def _setup(db, **extra):
    st = Station(id=uuid.uuid4(), name="KBR", type=StationType.INTERNET)
    song = Asset(id=uuid.uuid4(), title="Am Yisrael Chai", artist="Shlomo", file_path="a.mp3",
                 asset_type="music", duration=200, metadata_extra=extra or None)
    db.add_all([st, song])
    await db.commit()
    return st, song


async def _ask(client, st):
    r = await client.post(API, json={"station_id": str(st.id), "requester_name": "Dovid",
                                     "song_title": "Am Yisrael Chai", "song_artist": "Shlomo"})
    assert r.status_code == 201, r.text
    return r.json()


async def _queued(db, st, song):
    return (await db.execute(select(func.count(QueueEntry.id)).where(
        QueueEntry.station_id == st.id, QueueEntry.asset_id == song.id))).scalar()


@pytest.mark.asyncio
async def test_auto_approve_up_to_the_daily_limit_then_pending(client, db_session):
    st, song = await _setup(db_session, auto_approve_requests=True, max_requests_per_day=3)
    # Three requests fit a limit of three (each used to count twice: once as the
    # request, once as its queue entry, so only two got through)
    for i in range(3):
        body = await _ask(client, st)
        assert body["auto_approved"] is True and body["status"] == "queued", (i, body)
    assert await _queued(db_session, st, song) == 3
    body = await _ask(client, st)
    assert body["auto_approved"] is False and body["status"] == "pending"
    assert body["asset_id"] == str(song.id)  # matched, waiting for staff
    assert await _queued(db_session, st, song) == 3
    statuses = sorted(r.status.value for r in (await db_session.execute(select(SongRequest))).scalars())
    assert statuses == ["pending", "queued", "queued", "queued"]


@pytest.mark.asyncio
async def test_plays_today_count_toward_the_limit(client, db_session):
    st, song = await _setup(db_session, auto_approve_requests=True, max_requests_per_day=2)
    now = datetime.now(UTC)
    start = max(now - timedelta(minutes=10), now.replace(hour=0, minute=0, second=0, microsecond=0))
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=st.id, asset_id=song.id,
                           start_utc=start, end_utc=start + timedelta(minutes=3)))
    # Yesterday's play does not count
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=st.id, asset_id=song.id,
                           start_utc=now - timedelta(days=2), end_utc=now - timedelta(days=2) + timedelta(minutes=3)))
    await db_session.commit()
    assert (await _ask(client, st))["status"] == "queued"   # 1 play + 0 lined up < 2
    assert (await _ask(client, st))["status"] == "pending"  # 1 play + 1 lined up = 2


@pytest.mark.asyncio
async def test_no_auto_approve_unless_the_asset_allows_it(client, db_session):
    st, song = await _setup(db_session)  # the default is off
    body = await _ask(client, st)
    assert body["auto_approved"] is False and body["status"] == "pending"
    assert await _queued(db_session, st, song) == 0


@pytest.mark.asyncio
async def test_default_limit_is_three(client, db_session):
    st, song = await _setup(db_session, auto_approve_requests=True)
    assert [(await _ask(client, st))["status"] for _ in range(4)] == ["queued", "queued", "queued", "pending"]
