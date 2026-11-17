"""do_not_play never airs from an automatic path, and uploads are checked by codec.

160 library files were MP2 audio inside .mp3 names (the engine and Safari refuse
them), and five broken files were marked do_not_play, which a rule, block or old
queue row naming them could still draw (2026-11-17).
"""
import shutil
import subprocess
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.api.v1 import queue as queue_module
from app.models.asset import Asset, not_placeholder
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import audio_convert_service as conv
from app.services.playout_engine import pick_next


def _asset(title, category):
    return Asset(id=uuid.uuid4(), title=title, asset_type="music", category=category,
                 duration=180.0, file_path=f"https://cdn.example.com/{uuid.uuid4()}.mp3")


@pytest.mark.asyncio
async def test_a_rule_naming_do_not_play_gets_nothing(db_session):
    ok = _asset("Good song", "Lively")
    banned = _asset("Broken file", "do_not_play")
    shouty = _asset("Broken too", " DO_NOT_PLAY ")
    db_session.add_all([ok, banned, shouty])
    await db_session.commit()
    ids = set((await db_session.execute(select(Asset.id).where(not_placeholder()))).scalars().all())
    assert ids == {ok.id}


async def _queue_with_banned_first(db_session, source):
    station = Station(id=uuid.uuid4(), name="Guard FM", timezone="America/New_York")
    good = _asset("Good song", "Lively")
    banned = _asset("Broken file", "do_not_play")
    db_session.add_all([station, good, banned])
    await db_session.flush()
    first = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=banned.id,
                       position=1, status="pending", source=source)
    second = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=good.id,
                        position=2, status="pending", source="auto")
    db_session.add_all([first, second])
    await db_session.commit()
    return station, first, second


@pytest.mark.asyncio
async def test_browser_player_skips_a_queued_do_not_play(db_session):
    station, first, second = await _queue_with_banned_first(db_session, "auto")
    picked = await queue_module._pick_next_pending(db_session, station.id, datetime.now(UTC))
    assert picked.id == second.id


@pytest.mark.asyncio
async def test_engine_skips_a_queued_do_not_play(db_session):
    station, first, second = await _queue_with_banned_first(db_session, "block")
    picked = await pick_next(db_session, station.id)
    assert picked.id == second.id


@pytest.mark.asyncio
async def test_an_operator_may_still_play_it(db_session):
    station, first, _second = await _queue_with_banned_first(db_session, "manual")
    picked = await pick_next(db_session, station.id)
    assert picked.id == first.id


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def _tone(codec: str, fmt: str) -> bytes:
    out = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-ac", "2", "-ar", "44100", "-c:a", codec, "-b:a", "128k", "-f", fmt, "-"],
        capture_output=True, check=True,
    )
    return out.stdout


@needs_ffmpeg
def test_mp2_named_mp3_is_converted_to_real_mp3():
    mp2 = _tone("mp2", "mp2")
    assert conv.probe_codec(mp2, ".mp3") == "mp2"
    data, duration, ext = conv.convert_audio(mp2, "old song.mp3", "mp3")
    assert ext == ".mp3"
    assert conv.probe_codec(data, ".mp3") == "mp3"
    assert duration and 1.5 < duration < 2.5


@needs_ffmpeg
def test_mp2_file_becomes_mp3():
    data, _duration, ext = conv.convert_audio(_tone("mp2", "mp2"), "old song.mp2", "mp3")
    assert ext == ".mp3" and conv.probe_codec(data, ext) == "mp3"


@needs_ffmpeg
def test_real_mp3_is_kept_as_is():
    mp3 = _tone("libmp3lame", "mp3")
    data, _duration, ext = conv.convert_audio(mp3, "song.mp3", "mp3")
    assert data == mp3 and ext == ".mp3"
