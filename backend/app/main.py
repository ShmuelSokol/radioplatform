import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.config import settings
from app.core.middleware import setup_middleware

logger = logging.getLogger(__name__)

_tables_created = False


async def ensure_tables():
    """Create DB tables if they haven't been created yet, and add missing columns."""
    global _tables_created
    if _tables_created:
        return
    try:
        from app.db.engine import engine
        from app.db.base import Base
        import app.models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Add missing columns to existing tables (create_all doesn't ALTER)
        await _add_missing_columns(engine)

        _tables_created = True
    except Exception as e:
        logging.warning(f"Table creation skipped: {e}")


async def _add_missing_columns(engine):
    """Add columns that were added to models but missing from existing DB tables."""
    from sqlalchemy import text

    migrations = [
        "ALTER TABLE channel_streams ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true",
        "ALTER TABLE channel_streams ADD COLUMN IF NOT EXISTS schedule_id UUID REFERENCES schedules(id) ON DELETE SET NULL",
        "ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS channel_id UUID REFERENCES channel_streams(id) ON DELETE SET NULL",
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS review_status VARCHAR(50) DEFAULT 'pending'",
    ]
    try:
        async with engine.begin() as conn:
            for sql in migrations:
                await conn.execute(text(sql))
        logger.info("Column migrations applied successfully")
    except Exception as e:
        logger.warning(f"Column migration skipped: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup — create tables and start the scheduler engine
    await ensure_tables()

    try:
        from app.services.scheduler_engine import start_scheduler
        await start_scheduler()
        logger.info("Scheduler engine started")
    except Exception as e:
        logger.warning(f"Scheduler engine failed to start: {e}")

    yield

    # Shutdown — stop the scheduler cleanly
    try:
        from app.services.scheduler_engine import stop_scheduler
        await stop_scheduler()
        logger.info("Scheduler engine stopped")
    except Exception as e:
        logger.warning(f"Scheduler engine failed to stop: {e}")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Studio Kol Bramah API",
        version="0.1.0",
        description="Multi-channel radio streaming platform",
        debug=settings.APP_DEBUG,
        lifespan=lifespan,
    )

    setup_middleware(app)

    @app.get("/health")
    async def health_check():
        import shutil
        import subprocess
        ffmpeg_info = {}
        ffmpeg_info["which"] = shutil.which("ffmpeg")
        try:
            proc = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
            ffmpeg_info["rc"] = proc.returncode
            ffmpeg_info["version"] = proc.stdout[:120].decode(errors="replace")
        except FileNotFoundError:
            ffmpeg_info["error"] = "not found"
        except Exception as e:
            ffmpeg_info["error"] = str(e)
        return {"status": "ok", "ffmpeg": ffmpeg_info}

    @app.get("/api/v1/init")
    async def init_db():
        await ensure_tables()
        return {"status": "tables_created", "created": _tables_created}

    # Register API routers
    from app.api.v1 import router as api_v1_router
    app.include_router(api_v1_router, prefix="/api/v1")

    # Fallback middleware for environments without lifespan support (e.g. Vercel)
    @app.middleware("http")
    async def auto_create_tables(request, call_next):
        await ensure_tables()
        return await call_next(request)

    return app


app = create_app()
