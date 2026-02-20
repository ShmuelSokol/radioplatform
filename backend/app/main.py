import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.config import settings
from app.core.middleware import setup_middleware

logger = logging.getLogger(__name__)

# Initialize Sentry error tracking (if DSN configured)
if settings.sentry_enabled:
    try:
        import sentry_sdk
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            traces_sample_rate=0.1,
            profiles_sample_rate=0.1,
            environment=settings.APP_ENV,
            send_default_pii=False,
        )
        logger.info("Sentry error tracking initialized")
    except Exception as e:
        logger.warning("Sentry init failed: %s", e)

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

    # ALTER TYPE ADD VALUE cannot run inside a transaction — use autocommit
    enum_migrations = [
        "ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'SPONSOR'",
        "ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'LIVE_SHOW'",
        "ALTER TYPE alert_type ADD VALUE IF NOT EXISTS 'SILENCE'",
        # Live show enum types (created by create_all, but safe to re-run)
        # SQLAlchemy uses enum member NAMES (uppercase) for PostgreSQL enum values
        "DO $$ BEGIN CREATE TYPE live_show_status AS ENUM ('SCHEDULED','LIVE','ENDED','CANCELLED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$",
        "DO $$ BEGIN CREATE TYPE broadcast_mode AS ENUM ('WEBRTC','ICECAST'); EXCEPTION WHEN duplicate_object THEN NULL; END $$",
        "DO $$ BEGIN CREATE TYPE call_status AS ENUM ('WAITING','SCREENING','APPROVED','ON_AIR','COMPLETED','REJECTED','ABANDONED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$",
        "DO $$ BEGIN CREATE TYPE request_status AS ENUM ('PENDING','APPROVED','QUEUED','PLAYED','REJECTED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$",
        "DO $$ BEGIN CREATE TYPE readout_status AS ENUM ('pending','recorded','queued','skipped'); EXCEPTION WHEN duplicate_object THEN NULL; END $$",
    ]
    # asyncpg is autocommit by default — bypasses SQLAlchemy transaction wrapping
    # which is required for ALTER TYPE ADD VALUE (cannot run inside a transaction)
    import asyncpg
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    try:
        raw_conn = await asyncpg.connect(dsn, statement_cache_size=0)
        try:
            for sql in enum_migrations:
                try:
                    await raw_conn.execute(sql)
                except Exception as e:
                    logger.warning(f"Enum migration skipped ({sql[:50]}...): {e}")
        finally:
            await raw_conn.close()
    except Exception as e:
        logger.warning(f"Could not connect for enum migrations: {e}")

    migrations = [
        "ALTER TABLE channel_streams ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true",
        "ALTER TABLE channel_streams ADD COLUMN IF NOT EXISTS schedule_id UUID REFERENCES schedules(id) ON DELETE SET NULL",
        "ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS channel_id UUID REFERENCES channel_streams(id) ON DELETE SET NULL",
        "ALTER TABLE now_playing ADD COLUMN IF NOT EXISTS channel_id UUID REFERENCES channel_streams(id) ON DELETE SET NULL",
        "ALTER TABLE now_playing ADD COLUMN IF NOT EXISTS listener_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE now_playing ADD COLUMN IF NOT EXISTS stream_url TEXT",
        "ALTER TABLE now_playing ADD COLUMN IF NOT EXISTS block_id UUID REFERENCES schedule_blocks(id) ON DELETE SET NULL",
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS metadata_extra JSONB",
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS review_status VARCHAR(50) DEFAULT 'pending'",
        "ALTER TABLE schedule_blocks ADD COLUMN IF NOT EXISTS playlist_template_id UUID REFERENCES playlist_templates(id) ON DELETE SET NULL",
        "ALTER TABLE stations ADD COLUMN IF NOT EXISTS automation_config JSONB",
        # Sponsor portal columns
        "ALTER TABLE sponsors ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL",
        "ALTER TABLE sponsors ADD COLUMN IF NOT EXISTS contact_email VARCHAR(255)",
        "ALTER TABLE sponsors ADD COLUMN IF NOT EXISTS contact_phone VARCHAR(50)",
        "ALTER TABLE sponsors ADD COLUMN IF NOT EXISTS company_name VARCHAR(255)",
        # User notification fields
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_number VARCHAR(50)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS title VARCHAR(100)",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS alert_preferences JSONB",
        # Station-specific rules
        "ALTER TABLE schedule_rules ADD COLUMN IF NOT EXISTS station_id UUID REFERENCES stations(id) ON DELETE CASCADE",
        # DJ/Host profile fields
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS photo_url TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_public BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS social_links JSONB",
        # Show archives table columns
        "ALTER TABLE show_archives ADD COLUMN IF NOT EXISTS station_id UUID REFERENCES stations(id) ON DELETE CASCADE",
        "ALTER TABLE show_archives ADD COLUMN IF NOT EXISTS title VARCHAR(500) NOT NULL DEFAULT ''",
        "ALTER TABLE show_archives ADD COLUMN IF NOT EXISTS description TEXT",
        "ALTER TABLE show_archives ADD COLUMN IF NOT EXISTS host_name VARCHAR(255)",
        "ALTER TABLE show_archives ADD COLUMN IF NOT EXISTS recorded_at TIMESTAMPTZ",
        "ALTER TABLE show_archives ADD COLUMN IF NOT EXISTS duration_seconds INTEGER",
        "ALTER TABLE show_archives ADD COLUMN IF NOT EXISTS audio_url TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE show_archives ADD COLUMN IF NOT EXISTS cover_image_url TEXT",
        "ALTER TABLE show_archives ADD COLUMN IF NOT EXISTS is_published BOOLEAN NOT NULL DEFAULT true",
        "ALTER TABLE show_archives ADD COLUMN IF NOT EXISTS download_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE show_archives ADD COLUMN IF NOT EXISTS live_show_id UUID REFERENCES live_shows(id) ON DELETE SET NULL",
        # Link library assets to sponsors
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS sponsor_id UUID REFERENCES sponsors(id) ON DELETE SET NULL",
        # Release date for music assets (MusicBrainz auto-detection)
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS release_date DATE",
        # Preempt mechanism for exact-time playback (time announcements)
        "ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS preempt_at TIMESTAMPTZ",
        # Schedule block columns that may be missing from early table creation
        "ALTER TABLE schedule_blocks ADD COLUMN IF NOT EXISTS playback_mode VARCHAR(20) NOT NULL DEFAULT 'sequential'",
        "ALTER TABLE schedule_blocks ADD COLUMN IF NOT EXISTS start_date DATE",
        "ALTER TABLE schedule_blocks ADD COLUMN IF NOT EXISTS end_date DATE",
        "ALTER TABLE schedule_blocks ADD COLUMN IF NOT EXISTS start_sun_event VARCHAR(20)",
        "ALTER TABLE schedule_blocks ADD COLUMN IF NOT EXISTS start_sun_offset INTEGER",
        "ALTER TABLE schedule_blocks ADD COLUMN IF NOT EXISTS end_sun_event VARCHAR(20)",
        "ALTER TABLE schedule_blocks ADD COLUMN IF NOT EXISTS end_sun_offset INTEGER",
        # Holiday reason column + backfill
        "ALTER TABLE holiday_windows ADD COLUMN IF NOT EXISTS reason VARCHAR(100)",
        """UPDATE holiday_windows SET reason = CASE
            WHEN name ILIKE '%yom kippur%' THEN 'Yom Kippur'
            WHEN name ILIKE '%rosh hashanah%' THEN 'Rosh Hashanah'
            WHEN name ILIKE '%sukkot%' THEN 'Sukkot'
            WHEN name ILIKE '%shemini%' OR name ILIKE '%simchat%' THEN 'Shemini Atzeret'
            WHEN name ILIKE '%pesach%' THEN 'Pesach'
            WHEN name ILIKE '%shavuot%' THEN 'Shavuot'
            WHEN name ILIKE '%shabbos%' OR name ILIKE '%shabbat%' THEN 'Shabbos'
            ELSE 'Manual'
        END WHERE reason IS NULL""",
        # Listener sessions index for fast heartbeat lookups
        "CREATE INDEX IF NOT EXISTS ix_listener_sessions_heartbeat ON listener_sessions (last_heartbeat)",
        "CREATE INDEX IF NOT EXISTS ix_listener_sessions_started ON listener_sessions (started_at)",
        # User activity tracking
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_action VARCHAR(255)",
        # Audit log table
        """CREATE TABLE IF NOT EXISTS audit_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            user_email VARCHAR(255),
            action VARCHAR(50) NOT NULL,
            resource_type VARCHAR(100) NOT NULL,
            resource_id VARCHAR(100),
            detail TEXT,
            changes JSONB,
            ip_address VARCHAR(50),
            request_id VARCHAR(50),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_created ON audit_logs (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_user ON audit_logs (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_resource ON audit_logs (resource_type, resource_id)",
        # CRM indexes
        "CREATE INDEX IF NOT EXISTS ix_song_ratings_member ON song_ratings (member_id)",
        "CREATE INDEX IF NOT EXISTS ix_song_ratings_asset ON song_ratings (asset_id)",
        "CREATE INDEX IF NOT EXISTS ix_raffle_entries_raffle ON raffle_entries (raffle_id)",
        "CREATE INDEX IF NOT EXISTS ix_raffle_entries_member ON raffle_entries (member_id)",
    ]
    for sql in migrations:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql))
        except Exception as e:
            logger.warning(f"Migration skipped ({sql[:50]}...): {e}")


async def _seed_default_categories():
    """Insert default categories if the categories table is empty."""
    try:
        from app.db.engine import engine
        from sqlalchemy import text

        defaults = ["lively", "med_fast", "relax", "do_not_play", "requested"]
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM categories"))
            count = result.scalar()
            if count == 0:
                for name in defaults:
                    await conn.execute(
                        text("INSERT INTO categories (id, name, created_at, updated_at) VALUES (gen_random_uuid(), :name, NOW(), NOW())"),
                        {"name": name},
                    )
                logger.info("Seeded %d default categories", len(defaults))
    except Exception as e:
        logger.warning("Category seeding skipped: %s", e)


async def _seed_stations():
    """Seed the three special stations if they don't exist."""
    try:
        from app.db.engine import engine
        from sqlalchemy import text
        import json

        stations = [
            {
                "name": "Nothing New",
                "timezone": "America/New_York",
                "description": "Only the classics — songs from 10+ years ago",
                "automation_config": json.dumps({"oldies_only": True, "oldies_min_years": 10}),
            },
            {
                "name": "Kol Bramah Yerushalayim",
                "timezone": "Asia/Jerusalem",
                "description": "Broadcasting from Yerushalayim",
                "latitude": 31.7683,
                "longitude": 35.2137,
                "automation_config": None,
            },
            {
                "name": "Requests Only",
                "timezone": "America/New_York",
                "description": "You pick the music — listener requests only",
                "automation_config": json.dumps({"requests_only": True, "popular_request_threshold": 3}),
            },
        ]

        async with engine.begin() as conn:
            for s in stations:
                exists = await conn.execute(
                    text("SELECT 1 FROM stations WHERE name = :name"),
                    {"name": s["name"]},
                )
                if exists.scalar_one_or_none():
                    continue
                await conn.execute(
                    text("""
                        INSERT INTO stations (id, name, type, timezone, latitude, longitude,
                                              description, automation_config, is_active,
                                              created_at, updated_at)
                        VALUES (gen_random_uuid(), :name, 'internet', :timezone,
                                :latitude, :longitude, :description,
                                :automation_config::jsonb, true, NOW(), NOW())
                    """),
                    {
                        "name": s["name"],
                        "timezone": s["timezone"],
                        "description": s["description"],
                        "latitude": s.get("latitude"),
                        "longitude": s.get("longitude"),
                        "automation_config": s.get("automation_config"),
                    },
                )
                logger.info("Seeded station: %s", s["name"])
    except Exception as e:
        logger.warning("Station seeding skipped: %s", e)


async def _refresh_requested_category():
    """Refresh the dynamic 'requested' category on startup."""
    try:
        from app.db.engine import async_session_factory
        from app.services.requested_category_service import refresh_requested_category
        async with async_session_factory() as db:
            count = await refresh_requested_category(db)
            await db.commit()
            logger.info("Requested category refreshed: %d assets tagged", count)
    except Exception as e:
        logger.warning("Requested category refresh skipped: %s", e)


async def _resume_playback_on_startup():
    """
    Resume station playback after a server restart.

    1. Close stale PlayLog entries (end_utc=NULL → set to now, indicating downtime)
    2. Mark stale "playing" queue entries as "played" and log them
    3. Replenish queues for all active stations
    4. Start playback by advancing to next pending entry per station
    """
    from datetime import datetime, timezone

    from sqlalchemy import select, update

    from app.db.engine import async_session_factory
    from app.models.play_log import PlayLog
    from app.models.queue_entry import QueueEntry
    from app.models.station import Station

    now = datetime.now(timezone.utc)

    try:
        async with async_session_factory() as db:
            # 1. Close any open PlayLog entries (leftover from pre-restart)
            stale_logs = await db.execute(
                select(PlayLog).where(PlayLog.end_utc.is_(None))
            )
            stale_count = 0
            for log in stale_logs.scalars().all():
                log.end_utc = now
                stale_count += 1
            if stale_count:
                logger.info("Closed %d stale play log entries from before restart", stale_count)

            # 2. Mark stale "playing" queue entries as "played" and log them
            stale_playing = await db.execute(
                select(QueueEntry).where(QueueEntry.status == "playing")
            )
            stale_playing_count = 0
            for entry in stale_playing.scalars().all():
                # Log the play that was interrupted
                play_log = PlayLog(
                    station_id=entry.station_id,
                    asset_id=entry.asset_id,
                    start_utc=entry.started_at or now,
                    end_utc=now,
                    source="scheduler",
                )
                db.add(play_log)
                entry.status = "played"
                stale_playing_count += 1
            if stale_playing_count:
                logger.info("Closed %d stale playing queue entries", stale_playing_count)

            await db.commit()

            # 3. Get all active stations
            result = await db.execute(select(Station).where(Station.is_active.is_(True)))
            stations = result.scalars().all()

            for station in stations:
                try:
                    # 4a. Replenish queue
                    from app.services.queue_replenish_service import QueueReplenishService
                    replenish_svc = QueueReplenishService(db, station.id)
                    await replenish_svc.replenish()
                    await db.commit()

                    # 4b. Start playback — find next pending entry
                    next_result = await db.execute(
                        select(QueueEntry)
                        .where(
                            QueueEntry.station_id == station.id,
                            QueueEntry.status == "pending",
                        )
                        .order_by(QueueEntry.position)
                        .limit(1)
                    )
                    next_entry = next_result.scalar_one_or_none()
                    if next_entry:
                        next_entry.status = "playing"
                        next_entry.started_at = now
                        await db.commit()
                        logger.info(
                            "Station %s (%s): resumed playback with asset %s",
                            station.name, station.id, next_entry.asset_id,
                        )
                    else:
                        logger.warning("Station %s: no pending entries after replenish", station.name)
                        try:
                            from app.services.alert_service import create_alert
                            await create_alert(
                                db, alert_type="queue_empty", severity="warning",
                                title=f"Queue empty after restart: {station.name}",
                                message=f"Station '{station.name}' has no pending entries after queue replenish on startup",
                                station_id=station.id,
                            )
                        except Exception:
                            pass
                except Exception as e:
                    logger.error("Failed to resume station %s: %s", station.name, e, exc_info=True)

            # Create system restart alert
            total_recovered = stale_count + stale_playing_count
            if total_recovered > 0:
                try:
                    from app.services.alert_service import create_alert
                    await create_alert(
                        db, alert_type="system", severity="info",
                        title="Server restarted",
                        message=f"Server restarted. Recovered {total_recovered} stale entries ({stale_count} play logs, {stale_playing_count} queue entries).",
                    )
                    await db.commit()
                except Exception:
                    pass

    except Exception as e:
        logger.error("Playback resume on startup failed: %s", e, exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup — create tables and start the scheduler engine
    await ensure_tables()
    await _seed_default_categories()
    await _seed_stations()
    await _refresh_requested_category()

    # Resume playback for all stations (close stale entries, fill queues, start playing)
    try:
        await _resume_playback_on_startup()
        logger.info("Playback resumed for all active stations")
    except Exception as e:
        logger.warning(f"Playback resume failed: {e}")

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
        """Health check for Railway zero-downtime deploys.

        Returns 200 only when tables are created and DB is reachable.
        Railway keeps the old instance running until this returns 200.
        """
        if not _tables_created:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=503,
                content={"status": "starting", "detail": "Tables not yet created"},
            )
        # Verify DB connectivity
        try:
            from sqlalchemy import text
            from app.db.engine import engine
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as e:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=503,
                content={"status": "unhealthy", "detail": f"DB unreachable: {e}"},
            )
        return {"status": "ok"}

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
