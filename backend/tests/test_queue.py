import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry


@pytest.mark.asyncio
async def test_get_queue_returns_entries_in_order(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    res = await client.post("/api/v1/stations", json={"name": "Queue Station"}, headers=auth_headers)
    station_id = res.json()["id"]

    assets = [
        Asset(id=uuid.uuid4(), title=f"Song {i}", artist="Artist", duration=180.0,
              file_path=f"song{i}.mp3", asset_type="music")
        for i in range(3)
    ]
    db_session.add_all(assets)
    for i, asset in enumerate(assets):
        db_session.add(QueueEntry(
            id=uuid.uuid4(), station_id=uuid.UUID(station_id), asset_id=asset.id,
            position=i + 1, status="pending", source="manual",
        ))
    # A played entry must not show up in the queue.
    db_session.add(QueueEntry(
        id=uuid.uuid4(), station_id=uuid.UUID(station_id), asset_id=assets[0].id,
        position=0, status="played", source="auto",
    ))
    await db_session.commit()

    res = await client.get(f"/api/v1/stations/{station_id}/queue", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 3
    assert [e["asset"]["title"] for e in data["entries"]] == ["Song 0", "Song 1", "Song 2"]
    assert data["entries"][0]["asset"]["duration"] == 180.0
    assert data["total_queue_duration_seconds"] == 540.0


@pytest.mark.asyncio
async def test_queue_says_which_timed_items_cut_the_song(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """The control-room player fades the song only for an item that starts on the second
    (or a soft one at its limit). A soft sponsorship must not be reported as cutting."""
    from datetime import datetime, timedelta, timezone

    res = await client.post("/api/v1/stations", json={"name": "Timed Station"}, headers=auth_headers)
    station_id = uuid.UUID(res.json()["id"])
    at = datetime.now(timezone.utc) + timedelta(minutes=10)
    rows = [
        ("soft", "ad_slot", None, None),
        ("hard", "ad_slot", "hard", None),
        ("legacy", "ad_slot", None, None),
        ("limit", "block", "soft", at + timedelta(minutes=5)),
        ("hourly", "hourly", None, None),
    ]
    for i, (name, source, mode, hard_by) in enumerate(rows):
        asset = Asset(id=uuid.uuid4(), title=name, artist="", duration=30.0,
                      file_path=f"{name}.mp3", asset_type="spot")
        db_session.add(asset)
        db_session.add(QueueEntry(
            id=uuid.uuid4(), station_id=station_id, asset_id=asset.id, position=i + 1,
            status="pending", source=source, preempt_at=at + timedelta(seconds=i),
            timing_mode=mode if name != "soft" else "soft", hard_by=hard_by,
        ))
    await db_session.commit()

    res = await client.get(f"/api/v1/stations/{station_id}/queue", headers=auth_headers)
    by_name = {e["asset"]["title"]: e for e in res.json()["entries"]}
    assert {n: e["hard_timed"] for n, e in by_name.items()} == {
        "soft": False, "hard": True, "legacy": False, "limit": False, "hourly": True,
    }
    assert by_name["limit"]["hard_by"] is not None
    assert by_name["soft"]["hard_by"] is None
