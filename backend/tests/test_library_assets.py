"""Library (/assets) endpoints: diagnostics, backfills, analysis, bulk jobs, request
settings, audio URL, enhance, delete of items in use, upload refusals and error answers
(checklist B-ASSETS-*, V1-91..V1-93, V2-12, V2-13, V2-15, V2-17, V2-21, V2-100, V2-126).
Uses the real ffmpeg."""
import asyncio
import os
import shutil
import subprocess
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from app.api.v1 import assets as assets_api
from app.config import settings
from app.models.asset import Asset
from app.services import storage_service
from tests.conftest import TestSessionLocal
from tests.test_audio_processing import _audio, _probe, _stored_asset

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture
def local_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(storage_service, "LOCAL_STORAGE_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def jobs_db(monkeypatch):
    """Background jobs (bulk analyse, bulk trim, album art, upload analysis) open their
    own sessions: point them at the test database."""
    import app.db.engine as db_engine
    import app.db.session as db_session_mod

    monkeypatch.setattr(db_session_mod, "async_session_factory", TestSessionLocal)
    monkeypatch.setattr(db_engine, "async_session_factory", TestSessionLocal)


async def _finish_jobs():
    while assets_api._detached_tasks:
        await asyncio.gather(*list(assets_api._detached_tasks), return_exceptions=True)


def _stored_files(root) -> list[str]:
    return [os.path.join(d, f) for d, _, files in os.walk(root) for f in files]


async def _fresh(db, asset_id) -> Asset:
    return (await db.execute(
        select(Asset).where(Asset.id == asset_id).execution_options(populate_existing=True)
    )).scalar_one()


def _middle_silence() -> bytes:
    cmd = ["ffmpeg", "-v", "error",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1.5",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2",
           "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[o]", "-map", "[o]", "-f", "mp3", "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


# --- B-ASSETS-1, V2-21 ---

@pytest.mark.asyncio
async def test_ffmpeg_check_round_trip_passes_and_cleans_up(client, auth_headers, local_storage, monkeypatch):
    r = await client.get("/api/v1/assets/ffmpeg-check", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["PASS"] is True
    assert body["wav_tones_differ"] and body["mpg_tones_differ"]
    assert body["roundtrip_440_ok"] and body["roundtrip_880_ok"]
    for step in body["roundtrip"].values():
        assert step["mpg_to_mp2_ext"] == ".mp2" and step["mp2_size"] > 0 and "error" not in step
    assert _stored_files(local_storage) == []  # the test files were deleted from storage

    # A failed download: no PASS, and the uploaded test file is still removed
    async def broken_download(key):
        raise OSError("storage down")

    monkeypatch.setattr(storage_service, "download_file", broken_download)
    body = (await client.get("/api/v1/assets/ffmpeg-check", headers=auth_headers)).json()
    assert body["PASS"] is False and "storage down" in body["roundtrip"]["440Hz"]["error"]
    assert _stored_files(local_storage) == []

    # No ffmpeg: says so and stops early
    monkeypatch.setattr(settings, "FFMPEG_PATH", "/nonexistent/ffmpeg")
    body = (await client.get("/api/v1/assets/ffmpeg-check", headers=auth_headers)).json()
    assert "ffmpeg_error" in body and "roundtrip" not in body and "PASS" not in body


# --- B-ASSETS-2 ---

@pytest.mark.asyncio
async def test_refresh_requested_category(client, auth_headers, db_session):
    from app.models.song_request import SongRequest
    from app.models.station import Station

    st = Station(id=uuid.uuid4(), name="Req FM", timezone="UTC")
    popular = Asset(id=uuid.uuid4(), title="Popular", file_path="p.mp3", asset_type="music", category="lively")
    quiet = Asset(id=uuid.uuid4(), title="Quiet", file_path="q.mp3", asset_type="music", category="relax")
    db_session.add_all([st, popular, quiet])
    await db_session.flush()
    reqs = [SongRequest(id=uuid.uuid4(), station_id=st.id, requester_name="A", song_title="Popular", asset_id=popular.id)
            for _ in range(3)]
    reqs.append(SongRequest(id=uuid.uuid4(), station_id=st.id, requester_name="B", song_title="Quiet", asset_id=quiet.id))
    db_session.add_all(reqs)
    await db_session.commit()

    r = await client.post("/api/v1/assets/refresh-requested-category", headers=auth_headers)
    assert r.status_code == 200 and r.json() == {"tagged": 1}
    p = await _fresh(db_session, popular.id)
    # V2-114: the song keeps its own category and is tagged as requested
    assert p.category == "lively" and p.metadata_extra["requested"] == "yes"
    q = await _fresh(db_session, quiet.id)
    assert q.category == "relax" and "requested" not in (q.metadata_extra or {})

    for q in reqs[:3]:
        await db_session.delete(q)
    await db_session.commit()
    r = await client.post("/api/v1/assets/refresh-requested-category", headers=auth_headers)
    assert r.json() == {"tagged": 0}
    p = await _fresh(db_session, popular.id)
    assert p.category == "lively" and "requested" not in p.metadata_extra  # tag removed


# --- B-ASSETS-3 ---

@pytest.mark.asyncio
async def test_backfill_durations_endpoint(client, auth_headers, db_session, local_storage):
    good = await _stored_asset(db_session, _audio("mp3", 3), duration=None)
    gone = Asset(id=uuid.uuid4(), title="Gone", file_path="assets/missing.mp3", asset_type="music", duration=None)
    db_session.add(gone)
    await db_session.commit()

    r = await client.post("/api/v1/assets/backfill-durations", headers=auth_headers)
    assert r.status_code == 200
    out = r.json()
    assert out["fixed"] == 1 and out["failed"] == [str(gone.id)] and out["still_missing"] == 1
    assert abs((await _fresh(db_session, good.id)).duration - 3) < 0.2

    r = await client.post("/api/v1/assets/backfill-durations", params={"limit": 0}, headers=auth_headers)
    assert r.json()["failed"] == [str(gone.id)]  # limit is clamped to at least 1


# --- B-ASSETS-5, V2-12 ---

@pytest.mark.asyncio
async def test_release_date_backfill_keeps_the_work_done_when_cut_off(client, auth_headers, db_session, monkeypatch):
    from app.services import musicbrainz_service

    rows = {}
    for title, kw in (("A", {}), ("B", {}), ("C", {}), ("D", {}),
                      ("No artist", {"artist": None}), ("Shiur", {"asset_type": "shiur"}),
                      ("Dated", {"release_date": date(2001, 1, 1)})):
        a = Asset(id=uuid.uuid4(), title=title, file_path=f"{title}.mp3", artist=kw.get("artist", "Singer"),
                  asset_type=kw.get("asset_type", "music"), release_date=kw.get("release_date"))
        db_session.add(a)
        await db_session.commit()  # created in order: the walk goes oldest first
        rows[title] = a
    calls = []

    async def lookup(title, artist=None):
        calls.append(title)
        if len(calls) == 3:
            raise asyncio.CancelledError()  # the request was cut off (timeout / proxy)
        return date(1990, 1, 1)

    monkeypatch.setattr(musicbrainz_service, "lookup_release_date", lookup)
    with pytest.raises(asyncio.CancelledError):
        await assets_api.backfill_release_dates(limit=0, db=db_session, _user=None)
    await db_session.rollback()
    assert set(calls) <= {"A", "B", "C", "D"} and len(calls) == 3  # only music with an artist and no date
    for title in calls[:2]:  # the two found before the cut are kept
        assert (await _fresh(db_session, rows[title].id)).release_date == date(1990, 1, 1)
    left = sorted({"A", "B", "C", "D"} - set(calls[:2]))

    # Run again: carries on with what is left; a miss and an error are counted, not fatal
    async def lookup2(title, artist=None):
        assert title in left
        if title == left[0]:
            raise RuntimeError("musicbrainz down")
        return None

    monkeypatch.setattr(musicbrainz_service, "lookup_release_date", lookup2)
    r = await client.post("/api/v1/assets/backfill-release-dates", headers=auth_headers)
    assert r.status_code == 200 and r.json() == {"updated": 0, "total": 2}

    calls.clear()
    monkeypatch.setattr(musicbrainz_service, "lookup_release_date", lookup)
    r = await client.post("/api/v1/assets/backfill-release-dates", params={"limit": 1}, headers=auth_headers)
    assert r.json() == {"updated": 1, "total": 1}  # limit: one item per run


# --- B-ASSETS-8 ---

@pytest.mark.asyncio
async def test_analyze_audio_endpoint(client, auth_headers, db_session, local_storage):
    a = await _stored_asset(db_session, _audio("wav", 4, lead_silence=1.0), ext="wav", duration=None)
    r = await client.post(f"/api/v1/assets/{a.id}/analyze-audio", headers=auth_headers)
    assert r.status_code == 200, r.text
    an = r.json()["analysis"]
    assert abs(an["cue_in_seconds"] - 1.0) < 0.1 and abs(an["duration"] - 5) < 0.1
    fresh = await _fresh(db_session, a.id)
    assert fresh.metadata_extra["audio_analysis"]["cue_in_seconds"] == an["cue_in_seconds"]
    assert abs(fresh.duration - 5) < 0.1  # the missing length is filled in

    assert (await client.post(f"/api/v1/assets/{uuid.uuid4()}/analyze-audio", headers=auth_headers)).status_code == 404
    junk = await _stored_asset(db_session, b"not audio" * 50)
    r = await client.post(f"/api/v1/assets/{junk.id}/analyze-audio", headers=auth_headers)
    assert r.status_code == 422 and "could not be analysed" in r.json()["detail"]
    gone = Asset(id=uuid.uuid4(), title="Gone", file_path="assets/missing.mp3", asset_type="music")
    db_session.add(gone)
    await db_session.commit()
    assert (await client.post(f"/api/v1/assets/{gone.id}/analyze-audio", headers=auth_headers)).status_code == 502


# --- B-ASSETS-10 ---

@pytest.mark.asyncio
async def test_bulk_analyze_job_status(client, auth_headers, db_session, monkeypatch, jobs_db):
    from app.services import audio_analysis_service

    ok = Asset(id=uuid.uuid4(), title="Ok", file_path="ok.mp3", asset_type="music")
    bad = Asset(id=uuid.uuid4(), title="Bad", file_path="bad.mp3", asset_type="music")
    talk = Asset(id=uuid.uuid4(), title="Talk", file_path="talk.mp3", asset_type="shiur")
    db_session.add_all([ok, bad, talk])
    await db_session.commit()
    seen = []

    async def fake_analyze(db, asset_id, full=False):
        seen.append(asset_id)
        if asset_id == str(bad.id):
            raise ValueError("broken file")
        return {}

    monkeypatch.setattr(audio_analysis_service, "analyze_audio", fake_analyze)
    r = await client.post("/api/v1/assets/bulk-analyze", headers=auth_headers)
    assert r.status_code == 200 and r.json()["total"] == 2  # music only by default
    job_id = r.json()["job_id"]
    await _finish_jobs()
    st = (await client.get(f"/api/v1/assets/bulk-analyze/status/{job_id}", headers=auth_headers)).json()
    assert st == {"job_id": job_id, "status": "completed", "total": 2, "processed": 2, "analyzed": 1, "errors": 1}
    assert str(talk.id) not in seen

    st = (await client.get(f"/api/v1/assets/bulk-analyze/status/{uuid.uuid4()}", headers=auth_headers)).json()
    assert st["status"] == "failed" and "restarted" in st["detail"]


# --- B-ASSETS-11, B-ASSETS-12, V2-15, B-ASSETS-30 ---

@pytest.mark.asyncio
async def test_bulk_auto_trim_cuts_only_the_ends(client, auth_headers, db_session, local_storage, jobs_db):
    lead = await _stored_asset(db_session, _audio("mp3", 3, lead_silence=1.0), title="Lead", duration=4.0)
    tail = await _stored_asset(db_session, _audio("mp3", 3, tail_silence=1.5), title="Tail", duration=4.5)
    both = await _stored_asset(db_session, _audio("mp3", 3, lead_silence=1.0, tail_silence=1.0), title="Both", duration=5.0)
    clean = await _stored_asset(db_session, _audio("mp3", 3), title="Clean", duration=3.0)
    middle = await _stored_asset(db_session, _middle_silence(), title="Middle", duration=5.5)
    gone = Asset(id=uuid.uuid4(), title="Gone", file_path="assets/missing.mp3", asset_type="music")
    db_session.add(gone)
    await db_session.commit()
    ids = [str(x.id) for x in (lead, tail, both, clean, middle, gone)]
    before = {x.id: x.file_path for x in (lead, tail, both, clean, middle)}

    r = await client.post("/api/v1/assets/bulk-auto-trim", json={"asset_ids": ids}, headers=auth_headers)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    await _finish_jobs()
    st = (await client.get(f"/api/v1/assets/bulk-auto-trim/status/{job_id}", headers=auth_headers)).json()
    assert st == {"job_id": job_id, "status": "completed", "total": 6, "processed": 6, "trimmed": 3,
                  "skipped": 2, "errors": 1, "gaps_shortened": 0, "detail": None}

    for x, length in ((lead, 3.0), (tail, 3.0), (both, 3.0)):
        f = await _fresh(db_session, x.id)
        assert abs(f.duration - length) < 0.3, (x.title, f.duration)
        assert f.file_path != before[x.id]
        assert f.metadata_extra["original_file_path"] == before[x.id]
        assert f.metadata_extra["trim_history"][-1]["auto"] is True
    for x in (clean, middle):  # nothing at the ends: untouched (the middle pause stays)
        f = await _fresh(db_session, x.id)
        assert f.file_path == before[x.id] and not f.metadata_extra

    # B-ASSETS-30: non-destructive; restore-original brings the first file back
    r = await client.post(f"/api/v1/assets/{lead.id}/restore-original", headers=auth_headers)
    assert r.status_code == 200 and r.json()["file_path"] == before[lead.id]
    assert abs(r.json()["duration"] - 4.0) < 0.2

    # Filter selection with no match, and an unknown job
    r = await client.post("/api/v1/assets/bulk-auto-trim", json={"title_search": "zzz-none"}, headers=auth_headers)
    assert r.status_code == 400
    st = (await client.get(f"/api/v1/assets/bulk-auto-trim/status/{uuid.uuid4()}", headers=auth_headers)).json()
    assert st["status"] == "failed" and st["detail"]
    r = await client.post("/api/v1/assets/bulk-auto-trim", json={"asset_ids": ["nope"]}, headers=auth_headers)
    assert r.status_code == 422


# --- B-ASSETS-13 ---

@pytest.mark.asyncio
async def test_bulk_category(client, auth_headers, db_session):
    a = Asset(id=uuid.uuid4(), title="Song A", file_path="a.mp3", asset_type="music")
    b = Asset(id=uuid.uuid4(), title="Song B", file_path="b.mp3", asset_type="music")
    s1 = Asset(id=uuid.uuid4(), title="Gemara 1", file_path="s1.mp3", asset_type="shiur", duration=3000)
    s2 = Asset(id=uuid.uuid4(), title="Gemara 2", file_path="s2.mp3", asset_type="shiur", duration=600)
    db_session.add_all([a, b, s1, s2])
    await db_session.commit()

    r = await client.patch("/api/v1/assets/bulk-category", json={"asset_ids": [str(a.id), str(b.id)], "category": "kumzitz"},
                           headers=auth_headers)
    assert r.status_code == 200 and r.json() == {"updated": 2}
    assert (await _fresh(db_session, a.id)).category == "kumzitz"

    r = await client.patch("/api/v1/assets/bulk-category", json={
        "asset_type": "shiur", "title_search": "gemara", "duration_min": 1000, "category": "long"}, headers=auth_headers)
    assert r.json() == {"updated": 1}
    assert (await _fresh(db_session, s1.id)).category == "long"
    assert (await _fresh(db_session, s2.id)).category is None

    r = await client.patch("/api/v1/assets/bulk-category", json={"asset_ids": ["nope"], "category": "x"}, headers=auth_headers)
    assert r.status_code == 422


# --- B-ASSETS-16, V2-17 ---

@pytest.mark.asyncio
async def test_audio_url_converts_non_browser_formats_once(client, auth_headers, db_session, monkeypatch):
    mp3 = Asset(id=uuid.uuid4(), title="Mp3", file_path="assets/a.mp3", asset_type="music")
    web = Asset(id=uuid.uuid4(), title="Web", file_path="https://cdn.example.com/x.mp3", asset_type="music")
    db_session.add_all([mp3, web])
    await db_session.commit()

    # No Supabase: the /download proxy; a full URL as it is
    r = await client.get(f"/api/v1/assets/{mp3.id}/audio-url", headers=auth_headers)
    assert r.json() == {"url": f"/api/v1/assets/{mp3.id}/download"}
    r = await client.get(f"/api/v1/assets/{web.id}/audio-url", headers=auth_headers)
    assert r.json() == {"url": "https://cdn.example.com/x.mp3"}

    # Supabase: an .mp2 item becomes MP3 next to the original, once
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://sb.example.com")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_KEY", "key")
    store: dict[str, bytes] = {"assets/old.mp2": _audio("mp2", 3), "assets/broken.mp2": b"not audio" * 50}
    uploads = []

    async def download(key):
        return store[key]

    async def upload(data, key, content_type="audio/mpeg"):
        uploads.append(key)
        store[key] = data
        return key

    monkeypatch.setattr(storage_service, "download_file", download)
    monkeypatch.setattr(storage_service, "upload_file", upload)
    old = Asset(id=uuid.uuid4(), title="Old", file_path="assets/old.mp2", asset_type="music", duration=99)
    broken = Asset(id=uuid.uuid4(), title="Broken", file_path="assets/broken.mp2", asset_type="music")
    db_session.add_all([old, broken])
    await db_session.commit()
    base = f"https://sb.example.com/storage/v1/object/public/{settings.SUPABASE_STORAGE_BUCKET}/"

    r = await client.get(f"/api/v1/assets/{old.id}/audio-url", headers=auth_headers)
    assert r.json() == {"url": base + "assets/old.mp3"}
    f = await _fresh(db_session, old.id)
    assert f.file_path == "assets/old.mp3" and abs(f.duration - 3) < 0.3
    assert _probe(store["assets/old.mp3"])["codec"] == "mp3" and "assets/old.mp2" in store
    r = await client.get(f"/api/v1/assets/{old.id}/audio-url", headers=auth_headers)
    assert r.json() == {"url": base + "assets/old.mp3"} and uploads == ["assets/old.mp3"]  # not again

    # Conversion fails: the original's URL, item unchanged
    r = await client.get(f"/api/v1/assets/{broken.id}/audio-url", headers=auth_headers)
    assert r.status_code == 200 and r.json() == {"url": base + "assets/broken.mp2"}
    assert (await _fresh(db_session, broken.id)).file_path == "assets/broken.mp2"


# --- B-ASSETS-17 ---

@pytest.mark.asyncio
async def test_request_settings(client, auth_headers, db_session):
    a = Asset(id=uuid.uuid4(), title="Req", file_path="r.mp3", asset_type="music", metadata_extra={"bpm": 120})
    db_session.add(a)
    await db_session.commit()
    url = f"/api/v1/assets/{a.id}/request-settings"

    r = await client.patch(url, json={}, headers=auth_headers)
    assert r.json() == {"id": str(a.id), "auto_approve_requests": False, "max_requests_per_day": 3}
    r = await client.patch(url, json={"auto_approve_requests": True, "max_requests_per_day": 5}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["auto_approve_requests"] is True and r.json()["max_requests_per_day"] == 5
    f = await _fresh(db_session, a.id)
    assert f.metadata_extra == {"bpm": 120, "auto_approve_requests": True, "max_requests_per_day": 5}

    for bad in ({"max_requests_per_day": "abc"}, {"max_requests_per_day": -1}, {"max_requests_per_day": 2.5},
                {"auto_approve_requests": "yes"}):
        r = await client.patch(url, json=bad, headers=auth_headers)
        assert r.status_code == 422, bad
    assert (await _fresh(db_session, a.id)).metadata_extra["max_requests_per_day"] == 5
    assert (await client.patch(f"/api/v1/assets/{uuid.uuid4()}/request-settings", json={}, headers=auth_headers)).status_code == 404


# --- B-ASSETS-18 ---

@pytest.mark.asyncio
async def test_enhance_presets_list(client, auth_headers):
    from app.services.enhance_service import build_filter_chain

    r = await client.get("/api/v1/assets/enhance-presets", headers=auth_headers)
    assert r.status_code == 200
    presets = r.json()["presets"]
    assert {"broadcast_polish", "warm_up", "noise_reduction", "voice_boost"} <= set(presets)
    for name, filters in presets.items():
        assert filters and build_filter_chain(filters), name  # every preset is accepted by /enhance


# --- B-ASSETS-20 ---

@pytest.mark.asyncio
async def test_auto_enhance_endpoint(client, auth_headers, db_session, local_storage):
    a = await _stored_asset(db_session, _audio("mp3", 4), duration=4.0)
    first = a.file_path
    r = await client.post(f"/api/v1/assets/{a.id}/auto-enhance", headers=auth_headers)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["filters_applied"] and len(out["reasons"]) == len(out["filters_applied"])
    asset = out["asset"]
    assert asset["file_path"] != first and asset["metadata_extra"]["original_file_path"] == first
    hist = asset["metadata_extra"]["enhance_history"][-1]
    assert hist["preset"] == "ai_auto" and hist["filters"] == out["filters_applied"] and hist["from"] == first
    p = _probe(await storage_service.download_file(asset["file_path"]))
    assert p["codec"] == "mp3" and abs(p["duration"] - 4) < 0.3


# --- B-ASSETS-22 ---

@pytest.mark.asyncio
async def test_detect_audience_endpoint(client, auth_headers, db_session, local_storage):
    def sine(d, gain_db=0):
        return ["-f", "lavfi", "-i", f"sine=frequency=300:sample_rate=44100:duration={d},volume={gain_db}dB"]

    data = subprocess.run(
        ["ffmpeg", "-v", "error", *sine(3), *sine(2, -15), *sine(2), "-filter_complex",
         "[0:a][1:a][2:a]concat=n=3:v=0:a=1[o]", "-map", "[o]", "-f", "wav", "pipe:1"],
        capture_output=True, check=True,
    ).stdout
    a = await _stored_asset(db_session, data, ext="wav", asset_type="shiur")
    r = await client.post(f"/api/v1/assets/{a.id}/detect-audience", headers=auth_headers)
    assert r.status_code == 200
    out = r.json()
    assert out["count"] == 1 and abs(out["audience_segments"][0]["start"] - 3) < 0.1

    gone = Asset(id=uuid.uuid4(), title="Gone", file_path="assets/missing.wav", asset_type="shiur")
    db_session.add(gone)
    await db_session.commit()
    assert (await client.post(f"/api/v1/assets/{gone.id}/detect-audience", headers=auth_headers)).status_code == 502


# --- B-ASSETS-26, B-ASSETS-27, V2-13 ---

@pytest.mark.asyncio
async def test_transcode_and_clip_without_a_worker(client, auth_headers, db_session, monkeypatch):
    a = Asset(id=uuid.uuid4(), title="T", file_path="t.mp3", asset_type="music")
    db_session.add(a)
    await db_session.commit()

    monkeypatch.setattr(assets_api.task_transcode_audio, "delay", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(assets_api.task_clip_audio, "delay", lambda *a, **k: None, raising=False)
    r = await client.post(f"/api/v1/assets/{a.id}/transcode", json={"codec": "aac"}, headers=auth_headers)
    assert r.status_code == 503 and "not available" in r.json()["detail"]
    r = await client.post(f"/api/v1/assets/{a.id}/clip", json={"start": 0, "duration": 5}, headers=auth_headers)
    assert r.status_code == 503 and "not available" in r.json()["detail"]

    class Queued:
        id = "task-1"

    monkeypatch.setattr(assets_api.task_transcode_audio, "delay", lambda *a, **k: Queued(), raising=False)
    monkeypatch.setattr(assets_api.task_clip_audio, "delay", lambda *a, **k: Queued(), raising=False)
    r = await client.post(f"/api/v1/assets/{a.id}/transcode", json={}, headers=auth_headers)
    assert r.json() == {"task_id": "task-1", "status": "queued"}
    r = await client.post(f"/api/v1/assets/{a.id}/clip", json={"start": 1, "duration": 2}, headers=auth_headers)
    assert r.json() == {"task_id": "task-1", "status": "queued"}
    r = await client.post(f"/api/v1/assets/{uuid.uuid4()}/clip", json={"start": 1, "duration": 2}, headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_upload_saves_the_embedded_album_art(client, auth_headers, db_session, local_storage, jobs_db, tmp_path_factory):
    """V2-13: the album art was only extracted by a Celery task (never ran without Redis,
    and it never saved the picture on the item even with Redis)."""
    tmp_path = tmp_path_factory.mktemp("art")
    jpg = tmp_path / "cover.jpg"
    out = tmp_path / "cover.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-frames:v", "1",
                    str(jpg)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-i", str(jpg),
                    "-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame", "-c:v", "copy", "-id3v2_version", "3",
                    "-disposition:v", "attached_pic", str(out)], check=True)
    with_art = out.read_bytes()
    r = await client.post("/api/v1/assets/upload", files={"file": ("cover.mp3", with_art, "audio/mpeg")},
                          data={"title": "With cover", "asset_type": "spot"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    await _finish_jobs()
    f = await _fresh(db_session, uuid.UUID(r.json()["id"]))
    assert f.album_art_path == f"artwork/{f.id}.jpg"
    art = await storage_service.download_file(f.album_art_path)
    assert art[:2] == b"\xff\xd8"  # a JPEG

    r = await client.post("/api/v1/assets/upload", files={"file": ("plain.mp3", _audio("mp3", 2), "audio/mpeg")},
                          data={"title": "No cover", "asset_type": "spot"}, headers=auth_headers)
    await _finish_jobs()
    assert (await _fresh(db_session, uuid.UUID(r.json()["id"]))).album_art_path is None


# --- B-ASSETS-28 ---

@pytest.mark.asyncio
async def test_detect_silence_endpoint(client, auth_headers, db_session, local_storage):
    a = await _stored_asset(db_session, _audio("mp3", 3, lead_silence=1.0), duration=4.0)
    r = await client.post(f"/api/v1/assets/{a.id}/detect-silence", params={"threshold_db": -40}, headers=auth_headers)
    assert r.status_code == 200
    regions = r.json()["silence_regions"]
    assert len(regions) == 1 and regions[0]["start"] < 0.05 and abs(regions[0]["end"] - 1.0) < 0.1
    assert (await _fresh(db_session, a.id)).metadata_extra["silence_regions"] == regions
    r = await client.post(f"/api/v1/assets/{a.id}/trim", params={"trim_start": 1, "trim_end": 4}, headers=auth_headers)
    assert "silence_regions" not in r.json()["metadata_extra"]  # stale after a trim


# --- V1-91 ---

@pytest.mark.asyncio
async def test_deleting_an_item_used_by_a_spot_schedule_or_program_block_is_refused(client, auth_headers, db_session):
    from app.models.program_block import ProgramBlock
    from app.models.queue_entry import QueueEntry
    from app.models.sponsor import Sponsor
    from app.models.spot_schedule import SpotSchedule
    from app.models.station import Station

    st = Station(id=uuid.uuid4(), name="Del FM", timezone="UTC")
    sp = Sponsor(id=uuid.uuid4(), name="Acme", length_seconds=30, audio_file_path="x")
    spot = Asset(id=uuid.uuid4(), title="Acme spot", file_path="s.mp3", asset_type="spot")
    db_session.add_all([st, sp, spot])
    await db_session.flush()
    sched = SpotSchedule(id=uuid.uuid4(), sponsor_id=sp.id, asset_id=spot.id, name="Acme mornings")
    block = ProgramBlock(id=uuid.uuid4(), station_id=st.id, name="Weather Sponsors", times="08:00",
                         items=[{"kind": "time"}, {"kind": "asset", "asset_id": str(spot.id)}])
    queued = QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=spot.id, position=1, status="pending", source="auto")
    db_session.add_all([sched, block, queued])
    await db_session.commit()

    r = await client.delete(f"/api/v1/assets/{spot.id}", headers=auth_headers)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "Acme mornings" in detail and "Weather Sponsors" in detail
    assert (await db_session.get(SpotSchedule, sched.id)) is not None

    await db_session.delete(sched)
    await db_session.commit()
    r = await client.delete(f"/api/v1/assets/{spot.id}", headers=auth_headers)
    assert r.status_code == 409 and "Weather Sponsors" in r.json()["detail"] and "Acme" not in r.json()["detail"]

    block.items = [{"kind": "time"}]
    await db_session.commit()
    r = await client.delete(f"/api/v1/assets/{spot.id}", headers=auth_headers)
    assert r.status_code == 204  # only queued (not playing): it goes, and its queue line with it
    left = (await db_session.execute(select(func.count()).select_from(QueueEntry).where(QueueEntry.id == queued.id))).scalar()
    assert left == 0


# --- V1-92 ---

@pytest.mark.asyncio
async def test_upload_of_a_non_audio_file_is_refused_and_nothing_is_stored(client, auth_headers, db_session,
                                                                             local_storage, monkeypatch):
    png = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=16x16:d=1", "-frames:v", "1",
                          "-f", "image2", "-c:v", "png", "pipe:1"], capture_output=True, check=True).stdout
    pdf = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n" * 20
    for name, data, ctype, fmt in (("flyer.pdf", pdf, "application/pdf", "mp3"),
                                   ("photo.png", png, "image/png", "mp3"),
                                   ("flyer.pdf", pdf, "application/pdf", "original"),
                                   ("fake.mp3", pdf, "audio/mpeg", "mp3")):
        r = await client.post("/api/v1/assets/upload", files={"file": (name, data, ctype)},
                              data={"title": "Not audio", "format": fmt}, headers=auth_headers)
        assert r.status_code == 415, (name, fmt, r.text)
        assert "could not be read as audio" in r.json()["detail"]

    r = await client.post("/api/v1/assets/upload", files={"file": ("empty.mp3", b"", "audio/mpeg")},
                          data={"title": "Empty"}, headers=auth_headers)
    assert r.status_code == 422

    monkeypatch.setattr(settings, "MAX_UPLOAD_MB", 1)
    big = b"\0" * (1024 * 1024 + 10)
    r = await client.post("/api/v1/assets/upload", files={"file": ("long shiur.mp3", big, "audio/mpeg")},
                          data={"title": "Too big"}, headers=auth_headers)
    assert r.status_code == 413 and "larger than 1 MB" in r.json()["detail"]

    assert (await db_session.execute(select(func.count()).select_from(Asset))).scalar() == 0
    assert _stored_files(local_storage) == []


# --- V1-93 ---

@pytest.mark.asyncio
async def test_asset_error_answers(client, auth_headers, db_session, local_storage):
    a = await _stored_asset(db_session, _audio("mp3", 2))
    gone = Asset(id=uuid.uuid4(), title="Gone", file_path="assets/missing.mp3", asset_type="music")
    web_gone = Asset(id=uuid.uuid4(), title="Web gone", file_path="http://127.0.0.1:9/x.mp3", asset_type="music")
    db_session.add_all([gone, web_gone])
    await db_session.commit()

    r = await client.get(f"/api/v1/assets/{a.id}/download", params={"format": "exe"}, headers=auth_headers)
    assert r.status_code == 400 and "Unsupported format" in r.json()["detail"]
    r = await client.get(f"/api/v1/assets/{gone.id}/download", params={"format": "exe"}, headers=auth_headers)
    assert r.status_code == 400  # checked before any fetch
    for item in (gone, web_gone):
        r = await client.get(f"/api/v1/assets/{item.id}/download", headers=auth_headers)
        assert r.status_code == 502 and "storage" in r.json()["detail"]
    junk = await _stored_asset(db_session, b"not audio" * 50)
    r = await client.get(f"/api/v1/assets/{junk.id}/download", params={"format": "wav"}, headers=auth_headers)
    assert r.status_code == 422

    r = await client.post(f"/api/v1/assets/{a.id}/restore-original", headers=auth_headers)
    assert r.status_code == 400 and "No original" in r.json()["detail"]

    for path in ("enhance", "enhance-preview"):
        r = await client.post(f"/api/v1/assets/{gone.id}/{path}", json={"preset": "nope"}, headers=auth_headers)
        assert r.status_code == 400 and "Unknown preset" in r.json()["detail"]  # before any fetch
        r = await client.post(f"/api/v1/assets/{a.id}/{path}", json={"filters": []}, headers=auth_headers)
        assert r.status_code == 400 and "No filters" in r.json()["detail"]
        r = await client.post(f"/api/v1/assets/{gone.id}/{path}", json={"preset": "warm_up"}, headers=auth_headers)
        assert r.status_code == 502
    for path in ("detect-silence", "trim?trim_start=0&trim_end=1", "auto-enhance"):
        r = await client.post(f"/api/v1/assets/{gone.id}/{path}", headers=auth_headers)
        assert r.status_code == 502, path

    r = await client.post("/api/v1/assets/bulk-auto-trim", json={"asset_type": "no-such-type"}, headers=auth_headers)
    assert r.status_code == 400
    # A bulk job this process does not know answers "failed" with the reason, not 404 (V2-14:
    # a restart forgets running jobs and the page must stop waiting)
    for kind in ("bulk-analyze", "bulk-auto-trim"):
        r = await client.get(f"/api/v1/assets/{kind}/status/{uuid.uuid4()}", headers=auth_headers)
        assert r.status_code == 200 and r.json()["status"] == "failed"


# --- V2-126, V2-100 ---

@pytest.mark.asyncio
async def test_upload_format_choice_stores_a_playable_format(client, auth_headers, db_session, local_storage):
    wav = _audio("wav", 2)
    r = await client.post("/api/v1/assets/upload", files={"file": ("take.wav", wav, "audio/wav")},
                          data={"title": "MP2 choice", "format": "mp2", "asset_type": "spot"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["file_path"].endswith(".mp3")  # MP2 is stored as MP3 (Safari and the engine cannot play MP2)
    assert _probe(await storage_service.download_file(out["file_path"]))["codec"] == "mp3"

    # V2-100: the recording studio's WAV take is saved with format mp3: converted and loudness-normalised
    r = await client.post("/api/v1/assets/upload", files={"file": ("recording.wav", wav, "audio/wav")},
                          data={"title": "Studio take", "format": "mp3", "asset_type": "spot"}, headers=auth_headers)
    p = _probe(await storage_service.download_file(r.json()["file_path"]))
    assert p["codec"] == "mp3" and p["channels"] == 2 and abs(p["bit_rate"] - 256000) < 10000

    r = await client.post("/api/v1/assets/upload", files={"file": ("take.wav", wav, "audio/wav")},
                          data={"title": "Bad", "format": "exe"}, headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_keep_original_of_an_mp2_file_stores_a_browser_format(client, auth_headers, db_session, local_storage):
    """V2-126 (decided 2026-11-18): Keep Original keeps every format except MP2, which becomes
    MP3 because nothing here plays MP2; the upload screen says so next to the choice."""
    r = await client.post("/api/v1/assets/upload", files={"file": ("old.mp2", _audio("mp2", 2), "audio/mpeg")},
                          data={"title": "Old", "format": "original", "asset_type": "spot"}, headers=auth_headers)
    assert r.status_code == 201
    assert _probe(await storage_service.download_file(r.json()["file_path"]))["codec"] == "mp3"
    # Any other format really is kept
    r = await client.post("/api/v1/assets/upload", files={"file": ("take.flac", _audio("flac", 2), "audio/flac")},
                          data={"title": "Kept", "format": "original", "asset_type": "spot"}, headers=auth_headers)
    assert r.status_code == 201
    assert r.json()["file_path"].endswith(".flac")
    assert (await storage_service.download_file(r.json()["file_path"]))[:4] == b"fLaC"
    page = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "pages", "admin", "AssetUpload.tsx")
    if os.path.exists(page):
        assert "Keep Original (MP2 files are still converted to MP3 so they can play)" in open(page, encoding="utf-8").read()


def test_studio_saves_takes_as_mp3():
    """V2-100: RecordingStudio uploads its WAV take with format mp3 (it was 'wav': stored
    uncompressed, about 10 MB a minute, and not loudness-normalised)."""
    src = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "pages", "admin", "RecordingStudio.tsx")
    if not os.path.exists(src):
        pytest.skip("frontend not checked out")
    text = open(src, encoding="utf-8").read()
    assert "'wav'" not in text and text.count("'mp3',") >= 2


# --- B-ASSETS-11b: Shmuel 2026-11-19 "Yes also in middle ... replace the longer gaps
# with shorter gaps ... so it's not unnaturally short spaces" ---

def _long_middle_silence(gap: float = 4.0) -> bytes:
    cmd = ["ffmpeg", "-v", "error",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2",
           "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=mono:d={gap}",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2",
           "-filter_complex", "[0:a][1:a][2:a][3:a]concat=n=4:v=0:a=1[o]", "-map", "[o]", "-f", "mp3", "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


@pytest.mark.asyncio
async def test_bulk_auto_trim_shortens_long_middle_gaps(client, auth_headers, db_session, local_storage, jobs_db):
    from app.services.silence_service import detect_silence
    from app.services.storage_service import download_file

    long_gap = await _stored_asset(db_session, _long_middle_silence(4.0), title="Long gap", duration=9.0)
    short_gap = await _stored_asset(db_session, _middle_silence(), title="Short gap", duration=5.5)
    other = await _stored_asset(db_session, _long_middle_silence(4.0), title="Ends only", duration=9.0)
    before = {x.id: x.file_path for x in (long_gap, short_gap, other)}

    r = await client.post("/api/v1/assets/bulk-auto-trim",
                          json={"asset_ids": [str(long_gap.id), str(short_gap.id)]}, headers=auth_headers)
    job_id = r.json()["job_id"]
    await _finish_jobs()
    st = (await client.get(f"/api/v1/assets/bulk-auto-trim/status/{job_id}", headers=auth_headers)).json()
    assert (st["trimmed"], st["skipped"], st["errors"], st["gaps_shortened"]) == (1, 1, 0, 1)

    f = await _fresh(db_session, long_gap.id)
    # 1 s lead-in gone, 4 s gap now 0.75 s: 2 + 0.75 + 2
    assert abs(f.duration - 4.75) < 0.3, f.duration
    gaps = detect_silence(await download_file(f.file_path), threshold_db=-35, min_duration=0.3)
    assert len(gaps) == 1 and 0.5 < gaps[0]["end"] - gaps[0]["start"] < 1.0, gaps
    entry = f.metadata_extra["trim_history"][-1]
    assert entry["keep_gap"] == 0.75 and len(entry["middle_gaps"]) == 1
    assert f.metadata_extra["original_file_path"] == before[long_gap.id]
    # a 1.5 s pause is under the 2 s line: left alone
    assert (await _fresh(db_session, short_gap.id)).file_path == before[short_gap.id]

    # middle_gap_over 0: only the start and end, the long gap stays
    r = await client.post("/api/v1/assets/bulk-auto-trim",
                          json={"asset_ids": [str(other.id)], "middle_gap_over": 0}, headers=auth_headers)
    await _finish_jobs()
    f = await _fresh(db_session, other.id)
    assert abs(f.duration - 8.0) < 0.3, f.duration

    r = await client.post(f"/api/v1/assets/{long_gap.id}/restore-original", headers=auth_headers)
    assert r.json()["file_path"] == before[long_gap.id]
