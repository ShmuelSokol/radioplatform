"""KBR Jerusalem can have its own hourly time clips, like its own weather."""
import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from tests.test_weather_spots import _patches


async def _put(client: AsyncClient, headers: dict, hour: int, region: str | None = None):
    return await client.put(
        f"/api/v1/time-announcements/{hour}",
        params={"region": region} if region else None,
        files={"file": ("t.mp3", io.BytesIO(b"fake audio"), "audio/mpeg")},
        data={"script": f"the time in {region or 'lakewood'}"},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_regions_keep_their_own_clips(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    p1, p2, p3 = _patches(3.0)
    with p1, p2, p3:
        lk = await _put(client, auth_headers, 15)
        jm = await _put(client, auth_headers, 15, "jerusalem")
        jm2 = await _put(client, auth_headers, 15, "Jerusalem")      # replaces in place
        bad = await _put(client, auth_headers, 15, "../x")
    assert lk.status_code == 200 and jm.status_code == 200, (lk.text, jm.text)
    assert bad.status_code == 422
    assert lk.json()["region"] == "lakewood" and jm.json()["region"] == "jerusalem"
    assert lk.json()["asset_id"] != jm.json()["asset_id"]
    assert jm2.json()["asset_id"] == jm.json()["asset_id"] and jm2.json()["replaced"] is True
    assert "time/jerusalem_15_" in jm.json()["file_url"]

    got_lk = (await client.get("/api/v1/time-announcements", headers=auth_headers)).json()
    got_jm = (await client.get("/api/v1/time-announcements", params={"region": "jerusalem"}, headers=auth_headers)).json()
    assert [c["asset_id"] for c in got_lk] == [lk.json()["asset_id"]]
    assert [c["asset_id"] for c in got_jm] == [jm.json()["asset_id"]]

    titles = sorted(a.title for a in (await db_session.execute(select(Asset))).scalars())
    assert titles == ["Time Announcement - 15:00", "Time Announcement - 15:00 (Jerusalem)"]


@pytest.mark.asyncio
async def test_station_plays_its_regions_clip_else_the_default(db_session: AsyncSession):
    from app.services.weather_spot_service import get_or_create_weather_spot_assets

    old = Asset(id=uuid.uuid4(), title="Time Announcement - 15:00", file_path="lk15", asset_type="jingle",
                category="time_announcement", metadata_extra={"time_of_day": "15"})      # made before regions
    old16 = Asset(id=uuid.uuid4(), title="Time Announcement - 16:00", file_path="lk16", asset_type="jingle",
                  category="time_announcement", metadata_extra={"time_of_day": "16"})
    jm = Asset(id=uuid.uuid4(), title="Time Announcement - 15:00 (Jerusalem)", file_path="jm15", asset_type="jingle",
               category="time_announcement", metadata_extra={"time_of_day": "15", "region": "jerusalem"})
    db_session.add_all([old, old16, jm])
    await db_session.commit()

    p1, p2, p3 = _patches()
    with p1, p2, p3:
        t_jm, _ = await get_or_create_weather_spot_assets(db_session, "2026-11-16T15", "jerusalem")
        t_lk, _ = await get_or_create_weather_spot_assets(db_session, "2026-11-16T15")
        t_jm16, _ = await get_or_create_weather_spot_assets(db_session, "2026-11-16T16", "jerusalem")
    assert t_jm.id == jm.id
    assert t_lk.id == old.id                 # Lakewood never plays Jerusalem's clip
    assert t_jm16.id == old16.id             # no Jerusalem clip for 16:00 yet: the shared one


@pytest.mark.asyncio
async def test_queued_time_checks_move_to_the_new_regional_clip(db_session: AsyncSession):
    from app.services.queue_replenish_service import QueueReplenishService

    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem",
                      automation_config={"weather_region": "jerusalem"})
    lk15 = Asset(id=uuid.uuid4(), title="Time Announcement - 15:00", file_path="lk15", asset_type="jingle",
                 category="time_announcement", metadata_extra={"time_of_day": "15"})
    lk16 = Asset(id=uuid.uuid4(), title="Time Announcement - 16:00", file_path="lk16", asset_type="jingle",
                 category="time_announcement", metadata_extra={"time_of_day": "16"})
    db_session.add_all([station, lk15, lk16])
    at = datetime.now(timezone.utc) + timedelta(hours=1)
    e15 = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=lk15.id, position=1, status="pending", preempt_at=at)
    e16 = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=lk16.id, position=2, status="pending",
                     preempt_at=at + timedelta(hours=1))
    db_session.add_all([e15, e16])
    await db_session.commit()
    ids = (e15.id, e16.id, lk16.id)

    svc = QueueReplenishService(db_session, station.id)
    assert await svc._realign_time_clips("jerusalem") == 0        # no Jerusalem clips yet: keep the shared ones

    jm15 = Asset(id=uuid.uuid4(), title="Time Announcement - 15:00 (Jerusalem)", file_path="jm15", asset_type="jingle",
                 category="time_announcement", metadata_extra={"time_of_day": "15", "region": "jerusalem"})
    db_session.add(jm15)
    await db_session.commit()
    jm15_id = jm15.id
    assert await svc._realign_time_clips("jerusalem") == 1
    await db_session.commit()
    got = {e.id: e.asset_id for e in (await db_session.execute(select(QueueEntry))).scalars()}
    assert str(got[ids[0]]) == str(jm15_id)
    assert str(got[ids[1]]) == str(ids[2])                         # 16:00 has no Jerusalem clip: unchanged
    assert await svc._realign_time_clips("jerusalem") == 0
