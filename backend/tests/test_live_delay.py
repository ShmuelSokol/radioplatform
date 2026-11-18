"""The broadcast (profanity) delay on live shows: services/live_air.py.

Shmuel, 2026-11-18: "live isn't live, it's 15 or 20 seconds delayed, so this way you
could cut out curse words". Audio here is a ramp of sample values, so every test can
say exactly which part of what the host said reached listeners.
"""
import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.api.v1 import live_shows_ws
from app.models.asset import Asset
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import live_inserts, live_show_service
from app.services.live_air import RATE, Insert, LiveAir, settings_for, stretch

STEP = int(RATE * 0.05)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def speech(seconds: float, level: int = 5000, start: float = 0.0) -> np.ndarray:
    """Loud "speech": a 200 Hz tone whose size is easy to tell from a pause."""
    n = int(seconds * RATE)
    t = (np.arange(n) + int(start * RATE)) / RATE
    return (level * np.sin(2 * np.pi * 200 * t)).astype(np.int16)


def marked(seconds: float, start: float = 0.0) -> np.ndarray:
    """Audio whose sample value says when it was spoken: value = hundredths of a second
    since `start` + 1000 (loud enough to count as speech)."""
    n = int(seconds * RATE)
    return (1000 + (np.arange(n) / RATE + start) * 100).astype(np.int16)


def spoken_at(samples: np.ndarray) -> np.ndarray:
    """The inverse of `marked`: when each (non-silent) sample was spoken, in seconds."""
    return (samples[samples >= 1000].astype(np.float64) - 1000) / 100


async def _nowhere(data):
    return None


def run(air: LiveAir, clock: Clock, seconds: float, talk=None, t0: float = 0.0) -> np.ndarray:
    """Run the delay for `seconds`: the host talks (talk(t) gives a step of audio, or
    None for no audio at all) and we collect what listeners hear."""
    heard = []
    steps = int(round(seconds / 0.05))
    for i in range(steps):
        t = t0 + i * 0.05
        if talk is not None:
            frame = talk(t)
            if frame is not None:
                air.feed(frame)
        out = air.produce(STEP)
        heard.append(np.zeros(0, dtype=np.int16) if out is None else out)
        clock.t += 0.05
    return np.concatenate(heard) if heard else np.zeros(0, dtype=np.int16)


def clean(heard: np.ndarray) -> np.ndarray:
    """spoken_at without the 10 ms faded edges a Dump leaves (their values are scaled)."""
    at = spoken_at(heard)
    return at[2 * 240:]


def talker(start=0.0):
    return lambda t: marked(0.05, start=t)


# ---------- settings ----------

def test_station_settings_default_and_limits():
    assert settings_for(None) == (15.0, 0.0, "time")
    assert settings_for({"live_delay_seconds": 20, "live_dump_seconds": 8, "live_timed_items": "all"}) == (20.0, 8.0, "all")
    assert settings_for({"live_delay_seconds": 0}) == (0.0, 0.0, "time")
    assert settings_for({"live_delay_seconds": 999})[0] == 40.0
    assert settings_for({"live_delay_seconds": "abc", "live_timed_items": "weird"}) == (15.0, 0.0, "time")
    assert settings_for({"live_delay_seconds": -5})[0] == 0.0


def test_stretch_keeps_the_ends_and_the_length():
    x = np.arange(100, dtype=np.int16)
    for n in (50, 100, 103, 200):
        y = stretch(x, n)
        assert len(y) == n and y[0] == 0 and y[-1] == 99
    assert len(stretch(np.zeros(0, dtype=np.int16), 10)) == 10


# ---------- pre-roll: nothing airs until the delay is full ----------

def test_listeners_hear_the_host_exactly_the_delay_later():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)
    early = run(air, clock, 14.9, talker())
    assert len(early) == 0 and air.listening is False and air.status()["mode"] == "pre-roll"
    heard = run(air, clock, 5.0, talker(), t0=14.9)
    assert air.listening is True
    at = spoken_at(heard)
    # The first thing listeners hear is the host's first word, 15 s after it was said
    assert at[0] == pytest.approx(0.0, abs=0.02)
    assert len(heard) == pytest.approx(4.9 * RATE, abs=STEP * 2)
    # ... in order, nothing missing, at normal speed
    assert np.all(np.diff(at) >= -0.011) and at[-1] == pytest.approx(4.9, abs=0.15)
    status = air.status()
    assert status["mode"] == "steady" and status["delay"] == pytest.approx(15.0, abs=0.1) and status["safe"] is True


def test_no_delay_goes_straight_through():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=0, clock=clock)
    air.listening = True
    assert air.passthrough() is True
    assert air.status()["safe"] is False  # nothing can be dumped: the console must say so


# ---------- Dump ----------

def test_dump_cuts_what_was_just_said_and_nothing_before_it():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)
    run(air, clock, 60.0, talker())
    assert air.buffered == pytest.approx(15.0, abs=0.1)
    # At t=60 the caller curses (the last seconds said are the newest in the delay). Dump.
    cut = air.dump()
    assert cut == pytest.approx(15.0, abs=0.1) and air.buffered == 0
    heard = run(air, clock, 40.0, talker(), t0=60.0)
    at = clean(heard)
    # Nothing said between 45 s and 60 s ever reaches a listener
    assert not np.any((at > 45.05) & (at < 59.95))
    assert at.min() >= 59.95
    assert air.stats.dumps == 1


def test_partial_dump_keeps_the_older_part_and_allows_a_second_dump():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=20, dump=8, clock=clock)
    run(air, clock, 60.0, talker())
    assert air.dump() == pytest.approx(8.0, abs=0.01)
    assert air.buffered == pytest.approx(12.0, abs=0.1)
    assert air.status()["safe"] is True  # another Dump still cuts a full 8 s
    heard = run(air, clock, 30.0, talker(), t0=60.0)
    at = spoken_at(heard)
    # What was said from 40 s to 52 s still airs; 52 s to 60 s is gone
    assert np.any((at > 41) & (at < 51.9))
    assert int(((at > 52.1) & (at < 59.95)).sum()) <= 2 * 240  # only the 10 ms faded edge
    assert air.dump() == pytest.approx(8.0, abs=0.01)
    assert air.dump() > 0 and air.dump() >= 0
    assert air.buffered < 8.0


def test_dump_with_an_empty_delay_does_nothing():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)
    assert air.dump() == 0.0 and air.stats.dumps == 0


# ---------- build-back ----------

def test_delay_builds_back_after_a_dump_without_losing_a_word():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)

    def host(t):  # talks 3 s, pauses 1 s, like a person
        return speech(0.05, start=t) if (t % 4) < 3 else np.zeros(STEP, dtype=np.int16)

    run(air, clock, 30.0, host)
    air.dump()
    assert air.status()["safe"] is False
    run(air, clock, 1.0, host, t0=30.0)
    assert air.status()["mode"] == "building"
    # Pauses play at half speed and speech 3 % slow: about 0.15 s gained a second
    took = None
    for second in range(1, 400):
        run(air, clock, 1.0, host, t0=30.0 + second)
        if air.buffered >= 14.7:
            took = second
            break
    assert took is not None and 60 <= took <= 180, took
    run(air, clock, 5.0, host, t0=31.0 + took)
    assert air.status()["mode"] == "steady" and air.status()["safe"] is True
    assert air.buffered == pytest.approx(15.0, abs=0.4)


def test_build_back_plays_speech_only_slightly_slower():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)
    run(air, clock, 20.0, talker())
    air.dump()
    heard = run(air, clock, 10.0, talker(), t0=20.0)
    at = clean(heard)
    rate = (at[-1] - at[0]) / (len(at) / RATE)  # seconds of speech aired per second
    assert 0.96 <= rate <= 0.98
    assert np.all(np.diff(at) >= -0.011)  # in order, nothing repeated or skipped


def _tone_power(x: np.ndarray, f: float) -> float:
    """Mean size of the tone `f` over one-second pieces of `x`."""
    sizes = []
    for i in range(0, len(x) - RATE + 1, RATE):
        piece = x[i:i + RATE].astype(np.float64)
        t = np.arange(RATE) / RATE
        sizes.append(abs(np.dot(piece, np.exp(-2j * np.pi * f * t))) / RATE)
    return float(np.mean(sizes))


def test_the_voice_keeps_its_pitch_while_the_delay_builds_and_shrinks():
    """Slowing speech down by resampling lowers the voice (3 % is half a semitone and a
    1000 Hz tone came out at 970 Hz in the first browser run). A cycle of the voice is
    repeated or dropped instead, so the pitch stays."""
    for f in (120.0, 200.0, 310.0, 1000.0):
        clock = Clock()
        air = LiveAir("st", "show", _nowhere, delay=10, clock=clock)

        def host(t, f=f):
            n0 = int(round(t * RATE))
            return (6000 * np.sin(2 * np.pi * f * (n0 + np.arange(STEP)) / RATE)).astype(np.int16)

        run(air, clock, 12.0, host)
        air.dump()
        heard = run(air, clock, 20.0, host, t0=12.0)[RATE:]          # building
        assert air.buffered > 0.4, f                                 # it did build (3 % of 20 s)
        on, off = _tone_power(heard, f), _tone_power(heard, f * 0.97)
        assert on > 2000 and on > 8 * off, (f, on, off)
        # no clicks: the wave never jumps by more than a pure tone of this size can
        most = 6000 * 2 * np.pi * f / RATE * 1.6 + 200
        assert np.abs(np.diff(heard.astype(np.int32))).max() <= most, f
        air.exit_delay()
        heard = run(air, clock, 5.0, host, t0=32.0)                  # shrinking
        on, off = _tone_power(heard, f), _tone_power(heard, f * 1.03)
        assert on > 2000 and on > 8 * off, (f, on, off)
        assert np.abs(np.diff(heard.astype(np.int32))).max() <= most, f


# ---------- exit and build during the show ----------

def test_exit_winds_the_delay_down_to_live_and_build_brings_it_back():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)

    def host(t):
        return speech(0.05, start=t) if (t % 4) < 3 else np.zeros(STEP, dtype=np.int16)

    run(air, clock, 30.0, host)
    air.exit_delay()
    run(air, clock, 1.0, host, t0=30.0)
    assert air.status()["mode"] == "exiting"
    run(air, clock, 120.0, host, t0=31.0)
    assert air.buffered == 0 and air.passthrough() is True and air.status()["mode"] == "no delay"
    air.enter_delay(20)
    assert air.passthrough() is False and air.status()["target"] == 20
    run(air, clock, 240.0, host, t0=151.0)
    assert air.buffered == pytest.approx(20.0, abs=0.5)


def test_delay_winds_down_by_itself_before_the_hard_stop():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=20, clock=clock)
    air.end_at = clock.t + 300
    run(air, clock, 80.0, talker())                  # 220 s before the end: still delayed
    assert air.buffered == pytest.approx(20.0, abs=0.1) and air.auto_exit is False
    heard = run(air, clock, 219.9, talker(), t0=80.0)   # no pauses at all: speech is hurried
    assert air.auto_exit is True
    assert air.buffered < 0.5
    at = spoken_at(heard)
    assert np.all(np.diff(at) >= -0.011)             # nothing skipped on the way
    assert at[-1] == pytest.approx(299.9, abs=0.8)   # the host's last words air before the stop


# ---------- the show ends: the delay plays out ----------

def test_the_end_of_the_show_plays_out_then_stops():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)
    run(air, clock, 30.0, talker())
    air.finish()
    air.feed(marked(1.0, start=99.0))  # a page still sending after End show: ignored
    heard = run(air, clock, 16.0, None)
    at = spoken_at(heard)
    assert at[-1] == pytest.approx(29.99, abs=0.03)  # the goodbye was heard
    assert at.max() < 31
    assert air.drained() is True


@pytest.mark.asyncio
async def test_pump_sends_in_real_time_and_tells_listeners_when_it_is_over():
    sent, states, statuses = [], [], []

    async def send(data):
        sent.append(len(data) // 2)

    async def listening(on):
        states.append(on)

    async def status(data):
        statuses.append(data)

    air = LiveAir("st", "show", send, delay=0.4, on_listening=listening, on_status=status)
    air.start()
    for i in range(20):  # 1 s of talk
        air.feed(speech(0.05, start=i * 0.05))
        await asyncio.sleep(0.05)
    assert states == [True]
    assert 0.3 * RATE <= sum(sent) <= 0.9 * RATE  # about 0.6 s aired of 1 s said, 0.4 s waiting
    air.finish()
    await asyncio.wait_for(air.task, 3.0)
    assert states == [True, False]
    assert sum(sent) == pytest.approx(RATE, abs=RATE * 0.1)
    assert statuses and statuses[-1]["mode"] == "off"


# ---------- the time check airs on time, the host waits in the delay ----------

def test_time_check_airs_on_the_second_and_no_word_of_the_host_is_lost():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=15, clock=clock)
    run(air, clock, 30.0, talker())
    start = clock.t
    clip = np.full(int(8 * RATE), 77, dtype=np.int16)  # 8 s, value 77 (not speech-marked)
    assert air.add_insert(Insert(key="time-1", title="The time is 3 PM", pcm=clip, air_at=start + 10.0, kind="time"))
    assert air.add_insert(Insert(key="time-1", title="again", pcm=clip, air_at=start + 10.0)) is False
    assert air.status()["next_insert"]["in"] == pytest.approx(10.0, abs=0.1)
    heard = run(air, clock, 60.0, talker(), t0=30.0)
    is_clip = heard == 77
    first = int(np.argmax(is_clip))
    assert first / RATE == pytest.approx(10.0, abs=0.06)          # on its second
    assert int(is_clip.sum()) == len(clip)                         # all of it, once
    assert np.all(is_clip[first:first + len(clip)])                # in one piece
    at = spoken_at(heard[~is_clip])
    assert np.all(np.diff(at) >= -0.011)                           # the host carries on where they were
    assert air.stats.inserts[0]["key"] == "time-1"
    # The delay grew by the clip and is shrinking back to 15 s
    assert 15.0 < air.buffered < 23.0 and air.status()["mode"] == "shrinking"
    run(air, clock, 300.0, talker(), t0=90.0)
    assert air.buffered == pytest.approx(15.0, abs=0.6)


def test_items_due_together_air_one_after_another():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=5, clock=clock)
    run(air, clock, 10.0, talker())
    t = clock.t + 2
    air.add_insert(Insert(key="a", title="ID", pcm=np.full(RATE, 11, dtype=np.int16), air_at=t))
    air.add_insert(Insert(key="b", title="Time", pcm=np.full(RATE * 2, 22, dtype=np.int16), air_at=t))
    heard = run(air, clock, 8.0, talker(), t0=10.0)
    a, b = np.flatnonzero(heard == 11), np.flatnonzero(heard == 22)
    assert len(a) == RATE and len(b) == RATE * 2 and b[0] == a[-1] + 1


# ---------- the station: music until the host is heard, then back after the delay ----------

@pytest.fixture
def _quiet(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    relay = live_shows_ws.live_relay
    for d in (relay.listeners, relay.on_air, relay.hosts, relay.callers, relay.airs, relay.on_first_air):
        d.clear()
    yield
    for d in (relay.listeners, relay.on_air, relay.hosts, relay.callers, relay.airs, relay.on_first_air):
        d.clear()


async def _station(db, config=None):
    station = Station(id=uuid.uuid4(), name=f"Jerusalem {uuid.uuid4().hex[:6]}", timezone="America/New_York",
                      automation_config=config)
    db.add(station)
    song = Asset(id=uuid.uuid4(), title="Song", duration=240.0, file_path="s", asset_type="music")
    clock = Asset(id=uuid.uuid4(), title="The time is 3 PM", duration=8.0, file_path="https://x/t.mp3",
                  asset_type="jingle", category="time_announcement")
    weather = Asset(id=uuid.uuid4(), title="Weather", duration=30.0, file_path="https://x/w.mp3",
                    asset_type="jingle", category="weather_spot")
    db.add_all([song, clock, weather])
    await db.flush()
    now = datetime.now(UTC)
    db.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=1,
                      status="playing", source="auto", started_at=now - timedelta(seconds=100)))
    show = LiveShow(id=uuid.uuid4(), station_id=station.id, title="Live", status=LiveShowStatus.SCHEDULED,
                    scheduled_start=now, scheduled_end=now + timedelta(hours=1))
    db.add(show)
    await db.commit()
    return station, show, song, clock, weather


async def _playing(db, station):
    return (await db.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "playing"))).scalars().all()


@pytest.mark.asyncio
async def test_music_plays_on_until_listeners_hear_the_host(db_session, _quiet, monkeypatch):
    station, show, song, *_ = await _station(db_session)  # default: 15 s delay

    async def shared():
        yield db_session

    import app.db.session as session_module
    monkeypatch.setattr(session_module, "get_async_session", shared)

    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    await db_session.refresh(station)
    # The delay is filling: the song is still on air and the scheduler still runs the station
    assert "live_show_id" not in (station.automation_config or {})
    assert len(await _playing(db_session, station)) == 1

    # Listeners start hearing the host: the station is taken over at that moment
    await live_show_service.take_over_when_heard(str(station.id), str(show.id))
    await db_session.refresh(station)
    assert station.automation_config["live_show_id"] == str(show.id)
    assert await _playing(db_session, station) == []

    # End of show with nothing in the delay: the song carries on at once
    await live_show_service.end_show(db_session, show.id)
    await db_session.commit()
    await db_session.refresh(station)
    assert "live_show_id" not in (station.automation_config or {})
    assert [e.asset_id for e in await _playing(db_session, station)] == [song.id]


@pytest.mark.asyncio
async def test_show_ended_before_the_delay_filled_never_interrupts_the_music(db_session, _quiet):
    station, show, song, *_ = await _station(db_session)
    await live_show_service.start_show(db_session, show.id)
    await live_shows_ws.live_relay.set_on_air(str(station.id), str(show.id), delay=15)
    await live_shows_ws.live_relay.audio(str(station.id), speech(1.0).tobytes())
    await live_show_service.end_show(db_session, show.id)
    await db_session.commit()
    playing = await _playing(db_session, station)
    assert [e.asset_id for e in playing] == [song.id] and playing[0].source == "auto"  # never paused
    assert live_shows_ws.live_relay.airs == {}
    assert live_shows_ws.live_relay.state(str(station.id))["live"] is False


@pytest.mark.asyncio
async def test_end_show_lets_the_delay_play_out_before_the_music_returns(db_session, _quiet, monkeypatch):
    station, show, song, *_ = await _station(db_session, {"live_delay_seconds": 1})

    async def shared():
        yield db_session

    import app.db.session as session_module
    monkeypatch.setattr(session_module, "get_async_session", shared)

    class Listener:
        def __init__(self):
            self.text, self.samples = [], 0

        async def send_text(self, t):
            import json
            self.text.append(json.loads(t)["live"])

        async def send_bytes(self, b):
            self.samples += len(b) // 2

    relay = live_shows_ws.live_relay
    listener = Listener()
    relay.listeners[str(station.id)] = {listener}
    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    relay.on_first_air[str(station.id)] = live_show_service.take_over_when_heard
    await relay.set_on_air(str(station.id), str(show.id), delay=1.0)
    for i in range(30):  # the host talks for 1.5 s
        await relay.audio(str(station.id), speech(0.05, start=i * 0.05).tobytes())
        await asyncio.sleep(0.05)
    assert listener.text == [True]
    await db_session.refresh(station)
    assert station.automation_config.get("live_show_id") == str(show.id)

    await live_show_service.end_show(db_session, show.id)
    await db_session.commit()
    # Still on air: the last second is in the delay, and the station is not handed back yet
    assert relay.state(str(station.id))["live"] is True
    await db_session.refresh(station)
    assert station.automation_config.get("live_show_id") == str(show.id)
    assert await _playing(db_session, station) == []
    # A page still sending after End show is not heard
    await relay.audio(str(station.id), speech(0.5).tobytes())

    air = relay.airs[str(station.id)]
    await asyncio.wait_for(air.task, 4.0)
    assert listener.text == [True, False]
    assert listener.samples == pytest.approx(1.5 * RATE, abs=0.15 * RATE)  # every word, none of the late frame
    await db_session.refresh(station)
    assert "live_show_id" not in (station.automation_config or {})
    assert [e.asset_id for e in await _playing(db_session, station)] == [song.id]
    assert relay.airs == {}


@pytest.mark.asyncio
async def test_host_dropping_and_coming_back_keeps_the_same_delay(db_session, _quiet):
    relay = live_shows_ws.live_relay
    await relay.set_on_air("st-1", "show-1", delay=15)
    air = relay.airs["st-1"]
    air.listening = True
    air.feed(speech(10.0))
    await relay.host_left("st-1", "show-1")
    assert air.finishing is True and relay.airs["st-1"] is air  # playing out what was said
    await relay.set_on_air("st-1", "show-1", delay=15)
    assert relay.airs["st-1"] is air and air.finishing is False and air.target == 15
    assert air.buffered == pytest.approx(10.0, abs=0.01)
    await air.stop()


@pytest.mark.asyncio
async def test_host_back_on_a_station_already_theirs_is_heard_at_once(db_session, _quiet):
    relay = live_shows_ws.live_relay
    await relay.set_on_air("st-1", "show-1", delay=15, heard_now=True)
    air = relay.airs["st-1"]
    assert air.listening is True and relay.state("st-1")["live"] is True and air.target == 15
    await air.stop()


# ---------- which timed items air during a show ----------

@pytest.mark.asyncio
async def test_only_the_time_check_is_taken_up_by_default(db_session, _quiet, monkeypatch):
    station, show, song, clock_asset, weather = await _station(db_session)
    now = datetime.now(UTC)
    time_entry = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clock_asset.id, position=5,
                            status="pending", timing_mode="hard", preempt_at=now + timedelta(seconds=30))
    weather_entry = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=6,
                               status="pending", timing_mode="hard", preempt_at=now + timedelta(seconds=30))
    far = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clock_asset.id, position=7,
                     status="pending", timing_mode="hard", preempt_at=now + timedelta(hours=1))
    db_session.add_all([time_entry, weather_entry, far])
    await db_session.commit()

    async def shared():
        yield db_session

    import app.db.session as session_module
    monkeypatch.setattr(session_module, "get_async_session", shared)
    decoded = []

    async def fake_decode(url):
        decoded.append(url)
        return np.full(RATE * 2, 77, dtype=np.int16)

    monkeypatch.setattr(live_inserts, "decode_clip", fake_decode)
    monkeypatch.setattr("app.services.playout_engine.audio_url", lambda asset: asset.file_path)
    live_inserts._preparing.clear()

    relay = live_shows_ws.live_relay
    assert await live_inserts.prepare(db_session, station, now) == 0  # no show on air: nothing
    clock = Clock(now.timestamp())
    air = LiveAir(str(station.id), str(show.id), _nowhere, delay=5, clock=clock)
    relay.airs[str(station.id)] = air
    assert await live_inserts.prepare(db_session, station, now) == 0  # not heard yet (pre-roll)
    run(air, clock, 6.0, talker())
    assert await live_inserts.prepare(db_session, station, now) == 1
    assert await live_inserts.prepare(db_session, station, now) == 0  # not twice
    await asyncio.sleep(0.05)
    assert decoded == ["https://x/t.mp3"]
    assert [i.title for i in air.inserts] == ["The time is 3 PM"]
    assert air.inserts[0].air_at == pytest.approx(time_entry.preempt_at.replace(tzinfo=UTC).timestamp(), abs=0.01)

    # It airs: the queue entry is closed and the play log has it
    run(air, clock, 30.0, talker(), t0=6.0)
    await asyncio.sleep(0.05)
    await db_session.refresh(time_entry)
    await db_session.refresh(weather_entry)
    assert time_entry.status == "played" and weather_entry.status == "pending"
    logs = (await db_session.execute(select(PlayLog).where(PlayLog.station_id == station.id))).scalars().all()
    assert [(log.asset_id, getattr(log.source, 'value', log.source)) for log in logs] == [(clock_asset.id, "scheduler")]

    # "all": the weather too; "none": nothing
    live_inserts._preparing.clear()
    station.automation_config = {"live_timed_items": "none"}
    assert await live_inserts.prepare(db_session, station, now) == 0
    station.automation_config = {"live_timed_items": "all"}
    assert await live_inserts.prepare(db_session, station, now) == 1
    await asyncio.sleep(0.05)
    assert decoded[-1] == "https://x/w.mp3"


# ---------- the buttons: Dump, Exit delay, Build delay ----------

async def _admin_headers(db):
    from app.core.security import create_access_token, hash_password
    from app.models.user import User, UserRole

    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:8]}@t.com", hashed_password=hash_password("x"),
                role=UserRole.ADMIN, is_active=True)
    db.add(user)
    await db.commit()
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest.mark.asyncio
async def test_dump_exit_and_build_buttons(client, db_session, _quiet):
    from app.models.call_in_request import CallInRequest, CallStatus

    station, show, *_ = await _station(db_session, {"live_delay_seconds": 20, "live_dump_seconds": 8})
    headers = await _admin_headers(db_session)
    base = f"/api/v1/live-shows/{show.id}"

    # Not on air yet: the settings are shown, and the buttons say why they do nothing
    r = await client.get(f"{base}/air", headers=headers)
    assert r.status_code == 200 and r.json()["mode"] == "off" and r.json()["configured"] == 20
    assert r.json()["dump_size"] == 8
    r = await client.post(f"{base}/dump", headers=headers)
    assert r.status_code == 409 and "not on air" in r.json()["detail"]
    r = await client.post(f"{base}/delay", json={"action": "exit"}, headers=headers)
    assert r.status_code == 409
    assert (await client.post(f"{base}/dump")).status_code in (401, 403)  # signed-in staff only

    show.status = LiveShowStatus.LIVE
    caller = CallInRequest(id=uuid.uuid4(), live_show_id=show.id, caller_phone="Web caller",
                           caller_name="Moshe", status=CallStatus.ON_AIR,
                           air_start=datetime.now(UTC))
    db_session.add(caller)
    await db_session.commit()
    clock = Clock()
    air = LiveAir(str(station.id), str(show.id), _nowhere, delay=20, dump=8, clock=clock)
    live_shows_ws.live_relay.airs[str(station.id)] = air
    run(air, clock, 30.0, talker())

    r = await client.get(f"{base}/air", headers=headers)
    assert r.json()["mode"] == "steady" and r.json()["safe"] is True and r.json()["delay"] == pytest.approx(20, abs=0.2)

    r = await client.post(f"{base}/dump", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["dumped_seconds"] == 8.0 and r.json()["dropped_callers"] == []
    assert r.json()["air"]["delay"] == pytest.approx(12, abs=0.2) and r.json()["air"]["dumps"] == 1
    await db_session.refresh(caller)
    assert caller.status == CallStatus.ON_AIR  # a plain Dump leaves the caller on

    r = await client.post(f"{base}/dump", json={"drop_caller": True}, headers=headers)
    assert r.status_code == 200 and r.json()["dropped_callers"] == [str(caller.id)]
    await db_session.refresh(caller)
    assert caller.status == CallStatus.COMPLETED and caller.air_end is not None

    r = await client.post(f"{base}/delay", json={"action": "exit"}, headers=headers)
    assert r.status_code == 200 and r.json()["target"] == 0
    r = await client.post(f"{base}/delay", json={"action": "build", "seconds": 10}, headers=headers)
    assert r.status_code == 200 and r.json()["target"] == 10 and r.json()["configured"] == 10
    assert (await client.post(f"{base}/delay", json={"action": "build", "seconds": 99}, headers=headers)).status_code == 422
    assert (await client.post(f"{base}/delay", json={"action": "nonsense"}, headers=headers)).status_code == 422

    # Everything already dumped: the button says there is nothing left to cut
    air.count = 0
    r = await client.post(f"{base}/dump", headers=headers)
    assert r.status_code == 409 and "Nothing is waiting" in r.json()["detail"] and air.stats.dumps == 2

    # No delay at all: Dump says plainly that listeners already heard it
    air.exit_delay()
    air.configured = 0
    air.count = 0
    r = await client.post(f"{base}/dump", headers=headers)
    assert r.status_code == 409 and "already heard" in r.json()["detail"]


# ---------- round 2: the dump log, and callers kept off air until the delay is safe ----------

@pytest.mark.asyncio
async def test_every_dump_is_logged_with_a_recording_of_what_was_cut(client, db_session, _quiet, monkeypatch, tmp_path):
    import io
    import wave

    from app.api.v1 import live_shows as live_shows_api
    from app.services import storage_service

    monkeypatch.setattr(storage_service, "LOCAL_STORAGE_DIR", str(tmp_path))
    station, show, *_ = await _station(db_session, {"live_delay_seconds": 10})
    headers = await _admin_headers(db_session)
    base = f"/api/v1/live-shows/{show.id}"
    show.status = LiveShowStatus.LIVE
    await db_session.commit()
    assert (await client.get(f"{base}/dumps", headers=headers)).json() == {"dumps": []}

    clock = Clock()
    air = LiveAir(str(station.id), str(show.id), _nowhere, delay=10, clock=clock)
    live_shows_ws.live_relay.airs[str(station.id)] = air
    run(air, clock, 30.0, talker())  # said 0..30 s; listeners have heard 0..20 s

    r = await client.post(f"{base}/dump", headers=headers)
    assert r.status_code == 200 and r.json()["dump_id"]
    await asyncio.gather(*list(live_shows_api._dump_saves))

    log = (await client.get(f"{base}/dumps", headers=headers)).json()["dumps"]
    assert len(log) == 1
    entry = log[0]
    assert entry["id"] == r.json()["dump_id"] and entry["has_audio"] is True
    assert entry["cut_seconds"] == pytest.approx(10.0, abs=0.1) and entry["heard_seconds"] == pytest.approx(6.0, abs=0.1)
    assert entry["by"].endswith("@t.com") and entry["caller_name"] is None

    r = await client.get(f"{base}/dumps/{entry['id']}/audio", headers=headers)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(r.content)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (RATE, 1, 2)
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    heard, gap, cut = pcm[: 6 * RATE], pcm[6 * RATE: int(6.5 * RATE)], pcm[int(6.5 * RATE):]
    # First what listeners heard just before (said at 14..20 s), a gap, then what was cut (20..30 s)
    assert spoken_at(heard)[0] == pytest.approx(14.0, abs=0.1) and spoken_at(heard)[-1] == pytest.approx(20.0, abs=0.1)
    assert not gap.any()
    assert spoken_at(cut)[0] == pytest.approx(20.0, abs=0.1) and spoken_at(cut)[-1] == pytest.approx(30.0, abs=0.1)

    # Not for the public, not another show's
    assert (await client.get(f"{base}/dumps")).status_code in (401, 403)
    assert (await client.get(f"{base}/dumps/{entry['id']}/audio")).status_code in (401, 403)
    assert (await client.get(f"/api/v1/live-shows/{uuid.uuid4()}/dumps/{entry['id']}/audio", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_a_caller_cannot_go_on_air_until_the_delay_is_safe(client, db_session, _quiet):
    from app.models.call_in_request import CallInRequest, CallStatus

    station, show, *_ = await _station(db_session, {"live_delay_seconds": 10})
    headers = await _admin_headers(db_session)
    show.status = LiveShowStatus.LIVE
    caller = CallInRequest(id=uuid.uuid4(), live_show_id=show.id, caller_phone="Web caller",
                           caller_name="Moshe", status=CallStatus.APPROVED)
    db_session.add(caller)
    await db_session.commit()
    url = f"/api/v1/live-shows/{show.id}/calls/{caller.id}/on-air"

    clock = Clock()
    air = LiveAir(str(station.id), str(show.id), _nowhere, delay=10, clock=clock)
    live_shows_ws.live_relay.airs[str(station.id)] = air
    run(air, clock, 20.0, talker())
    air.dump()
    run(air, clock, 20.0, talker(), t0=20.0)   # building: well under 10 s

    r = await client.post(url, headers=headers)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "delay_not_safe"
    assert "not safe yet" in r.json()["detail"]["message"] and "of 10 s" in r.json()["detail"]["message"]
    await db_session.refresh(caller)
    assert caller.status == CallStatus.APPROVED

    # The host may decide to take the caller anyway
    r = await client.post(url + "?force=true", headers=headers)
    assert r.status_code == 200
    await db_session.refresh(caller)
    assert caller.status == CallStatus.ON_AIR

    # Safe delay, or a station with no delay at all: no question asked
    caller.status = CallStatus.APPROVED
    await db_session.commit()
    air.count = int(10 * RATE)
    assert (await client.post(url, headers=headers)).status_code == 200
    caller.status = CallStatus.APPROVED
    await db_session.commit()
    air.configured = air.target = 0.0
    air.count = 0
    assert (await client.post(url, headers=headers)).status_code == 200


# ---------- the show for the playout engine: /playout/{station}/live.mp3 ----------

@pytest.mark.asyncio
async def test_engine_pulls_the_delayed_show_as_mp3(client, db_session, _quiet, monkeypatch, tmp_path):
    import shutil
    import subprocess

    from app.config import settings

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", "engine-secret")
    station, show, *_ = await _station(db_session, {"live_delay_seconds": 1})
    url = f"/api/v1/playout/{station.id}/live.mp3"
    token = {"X-Playout-Token": "engine-secret"}

    assert (await client.get(url)).status_code == 403                        # no token
    assert (await client.get(url + "?token=engine-secret")).status_code == 403  # never from the address
    r = await client.get(url, headers=token)
    assert r.status_code == 204 and r.headers["cache-control"] == "no-store"  # no show heard

    relay = live_shows_ws.live_relay
    await relay.set_on_air(str(station.id), str(show.id), delay=1.0)
    air = relay.airs[str(station.id)]
    assert (await client.get(url, headers=token)).status_code == 204          # delay still filling

    async def host():
        for i in range(80):  # 4 s of a 440 Hz "voice", then the show ends
            n0 = i * STEP
            await relay.audio(str(station.id), (8000 * np.sin(2 * np.pi * 440 * (n0 + np.arange(STEP)) / RATE)).astype("<i2").tobytes())
            await asyncio.sleep(0.05)
        await relay.set_on_air(str(station.id), None)

    talking = asyncio.ensure_future(host())
    while not air.listening:
        await asyncio.sleep(0.02)
    r = await asyncio.wait_for(client.get(url, headers=token), 15)             # ends by itself with the show
    await talking
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert r.headers["cache-control"] == "no-store" and r.headers["x-accel-buffering"] == "no"
    assert r.headers["content-encoding"] == "identity"   # not gzipped: gzip delivers it in lumps
    assert air.taps == set()

    assert r.content[0] == 0xFF and r.content[1] & 0xE0 == 0xE0   # starts with audio, no tag before it
    mp3 = tmp_path / "live.mp3"
    mp3.write_bytes(r.content)
    info = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate",
                           "-of", "default=nw=1", str(mp3)], capture_output=True, text=True).stdout
    assert "codec_name=mp3" in info and "sample_rate=44100" in info and "channels=2" in info and "bit_rate=192000" in info
    pcm = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp3), "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
                                       capture_output=True).stdout, dtype="<i2")
    assert 2.0 * RATE <= len(pcm) <= 4.6 * RATE          # what was heard from the moment the engine joined
    assert _tone_power(pcm, 440) > 1000 and _tone_power(pcm, 440) > 20 * _tone_power(pcm, 620)


# ---------- reviewer findings on the first round (2026-11-18) ----------

def test_show_waits_for_a_timed_item_before_it_is_heard():
    clock = Clock()
    air = LiveAir("s", "show", lambda b: None, delay=6.0, clock=clock)
    air.hold_until = clock.t + 9.0   # a time check is on air until then
    heard = run(air, clock, 8.9, talk=talker())
    assert len(heard) == 0 and not air.listening and air.status()["mode"] == "pre-roll"
    assert 0 < air.status()["hold"] <= 0.2
    heard = run(air, clock, 1.0, talk=talker(), t0=8.9)
    assert air.listening and air.status()["hold"] == 0.0
    # Nothing the host said while waiting is lost: listeners get the first words first
    assert spoken_at(heard)[0] < 0.1
    assert air.buffered > 8.0   # longer than its 6 s for now; it comes back by itself


@pytest.mark.asyncio
async def test_timed_item_on_air_or_due_holds_the_show(db_session, _quiet):
    station, show, song, clock_asset, weather = await _station(db_session)
    now = datetime.now(UTC)
    assert await live_show_service.timed_item_ends(db_session, station.id, now, 8.0) is None   # only a song
    # The time check started 2.5 s ago and is 8 s long
    on_air = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clock_asset.id, position=0,
                        status="playing", source="scheduler", started_at=now - timedelta(seconds=2.5),
                        preempt_at=now - timedelta(seconds=2.5), timing_mode="hard")
    db_session.add(on_air)
    await db_session.commit()
    end = await live_show_service.timed_item_ends(db_session, station.id, now, 8.0)
    assert abs((end - now).total_seconds() - 5.5) < 0.1
    # The weather follows it in the same group: the show waits for that too
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=2,
                              status="pending", source="scheduler", preempt_at=now - timedelta(seconds=2.5),
                              timing_mode="hard"))
    # A soft item and a hard item far ahead do not hold it
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=3,
                              status="pending", source="ad_slot", preempt_at=now + timedelta(seconds=3)))
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=weather.id, position=4,
                              status="pending", source="scheduler", preempt_at=now + timedelta(seconds=300),
                              timing_mode="hard"))
    await db_session.commit()
    end = await live_show_service.timed_item_ends(db_session, station.id, now, 8.0)
    assert abs((end - now).total_seconds() - 30.0) < 0.1
    # Another station's items are not this station's business
    other, *_ = await _station(db_session)
    assert await live_show_service.timed_item_ends(db_session, other.id, now, 8.0) is None


@pytest.mark.asyncio
async def test_show_nobody_heard_is_still_hard_stopped(db_session, _quiet):
    from app.services.scheduler_engine import SchedulerEngine

    station, show, song, *_ = await _station(db_session)   # default 15 s delay, no microphone ever
    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    assert not (station.automation_config or {}).get("live_show_id")
    engine = SchedulerEngine()
    now = datetime.now(UTC)
    await engine._stop_unheard_live_show(db_session, station, now)
    await db_session.refresh(show)
    assert show.status == LiveShowStatus.LIVE          # its end has not come
    engine._unheard_checked.clear()
    await engine._stop_unheard_live_show(db_session, station, now + timedelta(hours=1, seconds=1))
    await db_session.refresh(show)
    assert show.status == LiveShowStatus.ENDED
    assert [e.asset_id for e in await _playing(db_session, station)] == [song.id]   # the music never stopped


@pytest.mark.parametrize("delay,show", [(20, 300), (30, 300), (40, 300), (40, 120), (15, 60), (40, 30)])
def test_the_delay_is_empty_by_the_hard_stop(delay, show):
    """Reviewer, 2026-11-18: the wind-down took back 21 s at most, so a long delay, or a
    short show, was cut with the host's last words still waiting."""
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=delay, clock=clock)
    air.end_at = clock.t + show
    heard = run(air, clock, show - 0.1, talker())    # the host talks without a pause up to the stop
    assert air.buffered < 0.5, f"{air.buffered:.1f} s never aired"
    at = spoken_at(heard)
    assert np.all(np.diff(at) >= -0.011)             # nothing skipped
    assert at[-1] > show - 0.8                       # the last words were heard
    assert air.status()["auto_exit"] is True


# ---------- findings of the live check on Jerusalem (2026-11-18) ----------

@pytest.mark.asyncio
async def test_station_goes_back_to_music_when_the_host_is_gone(db_session, _quiet, monkeypatch):
    from app.models.alert import Alert
    from app.services import bug_report_service
    from app.services.scheduler_engine import SchedulerEngine

    pushes = []

    async def push(message, title, **_):
        pushes.append((title, message))
        return True

    monkeypatch.setattr(bug_report_service, "send_ntfy", push)
    station, show, song, *_ = await _station(db_session, {"live_delay_seconds": 0, "live_host_gone_seconds": 15})
    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    await db_session.refresh(station)
    assert station.automation_config["live_show_id"] == str(show.id) and await _playing(db_session, station) == []
    relay = live_shows_ws.live_relay
    await relay.set_on_air(str(station.id), str(show.id), delay=0.0)
    engine = SchedulerEngine()
    now = datetime.now(UTC)

    await relay.audio(str(station.id), speech(0.05).tobytes())
    assert await engine._host_gone(db_session, station, show, now) is False      # the host is talking
    # Muted (silent frames) for a long time is still a host
    engine._host_gone_since[str(station.id)] = time.monotonic() - 60
    await relay.audio(str(station.id), np.zeros(STEP, dtype="<i2").tobytes())
    assert await engine._host_gone(db_session, station, show, now) is False
    assert str(station.id) not in engine._host_gone_since

    # The host's page closes
    await relay.host_left(str(station.id), str(show.id))
    assert await engine._host_gone(db_session, station, show, now) is False      # 0 s gone
    engine._host_gone_since[str(station.id)] = time.monotonic() - 14
    assert await engine._host_gone(db_session, station, show, now) is False      # 14 s gone
    assert await _playing(db_session, station) == []
    engine._host_gone_since[str(station.id)] = time.monotonic() - 15.5
    assert await engine._host_gone(db_session, station, show, now) is True       # 15 s: music is back
    await db_session.refresh(station)
    await db_session.refresh(show)
    assert "live_show_id" not in station.automation_config
    assert [e.asset_id for e in await _playing(db_session, station)] == [song.id]
    assert show.status == LiveShowStatus.LIVE                                     # the host may come back
    alerts = (await db_session.execute(select(Alert).where(Alert.station_id == station.id))).scalars().all()
    assert [a.title for a in alerts] == ["Live show lost its host: Live"] and "went back to its music" in alerts[0].message
    assert len(pushes) == 1 and "No sound from the host" in pushes[0][1]
    assert relay.state(str(station.id))["live"] is False


@pytest.mark.asyncio
async def test_frozen_host_connection_counts_as_gone_and_zero_switches_it_off(db_session, _quiet, monkeypatch):
    from app.services import bug_report_service
    from app.services.scheduler_engine import SchedulerEngine

    async def push(*a, **k):
        return True

    monkeypatch.setattr(bug_report_service, "send_ntfy", push)
    station, show, song, *_ = await _station(db_session, {"live_delay_seconds": 0, "live_host_gone_seconds": 0})
    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    await db_session.refresh(station)
    relay = live_shows_ws.live_relay
    clock = Clock(time.time())
    await relay.set_on_air(str(station.id), str(show.id), delay=0.0)
    air = relay.airs[str(station.id)]
    air.clock = clock
    air.last_input = clock.t
    engine = SchedulerEngine()
    now = datetime.now(UTC)
    clock.t += 120   # connected, but not a frame for two minutes
    assert await engine._host_gone(db_session, station, show, now) is False      # 0 = never hand back
    station.automation_config = {**station.automation_config, "live_host_gone_seconds": 15}
    engine._host_gone_since.clear()
    assert await engine._host_gone(db_session, station, show, now) is True
    assert air.closed and [e.asset_id for e in await _playing(db_session, station)] == [song.id]


@pytest.mark.asyncio
async def test_stream_server_is_told_a_second_before_the_host_is_heard(db_session, _quiet, monkeypatch):
    from app.config import settings
    from app.services import playout_engine

    station, show, *_ = await _station(db_session, {"live_delay_seconds": 3})
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", str(station.id))
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", "t")
    said = []

    async def live(station_id, on):
        said.append((on, relay.airs[str(station.id)].buffered if str(station.id) in relay.airs else None))
        return True

    monkeypatch.setattr(playout_engine, "engine_live", live)
    relay = live_shows_ws.live_relay
    await relay.set_on_air(str(station.id), str(show.id), delay=3.0)
    air = relay.airs[str(station.id)]
    for _ in range(36):   # 1.8 s of host
        await relay.audio(str(station.id), speech(0.05).tobytes())
    await asyncio.sleep(0.15)
    assert said == [] and not air.listening
    for _ in range(6):    # 2.1 s: within a second of first air
        await relay.audio(str(station.id), speech(0.05).tobytes())
    await asyncio.sleep(0.15)
    assert [on for on, _ in said] == [True] and not air.listening
    # Ended before anyone heard it: the stream gets its sound back
    await relay.set_on_air(str(station.id), None)
    await asyncio.sleep(0.1)
    assert [on for on, _ in said] == [True, False]


@pytest.mark.asyncio
async def test_a_time_check_cut_by_the_show_does_not_come_back_after_it(db_session, _quiet):
    station, show, song, clock_asset, _w = await _station(db_session, {"live_delay_seconds": 0})
    now = datetime.now(UTC)
    for e in await _playing(db_session, station):
        e.status = "played"
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clock_asset.id, position=0,
                              status="playing", source="scheduler", started_at=now - timedelta(seconds=2),
                              preempt_at=now - timedelta(seconds=2), timing_mode="hard"))
    await db_session.commit()
    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    left = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "pending"))).scalars().all()
    assert [e.asset_id for e in left if e.asset_id == clock_asset.id] == []


@pytest.mark.asyncio
async def test_a_time_check_cut_as_it_started_airs_over_the_delay(db_session, _quiet):
    """The engine started the time check early just as the delay filled: the take-over
    cut it after 0.8 s. It waits again, so the delay airs it whole at its time."""
    from app.services import live_inserts

    station, show, song, clock_asset, weather = await _station(db_session, {"live_delay_seconds": 15})
    now = datetime.now(UTC)
    for e in await _playing(db_session, station):
        e.status = "played"
    cut = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clock_asset.id, position=0,
                     status="playing", source="scheduler", started_at=now - timedelta(seconds=0.8),
                     preempt_at=now + timedelta(seconds=6), timing_mode="hard")
    db_session.add(cut)
    await db_session.commit()
    live_inserts._preparing.add(str(cut.id))
    await live_show_service.take_over_station(db_session, station, show, now)
    await db_session.commit()
    await db_session.refresh(cut)
    assert cut.status == "pending" and cut.started_at is None
    assert str(cut.id) not in live_inserts._preparing

    # Heard more than half: not said twice. And weather is not aired over the delay by default.
    for asset, heard in ((clock_asset, 5.0), (weather, 0.8)):
        station.automation_config = {"live_delay_seconds": 15}
        entry = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id, position=0,
                           status="playing", source="scheduler", started_at=now - timedelta(seconds=heard),
                           preempt_at=now, timing_mode="hard")
        db_session.add(entry)
        await db_session.commit()
        await live_show_service.take_over_station(db_session, station, show, now)
        await db_session.commit()
        await db_session.refresh(entry)
        assert entry.status == "played", asset.title


@pytest.mark.asyncio
async def test_host_gone_is_counted_from_the_last_frame_not_from_the_end_of_the_delay(db_session, _quiet, monkeypatch):
    """The host's page closes with 8 s in the delay and a limit of 8 s: when the delay
    has played out the station goes back at once, not 8 s of silence later."""
    from app.services import bug_report_service
    from app.services.scheduler_engine import SchedulerEngine

    async def push(message, title, **_):
        return True

    monkeypatch.setattr(bug_report_service, "send_ntfy", push)
    station, show, song, *_ = await _station(db_session, {"live_delay_seconds": 8, "live_host_gone_seconds": 8})
    await live_show_service.start_show(db_session, show.id)
    await live_show_service.take_over_station(db_session, station, show, datetime.now(UTC))
    await db_session.commit()
    relay = live_shows_ws.live_relay
    sid = str(station.id)
    await relay.set_on_air(sid, str(show.id), delay=8.0, heard_now=True)
    air = relay.airs[sid]
    engine = SchedulerEngine()
    now = datetime.now(UTC)
    await relay.audio(sid, speech(0.05).tobytes())
    assert await engine._host_gone(db_session, station, show, now) is False
    air.count = max(air.count, 1)            # something is still in the delay
    air.last_input = air.clock() - 9.0       # the last frame came 9 s ago
    air.finishing, air.finish_reason = True, "host left"
    # The words still in the delay play out: not handed back yet, but the count runs
    assert await engine._host_gone(db_session, station, show, now) is False
    assert time.monotonic() - engine._host_gone_since[sid] >= 8.9
    await air.stop()
    relay.airs.pop(sid, None)
    assert await engine._host_gone(db_session, station, show, now) is True
    await db_session.refresh(station)
    assert "live_show_id" not in station.automation_config
    assert [e.asset_id for e in await _playing(db_session, station)] == [song.id]


# ---------- the stream carries the show (engine proto 5) ----------

@pytest.mark.asyncio
async def test_pages_are_told_when_the_stream_carries_the_show_and_can_decline_the_audio(db_session, _quiet, monkeypatch):
    from app.config import settings
    from app.services import playout_engine

    class Sock:
        def __init__(self):
            self.frames, self.texts = 0, []

        async def send_bytes(self, data):
            self.frames += 1

        async def send_text(self, text):
            self.texts.append(text)

    relay = live_shows_ws.live_relay
    station = str(uuid.uuid4())
    assert relay.state(station)["on_stream"] is False                 # browser-player station
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", station)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", "t")
    monkeypatch.setattr(settings, "PLAYOUT_STREAM_LIVE", True)
    monkeypatch.setattr(settings, "ICECAST_STREAM_URL", "https://stream.invalid/live")
    playout_engine.reset_state(station)
    assert settings.public_stream_url(station)
    assert relay.state(station)["on_stream"] is False                 # an older engine: pages play the frames
    playout_engine.get_state(station).engine_proto = playout_engine.ENGINE_PROTO_LIVE_AUDIO
    assert relay.state(station)["on_stream"] is True

    stream_page, other_page = Sock(), Sock()
    relay.listeners[station] = {stream_page, other_page}
    relay.no_audio.add(stream_page)                                   # it sent {"type":"audio","on":false}
    try:
        await relay._send_frame(station, b"\x00\x00" * 10)
        await relay.broadcast_state(station)
        assert (stream_page.frames, other_page.frames) == (0, 1)      # no show audio for the stream's page
        assert len(stream_page.texts) == 1 and len(other_page.texts) == 1   # both still learn the state
    finally:
        relay.listeners.pop(station, None)
        relay.no_audio.discard(stream_page)
        playout_engine.reset_state(station)
