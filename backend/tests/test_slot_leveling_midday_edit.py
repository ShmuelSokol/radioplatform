"""A contract edited part way through its time slot (checklist V3-79).

The plan of a day is made afresh after every edit and does not know what already
aired. When the edit moves other contracts' plays between breaks, a play that
aired before the edit can be planned again after it (aired twice), and a play
moved into a break that is already over is lost. What should hold: for every
contract, the plays that aired before the edit plus the plays still planned
after it equal what the contract says, never more and never fewer.
"""
from collections import Counter
from datetime import datetime

import pytest

from tests.test_slot_leveling import DAY, NY, _breaks, _spot, plan_spots


def _aired_plus_planned(before, after, now):
    aired = Counter(p.schedule.name for p in before if p.local <= now)
    later = Counter(p.schedule.name for p in after if p.local > now)
    return aired + later


def _now(h, m):
    return datetime(DAY.year, DAY.month, DAY.day, h, m, tzinfo=NY)


def test_a_new_contract_mid_slot_leaves_the_others_as_promised():
    base = [_spot(f"s{i:02d}", "early_morning") for i in range(12)]
    before, _ = plan_spots(base, NY, DAY, 1, breaks=_breaks(6, 9))
    after, _ = plan_spots(base + [_spot("zz_new", "early_morning", plays=3)], NY, DAY, 1, breaks=_breaks(6, 9))
    total = _aired_plus_planned(before, after, _now(7, 50))
    assert all(total[s.name] == 1 for s in base), total


@pytest.mark.xfail(strict=True, reason="found 2026-11-18: raising one contract's plays mid-slot reshuffles the others; some air twice")
def test_raising_one_contract_mid_slot_airs_no_other_twice():
    base = [_spot(f"s{i:02d}", "early_morning") for i in range(12)]
    before, _ = plan_spots(base, NY, DAY, 1, breaks=_breaks(6, 9))
    raised = [_spot(s.name, "early_morning", plays=3 if s.name == "s05" else 1) for s in base]
    after, _ = plan_spots(raised, NY, DAY, 1, breaks=_breaks(6, 9))
    for now in (_now(6, 50), _now(7, 5), _now(7, 20)):
        total = _aired_plus_planned(before, after, now)
        assert all(total[s.name] == 1 for s in base if s.name != "s05"), (now, total)


@pytest.mark.xfail(strict=True, reason="found 2026-11-18: plays the new plan puts in breaks already over are never aired")
def test_raised_plays_land_in_breaks_still_to_come():
    base = [_spot(f"s{i:02d}", "early_morning") for i in range(12)]
    before, _ = plan_spots(base, NY, DAY, 1, breaks=_breaks(6, 9))
    raised = [_spot(s.name, "early_morning", plays=3 if s.name == "s05" else 1) for s in base]
    after, _ = plan_spots(raised, NY, DAY, 1, breaks=_breaks(6, 9))
    now = _now(7, 50)
    total = _aired_plus_planned(before, after, now)
    assert total["s05"] == 3, total
