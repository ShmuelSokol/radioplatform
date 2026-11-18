"""Finding where a slow request's time goes: the event loop being held, and
database connections that are refused for a moment."""
import asyncio
import logging
import time

import pytest

from app.core import loop_watch
from app.db import engine as db_engine


class _Lines(logging.Handler):
    """The app's loggers do not all reach pytest's caplog; listen directly."""

    def __init__(self, name):
        super().__init__(logging.WARNING)
        self.lines = []
        self.logger = logging.getLogger(name)

    def emit(self, record):
        self.lines.append(record.getMessage())

    def __enter__(self):
        self.logger.addHandler(self)
        return self.lines

    def __exit__(self, *exc):
        self.logger.removeHandler(self)


@pytest.mark.asyncio
async def test_a_held_event_loop_is_logged_with_what_held_it():
    """A query the database ran in 129 ms took 12 s as the app saw it
    (2026-11-18). The log now names whatever keeps the loop from running."""
    loop_watch.start()
    try:
        await asyncio.sleep(0.3)
        before = time.monotonic()

        def a_long_job_without_await():
            time.sleep(1.6)

        with _Lines("app.core.loop_watch") as lines:
            a_long_job_without_await()
            await asyncio.sleep(0.5)
        held = [line for line in lines if "EVENT LOOP HELD" in line]
        assert len(held) == 1  # once per hold-up, not every check
        assert "a_long_job_without_await" in held[0]
        assert any("EVENT LOOP free again" in line for line in lines)
        assert 1.3 < loop_watch.stalled_since(before) < 2.5
        assert loop_watch.stalled_since(time.monotonic()) == 0.0
    finally:
        loop_watch.stop()


@pytest.mark.asyncio
async def test_a_quiet_loop_logs_nothing():
    loop_watch.start()
    try:
        with _Lines("app.core.loop_watch") as lines:
            await asyncio.sleep(0.6)
        assert not lines
    finally:
        loop_watch.stop()


@pytest.mark.asyncio
async def test_a_refused_connection_is_tried_again(monkeypatch):
    """At the 09:31Z restart on 2026-11-18 the database refused connections for
    6 s; requests answered 500 at once."""
    import asyncpg

    calls = []

    async def connect(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            raise ConnectionRefusedError(111, "Connection refused")
        return "connection"

    monkeypatch.setattr(asyncpg, "connect", connect)
    monkeypatch.setattr(db_engine, "CONNECT_RETRY_WAITS", (0.0, 0.0, 0.0))
    with _Lines("app.db.engine") as lines:
        assert await db_engine._connect(host="db", statement_cache_size=0) == "connection"
    assert len(calls) == 3 and calls[0] == {"host": "db", "statement_cache_size": 0}
    assert sum("attempt" in line for line in lines) == 2


@pytest.mark.asyncio
async def test_a_database_that_stays_away_still_fails(monkeypatch):
    import asyncpg

    calls = []

    async def connect(*args, **kwargs):
        calls.append(1)
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr(asyncpg, "connect", connect)
    monkeypatch.setattr(db_engine, "CONNECT_RETRY_WAITS", (0.0, 0.0))
    with pytest.raises(ConnectionRefusedError):
        await db_engine._connect(host="db")
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_a_wrong_password_is_not_retried(monkeypatch):
    import asyncpg

    calls = []

    async def connect(*args, **kwargs):
        calls.append(1)
        raise asyncpg.exceptions.InvalidPasswordError("no")

    monkeypatch.setattr(asyncpg, "connect", connect)
    with pytest.raises(asyncpg.exceptions.InvalidPasswordError):
        await db_engine._connect(host="db")
    assert len(calls) == 1


def test_pool_state_reads_as_words():
    assert db_engine.pool_state().startswith("pool ")
