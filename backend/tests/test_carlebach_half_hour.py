"""Main's first Carlebach half hour (12:30-1 AM, 2026-11-23) aired three things it
should not have:

1. a Chanukah song (the holiday mix, in season since 21 November) inside a block
   whose playlist plays only Carlebach;
2. a Carlebach song at 1:01, after the block ended at the 1 AM hard break;
3. its 12:30 sponsorships waited for the song on air, which a long song could carry
   past 12:35, when the quiet hours start.
"""
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.holiday_music import HolidayItem
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.services.playlist_generator_service import generate
from app.services.spot_schedule_service import cap_sponsor_waits
from tests.test_hour_fit import HOUR, _order, _queue, _station, fit_before_hard_item

NY = ZoneInfo("America/New_York")


def _asset(title, asset_type="music", category="lively", duration=240.0):
    return Asset(id=uuid.uuid4(), title=title, artist=title, duration=duration,
                 file_path=f"{uuid.uuid4().hex}.mp3", asset_type=asset_type, category=category)


async def _themed_station(db: AsyncSession, steps: list[tuple[str, str | None]]):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", is_active=True,
                      automation_config={})
    db.add(station)
    await db.flush()
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    template = PlaylistTemplate(id=uuid.uuid4(), name="Main - Carlebach half hour", station_id=station.id,
                                is_active=True)
    db.add_all([schedule, template])
    await db.flush()
    for i, (t, c) in enumerate(steps):
        db.add(TemplateSlot(id=uuid.uuid4(), template_id=template.id, position=i, asset_type=t, category=c))
    db.add(ScheduleBlock(id=uuid.uuid4(), schedule_id=schedule.id, name="Carlebach", start_time=time(0),
                         end_time=time(23, 59), recurrence_type=RecurrenceType.DAILY,
                         playlist_template_id=template.id))
    library = [_asset(f"Carlebach {i}", category="Carlebach") for i in range(40)]
    library += [_asset(f"Lively {i}") for i in range(40)]
    library += [_asset(f"Jingle {i}", "jingle", None, duration=8) for i in range(4)]
    chanukah = [_asset(f"Lichvod Chanukah {i}", category="lively", duration=74) for i in range(20)]
    db.add_all(library + chanukah)
    await db.flush()
    for a in chanukah:
        db.add(HolidayItem(id=uuid.uuid4(), holiday="chanukah", asset_id=a.id))
    await db.commit()
    return station, {a.id for a in chanukah}


@pytest.mark.asyncio
async def test_no_holiday_song_inside_a_one_category_playlist(db_session: AsyncSession):
    """Erev Chanukah (1 holiday song in 4 elsewhere): a Carlebach-only playlist stays
    Carlebach only; a jingle step beside it does not make it a mixed playlist."""
    station, chanukah = await _themed_station(
        db_session, [("music", "Carlebach"), ("music", "Carlebach"), ("jingle", None)]
    )
    plan = await generate(db_session, station, datetime(2026, 12, 4, 6, 0, tzinfo=NY).astimezone(UTC), 8,
                          artist_distance=0)
    songs = [p for p in plan.picks if p.asset_type == "music"]
    assert len(songs) > 50
    assert not any(p.asset_id in chanukah for p in plan.picks)
    assert {p.category for p in songs} == {"Carlebach"}


@pytest.mark.asyncio
async def test_a_mixed_playlist_still_gets_the_holiday_mix(db_session: AsyncSession):
    station, chanukah = await _themed_station(db_session, [("music", "Carlebach"), ("music", "lively")])
    plan = await generate(db_session, station, datetime(2026, 12, 4, 6, 0, tzinfo=NY).astimezone(UTC), 8,
                          artist_distance=0)
    assert sum(1 for p in plan.picks if p.asset_id in chanukah) >= 10


@pytest.mark.asyncio
async def test_songs_of_a_block_that_ends_at_the_hard_break_do_not_air_after_it(db_session: AsyncSession):
    """The run-up fills the time before the hour; a Carlebach song the queue now puts
    after the 1 AM break, before the night block's songs, is taken out."""
    station = await _station(db_session)
    on_air_ends = HOUR - timedelta(minutes=8)
    await _queue(db_session, station, on_air_ends, [
        ("Carlebach 1", 240.0, {"block_id": "carlebach"}),
        ("Carlebach 2", 260.0, {"block_id": "carlebach"}),  # on air at the hour, loses ~14 s
        ("Carlebach 3", 200.0, {"block_id": "carlebach"}),  # would air at 1:01
        ("Night 1", 240.0, {"block_id": "night"}),
        ("Night 2", 240.0, {"block_id": "night"}),
    ])
    sid = station.id
    assert await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(minutes=2))
    await db_session.commit()
    assert await _order(db_session, sid) == ["Carlebach 1", "Carlebach 2", "Night 1", "Night 2"]
    # nothing more to do on the next look
    assert not await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(minutes=2))


@pytest.mark.asyncio
async def test_a_block_that_goes_on_past_the_hour_keeps_its_songs(db_session: AsyncSession):
    station = await _station(db_session)
    on_air_ends = HOUR - timedelta(minutes=8)
    await _queue(db_session, station, on_air_ends, [
        ("Top 1", 240.0, {"block_id": "day"}),
        ("Top 2", 260.0, {"block_id": "day"}),
        ("Top 3", 200.0, {"block_id": "day"}),
        ("Top 4", 240.0, {"block_id": "day"}),
    ])
    sid = station.id
    await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(minutes=2))
    await db_session.commit()
    assert await _order(db_session, sid) == ["Top 1", "Top 2", "Top 3", "Top 4"]


@pytest.mark.asyncio
async def test_a_persons_pick_after_the_break_is_never_taken_out(db_session: AsyncSession):
    station = await _station(db_session)
    on_air_ends = HOUR - timedelta(minutes=8)
    await _queue(db_session, station, on_air_ends, [
        ("Carlebach 1", 240.0, {"block_id": "carlebach"}),
        ("Carlebach 2", 260.0, {"block_id": "carlebach"}),
        ("Requested", 200.0, {"source": "manual"}),
        ("Carlebach 3", 200.0, {"block_id": "carlebach"}),
        ("Night 1", 240.0, {"block_id": "night"}),
    ])
    await fit_before_hard_item(db_session, station.id, on_air_ends - timedelta(minutes=2))
    await db_session.commit()
    assert "Requested" in await _order(db_session, station.id)


async def _sponsor_station(db: AsyncSession):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", is_active=True,
                      automation_config={"sponsor_quiet_hours": {"start": "00:35", "end": "06:00"}})
    spot = _asset("TEST SPONSOR 22", "spot", "sponsorship", 30)
    other = _asset("Station promo", "spot", "station_promo", 20)
    sponsor = Sponsor(id=uuid.uuid4(), name="Test", length_seconds=30, audio_file_path="x.mp3")
    db.add_all([station, spot, other, sponsor])
    await db.flush()
    db.add(SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=station.id))
    await db.flush()
    return station, spot, other


@pytest.mark.asyncio
async def test_a_soft_sponsorship_break_waits_no_later_than_the_quiet_hours(db_session: AsyncSession):
    station, spot, other = await _sponsor_station(db_session)
    night = (datetime.now(NY) + timedelta(days=1)).date()

    def local(h, m):
        return datetime(night.year, night.month, night.day, h, m, tzinfo=NY).astimezone(UTC)

    rows = {
        "12:30 sponsor": QueueEntry(asset_id=spot.id, preempt_at=local(0, 30), source="block", timing_mode="soft"),
        "12:30 limit 3": QueueEntry(asset_id=spot.id, preempt_at=local(0, 30), source="block", timing_mode="soft",
                                    hard_by=local(0, 33)),
        "12:00 sponsor": QueueEntry(asset_id=spot.id, preempt_at=local(0, 0), source="block", timing_mode="soft"),
        "12:15 promo": QueueEntry(asset_id=other.id, preempt_at=local(0, 15), source="block", timing_mode="soft"),
        "12:20 hard": QueueEntry(asset_id=spot.id, preempt_at=local(0, 20), source="block", timing_mode="hard"),
    }
    for i, entry in enumerate(rows.values()):
        entry.id, entry.station_id, entry.position, entry.status = uuid.uuid4(), station.id, i + 1, "pending"
        db_session.add(entry)
    await db_session.flush()

    sid = station.id
    ids = {k: e.id for k, e in rows.items()}
    assert await cap_sponsor_waits(db_session, station, datetime.now(UTC)) == 1
    db_session.expire_all()
    station = await db_session.get(Station, sid)
    got = {k: (await db_session.get(QueueEntry, i)).hard_by for k, i in ids.items()}

    def as_utc(dt):
        return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt

    assert as_utc(got["12:30 sponsor"]) == local(0, 35)  # faded at 12:35 at the latest
    assert as_utc(got["12:30 limit 3"]) == local(0, 33)  # an earlier limit stays
    assert got["12:00 sponsor"] is None  # 35 min before: not near enough to matter
    assert got["12:15 promo"] is None  # no sponsorship in it
    assert got["12:20 hard"] is None  # hard: it never waits
    # and a second pass changes nothing
    assert await cap_sponsor_waits(db_session, station, datetime.now(UTC)) == 0


@pytest.mark.asyncio
async def test_no_quiet_hours_no_cap(db_session: AsyncSession):
    station, spot, _ = await _sponsor_station(db_session)
    station.automation_config = {}
    at = (datetime.now(UTC) + timedelta(days=1)).replace(second=0, microsecond=0)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=spot.id, position=1,
                              status="pending", source="block", preempt_at=at, timing_mode="soft"))
    await db_session.flush()
    assert await cap_sponsor_waits(db_session, station, datetime.now(UTC)) == 0
    rows = (await db_session.execute(select(QueueEntry.hard_by).where(QueueEntry.station_id == station.id))).all()
    assert rows == [(None,)]
