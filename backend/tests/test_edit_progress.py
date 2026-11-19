"""Progress bar for long edits (AI Enhance, Remove All Found Silence).

Shmuel 2026-11-19: "And can it show a progress bar?" The page sends a progress id with
the edit and asks GET /assets/progress/{id} while it waits."""
import shutil
import subprocess
import uuid

import pytest

from app.models.asset import Asset
from app.services import job_progress, storage_service
from app.services.enhance_service import auto_enhance
from app.services.silence_service import remove_regions

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _tone(seconds: int, gap: bool = False) -> bytes:
    expr = "if(between(t,10,14),0,0.4*sin(440*2*PI*t))+0.01*(random(0)-0.5)" if gap else "0.4*sin(440*2*PI*t)"
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"aevalsrc='{expr}':s=44100:d={seconds}",
           "-b:a", "128k", "-f", "mp3", "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


def test_progress_ids_and_reports():
    assert job_progress.valid_id("abc12345-x_y")
    for bad in (None, "", "short", "has space here", "../../etc/passwd", "x" * 65):
        assert not job_progress.valid_id(bad)
    pid = f"t{uuid.uuid4().hex}"
    job_progress.report(pid, 0.4, "Enhancing")
    job_progress.report(pid, 0.2)  # never goes backwards
    assert job_progress.get(pid)["fraction"] == 0.4 and job_progress.get(pid)["stage"] == "Enhancing"
    job_progress.reporter(pid, 0.5, 0.9)(0.5, "Saving")
    assert abs(job_progress.get(pid)["fraction"] - 0.7) < 1e-9
    job_progress.finish(pid)
    assert job_progress.get(pid)["done"] and job_progress.get(pid)["fraction"] == 1.0
    job_progress.report("bad id", 0.5)  # ignored, no error
    assert job_progress.get("bad id") is None


@needs_ffmpeg
def test_remove_and_enhance_report_rising_progress_to_the_end():
    seen = []
    data = _tone(120, gap=True)
    remove_regions(data, [{"start": 10, "end": 14}], lambda f, s=None: seen.append((f, s)))
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    assert any(0.2 < f < 1.0 for f in fractions), f"no progress between start and end: {fractions}"
    assert "Removing the silence" in {s for _, s in seen}

    seen.clear()
    out, length, filters, reasons = auto_enhance(data, ".mp3", lambda f, s=None: seen.append((f, s)))
    assert out and abs(length - 120) < 1.0 and filters and reasons
    fractions = [f for f, _ in seen]
    assert fractions[-1] == 1.0 and fractions == sorted(fractions)
    stages = [s for _, s in seen if s]
    assert stages.index("Listening for volume and peaks") < stages.index("Enhancing")


@pytest.fixture
def local_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(storage_service, "LOCAL_STORAGE_DIR", str(tmp_path))
    return tmp_path


@needs_ffmpeg
@pytest.mark.asyncio
async def test_progress_endpoint_follows_an_edit(client, auth_headers, db_session, local_storage):
    key = f"assets/{uuid.uuid4()}.mp3"
    await storage_service.upload_file(_tone(40, gap=True), key)
    a = Asset(id=uuid.uuid4(), title="Gap", file_path=key, asset_type="music", duration=40)
    db_session.add(a)
    await db_session.commit()

    pid = f"p{uuid.uuid4().hex}"
    r = await client.get(f"/api/v1/assets/progress/{pid}", headers=auth_headers)
    assert r.json() == {"fraction": 0.0, "stage": None, "done": False, "error": None}  # not started yet
    r = await client.post(f"/api/v1/assets/{a.id}/auto-enhance", params={"progress_id": pid}, headers=auth_headers)
    assert r.status_code == 200, r.text
    r = await client.get(f"/api/v1/assets/progress/{pid}", headers=auth_headers)
    assert r.json() == {"fraction": 1.0, "stage": "Done", "done": True, "error": None}

    pid2 = f"q{uuid.uuid4().hex}"
    r = await client.post(f"/api/v1/assets/{a.id}/remove-silence", params={"progress_id": pid2},
                          json={"regions": [{"start": 0, "end": 99}]}, headers=auth_headers)
    assert r.status_code == 400
    r = await client.get(f"/api/v1/assets/progress/{pid2}", headers=auth_headers)
    assert r.json()["done"] and r.json()["error"]  # the page stops waiting and says why

    assert (await client.get("/api/v1/assets/progress/bad", headers=auth_headers)).status_code == 400
