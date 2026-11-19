"""A pooled database connection whose link dies silently must not hold a request for
the kernel's 15.6 minutes (2026-11-19 08:23-09:22Z: start reports, queue reads and
/asset-types hung 939 s each). The tests run on SQLite, so the Postgres side is
checked by its settings and by the pieces that act on a timeout; the real thing
(a proxy that stops forwarding) was run against local Postgres: a stale pooled
connection was replaced in 7 s and a call on a link that died in use failed after
60 s, its connection dropped and the next checkout answered at once."""
import asyncio

import pytest
from sqlalchemy.exc import DisconnectionError

from app.db import engine as db


def test_postgres_connections_have_a_time_limit_on_every_call():
    args = db.connect_args(True)
    assert args["command_timeout"] == db.COMMAND_TIMEOUT_SECONDS
    assert 10 <= db.COMMAND_TIMEOUT_SECONDS <= 120
    assert db.PING_TIMEOUT_SECONDS <= 10
    # a client-side timer only: nothing the pooler would refuse at startup
    assert "server_settings" not in args and "options" not in args
    assert "command_timeout" not in db.connect_args(False)


class _Context:
    def __init__(self, exc):
        self.original_exception = exc
        self.is_disconnect = False


def test_a_call_that_ran_out_of_time_drops_its_connection():
    ctx = _Context(asyncio.TimeoutError())
    db._discard_timed_out(ctx)
    assert ctx.is_disconnect is True


def test_other_errors_keep_their_connection():
    ctx = _Context(ValueError("bad value"))
    db._discard_timed_out(ctx)
    assert ctx.is_disconnect is False


class _Record:
    def __init__(self, idle_for):
        import time

        self.info = {"idle_since": time.monotonic() - idle_for}


class _HangingDriver:
    """An asyncpg connection on a dead link: the ping is never answered."""

    def __init__(self):
        self.timeout = None

    async def fetchval(self, query, timeout=None):
        self.timeout = timeout
        await asyncio.sleep(timeout if timeout is not None else 3600)
        raise asyncio.TimeoutError()


class _DbapiConn:
    def __init__(self):
        self.driver_connection = _HangingDriver()


@pytest.mark.asyncio
async def test_an_idle_connection_on_a_dead_link_is_replaced_within_seconds(monkeypatch):
    from sqlalchemy.util import greenlet_spawn

    monkeypatch.setattr(db, "PING_TIMEOUT_SECONDS", 0.2)
    conn = _DbapiConn()
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(DisconnectionError):
        await greenlet_spawn(db._ping_if_idle, conn, _Record(db.PING_AFTER_IDLE_SECONDS + 1), None)
    assert conn.driver_connection.timeout == 0.2
    assert loop.time() - started < 2


@pytest.mark.asyncio
async def test_a_recently_used_connection_is_not_pinged():
    from sqlalchemy.util import greenlet_spawn

    conn = _DbapiConn()
    await greenlet_spawn(db._ping_if_idle, conn, _Record(0.0), None)
    assert conn.driver_connection.timeout is None
