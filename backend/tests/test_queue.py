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
