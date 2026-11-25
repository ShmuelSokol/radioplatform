"""Main's first Carlebach half hour (12:30-1 AM, 2026-11-23) aired three things it
should not have:

1. a Chanukah song (the holiday mix, in season since 21 November) inside a block
   whose playlist plays only Carlebach;
2. a Carlebach song at 1:01, after the block ended at the 1 AM hard break;
3. its 12:30 sponsorships waited for the song on air, which a long song could carry
   past 12:35, when the quiet hours start. PR 498 faded the song at 12:35 for them,
   which cut a 7:45 Carlebach song mid-way; now such a break is skipped instead.
"""
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
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
from app.services.spot_schedule_service import clear_sponsor_wait_caps, skip_sponsors_past_quiet
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
    break, before the night block's songs, is taken out once the song before the hour
    is about to start (until then the fit may need it)."""
    station = await _station(db_session)
    sid = station.id
    ids = await _blocks(db_session, station, {"carlebach": (-1, 0), "night": (0, 3)})
    on_air_ends = HOUR - timedelta(minutes=4)
    await _queue(db_session, station, on_air_ends, [
        ("Carlebach 2", 260.0, {"block_id": ids["carlebach"]}),  # on air at the hour, loses ~14 s
        ("Carlebach 3", 200.0, {"block_id": ids["carlebach"]}),  # would air at 1:01
        ("Night 1", 240.0, {"block_id": ids["night"]}),
        ("Night 2", 240.0, {"block_id": ids["night"]}),
    ])
    assert not await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(minutes=2))
    await db_session.commit()
    assert "Carlebach 3" in await _order(db_session, sid)
    assert await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(seconds=60))
    await db_session.commit()
    assert await _order(db_session, sid) == ["Carlebach 2", "Night 1", "Night 2"]
    # nothing more to do on the next look, and no redraw: the night songs are right
    assert not await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(seconds=60))
    assert redraws == []


@pytest.mark.asyncio
async def test_the_block_songs_after_the_break_are_kept_for_the_fit(db_session: AsyncSession, redraws):
    """Main 2026-11-24 0:59:30: the Carlebach songs after 1 AM had been taken out before
    the fit looked, so it had nothing to swap and the 1:00 time check cut a 4:22 song
    after 30 s. Kept until the run-up is settled, one of them fills the place."""
    station = await _station(db_session)
    sid = station.id
    ids = await _blocks(db_session, station, {"carlebach": (-1, 0), "night": (0, 3)})
    on_air_ends = HOUR - timedelta(minutes=4)
    await _queue(db_session, station, on_air_ends, [
        ("Carlebach 2", 213.0, {"block_id": ids["carlebach"]}),  # ends 30 s before the hour
        ("Lulei Torascha", 262.0, {"block_id": ids["carlebach"]}),  # would start at 0:59:30
        ("Carlebach 4", 243.0, {"block_id": ids["carlebach"]}),  # ends right at the hour
        ("Night 1", 240.0, {"block_id": ids["night"]}),
        ("Night 2", 240.0, {"block_id": ids["night"]}),
    ])
    assert await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(minutes=3))
    await db_session.commit()
    first = (await _order(db_session, sid))[0]
    assert first in ("Lulei Torascha", "Carlebach 4")  # one song that runs to the hour
    for _ in range(3):
        await fit_before_hard_item(db_session, sid, on_air_ends - timedelta(seconds=60))
        await db_session.commit()
    assert await _order(db_session, sid) == [first, "Night 1", "Night 2"]
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


def _night_local(h, m, s=0):
    night = (datetime.now(NY) + timedelta(days=1)).date()
    return datetime(night.year, night.month, night.day, h, m, s, tzinfo=NY).astimezone(UTC)


async def _break_after_song(db: AsyncSession, station, spot, other, song_seconds: float):
    """A song on air from 12:29 and the 12:30 soft break waiting for it: a sponsorship
    and a promo, plus a sponsorship break at 12:45 (in the quiet hours already)."""
    song = _asset("Long Carlebach", category="Carlebach", duration=song_seconds)
    db.add(song)
    await db.flush()
    rows = {
        "song": QueueEntry(asset_id=song.id, status="playing", started_at=_night_local(0, 29), source="auto"),
        "12:30 sponsor": QueueEntry(asset_id=spot.id, preempt_at=_night_local(0, 30), source="block",
                                    timing_mode="soft"),
        "12:30 promo": QueueEntry(asset_id=other.id, preempt_at=_night_local(0, 30), source="block",
                                  timing_mode="soft"),
        "1:00 hard": QueueEntry(asset_id=spot.id, preempt_at=_night_local(1, 0), source="block", timing_mode="hard"),
    }
    for i, entry in enumerate(rows.values()):
        entry.id, entry.station_id, entry.position = uuid.uuid4(), station.id, i + 1
        entry.status = entry.status or "pending"
        db.add(entry)
    await db.flush()
    return {k: e.id for k, e in rows.items()}


async def _statuses(db: AsyncSession, ids):
    db.expire_all()
    return {k: (await db.get(QueueEntry, i)).status for k, i in ids.items()}


@pytest.mark.asyncio
async def test_a_sponsorship_break_the_song_carries_into_the_quiet_hours_is_skipped_not_cut(db_session: AsyncSession):
    """Main 2026-11-23 12:35 AM: the 12:30 sponsorships waited for a 7:45 Carlebach song
    and then cut it at 12:35. Now the song plays to its end and the break's
    sponsorships are skipped (missed plays); the promo still plays after the song."""
    station, spot, other = await _sponsor_station(db_session)
    sid = station.id
    ids = await _break_after_song(db_session, station, spot, other, 465.0)  # ends 12:36:45
    assert await skip_sponsors_past_quiet(db_session, station, _night_local(0, 31)) == 1
    got = await _statuses(db_session, ids)
    assert got == {"song": "playing", "12:30 sponsor": "cancelled", "12:30 promo": "pending",
                   "1:00 hard": "pending"}
    for i in ids.values():
        assert (await db_session.get(QueueEntry, i)).hard_by is None  # nothing is cut
    station = await db_session.get(Station, sid)
    assert await skip_sponsors_past_quiet(db_session, station, _night_local(0, 32)) == 0


@pytest.mark.asyncio
async def test_a_break_that_starts_before_the_quiet_hours_airs(db_session: AsyncSession):
    station, spot, other = await _sponsor_station(db_session)
    ids = await _break_after_song(db_session, station, spot, other, 300.0)  # ends 12:34
    assert await skip_sponsors_past_quiet(db_session, station, _night_local(0, 31)) == 0
    assert (await _statuses(db_session, ids))["12:30 sponsor"] == "pending"


@pytest.mark.asyncio
async def test_no_quiet_hours_no_skip(db_session: AsyncSession):
    station, spot, other = await _sponsor_station(db_session)
    station.automation_config = {}
    ids = await _break_after_song(db_session, station, spot, other, 465.0)
    assert await skip_sponsors_past_quiet(db_session, station, _night_local(0, 31)) == 0
    assert (await _statuses(db_session, ids))["12:30 sponsor"] == "pending"


@pytest.mark.asyncio
async def test_the_old_fade_limit_at_the_quiet_hours_is_taken_off(db_session: AsyncSession):
    """Entries queued while PR 498's cap was live carry hard_by = 12:35; that comes off,
    a block's own earlier limit stays."""
    station, spot, other = await _sponsor_station(db_session)
    now = datetime.now(UTC)
    rows = {
        "capped": QueueEntry(asset_id=spot.id, preempt_at=_night_local(0, 30), source="block", timing_mode="soft",
                             hard_by=_night_local(0, 35)),
        "own limit": QueueEntry(asset_id=spot.id, preempt_at=_night_local(0, 30), source="block",
                                timing_mode="soft", hard_by=_night_local(0, 33)),
    }
    for i, entry in enumerate(rows.values()):
        entry.id, entry.station_id, entry.position, entry.status = uuid.uuid4(), station.id, i + 1, "pending"
        db_session.add(entry)
    await db_session.flush()
    ids = {k: e.id for k, e in rows.items()}
    assert await clear_sponsor_wait_caps(db_session, station, now) == 1
    db_session.expire_all()
    got = {k: (await db_session.get(QueueEntry, i)).hard_by for k, i in ids.items()}
    assert got["capped"] is None
    assert got["own limit"].replace(tzinfo=UTC) == _night_local(0, 33)


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


@pytest.mark.asyncio
async def test_a_waiting_song_is_not_drawn_again_for_the_artist_rule(db_session: AsyncSession):
    """Main 2026-11-24: "Kol Birama" aired at 0:34 and 0:52. Most Carlebach songs share
    one artist; the one by another artist was the only song clear of the artist rule,
    and the draw took it again although it was already waiting. Now the artist rule
    gives way while songs that are not waiting remain."""
    from app.services.playlist_generator_service import _Library

    rows = [_asset(f"Carlebach {i}", category="Carlebach") for i in range(30)]
    for r in rows:
        r.artist = "Shlomo Carlebach"
    other = _asset("Kol Birama", category="Carlebach")
    other.artist = "Other singer"
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", is_active=True)
    db_session.add_all([station, other, *rows])
    await db_session.commit()
    library = _Library(db_session, station.id, set())
    at = datetime(2026, 11, 24, 5, 34, tzinfo=UTC)
    got, _ = await library.draw("music", "Carlebach", {"shlomo carlebach"}, at)
    assert got.title == "Kol Birama"  # the only one clear of the artist rule
    got, relaxed = await library.draw("music", "Carlebach", {"shlomo carlebach"}, at + timedelta(minutes=18))
    assert got.title != "Kol Birama" and relaxed
