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


async def _blocks(db: AsyncSession, station, spans: dict[str, tuple[int, int]]) -> dict[str, str]:
    """Schedule blocks around HOUR on the station's clock: name -> (first hour, last
    hour) counted from HOUR's hour. Returns name -> block id (what songs carry)."""
    local = HOUR.astimezone(ZoneInfo(station.timezone))
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Daily", is_active=True)
    db.add(schedule)
    await db.flush()
    ids = {}
    for name, (a, b) in spans.items():
        block = ScheduleBlock(
            id=uuid.uuid4(), schedule_id=schedule.id, name=name, recurrence_type=RecurrenceType.DAILY,
            start_time=time((local.hour + a) % 24), end_time=time((local.hour + b) % 24),
            created_at=HOUR - timedelta(days=3),
        )
        db.add(block)
        ids[name] = str(block.id)
    await db.flush()
    return ids


@pytest.fixture
def redraws(monkeypatch):
    from app.services import hour_fit

    asked = []
    monkeypatch.setattr(hour_fit, "request_redraw", lambda station_id: asked.append(str(station_id)))
    return asked


@pytest.mark.asyncio
async def test_songs_of_a_block_that_ends_at_the_hard_break_do_not_air_after_it(db_session: AsyncSession, redraws):
    """Carlebach ends at the hour: a Carlebach song the queue now puts after the
    break, before the night block's songs, is taken out."""
    station = await _station(db_session)
    sid = station.id
    ids = await _blocks(db_session, station, {"carlebach": (-1, 0), "night": (0, 3)})
    on_air_ends = HOUR - timedelta(minutes=8)
    await _queue(db_session, station, on_air_ends, [
        ("Carlebach 1", 240.0, {"block_id": ids["carlebach"]}),
        ("Carlebach 2", 260.0, {"block_id": ids["carlebach"]}),  # on air at the hour, loses ~14 s
        ("Carlebach 3", 200.0, {"block_id": ids["carlebach"]}),  # would air at 1:01
        ("Night 1", 240.0, {"block_id": ids["night"]}),
        ("Night 2", 240.0, {"block_id": ids["night"]}),
    ])
    assert await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(minutes=2))
    await db_session.commit()
    assert await _order(db_session, sid) == ["Carlebach 1", "Carlebach 2", "Night 1", "Night 2"]
    # nothing more to do on the next look, and no redraw: the night songs are right
    assert not await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(minutes=2))
    assert redraws == []


@pytest.mark.asyncio
async def test_a_block_that_goes_on_past_the_hour_keeps_its_songs(db_session: AsyncSession, redraws):
    """Live 2026-11-23 4 AM: Night instrumental runs to 6 AM and the 6 AM block's songs
    sat within the look. Every 4-6 AM song was taken out; now none is."""
    station = await _station(db_session)
    sid = station.id
    ids = await _blocks(db_session, station, {"instrumental": (-1, 2), "morning": (2, 5)})
    on_air_ends = HOUR - timedelta(minutes=8)
    songs = [("Instr 1", 240.0), ("Instr 2", 260.0)] + [(f"Instr {i}", 240.0) for i in range(3, 33)]
    await _queue(db_session, station, on_air_ends, [
        *[(t, s, {"block_id": ids["instrumental"]}) for t, s in songs],
        ("Morning 1", 240.0, {"block_id": ids["morning"]}),
        ("Morning 2", 240.0, {"block_id": ids["morning"]}),
    ])
    for look in range(3):
        await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(minutes=2))
        await db_session.commit()
    assert await _order(db_session, sid) == [t for t, _ in songs] + ["Morning 1", "Morning 2"]
    assert redraws == []


@pytest.mark.asyncio
async def test_songs_drawn_for_a_later_block_after_the_break_are_drawn_again(db_session: AsyncSession, redraws):
    """The queue Main was left with on 2026-11-23: the 6 AM block's songs right after
    the 4 AM break. The station's automatic songs are drawn again, once."""
    station = await _station(db_session)
    sid = station.id
    ids = await _blocks(db_session, station, {"instrumental": (-1, 2), "morning": (2, 5)})
    on_air_ends = HOUR - timedelta(minutes=8)
    await _queue(db_session, station, on_air_ends, [
        ("Instr 1", 240.0, {"block_id": ids["instrumental"]}),
        ("Instr 2", 260.0, {"block_id": ids["instrumental"]}),
        ("Morning 1", 240.0, {"block_id": ids["morning"]}),
        ("Morning 2", 240.0, {"block_id": ids["morning"]}),
    ])
    await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(minutes=2))
    await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(minutes=2))
    assert redraws == [str(sid)]
    # a fill that still disagreed would not bring a second redraw within 30 minutes
    from app.services import hour_fit

    hour_fit._redraw_asked.pop(str(sid), None)
    await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(minutes=2))
    assert redraws == [str(sid)]
    # nothing taken out by the hour fit itself
    assert await _order(db_session, sid) == ["Instr 1", "Instr 2", "Morning 1", "Morning 2"]


@pytest.mark.asyncio
async def test_a_persons_pick_after_the_break_is_never_taken_out(db_session: AsyncSession, redraws):
    station = await _station(db_session)
    sid = station.id
    ids = await _blocks(db_session, station, {"carlebach": (-1, 0), "night": (0, 3)})
    on_air_ends = HOUR - timedelta(minutes=8)
    await _queue(db_session, station, on_air_ends, [
        ("Carlebach 1", 240.0, {"block_id": ids["carlebach"]}),
        ("Carlebach 2", 260.0, {"block_id": ids["carlebach"]}),
        ("Requested", 200.0, {"source": "manual"}),
        ("Carlebach 3", 200.0, {"block_id": ids["carlebach"]}),
        ("Night 1", 240.0, {"block_id": ids["night"]}),
    ])
    await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(minutes=2))
    await db_session.commit()
    assert "Requested" in await _order(db_session, sid)
    assert redraws == []


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


@pytest.mark.asyncio
async def test_leftovers_go_while_the_song_before_the_hour_is_on_air(db_session: AsyncSession, redraws):
    """Simulator 2026-11-23: the redraw 15 min before 1 AM and a fit left a Carlebach
    song behind the 1 AM break, and the look that would take it out stopped because
    the song on air ran to the hour. It goes then too."""
    from sqlalchemy import update

    station = await _station(db_session)
    sid = station.id
    ids = await _blocks(db_session, station, {"carlebach": (-1, 0), "night": (0, 3)})
    on_air_ends = HOUR + timedelta(seconds=20)  # the song on air is faded at the hour
    await _queue(db_session, station, on_air_ends, [
        ("Carlebach 25", 230.0, {"block_id": ids["carlebach"]}),
        ("Night 1", 240.0, {"block_id": ids["night"]}),
        ("Night 2", 240.0, {"block_id": ids["night"]}),
    ])
    await db_session.execute(
        update(QueueEntry).where(QueueEntry.station_id == sid, QueueEntry.status == "playing")
        .values(block_id=ids["carlebach"])
    )
    await db_session.commit()
    assert await fit_before_hard_item(db_session, sid, HOUR - timedelta(minutes=3))
    await db_session.commit()
    assert await _order(db_session, sid) == ["Night 1", "Night 2"]
    assert redraws == []


async def _ended_queue(db_session, on_air_ends, songs):
    station = await _station(db_session)
    sid = station.id
    ids = await _blocks(db_session, station, {"night": (-1, 0), "carlebach": (0, 1)})
    await _queue(db_session, station, on_air_ends, [(t, s, {"block_id": ids[b]}) for t, s, b in songs])
    return sid


async def _drop_ended(db_session, sid, now):
    from app.services.hour_fit import drop_songs_of_ended_blocks

    station = await db_session.get(Station, sid, populate_existing=True)
    changed = await drop_songs_of_ended_blocks(db_session, station, now, force=True)
    await db_session.commit()
    return changed


@pytest.mark.asyncio
async def test_night_songs_that_would_start_after_the_carlebach_half_hour_begins_give_way(db_session):
    """Simulator 2026-11-23: two Night Chill songs aired at 12:30:43 and 12:37:13."""
    sid = await _ended_queue(db_session, HOUR + timedelta(seconds=40), [
        ("Night A", 390.0, "night"), ("Night B", 240.0, "night"),
        ("Carlebach 1", 260.0, "carlebach"), ("Carlebach 2", 260.0, "carlebach"),
    ])
    assert await _drop_ended(db_session, sid, HOUR - timedelta(minutes=4))
    assert await _order(db_session, sid) == ["Carlebach 1", "Carlebach 2"]


@pytest.mark.asyncio
async def test_the_song_that_starts_before_the_block_change_stays(db_session):
    sid = await _ended_queue(db_session, HOUR - timedelta(seconds=60), [
        ("Night A", 240.0, "night"), ("Carlebach 1", 260.0, "carlebach"),
    ])
    assert not await _drop_ended(db_session, sid, HOUR - timedelta(minutes=4))
    assert await _order(db_session, sid) == ["Night A", "Carlebach 1"]


@pytest.mark.asyncio
async def test_nothing_goes_without_a_song_of_the_block_that_is_on(db_session):
    sid = await _ended_queue(db_session, HOUR + timedelta(seconds=40), [
        ("Night A", 240.0, "night"), ("Night B", 240.0, "night"),
    ])
    assert not await _drop_ended(db_session, sid, HOUR - timedelta(minutes=4))
    assert await _order(db_session, sid) == ["Night A", "Night B"]


@pytest.mark.asyncio
async def test_a_song_about_to_start_is_left_alone(db_session):
    sid = await _ended_queue(db_session, HOUR + timedelta(seconds=40), [
        ("Night A", 240.0, "night"), ("Carlebach 1", 260.0, "carlebach"),
    ])
    assert not await _drop_ended(db_session, sid, HOUR + timedelta(seconds=30))
    assert await _order(db_session, sid) == ["Night A", "Carlebach 1"]
