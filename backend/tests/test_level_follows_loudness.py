"""An item's level always follows today's rule, whatever was stored when it was analysed.

Live 2026-11-18: a Carlebach story measured -2.5 LUFS kept the -12.0 dB stored under
the old limit and aired about 3.5 LU louder than everything else, although the limit
had since gone to -20 dB (it needs -15.5).
"""
import pytest

from app.models.asset import ANALYSIS_VERSION, replay_gain_for, stored_analysis
from app.services import playout_engine


def _extra(**analysis):
    return {"audio_analysis": {"version": ANALYSIS_VERSION, **analysis}}


def test_a_level_stored_under_the_old_limit_is_recomputed():
    a = stored_analysis(_extra(loudness_lufs=-2.5, replay_gain_db=-12.0, cue_out_seconds=570.0))
    assert a["replay_gain_db"] == -15.5
    assert a["cue_out_seconds"] == 570.0 and a["loudness_lufs"] == -2.5


def test_the_stored_row_is_not_touched():
    extra = _extra(loudness_lufs=-2.5, replay_gain_db=-12.0)
    stored_analysis(extra)
    assert extra["audio_analysis"]["replay_gain_db"] == -12.0


def test_an_old_analysis_keeps_only_cue_and_level_and_gets_the_rule_too():
    a = stored_analysis({"audio_analysis": {"loudness_lufs": -4.0, "replay_gain_db": -12.0,
                                            "cue_in_seconds": 0.4, "cue_out_seconds": 177.0}})
    assert a == {"loudness_lufs": -4.0, "replay_gain_db": -14.0, "cue_in_seconds": 0.4}


@pytest.mark.parametrize("lufs,gain", [(-18.0, 0.0), (-30.5, 12.0), (-40.0, 12.0), (-14.2, -3.8), (3.0, -20.0)])
def test_the_rule(lufs, gain):
    assert replay_gain_for(lufs) == gain
    assert stored_analysis(_extra(loudness_lufs=lufs, replay_gain_db=99.0))["replay_gain_db"] == gain


def test_a_loudness_nobody_measured_is_left_alone():
    # range-only analysis that stored the defaults: no level is made up from -18
    a = stored_analysis(_extra(method="range", loudness_lufs=-18.0, replay_gain_db=0.0))
    assert a["replay_gain_db"] == 0.0
    # a sampled reading louder than -5 LUFS is not believed
    a = stored_analysis(_extra(method="range", loudness_method="sampled", loudness_lufs=-3.1, replay_gain_db=-12.0))
    assert a["replay_gain_db"] == -12.0
    # no loudness at all: the stored level stays
    assert stored_analysis(_extra(replay_gain_db=-4.0))["replay_gain_db"] == -4.0
    assert stored_analysis(None) == {} and stored_analysis({"audio_analysis": "x"}) == {}


def test_the_engine_is_handed_the_recomputed_level():
    from types import SimpleNamespace

    asset = SimpleNamespace(
        id="a1", title="Yisrael Betach", artist=None, album=None, asset_type="music", duration=578.0,
        file_path="https://files.example.test/a.mp3", category=None,
        metadata_extra=_extra(loudness_lufs=-2.5, replay_gain_db=-12.0))
    entry = SimpleNamespace(id="e1", asset=asset, asset_id="a1", station_id="s1", preempt_at=None,
                            source="auto", timing_mode=None, block_id=None)
    uri = playout_engine.build_uri(entry, "main")
    assert 'liq_amplify="-15.50 dB"' in uri


def test_a_silence_is_never_handed_its_analysis():
    # Production's "Silence (5 min)" (2026-11-20): the analysis of a silent file put the
    # start cue at the end, so every silence ended at once and emergency music filled in
    from types import SimpleNamespace

    extra = {"audio_analysis": {"version": 2, "duration": 300.01, "loudness_lufs": -18.0,
                                "cue_in_seconds": 300.017, "cue_out_seconds": 0.0,
                                "cross_start_seconds": 305.017, "replay_gain_db": 0.0}}
    asset = SimpleNamespace(
        id="s1", title="Silence (5 min)", artist=None, album=None, asset_type="silence", duration=300.0,
        file_path="https://files.example.test/silence_5min.mp3", category=None, metadata_extra=extra)
    for lane in ("main", "timed"):
        entry = SimpleNamespace(id="e1", asset=asset, asset_id="s1", station_id="s1", preempt_at=None,
                                source="blackout", timing_mode=None, block_id=None)
        uri = playout_engine.build_uri(entry, lane)
        assert "liq_cue_in" not in uri and "liq_cue_out" not in uri and "liq_amplify" not in uri
        assert uri.endswith(":https://files.example.test/silence_5min.mp3")


def test_a_start_cue_at_the_end_is_never_trusted():
    from app.models.asset import stored_analysis

    # production's silence: start cue past the end, end cue 0
    a = stored_analysis({"audio_analysis": {"version": 2, "duration": 300.01, "cue_in_seconds": 300.017,
                                            "cue_out_seconds": 0.0, "replay_gain_db": 0.0}})
    assert "cue_in_seconds" not in a and "cue_out_seconds" not in a
    # an old analysis keeps only cue_in: still dropped when it is past the end
    a = stored_analysis({"audio_analysis": {"version": 1, "duration": 60.0, "cue_in_seconds": 60.0}})
    assert "cue_in_seconds" not in a
    # an ordinary song keeps its cues
    a = stored_analysis({"audio_analysis": {"version": 99, "duration": 200.0, "cue_in_seconds": 0.4,
                                            "cue_out_seconds": 197.0}})
    assert a["cue_in_seconds"] == 0.4 and a["cue_out_seconds"] == 197.0


def test_the_analysis_of_a_silent_file_has_no_cues(monkeypatch):
    from app.services import audio_analysis_service as aas

    monkeypatch.setattr(aas, "measure_loudness", lambda p: (None, None))
    monkeypatch.setattr(aas, "get_audio_duration", lambda p: 300.01)
    monkeypatch.setattr(aas, "detect_silence", lambda p, **k: [{"start": 0.0, "end": 300.01}])
    a = aas._analyze_file("x.mp3")
    assert a["cue_in_seconds"] == 0.0 and a["cue_out_seconds"] == 300.01


def test_blackout_items_are_marked_for_the_engine():
    from types import SimpleNamespace

    def uri(kind, source):
        asset = SimpleNamespace(id="a", title="t", artist=None, album=None, asset_type=kind, duration=30.0,
                                file_path="https://files.example.test/a.mp3", category=None, metadata_extra={})
        entry = SimpleNamespace(id="e", asset=asset, asset_id="a", station_id="s", preempt_at=None,
                                source=source, timing_mode=None, block_id=None)
        return playout_engine.build_uri(entry, "main")

    assert 'kbr_blackout_ok="1"' in uri("silence", "blackout")
    assert 'kbr_blackout_ok="1"' in uri("music", playout_engine.SHABBOS_END_SOURCE)
    assert "kbr_blackout_ok" not in uri("music", "auto")
