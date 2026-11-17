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

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_DEBUG,
    pool_recycle=300,
    # Reuse the most recently used connection so it stays warm and the rest age out.
    pool_use_lifo=True,
    connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
)


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
        logger.warning(
            "SLOW QUERY (%.3fs): %s | params=%s",
            elapsed,
            statement[:500],
            _redacted_params(parameters, executemany),
        )


def _redacted_params(parameters, executemany: bool) -> str | None:
    if not parameters:
        return None
    if executemany and isinstance(parameters, (list, tuple)):
        return f"<{len(parameters)} rows hidden>"
    try:
        return f"<{len(parameters)} hidden>"
    except TypeError:
        return "<hidden>"
