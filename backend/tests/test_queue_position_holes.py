"""Since PR 301 a start report no longer renumbers the queue: the item on air moves to just
before the first waiting one, so positions climb and have holes (9, 10, 13, 17, 30).
Every operator action that places or moves an entry must still give the order a person
asked for, with no two waiting entries on the same position."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda station_id: None)
    queue_module._fire_requests.clear()


async def _station_with_holes(db_session):
    station = Station(id=uuid.uuid4(), name="Holes FM", timezone="America/New_York")
    db_session.add(station)
    assets = {}
    for name in ["ON", "A", "B", "C", "D", "X", "Y"]:
        a = Asset(id=uuid.uuid4(), title=name, duration=180.0,
                  file_path=f"https://cdn.example.com/{name}.mp3", asset_type="music")
        db_session.add(a)
        assets[name] = a
    await db_session.flush()
    db_session.add(QueueEntry(
        id=uuid.uuid4(), station_id=station.id, asset_id=assets["ON"].id, position=9,
        status="playing", source="auto", started_at=datetime.now(UTC) - timedelta(seconds=20),
    ))
    entries = {}
    for name, pos in [("A", 10), ("B", 13), ("C", 17), ("D", 30)]:
        e = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=assets[name].id,
                       position=pos, status="pending", source="auto")
        db_session.add(e)
        entries[name] = e
    await db_session.commit()
    # Plain values: _waiting expires the session, and an expired object reloads lazily
    ids = {n: a.id for n, a in assets.items()}
    return station.id, ids, {n: e.id for n, e in entries.items()}


async def _waiting(db_session, station_id, assets):
    db_session.expire_all()
    rows = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position)
    )).scalars().all()
    by_id = {a: n for n, a in assets.items()}
    positions = [r.position for r in rows]
    assert len(positions) == len(set(positions)), f"two waiting entries share a position: {positions}"
    on_air = (await db_session.execute(
        select(QueueEntry.position).where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
    )).scalar_one()
    assert all(p > on_air for p in positions), f"a waiting entry is ahead of the item on air ({on_air}): {positions}"
    return [by_id[uuid.UUID(str(r.asset_id))] for r in rows], [(r.asset_id, r.position) for r in rows]


@pytest.mark.asyncio
async def test_operator_actions_keep_the_order_when_positions_have_holes(client, auth_headers, db_session):
    station, assets, entries = await _station_with_holes(db_session)
    base = f"/api/v1/stations/{station}/queue"

    # Play next: straight after the item on air
    r = await client.post(f"{base}/play-next", headers=auth_headers, json={"asset_id": str(assets["X"])})
    assert r.status_code == 201
    order, _ = await _waiting(db_session, station, assets)
    assert order == ["X", "A", "B", "C", "D"]

    # Insert at a point: after B
    r = await client.post(f"{base}/insert", headers=auth_headers,
                          json={"asset_id": str(assets["Y"]), "after_entry_id": str(entries["B"])})
    assert r.status_code == 201
    order, _ = await _waiting(db_session, station, assets)
    assert order == ["X", "A", "B", "Y", "C", "D"]

    # Arrows across a hole
    assert (await client.post(f"{base}/move-up", headers=auth_headers,
                              json={"entry_id": str(entries["D"]), "new_position": 0})).status_code == 200
    order, _ = await _waiting(db_session, station, assets)
    assert order == ["X", "A", "B", "Y", "D", "C"]
    assert (await client.post(f"{base}/move-down", headers=auth_headers,
                              json={"entry_id": str(entries["A"]), "new_position": 0})).status_code == 200
    order, _ = await _waiting(db_session, station, assets)
    assert order == ["X", "B", "A", "Y", "D", "C"]

    # Drag and drop sends the position of the row it is dropped on (QueuePanel.tsx)
    async def drop(name, on):
        _, rows = await _waiting(db_session, station, assets)
        target = next(pos for aid, pos in rows if str(aid) == str(assets[on]))
        res = await client.post(f"{base}/reorder-dnd", headers=auth_headers,
                                json={"entry_id": str(entries[name]), "new_position": target})
        assert res.status_code == 200

    await drop("C", "X")    # up to the top
    order, _ = await _waiting(db_session, station, assets)
    assert order == ["C", "X", "B", "A", "Y", "D"]
    await drop("B", "D")    # down to the end
    order, _ = await _waiting(db_session, station, assets)
    assert order == ["C", "X", "A", "Y", "D", "B"]

    # Next airs the top row
    r = await client.post(f"{base}/skip?fade=hard", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["now_playing"] == str(assets["C"])


@pytest.mark.asyncio
async def test_play_next_goes_ahead_of_everything_waiting(client, auth_headers, db_session):
    """Jerusalem 2026-11-19: a station ID aired ahead of an operator's Play next. With the
    item on air numbered after the waiting ones, "one after the item on air" was behind them."""
    station_id, assets, entries = await _station_with_holes(db_session)
    on_air = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.status == "playing"))).scalar_one()
    on_air.position = 12  # A (10) waits before it in the numbering
    await db_session.commit()
    res = await client.post(f"/api/v1/stations/{station_id}/queue/play-next",
                            json={"asset_id": str(assets["X"])}, headers=auth_headers)
    assert res.status_code in (200, 201), res.text
    db_session.expire_all()
    first = (await db_session.execute(
        select(QueueEntry.asset_id).where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position).limit(1))).scalar_one()
    assert str(first) == str(assets["X"])
