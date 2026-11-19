"""A song in several categories (services/asset_categories.py, models.asset.in_category)."""
import uuid
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, fit_for_random_pick, in_category, not_placeholder
from app.models.holiday_music import HolidayItem
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock
from app.models.station import Station
from app.services.asset_categories import bulk_add, set_extra
from app.services.playlist_generator_service import generate

NY = ZoneInfo("America/New_York")


def _asset(title, category="top_hits", asset_type="music"):
    return Asset(id=uuid.uuid4(), title=title, artist=title, duration=240.0,
                 file_path=f"{uuid.uuid4().hex}.mp3", asset_type=asset_type, category=category)


async def _ids(db, *conditions):
    return set((await db.execute(select(Asset.id).where(*conditions))).scalars().all())


@pytest.mark.asyncio
async def test_a_step_draws_main_and_extra_members(db_session: AsyncSession):
    hit = _asset("Hit")  # top_hits only
    havdalah_hit = _asset("New Havdalah song")  # top_hits, also motzei_shabbos
    eliyahu = _asset("Eliyahu Hanavi", "motzei_shabbos")  # motzei_shabbos only
    banned = _asset("Broken file", "do_not_play")  # extra must not bring it back
    carlebach = _asset("Carlebach song", "carlebach")
    db_session.add_all([hit, havdalah_hit, eliyahu, banned, carlebach])
    await db_session.commit()
    await set_extra(db_session, havdalah_hit.id, ["Motzei Shabbos"])
    await set_extra(db_session, banned.id, ["motzei_shabbos", "top_hits"])
    assert await bulk_add(db_session, [carlebach.id], "top_hits") == 1

    assert await _ids(db_session, in_category("motzei_shabbos"), not_placeholder()) == {havdalah_hit.id, eliyahu.id}
    assert await _ids(db_session, in_category("top_hits"), not_placeholder()) == {hit.id, havdalah_hit.id}
    # "any song" steps: a mix-only main category stays out, an extra one does not matter
    any_song = await _ids(db_session, Asset.asset_type == "music", fit_for_random_pick())
    assert hit.id in any_song and havdalah_hit.id in any_song
    assert eliyahu.id not in any_song and banned.id not in any_song


@pytest.mark.asyncio
async def test_playlist_with_a_motzei_shabbos_step(db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", is_active=True, automation_config={})
    db_session.add(station)
    await db_session.flush()
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    template = PlaylistTemplate(id=uuid.uuid4(), name="Motzei Shabbos", station_id=station.id, is_active=True)
    db_session.add_all([schedule, template])
    await db_session.flush()
    for i, c in enumerate(["motzei_shabbos", "top_hits"]):
        db_session.add(TemplateSlot(id=uuid.uuid4(), template_id=template.id, position=i, asset_type="music", category=c))
    db_session.add(ScheduleBlock(id=uuid.uuid4(), schedule_id=schedule.id, name="All day", start_time=time(0),
                                 end_time=time(23, 59), recurrence_type=RecurrenceType.DAILY,
                                 playlist_template_id=template.id))
    hits = [_asset(f"Hit {i}") for i in range(20)]
    eliyahu = [_asset(f"Eliyahu {i}", "motzei_shabbos") for i in range(3)]
    both = [_asset(f"Havdalah hit {i}") for i in range(3)]
    db_session.add_all(hits + eliyahu + both)
    await db_session.commit()
    assert await bulk_add(db_session, [a.id for a in both], "motzei_shabbos") == 3
    plan = await generate(db_session, station, datetime(2026, 11, 21, 18, 0, tzinfo=NY).astimezone(UTC), 3,
                          artist_distance=0)
    picked = [p.asset_id for p in plan.picks]
    mix = {a.id for a in eliyahu + both}
    # every second song comes from the Motzei Shabbos mix, songs only "also in" it included
    assert picked and sum(1 for a in picked if a in mix) >= len(picked) // 2 - 1
    assert any(a in picked for a in (b.id for b in both))


@pytest.mark.asyncio
async def test_categories_api(client: AsyncClient, db_session: AsyncSession, auth_headers: dict):
    song = _asset("Az Yashir")
    db_session.add(song)
    await db_session.flush()
    db_session.add(HolidayItem(id=uuid.uuid4(), holiday="pesach", asset_id=song.id, all_year=True))
    await db_session.commit()
    r = await client.put(f"/api/v1/assets/{song.id}/categories",
                         json={"extra_categories": ["Pesach Songs", "top_hits", "pesach songs"]}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # the main category is never listed twice; names as steps use them
    assert body["category"] == "top_hits" and body["extra_categories"] == ["pesach_songs"]
    assert body["holiday_lists"] == [{"holiday": "pesach", "name": "Pesach", "all_year": True, "station_name": None}]
    r = await client.get(f"/api/v1/assets/{song.id}/categories", headers=auth_headers)
    assert r.json()["extra_categories"] == ["pesach_songs"]
    # the Library's category filter finds it by an extra category too
    r = await client.get("/api/v1/assets?category=pesach_songs", headers=auth_headers)
    assert [a["id"] for a in r.json()["assets"]] == [str(song.id)]
    r = await client.post("/api/v1/assets/bulk-extra-category",
                          json={"asset_ids": [str(song.id)], "category": "pesach_songs", "remove": True},
                          headers=auth_headers)
    assert r.json() == {"changed": 1}
    r = await client.get(f"/api/v1/assets/{song.id}/categories", headers=auth_headers)
    assert r.json()["extra_categories"] == []
