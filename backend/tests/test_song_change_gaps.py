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
    # an old analysis's point is not trusted: the station's crossfade (1 s) before the end
    assert switch_time(entry) == started + timedelta(seconds=159.0)
    asset.metadata_extra["audio_analysis"]["version"] = ANALYSIS_VERSION
    # a current one is: audible end at 123 s (the point + 3), handed over 1 s before it
    assert switch_time(entry) == started + timedelta(seconds=122.0)


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
        "audio_analysis": {"version": ANALYSIS_VERSION, "loudness_lufs": -16.0, "replay_gain_db": -2.0}})
    for _ in range(3):
        upcoming_analysis.request(fresh)
        upcoming_analysis.request(done)
    await upcoming_analysis._worker
    assert ran == [str(fresh.id)]


def _range_server(data: bytes, served: list):
    import httpx

    def handler(request):
        rng = request.headers.get("range")
        if not rng:
            served.append(len(data))
            return httpx.Response(200, content=data, headers={"content-length": str(len(data))})
        start, end = rng.split("=")[1].split("-")
        start, end = int(start), min(int(end), len(data) - 1)
        body = data[start:end + 1]
        served.append(len(body))
        return httpx.Response(206, content=body, headers={
            "content-range": f"bytes {start}-{end}/{len(data)}", "content-length": str(len(body))})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _with_big_cover(mp3: bytes) -> bytes:
    import tempfile

    from mutagen.id3 import APIC, ID3
    with tempfile.NamedTemporaryFile(suffix=".mp3") as f:
        f.write(mp3)
        f.flush()
        tags = ID3()
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="cover", data=b"\xff" * 300_000))
        tags.save(f.name)
        return open(f.name, "rb").read()


@needs_ffmpeg
@pytest.mark.asyncio
@pytest.mark.parametrize("cover", [False, True])
async def test_end_found_from_a_few_ranges(cover):
    """Version-2 points without downloading the file: header, last seconds, start."""
    from app.services.tail_analysis import analyze_by_range

    song = _song_mp3(60, 3)  # 67 s: fade 60-64, silent 64-67
    if cover:
        song = _with_big_cover(song)
    served = []
    async with _range_server(song, served) as client:
        a = await analyze_by_range("https://cdn.example.com/s.mp3",
                                   {"cue_in_seconds": 0.0, "loudness_lufs": -15.0, "replay_gain_db": -3.0},
                                   client=client)
    assert a["version"] == ANALYSIS_VERSION
    assert 66 < a["duration"] < 68
    assert 62 < a["cue_out_seconds"] < 64.5
    assert a["cross_start_seconds"] == pytest.approx(a["cue_out_seconds"] - 3, abs=0.01)
    # stored loudness kept; only a small part of the file read
    assert a["replay_gain_db"] == -3.0 and a["loudness_lufs"] == -15.0
    assert sum(served) < 0.5 * len(song)


@needs_ffmpeg
@pytest.mark.asyncio
async def test_start_cue_measured_when_none_stored():
    from app.services.tail_analysis import analyze_by_range

    lead = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
         "anullsrc=r=44100:cl=mono:d=1.5", "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.3:d=40",
         "-filter_complex", "[0][1]concat=n=2:v=0:a=1", "-b:a", "128k", "-f", "mp3", "pipe:1"],
        capture_output=True, check=True).stdout
    async with _range_server(lead, []) as client:
        a = await analyze_by_range("https://cdn.example.com/s.mp3", None, client=client)
    assert 1.2 < a["cue_in_seconds"] < 1.8
    # no stored loudness: it is measured, not left at a made-up -18 LUFS
    assert a["loudness_method"] == "sampled"


def _tone_mp3(seconds: float, volume: float) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
         f"anoisesrc=c=pink:a={volume}:d={seconds}", "-ac", "1", "-ar", "44100",
         "-b:a", "128k", "-f", "mp3", "pipe:1"], capture_output=True, check=True).stdout


@needs_ffmpeg
@pytest.mark.asyncio
@pytest.mark.parametrize("volume", [0.05, 1.0])
async def test_loudness_sampled_when_none_was_measured(volume):
    """Jerusalem 18:00Z: items analysed only from their ends kept -18 LUFS / 0 dB and
    aired from -13.5 to -19.3 LUFS. Now three pieces are measured, within 1 dB of a
    full measurement, reading under half the file."""
    from app.services.audio_analysis_service import analyze_audio_bytes
    from app.services.tail_analysis import analyze_by_range

    song = _tone_mp3(150, volume)
    full = analyze_audio_bytes(song)
    served = []
    fake = {"cue_in_seconds": 0.0, "loudness_lufs": -18.0, "replay_gain_db": 0.0,
            "method": "range", "version": ANALYSIS_VERSION}
    async with _range_server(song, served) as client:
        a = await analyze_by_range("https://cdn.example.com/s.mp3", fake, client=client)
    assert a["loudness_method"] == "sampled"
    assert a["loudness_lufs"] == pytest.approx(full["loudness_lufs"], abs=1.0)
    assert a["replay_gain_db"] == pytest.approx(full["replay_gain_db"], abs=1.0)
    assert abs(a["replay_gain_db"]) > 2  # neither test file is near -18 LUFS
    assert sum(served) < 0.5 * len(song)
    # measured once: the next range analysis keeps it
    async with _range_server(song, []) as client:
        again = await analyze_by_range("https://cdn.example.com/s.mp3", a, client=client)
    assert again["loudness_lufs"] == a["loudness_lufs"] and again["loudness_method"] == "sampled"


def test_unmeasured_loudness_is_analysed_again():
    from app.models.asset import has_real_loudness

    fake = {"loudness_lufs": -18.0, "replay_gain_db": 0.0, "method": "range", "version": ANALYSIS_VERSION}
    assert not has_real_loudness(fake)
    assert needs_analysis(SimpleNamespace(metadata_extra={"audio_analysis": fake}))
    kept = {**fake, "loudness_lufs": -14.2, "replay_gain_db": -3.8}  # kept from a full analysis
    assert has_real_loudness(kept)
    assert not needs_analysis(SimpleNamespace(metadata_extra={"audio_analysis": kept}))
    sampled = {**fake, "loudness_method": "sampled"}  # really measured at -18
    assert has_real_loudness(sampled)
    full = {"loudness_lufs": -18.0, "replay_gain_db": 0.0, "version": ANALYSIS_VERSION}
    assert has_real_loudness(full)


def test_combined_loudness_is_a_power_average():
    from app.services.tail_analysis import combine_loudness

    lufs, peak = combine_loudness([(-10.0, -1.0), (-20.0, -5.0), (None, None), (-70.0, -60.0)])
    assert lufs == pytest.approx(-12.6, abs=0.05) and peak == -1.0
    assert combine_loudness([(None, None)]) is None


@needs_ffmpeg
@pytest.mark.asyncio
async def test_unbelievable_sampled_loudness_falls_back_to_the_whole_file(monkeypatch):
    """The Carlebach story "Yisrael Betach" read -3.1 LUFS from its sampled pieces. A
    reading louder than -5 LUFS is not stored from pieces: the whole file is measured."""
    from app.services import tail_analysis
    from app.services.tail_analysis import LoudnessNotSampledError, analyze_by_range

    song = _tone_mp3(60, 0.3)
    monkeypatch.setattr(tail_analysis, "combine_loudness", lambda m: (-3.1, -0.1))
    async with _range_server(song, []) as client:
        with pytest.raises(LoudnessNotSampledError):
            await analyze_by_range("https://cdn.example.com/s.mp3", None, client=client)


def test_bad_sampled_reading_is_analysed_again_and_loud_items_turn_down_further():
    from app.models.asset import has_real_loudness
    from app.services.audio_analysis_service import replay_gain_for

    bad = {"loudness_lufs": -3.1, "replay_gain_db": -12.0, "method": "range",
           "loudness_method": "sampled", "version": ANALYSIS_VERSION}
    assert not has_real_loudness(bad)
    assert needs_analysis(SimpleNamespace(metadata_extra={"audio_analysis": bad}))
    assert replay_gain_for(-3.1) == -14.9
    assert replay_gain_for(5.0) == -20.0 and replay_gain_for(-40.0) == 12.0
