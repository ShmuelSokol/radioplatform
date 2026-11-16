"""Report a bug: the report keeps what the browser and server saw, staff can read it,
and the GitHub/ntfy bridge sends one push per fix. See wiki/bug-reports.md."""
import base64
import logging

import pytest
from httpx import AsyncClient

from app.api.v1 import bug_reports as bug_api
from app.core import log_buffer
from app.services import bug_report_service as svc

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


@pytest.fixture(autouse=True)
def _reset():
    bug_api._recent.clear()
    log_buffer.clear()
    yield
    bug_api._recent.clear()


@pytest.mark.asyncio
async def test_anyone_can_report_and_staff_can_read_it(client: AsyncClient, auth_headers: dict):
    logging.getLogger("app.test").warning("queue advanced twice")
    r = await client.post("/api/v1/bug-reports", json={
        "description": "Two stations play at once",
        "page_url": "https://kbrlive.com/listen/abc",
        "station_id": "abc",
        "screenshot": PNG,
        "context": {"audio": [{"src": "a.mp3", "paused": False}], "console": [{"level": "error", "msg": "boom"}]},
    }, headers={"X-Request-ID": "rid-test-1"})
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["number"] >= 1 and first["mirrored"] is False

    r2 = await client.post("/api/v1/bug-reports", json={"description": "second"})
    assert r2.json()["number"] == first["number"] + 1

    # Listing and reading need a staff login
    assert (await client.get("/api/v1/bug-reports")).status_code in (401, 403)
    lst = (await client.get("/api/v1/bug-reports", headers=auth_headers)).json()["reports"]
    assert [x["number"] for x in lst][:2] == [first["number"] + 1, first["number"]]

    detail = (await client.get(f"/api/v1/bug-reports/{first['id']}", headers=auth_headers)).json()
    assert detail["client_context"]["audio"][0]["src"] == "a.mp3"
    msgs = [x["msg"] for x in detail["server_log"]["log"]]
    assert any("queue advanced twice" in m for m in msgs)
    assert detail["server_log"]["report_request_id"] == "rid-test-1"

    shot = await client.get(f"/api/v1/bug-reports/{first['id']}/screenshot", headers=auth_headers)
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/png"


@pytest.mark.asyncio
async def test_requests_are_logged_with_their_id(client: AsyncClient):
    await client.get("/api/v1/stations", headers={"X-Request-ID": "rid-abc"})
    snap = log_buffer.snapshot()
    assert any(x["rid"] == "rid-abc" and x["path"] == "/api/v1/stations" for x in snap["requests"])


@pytest.mark.asyncio
async def test_rate_limit(client: AsyncClient):
    codes = [(await client.post("/api/v1/bug-reports", json={"description": f"x{i}"})).status_code for i in range(bug_api.RATE_MAX + 1)]
    assert codes[-1] == 429 and codes[0] == 201


def test_bad_screenshot_is_dropped_not_fatal():
    assert bug_api._decode_screenshot("data:text/html;base64,PGI+") == (None, None)
    assert bug_api._decode_screenshot("nonsense") == (None, None)


def test_label_and_pr_link_parsing():
    assert svc.label_kind([{"name": "bug-report"}, {"name": "auto-fix-ready"}]) == "fix_ready"
    assert svc.label_kind([{"name": "auto-fix-needs-owner"}]) == "needs_owner"
    assert svc.label_kind([{"name": "bug-report"}]) is None
    comments = [{"body": "Plan: ..."}, {"body": "Fix ready: https://github.com/ShmuelSokol/radioplatform/pull/120"}]
    assert svc.latest_pr_url(comments) == "https://github.com/ShmuelSokol/radioplatform/pull/120"


def test_issue_body_has_summary_and_files():
    class R:
        number = 7
        reporter_name = "admin"
        created_at = None
        page_url = "https://kbrlive.com/admin/dashboard"
        station_id = "s1"
        user_agent = "Safari"
        description = "Left channel only"
        expected = "Both channels"
        client_context = {"console": [{"level": "error", "msg": "decode failed", "at": "t"}],
                          "network": [{"method": "GET", "url": "/queue", "status": 500, "rid": "r1"}]}
    body = svc.issue_body(R(), {"context.json": "https://x/context.json", "screenshot": "https://x/s.jpg"})
    assert "Left channel only" in body and "decode failed" in body and "/queue -> 500" in body
    assert "![screenshot](https://x/s.jpg?raw=true)" in body


@pytest.mark.asyncio
async def test_github_copy_and_one_push_per_fix(client: AsyncClient, auth_headers: dict, monkeypatch):
    import json as _json

    import httpx

    import app.db.engine as eng
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(eng, "async_session_factory", TestSessionLocal)
    monkeypatch.setattr(svc.settings, "BUG_REPORTS_GITHUB_TOKEN", "t")
    monkeypatch.setattr(svc.settings, "BUG_REPORTS_REPO", "o/private")
    monkeypatch.setattr(svc.settings, "NTFY_TOPIC", "secret-topic")
    calls, pushes = [], []
    state = {"labels": [{"name": "bug-report"}]}

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, str(req.url)))
        u = str(req.url)
        if "ntfy" in u:
            pushes.append((req.headers.get("Title"), req.content.decode(), req.headers.get("Click")))
            return httpx.Response(200, json={})
        if "/contents/" in u:
            return httpx.Response(201, json={"content": {"html_url": "https://github.com/o/private/blob/main/" + u.split("/contents/")[1]}})
        if req.method == "POST" and u.endswith("/issues"):
            assert _json.loads(req.content)["labels"] == ["bug-report"]
            return httpx.Response(201, json={"number": 3, "html_url": "https://github.com/o/private/issues/3"})
        if u.endswith("/issues/3"):
            return httpx.Response(200, json={"state": "open", "labels": state["labels"]})
        if "/issues/3/comments" in u:
            return httpx.Response(200, json=[{"body": "Fix ready: https://github.com/ShmuelSokol/radioplatform/pull/99"}])
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    # Run the copy inline instead of in the background
    monkeypatch.setattr(svc, "github_enabled", lambda: False)
    r = await client.post("/api/v1/bug-reports", json={"description": "Plays both stations", "screenshot": PNG})
    monkeypatch.setattr(svc, "github_enabled", lambda: True)
    await svc.mirror_to_github(__import__("uuid").UUID(r.json()["id"]))
    put_paths = [u for m, u in calls if m == "PUT"]
    assert any(p.endswith("context.json") for p in put_paths) and any(p.endswith("screenshot.png") for p in put_paths)

    assert (await svc.check_github_once())["pushed"] == 0  # no label yet
    state["labels"] = [{"name": "bug-report"}, {"name": "auto-fix-ready"}]
    assert (await svc.check_github_once())["pushed"] == 1
    assert (await svc.check_github_once())["pushed"] == 0  # only once
    title, body, click = pushes[0]
    assert "fix ready" in title and click.endswith("/pull/99") and "Plays both stations" in body
    detail = (await client.get(f"/api/v1/bug-reports/{r.json()['id']}", headers=auth_headers)).json()
    assert detail["status"] == "fix_ready" and detail["github_url"].endswith("/issues/3")
