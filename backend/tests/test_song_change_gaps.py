"""Song changes without quiet gaps.

The Mac's listener heard 2 to 3 s of near silence at song changes. Causes found:
- the length probe read the header from a pipe, where ffmpeg says "Duration: N/A"
  for MP3, so every analysed item was measured as 180 s (its end and crossfade point
  at 177-180 s whatever its real length, and its stored length set to 180 s);
- items without analysis crossfade 3 s before the stored end, over a quiet tail.
"""
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.asset import ANALYSIS_VERSION, needs_analysis, stored_analysis

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _song_mp3(seconds_loud: float, tail_silence: float) -> bytes:
    """Noise for `seconds_loud`, a 4 s fade-out, then `tail_silence` of silence."""
    total = seconds_loud + 4 + tail_silence
    cmd = [
        "ffmpeg", "-loglevel", "error", "-f", "lavfi",
        "-i", f"anoisesrc=c=pink:a=0.3:d={seconds_loud + 4},afade=t=out:st={seconds_loud}:d=4,apad",
        "-t", str(total), "-ac", "1", "-ar", "44100", "-b:a", "128k", "-f", "mp3", "pipe:1",
    ]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


@needs_ffmpeg
def test_length_of_piped_mp3_is_measured():
    from app.services.silence_service import get_audio_duration

    length = get_audio_duration(_song_mp3(20, 3))
    assert 26.5 < length < 27.5


@needs_ffmpeg
def test_analysis_finds_the_real_end():
    from app.services.audio_analysis_service import analyze_audio_bytes

    a = analyze_audio_bytes(_song_mp3(20, 3))
    assert a["version"] == ANALYSIS_VERSION
    assert 26.5 < a["duration"] < 27.5
    # the quiet tail is cut off and the hand-over comes before it
    assert a["cue_out_seconds"] < 24.5
    assert a["cross_start_seconds"] < a["cue_out_seconds"]


def test_old_analysis_keeps_only_start_and_loudness():
    old = {"audio_analysis": {"cue_in_seconds": 0.4, "cue_out_seconds": 180.0,
                              "cross_start_seconds": 177.0, "replay_gain_db": -2.0,
                              "duration": 180.0}}
    assert stored_analysis(old) == {"cue_in_seconds": 0.4, "replay_gain_db": -2.0}
    new = {"audio_analysis": {**old["audio_analysis"], "cross_start_seconds": 150.0,
                              "version": ANALYSIS_VERSION}}
    assert stored_analysis(new)["cross_start_seconds"] == 150.0
    assert stored_analysis(None) == {}
    assert needs_analysis(SimpleNamespace(metadata_extra=old))
    assert needs_analysis(SimpleNamespace(metadata_extra=None))
    assert not needs_analysis(SimpleNamespace(metadata_extra=new))


def test_server_change_ignores_an_old_crossfade_point():
    from app.api.v1.queue import switch_time

    started = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)
    asset = SimpleNamespace(duration=160.0, metadata_extra={"audio_analysis": {
        "cue_out_seconds": 180.0, "cross_start_seconds": 120.0}})
    entry = SimpleNamespace(asset=asset, started_at=started)
    # an old analysis's point is not trusted: 3 s before the end
    assert switch_time(entry) == started + timedelta(seconds=157.0)
    asset.metadata_extra["audio_analysis"]["version"] = ANALYSIS_VERSION
    assert switch_time(entry) == started + timedelta(seconds=120.0)


@pytest.mark.asyncio
async def test_upcoming_items_are_queued_once(monkeypatch):
    from app.services import upcoming_analysis

    ran = []

    async def fake_run():
        while not upcoming_analysis._queue.empty():
            ran.append(await upcoming_analysis._queue.get())

    monkeypatch.setattr(upcoming_analysis, "ENABLED", True)
    monkeypatch.setattr(upcoming_analysis, "_seen", set())
    monkeypatch.setattr(upcoming_analysis, "_queue", None)
    monkeypatch.setattr(upcoming_analysis, "_worker", None)
    monkeypatch.setattr(upcoming_analysis, "_run", fake_run)
    fresh = SimpleNamespace(id=uuid.uuid4(), file_path="a.mp3", metadata_extra=None)
    done = SimpleNamespace(id=uuid.uuid4(), file_path="b.mp3", metadata_extra={
        "audio_analysis": {"version": ANALYSIS_VERSION}})
    for _ in range(3):
        upcoming_analysis.request(fresh)
        upcoming_analysis.request(done)
    await upcoming_analysis._worker
    assert ran == [str(fresh.id)]
