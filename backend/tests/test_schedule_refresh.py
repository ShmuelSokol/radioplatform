"""A schedule change takes over on time, even with a long queue already built."""
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.station import Station
from app.services.queue_replenish_service import QueueReplenishService

NY = ZoneInfo("America/New_York")
MONDAY_7AM = datetime(2026, 11, 16, 7, 0, tzinfo=NY).astimezone(UTC)


def _song(category: str, n: int, artist: str | None = None) -> Asset:
    return Asset(
        id=uuid.uuid4(),
        title=f"{category} {n}",
        artist=artist or f"{category} artist {n % 7}",
        duration=240.0,
        file_path=f"{uuid.uuid4().hex}.mp3",
        asset_type="music",
        category=category,
    )


def _pin_clock(monkeypatch):
    import app.services.queue_replenish_service as qrs

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return MONDAY_7AM

    monkeypatch.setattr(qrs, "datetime", _Clock)


async def _pending(db: AsyncSession, station_id) -> list[QueueEntry]:
    rows = await db.execute(
        select(QueueEntry)
        .where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position)
    )
    return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_new_block_with_a_playlist_replaces_the_old_queue(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    _pin_clock(monkeypatch)
    station = Station(id=uuid.uuid4(), name="KBR", timezone="America/New_York", is_active=True)
    db_session.add(station)
    await db_session.flush()
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Weekdays", is_active=True)
    mix = PlaylistTemplate(id=uuid.uuid4(), name="Morning mix", station_id=station.id, is_active=True)
    db_session.add_all([schedule, mix])
    await db_session.flush()
    for i, category in enumerate(["Fast", "Relax"]):
        db_session.add(
            TemplateSlot(id=uuid.uuid4(), template_id=mix.id, position=i, asset_type="music", category=category)
        )
    old = [_song("Old", n) for n in range(5)]
    songs = [_song(c, n) for c in ("Fast", "Relax") for n in range(60)]
    db_session.add_all(old + songs)
    await db_session.flush()
    # A week of ordinary fill already queued (300 songs, 20 hours)
    for i in range(300):
        db_session.add(
            QueueEntry(
                id=uuid.uuid4(),
                station_id=station.id,
                asset_id=old[i % 5].id,
                position=i + 1,
                status="pending",
                source="auto",
            )
        )
    # Something a person queued stays
    person = QueueEntry(
        id=uuid.uuid4(), station_id=station.id, asset_id=old[0].id, position=301, status="pending", source="manual"
    )
    db_session.add(person)
    station_id, person_id, mix_id = station.id, person.id, mix.id
    category = {s.id: s.category for s in old + songs}
    await db_session.commit()

    r = await client.post(
        "/api/v1/schedules/blocks",
        json={
            "schedule_id": str(schedule.id),
            "name": "Morning mix",
            "start_time": "08:00:00",
            "end_time": "18:00:00",
            "recurrence_type": "daily",
            "playlist_template_id": str(mix.id),
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text

    entries = await _pending(db_session, station_id)
    auto = [e for e in entries if e.source == "auto"]
    # The old fill is gone (before 8:00 any music may play); the new fill
    # reaches a few hours, not a week
    assert not [e for e in auto[15:] if category[e.asset_id] == "Old"]
    assert 60 <= len(auto) <= 95
    assert any(e.id == person_id for e in entries)
    # From 8:00 the block's playlist plays in order. Each song holds the air for
    # 237 s (until its crossfade); the person's song and 15 automatic ones fill
    # 7:00 to 7:59:15, so the block starts with the sixteenth automatic one.
    first = next(i for i, e in enumerate(auto) if e.block_id)
    assert first == 15
    cats = [category[e.asset_id] for e in auto]
    assert cats[15:21] == ["Fast", "Relax"] * 3

    # Changing the playlist regenerates again: now it is Relax only
    slots = (await db_session.execute(select(TemplateSlot).where(TemplateSlot.template_id == mix_id))).scalars().all()
    fast = next(s for s in slots if s.category == "Fast")
    r = await client.delete(f"/api/v1/playlists/slots/{fast.id}", headers=auth_headers)
    assert r.status_code in (200, 204), r.text
    db_session.expire_all()
    auto = [e for e in await _pending(db_session, station_id) if e.source == "auto"]
    cats = [category[e.asset_id] for e in auto]
    assert cats[15:21] == ["Relax"] * 6

    # The schedule check says which block plays
    r = await client.get(
        "/api/v1/schedules/look-ahead", params={"station_id": str(station_id), "days": 2}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    plays = [f for f in r.json()["findings"] if f["kind"] == "plays"]
    assert plays and all(f["title"] == '"Morning mix"' for f in plays)
    assert all(f["local_start"][11:] in ("08:00",) or f["local_start"][11:16] < "18:00" for f in plays)
    assert 'playlist "Morning mix"' in plays[0]["detail"]


@pytest.mark.asyncio
async def test_station_without_block_playlists_keeps_its_queue(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station = Station(id=uuid.uuid4(), name="KBR 2", timezone="America/New_York", is_active=True)
    db_session.add(station)
    await db_session.flush()
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    song = _song("Old", 1)
    db_session.add_all([schedule, song])
    await db_session.flush()
    entry = QueueEntry(
        id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1, status="pending", source="auto"
    )
    db_session.add(entry)
    await db_session.commit()
    r = await client.post(
        "/api/v1/schedules/blocks",
        json={"schedule_id": str(schedule.id), "name": "Talk", "start_time": "08:00:00", "end_time": "09:00:00"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert (await db_session.execute(select(QueueEntry.id).where(QueueEntry.id == entry.id))).first()


@pytest.mark.asyncio
async def test_dated_weather_already_queued_as_a_song_is_cleared(db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="KBR 3", timezone="America/New_York", is_active=True)
    weather = Asset(
        id=uuid.uuid4(),
        title="Weather Report 2026-11-14",
        duration=40.0,
        file_path="w.mp3",
        asset_type="spot",
        category="weather_spot",
    )
    db_session.add_all([station, weather])
    await db_session.flush()
    plain = QueueEntry(
        id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=1, status="pending", source="auto"
    )
    timed = QueueEntry(
        id=uuid.uuid4(),
        station_id=station.id,
        asset_id=weather.id,
        position=2,
        status="pending",
        source="auto",
        preempt_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add_all([plain, timed])
    await db_session.commit()
    await QueueReplenishService(db_session, station.id).replenish()
    await db_session.commit()
    ids = {r[0] for r in (await db_session.execute(select(QueueEntry.id))).all()}
    assert plain.id not in ids


@pytest.mark.asyncio
async def test_block_does_not_start_early_while_a_song_is_on_the_air(db_session: AsyncSession, monkeypatch):
    """The song on the air counts only for what is left of it (live test: the
    block's pattern began 1.5 minutes early)."""
    from datetime import time

    from app.models.schedule_block import RecurrenceType, ScheduleBlock

    _pin_clock(monkeypatch)
    station = Station(id=uuid.uuid4(), name="KBR 4", timezone="America/New_York", is_active=True)
    db_session.add(station)
    await db_session.flush()
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    talk = PlaylistTemplate(id=uuid.uuid4(), name="Shiurim", station_id=station.id, is_active=True)
    db_session.add_all([schedule, talk])
    await db_session.flush()
    db_session.add(TemplateSlot(id=uuid.uuid4(), template_id=talk.id, position=0, asset_type="shiur", category="Halacha"))
    # The block starts at 7:03; the song on the air started at 6:57 and ends at 7:01
    db_session.add(
        ScheduleBlock(
            id=uuid.uuid4(), schedule_id=schedule.id, name="Shiurim", start_time=time(7, 3),
            end_time=time(18), recurrence_type=RecurrenceType.DAILY, playlist_template_id=talk.id,
        )
    )
    music = [_song("Music", n) for n in range(30)]
    shiurim = [
        Asset(id=uuid.uuid4(), title=f"Shiur {n}", artist=f"Rav {n}", duration=240.0,
              file_path=f"{uuid.uuid4().hex}.mp3", asset_type="shiur", category="Halacha")
        for n in range(30)
    ]
    db_session.add_all(music + shiurim)
    await db_session.flush()
    db_session.add(
        QueueEntry(
            id=uuid.uuid4(), station_id=station.id, asset_id=music[0].id, position=0, status="playing",
            source="auto", started_at=MONDAY_7AM - timedelta(minutes=3),
        )
    )
    station_id = station.id
    kinds = {a.id: a.asset_type for a in music + shiurim}
    await db_session.commit()

    await QueueReplenishService(db_session, station_id).replenish()
    await db_session.commit()
    pending = [e for e in await _pending(db_session, station_id) if e.source == "auto"]
    # 7:01 is before the block: a song. From 7:05 the block plays.
    assert kinds[pending[0].asset_id] == "music"
    assert kinds[pending[1].asset_id] == "shiur"
