"""Activity log internals (checklist V2-41, V2-43..V2-47)."""
import asyncio
import logging
import uuid

import pytest
from starlette.requests import Request

from app.models.station import Station, StationType
from app.services import activity_log


async def _rows(client, auth_headers, **params):
    await activity_log.wait_idle()
    r = await client.get("/api/v1/activity", params=params, headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


@pytest.mark.asyncio
async def test_not_logged_by_design(client, auth_headers, db_session):
    """V2-41: heartbeats, member (CRM) calls, web call-ins, AI drafts, wording checks
    and anonymous or 401 requests leave no row; a signed-in write does."""
    st = Station(id=uuid.uuid4(), name="Quiet", type=StationType.INTERNET)
    db_session.add(st)
    await db_session.commit()
    await client.post("/api/v1/listeners/heartbeat", json={"station_id": str(st.id), "session_key": "k"}, headers=auth_headers)
    await client.post("/api/v1/crm/register", json={"name": "Member"}, headers=auth_headers)
    await client.post(f"/api/v1/call-in/{st.id}", json={"name": "Caller"}, headers=auth_headers)
    await client.post("/api/v1/ai-emails/draft", json={"sponsor_name": "Acme"}, headers=auth_headers)
    await client.post("/api/v1/stations", json={"name": "Anonymous"})  # no token
    await client.post("/api/v1/stations", json={"name": "Bad token"}, headers={"Authorization": "Bearer nope"})
    def written(rows):
        return [r["label"] for r in rows if r["category"] != "signin"]  # the test's own sign-in

    assert written(await _rows(client, auth_headers, include_views="false")) == []

    await client.post("/api/v1/stations", json={"name": "Logged"}, headers=auth_headers)
    assert written(await _rows(client, auth_headers, include_views="false")) == ["Added station"]


def test_request_bodies_are_redacted_and_bounded():
    """V2-43: secret keys masked at any depth (to depth 3), 25 keys, 20 list items."""
    body = {
        "name": "x",
        "Password": "p1", "api_key": "k", "nested": {"refresh_token": "t", "deeper": {"x": {"y": {"z": 1}}}},
        "items": list(range(25)),
        **{f"k{i}": i for i in range(30)},
    }
    out = activity_log._redact(body)
    assert out["Password"] == "•••" and out["api_key"] == "•••"
    assert out["nested"]["refresh_token"] == "•••"
    assert out["nested"]["deeper"]["x"]["y"] == "…"  # cut below depth 3
    assert out["items"][:20] == list(range(20)) and out["items"][20] == "… 5 more"
    assert len(out) == 26 and out["…"] == f"{len(body) - 25} more"


def test_derived_labels_and_categories():
    """V2-44"""
    d = activity_log.describe
    assert d("POST", "/playlists/{template_id}/slots") == ("Added playlist slot", "schedule")
    assert d("PATCH", "/reviews/items/{item_id}")[0] == "Changed review item"
    assert d("POST", "/campaigns/{campaign_id}/comments")[0] == "Added comment"
    assert d("POST", "/stations/{station_id}/controls/play")[1] == "control"
    assert activity_log.action_kind("DELETE", "/x/{id}") == "delete"
    assert activity_log.action_kind("PATCH", "/x/{id}") == "update"


@pytest.mark.parametrize("ua,expected", [
    ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Safari/604.1", "iPad · Safari"),
    ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36", "Android · Chrome"),
    ("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/120.0 Safari/537.36 Edg/120.0", "Windows · Edge"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) Gecko/20100101 Firefox/120.0", "Mac · Firefox"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36", "Linux · Chrome"),
    ("python-httpx/0.27", "Script"),
    ("SomethingOdd/1.0", "SomethingOdd/1.0"),
    (None, None),
])
def test_device_names(ua, expected):
    """V2-47"""
    assert activity_log.describe_device(ua) == expected


def _req(headers: dict, client=("10.0.0.9", 1234)):
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": client, "method": "GET", "path": "/"})


def test_client_ip_order():
    """V2-47: first X-Forwarded-For hop, else X-Real-IP, else the socket."""
    assert activity_log.client_ip(_req({"X-Forwarded-For": "203.0.113.1, 10.0.0.1"})) == "203.0.113.1"
    assert activity_log.client_ip(_req({"X-Real-IP": "198.51.100.2"})) == "198.51.100.2"
    assert activity_log.client_ip(_req({})) == "10.0.0.9"


@pytest.mark.asyncio
async def test_location_text_and_cache(monkeypatch):
    """V2-46: "City, Region, Country" without repeats or "Unknown"; one lookup per IP."""
    calls = []

    async def fake_geo(ip):
        calls.append(ip)
        return {"Lakewood": {"city": "Lakewood", "region": "New Jersey", "country": "United States"},
                "Singapore": {"city": "Singapore", "region": "Singapore", "country": "Singapore"},
                "x": {"city": "", "region": "", "country": "Unknown"}}[ip]

    monkeypatch.setattr("app.api.v1.listeners._resolve_geo", fake_geo)
    monkeypatch.setattr(activity_log, "GEO_LOOKUP", True)
    activity_log._geo_cache.clear()
    assert await activity_log._location("Lakewood") == "Lakewood, New Jersey, United States"
    assert await activity_log._location("Singapore") == "Singapore"
    assert await activity_log._location("x") is None
    assert await activity_log._location("Lakewood") == "Lakewood, New Jersey, United States"
    assert calls == ["Lakewood", "Singapore", "x"]
    activity_log._geo_cache.clear()


@pytest.mark.asyncio
async def test_writer_drops_when_full_and_survives_insert_errors(monkeypatch, caplog):
    """V2-45: queue capped at 5,000 (extra rows dropped with a warning); a failed
    insert is logged and the writer keeps going."""
    lg = logging.getLogger("app")
    lg.addHandler(caplog.handler)
    caplog.set_level(logging.WARNING)
    batches = []
    fail = {"once": True}

    async def fake_insert(batch):
        if fail["once"]:
            fail["once"] = False
            raise RuntimeError("db down")
        batches.append(len(batch))

    monkeypatch.setattr(activity_log, "_insert", fake_insert)
    w = activity_log._Writer()
    try:
        for i in range(5001):
            w.put({"i": i})
        assert any("queue full" in m for m in caplog.messages)
        await asyncio.wait_for(w.wait_idle(), 5)
        # first batch failed (logged), the rest were written in batches of at most 100
        assert any("activity log write failed" in m for m in caplog.messages)
        assert max(batches) == 100 and sum(batches) == 4900  # 5,000 kept, the failed 100 lost
    finally:
        w.task.cancel()
        lg.removeHandler(caplog.handler)
