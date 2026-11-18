import logging
import asyncio
import re
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from app.config import settings
from app.core.middleware import setup_middleware


def _configure_logging() -> None:
    """Send the app's own log lines to stdout with timestamps.

    Nothing configured logging before, so every app logger used Python's
    last-resort handler: INFO lines (track changes, timed starts, retries) were
    dropped, and the scheduler looked silent in the Railway log even while it ran.
    """
    app_logger = logging.getLogger("app")
    # Also keep recent lines in memory so a bug report can carry them.
    from app.core import log_buffer
    log_buffer.install()
    if any(not isinstance(h, log_buffer.RingBufferHandler) for h in app_logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False


_configure_logging()
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
    except Exception:
        # Loud on purpose: a database that could not be set up used to be a
        # one-line warning, and the app then ran with no tables at all.
        logger.exception("DATABASE SETUP FAILED: tables were not created; /health will report 'starting'")


def model_enum_labels() -> dict[str, list[str]]:
    """Every enum type of the models with the labels SQLAlchemy sends (member NAMES)."""
    from sqlalchemy import Enum as SAEnum

    from app.db.base import Base

    labels: dict[str, list[str]] = {}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            kind = column.type
            if isinstance(kind, SAEnum) and kind.name:
                known = labels.setdefault(kind.name, [])
                known += [label for label in kind.enums if label not in known]
    return labels


async def add_missing_enum_labels(raw_conn) -> list[str]:
    """Adds the labels a database's enum types lack (asyncpg connection, autocommit).

    A member added to a Python enum after its type was created is missing from the
    database until this runs: 'DJ' was once added as 'dj', so creating a DJ user
    failed on production only (the tests' SQLite has no enum types). Returns what
    it added, as "type.LABEL".
    """
    added = []
    for name, wanted in sorted(model_enum_labels().items()):
        rows = await raw_conn.fetch(
            "SELECT enumlabel FROM pg_enum WHERE enumtypid = to_regtype($1)::oid", name)
        have = {r["enumlabel"] for r in rows}
        if not have:
            continue  # the type does not exist yet: create_all makes it whole
        for label in wanted:
            if label in have:
                continue
            safe = label.replace("'", "''")
            try:
                await raw_conn.execute(f"ALTER TYPE {name} ADD VALUE IF NOT EXISTS '{safe}'")
                added.append(f"{name}.{label}")
            except Exception as e:
                logger.warning(f"Enum label {name}.{label} not added: {e}")
    logger.info("Enum labels added: %s", ", ".join(added) or "none, every label the models send is there")
    return added


async def _add_missing_columns(engine):
    """Add columns that were added to models but missing from existing DB tables."""

    # ALTER TYPE ADD VALUE cannot run inside a transaction — use autocommit
    enum_migrations = [
        "ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'SPONSOR'",
        # The model stores a role by its NAME. The DJ role was first added as 'dj', which
        # the code never writes, so creating a DJ user failed (production, 2026-11-18).
        "ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'DJ'",
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
            await raw_conn.execute(f"SET lock_timeout = '{MIGRATION_LOCK_TIMEOUT}'")
            for sql in enum_migrations:
                try:
                    await raw_conn.execute(sql)
                except Exception as e:
                    logger.warning(f"Enum migration skipped ({sql[:50]}...): {e}")
            try:
                await add_missing_enum_labels(raw_conn)
            except Exception as e:
                logger.warning(f"Enum labels not checked: {e}")
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
        "ALTER TABLE schedule_blocks ADD COLUMN IF NOT EXISTS until_next BOOLEAN NOT NULL DEFAULT false",
        "ALTER TABLE spot_schedules ADD COLUMN IF NOT EXISTS fixed_times VARCHAR(400)",
        "ALTER TABLE spot_schedules ADD COLUMN IF NOT EXISTS timing_mode VARCHAR(10)",
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
        "ALTER TABLE call_in_requests ADD COLUMN IF NOT EXISTS caller_key VARCHAR(120)",
        "CREATE INDEX IF NOT EXISTS ix_call_in_requests_caller_key ON call_in_requests (caller_key)",
        # Link library assets to sponsors
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS sponsor_id UUID REFERENCES sponsors(id) ON DELETE SET NULL",
        # Release date for music assets (MusicBrainz auto-detection)
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS release_date DATE",
        # Staff vote per song, used by playlist generation's minimum vote
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS vote INTEGER NOT NULL DEFAULT 0",
        # Preempt mechanism for exact-time playback (time announcements)
        "ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS preempt_at TIMESTAMPTZ",
        "ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS timing_mode VARCHAR(10)",
        "ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS hard_by TIMESTAMPTZ",
        # Schedule block an automatic song was drawn for (playlist place on redraw)
        "ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS block_id VARCHAR(36)",
        "ALTER TABLE program_blocks ADD COLUMN IF NOT EXISTS latest_minutes INTEGER",
        # Source tracking: 'request', 'manual', 'auto' (replenish)
        "ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'auto'",
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
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS intro_seconds DOUBLE PRECISION",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_action VARCHAR(255)",
        # Per-station staff permissions and the protected (FM) station
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS station_ids JSONB",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE stations ADD COLUMN IF NOT EXISTS protected BOOLEAN NOT NULL DEFAULT false",
        # A listener who stops leaves the live count at once
        "ALTER TABLE listener_sessions ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ",
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
        # Activity page (wiki/activity-log.md)
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS label VARCHAR(200)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS category VARCHAR(30)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS station_id UUID",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS user_name VARCHAR(255)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS user_role VARCHAR(20)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS method VARCHAR(10)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS path VARCHAR(300)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS status_code INTEGER",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS user_agent VARCHAR(400)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS device VARCHAR(80)",
        "ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS location VARCHAR(160)",
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_station_created ON audit_logs (station_id, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_category_created ON audit_logs (category, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_audit_logs_user_created ON audit_logs (user_id, created_at)",
        # CRM indexes
        "CREATE INDEX IF NOT EXISTS ix_song_ratings_member ON song_ratings (member_id)",
        "CREATE INDEX IF NOT EXISTS ix_song_ratings_asset ON song_ratings (asset_id)",
        "CREATE INDEX IF NOT EXISTS ix_raffle_entries_raffle ON raffle_entries (raffle_id)",
        "CREATE INDEX IF NOT EXISTS ix_raffle_entries_member ON raffle_entries (member_id)",
        # Queue + play log hot paths (queue page, scheduler tick, skip, add, history).
        # Without these every lookup scans all played history for all stations.
        "CREATE INDEX IF NOT EXISTS ix_queue_entries_station_status_position ON queue_entries (station_id, status, position)",
        "CREATE INDEX IF NOT EXISTS ix_play_logs_station_start ON play_logs (station_id, start_utc)",
        # Last played per item (control room library): answered from the index alone
        "CREATE INDEX IF NOT EXISTS ix_play_logs_station_asset_start ON play_logs (station_id, asset_id, start_utc)",
        # Two bug reports sent at once got the same number (max+1); the second now retries
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_bug_reports_number ON bug_reports (number)",
    ]
    await _run_migrations(engine, migrations)


# A schema change needs an exclusive lock on its table, and Postgres takes it
# even for "ADD COLUMN IF NOT EXISTS" on a column that is already there (and a
# share lock for "CREATE INDEX IF NOT EXISTS" on an index that exists). Waiting
# for that lock behind a long transaction froze the live backend for 4 minutes
# during a deploy. So statements whose column or index already exists are not
# sent at all, and the rest give up after MIGRATION_LOCK_TIMEOUT instead of
# queueing (see wiki/streaming-architecture.md, "Deploys must never freeze").
MIGRATION_LOCK_TIMEOUT = "1s"

# A restart closes the play log rows it left open; only rows this recent are looked at.
STARTUP_OPEN_LOG_DAYS = 2

_ADD_COLUMN_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\w.]+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(\w+)",
    re.IGNORECASE,
)
_CREATE_TABLE_RE = re.compile(
    r"^\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([\w.]+)", re.IGNORECASE,
)
_CREATE_INDEX_RE = re.compile(
    r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?IF\s+NOT\s+EXISTS\s+(\w+)",
    re.IGNORECASE,
)


def migrations_to_run(
    statements: list[str],
    existing_columns: set[tuple[str, str]],
    existing_indexes: set[str],
) -> list[str]:
    """Drop the statements whose column or index already exists."""
    todo = []
    for sql in statements:
        m = _ADD_COLUMN_RE.match(sql)
        if m:
            table = m.group(1).split(".")[-1].lower()
            if (table, m.group(2).lower()) in existing_columns:
                continue
        m = _CREATE_INDEX_RE.match(sql)
        if m and m.group(1).lower() in existing_indexes:
            continue
        m = _CREATE_TABLE_RE.match(sql)
        if m and m.group(1).split(".")[-1].lower() in {t for t, _ in existing_columns}:
            continue
        todo.append(sql)
    return todo


async def _run_migrations(engine, statements: list[str]) -> None:
    from sqlalchemy import text

    is_pg = engine.dialect.name == "postgresql"
    existing_columns: set[tuple[str, str]] = set()
    existing_indexes: set[str] = set()
    if is_pg:
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema()"
                ))
                existing_columns = {(t.lower(), c.lower()) for t, c in rows.fetchall()}
                rows = await conn.execute(text(
                    "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
                ))
                existing_indexes = {r[0].lower() for r in rows.fetchall()}
        except Exception as e:
            logger.warning("Could not read the schema, running every migration: %s", e)

    todo = migrations_to_run(statements, existing_columns, existing_indexes)
    logger.info("Migrations: %d of %d need running", len(todo), len(statements))
    failed = await _execute_migrations(engine, todo, is_pg)
    if failed and is_pg:
        # A busy table made these give up; try again in the background rather than
        # wait now (a new column the code needs would otherwise stay missing)
        task = asyncio.create_task(_retry_migrations(engine, failed))
        _migration_retry_tasks.add(task)
        task.add_done_callback(_migration_retry_tasks.discard)


_migration_retry_tasks: set = set()


async def _execute_migrations(engine, statements: list[str], is_pg: bool) -> list[str]:
    from sqlalchemy import text

    failed = []
    for sql in statements:
        try:
            async with engine.begin() as conn:
                if is_pg:
                    await conn.execute(text(f"SET LOCAL lock_timeout = '{MIGRATION_LOCK_TIMEOUT}'"))
                await conn.execute(text(sql))
        except Exception as e:
            logger.warning(f"Migration skipped ({sql[:50]}...): {e}")
            failed.append(sql)
    return failed


async def _retry_migrations(engine, statements: list[str], attempts: int = 40, every: float = 30.0) -> None:
    for _ in range(attempts):
        await asyncio.sleep(every)
        statements = await _execute_migrations(engine, statements, True)
        if not statements:
            logger.info("Migrations that had to wait have now run")
            return
    logger.error("MIGRATIONS STILL NOT RUN after %d tries: %s", attempts, [s[:60] for s in statements])



async def _warm_connection_pool(timeout: float = 20.0) -> None:
    """Open the pool's connections before the first request does.

    A new connection costs about 2 s (connect, then asyncpg looking up the JSON,
    UUID and enum types on first use). Without this the first burst of requests
    after a deploy opened them one by one and waited 4 to 5 s. Runs before the app
    starts serving, so the health check (and with it the switch-over) waits for it.
    """
    from sqlalchemy import text

    from app.db.engine import engine

    if engine.dialect.name != "postgresql":
        return
    size = getattr(engine.pool, "size", lambda: 5)()

    async def warm_one() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            # Touch the column types the app uses so their codecs are set up now
            await conn.execute(text("SELECT id, metadata_extra FROM assets LIMIT 1"))
            await conn.execute(text("SELECT role FROM users LIMIT 1"))
            # Hold this connection until the others are open, so each is a new one
            await asyncio.sleep(0.2)

    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(warm_one() for _ in range(size)), return_exceptions=True), timeout
        )
        failed = [r for r in results if isinstance(r, Exception)]
        if failed:
            logger.warning("Warming %d database connection(s) failed: %s", len(failed), failed[0])
        logger.info("Database pool warmed: %d connections in %.1f s",
                    size - len(failed), loop.time() - started)
    except Exception as e:
        logger.warning("Database pool warm-up skipped: %s", e)


# Indexes on big, busy tables. A plain CREATE INDEX blocks writes to the table for
# the whole build, so these are built CONCURRENTLY (reads and writes carry on) in
# the background after startup, outside any transaction.
CONCURRENT_INDEXES = {
    # The library's "last played" per item looks up plays by item across stations
    "ix_play_logs_asset_start": "play_logs (asset_id, start_utc)",
    # The on-air self-check (air_watch) reads, every 5 s per station, what started or
    # came due in the last 10 minutes. Without these it read the station's whole queue
    # history each time (2.4 to 3.9 s on production, 2026-11-18).
    "ix_queue_entries_station_started": "queue_entries (station_id, started_at)",
    "ix_queue_entries_station_preempt": "queue_entries (station_id, preempt_at)",
    # queue_entries keeps every entry ever played (about 255k rows) and only about
    # 1,850 wait. The next due timed item (GET /now-playing, the timed-groups check)
    # and the row on air walked that history: 0.94 s, 0.95 s and 3.6 s on production,
    # 2026-11-18. These hold only the waiting / playing rows: 0.01 to 0.06 ms locally
    # on 250k history rows (a full read without them).
    "ix_qe_pending_preempt": "queue_entries (station_id, preempt_at, position) WHERE status = 'pending'",
    "ix_qe_playing_started": "queue_entries (station_id, started_at) WHERE status = 'playing'",
    # Deleting a library item makes Postgres look in every table that points at it
    # (cascade / set null). Without these each delete read the whole of queue_entries
    # (it keeps every entry ever played) and the other tables, row by row.
    "ix_queue_entries_asset": "queue_entries (asset_id)",
    "ix_engine_handoffs_asset": "engine_handoffs (asset_id)",
    "ix_review_items_asset": "review_items (asset_id)",
    "ix_review_actions_asset": "review_actions (asset_id)",
    "ix_song_requests_asset": "song_requests (asset_id)",
    "ix_spot_schedules_asset": "spot_schedules (asset_id)",
    "ix_spot_recordings_asset": "spot_recordings (asset_id)",
    "ix_weather_readouts_asset": "weather_readouts (asset_id)",
}


INDEX_BUILD_TRIES = 5
INDEX_BUILD_WAIT = 30.0  # seconds between tries


async def _build_indexes_once(conn, names) -> list[str]:
    """Builds the named indexes that are missing or unusable. Returns those still missing."""
    missing = []
    for name in names:
        target = CONCURRENT_INDEXES[name]
        try:
            valid = await conn.fetchval(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = $1", name,
            )
            if valid:
                continue
            if valid is False:  # a build that was interrupted leaves an unusable index
                await conn.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
            started = asyncio.get_running_loop().time()
            await conn.execute(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON {target}")
            logger.info("Built index %s in %.1f s", name, asyncio.get_running_loop().time() - started)
        except Exception as e:
            logger.warning("Index %s not built: %s", name, e)
            missing.append(name)
    return missing


async def _build_concurrent_indexes(dsn: str | None = None, wait: float = INDEX_BUILD_WAIT) -> list[str]:
    """Builds CONCURRENT_INDEXES in the background; a failed connection or build is
    tried again, and the outcome is logged once either way (so the log of any start
    says which indexes exist). Returns the names still missing."""
    import asyncpg

    if dsn is None:
        if not settings.DATABASE_URL.startswith("postgresql"):
            return []
        dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    missing = list(CONCURRENT_INDEXES)
    for attempt in range(1, INDEX_BUILD_TRIES + 1):
        try:
            conn = await asyncpg.connect(dsn, statement_cache_size=0)
        except Exception as e:
            logger.warning("Could not connect to build indexes (try %d of %d): %s", attempt, INDEX_BUILD_TRIES, e)
        else:
            try:
                await conn.execute("SET statement_timeout = '10min'")
                missing = await _build_indexes_once(conn, missing)
            except Exception as e:
                logger.warning("Index build stopped (try %d of %d): %s", attempt, INDEX_BUILD_TRIES, e)
            finally:
                await conn.close()
            if not missing:
                break
        if attempt < INDEX_BUILD_TRIES:
            await asyncio.sleep(wait)
    if missing:
        logger.error("INDEXES MISSING after %d tries: %s", INDEX_BUILD_TRIES, ", ".join(missing))
    else:
        logger.info("Indexes in place: %s", ", ".join(CONCURRENT_INDEXES))
    return missing


async def _wait_for_database(limit: float = 90.0) -> None:
    """Nothing starts until the database answers. A process that came up while
    the database refused connections ran its whole startup against nothing and
    then served 500s."""
    from sqlalchemy import text

    from app.db.engine import engine

    loop = asyncio.get_running_loop()
    started = loop.time()
    wait = 0.5
    while True:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            if loop.time() - started > 1.0:
                logger.info("Database answered after %.1f s", loop.time() - started)
            return
        except Exception as e:
            if loop.time() - started > limit:
                logger.error("Database did not answer within %.0f s (%s); starting anyway", limit, e)
                return
            logger.warning("Database not answering yet (%s); trying again in %.1f s", e, wait)
            await asyncio.sleep(wait)
            wait = min(wait * 2, 8.0)


async def _log_database_link() -> None:
    """Once after a start: how far away the database is and how big it is.

    On 2026-11-18 single statements took 0.6 to 1 s with an idle event loop and a
    free pool, and a 48 MB table took 27 s to vacuum. Whether that is distance
    (this server's region against the database's) or a small database that reads
    from disk cannot be seen from outside; this line says both."""
    import os
    import statistics
    import time as _time

    from sqlalchemy import text
    from sqlalchemy.engine import make_url

    from app.db.engine import engine

    if engine.dialect.name != "postgresql":
        return
    await asyncio.sleep(45)  # after the startup rush, so the times are the link's own
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            times = []
            for _ in range(7):
                began = _time.monotonic()
                await conn.execute(text("SELECT 1"))
                times.append((_time.monotonic() - began) * 1000)
            facts = (await conn.execute(text(
                "SELECT current_setting('shared_buffers'), current_setting('effective_cache_size'), "
                "current_setting('max_connections'), pg_size_pretty(pg_database_size(current_database())), "
                "current_setting('server_version'), current_setting('track_io_timing')"
            ))).first()
        logger.warning(
            "DATABASE LINK: one statement (SELECT 1) takes %.0f ms at best, %.0f ms typically, %.0f ms at worst. "
            "This server: region %s. Database host: %s. Database: version %s, shared_buffers %s, "
            "effective_cache_size %s, max_connections %s, size %s, track_io_timing %s",
            min(times), statistics.median(times), max(times),
            os.environ.get("RAILWAY_REPLICA_REGION") or os.environ.get("RAILWAY_REGION") or "unknown",
            make_url(settings.DATABASE_URL).host,
            facts[4], facts[0], facts[1], facts[2], facts[3], facts[5],
        )
    except Exception as e:
        logger.warning("Database link facts not read: %s", e)
    await _log_first_use_cost()


async def _log_first_use_cost() -> None:
    """What the first use of an enum column costs on a new connection (asyncpg
    looks the type up once per connection), against the same statement again,
    and the database's JIT setting. Connections are kept 30 minutes so this is
    paid rarely; this line says how much it is."""
    import time as _time

    import asyncpg

    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    try:
        began = _time.monotonic()
        conn = await asyncpg.connect(dsn, statement_cache_size=0)
        try:
            connected = (_time.monotonic() - began) * 1000
            began = _time.monotonic()
            await conn.fetch("SELECT role FROM users LIMIT 1")
            first = (_time.monotonic() - began) * 1000
            began = _time.monotonic()
            await conn.fetch("SELECT role FROM users LIMIT 1")
            second = (_time.monotonic() - began) * 1000
            jit = await conn.fetchval("SHOW jit")
        finally:
            await conn.close()
        logger.warning(
            "FIRST USE OF A TYPE on a new connection (SELECT role FROM users LIMIT 1): connecting %.0f ms, "
            "first use %.0f ms, second %.0f ms, database jit %s",
            connected, first, second, jit,
        )
    except Exception as e:
        logger.warning("First-use cost not measured: %s", type(e).__name__)


# Tables read by index-only scans. The database skips the table itself only for
# pages its visibility map marks clean, and only VACUUM marks them: on
# 2026-11-18 the last-played read of play_logs fetched 139,992 of 145,557 rows
# from the table anyway. Inserts alone start autovacuum late (20% of the table).
VACUUM_TABLES = ("play_logs", "queue_entries", "assets", "alerts")
VACUUM_EVERY_DAYS = 7
# A table with this share of dead rows is vacuumed whatever its last vacuum.
# queue_entries has 700 to 900 rows rewritten at every song change.
VACUUM_DEAD_SHARE = 0.10


async def _vacuum_stale_tables() -> None:
    """After startup, in the background: VACUUM (ANALYZE) each of VACUUM_TABLES
    that was not vacuumed in the last week or holds many dead rows, and make
    autovacuum come sooner for them. VACUUM does not block reads or writes.
    Every table gets a log line either way, with its sizes and dead rows:
    nobody can look at the live database directly."""
    import asyncpg

    if not settings.DATABASE_URL.startswith("postgresql"):
        return
    await asyncio.sleep(120)  # after the startup rush
    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    try:
        conn = await asyncpg.connect(dsn, statement_cache_size=0)
    except Exception as e:
        logger.warning("Could not connect to vacuum: %s", e)
        return
    try:
        await conn.execute("SET statement_timeout = '10min'")
        for table in VACUUM_TABLES:
            try:
                row = await conn.fetchrow(
                    "SELECT greatest(last_vacuum, last_autovacuum) AS last, "
                    "coalesce(now() - greatest(last_vacuum, last_autovacuum) > make_interval(days => $2), true) AS old, "
                    "n_live_tup AS live, n_dead_tup AS dead, vacuum_count + autovacuum_count AS runs, "
                    "pg_size_pretty(pg_table_size(relid)) AS size, pg_size_pretty(pg_indexes_size(relid)) AS indexes "
                    "FROM pg_stat_user_tables WHERE relname = $1", table, VACUUM_EVERY_DAYS,
                )
                if row is None:
                    continue
                state = (
                    f"{row['live']} live and {row['dead']} dead rows, table {row['size']}, indexes {row['indexes']}, "
                    f"last vacuum {row['last'] or 'never'}, {row['runs']} vacuums so far"
                )
                many_dead = row["dead"] > 1000 and row["dead"] > VACUUM_DEAD_SHARE * max(row["live"], 1)
                if not row["old"] and not many_dead:
                    logger.info("Vacuum not needed for %s: %s", table, state)
                    continue
                await conn.execute(
                    f"ALTER TABLE {table} SET (autovacuum_vacuum_insert_scale_factor = 0.02, "
                    "autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.02)"
                )
                started = asyncio.get_running_loop().time()
                await conn.execute(f"VACUUM (ANALYZE) {table}")
                after = await conn.fetchrow(
                    "SELECT pg_size_pretty(pg_table_size(relid)) AS size, "
                    "pg_size_pretty(pg_indexes_size(relid)) AS indexes "
                    "FROM pg_stat_user_tables WHERE relname = $1", table,
                )
                logger.info(
                    "Vacuumed %s in %.1f s. Before: %s. After: table %s, indexes %s",
                    table, asyncio.get_running_loop().time() - started, state, after["size"], after["indexes"],
                )
            except Exception as e:
                logger.warning("Vacuum of %s failed: %s", table, e)
    finally:
        await conn.close()


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
                        text("INSERT INTO categories (id, name, fade_allowed, created_at, updated_at) VALUES (gen_random_uuid(), :name, true, NOW(), NOW())"),
                        {"name": name},
                    )
                logger.info("Seeded %d default categories", len(defaults))
    except Exception as e:
        logger.warning("Category seeding skipped: %s", e)


async def _seed_default_asset_types():
    """Insert default asset types and any types already used in the assets table."""
    try:
        from app.db.engine import engine
        from sqlalchemy import text

        defaults = ["music", "shiur", "spot", "jingle", "zmanim"]
        async with engine.begin() as conn:
            # Gather types already in asset_types table
            existing = await conn.execute(text("SELECT name FROM asset_types"))
            existing_names = {row[0] for row in existing.fetchall()}

            # Also discover types actually used in assets table
            try:
                used = await conn.execute(text(
                    "SELECT DISTINCT asset_type FROM assets WHERE asset_type IS NOT NULL AND asset_type != ''"
                ))
                used_names = {row[0] for row in used.fetchall()}
            except Exception:
                used_names = set()

            # Merge: defaults + used types - already existing
            to_insert = (set(defaults) | used_names) - existing_names
            for name in sorted(to_insert):
                await conn.execute(
                    text("INSERT INTO asset_types (id, name, created_at, updated_at) VALUES (gen_random_uuid(), :name, NOW(), NOW())"),
                    {"name": name},
                )
            if to_insert:
                logger.info("Seeded %d asset types: %s", len(to_insert), sorted(to_insert))
    except Exception as e:
        logger.warning("Asset type seeding skipped: %s", e)


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

    from sqlalchemy import select

    from app.db.engine import async_session_factory
    from app.models.play_log import PlayLog
    from app.models.queue_entry import QueueEntry, aired_from
    from app.models.station import Station

    now = datetime.now(timezone.utc)

    try:
        async with async_session_factory() as db:
            # A restart is not a reason to cut a song. Find what is still on air:
            # entries marked playing whose scheduled end is still ahead of us.
            from datetime import timedelta
            from app.models.asset import Asset

            playing = (await db.execute(
                select(QueueEntry, Asset.duration)
                .outerjoin(Asset, QueueEntry.asset_id == Asset.id)
                .where(QueueEntry.status == "playing")
            )).all()
            on_air: dict = {}  # station_id -> asset_id still playing
            finished = []
            for entry, duration in playing:
                started = entry.started_at
                if started is not None and started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                # An item with no recorded length is timed like the queue times it (3 min)
                length = float(duration or 180.0)
                still_playing = (
                    started is not None
                    and started + timedelta(seconds=length) > now + timedelta(seconds=2)
                    and entry.station_id not in on_air
                )
                if still_playing:
                    on_air[entry.station_id] = entry.asset_id
                else:
                    finished.append(entry)

            # 1. Close open PlayLog entries, except the ones for songs still on air
            # Only recent rows, station by station, so the (station_id, start_utc)
            # index answers it: the open rows a restart leaves are minutes old,
            # and an unbounded "end_utc IS NULL" read all of play history (6.5 s
            # per container at a deploy, slowing everything else).
            station_ids = (await db.execute(select(Station.id))).scalars().all()
            stale_logs = await db.execute(
                select(PlayLog).where(
                    PlayLog.station_id.in_(station_ids),
                    PlayLog.start_utc >= now - timedelta(days=STARTUP_OPEN_LOG_DAYS),
                    PlayLog.end_utc.is_(None),
                )
            ) if station_ids else None
            stale_count = 0
            for log in (stale_logs.scalars().all() if stale_logs is not None else []):
                if on_air.get(log.station_id) == log.asset_id:
                    continue
                log.end_utc = now
                stale_count += 1
            if stale_count:
                logger.info("Closed %d stale play log entries from before restart", stale_count)

            # 2. Entries that ended while the server was down: mark played and log them
            stale_playing_count = 0
            for entry in finished:
                # The playout engine keeps playing through a backend restart; it reports
                # what really aired, so its station's entries are left for it to settle.
                if settings.engine_drives(entry.station_id):
                    continue
                play_log = PlayLog(
                    station_id=entry.station_id,
                    asset_id=entry.asset_id,
                    start_utc=aired_from(entry) or now,
                    end_utc=now,
                    source="scheduler",
                )
                db.add(play_log)
                entry.status = "played"
                stale_playing_count += 1
            if stale_playing_count:
                logger.info("Closed %d stale playing queue entries", stale_playing_count)
            if on_air:
                logger.info("Restart left %d station(s) playing their current song", len(on_air))

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

                    if station.id in on_air:
                        continue  # still playing; the scheduler carries on from here
                    if settings.engine_drives(station.id):
                        continue  # the engine asks for its next track itself

                    # 4b. Start playback the same way Start does: the next item by the
                    # queue's own rule (an item timed for later stays where it is), and
                    # nothing if another process already started one.
                    from app.api.v1.queue import start_from_queue
                    next_entry = await start_from_queue(db, station.id)
                    if next_entry:
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
                            # create_alert only flushes: without this the alert was lost
                            # whenever nothing else committed after it (last station)
                            await db.commit()
                        except Exception:
                            logger.warning("Could not record the empty-queue alert", exc_info=True)
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
    from app.core import loop_watch
    loop_watch.start()  # logs what holds the event loop when requests stall
    # Startup — create tables and start the scheduler engine
    await _wait_for_database()
    await ensure_tables()
    await _warm_connection_pool()
    await _seed_default_categories()
    await _seed_default_asset_types()
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

    # Items with no length break timing; measure them in the background
    import asyncio as _asyncio
    from app.services.duration_backfill import backfill_on_startup
    backfill_task = _asyncio.get_running_loop().create_task(backfill_on_startup())
    index_task = _asyncio.get_running_loop().create_task(_build_concurrent_indexes())
    # When each item last aired: read before the first refill or redraw needs it
    from app.services.last_played import warm_all
    warm_task = _asyncio.get_running_loop().create_task(warm_all())
    vacuum_task = _asyncio.get_running_loop().create_task(_vacuum_stale_tables())
    link_task = _asyncio.get_running_loop().create_task(_log_database_link())
    # Bug reports: push "fix ready" to ntfy (see wiki/bug-reports.md)
    from app.services.bug_report_service import notify_loop
    bug_task = _asyncio.get_running_loop().create_task(notify_loop())
    # On-air self-check: late time checks, dead air, drift (see wiki/air-watch.md)
    from app.services.air_watch import watch_loop
    watch_task = _asyncio.get_running_loop().create_task(watch_loop())

    yield

    bug_task.cancel()
    watch_task.cancel()

    backfill_task.cancel()
    index_task.cancel()
    warm_task.cancel()
    vacuum_task.cancel()
    link_task.cancel()
    loop_watch.stop()
    # Shutdown — stop the scheduler cleanly
    try:
        from app.services.scheduler_engine import stop_scheduler
        await stop_scheduler()
        logger.info("Scheduler engine stopped")
    except Exception as e:
        logger.warning(f"Scheduler engine failed to stop: {e}")
    from app.services.playout_engine import close_client
    await close_client()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Studio Kol B'ramah API",
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
        return {"status": "ok", "version": "2026-02-22a"}

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
