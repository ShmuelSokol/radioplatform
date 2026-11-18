import logging
import time

from sqlalchemy import event
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

logger = logging.getLogger(__name__)

SLOW_QUERY_THRESHOLD = 0.5  # seconds

# A pooled connection is only tested before use if it has sat idle this long.
# Testing costs four network round trips to the database (BEGIN, parse, run,
# ROLLBACK), which `pool_pre_ping=True` paid on every single request.
PING_AFTER_IDLE_SECONDS = 15.0

# The database refuses new connections for a few seconds now and then (6 s at the
# 09:31Z restart on 2026-11-18: four requests answered 500 and the scheduler
# missed two rounds). A refused connection is tried again for this long in all.
CONNECT_RETRY_WAITS = (0.3, 0.6, 1.2, 2.4, 3.5)
SLOW_CONNECT_SECONDS = 1.0

_POSTGRES = settings.DATABASE_URL.startswith("postgresql")


async def _connect(*args, **kwargs):
    """Opens one database connection; a refused or dropped attempt is repeated."""
    import asyncio

    import asyncpg

    started = time.monotonic()
    attempt = 0
    while True:
        try:
            conn = await asyncpg.connect(*args, **kwargs)
            break
        except (OSError, asyncpg.exceptions.CannotConnectNowError, asyncpg.exceptions.ConnectionDoesNotExistError) as e:
            if attempt >= len(CONNECT_RETRY_WAITS):
                raise
            logger.warning(
                "Database connection attempt %d failed (%s: %s); trying again in %.1f s",
                attempt + 1, type(e).__name__, e, CONNECT_RETRY_WAITS[attempt],
            )
            await asyncio.sleep(CONNECT_RETRY_WAITS[attempt])
            attempt += 1
    took = time.monotonic() - started
    if took >= SLOW_CONNECT_SECONDS:
        logger.warning(
            "SLOW CONNECT (%.3fs) after %d failed attempt(s) | %s | loop held %.1f s of it | for %s",
            took, attempt, pool_state(), _loop_held(started), _asked_by(),
        )
    return conn


# The pool keeps this many connections open. With the default (5) every request
# beyond five at once opened a connection of its own, about 2 s each, and threw it
# away afterwards: three stations' engines, the scheduler, air watch and a few
# open pages are more than five.
POOL_SIZE = 12
MAX_OVERFLOW = 8

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_DEBUG,
    pool_recycle=300,
    # Reuse the most recently used connection so it stays warm and the rest age out.
    pool_use_lifo=True,
    **({"pool_size": POOL_SIZE, "max_overflow": MAX_OVERFLOW} if _POSTGRES else {}),
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        **({"async_creator_fn": _connect} if _POSTGRES else {}),
    },
)


def pool_state() -> str:
    """"pool 7 of 12 in use, 0 extra" for log lines."""
    try:
        pool = engine.pool
        return f"pool {pool.checkedout()} in use of {pool.size()}, {max(pool.overflow(), 0)} extra"
    except Exception:
        return "pool unknown"


def _loop_held(since: float) -> float:
    try:
        from app.core import loop_watch

        return loop_watch.stalled_since(since)
    except Exception:
        return 0.0


@event.listens_for(engine.sync_engine, "checkin")
def _mark_idle(dbapi_conn, connection_record):
    connection_record.info["idle_since"] = time.monotonic()


@event.listens_for(engine.sync_engine, "checkout")
def _ping_if_idle(dbapi_conn, connection_record, connection_proxy):
    idle_since = connection_record.info.get("idle_since")
    if idle_since is None or time.monotonic() - idle_since < PING_AFTER_IDLE_SECONDS:
        return
    try:
        dbapi_conn.ping()
    except Exception as e:
        # Tells the pool to throw this connection away and hand out a fresh one.
        raise DisconnectionError("stale pooled connection") from e

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# Slow query logging — logs any query taking > 500ms
@event.listens_for(engine.sync_engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info["query_start_time"] = time.monotonic()


@event.listens_for(engine.sync_engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    start = conn.info.pop("query_start_time", None)
    if start is None:
        return
    elapsed = time.monotonic() - start
    if elapsed >= SLOW_QUERY_THRESHOLD:
        # Parameter values are left out: they hold emails and phone numbers, and bug
        # reports copy this log. Only how many there were is kept.
        # "loop held" is how much of the wait was this process busy with other
        # work (see app/core/loop_watch.py), not the database
        logger.warning(
            "SLOW QUERY (%.3fs): %s | params=%s | for %s | loop held %.1f s of it | %s",
            elapsed,
            _shown(statement),
            _redacted_params(parameters, executemany),
            _asked_by(),
            _loop_held(start),
            pool_state(),
        )


def _shown(statement: str) -> str:
    """The start and the end of a statement. The start alone (the column list)
    did not say which query it was: the WHERE and ORDER BY are at the end."""
    statement = " ".join(statement.split())
    if len(statement) <= 800:
        return statement
    return f"{statement[:500]} ... {statement[-300:]}"


def _asked_by() -> str:
    """The request the query ran for, or "background"."""
    try:
        from app.core.log_buffer import request_path_var

        return request_path_var.get() or "background"
    except Exception:
        return "unknown"


def _redacted_params(parameters, executemany: bool) -> str | None:
    if not parameters:
        return None
    if executemany and isinstance(parameters, (list, tuple)):
        return f"<{len(parameters)} rows hidden>"
    try:
        return f"<{len(parameters)} hidden>"
    except TypeError:
        return "<hidden>"
