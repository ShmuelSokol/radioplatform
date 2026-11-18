"""Edges of the live-show broadcast delay (PR 316) that tests/test_live_delay.py leaves
open: checklist rows LD-20, LD-27, LD-28 and LD-29 (docs/test-checklist.md)."""
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.api.v1 import live_shows_ws
from app.models.queue_entry import QueueEntry
from app.services import live_inserts
from app.services.live_air import RATE, LiveAir
from tests.test_live_delay import Clock, _nowhere, _quiet, _station, marked, run, spoken_at, talker  # noqa: F401


_WINDS_DOWN_TOO_SLOWLY = pytest.mark.xfail(strict=True, reason=(
    "found 2026-11-18: the wind-down starts 3 minutes before the hard stop and speeds "
    "non-stop speech up by at most 12 %, so it can take back only about 21 s; a longer "
    "delay, or a show shorter than the delay plus 3 minutes, still has audio waiting at "
    "the stop, which is cut"))


@pytest.mark.parametrize("delay,length", [
    (20, 600),
    pytest.param(30, 600, marks=_WINDS_DOWN_TOO_SLOWLY),
    pytest.param(40, 600, marks=_WINDS_DOWN_TOO_SLOWLY),
    pytest.param(40, 120, marks=_WINDS_DOWN_TOO_SLOWLY),
    pytest.param(15, 60, marks=_WINDS_DOWN_TOO_SLOWLY),
])
def test_the_delay_is_empty_by_the_hard_stop(delay, length):
    """LD-27: the host talks without a pause until the hard stop. Whatever the delay
    (0 to 40 s) and however short the show, nothing may still be waiting in the delay
    when the stop cuts it: those would be the host's last words."""
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=delay, clock=clock)
    air.end_at = clock.t + length
    run(air, clock, float(length), lambda t: marked(0.05, start=t % 300))
    assert air.buffered <= 0.5, f"{air.buffered:.1f} s of the host's last words still waiting at the stop"


def test_dump_pressed_five_times_fast_cuts_each_time_and_builds_back_once():
    """LD-28: Dump pressed five times in 2 s (console and screener together). Every press
    cuts only what is waiting, nothing that was cut ever airs, the counter is right and the
    delay builds back afterwards."""
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)
    run(air, clock, 60.0, talker())
    cuts, t, windows = [], 60.0, []
    for _ in range(5):
        before = air.buffered
        cuts.append(air.dump())
        windows.append((t - before, t))
        run(air, clock, 0.4, talker(), t0=t)
        t += 0.4
    assert cuts[0] == pytest.approx(15.0, abs=0.1)
    assert all(c >= 0 for c in cuts)
    assert air.stats.dumps == sum(1 for c in cuts if c > 0)
    heard = run(air, clock, 60.0, talker(), t0=t)
    at = spoken_at(heard)
    at = at[(at > 0)]
    for a, b in windows:
        inside = ((at > a + 0.02) & (at < b - 0.02)).sum()
        assert inside <= 2 * 240, f"{inside} samples said between {a:.2f} and {b:.2f} s aired after a Dump"
    assert air.buffered > 0.5, f"the delay did not build back ({air.buffered:.2f} s after 60 s)"


def test_two_stations_never_hear_each_other():
    """LD-20: two stations with a show each. What one host says and a Dump on one never
    reach the other station's listeners or change its delay."""
    clock = Clock()
    a = LiveAir("station-a", "show-a", _nowhere, delay=10, clock=clock)
    b = LiveAir("station-b", "show-b", _nowhere, delay=5, clock=clock)
    heard_a, heard_b = [], []
    for i in range(int(40 / 0.05)):
        t = i * 0.05
        a.feed(marked(0.05, start=t))                   # A says 0..40 s
        b.feed(marked(0.05, start=100 + t))             # B says 100..140 s
        if i == int(30 / 0.05):
            a.dump()
        for air, out in ((a, heard_a), (b, heard_b)):
            frame = air.produce(int(RATE * 0.05))
            if frame is not None:
                out.append(frame)
        clock.t += 0.05
    at_a, at_b = spoken_at(np.concatenate(heard_a)), spoken_at(np.concatenate(heard_b))
    assert at_a.max() < 50 and at_b.min() >= 99.9, "one station's host reached the other's listeners"
    assert b.stats.dumps == 0 and b.buffered == pytest.approx(5.0, abs=0.1), "a Dump on A touched B"


@pytest.mark.asyncio
@pytest.mark.usefixtures("_quiet")
async def test_time_check_that_cannot_be_decoded_does_not_break_the_show(db_session, monkeypatch):
    """LD-29: the time check's file cannot be read (missing, ffmpeg error). The show carries
    on, nothing is inserted, the entry stays for the scheduler (it is closed or dropped as
    an item missed during the show, never aired late over the host), and a later pass may
    try again."""
    station, show, _song, clock_asset, _weather = await _station(db_session)
    now = datetime.now(UTC)
    entry = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clock_asset.id, position=5,
                       status="pending", timing_mode="hard", preempt_at=now + timedelta(seconds=30))
    db_session.add(entry)
    await db_session.commit()
    calls = []

    async def broken(url):
        calls.append(url)
        return None

    monkeypatch.setattr(live_inserts, "decode_clip", broken)
    monkeypatch.setattr("app.services.playout_engine.audio_url", lambda asset: asset.file_path)
    live_inserts._preparing.clear()
    clock = Clock(now.timestamp())
    air = LiveAir(str(station.id), str(show.id), _nowhere, delay=5, clock=clock)
    live_shows_ws.live_relay.airs[str(station.id)] = air
    run(air, clock, 6.0, talker())
    assert await live_inserts.prepare(db_session, station, now) == 1
    await asyncio.sleep(0.05)
    assert calls == ["https://x/t.mp3"]
    assert air.inserts == [] or not list(air.inserts)
    heard = run(air, clock, 40.0, talker(), t0=6.0)
    at = spoken_at(heard)
    assert np.all(np.diff(at) >= -0.011) and at[-1] > 40, "the host's audio was disturbed"
    await db_session.refresh(entry)
    assert entry.status == "pending"
    assert str(entry.id) not in live_inserts._preparing      # a later pass can try again
