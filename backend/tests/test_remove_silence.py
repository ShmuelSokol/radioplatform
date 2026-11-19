"""Remove all found silence (Library, Silence Detection): every region the scan found
comes out, the middle ones too, and the original stays for Undo / Restore Original.

Shmuel 2026-11-18: "it brings up a whole bunch of silent regions, how do I delete all of
the ones that it found?" Before, only the start and end could go, and "Trim Out" on a
gap in the middle cut off everything after it."""
import shutil
import subprocess
import uuid

import pytest

from app.models.asset import Asset
from app.services import storage_service
from app.services.silence_service import cut_spans, detect_silence, get_audio_duration, merge_regions, remove_regions

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _tone_with_gaps() -> bytes:
    """1 s silence, 3 s tone, 2 s silence, 3 s tone, 1.5 s silence, 2 s tone, 1 s silence = 13.5 s."""
    parts = [("anullsrc=r=44100:cl=mono:d=1", None), ("sine=frequency=440:sample_rate=44100:duration=3", None),
             ("anullsrc=r=44100:cl=mono:d=2", None), ("sine=frequency=660:sample_rate=44100:duration=3", None),
             ("anullsrc=r=44100:cl=mono:d=1.5", None), ("sine=frequency=550:sample_rate=44100:duration=2", None),
             ("anullsrc=r=44100:cl=mono:d=1", None)]
    args = []
    for src, _ in parts:
        args += ["-f", "lavfi", "-i", src]
    labels = "".join(f"[{i}:a]" for i in range(len(parts)))
    cmd = ["ffmpeg", "-v", "error", *args, "-filter_complex", f"{labels}concat=n={len(parts)}:v=0:a=1[o]",
           "-map", "[o]", "-b:a", "128k", "-f", "mp3", "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def test_merge_regions_sorts_joins_and_clips():
    got = merge_regions([{"start": 5, "end": 6}, {"start": -1, "end": 1}, {"start": 0.5, "end": 2}, {"start": 9, "end": 20},
                         {"start": "x"}, {"start": 3, "end": 3}], length=10)
    assert got == [(0.0, 2.0), (5.0, 6.0), (9.0, 10.0)]


def test_every_found_region_comes_out_middle_ones_too():
    data = _tone_with_gaps()
    regions = detect_silence(data, threshold_db=-30, min_duration=0.5)
    assert len(regions) == 4
    out, length = remove_regions(data, regions)
    assert abs(length - 8.0) < 0.3  # only the 3 + 3 + 2 s of tone are left
    assert detect_silence(out, threshold_db=-30, min_duration=0.5) == []


def test_nothing_or_everything_is_refused():
    data = _tone_with_gaps()
    with pytest.raises(ValueError, match="No silence"):
        remove_regions(data, [])
    with pytest.raises(ValueError, match="whole recording"):
        remove_regions(data, [{"start": 0, "end": 14}])


@pytest.fixture
def local_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(storage_service, "LOCAL_STORAGE_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_remove_all_then_restore_original(client, auth_headers, db_session, local_storage):
    data = _tone_with_gaps()
    key = f"assets/{uuid.uuid4()}.mp3"
    await storage_service.upload_file(data, key)
    a = Asset(id=uuid.uuid4(), title="Story with gaps", file_path=key, asset_type="music", duration=13.5,
              metadata_extra={"audio_analysis": {"cue_out_seconds": 13.4}})
    db_session.add(a)
    await db_session.commit()

    r = await client.post(f"/api/v1/assets/{a.id}/detect-silence", headers=auth_headers)
    regions = r.json()["silence_regions"]
    assert len(regions) == 4

    r = await client.post(f"/api/v1/assets/{a.id}/remove-silence", json={"regions": regions}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_path"] != key and abs(body["duration"] - 8.0) < 0.3
    extra = body["metadata_extra"]
    assert extra["original_file_path"] == key
    assert len(extra["trim_history"][-1]["removed"]) == 4
    assert "audio_analysis" not in extra  # old end point would cut the new, shorter audio
    new_audio = await storage_service.download_file(body["file_path"])
    assert abs(get_audio_duration(new_audio) - 8.0) < 0.3

    r = await client.post(f"/api/v1/assets/{a.id}/restore-original", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["file_path"] == key and abs(r.json()["duration"] - 13.5) < 0.3

    r = await client.post(f"/api/v1/assets/{a.id}/remove-silence", json={"regions": []}, headers=auth_headers)
    assert r.status_code == 400


# Shmuel 2026-11-19: "add a way to replace the longer gaps with shorter gaps ... so it's
# not unnaturally short spaces"

def test_cut_spans_shortens_middle_gaps_and_clears_the_ends():
    regions = [{"start": 0, "end": 1}, {"start": 4, "end": 6}, {"start": 9, "end": 9.4}, {"start": 12, "end": 13.5}]
    assert cut_spans(regions, 13.5, 0) == [(0, 1), (4, 6), (9, 9.4), (12, 13.5)]
    # each middle gap keeps 0.5 s (half each side); the 0.4 s one is already short
    assert cut_spans(regions, 13.5, 0.5) == [(0, 1), (4.25, 5.75), (12, 13.5)]


def test_remove_all_with_a_kept_gap():
    data = _tone_with_gaps()
    regions = detect_silence(data, threshold_db=-30, min_duration=0.5)
    out, length = remove_regions(data, regions, keep_gap=0.5)
    assert abs(length - 9.0) < 0.3  # 8 s of tone + two 0.5 s gaps; start and end silence gone
    gaps = detect_silence(out, threshold_db=-30, min_duration=0.3)
    assert len(gaps) == 2 and all(0.35 < g["duration"] < 0.7 for g in gaps), gaps
    with pytest.raises(ValueError, match="already"):
        remove_regions(data, [r for r in regions if 2 < r["start"] < 11], keep_gap=3)


@pytest.mark.asyncio
async def test_remove_silence_api_keeps_a_gap(client, auth_headers, db_session, local_storage):
    data = _tone_with_gaps()
    key = f"assets/{uuid.uuid4()}.mp3"
    await storage_service.upload_file(data, key)
    a = Asset(id=uuid.uuid4(), title="Shiur with pauses", file_path=key, asset_type="music", duration=13.5)
    db_session.add(a)
    await db_session.commit()
    regions = detect_silence(data, threshold_db=-30, min_duration=0.5)
    r = await client.post(f"/api/v1/assets/{a.id}/remove-silence", json={"regions": regions, "keep_gap": 0.5},
                          headers=auth_headers)
    assert r.status_code == 200, r.text
    assert abs(r.json()["duration"] - 9.0) < 0.3
    assert r.json()["metadata_extra"]["trim_history"][-1]["keep_gap"] == 0.5
    r = await client.post(f"/api/v1/assets/{a.id}/remove-silence", json={"regions": regions, "keep_gap": 20},
                          headers=auth_headers)
    assert r.status_code == 422
