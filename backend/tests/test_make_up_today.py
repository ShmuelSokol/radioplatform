"""A plan changed mid-day never sends today's remaining plays into the past.

Main 2026-11-18: sponsor places were added at :00 and :15 at 5:41 PM; the re-plan
put Hatzalah (3 to 6 PM slot, due 5:45 PM) on a 3 PM break that had gone, so it
would not have aired that day.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.models.asset import Asset
from app.models.play_log import PlayLog, PlaySource
from app.models.station import Station
from app.services.spot_schedule_service import Break, aired_today, plan_spots, settle_today

NY = ZoneInfo("America/New_York")
TODAY = datetime(2026, 11, 18, tzinfo=NY).date()


def _spot(name, dayparts, sponsor=None):
    return SimpleNamespace(
        id=name, created_at=name, is_active=True, start_date=None, end_date=None,
        days_of_week="0,1,2,3,4,5,6", dayparts=dayparts, plays_per_daypart=1,
        asset_id=uuid.uuid4(), sponsor_id=sponsor or f"sponsor-{name}", name=name,
        fixed_times=None, timing_mode=None,
    )


def _breaks(h_start, h_end):
    return [
        Break(datetime(TODAY.year, TODAY.month, TODAY.day, h, m, tzinfo=NY), 3, "soft", "TEST block", None, None)
        for h in range(h_start, h_end)
        for m in (0, 15, 30, 45)
    ]


async def _station(db):
    st = Station(id=uuid.uuid4(), name="TEST make-up", timezone="America/New_York", is_active=True)
    db.add(st)
    await db.commit()
    return st


def _local(h, m):
    return datetime(TODAY.year, TODAY.month, TODAY.day, h, m, tzinfo=NY)


@pytest.mark.asyncio
async def test_a_play_planned_on_a_gone_break_moves_to_a_later_one_in_its_slot(db_session):
    st = await _station(db_session)
    hatzalah = _spot("hatzalah", "late_afternoon")
    breaks = _breaks(15, 18)
    placed, _ = plan_spots([hatzalah], NY, TODAY, 1, breaks=breaks)
    now = _local(17, 41).astimezone(UTC)
    assert placed[0].local < now  # the plan alone puts it in the past
    out = settle_today(placed, breaks, [], NY, now, await aired_today(db_session, st, NY, now, [hatzalah]))
    p = next(x for x in out if x.schedule is hatzalah)
    assert p.local == _local(17, 45) and p.block == "TEST block" and p.moved_from is None


@pytest.mark.asyncio
async def test_a_play_that_already_aired_in_its_slot_is_not_made_up(db_session):
    st = await _station(db_session)
    spot = _spot("aired", "late_afternoon")
    breaks = _breaks(15, 18)
    placed, _ = plan_spots([spot], NY, TODAY, 1, breaks=breaks)
    at = _local(15, 30).astimezone(UTC)
    db_session.add(Asset(id=spot.asset_id, title="TEST aired", file_path="a.mp3", duration=30.0, asset_type="spot"))
    await db_session.flush()
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=st.id, asset_id=spot.asset_id, start_utc=at,
                           end_utc=at + timedelta(seconds=30), source=PlaySource.AD))
    await db_session.commit()
    now = _local(17, 41).astimezone(UTC)
    aired = await aired_today(db_session, st, NY, now, [spot])
    assert aired == {str(spot.asset_id): [_local(15, 30)]}
    out = settle_today(placed, breaks, [], NY, now, aired)
    assert [p.local for p in out] == [p.local for p in placed]


def test_with_its_slot_over_a_play_is_not_pushed_into_the_evening():
    spot = _spot("late", "late_afternoon")
    breaks = _breaks(15, 20)
    placed, _ = plan_spots([spot], NY, TODAY, 1, breaks=breaks)
    now = _local(18, 5).astimezone(UTC)
    out = settle_today([p for p in placed if p.schedule is spot], breaks, [], NY, now, {})
    assert [p.local for p in out] == [p.local for p in placed if p.schedule is spot]


def test_a_full_later_break_is_skipped():
    spot = _spot("x", "late_afternoon")
    others = [_spot(f"o{i}", "late_afternoon") for i in range(3)]
    breaks = [b for b in _breaks(15, 18) if b.local < _local(17, 0) or b.local >= _local(17, 30)]
    placed, _ = plan_spots([spot], NY, TODAY, 1, breaks=breaks)
    full = [
        type(placed[0])(at=_local(17, 30).astimezone(UTC), local=_local(17, 30), daypart="late_afternoon",
                        schedule=o, block="TEST block", seat=i)
        for i, o in enumerate(others)
    ]
    now = _local(17, 20).astimezone(UTC)
    out = settle_today(placed + full, breaks, [], NY, now, {})
    p = next(x for x in out if x.schedule is spot)
    assert p.local == _local(17, 45)


@pytest.mark.asyncio
async def test_talk_windows_start_after_the_items_before_them_in_their_group(db_session):
    from app.models.queue_entry import QueueEntry
    from app.services.spot_schedule_service import talk_windows

    st = await _station(db_session)
    time_check = Asset(id=uuid.uuid4(), title="TEST time", file_path="t.mp3", duration=10.0, asset_type="announcement")
    intro = Asset(id=uuid.uuid4(), title="TEST Daf intro", file_path="i.mp3", duration=50.0, asset_type="announcement")
    daf = Asset(id=uuid.uuid4(), title="TEST Daf Yomi", file_path="d.mp3", duration=3360.0, asset_type="shiur")
    song = Asset(id=uuid.uuid4(), title="TEST song", file_path="s.mp3", duration=200.0, asset_type="music")
    db_session.add_all([time_check, intro, daf, song])
    await db_session.flush()
    at = _local(20, 0).astimezone(UTC)
    for pos, a in enumerate([time_check, intro, daf], start=1):
        db_session.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=a.id, position=pos,
                                  status="pending", preempt_at=at, source="block"))
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, position=9,
                              status="pending", preempt_at=_local(21, 0).astimezone(UTC)))
    await db_session.commit()
    windows = await talk_windows(db_session, st, NY, _local(19, 0).astimezone(UTC))
    assert windows == [(_local(20, 1), _local(20, 57))]
