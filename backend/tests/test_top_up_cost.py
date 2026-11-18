"""What the top-up after a song change costs.

A top-up ran about 44 database round trips even to add one song: 0.1 s on a
local database, 2 to 3 s from the server to the live one. It ran inside the
queue delete request (2.2 s for a plain delete, 2026-11-18) and inside the
playout engine's start report. Round trips are counted here, so the test does
not depend on the machine.
"""


import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.queue_entry import QueueEntry
from app.services import queue_replenish_service as replenish
from app.services.queue_replenish_service import QueueReplenishService
from tests.test_redraw_lock_time import Spans, _filled_station, _queue

# Round trips one top-up may make when the queue is nearly full (was 44)
TOP_UP_LIMIT = 28


async def _drop_first(db: AsyncSession, station_id, n: int = 1) -> None:
    for entry_id, _asset in (await _queue(db, station_id))[:n]:
        await db.execute(QueueEntry.__table__.delete().where(QueueEntry.id == entry_id))
    await db.commit()


@pytest.mark.asyncio
async def test_a_top_up_after_one_song_is_few_round_trips(db_session: AsyncSession):
    station_id = await _filled_station(db_session, "Cheap top-up")
    before = len(await _queue(db_session, station_id))
    await _drop_first(db_session, station_id)
    with Spans() as spans:
        await QueueReplenishService(db_session, station_id).replenish()
        await db_session.commit()
    assert spans.total <= TOP_UP_LIMIT, f"{spans.total} round trips for one top-up"
    # one song short of full: no new song is drawn yet
    assert len(await _queue(db_session, station_id)) == before - 1


@pytest.mark.asyncio
async def test_the_queue_is_filled_again_once_it_is_twenty_minutes_short(db_session: AsyncSession):
    station_id = await _filled_station(db_session, "Top-up slack")
    before = len(await _queue(db_session, station_id))
    short = int(replenish.TOP_UP_SLACK_SECONDS // 240) + 2  # four-minute songs
    await _drop_first(db_session, station_id, short)
    assert len(await _queue(db_session, station_id)) == before - short
    await QueueReplenishService(db_session, station_id).replenish()
    await db_session.commit()
    after = len(await _queue(db_session, station_id))
    assert after >= before - 1, (before, after)  # back to its full length


@pytest.mark.asyncio
async def test_a_short_queue_is_never_left_short(db_session: AsyncSession):
    """The slack is at most a quarter of the queue's length: a station that
    keeps one hour queued is topped up when 15 minutes are missing, not 20."""
    from app.models.station import Station

    station_id = await _filled_station(db_session, "Short queue")
    station = await db_session.get(Station, station_id)
    station.automation_config = {"playlist_generation": {"enabled": True, "hours": 1, "artist_distance": 2}}
    await db_session.commit()
    await db_session.execute(QueueEntry.__table__.delete().where(QueueEntry.station_id == station_id))
    await db_session.commit()
    await QueueReplenishService(db_session, station_id).replenish()
    await db_session.commit()
    full = len(await _queue(db_session, station_id))
    assert 14 <= full <= 17  # an hour of four-minute songs
    await _drop_first(db_session, station_id, 5)  # 20 minutes short
    await QueueReplenishService(db_session, station_id).replenish()
    await db_session.commit()
    assert len(await _queue(db_session, station_id)) >= full - 1


@pytest.mark.asyncio
async def test_a_queue_delete_answers_before_the_top_up(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    """The delete is saved and answered; the top-up follows in the background."""
    from app.api.v1 import queue as queue_api

    station_id = await _filled_station(db_session, "Delete first")
    target = (await _queue(db_session, station_id))[5][0]
    inside: list = []
    after: list = []

    async def inline(db, sid):
        inside.append(sid)

    def soon(sid, rebroadcast=False):
        after.append(sid)

    monkeypatch.setattr(queue_api, "_replenish_queue", inline)
    monkeypatch.setattr(queue_api, "_replenish_soon", soon)
    with Spans() as spans:
        response = await client.delete(f"/api/v1/stations/{station_id}/queue/{target}", headers=auth_headers)
    assert response.status_code == 204
    assert inside == [] and after == [station_id]
    # the delete, its checks and the activity record; with the top-up inside it was over 40
    assert spans.longest <= 12, f"{spans.longest} statements while the deleted row was locked"
    gone = await db_session.execute(select(func.count()).select_from(QueueEntry).where(QueueEntry.id == target))
    assert gone.scalar() == 0
