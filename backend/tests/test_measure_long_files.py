"""A long, large MP3 measures, also when FFmpeg cannot read it from a pipe; items
wrongly taken off the air come back.

2026-11-18: "chanuka radio pt02" (44 MB, 35:04) and "Story the Riminover and the wind"
(14 MB, 14:44) were taken off the air because their length "could not be measured",
though both play and ffprobe reads them. The measure read the audio from a pipe.
"""
import io
import shutil
import subprocess
import uuid

import pytest
from mutagen.id3 import ID3, TIT2
from sqlalchemy import select

from app.models.alert import Alert
from app.models.asset import Asset
from app.services import activity_log, audio_analysis_service, upcoming_analysis
from app.services.audio_analysis_service import analyze_audio_bytes
from app.services.silence_service import get_audio_duration
from tests.conftest import TestSessionLocal

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg not installed")


def _mp3(seconds: int, kbps: int) -> bytes:
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
           "-ac", "2", "-b:a", f"{kbps}k", "-f", "mp3", "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True, timeout=600).stdout


def _id3() -> bytes:
    tag = ID3()
    tag.add(TIT2(encoding=3, text="chanuka radio pt02"))
    buf = io.BytesIO()
    tag.save(buf, v2_version=3)
    return buf.getvalue()


@pytest.fixture(scope="module")
def long_mp3() -> bytes:
    return _id3() + _mp3(2104, 168)  # 35:04, about 44 MB like the real story


@needs_ffmpeg
def test_a_35_minute_44_mb_mp3_measures_whole(long_mp3):
    assert len(long_mp3) > 40_000_000
    result = analyze_audio_bytes(long_mp3)
    assert abs(result["duration"] - 2104) < 1.0
    assert result["cue_out_seconds"] > 2100


@needs_ffmpeg
def test_stray_bytes_after_the_tags_still_measure():
    # FFmpeg reading a pipe gives up on this ("Invalid data found when processing
    # input"); from a file, as ffprobe reads it, it plays.
    audio = _mp3(884, 128)
    data = _id3() + bytes(range(256)) * 12000 + audio  # ~3 MB of stray bytes
    piped = subprocess.run(["ffmpeg", "-i", "pipe:0", "-f", "null", "-"], input=data, capture_output=True, timeout=300)
    assert b"time=00:14" not in piped.stderr  # the old way read nothing
    assert abs(get_audio_duration(data) - 884) < 2.0
    assert abs(analyze_audio_bytes(data)["duration"] - 884) < 2.0


@needs_ffmpeg
def test_noise_is_still_not_audio():
    with pytest.raises(ValueError, match="Could not measure"):
        analyze_audio_bytes(bytes(range(256)) * 4000)


async def _taken_off(db, title, *, by_check=True):
    a = Asset(id=uuid.uuid4(), title=title, file_path=f"{title}.mp3", asset_type="music",
              category="do_not_play", duration=600,
              metadata_extra={"category_before_do_not_play": "stories",
                              **({"unplayable": {"reason": "its length cannot be measured", "at": "x"}} if by_check else {})})
    db.add(a)
    db.add(Alert(alert_type="asset_missing", severity="warning", title=f"Taken off the air: {title}",
                 message="m", context={"asset_id": str(a.id)}))
    await db.commit()
    return a


@pytest.mark.asyncio
async def test_items_wrongly_taken_off_come_back(db_session, monkeypatch):
    good = await _taken_off(db_session, "chanuka radio pt02")
    broken = await _taken_off(db_session, "Really broken")
    by_hand = await _taken_off(db_session, "Marked by a person", by_check=False)

    async def fake(db, asset_id, full=False):
        if asset_id == str(broken.id):
            raise ValueError("Could not measure the length of the audio")
        return {"duration": 2104.08}

    monkeypatch.setattr(audio_analysis_service, "analyze_audio", fake)
    monkeypatch.setattr("app.db.session.async_session_factory", TestSessionLocal)
    rows = []
    monkeypatch.setattr(activity_log, "enqueue", rows.append)

    assert await upcoming_analysis.restore_measurable_items() == 1
    async with TestSessionLocal() as db:
        g = await db.get(Asset, good.id)
        assert g.category == "stories"
        assert "unplayable" not in g.metadata_extra
        assert g.metadata_extra["restored_after_unplayable"]["length"] == 2104.08
        assert (await db.get(Asset, broken.id)).category == "do_not_play"
        assert (await db.get(Asset, by_hand.id)).category == "do_not_play"  # a person's choice stays
        alerts = {a.title: a.is_resolved for a in (await db.execute(select(Alert))).scalars().all()}
        assert alerts["Taken off the air: chanuka radio pt02"] is True
        assert alerts["Taken off the air: Really broken"] is False
    assert len(rows) == 1 and rows[0]["resource_id"] == str(good.id)
    assert rows[0]["label"] == "Put back on the air (library item)"
    assert "stories" in rows[0]["detail"]
