"""Deleting a library item must be quick and must not hang the backend.

2026-11-18: removing duplicate songs one by one, a DELETE /assets/{id} took 42 s on
production. Two causes, both checked here:
- nothing indexed the columns that point at an item (queue_entries.asset_id and
  others), so every delete read those tables whole;
- the delete waited without limit for a queue refill that held the item's queue
  entries. It now gives up after a few seconds and answers 503 "busy, try again".
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401  (every table)
from app import main as app_main
from app.db.base import Base
from app.models.asset import Asset
from app.services import asset_service

API = "/api/v1"


def test_every_column_that_points_at_an_item_is_indexed():
    """A delete looks in each of these tables; without an index that is a full read."""
    built_later = {target.replace(" ", "") for target in app_main.CONCURRENT_INDEXES.values()}
    missing = []
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if not any(fk.column.table.name == "assets" and fk.column.name == "id" for fk in col.foreign_keys):
                continue
            leads_an_index = col.index or any(
                list(ix.columns)[0] is col for ix in table.indexes
            ) or (col.primary_key and list(table.primary_key.columns)[0] is col)
            starts_a_later_one = any(t.startswith(f"{table.name}({col.name}") for t in built_later)
            if not (leads_an_index or starts_a_later_one):
                missing.append(f"{table.name}.{col.name}")
    assert not missing, f"add these to CONCURRENT_INDEXES in app/main.py: {missing}"


def test_big_table_indexes_are_built_without_locking():
    """The indexes for deletes are in the list that is built CONCURRENTLY after startup
    (no write lock on queue_entries or play_logs), not in the startup migration list."""
    assert app_main.CONCURRENT_INDEXES["ix_queue_entries_asset"] == "queue_entries (asset_id)"
    assert app_main.CONCURRENT_INDEXES["ix_play_logs_asset_start"].startswith("play_logs (asset_id")


def test_lock_and_statement_timeouts_are_told_apart_from_other_errors():
    class OrigError(Exception):
        sqlstate = None

    def err(msg, code=None):
        o = OrigError(msg)
        o.sqlstate = code
        return DBAPIError("DELETE", {}, o)

    assert asset_service._is_timeout(err("canceling statement due to lock timeout", "55P03"))
    assert asset_service._is_timeout(err("canceling statement due to statement timeout", "57014"))
    assert asset_service._is_timeout(err("canceling statement due to lock timeout"))
    assert not asset_service._is_timeout(err("violates foreign key constraint", "23503"))
    assert asset_service.DELETE_LOCK_TIMEOUT_MS <= 5000


@pytest.mark.asyncio
async def test_a_delete_that_cannot_get_its_locks_answers_busy_and_deletes_nothing(
    client, auth_headers, db_session, monkeypatch
):
    asset = Asset(id=uuid.uuid4(), title="Duplicate copy", file_path="assets/dup.mp3", asset_type="music", duration=100.0)
    db_session.add(asset)
    await db_session.commit()
    asset_id = asset.id

    real_flush = AsyncSession.flush

    async def held_by_a_refill(self, *a, **kw):
        if any(isinstance(o, Asset) for o in self.sync_session.deleted):
            class LockNotAvailableError(Exception):
                sqlstate = "55P03"
            raise DBAPIError("DELETE FROM assets", {}, LockNotAvailableError("canceling statement due to lock timeout"))
        return await real_flush(self, *a, **kw)

    monkeypatch.setattr(AsyncSession, "flush", held_by_a_refill)
    r = await client.delete(f"{API}/assets/{asset_id}", headers=auth_headers)
    assert r.status_code == 503, r.text
    assert r.headers.get("retry-after") == "5"
    assert "try again" in r.json()["detail"]

    monkeypatch.setattr(AsyncSession, "flush", real_flush)
    db_session.expire_all()
    assert (await db_session.execute(select(Asset.id).where(Asset.id == asset_id))).scalar_one_or_none() is not None
    # once the refill is done the same call deletes it
    r = await client.delete(f"{API}/assets/{asset_id}", headers=auth_headers)
    assert r.status_code == 204, r.text
    db_session.expire_all()
    assert (await db_session.execute(select(Asset.id).where(Asset.id == asset_id))).scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_another_database_error_is_not_called_busy(client, auth_headers, db_session, monkeypatch):
    asset = Asset(id=uuid.uuid4(), title="Other error", file_path="assets/o.mp3", asset_type="music", duration=100.0)
    db_session.add(asset)
    await db_session.commit()
    real_flush = AsyncSession.flush

    async def broken(self, *a, **kw):
        if any(isinstance(o, Asset) for o in self.sync_session.deleted):
            raise DBAPIError("DELETE FROM assets", {}, Exception("connection reset"))
        return await real_flush(self, *a, **kw)

    monkeypatch.setattr(AsyncSession, "flush", broken)
    with pytest.raises(DBAPIError):
        await asset_service.delete_asset(db_session, asset.id)
