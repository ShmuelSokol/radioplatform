"""F-BLOCKS-5b: schedule block and "+ Add song" saves answer at once.

The live check of PR 169 found "+ Add song" taking 6 s, removing a song 20 s and
deleting a block 14 s, because each save waited for the queue rebuild. Now they
answer straight away and the rebuild runs in the background (one per station),
as playlist edits already did.
"""
import asyncio
import time as _time
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1 import playlists as pl
from app.models.asset import Asset
from app.models.schedule import Schedule
from app.models.station import Station

SLOW = 2.0  # a rebuild that takes this long must not hold any save


@pytest.mark.asyncio
async def test_block_and_song_saves_do_not_wait_for_the_rebuild(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    import app.services.schedule_refresh_service as srs

    rebuilds: list[tuple[str, bool]] = []

    async def slow_refresh(db, station_id, *, had_playlists=False, **kw):
        await asyncio.sleep(SLOW)
        rebuilds.append((str(station_id), had_playlists))

    class _Session:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(srs, "refresh_station", slow_refresh)
    monkeypatch.setattr("app.db.engine.async_session_factory", lambda: _Session())
    monkeypatch.setattr(pl, "REFRESH_INLINE", False)
    monkeypatch.setattr(pl, "REFRESH_DELAY", 3.0)  # the whole burst shares one rebuild

    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem", is_active=True)
    db_session.add(station)
    await db_session.flush()
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    song = Asset(
        id=uuid.uuid4(), title="Song", artist="A", duration=200.0,
        file_path="s.mp3", asset_type="music", category="Music",
    )
    db_session.add_all([schedule, song])
    station_id, schedule_id, song_id = str(station.id), str(schedule.id), str(song.id)
    await db_session.commit()

    async def timed(call):
        t0 = _time.monotonic()
        r = await call
        took = _time.monotonic() - t0
        assert took < 1.0, f"save took {took:.2f}s"
        return r

    r = await timed(client.post(
        "/api/v1/schedules/blocks",
        json={"schedule_id": schedule_id, "name": "Morning", "start_time": "06:00:00",
              "end_time": "09:00:00", "recurrence_type": "daily"},
        headers=auth_headers,
    ))
    assert r.status_code == 201, r.text
    block_id = r.json()["id"]

    r = await timed(client.post(
        "/api/v1/schedules/playlist-entries",
        json={"block_id": block_id, "asset_id": song_id, "position": 0},
        headers=auth_headers,
    ))
    assert r.status_code == 201, r.text
    entry_id = r.json()["id"]

    r = await timed(client.delete(f"/api/v1/schedules/playlist-entries/{entry_id}", headers=auth_headers))
    assert r.status_code == 204, r.text
    r = await timed(client.delete(f"/api/v1/schedules/blocks/{block_id}", headers=auth_headers))
    assert r.status_code == 204, r.text
    r = await timed(client.delete(f"/api/v1/schedules/{schedule_id}", headers=auth_headers))
    assert r.status_code == 204, r.text

    # The rebuild still happens, once for the burst of edits, and remembers that the
    # station had block songs before the edit (so their queued songs get replaced).
    for _ in range(200):
        if rebuilds and not pl._refresh_tasks:
            break
        await asyncio.sleep(0.05)
    assert rebuilds, "the queue was never rebuilt"
    assert all(sid == station_id for sid, _ in rebuilds)
    assert len(rebuilds) == 1, rebuilds
    assert any(had for _, had in rebuilds), rebuilds


@pytest.mark.asyncio
async def test_old_songs_stay_on_the_air_until_the_new_ones_are_written(db_session: AsyncSession, monkeypatch):
    """F-BLOCKS-5c: the queue never runs out of songs while it is redrawn.

    Live on Jerusalem the rebuild deleted the old automatic songs and committed at
    once, then took 20-60 s to write new ones: the queue had no songs in between.
    Another connection (the player) must see the old songs until the new ones are
    committed with their removal, and a failed redraw must leave the old ones."""
    import uuid as _uuid

    from sqlalchemy import func, select

    from app.models.queue_entry import QueueEntry
    from app.services import queue_replenish_service as qrs_mod
    from app.services.schedule_refresh_service import refresh_station
    from tests.conftest import TestSessionLocal
    from tests.test_block_songs_reach_queue import _block, _pin_clock, _song, _station

    _pin_clock(monkeypatch)
    station, schedule, filler = await _station(db_session)
    only = _song("The one song")
    db_session.add(only)
    await db_session.flush()
    for i in range(40):
        db_session.add(QueueEntry(
            id=_uuid.uuid4(), station_id=station.id, asset_id=filler[i % 30].id,
            position=i + 1, status="pending", source="auto",
        ))
    await _block(db_session, schedule, [only])
    station_id = station.id
    await db_session.commit()

    async def player_sees() -> int:
        async with TestSessionLocal() as other:
            return (await other.execute(
                select(func.count()).select_from(QueueEntry).where(
                    QueueEntry.station_id == station_id, QueueEntry.status == "pending",
                    QueueEntry.source == "auto",
                )
            )).scalar()

    seen_mid_redraw: list[int] = []
    real_fill = qrs_mod.QueueReplenishService._fill_from_playlists

    async def watched_fill(self, *a, **kw):
        seen_mid_redraw.append(await player_sees())
        done = await real_fill(self, *a, **kw)
        seen_mid_redraw.append(await player_sees())  # the swap: old out and new in together
        return done

    monkeypatch.setattr(qrs_mod.QueueReplenishService, "_fill_from_playlists", watched_fill)
    removed = await refresh_station(db_session, station_id, had_playlists=True)
    assert removed == 39  # all but the next song
    # While the new songs are picked the player still sees all the old ones; the
    # fill then swaps old for new in one short transaction of its own
    assert seen_mid_redraw[0] == 40 and seen_mid_redraw[1] > 40, seen_mid_redraw
    titles = await _titles(db_session, station_id)
    assert set(titles[1:]) == {"The one song"}, titles[:5]

    # A redraw that fails part way changes nothing: the songs written stay
    async def broken_fill(self, *a, **kw):
        await real_fill(self, *a, **kw)
        raise RuntimeError("database went away")

    monkeypatch.setattr(qrs_mod.QueueReplenishService, "_fill_from_playlists", broken_fill)
    before = await _titles(db_session, station_id)
    assert await refresh_station(db_session, station_id, had_playlists=True) == 0
    assert await _titles(db_session, station_id) == before


async def _titles(db: AsyncSession, station_id) -> list[str]:
    from tests.test_block_songs_reach_queue import _pending_titles

    db.expire_all()
    return await _pending_titles(db, station_id)
