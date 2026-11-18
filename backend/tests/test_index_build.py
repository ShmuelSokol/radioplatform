"""The background index build after start: a refused connection or a failed build is
tried again, and the log says once which indexes exist.

On production (2026-11-18) the database refused connections for about 6 s right
after a deploy; the build gave up at once and nothing in the log said whether the
on-air self-check's indexes were there.
"""
import logging

import pytest

import app.main as app_main


class FakeConn:
    def __init__(self, db):
        self.db = db

    async def execute(self, sql):
        if sql.startswith("CREATE INDEX"):
            name = sql.split("IF NOT EXISTS ")[1].split(" ")[0]
            if self.db["fail_once"].pop(name, None):
                raise RuntimeError("canceling statement due to statement timeout")
            self.db["valid"][name] = True
        elif sql.startswith("DROP INDEX"):
            self.db["valid"].pop(sql.split("IF EXISTS ")[1], None)

    async def fetchval(self, sql, name):
        return self.db["valid"].get(name)

    async def close(self):
        pass


def fake_database(monkeypatch, refuse=0, fail_once=(), valid=None):
    import asyncpg

    db = {"refuse": refuse, "fail_once": {n: True for n in fail_once}, "valid": dict(valid or {}), "connects": 0}

    async def connect(dsn, **kw):
        db["connects"] += 1
        if db["refuse"] > 0:
            db["refuse"] -= 1
            raise ConnectionRefusedError("the database system is starting up")
        return FakeConn(db)

    monkeypatch.setattr(asyncpg, "connect", connect)
    return db


@pytest.mark.asyncio
async def test_a_refused_connection_is_tried_again(monkeypatch, caplog):
    db = fake_database(monkeypatch, refuse=2)
    with caplog.at_level(logging.INFO, logger=app_main.logger.name):
        missing = await app_main._build_concurrent_indexes("postgresql://x", wait=0)
    assert missing == []
    assert db["connects"] == 3
    assert all(db["valid"].get(n) for n in app_main.CONCURRENT_INDEXES)
    assert "Indexes in place: " in caplog.text


@pytest.mark.asyncio
async def test_a_failed_build_is_tried_again_and_only_that_one(monkeypatch, caplog):
    name = "ix_queue_entries_station_started"
    db = fake_database(monkeypatch, fail_once=[name])
    with caplog.at_level(logging.INFO, logger=app_main.logger.name):
        missing = await app_main._build_concurrent_indexes("postgresql://x", wait=0)
    assert missing == []
    assert db["connects"] == 2
    assert f"Index {name} not built" in caplog.text
    assert caplog.text.count("Built index ix_queue_entries_station_preempt") == 1
    assert f"Built index {name}" in caplog.text


@pytest.mark.asyncio
async def test_existing_indexes_are_named_in_the_log_and_an_unusable_one_is_rebuilt(monkeypatch, caplog):
    valid = {n: True for n in app_main.CONCURRENT_INDEXES}
    valid["ix_queue_entries_asset"] = False  # left by an interrupted build
    fake_database(monkeypatch, valid=valid)
    with caplog.at_level(logging.INFO, logger=app_main.logger.name):
        await app_main._build_concurrent_indexes("postgresql://x", wait=0)
    assert caplog.text.count("Built index") == 1
    assert "Built index ix_queue_entries_asset" in caplog.text
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Indexes in place")][0]
    assert all(n in line for n in app_main.CONCURRENT_INDEXES)


@pytest.mark.asyncio
async def test_a_database_that_never_answers_is_an_error_in_the_log(monkeypatch, caplog):
    db = fake_database(monkeypatch, refuse=99)
    with caplog.at_level(logging.INFO, logger=app_main.logger.name):
        missing = await app_main._build_concurrent_indexes("postgresql://x", wait=0)
    assert missing == list(app_main.CONCURRENT_INDEXES)
    assert db["connects"] == app_main.INDEX_BUILD_TRIES
    assert "INDEXES MISSING" in caplog.text
