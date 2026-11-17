"""Backend platform: debug setting, response headers, gzip, request logging and
database connection health (checklist V2-123, V2-124, V2-129)."""
import logging
import time
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import DisconnectionError

from app.config import Settings, settings
from app.db import engine as engine_mod


@pytest.fixture
def app_log(caplog):
    """The "app" logger does not propagate to the root (main.py), so listen on it directly."""
    lg = logging.getLogger("app")
    lg.addHandler(caplog.handler)
    caplog.set_level(logging.WARNING)
    yield caplog
    lg.removeHandler(caplog.handler)


def test_debug_is_off_unless_asked(monkeypatch):
    monkeypatch.delenv("APP_DEBUG", raising=False)
    assert Settings(_env_file=None).APP_DEBUG is False


def test_debug_setting_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("APP_DEBUG", "false")
    assert Settings(_env_file=None).APP_DEBUG is False
    monkeypatch.setenv("APP_DEBUG", "true")
    assert Settings(_env_file=None).APP_DEBUG is True


def _app_with_test_routes():
    from fastapi import FastAPI

    from app.main import create_app

    app: FastAPI = create_app()

    @app.get("/_t/big")
    async def big():
        return {"text": "x" * 5000}

    @app.get("/_t/boom")
    async def boom():
        raise RuntimeError("kaboom")

    return app


@pytest.mark.asyncio
async def test_security_headers_gzip_and_request_id(monkeypatch):
    app = _app_with_test_routes()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/_t/big", headers={"Accept-Encoding": "gzip", "X-Request-ID": "abc123"})
        assert r.status_code == 200
        assert r.headers["content-encoding"] == "gzip"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert r.headers["x-xss-protection"] == "1; mode=block"
        assert r.headers["x-request-id"] == "abc123"
        assert "strict-transport-security" not in r.headers

        small = await c.get("/health", headers={"Accept-Encoding": "gzip"})
        assert "content-encoding" not in small.headers  # under 500 bytes
        assert len(small.headers["x-request-id"]) == 8  # made up when the client sends none

        monkeypatch.setattr(settings, "APP_ENV", "production")
        r = await c.get("/health")
        assert r.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


@pytest.mark.asyncio
async def test_failed_and_slow_requests_are_logged_with_their_id(monkeypatch, app_log):
    caplog = app_log
    app = _app_with_test_routes()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/_t/boom", headers={"X-Request-ID": "rid500"})
        assert r.status_code == 500 and r.json() == {"detail": "Server error. Please try again."}
        assert any("GET /_t/boom -> 500" in m and "rid500" in m for m in caplog.messages)

        caplog.clear()
        clock = iter([100.0, 104.0])  # the request takes 4 s on the request-id layer's clock
        monkeypatch.setattr("app.core.middleware.time", SimpleNamespace(perf_counter=lambda: next(clock)))
        await c.get("/_t/big", headers={"X-Request-ID": "ridslow"})
        monkeypatch.setattr("app.core.middleware.time", time)
        assert any("GET /_t/big -> 200" in m and "ridslow" in m for m in caplog.messages)


def test_idle_connection_is_pinged_and_replaced_when_dead(monkeypatch):
    """V2-129: a connection idle 15 s or more is pinged; a dead one is thrown away."""
    pings = []

    class Conn:
        def __init__(self, ok):
            self.ok = ok

        def ping(self):
            pings.append(self.ok)
            if not self.ok:
                raise OSError("server closed the connection")

    now = 1000.0
    monkeypatch.setattr(engine_mod.time, "monotonic", lambda: now)
    fresh = SimpleNamespace(info={"idle_since": now - 5})
    engine_mod._ping_if_idle(Conn(False), fresh, None)
    assert pings == []  # used recently: no round trip
    engine_mod._ping_if_idle(Conn(True), SimpleNamespace(info={}), None)
    assert pings == []  # brand new
    engine_mod._ping_if_idle(Conn(True), SimpleNamespace(info={"idle_since": now - 16}), None)
    assert pings == [True]
    with pytest.raises(DisconnectionError):
        engine_mod._ping_if_idle(Conn(False), SimpleNamespace(info={"idle_since": now - 600}), None)
    assert engine_mod.engine.pool._recycle == 300


def test_slow_query_log_hides_personal_parameters(monkeypatch, app_log):
    caplog = app_log
    conn = SimpleNamespace(info={})
    t = [0.0]
    monkeypatch.setattr(engine_mod.time, "monotonic", lambda: t[0])
    engine_mod._before_cursor_execute(conn, None, "SELECT", None, None, False)
    t[0] = 1.0
    engine_mod._after_cursor_execute(conn, None, "SELECT * FROM users WHERE email = $1",
                                     ("someone@example.com", "+15551234567"), None, False)
    assert any("SLOW QUERY" in m for m in caplog.messages)
    assert not any("someone@example.com" in m or "+15551234567" in m for m in caplog.messages)
