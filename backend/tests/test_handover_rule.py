"""One hand-over rule for the server and the pages.

All Day Stories, 10 PM ET 2026-11-16: the paused story came back 1.8 s into a 5 s time
check. The scheduler's timer and the now-playing message used "3 s before the end, at
most half" while the queue used "short and timed items play to their end".
"""
import uuid
from datetime import UTC, datetime, timedelta

from app.api.v1.queue import SHORT_ITEM_HANDOVER_SECONDS, handover_seconds, switch_time
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry


def _entry(duration, analysis=None, **kw):
    a = Asset(id=uuid.uuid4(), title="x", duration=duration, file_path="x.mp3", asset_type="music",
              metadata_extra={"audio_analysis": {**analysis, "version": 99}} if analysis else None)
    e = QueueEntry(id=uuid.uuid4(), station_id=uuid.uuid4(), asset_id=a.id, position=1, status="playing", **kw)
    e.asset = a
    return e


def test_time_check_plays_to_its_end_even_with_an_analysis():
    # the analysis puts cross_start at cue_in + 5 (= 5.0) on a 5.6 s clip
    e = _entry(5.6, {"cue_in_seconds": 0.0, "cue_out_seconds": 5.6, "cross_start_seconds": 5.0},
               preempt_at=datetime.now(UTC), timing_mode="hard")
    assert handover_seconds(e) == SHORT_ITEM_HANDOVER_SECONDS


def test_short_item_without_analysis_is_not_cut_at_half():
    assert handover_seconds(_entry(5.0)) == SHORT_ITEM_HANDOVER_SECONDS


def test_long_timed_item_plays_to_its_end():
    assert handover_seconds(_entry(30.0, source="block")) == SHORT_ITEM_HANDOVER_SECONDS


def test_song_uses_its_crossfade_point():
    e = _entry(200.0, {"cross_start_seconds": 195.5})
    assert handover_seconds(e) == 4.5
    assert handover_seconds(_entry(200.0)) == 3.0
    now = datetime.now(UTC)
    e.started_at = now
    assert switch_time(e) == now + timedelta(seconds=195.5)
