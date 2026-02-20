import logging
import time

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

logger = logging.getLogger(__name__)

SLOW_QUERY_THRESHOLD = 0.5  # seconds

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_DEBUG,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
)

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
        logger.warning(
            "SLOW QUERY (%.3fs): %s | params=%s",
            elapsed,
            statement[:500],
            str(parameters)[:200] if parameters else None,
        )
