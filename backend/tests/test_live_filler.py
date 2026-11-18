"""After a Dump a station's filler (a jingle) plays while the delay refills at once.

Without one the delay builds back by stretching the host's speech, which takes a
minute or two during which a second Dump cuts little. Talk-show delay units play a
recorded fill for exactly the length that was cut.
"""
import numpy as np
import pytest

from app.services.live_air import RATE, LiveAir, filler_asset
from tests.test_live_delay import Clock, _nowhere, run, spoken_at, talker

FILL = 300  # sample value of the filler: below `marked` speech (>= 1000), above silence


def jingle(seconds: float) -> np.ndarray:
    return np.full(int(seconds * RATE), FILL, dtype=np.int16)


def fill_seconds(heard: np.ndarray) -> float:
    return float((heard == FILL).sum()) / RATE


def early(at: np.ndarray, floor: float) -> int:
    """Samples that seem spoken before `floor`. A Dump fades 10 ms on each side of the
    cut, and a faded sample reads as an earlier time, so up to 480 are no finding."""
    return int((at < floor).sum())


def test_setting():
    assert filler_asset(None) is None and filler_asset({}) is None
    assert filler_asset({"live_dump_filler_asset_id": "abc"}) == "abc"
    assert filler_asset({"live_dump_filler_asset_id": ""}) is None


def test_full_dump_plays_the_filler_and_the_delay_is_whole_when_it_ends():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)
    air.filler, air.filler_title = jingle(30), "Station jingle"
    run(air, clock, 60.0, talker())
    assert air.dump() == pytest.approx(15.0, abs=0.1)
    first = run(air, clock, 1.0, talker(), t0=60.0)
    assert fill_seconds(first) > 0.9          # the jingle starts at once, no silence
    status = air.status()
    assert status["mode"] == "insert" and status["insert"]["kind"] == "filler"
    assert status["insert"]["title"] == "Station jingle"
    heard = np.concatenate((first, run(air, clock, 30.0, talker(), t0=61.0)))
    assert fill_seconds(heard) == pytest.approx(15.0, abs=0.5)
    # Safe to Dump again as soon as the jingle is over, not a minute or two later
    assert air.buffered == pytest.approx(15.0, abs=0.3)
    assert air.status()["safe"] is True and air.status()["mode"] == "steady"
    at = spoken_at(heard)
    assert early(at, 59.95) <= 480            # nothing that was dumped airs
    assert at.max() == pytest.approx(76.0, abs=0.6)  # the host is 15 s behind again
    assert air.stats.fills == 1


def test_partial_dump_airs_what_was_kept_then_the_filler_where_the_cut_is():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=20, dump=8, clock=clock)
    air.filler = jingle(30)
    run(air, clock, 60.0, talker())
    assert air.dump() == pytest.approx(8.0, abs=0.01)
    heard = run(air, clock, 40.0, talker(), t0=60.0)
    is_fill = heard == FILL
    start = int(np.argmax(is_fill))
    assert start / RATE == pytest.approx(12.0, abs=0.3)   # after the 12 s that stayed
    before = spoken_at(heard[:start])
    assert before.max() < 52.05 and early(before, 39.5) <= 480   # said from 40 s to 52 s
    assert fill_seconds(heard) == pytest.approx(8.0, abs=0.5)
    end = len(heard) - int(np.argmax(is_fill[::-1]))
    after = spoken_at(heard[end + 480:])
    assert early(after, 59.9) <= 480                      # 52 s to 60 s never airs
    assert air.buffered == pytest.approx(20.0, abs=0.4)


def test_a_short_filler_plays_once_and_the_rest_builds_back():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)
    air.filler = jingle(5)
    run(air, clock, 60.0, talker())
    air.dump()
    heard = run(air, clock, 10.0, talker(), t0=60.0)
    assert fill_seconds(heard) == pytest.approx(5.0, abs=0.1)
    assert 4.8 <= air.buffered < 7.0
    assert air.status()["mode"] == "building"


def test_no_filler_when_the_delay_is_being_left_or_none_is_set():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)
    run(air, clock, 30.0, talker())
    air.dump()
    assert fill_seconds(run(air, clock, 5.0, talker(), t0=30.0)) == 0   # none set
    air.filler = jingle(30)
    run(air, clock, 120.0, talker(), t0=35.0)
    air.dump()
    air.exit_delay()                                                    # going really live
    assert fill_seconds(run(air, clock, 5.0, talker(), t0=155.0)) == 0
    assert air.stats.fills == 0


def test_a_second_dump_during_the_filler_starts_it_again():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)
    air.filler = jingle(30)
    run(air, clock, 60.0, talker())
    air.dump()
    run(air, clock, 6.0, talker(), t0=60.0)
    assert air.buffered == pytest.approx(6.0, abs=0.2)
    assert air.dump() == pytest.approx(6.0, abs=0.2)     # the host said it again
    heard = run(air, clock, 40.0, talker(), t0=66.0)
    assert early(spoken_at(heard), 65.9) <= 480
    assert air.buffered == pytest.approx(15.0, abs=0.4)
    assert fill_seconds(heard) == pytest.approx(15.0, abs=1.0)   # two faded ends are not counted
