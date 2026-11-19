"""Checklist rows on startup jobs, /health and the app's middleware:
V1-111, V1-113, B-JOB-90, 91, 92, 100, 101, 114, 115.

No real database or Postgres here: the startup steps are run against the SQLite
test database (with gen_random_uuid()/NOW() added) or against stand-ins."""
import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import app.db.engine as db_engine
import app.main as main
from app.db.base import Base


# ---------------------------------------------------------------- /health (V1-111)

@pytest.mark.asyncio
async def test_health_is_503_starting_until_tables_exist(client: AsyncClient, monkeypatch):
    """V1-111: Railway keeps the old instance while the new one answers 503."""
    async def no_tables():
        return None

    monkeypatch.setattr(main, "_tables_created", False)
    monkeypatch.setattr(main, "ensure_tables", no_tables)  # the fallback middleware calls it too
    r = await client.get("/health")
    assert r.status_code == 503
    assert r.json()["status"] == "starting"


@pytest.mark.asyncio
async def test_health_is_503_unhealthy_when_the_database_does_not_answer(client: AsyncClient, monkeypatch, tmp_path):
    """V1-111: tables made, but the database cannot be reached -> 503 unhealthy."""
    broken = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/no/such/dir/x.db")
    monkeypatch.setattr(main, "_tables_created", True)
    monkeypatch.setattr(db_engine, "engine", broken)
    r = await client.get("/health")
    await broken.dispose()
    assert r.status_code == 503
    assert r.json()["status"] == "unhealthy"
    assert "DB unreachable" in r.json()["detail"]


@pytest.mark.asyncio
async def test_health_is_200_when_tables_exist_and_the_database_answers(client: AsyncClient, monkeypatch, tmp_path):
    ok = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/ok.db")
    monkeypatch.setattr(main, "_tables_created", True)
    monkeypatch.setattr(db_engine, "engine", ok)
    r = await client.get("/health")
    await ok.dispose()
    assert r.status_code == 200 and r.json()["status"] == "ok"


# ------------------------------------------------- fallback middleware (B-JOB-101)

@pytest.mark.asyncio
async def test_fallback_middleware_creates_tables_on_requests_without_lifespan(client: AsyncClient, monkeypatch):
    """B-JOB-101: on Vercel no lifespan runs; every request first calls ensure_tables
    (which returns at once once the tables are made)."""
    calls = []

    async def ensure():
        calls.append(1)

    monkeypatch.setattr(main, "ensure_tables", ensure)
    await client.get("/api/v1/stations")
    await client.get("/api/v1/stations")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_ensure_tables_creates_the_tables_once(monkeypatch, tmp_path):
    """B-JOB-101: ensure_tables makes every table on an empty database, then is a no-op."""
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fresh.db")
    added = []

    async def no_columns(engine):
        added.append(engine)

    monkeypatch.setattr(db_engine, "engine", eng)
    monkeypatch.setattr(main, "_add_missing_columns", no_columns)
    monkeypatch.setattr(main, "_tables_created", False)
    await main.ensure_tables()
    assert main._tables_created is True
    async with eng.connect() as conn:
        names = await conn.run_sync(lambda c: set(__import__("sqlalchemy").inspect(c).get_table_names()))
    assert {"stations", "assets", "queue_entries", "users"} <= names
    await main.ensure_tables()
    assert len(added) == 1  # second call did nothing
    await eng.dispose()


# --------------------------------------------------- lifespan order (B-JOB-90/92/100, V1-113)

def _stub_lifespan(monkeypatch, order: list, *, resume=None, scheduler=None, index_gate=None):
    """Replace every startup step with a recorder; returns nothing. The steps that
    must happen before serving are recorded in `order`."""
    from app.core import loop_watch
    from app.services import (
        air_watch,
        bug_report_service,
        duration_backfill,
        last_played,
        playout_engine,
        scheduler_engine,
        upcoming_analysis,
    )

    def rec(name, fail=None):
        async def step(*a, **k):
            order.append(name)
            if fail is not None:
                raise fail
        return step

    async def forever(*a, **k):
        await asyncio.Event().wait()

    async def slow_index_build(*a, **k):
        order.append("index build started")
        await (index_gate or asyncio.Event()).wait()
        order.append("index build finished")

    monkeypatch.setattr(loop_watch, "start", lambda: None)
    monkeypatch.setattr(loop_watch, "stop", lambda: None)
    monkeypatch.setattr(main, "_wait_for_database", rec("wait for database"))
    monkeypatch.setattr(main, "ensure_tables", rec("tables"))
    monkeypatch.setattr(main, "_warm_connection_pool", rec("warm pool"))
    monkeypatch.setattr(main, "_seed_default_categories", rec("seed categories"))
    monkeypatch.setattr(main, "_seed_default_asset_types", rec("seed asset types"))
    monkeypatch.setattr(main, "_refresh_requested_category", rec("refresh requested"))
    monkeypatch.setattr(main, "_resume_playback_on_startup", rec("resume", resume))
    monkeypatch.setattr(scheduler_engine, "start_scheduler", rec("scheduler", scheduler))
    monkeypatch.setattr(scheduler_engine, "stop_scheduler", rec("stop scheduler"))
    monkeypatch.setattr(main, "_build_concurrent_indexes", slow_index_build)
    for mod, name in [(duration_backfill, "backfill_on_startup"), (upcoming_analysis, "restore_on_startup"),
                      (last_played, "warm_all"), (bug_report_service, "notify_loop"),
                      (air_watch, "watch_loop")]:
        monkeypatch.setattr(mod, name, forever)
    monkeypatch.setattr(main, "_vacuum_stale_tables", forever)
    monkeypatch.setattr(main, "_log_database_link", forever)
    monkeypatch.setattr(playout_engine, "close_client", rec("close client"))


@pytest.mark.asyncio
async def test_startup_warms_pool_seeds_and_refreshes_before_serving(monkeypatch):
    """B-JOB-90 (pool warmed before serving), B-JOB-92 (requested refreshed at
    startup), B-JOB-100 (index build runs in the background: serving starts while it
    is still going)."""
    order: list = []
    _stub_lifespan(monkeypatch, order)
    app = main.create_app()
    async with asyncio.timeout(5):
        async with main.lifespan(app):
            await asyncio.sleep(0)  # let background tasks begin
            serving_at = list(order)
    before = serving_at[: serving_at.index("scheduler") + 1]
    assert before == ["wait for database", "tables", "warm pool", "seed categories", "seed asset types",
                      "refresh requested", "resume", "scheduler"]
    assert "index build started" in serving_at and "index build finished" not in serving_at


@pytest.mark.asyncio
async def test_api_serves_when_resume_and_scheduler_fail_at_startup(monkeypatch):
    """V1-113 (first half): a failing resume or scheduler start is logged, the API still serves."""
    order: list = []
    _stub_lifespan(monkeypatch, order, resume=RuntimeError("resume broke"), scheduler=RuntimeError("no scheduler"))
    app = main.create_app()
    async with asyncio.timeout(5):
        async with main.lifespan(app):
            served = True
    assert served and "resume" in order and "scheduler" in order


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="found 2026-11-19: scheduler failing to start at boot is only a log warning; no alert is raised, so stations silently never advance")
async def test_scheduler_failing_at_startup_raises_an_alert(monkeypatch, db_session: AsyncSession):
    """V1-113 (second half): staff must hear about it."""
    from app.models.alert import Alert

    order: list = []
    _stub_lifespan(monkeypatch, order, scheduler=RuntimeError("no scheduler"))
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(db_engine, "async_session_factory", TestSessionLocal)
    app = main.create_app()
    async with asyncio.timeout(5):
        async with main.lifespan(app):
            pass
    count = (await db_session.execute(select(func.count(Alert.id)))).scalar()
    assert count >= 1


# ------------------------------------------------------- pool warm-up (B-JOB-90)

class _FakePool:
    def __init__(self, n):
        self.n = n

    def size(self):
        return self.n


class _FakeEngine:
    def __init__(self, n=3, hang=False):
        self.dialect = type("D", (), {"name": "postgresql"})()
        self.pool = _FakePool(n)
        self.hang = hang
        self.opened = 0
        self.open_now = 0
        self.most_open = 0

    @asynccontextmanager
    async def connect(self):
        self.opened += 1
        self.open_now += 1
        self.most_open = max(self.most_open, self.open_now)
        try:
            if self.hang:
                await asyncio.Event().wait()

            class Conn:
                async def execute(self, *a, **k):
                    return None
            yield Conn()
        finally:
            self.open_now -= 1


@pytest.mark.asyncio
async def test_pool_warm_up_opens_every_connection_at_once(monkeypatch):
    """B-JOB-90: one new connection per pool slot, held together so each is a new one."""
    fake = _FakeEngine(4)
    monkeypatch.setattr(db_engine, "engine", fake)
    await main._warm_connection_pool()
    assert fake.opened == 4 and fake.most_open == 4


@pytest.mark.asyncio
async def test_pool_warm_up_gives_up_at_its_timeout(monkeypatch):
    """B-JOB-90: a database that never answers does not hold startup past the timeout (20 s default)."""
    import inspect

    assert inspect.signature(main._warm_connection_pool).parameters["timeout"].default == 20.0
    fake = _FakeEngine(2, hang=True)
    monkeypatch.setattr(db_engine, "engine", fake)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await main._warm_connection_pool(timeout=0.2)  # no exception
    assert loop.time() - t0 < 1.0


# -------------------------------------------------------- seeding (B-JOB-91)

@pytest.fixture
async def pg_like_engine(tmp_path, monkeypatch):
    """A SQLite database with the Postgres functions the seed SQL uses."""
    import app.models  # noqa: F401

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/seed.db")

    @event.listens_for(eng.sync_engine, "connect")
    def _fns(dbapi_conn, _):
        dbapi_conn.create_function("gen_random_uuid", 0, lambda: uuid.uuid4().hex)
        dbapi_conn.create_function("NOW", 0, lambda: "2026-11-19 00:00:00")

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(db_engine, "engine", eng)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_default_categories_and_asset_types_seeded_once(pg_like_engine):
    """B-JOB-91: running the seeds twice leaves one of each default, and an asset
    type already used in the library is added too."""
    from sqlalchemy import text

    async with pg_like_engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO assets (id, title, file_path, asset_type, created_at, updated_at) "
            "VALUES (:id, 'x', 'x.mp3', 'story', '2026-11-19', '2026-11-19')"), {"id": uuid.uuid4().hex})
    for _ in range(2):
        await main._seed_default_categories()
        await main._seed_default_asset_types()
    async with pg_like_engine.connect() as conn:
        cats = [r[0] for r in (await conn.execute(text("SELECT name FROM categories"))).all()]
        types = [r[0] for r in (await conn.execute(text("SELECT name FROM asset_types"))).all()]
    assert sorted(cats) == sorted(["lively", "med_fast", "relax", "do_not_play", "requested"])
    assert sorted(types) == sorted(["music", "shiur", "spot", "jingle", "zmanim", "story"])


@pytest.mark.asyncio
async def test_category_seed_leaves_a_staff_made_list_alone(pg_like_engine):
    from sqlalchemy import text

    async with pg_like_engine.begin() as conn:
        await conn.execute(text("INSERT INTO categories (id, name, fade_allowed, created_at, updated_at) "
                                "VALUES (:id, 'mine', 1, '2026-11-19', '2026-11-19')"), {"id": uuid.uuid4().hex})
    await main._seed_default_categories()
    async with pg_like_engine.connect() as conn:
        cats = [r[0] for r in (await conn.execute(text("SELECT name FROM categories"))).all()]
    assert cats == ["mine"]


# ------------------------------------------- requested category (B-JOB-92, B-JOB-114)

async def _popular_song(db: AsyncSession, requests: int = 3):
    from app.models.asset import Asset
    from app.models.song_request import SongRequest
    from app.models.station import Station

    st = Station(name="Req FM", timezone="UTC")
    song = Asset(title="Asked For", file_path="a.mp3", asset_type="music", category="lively")
    db.add_all([st, song])
    await db.flush()
    for i in range(requests):
        db.add(SongRequest(station_id=st.id, requester_name=f"r{i}", song_title="Asked For", asset_id=song.id))
    await db.commit()
    return song


@pytest.mark.asyncio
async def test_startup_refresh_tags_requested_songs(db_session: AsyncSession, monkeypatch):
    """B-JOB-92: the startup step runs the refresh in its own session and commits."""
    from app.models.asset import REQUESTED_TAG, REQUESTED_TAG_VALUE, Asset
    from tests.conftest import TestSessionLocal

    song = await _popular_song(db_session)
    monkeypatch.setattr(db_engine, "async_session_factory", TestSessionLocal)
    await main._refresh_requested_category()
    async with TestSessionLocal() as fresh:
        got = await fresh.get(Asset, song.id)
        assert (got.metadata_extra or {}).get(REQUESTED_TAG) == REQUESTED_TAG_VALUE
        assert got.category == "lively"  # keeps its own category


@pytest.mark.asyncio
async def test_startup_refresh_failure_does_not_stop_startup(monkeypatch):
    """B-JOB-92: a refresh that fails is logged and startup goes on."""
    def broken():
        raise RuntimeError("db down")

    monkeypatch.setattr(db_engine, "async_session_factory", broken)
    await main._refresh_requested_category()  # no exception


@pytest.mark.asyncio
async def test_refresh_requested_endpoint(client: AsyncClient, db_session: AsyncSession, auth_headers: dict):
    """B-JOB-114: POST /assets/refresh-requested-category tags the popular songs (managers); without a
    login it is refused."""
    from app.models.asset import REQUESTED_TAG

    song = await _popular_song(db_session)
    r = await client.post("/api/v1/assets/refresh-requested-category", headers=auth_headers)
    assert r.status_code == 200, r.text
    await db_session.refresh(song)
    assert REQUESTED_TAG in (song.metadata_extra or {})
    anon = await client.post("/api/v1/assets/refresh-requested-category")
    assert anon.status_code in (401, 403)


# -------------------------------------------- concurrent indexes (B-JOB-100)

class _IndexConn:
    def __init__(self, valid: dict, fail: set = frozenset()):
        self.valid = valid
        self.fail = fail
        self.sql = []

    async def fetchval(self, _sql, name):
        return self.valid.get(name)

    async def execute(self, sql):
        self.sql.append(sql)
        name = sql.split("EXISTS ")[1].split()[0]
        if name in self.fail and sql.startswith("CREATE"):
            raise RuntimeError("lock timeout")


@pytest.mark.asyncio
async def test_indexes_are_built_concurrently_and_only_when_missing():
    """B-JOB-100: valid index skipped, missing one built CONCURRENTLY (no write lock),
    an interrupted (invalid) one dropped and rebuilt, a failed build reported missing."""
    conn = _IndexConn(
        valid={"ix_play_logs_asset_start": True, "ix_queue_entries_station_started": False},
        fail={"ix_queue_entries_asset"},
    )
    names = ["ix_play_logs_asset_start", "ix_queue_entries_station_started",
             "ix_qe_pending_preempt", "ix_queue_entries_asset"]
    missing = await main._build_indexes_once(conn, names)
    assert missing == ["ix_queue_entries_asset"]
    assert not any("ix_play_logs_asset_start" in s for s in conn.sql)
    assert conn.sql[0] == "DROP INDEX CONCURRENTLY IF EXISTS ix_queue_entries_station_started"
    assert all("CONCURRENTLY" in s for s in conn.sql)
    assert any(s.startswith("CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_qe_pending_preempt ON") for s in conn.sql)


@pytest.mark.asyncio
async def test_index_build_retries_then_reports(monkeypatch):
    """B-JOB-100: a refused connection is tried again (INDEX_BUILD_TRIES), then the
    missing indexes are listed; nothing raises."""
    import asyncpg

    tries = []

    async def refuse(*a, **k):
        tries.append(1)
        raise OSError("refused")

    monkeypatch.setattr(asyncpg, "connect", refuse)
    missing = await main._build_concurrent_indexes(dsn="postgresql://x/y", wait=0)
    assert len(tries) == main.INDEX_BUILD_TRIES
    assert set(missing) == set(main.CONCURRENT_INDEXES)


# ---------------------------------------- errors keep CORS, request id (B-JOB-115)

@pytest.mark.asyncio
async def test_unhandled_error_keeps_cors_headers_and_request_id(monkeypatch):
    """B-JOB-115: a crash answers a plain 500 that the browser can read (CORS headers
    present), and every answer carries X-Request-ID (the caller's own when sent)."""
    from app.config import settings

    async def no_tables():
        return None

    monkeypatch.setattr(main, "ensure_tables", no_tables)
    app = main.create_app()  # its own app: a route that crashes on purpose

    @app.get("/boom-for-test")
    async def boom():
        raise RuntimeError("kaboom")

    origin = settings.CORS_ORIGINS[0]
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/boom-for-test", headers={"Origin": origin, "X-Request-ID": "abc123"})
        assert r.status_code == 500
        assert r.json() == {"detail": "Server error. Please try again."}
        assert r.headers.get("access-control-allow-origin") == origin
        ok = await c.get("/health", headers={"Origin": origin})
        assert len(ok.headers.get("x-request-id", "")) == 8  # made up when the caller sends none
