import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import security as _security
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models.user import User, UserRole
from app.services import upcoming_analysis

upcoming_analysis.ENABLED = False  # no audio to download in tests

# Passwords in tests are hashed with the cheapest bcrypt setting. Production's 12
# rounds cost a quarter of a second per hash or login, three times per test: about
# a third of the whole run. Existing hashes of any strength still verify.
from passlib.context import CryptContext

_security.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=4)

_shared_app = None


def _app():
    """One app for the whole run. Building it registers some 340 routes and took
    over a second, once per test: half of the run. Nothing in it is per test except
    the dependency overrides, which the `client` fixture sets and clears. A test
    that needs an app of its own (extra routes, a lifespan) still calls create_app()."""
    global _shared_app
    if _shared_app is None:
        _shared_app = create_app()
    return _shared_app

from app.api.v1 import playlists as _playlists

_playlists.REFRESH_INLINE = True  # tests read the rebuilt queue right after a playlist edit
from app.services import last_played as _last_played  # noqa: E402

_last_played.BACKGROUND_FULL_READ = False  # tests see every play on the first read
# Code that opens the app's own engine (no database behind it in the tests) fails
# at once, as it always did, instead of trying again for 8 s
import app.db.engine as _db_engine  # noqa: E402

_db_engine.CONNECT_RETRY_WAITS = ()

# Use SQLite for testing (no PostgreSQL needed)
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

from app.services import activity_log as _activity_log

_activity_log.INLINE = True  # activity rows are written after each request, through the test session
_activity_log.GEO_LOOKUP = False

# SQLite compatibility: compile PostgreSQL types to SQLite equivalents
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB, ENUM as PG_ENUM


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # Every test creates and drops all tables: some 300 statements, each waiting
    # for the disk to confirm the write. A test database need not survive a crash.
    cursor.execute("PRAGMA synchronous=OFF")
    cursor.execute("PRAGMA journal_mode=MEMORY")
    cursor.close()


# Monkey-patch the PostgreSQL-specific column types for SQLite

_orig_uuid_compile = None
_orig_jsonb_compile = None


def _register_sqlite_compilers():
    """Register SQLite-compatible compilers for PG types."""
    from sqlalchemy.ext.compiler import compiles

    @compiles(PG_UUID, "sqlite")
    def compile_uuid(type_, compiler, **kw):
        return "VARCHAR(36)"

    @compiles(JSONB, "sqlite")
    def compile_jsonb(type_, compiler, **kw):
        return "TEXT"

    @compiles(PG_ENUM, "sqlite")
    def compile_enum(type_, compiler, **kw):
        return "VARCHAR(50)"


_register_sqlite_compilers()


def _accept_string_uuids_on_sqlite():
    """Production (asyncpg) accepts a UUID column value given as a string; SQLite's
    emulated UUID type crashed on it ('str' object has no attribute 'hex'). Many
    schemas type ids as `UUID | str`, so without this the tests failed where the
    site works. Convert a valid string to a UUID first, as the database would."""
    from sqlalchemy.sql import sqltypes

    original = sqltypes.Uuid.bind_processor

    def bind_processor(self, dialect):
        process = original(self, dialect)
        if process is None or dialect.name != "sqlite":
            return process

        def convert(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except ValueError:
                    pass
            return process(value)

        return convert

    sqltypes.Uuid.bind_processor = bind_processor


_accept_string_uuids_on_sqlite()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(autouse=True)
async def reset_rate_limiter():
    """Reset the auth rate limiter between tests to prevent cross-test pollution."""
    try:
        from app.api.v1.auth import limiter
        limiter.reset()
    except Exception:
        pass
    from app.api.v1 import crm as _crm

    _crm._pin_failures.clear()  # wrong CRM PINs are counted per address
    yield
    try:
        from app.api.v1.auth import limiter
        limiter.reset()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def reset_weather_state():
    """Weather answers and announcement-generation pauses are kept per process; start each test clean."""
    from app.services.weather_service import clear_weather_cache
    from app.services.weather_spot_service import resume_generation

    clear_weather_cache()
    resume_generation()
    yield
    clear_weather_cache()
    resume_generation()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    # Import all models
    import app.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    app = _app()

    async def override_get_db():
        yield db_session

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = override_get_db
    limiter = getattr(app.state, "limiter", None)
    if limiter is not None:
        limiter.reset()  # counts are per app; a fresh app used to start them at zero

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def shared_session():
        yield db_session

    _activity_log.session_factory = shared_session
    _activity_log._inline_rows.clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession) -> User:
    user = User(
        id=uuid.uuid4(),
        email="testadmin@test.com",
        hashed_password=hash_password("testpass123"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def admin_token(client: AsyncClient, admin_user: User) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "testadmin@test.com", "password": "testpass123"},
    )
    return response.json()["access_token"]


@pytest_asyncio.fixture
async def auth_headers(admin_token: str) -> dict:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(autouse=True)
def reset_last_played():
    """Each test starts with an empty in-memory last-played map."""
    from app.services import last_played
    last_played.reset()
    yield
    last_played.reset()


@pytest.fixture(autouse=True)
def _forget_recent_panics():
    """Panic ignores a repeat within 1.5 s per station; tests start with none remembered."""
    from app.api.v1 import queue as queue_module

    queue_module._last_panic.clear()
    yield
    queue_module._last_panic.clear()


@pytest.fixture(autouse=True)
def app_log_reaches_caplog(caplog):
    """A test that reads `caplog` sees the app's log lines however it is run.

    The "app" logger does not pass lines up to the root logger (app/main.py), which
    is where caplog listens. Whether a line reached caplog depended on which tests
    had run before: several tests passed in the whole suite and failed when their
    file ran by itself (found 2026-11-18). So caplog's handler also listens on the
    "app" logger while it does not propagate.
    """
    import logging

    app_logger = logging.getLogger("app")
    added = not app_logger.propagate and caplog.handler not in app_logger.handlers
    if added:
        app_logger.addHandler(caplog.handler)
    yield
    if added:
        app_logger.removeHandler(caplog.handler)


@pytest.fixture
def clock_off_the_slots(monkeypatch):
    """Pins "now" to 5 minutes past the last quarter hour (today's date, so date logic is
    unchanged) for tests that count sponsorship slots. Slots sit on quarter hours, and
    one due within the 2-minute safety margin is left alone by design (it may already
    be with the player), so a test run at hh:13 to hh:15 counted one slot too many.
    Patches `datetime` in every loaded app and test module."""
    import sys
    from datetime import UTC, datetime as real

    # The latest hh:05, hh:20, hh:35 or hh:50 not after now: 10 minutes from the nearest slot,
    # and never in the future, so sign-in tokens made at the pinned time (30 minutes) have
    # not expired against the real clock. Pinning to hh:05 failed every test from hh:35 on.
    from datetime import timedelta
    t = real.now(UTC).replace(second=0, microsecond=0)
    pinned = t.replace(minute=(t.minute // 15) * 15 + 5)
    if pinned > t:
        pinned -= timedelta(minutes=15)

    class Pinned(real):
        @classmethod
        def now(cls, tz=None):
            return pinned.astimezone(tz) if tz else pinned.replace(tzinfo=None)

        @classmethod
        def utcnow(cls):
            return pinned.replace(tzinfo=None)

    for name, mod in list(sys.modules.items()):
        if name.split(".")[0] in ("app", "tests") and getattr(mod, "datetime", None) is real:
            monkeypatch.setattr(mod, "datetime", Pinned)
    return pinned
