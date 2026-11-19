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


def test_one_sponsors_plays_are_not_in_neighbouring_breaks():
    """Seen on Main 2026-11-18: one sponsor at 7:00, 7:15 and 7:45 PM with other breaks as empty."""
    wide = [_spot("busy", "evening", plays=3)] + [_spot(f"o{i}", "evening") for i in range(9)]
    placed, unplaced = plan_spots(wide, NY, DAY, 1, breaks=_breaks(18, 24))
    assert not unplaced
    busy = sorted(p.local for p in placed if p.schedule.name == "busy")
    assert all((b - a).total_seconds() >= 3600 for a, b in zip(busy, busy[1:]))
    # still level: no break holds more than one
    assert max(_counts(placed).values()) == 1


def test_two_contracts_of_one_sponsor_are_kept_apart_too():
    a = _spot("a", "early_morning")
    b = _spot("b", "early_morning")
    b.sponsor_id = a.sponsor_id
    placed, _ = plan_spots([a, b], NY, DAY, 1, breaks=_breaks(6, 9))
    t = sorted(p.local for p in placed)
    assert (t[1] - t[0]).total_seconds() >= 3600


def test_keeping_apart_never_beats_levelling():
    # A sponsor with 4 plays in one hour's 4 breaks: they must go one per break
    placed, unplaced = plan_spots([_spot("x", "early_morning", plays=4)] + [_spot(f"o{i}", "early_morning") for i in range(8)],
                                  NY, DAY, 1, breaks=_breaks(6, 9))
    assert not unplaced
    assert max(_counts(placed).values()) == 1


def _mixed_breaks(h_start, h_end):
    """:00 and :30 hard (time check, weather), :15 and :45 soft, 3 places each."""
    return [
        Break(datetime(DAY.year, DAY.month, DAY.day, h, m, tzinfo=NY), 3,
              "hard" if m in (0, 30) else "soft", "TEST block", None, None)
        for h in range(h_start, h_end)
        for m in (0, 15, 30, 45)
    ]


def test_sponsorships_steer_around_a_shiur_even_its_hard_breaks():
    """Main 2026-11-18: the 8 PM Daf Yomi (56 min) had sponsorships at 8:15, 8:30 and 8:45.
    The hard 8:30 time check pauses the shiur anyway, but sponsorships would make that
    pause longer, so they go there only when the slot has no other room."""
    shiur = (datetime(DAY.year, DAY.month, DAY.day, 20, 1, tzinfo=NY), datetime(DAY.year, DAY.month, DAY.day, 20, 57, tzinfo=NY))
    spots = [_spot(f"s{i:02d}", "early_evening") for i in range(10)]
    placed, unplaced = plan_spots(spots, NY, DAY, 1, breaks=_mixed_breaks(18, 21), quiet=[shiur])
    assert not unplaced
    assert not [p.local.strftime("%H:%M") for p in placed if shiur[0] < p.local < shiur[1]]
    # 10 plays over the 9 breaks outside the shiur: one break takes a second
    assert max(_counts(placed).values()) == 2


def test_a_hard_shiur_break_comes_before_a_soft_one():
    # every break outside the shiur is full, so the overflow pauses the shiur only where
    # the time check pauses it anyway
    shiur = (datetime(DAY.year, DAY.month, DAY.day, 20, 1, tzinfo=NY), datetime(DAY.year, DAY.month, DAY.day, 20, 57, tzinfo=NY))
    spots = [_spot(f"s{i:02d}", "early_evening") for i in range(28)]
    placed, unplaced = plan_spots(spots, NY, DAY, 1, breaks=_mixed_breaks(18, 21), quiet=[shiur])
    assert not unplaced
    during = {p.local.strftime("%H:%M") for p in placed if shiur[0] < p.local < shiur[1]}
    assert during == {"20:30"}


def test_a_shiur_break_is_used_when_the_slot_has_no_other_room():
    shiur = (datetime(DAY.year, DAY.month, DAY.day, 18, 0, tzinfo=NY), datetime(DAY.year, DAY.month, DAY.day, 21, 0, tzinfo=NY))
    spots = [_spot(f"s{i:02d}", "early_evening") for i in range(36)]
    placed, unplaced = plan_spots(spots, NY, DAY, 1, breaks=_mixed_breaks(18, 21), quiet=[shiur])
    assert not unplaced and len(placed) == 36


def test_a_soft_break_in_the_last_minutes_of_a_shiur_is_fine():
    # 8:45 with the shiur ending 8:48 waits for its end anyway (no pause)
    shiur = (datetime(DAY.year, DAY.month, DAY.day, 20, 1, tzinfo=NY), datetime(DAY.year, DAY.month, DAY.day, 20, 48, tzinfo=NY))
    spots = [_spot(f"s{i:02d}", "early_evening") for i in range(11)]
    placed, _ = plan_spots(spots, NY, DAY, 1, breaks=_mixed_breaks(18, 21), quiet=[shiur])
    times = {p.local.strftime("%H:%M") for p in placed}
    assert "20:15" not in times and "20:45" in times
