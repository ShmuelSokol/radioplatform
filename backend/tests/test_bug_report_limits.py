"""Bug report limits and what the server copies into a report (checklist V2-49..V2-52, V2-56)."""
import base64
import time

import pytest

from app.api.v1 import bug_reports as bug_api
from app.core import log_buffer
from app.models.bug_report import BugReport


@pytest.fixture(autouse=True)
def _reset():
    bug_api._recent.clear()
    log_buffer.clear()
    yield
    bug_api._recent.clear()


@pytest.mark.xfail(strict=True, reason="BUG: report numbers are max+1 with no unique key; two reports at once share a number")
def test_report_numbers_are_unique():
    col = BugReport.__table__.c.number
    unique = col.unique or any(
        getattr(c, "columns", None) is not None and list(c.columns) == [col] for c in BugReport.__table__.constraints
    ) or any(i.unique and list(i.columns) == [col] for i in BugReport.__table__.indexes)
    assert unique


@pytest.mark.asyncio
async def test_oversized_or_wrong_screenshot_is_dropped_report_kept(client, auth_headers):
    """V2-50: over 4 MB, a GIF, or bad base64 -> saved without a screenshot."""
    big = "data:image/jpeg;base64," + base64.b64encode(b"\xff" * (bug_api.MAX_SCREENSHOT_BYTES + 1)).decode()
    gif = "data:image/gif;base64," + base64.b64encode(b"GIF89a").decode()
    for shot in (big, gif, "data:image/png;base64,%%%"):
        t0 = time.perf_counter()
        r = await client.post("/api/v1/bug-reports", json={"description": "x", "screenshot": shot})
        assert time.perf_counter() - t0 < 1.0
        assert r.status_code == 201
    reports = (await client.get("/api/v1/bug-reports", headers=auth_headers)).json()["reports"]
    assert len(reports) == 3 and not any(x["has_screenshot"] for x in reports)


@pytest.mark.xfail(strict=True, reason="BUG: a dropped screenshot is not reported back; the reporter thinks it was sent")
@pytest.mark.asyncio
async def test_reporter_is_told_when_the_screenshot_was_dropped(client):
    gif = "data:image/gif;base64," + base64.b64encode(b"GIF89a").decode()
    r = await client.post("/api/v1/bug-reports", json={"description": "x", "screenshot": gif})
    assert r.json().get("screenshot_saved") is False


def test_context_is_trimmed_to_the_cap(monkeypatch):
    """V2-51: long lists halved up to 6 times, else replaced by {"trimmed": true}."""
    monkeypatch.setattr(bug_api, "MAX_CONTEXT_CHARS", 20_000)
    ctx = {"console": [{"msg": "x" * 100}] * 400, "network": [{"u": "y" * 50}] * 100, "audio": {"ok": True}}
    out = bug_api._fit_context(ctx)
    import json

    assert len(json.dumps(out)) <= 20_000
    assert out["audio"] == {"ok": True} and 20 <= len(out["console"]) < 400
    huge = {"blob": "z" * 30_000}
    assert bug_api._fit_context(huge) == {"trimmed": True, "note": "context was too large to keep"}


@pytest.mark.asyncio
async def test_server_log_in_a_report_has_no_passwords(client, admin_user):
    """V2-52: the server log slice copied into every report carries request ids but
    never a password or token from the requests around it."""
    await client.post("/api/v1/auth/login", json={"email": admin_user.email, "password": "wrong-secret-pw"},
                      headers={"X-Request-ID": "rid-login"})
    ok = await client.post("/api/v1/auth/login", json={"email": admin_user.email, "password": "testpass123"})
    token = ok.json()["access_token"]
    await client.get("/api/v1/stations", headers={"Authorization": f"Bearer {token}", "X-Request-ID": "rid-st"})
    snap = log_buffer.snapshot()
    text = str(snap)
    assert "wrong-secret-pw" not in text and "testpass123" not in text and token not in text
    assert {"rid-login", "rid-st"} <= {r["rid"] for r in snap["requests"]}


@pytest.mark.asyncio
async def test_rate_limit_keys(client, auth_headers):
    """V2-56: 8 per 10 min per signed-in user (separate from anonymous callers);
    anonymous callers are keyed by their first X-Forwarded-For address."""
    signed = [(await client.post("/api/v1/bug-reports", json={"description": "s"}, headers=auth_headers)).status_code
              for _ in range(bug_api.RATE_MAX + 1)]
    assert signed == [201] * bug_api.RATE_MAX + [429]
    r = await client.post("/api/v1/bug-reports", json={"description": "anon"}, headers={"X-Forwarded-For": "198.51.100.3"})
    assert r.status_code == 201
    r = await client.post("/api/v1/bug-reports", json={"description": "s"}, headers=auth_headers)
    assert r.status_code == 429 and "Too many reports" in r.json()["detail"]


@pytest.mark.xfail(strict=True, reason="BUG: anonymous bug-report limit trusts the client's X-Forwarded-For; changing it gets round the limit")
@pytest.mark.asyncio
async def test_rate_limit_cannot_be_dodged_with_forwarded_for(client):
    codes = [(await client.post("/api/v1/bug-reports", json={"description": "x"},
                                headers={"X-Forwarded-For": f"10.9.0.{i}, 203.0.113.50"})).status_code
             for i in range(bug_api.RATE_MAX + 1)]
    assert codes[-1] == 429
