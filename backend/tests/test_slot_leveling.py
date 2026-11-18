"""Sponsorships sold into a time slot are levelled over its breaks.

Shmuel 2026-11-18: a contract plays somewhere in a slot of a few hours, not at an
exact minute, and inside the slot every break gets 3 before any gets a 4th; the
next sponsorship goes to the break that has only 2.
"""

from collections import Counter
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.schemas.spot_schedule import SpotScheduleCreate
from app.services.spot_schedule_service import (
    DAYPARTS,
    TIME_SLOTS,
    Break,
    daypart_of,
    plan_spots,
)

NY = ZoneInfo("America/New_York")
DAY = date(2026, 11, 16)


def _spot(name, dayparts, plays=1, fixed_times=None):
    return SimpleNamespace(
        id=name, created_at=name, is_active=True, start_date=None, end_date=None,
        days_of_week="0,1,2,3,4,5,6", dayparts=dayparts, plays_per_daypart=plays,
        asset_id=f"asset-{name}", sponsor_id=f"sponsor-{name}", name=name,
        fixed_times=fixed_times, timing_mode=None,
    )


def _breaks(h_start, h_end, places=10):
    return [
        Break(datetime(DAY.year, DAY.month, DAY.day, h, m, tzinfo=NY), places, "soft", "TEST block", None, None)
        for h in range(h_start, h_end)
        for m in (0, 15, 30, 45)
    ]


def _counts(placed):
    return Counter(p.local for p in placed)


def test_slots_cover_the_day_without_gaps_and_reports_stay_broad():
    hours = sorted(h for a, b in TIME_SLOTS.values() for h in range(a, b))
    assert hours == list(range(6, 24))
    assert {daypart_of(h) for h in range(24)} == {"overnight", "morning", "afternoon", "evening"}
    assert SpotScheduleCreate(sponsor_id=DAY and "00000000-0000-0000-0000-000000000001",
                              asset_id="00000000-0000-0000-0000-000000000002",
                              dayparts="late_morning,early_morning").dayparts == "early_morning,late_morning"


def test_every_break_gets_three_before_any_gets_a_fourth():
    # 6-9 AM has 12 breaks; 38 plays = 3 each plus 2 breaks with a 4th
    spots = [_spot(f"s{i:02d}", "early_morning") for i in range(38)]
    placed, unplaced = plan_spots(spots, NY, DAY, 1, breaks=_breaks(6, 9))
    assert not unplaced
    counts = _counts(placed)
    assert len(counts) == 12
    assert sorted(counts.values()) == [3] * 10 + [4] * 2
    assert all(6 <= p.local.hour < 9 for p in placed)


def test_the_next_sponsorship_goes_to_the_break_that_has_only_two():
    breaks = _breaks(9, 10)  # 9:00, 9:15, 9:30, 9:45
    spots = [_spot(f"s{i}", "late_morning") for i in range(11)]  # 3,3,3,2
    placed, _ = plan_spots(spots, NY, DAY, 1, breaks=breaks)
    before = _counts(placed)
    short = next(at for at, n in before.items() if n == 2)
    placed, _ = plan_spots(spots + [_spot("s99", "late_morning")], NY, DAY, 1, breaks=breaks)
    new = next(p for p in placed if p.schedule.name == "s99")
    assert new.local == short
    assert sorted(_counts(placed).values()) == [3, 3, 3, 3]


def test_a_contracts_own_plays_stay_apart():
    spots = [_spot("a", "early_morning", plays=4)]
    placed, _ = plan_spots(spots, NY, DAY, 1, breaks=_breaks(6, 9))
    times = sorted(p.local for p in placed)
    assert len(set(times)) == 4
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(times, times[1:])]
    assert min(gaps) >= 30


def test_exact_times_still_work_and_count_toward_the_level():
    fixed = _spot("fixed", "early_morning", fixed_times="07:15")
    spread = [_spot(f"s{i}", "early_morning") for i in range(11)]
    placed, unplaced = plan_spots([fixed, *spread], NY, DAY, 1, breaks=_breaks(6, 9))
    assert not unplaced
    assert any(p.schedule.name == "fixed" and p.local.strftime("%H:%M") == "07:15" for p in placed)
    # 12 plays over 12 breaks: one each, the exact one included
    assert sorted(_counts(placed).values()) == [1] * 12


def test_a_narrow_slot_and_a_whole_morning_share_the_breaks_evenly():
    narrow = [_spot(f"n{i}", "early_morning") for i in range(12)]
    wide = [_spot(f"w{i}", "morning") for i in range(12)]
    placed, unplaced = plan_spots(narrow + wide, NY, DAY, 1, breaks=_breaks(6, 12))
    assert not unplaced
    assert all(6 <= p.local.hour < 9 for p in placed if p.schedule.name.startswith("n"))
    # 24 plays over 24 morning breaks: the whole-morning ones fill the empty late morning
    assert sorted(_counts(placed).values()) == [1] * 24


def test_without_blocks_a_slot_spreads_over_its_clock_slots_and_never_doubles_up():
    narrow = [_spot("n", "early_morning", plays=3)]
    wide = [_spot(f"w{i}", "morning", plays=3) for i in range(3)]
    placed, unplaced = plan_spots(narrow + wide, NY, DAY, 1)
    assert not unplaced
    assert len(placed) == 12
    assert len({p.local for p in placed}) == 12  # one sponsorship per clock slot
    assert all(6 <= p.local.hour < 9 for p in placed if p.schedule.name == "n")
    assert set(DAYPARTS) >= set(TIME_SLOTS)


def test_a_spread_play_is_labelled_with_its_contracts_slot():
    placed, _ = plan_spots([_spot("a", "late_morning")], NY, DAY, 1, breaks=_breaks(9, 12))
    assert [p.daypart for p in placed] == ["late_morning"]
