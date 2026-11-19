"""Station IDs between songs (automation_config.station_ids): a short jingle
sprinkled after a song every min..max minutes, never close to a timed break,
never two spoken items together. See wiki/playlist-generation.md."""
import random
import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.services.playlist_generator_service import (
    Clock,
    TimedItem,
    generate,
    id_settings_for,
)
from tests.test_playlist_generation import MONDAY_7AM, _setup

IDS = {"enabled": True, "min_minutes": 12, "max_minutes": 20}


def _jingle(n: int, category: str = "station_id") -> Asset:
    return Asset(
        id=uuid.uuid4(), title=f"Station ID {n}", duration=8.0, file_path=f"{uuid.uuid4().hex}.mp3",
        asset_type="jingle", category=category,
    )


async def _station_with_ids(db: AsyncSession, ids: dict | None, jingles: int = 6):
    station, songs, morning, evening = await _setup(db, {"station_ids": ids} if ids is not None else None)
    rows = [_jingle(n) for n in range(jingles)]
    db.add_all(rows)
    await db.commit()
    return station, rows


def test_settings_defaults_and_limits():
    assert id_settings_for(None).enabled is False
    s = id_settings_for({"station_ids": {"enabled": True, "min_minutes": 30, "max_minutes": 10}})
    assert s.enabled and s.min_minutes == 30 and s.max_minutes == 30  # max never under min
    s = id_settings_for({"station_ids": {"enabled": True, "min_minutes": "x", "category": "  "}})
    assert s.min_minutes == 12 and s.category == "station_id"
    assert id_settings_for({"station_ids": {"min_minutes": 0.5}}).min_minutes == 3


@pytest.mark.asyncio
async def test_off_by_default_no_ids(db_session: AsyncSession):
    station, _ = await _station_with_ids(db_session, None)
    plan = await generate(db_session, station, MONDAY_7AM, 3, artist_distance=0)
    assert all(p.asset_type == "music" for p in plan.picks)


@pytest.mark.asyncio
async def test_ids_are_spaced_rotated_and_only_after_songs(db_session: AsyncSession):
    random.seed(3)
    station, jingles = await _station_with_ids(db_session, IDS)
    plan = await generate(db_session, station, MONDAY_7AM, 4, artist_distance=0)
    ids = [p for p in plan.picks if p.asset_type == "jingle"]
    # four hours, one every 12 to 20 minutes after the song that passes the mark
    assert 10 <= len(ids) <= 21, len(ids)
    for a, b in zip(ids, ids[1:]):
        gap = (b.at - a.at).total_seconds() / 60
        assert 12 <= gap <= 20 + 4.5, gap  # plus at most one song
    # never two IDs together, never first
    for i, p in enumerate(plan.picks):
        if p.asset_type == "jingle":
            assert i > 0 and plan.picks[i - 1].asset_type == "music"
    # rotation: the six IDs are all used before any comes back
    first_six = [p.asset_id for p in ids[:6]]
    assert len(set(first_six)) == 6
    assert "Station ID" in {p.block_name for p in ids}


@pytest.mark.asyncio
async def test_ids_keep_clear_of_timed_breaks(db_session: AsyncSession):
    random.seed(5)
    station, _ = await _station_with_ids(db_session, {**IDS, "min_minutes": 5, "max_minutes": 6})
    # a break at every :00, :15, :30, :45 for three hours (60 s each)
    timed = [TimedItem(MONDAY_7AM + timedelta(minutes=15 * k), 60.0, k % 2 == 0) for k in range(1, 13)]
    plan = await generate(db_session, station, MONDAY_7AM, 3, artist_distance=0, timed=list(timed))
    ids = [p for p in plan.picks if p.asset_type == "jingle"]
    assert ids
    margin = timedelta(minutes=3)
    for p in ids:
        for t in timed:
            ends = t.at + timedelta(seconds=t.seconds)
            assert not (t.at - margin < p.at + timedelta(seconds=8) and p.at < ends + margin), (p.at, t.at)


@pytest.mark.asyncio
async def test_spacing_counts_from_the_last_id_already_queued(db_session: AsyncSession):
    random.seed(7)
    station, jingles = await _station_with_ids(db_session, IDS, jingles=2)
    # one ID waits at the end of the queue: the next one is at least 12 minutes later
    plan = await generate(
        db_session, station, MONDAY_7AM, 1, artist_distance=0,
        queued=[jingles[0].id], busy_ids={jingles[0].id},
    )
    first = next(p for p in plan.picks if p.asset_type == "jingle")
    assert first.at - MONDAY_7AM >= timedelta(minutes=12) - timedelta(seconds=240)
    assert first.asset_id == jingles[1].id  # the other one, not the one waiting


@pytest.mark.asyncio
async def test_no_id_right_after_a_spoken_item_or_a_break_before_the_fill(db_session: AsyncSession):
    random.seed(9)
    station, _ = await _station_with_ids(db_session, {**IDS, "min_minutes": 3, "max_minutes": 3})
    plan = await generate(db_session, station, MONDAY_7AM, 0.3, artist_distance=0,
                          after_type="jingle", timed_ended=MONDAY_7AM - timedelta(seconds=30))
    assert plan.picks[0].asset_type == "music"
    first = next((p for p in plan.picks if p.asset_type == "jingle"), None)
    assert first is None or first.at >= MONDAY_7AM + timedelta(seconds=150)


@pytest.mark.asyncio
async def test_on_with_no_jingles_says_so(db_session: AsyncSession):
    station, songs, *_ = await _setup(db_session, {"station_ids": IDS})
    plan = await generate(db_session, station, MONDAY_7AM, 1, artist_distance=0)
    assert all(p.asset_type == "music" for p in plan.picks)
    assert any("station_id" in n for n in plan.notes)


def test_clock_clear_of_timed():
    clock = Clock(MONDAY_7AM, [TimedItem(MONDAY_7AM + timedelta(minutes=10), 60.0, True)])
    m = timedelta(minutes=3)
    assert clock.clear_of_timed(MONDAY_7AM, 10, m)
    assert not clock.clear_of_timed(MONDAY_7AM + timedelta(minutes=7), 10, m)
    clock.at = MONDAY_7AM + timedelta(minutes=10)
    clock.start()
    assert not clock.clear_of_timed(clock.at, 10, m)  # right after the break (ends 11:00)
    assert clock.clear_of_timed(MONDAY_7AM + timedelta(minutes=14, seconds=1), 10, m)


@pytest.mark.asyncio
async def test_queue_fill_puts_ids_between_songs_and_a_top_up_keeps_the_spacing(
    db_session: AsyncSession, monkeypatch
):
    from datetime import datetime

    from sqlalchemy import select

    import app.services.queue_replenish_service as qrs
    from app.models.queue_entry import QueueEntry
    from app.services.queue_replenish_service import QueueReplenishService

    random.seed(11)
    station, jingles = await _station_with_ids(db_session, IDS)
    station.automation_config = {
        **station.automation_config, "playlist_generation": {"enabled": True, "hours": 2},
    }
    await db_session.commit()

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return MONDAY_7AM

    monkeypatch.setattr(qrs, "datetime", _Clock)
    await QueueReplenishService(db_session, station.id).replenish()
    jingle_ids = {j.id for j in jingles}

    async def queue():
        return (await db_session.execute(
            select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
            .order_by(QueueEntry.position)
        )).scalars().all()

    entries = await queue()
    marks = [i for i, e in enumerate(entries) if e.asset_id in jingle_ids]
    assert 4 <= len(marks) <= 11, marks
    assert all(entries[i].source == "auto" and entries[i].preempt_at is None for i in marks)
    assert all(b - a >= 3 for a, b in zip(marks, marks[1:]))  # 12+ minutes = 3+ songs
    assert marks[0] > 0 and all(entries[i - 1].asset_id not in jingle_ids for i in marks)

    # play 40 minutes off the top, then top up: the join keeps at least three songs between IDs
    for e in entries[:10]:
        e.status = "played"
    await db_session.commit()
    monkeypatch.setattr(qrs, "TOP_UP_SLACK_SECONDS", 0)
    await QueueReplenishService(db_session, station.id).replenish()
    entries = await queue()
    marks = [i for i, e in enumerate(entries) if e.asset_id in jingle_ids]
    assert all(b - a >= 3 for a, b in zip(marks, marks[1:])), marks


async def _stories_with_ids(db: AsyncSession, ids: dict):
    from app.models.schedule_rule import ScheduleRule
    from app.models.station import Station

    station = Station(id=uuid.uuid4(), name=f"Stories {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      is_active=True, automation_config={"station_ids": ids})
    db.add(station)
    await db.flush()
    db.add(ScheduleRule(
        id=uuid.uuid4(), name="All day stories", rule_type="daypart", asset_type="story_ids",
        hour_start=0, hour_end=24, days_of_week="0,1,2,3,4,5,6", is_active=True, priority=10,
        station_id=station.id,
    ))
    for n in range(12):
        db.add(Asset(id=uuid.uuid4(), title=f"Story {n}", duration=600.0 + 60 * n,
                     file_path=f"{uuid.uuid4().hex}.mp3", asset_type="story_ids"))
    jingles = [_jingle(n) for n in range(4)]
    db.add_all(jingles)
    await db.commit()
    return station, {j.id for j in jingles}


async def _pending(db: AsyncSession, station_id):
    from sqlalchemy import select

    from app.models.queue_entry import QueueEntry

    return (await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position)
    )).scalars().all()


def _gaps_ok(entries, jingle_ids, durations, low_minutes):
    """IDs never together, never first, and at least low_minutes apart by running time."""
    t = 0.0
    last = None
    for i, e in enumerate(entries):
        if e.asset_id in jingle_ids:
            assert i > 0 and entries[i - 1].asset_id not in jingle_ids
            if last is not None:
                assert t - last >= low_minutes * 60 - 1, (t - last) / 60
            last = t
        t += durations[e.asset_id]


@pytest.mark.asyncio
async def test_rule_filled_station_gets_ids_between_stories(db_session: AsyncSession):
    from sqlalchemy import select

    from app.services.queue_replenish_service import QueueReplenishService

    random.seed(13)
    station, jingle_ids = await _stories_with_ids(db_session, {"enabled": True, "min_minutes": 25, "max_minutes": 35})
    await QueueReplenishService(db_session, station.id).replenish()
    entries = await _pending(db_session, station.id)
    durations = dict((await db_session.execute(select(Asset.id, Asset.duration))).all())
    n_ids = sum(e.asset_id in jingle_ids for e in entries)
    stories = len(entries) - n_ids
    assert stories > 100 and n_ids > stories // 4, (stories, n_ids)
    _gaps_ok(entries, jingle_ids, durations, 25 - 1)  # the clock counts each item a few seconds short of its length
    # every ID gets its turn
    used = [e.asset_id for e in entries if e.asset_id in jingle_ids]
    assert set(used[:4]) == jingle_ids


@pytest.mark.asyncio
async def test_rule_fill_off_by_default_and_hours_window(db_session: AsyncSession):
    from app.services.queue_replenish_service import QueueReplenishService

    station, jingle_ids = await _stories_with_ids(db_session, {"enabled": False})
    await QueueReplenishService(db_session, station.id).replenish()
    assert not any(e.asset_id in jingle_ids for e in await _pending(db_session, station.id))

    s = id_settings_for({"station_ids": {"enabled": True, "start_hour": 6, "end_hour": 22}})
    assert s.in_hours(6) and s.in_hours(21) and not s.in_hours(22) and not s.in_hours(3)
    night = id_settings_for({"station_ids": {"start_hour": 22, "end_hour": 6}})
    assert night.in_hours(23) and night.in_hours(2) and not night.in_hours(12)
    assert id_settings_for({"station_ids": {"start_hour": 5, "end_hour": 5}}).in_hours(13)


@pytest.mark.asyncio
async def test_playlist_fill_keeps_ids_inside_the_hours(db_session: AsyncSession):
    from zoneinfo import ZoneInfo

    random.seed(17)
    station, _ = await _station_with_ids(db_session, {**IDS, "min_minutes": 5, "max_minutes": 6,
                                                       "start_hour": 8, "end_hour": 10})
    plan = await generate(db_session, station, MONDAY_7AM, 4, artist_distance=0)
    hours = {p.at.astimezone(ZoneInfo("America/New_York")).hour for p in plan.picks if p.asset_type == "jingle"}
    assert hours and hours <= {8, 9}, hours


# --- First switch-on, 2026-11-19: station IDs drawn by "any jingle" picks ---------


@pytest.mark.asyncio
async def test_a_jingle_step_with_no_category_never_draws_a_station_id(db_session: AsyncSession):
    """A playlist step asking for "a jingle" took KBR Jerusalem's ID onto Main, with the
    setting off. Station IDs air only through their own rule (or a step naming them)."""
    from app.models.playlist_template import TemplateSlot

    random.seed(21)
    station, songs, morning, evening = await _setup(db_session, None)
    db_session.add(TemplateSlot(id=uuid.uuid4(), template_id=morning.playlist_template_id,
                                position=4, asset_type="jingle", category=None))
    main_ids = [_jingle(n) for n in range(3)]
    jerusalem = [_jingle(n, "station_id_jerusalem") for n in range(2)]
    sweep = Asset(id=uuid.uuid4(), title="Sweeper", duration=5.0, file_path="s.mp3",
                  asset_type="jingle", category="sweepers")
    db_session.add_all([*main_ids, *jerusalem, sweep])
    await db_session.commit()

    plan = await generate(db_session, station, MONDAY_7AM, 3, artist_distance=0)
    jingles = [p.asset_id for p in plan.picks if p.asset_type == "jingle"]
    assert jingles and set(jingles) == {sweep.id}

    # Turned on: only this station's own IDs, never Jerusalem's, never two short items together
    station.automation_config = {"station_ids": {**IDS, "min_minutes": 5, "max_minutes": 6}}
    await db_session.commit()
    plan = await generate(db_session, station, MONDAY_7AM, 3, artist_distance=0)
    picked = {p.asset_id for p in plan.picks}
    assert not picked & {a.id for a in jerusalem}
    assert picked & {a.id for a in main_ids}
    for a, b in zip(plan.picks, plan.picks[1:]):
        assert not (a.asset_type == "jingle" and b.asset_type == "jingle"), (a.title, b.title)


@pytest.mark.asyncio
async def test_a_generic_jingle_rule_never_draws_a_station_id(db_session: AsyncSession):
    """The seeded "Jingle Every 30 Min" rule (any jingle) queued station IDs on All Day
    Stories with the setting off; it may still play other jingles."""
    from app.models.schedule_rule import ScheduleRule
    from app.services.queue_replenish_service import QueueReplenishService

    station, jingle_ids = await _stories_with_ids(db_session, {"enabled": False})
    jerusalem = _jingle(9, "station_id_jerusalem")
    sweep = Asset(id=uuid.uuid4(), title="Sweeper", duration=5.0, file_path="s2.mp3",
                  asset_type="jingle", category="sweepers")
    db_session.add_all([jerusalem, sweep])
    db_session.add(ScheduleRule(
        id=uuid.uuid4(), name="Jingle Every 30 Min", rule_type="interval", asset_type="jingle",
        interval_minutes=30, hour_start=0, hour_end=24, days_of_week="0,1,2,3,4,5,6",
        is_active=True, priority=40, station_id=station.id,
    ))
    await db_session.commit()
    await QueueReplenishService(db_session, station.id).replenish()
    queued = {e.asset_id for e in await _pending(db_session, station.id)}
    assert sweep.id in queued
    assert not queued & (jingle_ids | {jerusalem.id})


@pytest.mark.asyncio
async def test_a_block_jingle_item_with_no_category_never_draws_a_station_id(db_session: AsyncSession):
    from app.services.program_block_service import _Resolver
    from tests.test_program_blocks import _library

    station, made = await _library(db_session)
    sweep = Asset(id=uuid.uuid4(), title="Sweeper", duration=5.0, file_path="s3.mp3",
                  asset_type="jingle", category="sweepers")
    db_session.add(sweep)
    await db_session.commit()
    resolver = _Resolver(db_session, station, dry_run=True)
    at = MONDAY_7AM
    drawn = {getattr(await resolver._rotate("jingle", None, at + timedelta(hours=h)), "id", None) for h in range(4)}
    assert drawn == {sweep.id}, drawn  # never made["id"], the station_id jingle
    named = await resolver._rotate("jingle", "station_id", at)
    assert named is not None and named.id == made["id"].id  # naming the category still works
