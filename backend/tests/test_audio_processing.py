"""Audio processing: enhance, preview, history and restore, upload conversion, BPM,
MusicBrainz, trim, download, edits, bulk create and analysis numbers (checklist
V2-2..V2-6, V2-8..V2-11, V2-16, V2-18, V2-20, V2-22, V2-23). Uses the real ffmpeg."""
import json
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import date

import pytest

from app.models.asset import Asset
from app.services import storage_service
from tests.timing import limit

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _audio(fmt: str, seconds: float = 4, lead_silence: float = 0, tail_silence: float = 0, freq: int = 440) -> bytes:
    parts, labels = [], []
    if lead_silence:
        parts += ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={lead_silence}"]
    parts += ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:duration={seconds}"]
    if tail_silence:
        parts += ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={tail_silence}"]
    n = (1 if lead_silence else 0) + 1 + (1 if tail_silence else 0)
    labels = "".join(f"[{i}:a]" for i in range(n))
    cmd = ["ffmpeg", "-v", "error", *parts, "-filter_complex", f"{labels}concat=n={n}:v=0:a=1[o]", "-map", "[o]",
           "-f", fmt, "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def _probe(data: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".bin") as f:
        f.write(data)
        f.flush()
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,channels,sample_rate,bit_rate:format=duration",
             "-of", "json", f.name], capture_output=True, check=True,
        ).stdout
    j = json.loads(out)
    s = j["streams"][0]
    return {"codec": s["codec_name"], "channels": s.get("channels"), "rate": int(s.get("sample_rate", 0)),
            "bit_rate": int(s.get("bit_rate") or 0), "duration": float(j["format"]["duration"])}


@pytest.fixture
def local_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(storage_service, "LOCAL_STORAGE_DIR", str(tmp_path))
    return tmp_path


async def _stored_asset(db, data: bytes, ext="mp3", **kw) -> Asset:
    key = f"assets/{uuid.uuid4()}.{ext}"
    await storage_service.upload_file(data, key)
    a = Asset(id=uuid.uuid4(), title=kw.pop("title", "Tone"), file_path=key, asset_type=kw.pop("asset_type", "music"),
              duration=kw.pop("duration", 4.0), **kw)
    db.add(a)
    await db.commit()
    return a


# --- V2-2 ---

@pytest.mark.asyncio
async def test_enhance_values_are_range_checked(client, auth_headers, db_session, local_storage):
    a = await _stored_asset(db_session, _audio("mp3", 2))
    bad = [
        [{"name": "highpass", "params": {"frequency": 5}}],
        [{"name": "lowpass", "params": {"frequency": 30000}}],
        [{"name": "bass", "params": {"gain": 25}}],
        [{"name": "acompressor", "params": {"ratio": 50}}],
        [{"name": "loudnorm", "params": {"i": -2}}],
        [{"name": "highpass", "params": {"frequency": "80;rm -rf /"}}],
        [{"name": "reverb", "params": {}}],
    ]
    for filters in bad:
        for path in ("enhance", "enhance-preview"):
            t0 = time.perf_counter()
            r = await client.post(f"/api/v1/assets/{a.id}/{path}", json={"filters": filters}, headers=auth_headers)
            assert time.perf_counter() - t0 < limit(1.0)
            assert r.status_code == 400, (filters, path, r.text)
    r = await client.post(f"/api/v1/assets/{a.id}/enhance", json={"preset": "nope"}, headers=auth_headers)
    assert r.status_code == 400
    await db_session.refresh(a)
    assert "enhance_history" not in (a.metadata_extra or {})


# --- V2-3, V2-5, V2-6 ---

@pytest.mark.asyncio
async def test_enhance_preview_history_and_restore(client, auth_headers, db_session, local_storage):
    original = _audio("mp3", 6)
    a = await _stored_asset(db_session, original, duration=6.0)
    first_path = a.file_path

    # Preview: 2 s at 128k, nothing on the item changes
    r = await client.post(f"/api/v1/assets/{a.id}/enhance-preview",
                          json={"preset": "warm_up", "start_seconds": 1, "duration_seconds": 2}, headers=auth_headers)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    p = _probe(r.content)
    assert p["codec"] == "mp3" and abs(p["duration"] - 2) < 0.2 and abs(p["bit_rate"] - 128000) < 8000
    await db_session.refresh(a)
    assert a.file_path == first_path and not a.metadata_extra

    # Enhance with a preset: new MP3 192k file, history {from, to, preset}
    r = await client.post(f"/api/v1/assets/{a.id}/enhance", json={"preset": "broadcast_polish"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file_path"] != first_path
    enhanced = await storage_service.download_file(body["file_path"])
    p = _probe(enhanced)
    assert p["codec"] == "mp3" and abs(p["bit_rate"] - 192000) < 8000 and abs(p["duration"] - 6) < 0.3
    hist = body["metadata_extra"]["enhance_history"]
    assert hist == [{"from": first_path, "to": body["file_path"], "preset": "broadcast_polish", "filters": None}]
    assert body["metadata_extra"]["original_file_path"] == first_path

    # Custom filters are stored
    custom = [{"name": "highpass", "params": {"frequency": 120}}]
    r = await client.post(f"/api/v1/assets/{a.id}/enhance", json={"filters": custom}, headers=auth_headers)
    assert r.json()["metadata_extra"]["enhance_history"][-1]["filters"] == custom

    # Trim keeps the FIRST original
    r = await client.post(f"/api/v1/assets/{a.id}/trim", params={"trim_start": 1, "trim_end": 4}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["metadata_extra"]["original_file_path"] == first_path
    assert r.json()["duration"] == 3

    # Restore: back to the first file, length re-measured, trim history cleared
    r = await client.post(f"/api/v1/assets/{a.id}/restore-original", headers=auth_headers)
    assert r.status_code == 200
    out = r.json()
    assert out["file_path"] == first_path and abs(out["duration"] - 6) < 0.2
    assert "trim_history" not in out["metadata_extra"] and "original_file_path" not in out["metadata_extra"]
    # Known gap (V2-6): enhance history stays after a restore
    assert len(out["metadata_extra"]["enhance_history"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["noise_reduction", "clarity_boost", "de_hiss", "bbe_sonic_maximizer", "voice_boost"])
async def test_every_preset_produces_playable_mp3(preset):
    from app.services.enhance_service import ENHANCEMENT_PRESETS, enhance_audio

    data, duration = enhance_audio(_audio("mp3", 2), ENHANCEMENT_PRESETS[preset], ".mp3")
    p = _probe(data)
    assert p["codec"] == "mp3" and abs(p["duration"] - 2) < 0.3 and abs(duration - 2) < 0.3


# --- V2-4 ---

def test_auto_enhance_rules():
    from app.services.enhance_service import build_auto_filters

    noisy, reasons = build_auto_filters({"noise_floor": -30, "mean_volume": -20, "loudness_range": 20, "dynamic_range": 10})
    names = [f["name"] for f in noisy]
    assert names == ["afftdn", "highpass", "treble", "acompressor", "loudnorm"]
    assert noisy[0]["params"]["noise_floor"] == -25  # -30 + 5, inside -40..-10
    assert noisy[1]["params"]["frequency"] == 120 and noisy[2]["params"]["gain"] == 1
    assert noisy[3]["params"]["ratio"] == 3 and len(reasons) == len(noisy)

    clean, _ = build_auto_filters({"noise_floor": -70, "mean_volume": -30, "loudness_range": 5, "dynamic_range": 10})
    assert [f["name"] for f in clean] == ["highpass", "bass", "treble", "loudnorm"]
    assert clean[0]["params"]["frequency"] == 80 and clean[2]["params"]["gain"] == 3
    assert clean[-1]["params"] == {"i": -16, "tp": -1.5, "lra": 11}

    edge, _ = build_auto_filters({"noise_floor": -12, "mean_volume": -20, "loudness_range": 10, "dynamic_range": 30})
    assert edge[0]["params"]["noise_floor"] == -10  # clamped
    assert any(f["name"] == "acompressor" and f["params"]["ratio"] == 2.5 for f in edge)


# --- V2-8, V2-9 ---

def test_upload_conversion_normalises_and_keeps_real_mp3():
    from app.services.audio_convert_service import convert_audio

    wav = _audio("wav", 3)
    out, duration, ext = convert_audio(wav, "song.wav", "mp3")
    p = _probe(out)
    assert ext == ".mp3" and p["codec"] == "mp3" and p["channels"] == 2 and p["rate"] == 44100
    assert abs(p["bit_rate"] - 256000) < 10000 and abs(duration - 3) < 0.2

    mp3 = _audio("mp3", 3)
    same, _, ext = convert_audio(mp3, "song.mp3", "mp3")
    assert same == mp3 and ext == ".mp3"  # stored untouched (not normalised)

    kept, _, ext = convert_audio(wav, "song.wav", "original")
    assert kept == wav and ext == ".wav"


def test_video_upload_keeps_only_the_audio():
    from app.services.audio_convert_service import convert_audio

    mkv = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=2", "-shortest", "-c:v", "mpeg4", "-c:a", "aac", "-f", "matroska", "pipe:1"],
        capture_output=True, check=True,
    ).stdout
    out, duration, ext = convert_audio(mkv, "clip.mkv", "mp3")
    p = _probe(out)
    assert ext == ".mp3" and p["codec"] == "mp3" and abs(duration - 2) < 0.3


@pytest.mark.xfail(strict=True, reason="BUG: when ffmpeg cannot read an upload, the original bytes are stored with their own extension and the upload reports success")
def test_unreadable_upload_is_refused():
    from app.services.audio_convert_service import convert_audio

    _, _, ext = convert_audio(b"not audio at all" * 100, "broken.mkv", "mp3")
    assert ext == ".mp3"


# --- V2-10, V2-11 ---

def test_bpm_categories_and_short_audio():
    from app.services.bpm_service import _categorize_bpm, detect_bpm

    assert [_categorize_bpm(b) for b in (60, 89.9, 90, 119.9, 120, 180)] == [
        "relax", "relax", "med_fast", "med_fast", "lively", "lively"]
    assert detect_bpm(_audio("mp3", 1), "short.mp3") == (None, None)


@pytest.mark.asyncio
async def test_upload_survives_bpm_and_musicbrainz_failures(db_session, local_storage, monkeypatch):
    from app.services import asset_service, bpm_service, musicbrainz_service

    def boom(*a, **k):
        raise RuntimeError("bpm crashed")

    async def mb_down(title, artist=None):
        raise RuntimeError("musicbrainz down")

    monkeypatch.setattr(bpm_service, "detect_bpm", boom)
    monkeypatch.setattr(musicbrainz_service, "lookup_release_date", mb_down)
    a = await asset_service.create_asset(db_session, title="Song", filename="s.mp3", file_data=_audio("mp3", 3),
                                         content_type="audio/mpeg", asset_type="music", artist="Singer")
    assert a.category is None and a.release_date is None and abs(a.duration - 3) < 0.2

    async def mb_ok(title, artist=None):
        return date(1999, 5, 1)

    monkeypatch.setattr(bpm_service, "detect_bpm", lambda data, name: (128.0, "lively"))
    monkeypatch.setattr(musicbrainz_service, "lookup_release_date", mb_ok)
    b = await asset_service.create_asset(db_session, title="Song 2", filename="s2.mp3", file_data=_audio("mp3", 3),
                                         content_type="audio/mpeg", asset_type="music", artist="Singer")
    assert b.category == "lively" and b.metadata_extra == {"bpm": 128.0} and b.release_date == date(1999, 5, 1)


@pytest.mark.asyncio
async def test_musicbrainz_lookup_rules(monkeypatch):
    import httpx

    from app.services import musicbrainz_service as mb

    assert mb._parse_date("1998") == date(1998, 1, 1)
    assert mb._parse_date("1998-07") == date(1998, 7, 1)
    assert mb._parse_date("1998-07-15") == date(1998, 7, 15)
    assert mb._parse_date("") is None and mb._parse_date("abc") is None

    async def no_wait():
        return None

    monkeypatch.setattr(mb, "_rate_limit", no_wait)
    payload = {"recordings": [{"score": 70, "first-release-date": "1980"}, {"score": 95, "first-release-date": "2001-03"}]}

    async def fake_get(self, url, **kw):
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    assert await mb.lookup_release_date("Song", "Singer") == date(2001, 3, 1)

    payload = {"recordings": [{"score": 79, "first-release-date": "1980"}]}
    assert await mb.lookup_release_date("Song", "Singer") is None

    async def down(self, url, **kw):
        raise httpx.ConnectTimeout("slow")

    monkeypatch.setattr(httpx.AsyncClient, "get", down)
    assert await mb.lookup_release_date("Song", "Singer") is None


# --- V2-16 ---

@pytest.mark.xfail(strict=True, reason="BUG: trim uses stream copy into an MP3 container, so a WAV/FLAC item cannot be trimmed (500)")
@pytest.mark.asyncio
async def test_trim_a_wav_item(client, auth_headers, db_session, local_storage):
    a = await _stored_asset(db_session, _audio("wav", 4), ext="wav")
    r = await client.post(f"/api/v1/assets/{a.id}/trim", params={"trim_start": 1, "trim_end": 3}, headers=auth_headers)
    assert r.status_code == 200


# --- V2-18 ---

@pytest.mark.asyncio
async def test_download_original_and_converted(client, auth_headers, db_session, local_storage):
    data = _audio("mp3", 2)
    a = await _stored_asset(db_session, data, title='Nigun / "Special" <1>!')
    r = await client.get(f"/api/v1/assets/{a.id}/download", headers=auth_headers)
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-disposition"] == 'attachment; filename="Nigun  Special 1.mp3"'

    b = await _stored_asset(db_session, data, title="!!!")
    r = await client.get(f"/api/v1/assets/{b.id}/download", headers=auth_headers)
    assert 'filename="download.mp3"' in r.headers["content-disposition"]

    for fmt, mime, codec in (("wav", "audio/wav", "pcm_s16le"), ("flac", "audio/flac", "flac"), ("ogg", "audio/ogg", "vorbis")):
        r = await client.get(f"/api/v1/assets/{a.id}/download", params={"format": fmt}, headers=auth_headers)
        assert r.status_code == 200, (fmt, r.text)
        assert r.headers["content-type"].startswith(mime)
        assert _probe(r.content)["codec"] == codec
    r = await client.get(f"/api/v1/assets/{a.id}/download", params={"format": "exe"}, headers=auth_headers)
    assert r.status_code == 400


# --- V2-20, V2-22 ---

@pytest.mark.asyncio
async def test_asset_edit_side_effects(client, auth_headers, db_session):
    from app.models.sponsor import Sponsor

    sp = Sponsor(id=uuid.uuid4(), name="Acme", length_seconds=30, audio_file_path="x")
    db_session.add(sp)
    await db_session.commit()
    a = Asset(id=uuid.uuid4(), title="Spot", file_path="s.mp3", asset_type="spot", sponsor_id=sp.id,
              release_date=date(2020, 1, 1), vote=5)
    db_session.add(a)
    await db_session.commit()

    r = await client.patch(f"/api/v1/assets/{a.id}", json={"release_date": "", "vote": None}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["release_date"] is None and r.json()["vote"] == 5
    r = await client.patch(f"/api/v1/assets/{a.id}", json={"asset_type": "music"}, headers=auth_headers)
    assert r.json()["sponsor_id"] is None


@pytest.mark.asyncio
async def test_bulk_create(client, auth_headers, db_session):
    assert (await client.post("/api/v1/assets/bulk-create", json={"assets": []}, headers=auth_headers)).json() == {"created": 0}
    r = await client.post("/api/v1/assets/bulk-create", json={"assets": [{"title": "A", "file_path": "assets/a.mp3"}]},
                          headers=auth_headers)
    assert r.status_code == 201 and r.json()["created"] == 1
    a = await db_session.get(Asset, uuid.UUID(r.json()["ids"][0]))
    assert a.asset_type == "music" and a.review_status == "approved"
    r = await client.post("/api/v1/assets/bulk-create", json={"assets": [{"file_path": "assets/b.mp3"}]}, headers=auth_headers)
    assert r.status_code == 422


# --- V2-23 ---

def test_analysis_numbers():
    from app.services.audio_analysis_service import analyze_audio_bytes

    data = _audio("wav", 8, lead_silence=1.0, tail_silence=1.5)
    t0 = time.perf_counter()
    r = analyze_audio_bytes(data)
    assert time.perf_counter() - t0 < limit(5)
    assert abs(r["duration"] - 10.5) < 0.1
    assert abs(r["cue_in_seconds"] - 1.0) < 0.1
    assert abs(r["cue_out_seconds"] - 9.0) < 0.1
    assert r["cross_start_seconds"] == round(max(r["cue_in_seconds"] + 5, r["cue_out_seconds"] - 3), 3)
    assert r["replay_gain_db"] == round(max(-12.0, min(12.0, -18.0 - r["loudness_lufs"])), 2)

    with pytest.raises(ValueError):
        analyze_audio_bytes(b"not audio")


# --- V2-7 ---

def test_audience_detection():
    """Quiet (between -45 and -25 dB) stretches are audience; true silence is not."""
    from app.services.enhance_service import detect_audience_segments

    def sine(d, gain_db=0):
        return ["-f", "lavfi", "-i", f"sine=frequency=300:sample_rate=44100:duration={d},volume={gain_db}dB"]

    inputs = [*sine(3), *sine(2, -15), *sine(2), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=2", *sine(2)]
    data = subprocess.run(
        ["ffmpeg", "-v", "error", *inputs, "-filter_complex", "[0:a][1:a][2:a][3:a][4:a]concat=n=5:v=0:a=1[o]",
         "-map", "[o]", "-f", "wav", "pipe:1"], capture_output=True, check=True,
    ).stdout
    segs = detect_audience_segments(data)
    assert len(segs) == 1
    assert abs(segs[0]["start"] - 3) < 0.1 and abs(segs[0]["end"] - 5) < 0.1
    assert segs[0]["duration"] == round(segs[0]["end"] - segs[0]["start"], 2)


# --- V2-14 ---

@pytest.mark.xfail(strict=True, reason="BUG: bulk analyse/auto-trim job status lives in one process's memory; after a restart the poll gets 404 'Job not found'")
@pytest.mark.asyncio
async def test_bulk_job_status_survives_a_restart(client, auth_headers, monkeypatch):
    from app.api.v1 import assets as assets_api

    monkeypatch.setattr(assets_api, "_bulk_analyze_jobs", {})  # the process restarted
    r = await client.get(f"/api/v1/assets/bulk-analyze/status/{uuid.uuid4()}", headers=auth_headers)
    assert r.status_code != 404


# --- V2-19 ---

@pytest.mark.asyncio
async def test_studio_mix(client, auth_headers, db_session, local_storage):
    bed = await _stored_asset(db_session, _audio("mp3", 6, freq=200), title="Bed")
    voice = await _stored_asset(db_session, _audio("mp3", 3, freq=900), title="Voice")
    t0 = time.perf_counter()
    r = await client.post("/api/v1/studio/mix", json={
        "backtrack_asset_id": str(bed.id), "overlay_asset_id": str(voice.id), "output_title": "Promo",
    }, headers=auth_headers)
    assert time.perf_counter() - t0 < limit(10)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["asset_type"] == "spot" and out["category"] == "studio"
    assert out["metadata_extra"] == {"source": "mixer", "backtrack_asset_id": str(bed.id), "overlay_asset_id": str(voice.id)}
    p = _probe(await storage_service.download_file(out["file_path"]))
    assert p["codec"] == "mp3" and abs(p["duration"] - 6) < 0.5  # as long as the longer input

    r = await client.post("/api/v1/studio/mix", json={
        "backtrack_asset_id": str(uuid.uuid4()), "overlay_asset_id": str(voice.id), "output_title": "x",
    }, headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.xfail(strict=True, reason="BUG: a studio mix that ffmpeg cannot make answers a bare 500, not a clear error")
@pytest.mark.asyncio
async def test_studio_mix_failure_is_explained(client, auth_headers, db_session, local_storage):
    bed = await _stored_asset(db_session, b"not audio", title="Bed")
    voice = await _stored_asset(db_session, _audio("mp3", 2), title="Voice")
    r = await client.post("/api/v1/studio/mix", json={
        "backtrack_asset_id": str(bed.id), "overlay_asset_id": str(voice.id), "output_title": "x",
    }, headers=auth_headers)
    assert r.status_code in (400, 422, 502)
