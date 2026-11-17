import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.play_log import PlayLog
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock
from app.models.station import Station
from app.services.playlist_generator_service import generate, settings_for
from app.services.queue_replenish_service import QueueReplenishService

NY = ZoneInfo("America/New_York")
MONDAY_7AM = datetime(2026, 11, 16, 7, 0, tzinfo=NY).astimezone(UTC)


def _song(category: str, artist: str, n: int) -> Asset:
    return Asset(
        id=uuid.uuid4(),
        title=f"{category} {artist} {n}",
        artist=artist,
        duration=240.0,
        file_path=f"{uuid.uuid4().hex}.mp3",
        asset_type="music",
        category=category,
    )


async def _setup(db: AsyncSession, automation_config: dict | None = None):
    """A station with a morning mix (fast, hit, fast, relax) and an evening block of relax only."""
    station = Station(
        id=uuid.uuid4(),
        name="KBR",
        timezone="America/New_York",
        is_active=True,
        automation_config=automation_config,
    )
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    mix = PlaylistTemplate(id=uuid.uuid4(), name="6 am mix", station_id=station.id, is_active=True)
    calm = PlaylistTemplate(id=uuid.uuid4(), name="Evening", station_id=station.id, is_active=True)
    db.add(station)
    await db.flush()
    db.add_all([schedule, mix, calm])
    await db.flush()
    for i, category in enumerate(["1 med fast", "*NEWHIT*", "1 med fast", "1 Relax"]):
        db.add(
            TemplateSlot(
                id=uuid.uuid4(),
                template_id=mix.id,
                position=i,
                asset_type="music",
                category=category,
            )
        )
    db.add(
        TemplateSlot(
            id=uuid.uuid4(), template_id=calm.id, position=0, asset_type="music", category="1 Relax"
        )
    )
    morning = ScheduleBlock(
        id=uuid.uuid4(),
        schedule_id=schedule.id,
        name="6 am mix",
        start_time=time(6),
        end_time=time(18),
        recurrence_type=RecurrenceType.DAILY,
        playlist_template_id=mix.id,
    )
    evening = ScheduleBlock(
        id=uuid.uuid4(),
        schedule_id=schedule.id,
        name="Evening",
        start_time=time(18),
        end_time=time(23, 59),
        recurrence_type=RecurrenceType.DAILY,
        playlist_template_id=calm.id,
    )
    db.add_all([morning, evening])
    songs = []
    for category, artists in {
        "1 med fast": ["Avraham Fried", "MBD", "Shwekey", "Benny Friedman"],
        "*NEWHIT*": ["Avraham Fried", "Ishay Ribo"],
        "1 Relax": ["Avraham Fried", "Shwekey", "Baruch Levine"],
    }.items():
        for artist in artists:
            for n in range(3):
                songs.append(_song(category, artist, n))
    db.add_all(songs)
    await db.commit()
    return station, songs, morning, evening


@pytest.mark.asyncio
async def test_sequence_artist_distance_and_block_change(db_session: AsyncSession):
    station, songs, morning, evening = await _setup(db_session)
    # The draw is random among equally rested songs. In this tiny library some
    # orders leave only one artist's songs unplayed; the artist rule then wins
    # and a queued song is used again, which breaks the "nothing repeats"
    # check below by luck. Fix the order so the test is about the rules.
    import random
    random.seed(7)
    plan = await generate(db_session, station, MONDAY_7AM, 2, artist_distance=2)
    picks = plan.picks
    # two hours of four-minute songs, each on the air until 3 s before its end
    assert len(picks) == 31
    # the sequence repeats in order
    assert [p.category for p in picks[:8]] == [
        "1 med fast",
        "*NEWHIT*",
        "1 med fast",
        "1 Relax",
    ] * 2
    # the same artist never comes back within two songs
    for i, p in enumerate(picks):
        assert p.artist not in [q.artist for q in picks[max(0, i - 2) : i]]
        assert not p.artist_rule_relaxed
    # nothing repeats while unplayed songs of its type remain
    fast = [p.asset_id for p in picks if p.category == "1 med fast"]
    assert len(set(fast[:12])) == 12
    # the cursor says where to carry on
    assert plan.cursor[str(morning.id)] == 31 % 4

    # carrying on gives the next type in the sequence, not the first again
    more = await generate(
        db_session, station, picks[-1].at, 0.1, artist_distance=2, cursor=plan.cursor
    )
    assert more.picks[0].category == "1 Relax"  # position 4 of the sequence

    # across 6 PM the evening block's own sequence takes over
    five_thirty = datetime(2026, 11, 16, 17, 30, tzinfo=NY).astimezone(UTC)
    cross = await generate(db_session, station, five_thirty, 1, artist_distance=1)
    names = [p.block_name for p in cross.picks]
    assert names[0] == "6 am mix" and names[-1] == "Evening"
    assert all(p.category == "1 Relax" for p in cross.picks if p.block_name == "Evening")


@pytest.mark.asyncio
async def test_longest_rested_songs_come_first_and_gaps_are_reported(db_session: AsyncSession):
    station, songs, *_ = await _setup(db_session)
    relax = [s for s in songs if s.category == "1 Relax"]
    # everything in Relax aired recently except two songs
    rested = {relax[0].id, relax[4].id}
    for s in relax:
        if s.id not in rested:
            db_session.add(
                PlayLog(
                    id=uuid.uuid4(),
                    station_id=station.id,
                    asset_id=s.id,
                    start_utc=MONDAY_7AM - timedelta(hours=1),
                )
            )
    # Shabbos covers part of the window
    db_session.add(
        HolidayWindow(
            id=uuid.uuid4(),
            name="Shabbos",
            start_datetime=datetime(2026, 11, 16, 19, 0, tzinfo=NY).astimezone(UTC),
            end_datetime=datetime(2026, 11, 16, 20, 0, tzinfo=NY).astimezone(UTC),
            is_blackout=True,
        )
    )
    await db_session.commit()
    evening_start = datetime(2026, 11, 16, 18, 30, tzinfo=NY).astimezone(UTC)
    plan = await generate(db_session, station, evening_start, 2, artist_distance=0)
    assert {p.asset_id for p in plan.picks[:2]} == rested
    off_start = datetime(2026, 11, 16, 19, 0, tzinfo=NY).astimezone(UTC)
    off_end = datetime(2026, 11, 16, 20, 0, tzinfo=NY).astimezone(UTC)
    # a song may run into the off-air time, but none starts inside it
    assert not [p for p in plan.picks if off_start <= p.at < off_end]
    assert any("Shabbos" in n for n in plan.notes)

    # midnight to 6 AM has no block
    night = datetime(2026, 11, 17, 1, 0, tzinfo=NY).astimezone(UTC)
    empty = await generate(db_session, station, night, 1)
    # Time with no block still plays: any music, so the station never goes quiet
    assert empty.picks and all(p.asset_type == "music" and p.block_name == "No block" for p in empty.picks)
    assert any("no schedule block" in n for n in empty.notes)


@pytest.mark.asyncio
async def test_settings_endpoints_and_queue_fill(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    station, songs, morning, _ = await _setup(db_session)
    url = "/api/v1/playlists/generation"
    r = await client.get(url, params={"station_id": str(station.id)}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False and r.json()["hours"] == 6
    assert (await client.get(url, params={"station_id": str(station.id)})).status_code in (401, 403)

    r = await client.put(
        url,
        json={"station_id": str(station.id), "enabled": True, "hours": 2, "artist_distance": 1},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    await db_session.refresh(station)
    assert settings_for(station.automation_config).hours == 2

    # the queue filler runs "now"; pin now to Monday morning so the morning block is on
    import app.services.queue_replenish_service as qrs

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return MONDAY_7AM

    monkeypatch.setattr(qrs, "datetime", _Clock)
    await QueueReplenishService(db_session, station.id).replenish()
    entries = (
        (
            await db_session.execute(
                select(QueueEntry)
                .where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
                .order_by(QueueEntry.position)
            )
        )
        .scalars()
        .all()
    )
    assert len(entries) == 31  # two hours, not seven days
    by_id = {s.id: s for s in songs}
    assert [by_id[e.asset_id].category for e in entries[:4]] == [
        "1 med fast",
        "*NEWHIT*",
        "1 med fast",
        "1 Relax",
    ]
    await db_session.refresh(station)
    assert station.automation_config["playlist_generation"]["cursor"][str(morning.id)] == 31 % 4

    # a second run adds nothing: the queue already reaches two hours
    await QueueReplenishService(db_session, station.id).replenish()
    count = len(
        (
            await db_session.execute(
                select(QueueEntry.id).where(QueueEntry.station_id == station.id)
            )
        ).all()
    )
    assert count == 31

    # regenerate keeps what a person queued and replaces the rest
    entries[0].source = "manual"
    await db_session.commit()
    r = await client.post(
        f"{url}/regenerate", params={"station_id": str(station.id)}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 29  # the next song stays (it may be about to start)
    kept = (
        (await db_session.execute(select(QueueEntry).where(QueueEntry.source == "manual")))
        .scalars()
        .all()
    )
    assert len(kept) == 1


@pytest.mark.asyncio
async def test_minimum_vote_keeps_low_voted_songs_out(db_session: AsyncSession):
    station, songs, *_ = await _setup(db_session)
    fast = [s for s in songs if s.category == "1 med fast"]
    liked = {s.id for s in fast[:6]}
    for s in fast[:6]:
        s.vote = 3
    await db_session.commit()

    plan = await generate(db_session, station, MONDAY_7AM, 2, artist_distance=0, min_vote=3)
    picked_fast = {p.asset_id for p in plan.picks if p.category == "1 med fast"}
    assert picked_fast and picked_fast <= liked
    # Types where nothing reaches the minimum still play, and say so.
    assert any(p.category == "1 Relax" for p in plan.picks)
    assert any("1 Relax" in n and "minimum vote" in n for n in plan.notes)
    assert not any("1 med fast" in n and "minimum vote" in n for n in plan.notes)

    # Without a minimum, everything is eligible again.
    everyone = await generate(db_session, station, MONDAY_7AM, 2, artist_distance=0)
    assert not {p.asset_id for p in everyone.picks if p.category == "1 med fast"} <= liked


@pytest.mark.asyncio
async def test_vote_can_be_set_and_is_listed(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    _, songs, *_ = await _setup(db_session)
    song = songs[0]
    r = await client.patch(f"/api/v1/assets/{song.id}", json={"vote": 4}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["vote"] == 4
    r = await client.patch(f"/api/v1/assets/{song.id}", json={"vote": -1}, headers=auth_headers)
    assert r.status_code == 422
    listed = await client.get("/api/v1/assets", params={"limit": 500}, headers=auth_headers)
    assert listed.status_code == 200
    by_id = {a["id"]: a for a in listed.json()["assets"]}
    assert by_id[str(song.id)]["vote"] == 4
    assert by_id[str(songs[1].id)]["vote"] == 0
