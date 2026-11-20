"""Station IDs in the breaks, not between songs (Shmuel 2026-11-19 8:09 PM ET: "Station IDs
should be part of the ad blocks. Or once an hour. Not randomly between songs.")."""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.program_block import ProgramBlock
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.playlist_generator_service import id_settings_for
from app.services.program_block_service import reconcile_block_entries


async def _station(db: AsyncSession, ids: dict):
    station = Station(id=uuid.uuid4(), name=f"S {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      automation_config={"station_ids": ids})
    db.add(station)
    psa = Asset(id=uuid.uuid4(), title="PSA", file_path="p.mp3", asset_type="spot", category="psa", duration=30)
    shiur = Asset(id=uuid.uuid4(), title="Daf", file_path="d.mp3", asset_type="shiur", category="program",
                  duration=3600)
    jingles = [Asset(id=uuid.uuid4(), title=f"Station ID {n}", file_path=f"j{n}.mp3", asset_type="jingle",
                     category="station_id", duration=6) for n in range(3)]
    db.add_all([psa, shiur, *jingles])
    await db.flush()
    db.add(ProgramBlock(id=uuid.uuid4(), station_id=station.id, name="Breaks", timing_mode="soft",
                        times=",".join(f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)),
                        items=[{"kind": "asset", "asset_id": str(psa.id)}]))
    db.add(ProgramBlock(id=uuid.uuid4(), station_id=station.id, name="Daf", timing_mode="hard", times="20:00",
                        items=[{"kind": "asset", "asset_id": str(shiur.id)}]))
    await db.commit()
    return station, {j.id for j in jingles}


async def _groups(db: AsyncSession, station):
    rows = (await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
        .order_by(QueueEntry.preempt_at, QueueEntry.position))).scalars().all()
    groups: dict = {}
    for e in rows:
        groups.setdefault(e.preempt_at, []).append(e.asset_id)
    return groups


def test_ids_go_in_the_breaks_by_default_not_between_songs():
    s = id_settings_for({"station_ids": {"enabled": True}})
    assert s.on and s.placement == "hourly" and not s.enabled  # no sprinkle between songs
    assert id_settings_for({"station_ids": {"enabled": True, "placement": "between_songs"}}).enabled


@pytest.mark.asyncio
async def test_once_an_hour_the_id_closes_the_top_of_hour_break(db_session: AsyncSession):
    station, jingles = await _station(db_session, {"enabled": True})
    await reconcile_block_entries(db_session, station.id)
    await db_session.commit()
    groups = await _groups(db_session, station)
    with_id = {at for at, assets in groups.items() if set(assets) & jingles}
    assert with_id and all(at.minute == 0 for at in with_id)
    for at in with_id:
        assert groups[at][-1] in jingles  # last in its break
        assert sum(a in jingles for a in groups[at]) == 1
    # never after the Daf at 8 PM: the ID would follow the whole shiur
    assert not any(at.hour == 1 for at in with_id)  # 8 PM New York in November = 01:00Z
    # a second run adds nothing
    await reconcile_block_entries(db_session, station.id)
    await db_session.commit()
    assert {at for at, a in (await _groups(db_session, station)).items() if set(a) & jingles} == with_id


@pytest.mark.asyncio
async def test_every_block_puts_one_id_in_each_break(db_session: AsyncSession):
    station, jingles = await _station(db_session, {"enabled": True, "placement": "every_block"})
    await reconcile_block_entries(db_session, station.id)
    await db_session.commit()
    groups = await _groups(db_session, station)
    breaks = [at for at in groups if at.hour != 1 or at.minute != 0]
    assert breaks and all(sum(a in jingles for a in groups[at]) == 1 for at in breaks)
    # the three IDs take turns
    firsts = [next(a for a in groups[at] if a in jingles) for at in sorted(breaks)[:3]]
    assert set(firsts) == jingles


@pytest.mark.asyncio
async def test_ids_off_or_between_songs_put_none_in_the_breaks(db_session: AsyncSession):
    for ids in ({"enabled": False}, {"enabled": True, "placement": "between_songs"}):
        station, jingles = await _station(db_session, ids)
        await reconcile_block_entries(db_session, station.id)
        await db_session.commit()
        groups = await _groups(db_session, station)
        assert groups and not any(set(a) & jingles for a in groups.values())


@pytest.mark.asyncio
async def test_a_station_without_blocks_gets_the_id_in_its_top_of_hour_group(db_session: AsyncSession):
    from app.services.queue_replenish_service import QueueReplenishService

    station = Station(id=uuid.uuid4(), name=f"S {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      automation_config={"station_ids": {"enabled": True}})
    db_session.add(station)
    jingles = [Asset(id=uuid.uuid4(), title=f"Station ID {n}", file_path=f"k{n}.mp3", asset_type="jingle",
                     category="station_id", duration=6) for n in range(2)]
    db_session.add_all(jingles)
    await db_session.commit()
    service = QueueReplenishService(db_session, station.id)
    service.max_pos = 0
    ids = id_settings_for(station.automation_config)
    await service._schedule_hourly_station_ids(ids.category, ids)
    await db_session.commit()
    groups = await _groups(db_session, station)
    assert 47 <= len(groups) <= 48 and all(at.minute == 0 for at in groups)
    assert {a for assets in groups.values() for a in assets} == {j.id for j in jingles}  # they take turns


@pytest.mark.asyncio
async def test_left_over_between_songs_ids_are_taken_out(db_session: AsyncSession):
    """IDs the 12-20 min rule queued between songs go when the station's IDs move to the breaks."""
    from app.services.station_id_guard import clear_between_songs

    station, jingles = await _station(db_session, {"enabled": True})
    song = Asset(id=uuid.uuid4(), title="Song", file_path="s.mp3", asset_type="music", category="lively",
                 duration=200)
    db_session.add(song)
    jingle = next(iter(jingles))
    rows = [QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=a, position=n, status="pending",
                       source=src) for n, (a, src) in enumerate([(song.id, "auto"), (jingle, "auto"),
                                                                (song.id, "auto"), (jingle, "manual")], 1)]
    db_session.add_all(rows)
    await db_session.commit()
    assert await clear_between_songs(db_session, station.id) == 1
    await db_session.commit()
    assert [r.status for r in rows] == ["pending", "skipped", "pending", "pending"]  # one put in by hand stays
    # with the between-songs rule chosen, they stay
    station.automation_config = {"station_ids": {"enabled": True, "placement": "between_songs"}}
    rows[1].status = "pending"
    await db_session.commit()
    assert await clear_between_songs(db_session, station.id) == 0
