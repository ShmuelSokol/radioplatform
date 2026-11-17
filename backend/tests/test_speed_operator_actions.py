"""Speed rows S-3 and S-4: every operator call and the reads the control room makes
answer well under 300 ms of server time with a 200-item queue and a 1,000-item library.

Measured in process (ASGI, SQLite test database), so this is server time without the
network. Each call is timed three times and the median is compared with the limit, so
one slow moment on a busy machine does not fail the run; a real regression (an extra
full-queue read, a synchronous refill) does. The live-site numbers come from
loadtests/operator_speed.py.
"""
import statistics
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station

LIMIT_S = 0.3
QUEUE_ITEMS = 200
LIBRARY_ITEMS = 1000


@pytest.fixture(autouse=True)
def _no_background_refill(monkeypatch):
    # The top-up after Next runs in the background on its own connection (not part of
    # the answer); in tests it would open the real database, so it is left out.
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)
    queue_module._fire_requests.clear()


async def _big_station(db):
    station = Station(id=uuid.uuid4(), name="Speed FM", timezone="America/New_York")
    db.add(station)
    assets = []
    for i in range(LIBRARY_ITEMS):
        a = Asset(id=uuid.uuid4(), title=f"Song {i:04d}", artist=f"Artist {i % 97}",
                  duration=180.0 + i % 60, asset_type="music",
                  file_path=f"https://cdn.example.com/{i}.mp3")
        assets.append(a)
    db.add_all(assets)
    await db.flush()
    now = datetime.now(UTC)
    rows = [QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=assets[0].id, position=1,
                       status="playing", source="auto", started_at=now - timedelta(seconds=30))]
    for i in range(1, QUEUE_ITEMS):
        rows.append(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=assets[i].id,
                               position=i + 1, status="pending", source="auto"))
    # a few hourly time checks in the queue, as on a real station
    for h in range(1, 6):
        rows.append(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=assets[900 + h].id,
                               position=QUEUE_ITEMS + h, status="pending", timing_mode="hard",
                               preempt_at=(now + timedelta(hours=h)).replace(minute=0, second=0)))
    db.add_all(rows)
    await db.commit()
    return station, assets


async def _pending(db, station_id, n=3):
    rows = (await db.execute(
        select(QueueEntry.id).where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position).limit(n)
    )).scalars().all()
    return [str(r) for r in rows]


async def _median_time(make_call, runs=3):
    times = []
    for _ in range(runs):
        method, url, kw = await make_call()
        started = time.perf_counter()
        res = await method(url, **kw)
        times.append(time.perf_counter() - started)
        assert res.status_code < 400, (url, res.status_code, res.text[:200])
    return statistics.median(times)


@pytest.mark.asyncio
async def test_operator_calls_answer_within_300_ms(client, auth_headers, db_session):
    """S-3: skip (each fade), fire-now, insert, loop, mix-over, move, remove, reorder"""
    station, assets = await _big_station(db_session)
    base = f"/api/v1/stations/{station.id}/queue"
    h = {"headers": auth_headers}
    pick = iter(assets[300:800])

    def post(url, json=None):
        async def make():
            return client.post, url, {**h, "json": json() if callable(json) else json}
        return make

    async def first_pending():
        return (await _pending(db_session, station.id, 1))[0]

    calls = {
        "skip hard": post(f"{base}/skip?fade=hard"),
        "skip fade": post(f"{base}/skip?fade=fade"),
        "skip soft": post(f"{base}/skip?fade=soft"),
        "fire-now": post(f"{base}/fire-now", lambda: {"asset_id": str(next(pick).id)}),
        "insert": post(f"{base}/insert", lambda: {"asset_ids": [str(next(pick).id), str(next(pick).id)]}),
        "mix-over": post(f"{base}/mix-over", lambda: {"asset_id": str(next(pick).id)}),
        "add": post(f"{base}/add", lambda: {"asset_id": str(next(pick).id)}),
        "play-next": post(f"{base}/play-next", lambda: {"asset_id": str(next(pick).id)}),
    }

    async def loop_toggle():
        loop_toggle.on = not getattr(loop_toggle, "on", False)
        return client.post, f"{base}/loop", {**h, "json": {"on": loop_toggle.on}}

    async def move(path):
        entry = (await _pending(db_session, station.id, 3))[1]
        return client.post, f"{base}/{path}", {**h, "json": {"entry_id": entry, "new_position": 0}}

    async def move_up():
        return await move("move-up")

    async def move_down():
        return await move("move-down")

    async def reorder():
        return client.put, f"{base}/reorder", {**h, "json": {"entry_id": await first_pending(), "new_position": 150}}

    async def reorder_dnd():
        return client.post, f"{base}/reorder-dnd", {**h, "json": {"entry_id": await first_pending(), "new_position": 50}}

    async def remove():
        entry = (await _pending(db_session, station.id, 3))[2]
        return client.delete, f"{base}/{entry}", h

    calls.update({"loop": loop_toggle, "move-up": move_up, "move-down": move_down,
                  "reorder": reorder, "reorder-dnd": reorder_dnd, "remove": remove})

    slow = {}
    for name, make in calls.items():
        took = await _median_time(make)
        if took >= LIMIT_S:
            slow[name] = round(took * 1000)
    assert not slow, f"over {LIMIT_S * 1000:.0f} ms (median ms): {slow}"


@pytest.mark.asyncio
async def test_reads_answer_within_300_ms(client, auth_headers, db_session):
    """S-4: queue, now-playing (control room and listener), library search"""
    station, assets = await _big_station(db_session)

    def get(url, auth=True):
        async def make():
            return client.get, url, ({"headers": auth_headers} if auth else {})
        return make

    calls = {
        "queue": get(f"/api/v1/stations/{station.id}/queue"),
        "queue log": get(f"/api/v1/stations/{station.id}/queue/log"),
        "last played": get(f"/api/v1/stations/{station.id}/queue/last-played"),
        "now-playing": get(f"/api/v1/now-playing/{station.id}", auth=False),
        "live-audio": get(f"/api/v1/stations/{station.id}/live-audio", auth=False),
        "library search": get("/api/v1/assets?search=Song%2004&limit=50"),
        "library page": get("/api/v1/assets?limit=50&skip=500"),
    }
    slow = {}
    for name, make in calls.items():
        took = await _median_time(make)
        if took >= LIMIT_S:
            slow[name] = round(took * 1000)
    assert not slow, f"over {LIMIT_S * 1000:.0f} ms (median ms): {slow}"


@pytest.mark.asyncio
async def test_remove_with_the_real_refill_stays_fast(client, auth_headers, db_session):
    """V1-44: DELETE /queue/{entry} runs the queue refill before answering; with a full
    queue (nothing to add) it must still be inside the S-3 limit"""
    station, assets = await _big_station(db_session)
    base = f"/api/v1/stations/{station.id}/queue"

    async def remove():
        entry = (await _pending(db_session, station.id, 3))[2]
        return client.delete, f"{base}/{entry}", {"headers": auth_headers}

    took = await _median_time(remove)
    assert took < LIMIT_S, f"remove took {took * 1000:.0f} ms"
