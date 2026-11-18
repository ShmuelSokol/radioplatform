"""The show's picture passes the same delay as its sound (services/live_video.py)."""
import numpy as np
import pytest

from app.services.live_air import RATE, Insert, LiveAir
from app.services.live_video import LiveVideo, clean_config
from tests.test_live_delay import STEP, Clock, _nowhere, marked

CONFIG = {"codec": "vp8", "width": 640, "height": 360}


def frame(t: float, key: bool = False) -> tuple[bool, bytes]:
    """A stand-in frame that says when it was filmed (hundredths of a second)."""
    return key, int(round(t * 100)).to_bytes(4, "big")


def filmed(data: bytes) -> float:
    return int.from_bytes(data, "big") / 100


def show(air: LiveAir, video: LiveVideo, clock: Clock, seconds: float, t0: float = 0.0, talk: bool = True):
    """Run a show: sound and a frame every 0.1 s (keyframe every second). Returns
    [(time aired, time filmed, keyframe)] and how often a keyframe was asked for."""
    aired, asked = [], 0
    want_key = False
    for i in range(int(round(seconds / 0.05))):
        t = t0 + i * 0.05
        if i % 2 == 0:
            key = want_key or abs(t - round(t)) < 0.001
            if video.push(air, *frame(t, key)[:1], frame(t, key)[1]):
                want_key = False
            else:
                asked += 1
                want_key = True
        if talk:
            air.feed(marked(0.05, start=t))
        air.produce(STEP)
        for key, data in video.due(air):
            aired.append((round(t, 2), filmed(data), key))
        clock.t += 0.05
    return aired, asked


def test_config_is_checked():
    assert clean_config({"codec": "vp8", "width": 640, "height": 360}) == CONFIG
    assert clean_config({"codec": "avc1.42E01F", "width": "1280", "height": 720})["codec"] == "avc1.42E01F"
    for bad in ({}, {"codec": "theora", "width": 640, "height": 360}, {"codec": "vp8", "width": 0, "height": 360},
                {"codec": "vp8", "width": 9000, "height": 360}, {"codec": "vp8" + "x" * 50, "width": 640, "height": 360},
                {"codec": "vp8", "width": "wide", "height": 360}):
        assert clean_config(bad) is None


def test_the_picture_airs_with_its_sound_the_delay_later():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=5, clock=clock)
    video = LiveVideo("show", CONFIG)
    aired, asked = show(air, video, clock, 20.0)
    assert asked == 0
    assert aired[0][2] is True                            # the first frame out is a keyframe
    assert aired[0][0] == pytest.approx(5.0, abs=0.15)    # nothing before listeners hear the show
    for at, was, _key in aired:
        assert at - was == pytest.approx(5.0, abs=0.15)
    assert [was for _at, was, _key in aired] == sorted(was for _at, was, _key in aired)
    assert video.waiting_bytes == 4 * len(video.frames)


def test_no_delay_means_the_picture_goes_straight_out():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=0, clock=clock)
    video = LiveVideo("show", CONFIG)
    aired, _ = show(air, video, clock, 3.0)
    assert all(at - was <= 0.11 for at, was, _key in aired) and len(aired) >= 28


def test_nothing_is_taken_before_a_keyframe():
    air = LiveAir("st", "show", _nowhere, delay=5, clock=Clock())
    video = LiveVideo("show", CONFIG)
    assert video.push(air, False, b"delta") is False and not video.frames
    assert video.push(air, True, b"key") is True
    assert video.push(air, False, b"delta") is True and len(video.frames) == 2
    assert video.push(air, True, b"x" * 700_000) is True and len(video.frames) == 2   # absurd frame: ignored


def test_a_dump_cuts_the_same_seconds_of_picture_and_asks_for_a_keyframe():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=10, dump=4, clock=clock)
    video = LiveVideo("show", CONFIG)
    before, _ = show(air, video, clock, 30.0)
    assert air.dump() == pytest.approx(4.0, abs=0.01)
    dropped = video.cut(air)
    assert dropped == pytest.approx(40, abs=2)            # 4 s at ten frames a second
    after, asked = show(air, video, clock, 30.0, t0=30.1)
    assert asked == 1                                      # the camera is asked once, then sends one
    seen = [was for _at, was, _key in before + after]
    assert not [t for t in seen if 26.1 < t < 29.95]       # filmed in the cut seconds: never shown
    assert [t for t in seen if 25.0 < t <= 26.0]           # what was kept still airs
    first_new = next(x for x in after if x[1] >= 29.95)
    assert first_new[2] is True                            # and the picture restarts on a keyframe
    assert first_new[0] - 30.0 == pytest.approx(6.0, abs=0.3)   # right after the 6 s that stayed
    # The sound of 26 s airs when the picture of 26 s does
    at_26 = next(at for at, was, _key in after if abs(was - 26.0) < 0.06)
    assert at_26 == pytest.approx(36.1, abs=0.3)   # a little later: the delay is building back


def test_a_full_dump_leaves_no_picture_waiting():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=8, clock=clock)
    video = LiveVideo("show", CONFIG)
    show(air, video, clock, 20.0)
    air.dump()
    assert video.cut(air) >= 78 and not video.frames and video.waiting_bytes == 0 and video.need_key


def test_the_picture_holds_while_a_time_check_airs_over_the_delay():
    clock = Clock()
    air = LiveAir("st", "show", _nowhere, delay=5, clock=clock)
    video = LiveVideo("show", CONFIG)
    show(air, video, clock, 10.0)
    air.add_insert(Insert(key="t", title="The time is 3 PM", pcm=np.full(RATE * 3, 77, dtype=np.int16),
                          air_at=clock.t))
    during, _ = show(air, video, clock, 3.0, t0=10.0)
    assert during == []                                    # the last picture stays on screen
    after, _ = show(air, video, clock, 10.0, t0=13.0)
    assert after[0][1] == pytest.approx(5.0, abs=0.15)     # and carries on where the sound does
    assert len(video.frames) < 120


def test_too_much_waiting_starts_again_from_a_keyframe(monkeypatch):
    monkeypatch.setattr("app.services.live_video.MAX_WAITING_BYTES", 1000)
    air = LiveAir("st", "show", _nowhere, delay=5, clock=Clock())
    video = LiveVideo("show", CONFIG)
    assert video.push(air, True, b"k" * 600)
    assert video.push(air, False, b"d" * 600) is False and not video.frames and video.need_key
    assert video.push(air, True, b"k" * 600) and len(video.frames) == 1
