"""A contract edited part way through its time slot (checklist V3-79).

The plan of a day is made afresh after every edit; ``settle_today`` then fits it
to what already aired (``plan_for_station`` reads the play log). When the edit moves other contracts' plays between breaks, a play that
aired before the edit can be planned again after it (aired twice), and a play
moved into a break that is already over is lost. What should hold: for every
contract, the plays that aired before the edit plus the plays still planned
after it equal what the contract says, never more and never fewer.
"""
from collections import Counter
from datetime import datetime

from app.services.spot_schedule_service import settle_today
from tests.test_slot_leveling import DAY, NY, _breaks, _spot, plan_spots


def _aired_plus_planned(before, after, now):
    aired = Counter(p.schedule.name for p in before if p.local <= now)
    later = Counter(p.schedule.name for p in after if p.local > now)
    return aired + later


def _replan(before, schedules, now):
    """The plan after an edit at `now`, given what the plan before it aired."""
    aired: dict = {}
    for p in before:
        if p.local <= now:
            aired.setdefault(str(p.schedule.asset_id), []).append(p.local)
    after, _ = plan_spots(schedules, NY, DAY, 1, breaks=_breaks(6, 9))
    return settle_today(after, _breaks(6, 9), [], NY, now, aired)


def _now(h, m):
    return datetime(DAY.year, DAY.month, DAY.day, h, m, tzinfo=NY)


def test_a_new_contract_mid_slot_leaves_the_others_as_promised():
    base = [_spot(f"s{i:02d}", "early_morning") for i in range(12)]
    before, _ = plan_spots(base, NY, DAY, 1, breaks=_breaks(6, 9))
    now = _now(7, 50)
    after = _replan(before, base + [_spot("zz_new", "early_morning", plays=3)], now)
    total = _aired_plus_planned(before, after, now)
    assert all(total[s.name] == 1 for s in base), total


def test_raising_one_contract_mid_slot_airs_no_other_twice():
    base = [_spot(f"s{i:02d}", "early_morning") for i in range(12)]
    before, _ = plan_spots(base, NY, DAY, 1, breaks=_breaks(6, 9))
    raised = [_spot(s.name, "early_morning", plays=3 if s.name == "s05" else 1) for s in base]
    for now in (_now(6, 50), _now(7, 5), _now(7, 20)):
        after = _replan(before, raised, now)
        total = _aired_plus_planned(before, after, now)
        assert all(total[s.name] == 1 for s in base if s.name != "s05"), (now, total)


def test_raised_plays_land_in_breaks_still_to_come():
    base = [_spot(f"s{i:02d}", "early_morning") for i in range(12)]
    before, _ = plan_spots(base, NY, DAY, 1, breaks=_breaks(6, 9))
    raised = [_spot(s.name, "early_morning", plays=3 if s.name == "s05" else 1) for s in base]
    now = _now(7, 50)
    after = _replan(before, raised, now)
    total = _aired_plus_planned(before, after, now)
    assert total["s05"] == 3, total


def test_after_the_edit_the_coming_breaks_stay_level():
    base = [_spot(f"s{i:02d}", "early_morning") for i in range(24)]
    before, _ = plan_spots(base, NY, DAY, 1, breaks=_breaks(6, 9))
    raised = [_spot(s.name, "early_morning", plays=3 if s.name == "s05" else 1) for s in base]
    now = _now(7, 20)
    after = _replan(before, raised, now)
    coming = Counter(p.local for p in after if p.local > now)
    assert max(coming.values()) - min(coming.values()) <= 1, coming
