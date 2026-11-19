"""F-HANDOVER-17 / F-PLAYER-14: both players start an item at its cue-in (leading silence
skipped), so the server counts the item's time from the top of the file: started_at lies
cue_in seconds before the moment it came on air. The page and the server then agree on
the position, and the hand-over is not late by the cue-in."""
import uuid
from datetime import UTC, datetime, timedelta

from app.models.asset import ANALYSIS_VERSION, Asset
from app.models.queue_entry import QueueEntry, aired_from, mark_started, resume_source, start_offset


def _asset(cue_in, duration=200.0):
    return Asset(id=uuid.uuid4(), title="Song", file_path="x.mp3", duration=duration,
                 metadata_extra={"audio_analysis": {"version": ANALYSIS_VERSION, "cue_in_seconds": cue_in}})


def _entry(asset, source="auto"):
    e = QueueEntry(id=uuid.uuid4(), station_id=uuid.uuid4(), asset_id=asset.id, position=1,
                   status="pending", source=source)
    e.asset = asset
    return e


def test_a_fresh_item_counts_from_the_top_of_the_file():
    now = datetime.now(UTC)
    e = _entry(_asset(1.5))
    mark_started(e, now)
    assert e.started_at == now - timedelta(seconds=1.5)
    assert aired_from(e) == now


def test_a_resumed_shiur_counts_from_where_it_stopped_not_its_cue_in():
    now = datetime.now(UTC)
    e = _entry(_asset(1.5, duration=3600.0), source=resume_source(600))
    mark_started(e, now)
    assert start_offset(e) == 600
    assert e.started_at == now - timedelta(seconds=600)
    assert aired_from(e) == now


def test_no_analysis_or_a_wrong_cue_in_counts_from_now():
    now = datetime.now(UTC)
    for cue in (0, None, 45.0, 199.5):
        e = _entry(_asset(cue))
        mark_started(e, now)
        assert e.started_at == now, cue
