"""Deploys must not freeze the live backend.

A schema change takes an exclusive lock on its table even when the column is
already there, and it waits behind any open transaction while every other query
waits behind it. Startup now skips statements whose column or index exists, and
the audio analysis closes its transaction before the slow network and FFmpeg work.
"""
import uuid

import pytest

from app.main import migrations_to_run
from app.models.asset import Asset


def test_existing_columns_and_indexes_are_not_touched():
    stmts = [
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS metadata_extra JSONB",
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS intro_seconds DOUBLE PRECISION",
        "alter table Users add column if not exists last_seen_at TIMESTAMPTZ",
        "CREATE INDEX IF NOT EXISTS ix_play_logs_station_start ON play_logs (station_id, start_utc)",
        "CREATE INDEX IF NOT EXISTS ix_new_one ON play_logs (asset_id)",
        "CREATE TABLE IF NOT EXISTS audit_logs (id UUID PRIMARY KEY)",
        "UPDATE holiday_windows SET reason = 'Manual' WHERE reason IS NULL",
    ]
    cols = {("assets", "metadata_extra"), ("users", "last_seen_at"), ("audit_logs", "id")}
    idx = {"ix_play_logs_station_start"}
    assert migrations_to_run(stmts, cols, idx) == [stmts[1], stmts[4], stmts[6]]
    # a table that is not there yet is created
    assert stmts[5] in migrations_to_run(stmts, cols - {("audit_logs", "id")}, idx)


def test_unknown_schema_runs_everything():
    stmts = ["ALTER TABLE assets ADD COLUMN IF NOT EXISTS x INT", "CREATE INDEX IF NOT EXISTS ix_a ON t (a)"]
    assert migrations_to_run(stmts, set(), set()) == stmts


@pytest.mark.asyncio
async def test_analysis_holds_no_transaction_while_measuring(db_session, monkeypatch):
    from app.services import audio_analysis_service, tail_analysis

    asset = Asset(id=uuid.uuid4(), title="Song", duration=200.0, asset_type="music",
                  file_path="https://cdn.example.com/song.mp3")
    db_session.add(asset)
    await db_session.commit()

    seen = {}

    async def fake_range(url, previous, client=None):
        seen["in_transaction"] = db_session.in_transaction()
        return {"version": 2, "method": "range", "duration": 190.0, "loudness_lufs": -14.0,
                "cue_in_seconds": 0.0, "cue_out_seconds": 188.0}

    monkeypatch.setattr(tail_analysis, "analyze_by_range", fake_range)
    await audio_analysis_service.analyze_audio(db_session, str(asset.id))
    await db_session.commit()

    assert seen["in_transaction"] is False
    await db_session.refresh(asset)
    assert asset.metadata_extra["audio_analysis"]["cue_out_seconds"] == 188.0
    assert asset.duration == 190.0


@pytest.mark.asyncio
async def test_bulk_analysis_request_ends_before_the_job(client, auth_headers, db_session, monkeypatch):
    """The job must not run inside the request (its session would stay open for hours)."""
    import asyncio

    from app.api.v1 import assets as assets_api

    db_session.add(Asset(id=uuid.uuid4(), title="Song", duration=200.0, asset_type="music",
                         file_path="https://cdn.example.com/song.mp3"))
    await db_session.commit()

    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_job(job_id, asset_ids):
        started.set()
        await release.wait()

    monkeypatch.setattr(assets_api, "_run_bulk_analyze", slow_job)
    resp = await asyncio.wait_for(
        client.post("/api/v1/assets/bulk-analyze", headers=auth_headers), timeout=5
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1
    await asyncio.wait_for(started.wait(), timeout=5)
    release.set()
