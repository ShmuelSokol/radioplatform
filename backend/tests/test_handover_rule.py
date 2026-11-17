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
    # audible end 198.5 s (the analysed point + 3), default crossfade 1 s
    e = _entry(200.0, {"cross_start_seconds": 195.5})
    assert handover_seconds(e) == 2.5
    assert handover_seconds(_entry(200.0)) == 1.0
    now = datetime.now(UTC)
    e.started_at = now
    assert switch_time(e) == now + timedelta(seconds=197.5)


# ── Only songs overlap the next item (Shmuel, 2026-11-17: sponsorships played back to
# back faded into each other) and the song crossfade length is a station setting ──


def _typed(duration, asset_type, analysis=None, station_id=None, **kw):
    e = _entry(duration, analysis, **kw)
    e.asset.asset_type = asset_type
    if station_id is not None:
        e.station_id = station_id
    return e


def test_a_sponsorship_played_by_hand_is_never_crossfaded():
    from app.api.v1.queue import overlap_seconds

    # 30 s spot, queued by an operator (not timed), with an analysis that has a crossfade point
    for kind in ("spot", "sponsor", "shiur", "story", "jingle", "weather", "announcement"):
        e = _typed(30.0, kind, {"cross_start_seconds": 26.0}, source="manual")
        assert handover_seconds(e) == SHORT_ITEM_HANDOVER_SECONDS, kind
        assert overlap_seconds(e) == 0.0, kind
    # a 40 minute shiur ends at its end too
    assert handover_seconds(_typed(2400.0, "shiur")) == SHORT_ITEM_HANDOVER_SECONDS


def test_songs_crossfade_one_second_by_default():
    from app.api.v1.queue import overlap_seconds

    e = _typed(200.0, "music")
    assert handover_seconds(e) == 1.0 and overlap_seconds(e) == 1.0
    e = _typed(200.0, "music", {"cross_start_seconds": 190.0})  # 7 s of silent tail
    assert handover_seconds(e) == 8.0 and overlap_seconds(e) == 1.0


def test_station_crossfade_setting_moves_the_hand_over():
    from types import SimpleNamespace

    from app.api.v1.queue import crossfade_setting, note_station_crossfade, overlap_seconds

    sid = uuid.uuid4()
    note_station_crossfade(SimpleNamespace(id=sid, automation_config={"crossfade_seconds": 0}))
    plain = _typed(200.0, "music", station_id=sid)
    tail = _typed(200.0, "music", {"cross_start_seconds": 190.0}, station_id=sid)
    # no crossfade: the song plays to its audible end (193 s), then the next one starts
    assert overlap_seconds(plain) == 0.0 and handover_seconds(plain) == SHORT_ITEM_HANDOVER_SECONDS
    assert handover_seconds(tail) == 7.0 + SHORT_ITEM_HANDOVER_SECONDS

    note_station_crossfade(SimpleNamespace(id=sid, automation_config={"crossfade_seconds": 6}))
    assert overlap_seconds(plain) == 6.0 and handover_seconds(plain) == 6.0
    assert handover_seconds(tail) == 13.0
    # another station is untouched; a spot on this one still never overlaps
    assert handover_seconds(_typed(200.0, "music")) == 1.0
    assert handover_seconds(_typed(30.0, "spot", station_id=sid)) == SHORT_ITEM_HANDOVER_SECONDS

    assert crossfade_setting(None) == 1.0 and crossfade_setting({"crossfade_seconds": "x"}) == 1.0
    assert crossfade_setting({"crossfade_seconds": 99}) == 10.0
    assert crossfade_setting({"crossfade_seconds": -1}) == 0.0
    assert crossfade_setting({"crossfade_seconds": True}) == 1.0
