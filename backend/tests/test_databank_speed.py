"""The Databank's first page on a library of the real size.

Production, 2026-11-18: 9,046 items; GET /databank/items took 7 to 12 s and the Databank
(page and control room DB window) stayed on "Loading... Rec 0 Tot 0". Every page joined a
GROUP BY over the whole play history. Now "last played" comes from services/last_played,
which reads the history once per station per process and then only the new plays.
"""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, insert

from app.models.asset import Asset
from app.models.play_log import PlayLog, PlaySource
from app.models.station import Station

ITEMS = 9046
PLAYS = 60000
API = "/api/v1/databank/items"


@pytest.fixture
async def big_library(db_session):
    stations = [Station(id=uuid.uuid4(), name=f"Speed {i}", timezone="America/New_York") for i in range(3)]
    db_session.add_all(stations)
    ids = [uuid.uuid4() for _ in range(ITEMS)]
    await db_session.execute(insert(Asset), [
        {"id": i, "title": f"Song {n:05d}", "artist": f"Artist {n % 400}", "duration": 200.0,
         "file_path": f"assets/{n}.mp3", "asset_type": "music", "category": f"cat {n % 12}", "vote": n % 100}
        for n, i in enumerate(ids)
    ])
    now = datetime.now(timezone.utc)
    await db_session.execute(insert(PlayLog), [
        {"id": uuid.uuid4(), "station_id": stations[n % 3].id, "asset_id": ids[(n * 7) % (ITEMS - 500)],
         "start_utc": now - timedelta(minutes=n + 30), "source": PlaySource.SCHEDULER}
        for n in range(PLAYS)
    ])
    await db_session.commit()
    return stations, ids


@pytest.mark.asyncio
async def test_first_page_is_fast_on_the_real_library_size(client, auth_headers, db_session, big_library):
    stations, _ids = big_library
    # The first call of a process reads each station's history once; that is the scheduler's
    # job on the live site (it asks every few seconds), so it is not what a person waits for.
    assert (await client.get(API, params={"asset_type": "music", "limit": 1}, headers=auth_headers)).status_code == 200

    for params in (
        {"asset_type": "music", "sort": "title", "limit": 1},
        {"asset_type": "music", "sort": "artist", "limit": 50, "station_id": str(stations[0].id)},
        {"asset_type": "music", "sort": "last_played", "desc": "true", "limit": 200},
        {"asset_type": "music", "search": "Song 0412", "limit": 200},
    ):
        t = time.perf_counter()
        r = await client.get(API, params=params, headers=auth_headers)
        took = time.perf_counter() - t
        assert r.status_code == 200, r.text
        assert r.json()["total"] >= 1
        assert took < 1.0, f"{params} took {took:.2f} s"


@pytest.mark.asyncio
async def test_a_page_does_not_reread_the_whole_play_history(client, auth_headers, db_session, big_library):
    """What made it slow: no page may run an unbounded read of play_logs once the process is warm."""
    stations, _ids = big_library
    await client.get(API, params={"asset_type": "music", "limit": 1}, headers=auth_headers)

    seen: list[str] = []

    def note(_conn, _cur, statement, *_a):
        if "play_logs" in statement:
            seen.append(" ".join(statement.split()))

    engine = db_session.bind.sync_engine if hasattr(db_session.bind, "sync_engine") else db_session.bind
    event.listen(engine, "before_cursor_execute", note)
    try:
        for params in ({"asset_type": "music", "limit": 50},
                       {"asset_type": "music", "limit": 50, "station_id": str(stations[1].id)},
                       {"asset_type": "music", "sort": "last_played", "limit": 50}):
            assert (await client.get(API, params=params, headers=auth_headers)).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", note)
    assert seen, "expected the small 'plays since the last read' query"
    unbounded = [s for s in seen if "start_utc >=" not in s]
    assert not unbounded, unbounded[:2]


@pytest.mark.asyncio
async def test_last_played_order_and_values_match_the_history(client, auth_headers, db_session, big_library):
    stations, ids = big_library
    r = await client.get(API, params={"asset_type": "music", "sort": "last_played", "desc": "true", "limit": 5},
                         headers=auth_headers)
    items = r.json()["items"]
    times = [i["last_played"] for i in items]
    assert all(times) and times == sorted(times, reverse=True)
    # newest play in the fixture is n=0: item (0*7) % ... = ids[0]
    assert items[0]["id"] == str(ids[0])
    # oldest first, and never-played items come after every played one
    r = await client.get(API, params={"asset_type": "music", "sort": "last_played", "limit": 500, "skip": ITEMS - 500},
                         headers=auth_headers)
    assert all(i["last_played"] is None for i in r.json()["items"])
    # one station only: its own times
    one = await client.get(API, params={"asset_type": "music", "sort": "last_played", "desc": "true", "limit": 1,
                                        "station_id": str(stations[1].id)}, headers=auth_headers)
    assert one.json()["items"][0]["id"] == str(ids[7])   # n=1 is station 1's newest play
