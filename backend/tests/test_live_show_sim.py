"""Live shows in the station simulator (tests/sim/live.py, wiki/station-simulator.md).

A host goes on air on the virtual clock: the real audio socket handler, the broadcast
delay, the timed inserts and the scheduler run, and what listeners got is read back
word by word. One scenario runs in CI; the rest take about half a minute each:

    cd backend && SIM_LONG=1 uv run pytest tests/test_live_show_sim.py -v
"""
import os
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models.alert import Alert
from app.models.live_show import LiveShow
from app.models.station import Station
from tests.sim import live
from tests.sim.run import simulate
from tests.sim.stations import JERUSALEM, MAIN

NY = ZoneInfo("America/New_York")
EXPORT = Path(__file__).parent / "sim" / "fixtures" / "stations-2026-11-19.json"
pytestmark = pytest.mark.asyncio
long = pytest.mark.skipif(os.environ.get("SIM_LONG") != "1", reason="simulator scenarios: SIM_LONG=1")

LIVE = (
    "live show: music stops when the host is first heard, not before, not after",
    "live show: no silence while the show has the air",
    "live show: a Dump cuts those words and only those",
    "live show: listeners hear the host through the delay, never early",
    "live show: the last words air, then the station carries on at once",
    "live show: nothing of the host after the show",
)
AROUND = ("hard items start on their second", "time check at :00 and :30 within 1 s",
          "time check says the right time", "no queue entry airs twice", "no dead air or emergency music",
          "site play log: one row per item aired, at its real time, none overlapping",
          "paused shiur: one resume per pause, no audio lost or repeated")
QUIET = {"live_stream_lag_seconds": 0, "live_record_shows": False}


def at(h, m, s=0, day=19):
    return datetime(2026, 11, day, h, m, s, tzinfo=NY)


def failing(result, check):
    return [f"{f.at}: {f.text}" for f in result.failed(check)]


def all_pass(result, checks=LIVE + AROUND):
    for check in checks:
        assert not result.failed(check), (check, failing(result, check))


def hhmmss(t):
    return datetime.fromtimestamp(t, NY).strftime("%H:%M:%S")


class Show:
    """A scenario's hand on one show: start, Dump, End at chosen moments."""

    def __init__(self, station=MAIN, minutes=20.0):
        self.station, self.minutes = station, minutes
        self.host = None
        self.kept = {}

    def start(self, planned_start):
        async def go(sim):
            show = await live.create_show(sim, self.station, planned_start, self.minutes)
            self.host = await live.go_live(sim, self.station, show)
            async with sim.Session() as db:
                station = await db.get(Station, uuid.UUID(self.station))
                from app.services.live_air import settings_for
                self.host.marks["delay"] = settings_for(station.automation_config)[0]
        return go

    async def dump(self, sim):
        self.kept.setdefault("cuts", []).append(await self.host.dump())

    async def end(self, sim):
        await self.host.end_show()

    async def leave(self, sim):
        await self.host.leave()

    async def read(self, sim):
        async with sim.Session() as db:
            self.kept["show"] = (await db.execute(select(LiveShow))).scalars().first()
            self.kept["alerts"] = [(a.title, a.severity) for a in (await db.execute(select(Alert))).scalars().all()]
            station = await db.get(Station, uuid.UUID(self.station))
            self.kept["live_show_id"] = (station.automation_config or {}).get("live_show_id")


async def run(tmp_path, start, hours, events, stations=("main",), overrides=None, restarts=None):
    config = {MAIN: dict(QUIET), JERUSALEM: dict(QUIET)}
    for sid, extra in (overrides or {}).items():
        config[sid].update(extra)
    _, result = await simulate(EXPORT, start, hours, list(stations), tmp_path, events=events,
                               overrides=config, quiet_pass=12.0)
    print(result.report())
    for r in result.rows:
        print("  ", r.local_start, r.local_end[4:], r.lane, (r.title or "")[:44], r.kind, r.category or "",
              "CUT" if r.cut and r.lane != "live" else "", r.timing_mode or "", r.source or "")
    return result


def rows_after_show(result, station=MAIN):
    rows = [r for r in result.rows if r.station == station]
    show = next(r for r in rows if r.lane == "live" and not r.entry_id)
    return show, [r for r in rows if r.start >= show.end - 0.01 and r.entry_id and r.lane != "live"]


async def test_a_show_across_the_hour_with_a_dump(tmp_path):
    """Thursday 7:50 PM on Main: the host goes live, Dumps at 7:55, talks through the
    8 PM break and ends at 8:08. Music plays until the first words come out of the
    delay; the 8 PM time check airs on its second over the delay; the dumped words never
    air and the words before them do; the last words air; the three sponsorships of
    the 8 PM break air first after the show; then the song the show cut carries on."""
    show = Show()
    result = await run(tmp_path, at(19, 44), 0.6, [
        (at(19, 50, 7), show.start(at(19, 50))), (at(19, 55), show.dump), (at(20, 8), show.end)])
    all_pass(result)
    assert result.checks_run[LIVE[2]] == 1 and 14 < show.kept["cuts"][0] <= 15
    host = show.host
    # the delay filled before anyone heard the host
    assert 14.5 <= host.marks["first_heard"] - host.marks["connected"] <= 16
    time_check = [r for r in result.rows if r.is_time and r.lane == "live"]
    assert len(time_check) == 1 and abs(time_check[0].start - at(20, 0).timestamp()) <= 0.2
    live_row, after = rows_after_show(result)
    sponsors = [r for r in after[:3]]
    assert all(r.is_sponsorship for r in sponsors), [r.label() for r in after[:4]]
    assert after[3].source.startswith("resume@"), after[3].label()
    # nothing of the 8 PM break aired late: no weather, no second time check
    assert not [r for r in after if r.category in ("weather_spot", "time_announcement")
                and r.start < at(20, 29).timestamp()]


@long
async def test_going_live_seconds_before_the_time_check(tmp_path):
    """GO LIVE at 8:29:50: the delay is full at 8:30:05, in the middle of the 8:30 time
    check. The show waits for the time check to end; it is never cut and never said twice."""
    show = Show(minutes=15)
    result = await run(tmp_path, at(20, 24), 0.4, [
        (at(20, 29, 50), show.start(at(20, 30))), (at(20, 40), show.end)])
    all_pass(result)
    checks = [r for r in result.rows if r.is_time and at(20, 29).timestamp() < r.start < at(20, 31).timestamp()]
    assert len(checks) == 1 and not checks[0].cut and checks[0].lane != "live", [r.label() for r in checks]
    assert show.host.marks["first_heard"] >= checks[0].end - 0.5


@long
async def test_no_delay_the_host_is_heard_at_once(tmp_path):
    """A station with no broadcast delay: the host is heard from the first frame and the
    music stops then. The 8 PM time check has no delay to air over: it is not aired late
    after the show either."""
    show = Show()
    result = await run(tmp_path, at(19, 50), 0.4, [
        (at(19, 56), show.start(at(19, 56))), (at(20, 4), show.end)],
        overrides={MAIN: {"live_delay_seconds": 0}})
    all_pass(result, LIVE + ("no queue entry airs twice", "no dead air or emergency music"))
    assert show.host.marks["first_heard"] - show.host.marks["connected"] <= 0.2
    _, after = rows_after_show(result)
    assert not [r for r in after if r.is_time and r.start < at(20, 29).timestamp()]


@long
async def test_the_hard_stop_ends_a_show_the_host_did_not_end(tmp_path):
    """The show is planned until 8:10 and the host keeps talking: the delay winds down
    before the hard stop so the words said up to it air, the show stops at 8:10:00, the
    station carries on and an alert says the show was stopped."""
    show = Show(minutes=14)
    result = await run(tmp_path, at(19, 50), 0.5, [
        (at(19, 56), show.start(at(19, 56))), (at(20, 15), show.read)])
    all_pass(result, tuple(c for c in LIVE if "last words" not in c) + AROUND)
    live_row, after = rows_after_show(result)
    assert abs(live_row.end - at(20, 10).timestamp()) <= 4, hhmmss(live_row.end)
    assert after and after[0].start - live_row.end <= 1.0
    assert str(show.kept["show"].status).endswith("ENDED") or "ended" in str(show.kept["show"].status).lower()
    assert any("hard stopped" in t for t, _ in show.kept["alerts"]), show.kept["alerts"]
    assert show.kept["live_show_id"] is None
    # what the host said until three seconds before the stop was heard
    aired = live.absolute(show.host.heard)
    said_at_stop = max(f for f, t in show.host.said.items() if t <= at(20, 10).timestamp() - 3)
    assert aired[-1][1] >= said_at_stop - 20, (aired[-1][1], said_at_stop)


@long
async def test_a_host_who_disappears_and_comes_back(tmp_path):
    """The host's page closes at 8:02 without End. What was said plays out of the
    delay, 15 s after the last word the station goes back to music with an alert, and
    the show stays open: at 8:05 the host is back and on air again through the delay."""
    show = Show(minutes=30)

    async def back(sim):
        await show.host.connect()

    result = await run(tmp_path, at(19, 54), 0.4, [
        (at(19, 56), show.start(at(19, 56))), (at(20, 2), show.leave), (at(20, 4), show.read),
        (at(20, 5), back), (at(20, 12), show.end)])
    rows = [r for r in result.rows if r.lane == "live" and not r.entry_id]
    assert len(rows) == 2, [r.label() for r in rows]
    gone, again = rows
    # music 15 s after the host's last word (it aired 15 s after it was said)
    assert 13 <= gone.end - at(20, 2).timestamp() <= 19, gone.end - at(20, 2).timestamp()
    assert any("lost its host" in t for t, _ in show.kept["alerts"]), show.kept["alerts"]
    assert 14 <= again.start - at(20, 5).timestamp() <= 17
    all_pass(result, ("no dead air or emergency music", "no queue entry airs twice",
                      "live show: nothing of the host after the show",
                      "live show: listeners hear the host through the delay, never early"))
    # the only silence listeners got: the wait for a host who might come back
    quiet = failing(result, LIVE[1])
    assert len(quiet) <= 1, quiet


@long
async def test_a_backend_restart_in_mid_show(tmp_path):
    """A deployment at 8:03 during a show: the delay and its 15 s of words are lost with
    the process, the host's page reconnects by itself 3 s later and the host is on air
    again. The stream never plays music under the show and the show ends cleanly."""
    show = Show(minutes=30)

    async def restart(sim):
        await sim.restart_backend()

    result = await run(tmp_path, at(19, 54), 0.4, [
        (at(19, 56), show.start(at(19, 56))), (at(20, 3), restart), (at(20, 9), show.end)])
    all_pass(result, ("no queue entry airs twice", "live show: nothing of the host after the show",
                      "live show: listeners hear the host through the delay, never early",
                      "live show: the last words air, then the station carries on at once"))
    rows = [r for r in result.rows if r.station == MAIN]
    music_in_show = [r for r in rows if r.lane == "main" and at(19, 57).timestamp() < r.start < at(20, 9).timestamp()]
    assert not music_in_show, [r.label() for r in music_in_show]
    # how long listeners heard nothing around the restart
    heard = [h.t for h in show.host.heard if h.kind != "quiet"]
    gaps = [(b - a, a) for a, b in zip(heard, heard[1:]) if b - a > 2]
    print("silence around the restart:", [(round(g, 1), hhmmss(a)) for g, a in gaps])
    assert len(gaps) == 1 and gaps[0][0] <= 22, gaps


@long
async def test_an_engine_restart_in_mid_show(tmp_path):
    """The stream server restarts at 8:03 during a show. It must not come back playing
    music under the host: it is told of the show again and the stream is the show's."""
    show = Show(minutes=30)

    async def restart(sim):
        await sim.restart_engine(MAIN)

    result = await run(tmp_path, at(19, 54), 0.4, [
        (at(19, 56), show.start(at(19, 56))), (at(20, 3), restart), (at(20, 9), show.end)])
    rows = [r for r in result.rows if r.station == MAIN]
    under = [r for r in rows if r.lane in ("main", "timed", "emergency")
             and at(20, 3).timestamp() <= r.start < at(20, 9).timestamp()]
    seconds = sum(min(r.end, at(20, 9).timestamp()) - r.start for r in under)
    print("on the stream under the host after the engine restart:", [(r.label(), round(r.seconds, 1)) for r in under])
    assert seconds <= 20, seconds
    all_pass(result, ("no queue entry airs twice", "live show: the last words air, then the station carries on at once"))


@long
async def test_a_show_that_runs_into_shabbos(tmp_path):
    """Friday on Main: a show planned 4:00 to 4:40 PM, candle lighting at 4:18. The host
    must be off the air by candle lighting and the Shabbos silence must begin on time,
    whatever the show's planned end says."""
    show = Show(minutes=40)
    result = await run(tmp_path, at(15, 50, day=20), 0.75, [
        (at(16, 0, day=20), show.start(at(16, 0, day=20))), (at(16, 30, day=20), show.read)])
    assert not result.failed("Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia"), \
        failing(result, "Shabbos/Yom Tov: silence from candle lighting to 72 min after shekia")

    live_row = next(r for r in result.rows if r.lane == "live" and not r.entry_id)
    silence = [r for r in result.rows if r.kind == "silence"]
    assert silence, "no Shabbos silence aired"
    assert live_row.end <= silence[0].start + 0.5 and silence[0].start - live_row.end <= 30, \
        (hhmmss(live_row.end), hhmmss(silence[0].start))
    assert not [h for h in show.host.heard if h.kind == "host" and h.t > silence[0].start + 1]


@long
async def test_a_show_during_a_shiur_resumes_it_where_it_stopped(tmp_path):
    """8:20 PM on Main, in the Daf Yomi shiur: a ten-minute show. After it the shiur
    carries on from the second it stopped at: nothing lost, nothing said twice."""
    show = Show(minutes=20)
    result = await run(tmp_path, at(19, 56), 0.75, [
        (at(20, 20), show.start(at(20, 20))), (at(20, 31), show.end)])
    all_pass(result)
    live_row, after = rows_after_show(result)
    before = [r for r in result.rows if r.station == MAIN and r.end <= live_row.start + 0.01 and r.entry_id][-1]
    assert before.is_long and before.cut, before.label()
    back = next(r for r in after if r.asset_id == before.asset_id)
    assert -5 <= back.cue_in - (before.cue_in + before.seconds) <= 0.5, (back.cue_in, before.cue_in + before.seconds)
    assert back.start - live_row.end <= 120


@long
async def test_a_show_on_one_station_leaves_the_other_alone(tmp_path):
    """A show on KBR Jerusalem while Main plays on: Main's music, time check and
    sponsorships are untouched and no word of the host reaches it."""
    show = Show(station=JERUSALEM)
    result = await run(tmp_path, at(19, 50), 0.4, [
        (at(19, 55), show.start(at(19, 55))), (at(20, 6), show.end)], stations=("main", "jerusalem"))
    all_pass(result)
    assert not [r for r in result.rows if r.station == MAIN and r.lane == "live"]
    assert [r for r in result.rows if r.station == JERUSALEM and r.lane == "live"]
    assert [r for r in result.rows if r.station == MAIN and r.is_time and abs(r.start - at(20, 0).timestamp()) <= 1]


@long
async def test_a_caller_on_air_held_dumped_and_hung_up(tmp_path):
    """A web caller in a show with a 15 s delay: listeners hear them only for what they
    said while on air (not a word from before Put On Air, on hold or after the hang up),
    through the same delay as the host, and a Dump takes the caller's words with the
    host's."""
    show = Show(minutes=10)
    kept = {}

    async def call(sim):
        kept["caller"] = live.SimCaller(show.host)
        await kept["caller"].call()
        await kept["caller"].approve()

    def do(what):
        async def go(sim):
            await getattr(kept["caller"], what)()
        return go

    result = await run(tmp_path, at(14, 38), 0.3, [
        (at(14, 42), show.start(at(14, 42))), (at(14, 43), call),
        (at(14, 44), do("on_air")), (at(14, 45), do("hold")),
        (at(14, 46), do("on_air")), (at(14, 46, 20), show.dump), (at(14, 47), do("hang_up")),
        (at(14, 49), show.end)])
    all_pass(result)
    host, caller = show.host, kept["caller"]
    assert len(caller.air) == 2 and show.kept["cuts"][0] > 10

    def on_air(t, slack=0.0):
        return any(a - slack <= t <= b + slack for a, b in caller.air)

    with_caller = [h for h in host.heard if h.caller]
    assert with_caller, "the caller was never heard"
    spoken = [(h, host.said[int(h.frame)]) for h in host.heard if h.kind == "host"]
    # nothing of the caller from a moment they were not on air (0.2 s: frames in flight)
    stray = [(hhmmss(h.t), hhmmss(said)) for h, said in spoken if h.caller and not on_air(said, 0.2)]
    assert not stray, stray[:5]
    # ... and all of what they said on air, bar the first moment while their line fills
    missing = [hhmmss(said) for h, said in spoken
               if not h.caller and any(a + 0.5 <= said <= b - 0.2 for a, b in caller.air)]
    assert not missing, missing[:5]
    # through the delay like the host: never sooner than the words beside them
    first = min(h.t for h in with_caller)
    assert first - at(14, 44).timestamp() >= 14.5, first - at(14, 44).timestamp()
    # the Dump took the caller's words with the host's: 14:46:05 to 14:46:20 never aired
    cut_from = at(14, 46, 20).timestamp() - show.kept["cuts"][0]
    assert not [1 for _h, said in spoken if cut_from + 0.1 < said < at(14, 46, 20).timestamp() - 0.1]
    # after the hang up the caller's sound ends once the delay has played it out
    assert max(h.t for h in with_caller) <= at(14, 47).timestamp() + 16
