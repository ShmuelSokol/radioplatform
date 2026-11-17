"""Environment switches (CORS, database URL, Redis/Celery, ntfy, Icecast, the unused
emergency category) and small admin routes (city search, Icecast status, review activity).

Rows: B-SET-31, B-SET-37, B-SET-41, B-SET-42, B-SET-43, B-SET-44, B-JOB-106, B-ICECAST-1,
B-STATIONS-5, B-STATIONS-6, B-REVIEWS-10."""
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import pytest

from app.config import Settings, settings

BACKEND = Path(__file__).resolve().parents[1]


# ── B-SET-41 ──────────────────────────────────────────────────────────────


def test_cors_origins_json_or_comma_list():
    """B-SET-41: CORS_ORIGINS accepts a JSON array or a comma-separated list."""
    assert Settings(_env_file=None, CORS_ORIGINS='["https://a.com","https://b.com"]').CORS_ORIGINS == [
        "https://a.com", "https://b.com"]
    assert Settings(_env_file=None, CORS_ORIGINS="https://a.com, https://b.com").CORS_ORIGINS == [
        "https://a.com", "https://b.com"]
    assert "https://kbrlive.com" in Settings(_env_file=None).CORS_ORIGINS


@pytest.mark.asyncio
async def test_server_error_still_carries_cors(client, monkeypatch):
    """B-SET-41: an unhandled error answers 500 with the CORS header for an allowed origin, so the
    page sees the error instead of a CORS failure; an unknown origin gets no header."""
    from app.api.v1 import stations as stations_api

    async def boom(*a, **k):
        raise RuntimeError("database fell over")

    monkeypatch.setattr(stations_api, "list_stations", boom)
    r = await client.get("/api/v1/stations", headers={"Origin": "https://kbrlive.com"})
    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == "https://kbrlive.com"
    r = await client.get("/api/v1/stations", headers={"Origin": "https://evil.example"})
    assert r.status_code == 500 and "access-control-allow-origin" not in r.headers


# ── B-SET-42 / B-SET-43 / B-SET-44 ────────────────────────────────────────


def test_database_url_fixed_for_asyncpg_and_pooler():
    """B-SET-42: postgres:// and postgresql:// become postgresql+asyncpg://; the engine turns off
    statement caches (Supabase transaction pooler)."""
    assert Settings(_env_file=None, DATABASE_URL="postgres://u:p@h:6543/db").DATABASE_URL == \
        "postgresql+asyncpg://u:p@h:6543/db"
    assert Settings(_env_file=None, DATABASE_URL="postgresql://u:p@h/db").DATABASE_URL == \
        "postgresql+asyncpg://u:p@h/db"
    assert Settings(_env_file=None, DATABASE_URL="postgresql+asyncpg://u:p@h/db").DATABASE_URL == \
        "postgresql+asyncpg://u:p@h/db"
    source = (BACKEND / "app" / "db" / "engine.py").read_text()
    assert '"statement_cache_size": 0' in source and '"prepared_statement_cache_size": 0' in source


def test_no_redis_means_no_workers_and_no_errors():
    """B-SET-43: REDIS_URL empty -> Celery is a stand-in whose .delay() does nothing (imports
    cleanly, no broker); set -> broker and results derived from it (/1 and /2)."""
    s = Settings(_env_file=None, REDIS_URL="redis://r:6379")
    assert (s.CELERY_BROKER_URL, s.CELERY_RESULT_BACKEND) == ("redis://r:6379/1", "redis://r:6379/2")
    assert Settings(_env_file=None).CELERY_BROKER_URL == ""
    code = (
        "from app.workers.celery_app import celery_app\n"
        "assert type(celery_app).__name__ == '_NoOpCelery'\n"
        "@celery_app.task(name='t')\n"
        "def t(x): return x\n"
        "assert t.delay(1) is None and t(2) == 2\n"
    )
    env = {"REDIS_URL": "", "PATH": "/usr/bin:/bin"}
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]


def test_emergency_fallback_category_loads_but_is_unused():
    """B-SET-44: EMERGENCY_FALLBACK_CATEGORY still loads (old settings keep working) and no code
    reads it (dead air restarts the queue instead)."""
    assert Settings(_env_file=None, EMERGENCY_FALLBACK_CATEGORY="old").EMERGENCY_FALLBACK_CATEGORY == "old"
    users = [p for p in (BACKEND / "app").rglob("*.py")
             if "EMERGENCY_FALLBACK_CATEGORY" in p.read_text() and p.name != "config.py"]
    assert users == []


# ── B-JOB-106 / B-SET-37 ──────────────────────────────────────────────────


class _Recorder:
    calls: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, content=None, headers=None):
        _Recorder.calls.append((url, content, headers))
        return httpx.Response(200)


@pytest.mark.asyncio
async def test_ntfy_only_with_a_topic_and_on_its_server(monkeypatch):
    """B-JOB-106 / B-SET-37: NTFY_TOPIC empty -> nothing sent; set -> one POST to
    NTFY_SERVER/NTFY_TOPIC with the title (non-ASCII dropped), click link and tags."""
    from app.services import bug_report_service

    _Recorder.calls = []
    monkeypatch.setattr(bug_report_service.httpx, "AsyncClient", _Recorder)
    monkeypatch.setattr(settings, "NTFY_TOPIC", "")
    assert await bug_report_service.send_ntfy("hello", "Fix ready") is False
    assert _Recorder.calls == []

    monkeypatch.setattr(settings, "NTFY_TOPIC", "secret-topic")
    monkeypatch.setattr(settings, "NTFY_SERVER", "https://ntfy.example/")
    ok = await bug_report_service.send_ntfy("hello", "Fix ready ✅", click="https://x/pr/1", tags="tada")
    assert ok is True
    url, content, headers = _Recorder.calls[0]
    assert url == "https://ntfy.example/secret-topic" and content == b"hello"
    assert headers["Title"] == "Fix ready " and headers["Click"] == "https://x/pr/1" and headers["Tags"] == "tada"


# ── B-ICECAST-1 / B-SET-31 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_icecast_status_and_start_follow_icecast_host(client, auth_headers, db_session, monkeypatch):
    """B-ICECAST-1 / B-SET-31: status reports whether ICECAST_HOST is set (and the active
    streams); with no host, start refuses with a message and opens nothing; with a host it starts
    the station's source on /<station id> by default."""
    from app.models.station import Station
    from app.services import icecast_service

    st = Station(id=uuid.uuid4(), name="FM feed", timezone="UTC")
    db_session.add(st)
    await db_session.commit()
    monkeypatch.setattr(settings, "ICECAST_HOST", "")
    status = (await client.get("/api/v1/icecast/status", headers=auth_headers)).json()
    assert status["enabled"] is False and status["host"] is None and status["active_streams"] == []

    started = []

    async def fake_start(station_id, name, mount=None):
        started.append((station_id, name, mount))

    monkeypatch.setattr(icecast_service, "start_icecast_stream", fake_start)
    r = await client.post(f"/api/v1/icecast/{st.id}/start", headers=auth_headers)
    assert "not configured" in r.json()["error"] and started == []

    monkeypatch.setattr(settings, "ICECAST_HOST", "ice.invalid")
    assert (await client.get("/api/v1/icecast/status", headers=auth_headers)).json()["enabled"] is True
    r = await client.post(f"/api/v1/icecast/{st.id}/start", headers=auth_headers)
    assert r.json() == {"status": "started", "station": "FM feed", "mount": f"/{st.id}"}
    assert started == [(str(st.id), "FM feed", None)]
    assert (await client.post(f"/api/v1/icecast/{uuid.uuid4()}/start", headers=auth_headers)).status_code == 404


# ── B-STATIONS-5 / B-STATIONS-6 ───────────────────────────────────────────


def _nominatim(results):
    class Fake:
        asked: list = []

        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, headers=None):
            Fake.asked.append(params)
            if isinstance(results, Exception):
                raise results
            return httpx.Response(200, json=results, request=httpx.Request("GET", url))

    return Fake


@pytest.mark.asyncio
async def test_city_search_returns_places_with_time_zones(client, auth_headers, monkeypatch):
    """B-STATIONS-5 / B-STATIONS-6: city search asks Nominatim for at most 5 cities and returns
    name, coordinates and the time zone at those coordinates; Nominatim failing -> empty list,
    not an error; managers only."""
    from app.api.v1 import stations as stations_api

    rows = [{"lat": "31.778", "lon": "35.235", "name": "Jerusalem",
             "address": {"city": "Jerusalem", "country": "Israel"}},
            {"lat": "40.0821", "lon": "-74.2097", "name": "Lakewood",
             "address": {"town": "Lakewood", "state": "New Jersey", "country": "United States"}}]
    fake = _nominatim(rows)
    monkeypatch.setattr(stations_api.httpx, "AsyncClient", fake)
    r = await client.get("/api/v1/stations/geocode", params={"q": "jeru"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert fake.asked[0]["limit"] == 5 and fake.asked[0]["q"] == "jeru"
    assert r.json() == [
        {"display_name": "Jerusalem, Israel", "latitude": 31.778, "longitude": 35.235, "timezone": "Asia/Jerusalem"},
        {"display_name": "Lakewood, New Jersey, United States", "latitude": 40.0821, "longitude": -74.2097,
         "timezone": "America/New_York"},
    ]

    monkeypatch.setattr(stations_api.httpx, "AsyncClient", _nominatim(httpx.ConnectTimeout("slow")))
    r = await client.get("/api/v1/stations/geocode", params={"q": "jeru"}, headers=auth_headers)
    assert r.status_code == 200 and r.json() == []
    assert (await client.get("/api/v1/stations/geocode", params={"q": "jeru"})).status_code in (401, 403)
    assert (await client.get("/api/v1/stations/geocode", params={"q": "j"}, headers=auth_headers)).status_code == 422


# ── B-REVIEWS-10 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queue_activity_lists_this_queues_decisions(client, auth_headers, db_session):
    """B-REVIEWS-10: activity lists the decisions on this queue's items, newest first, with the
    reviewer's email; another queue's decisions are not shown; a queue with no items -> []."""
    from app.models.asset import Asset

    assets = [Asset(id=uuid.uuid4(), title=f"A{i}", file_path=f"{i}.mp3", asset_type="music", duration=180)
              for i in range(3)]
    db_session.add_all(assets)
    await db_session.commit()

    async def make(name, ids):
        q = (await client.post("/api/v1/reviews/queues", json={"name": name, "asset_ids": ids}, headers=auth_headers)).json()
        items = (await client.get(f"/api/v1/reviews/queues/{q['id']}/items", headers=auth_headers)).json()["items"]
        return q, items

    q1, items1 = await make("One", [str(assets[0].id), str(assets[1].id)])
    q2, items2 = await make("Two", [str(assets[2].id)])
    await client.patch(f"/api/v1/reviews/items/{items1[0]['id']}", json={"status": "approved", "version": 1},
                       headers=auth_headers)
    await client.patch(f"/api/v1/reviews/items/{items1[1]['id']}", json={"status": "flagged", "notes": "loud", "version": 1},
                       headers=auth_headers)
    await client.patch(f"/api/v1/reviews/items/{items2[0]['id']}", json={"status": "rejected", "version": 1},
                       headers=auth_headers)

    act = (await client.get(f"/api/v1/reviews/queues/{q1['id']}/activity", headers=auth_headers)).json()
    assert sorted(a["action_type"] for a in act) == ["approved", "flagged"]
    assert all(a["user_email"] == "testadmin@test.com" for a in act)
    assert [a["created_at"] for a in act] == sorted((a["created_at"] for a in act), reverse=True)
    assert (await client.get(f"/api/v1/reviews/queues/{uuid.uuid4()}/activity", headers=auth_headers)).json() == []
