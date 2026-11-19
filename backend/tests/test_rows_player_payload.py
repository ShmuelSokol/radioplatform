"""V1-55: the now-playing payload when an item has no usable file path.

The payload builder calls .startswith on the file path of the item on air and of the
next item. A null path cannot reach it (the column is NOT NULL); an empty path, or a
storage path when storage is not configured, gives audio_url None and no crash, so
listeners keep the item on air.
"""
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.api.v1 import queue as queue_module
from app.models.asset import Asset
from app.services.scheduler_engine import SchedulerEngine
from tests.test_queue_advance import _station_with_two_tracks


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


def test_file_path_column_is_not_nullable():
    assert Asset.__table__.c.file_path.nullable is False


@pytest.mark.asyncio
async def test_null_file_path_is_refused_by_the_database(db_session):
    db_session.add(Asset(id=uuid.uuid4(), title="No file", duration=10.0, file_path=None, asset_type="music"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["", "audio/no-storage.mp3"])
async def test_payload_with_unusable_paths_does_not_crash(db_session, path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: False))
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10)
    await db_session.refresh(playing, ["asset"])
    await db_session.refresh(pending, ["asset"])
    playing.asset.file_path = path
    pending.asset.file_path = path
    await db_session.commit()
    await db_session.refresh(playing, ["asset"])
    payload = await SchedulerEngine().queue_entry_payload(db_session, station.id, playing)
    assert payload is not None
    # listeners keep the item on air: same asset, no URL to replace it with
    assert payload["asset_id"] == str(playing.asset_id)
    assert payload["next_asset"]["id"] == str(pending.asset_id)
    assert payload["next_asset"]["audio_url"] is None
