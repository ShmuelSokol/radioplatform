"""Live shows are recorded as they aired and land in Archives as drafts.
See app/services/live_recording.py."""
import asyncio
import shutil
import subprocess
import uuid

import numpy as np
import pytest
from sqlalchemy import select

from app.api.v1 import live_shows_ws
from app.models.live_show import LiveShowStatus
from app.models.show_archive import ShowArchive
from app.services import live_recording, storage_service
from app.services.live_air import RATE
from tests.test_live_delay import _station

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_recording_is_on_unless_the_station_switches_it_off():
    assert live_recording.wanted(None) and live_recording.wanted({}) and live_recording.wanted({"live_record_shows": True})
    assert not live_recording.wanted({"live_record_shows": False})


def _tone(f, seconds):
    return (8000 * np.sin(2 * np.pi * f * np.arange(int(seconds * RATE)) / RATE)).astype("<i2")


def _power(pcm, f):
    x = pcm.astype(np.float64)
    t = np.arange(len(x)) / RATE
    return abs(np.sum(x * np.exp(-2j * np.pi * f * t))) / max(1, len(x))


@needs_ffmpeg
@pytest.mark.asyncio
async def test_the_recording_is_what_listeners_heard_without_what_was_dumped(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage_service, "LOCAL_STORAGE_DIR", str(tmp_path / "store"))
    station, show, *_ = await _station(db_session, {"live_delay_seconds": 2})
    show.status = LiveShowStatus.LIVE
    show.description = "Weekly call-in"
    await db_session.commit()
    relay = live_shows_ws.live_relay
    sid = str(station.id)
    await relay.set_on_air(sid, str(show.id), delay=2.0)       # recorded by hand below
    air = relay.airs[sid]
    path = str(tmp_path / "show.mp3")
    recording = asyncio.ensure_future(live_recording.capture(air, path))
    await asyncio.sleep(0)

    step = RATE // 20
    async def say(f, seconds):
        tone = _tone(f, seconds)
        for i in range(0, len(tone), step):
            await relay.audio(sid, tone[i:i + step].tobytes())
            await asyncio.sleep(0.05)

    await say(440, 4.0)          # heard
    await say(1000, 1.0)         # the curse
    air.dump()                   # ...cut before it airs
    await say(440, 2.0)
    await relay.set_on_air(sid, None)                          # End show: the delay plays out
    seconds, started = await asyncio.wait_for(recording, 20)
    assert started is not None and 3.5 < seconds < 8.0

    pcm = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"],
                                       capture_output=True).stdout, dtype="<i2")
    assert abs(len(pcm) / RATE - seconds) < 0.3
    assert _power(pcm, 440) > 500
    assert _power(pcm, 1000) < _power(pcm, 440) / 50           # what was dumped is not in the recording

    archive = await live_recording.save(db_session, station.id, show.id, path, seconds, started)
    await db_session.commit()
    rows = (await db_session.execute(select(ShowArchive).where(ShowArchive.live_show_id == show.id))).scalars().all()
    assert len(rows) == 1 and rows[0].id == archive.id
    a = rows[0]
    assert a.title == "Live" and a.description == "Weekly call-in" and a.station_id == station.id
    assert a.is_published is False                              # a draft: someone listens before it is public
    assert a.duration_seconds == int(round(seconds)) and a.recorded_at is not None
    assert a.audio_url.startswith(f"show-recordings/{show.id}/") and a.audio_url.endswith(".mp3")
    stored = await storage_service.download_file(a.audio_url)
    assert stored[:2] in (b"ID", b"\xff\xf3", b"\xff\xfb", b"\xff\xe3") and len(stored) > 5000

    # The host dropped and came back later: a second part, not a second "Live"
    again = await live_recording.save(db_session, station.id, show.id, path, 65.0, started)
    assert again.title == "Live (part 2)"
    assert await live_recording.save(db_session, station.id, uuid.uuid4(), path, 65.0, started) is None


@needs_ffmpeg
@pytest.mark.asyncio
async def test_a_show_of_a_few_seconds_leaves_no_recording_and_a_station_can_switch_it_off(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(storage_service, "LOCAL_STORAGE_DIR", str(tmp_path / "store"))
    started = []
    monkeypatch.setattr(live_recording, "start", lambda station_id, show_id, air: started.append(show_id))
    station, show, *_ = await _station(db_session, {"live_delay_seconds": 0})
    relay = live_shows_ws.live_relay
    await relay.set_on_air(str(station.id), str(show.id), delay=0.0)                 # record not asked
    await relay.set_on_air(str(station.id), None)
    assert started == []
    await relay.set_on_air(str(station.id), str(show.id), delay=0.0, record=True)
    assert started == [str(show.id)]
    await relay.set_on_air(str(station.id), str(show.id), delay=0.0, record=True)    # the host reconnects: same air
    assert started == [str(show.id)]
    await relay.set_on_air(str(station.id), None)
    assert live_recording.MIN_SECONDS >= 10
