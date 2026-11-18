"""Admin checklist rows: AI emails, bug report GitHub copy and pushes, review queue
endpoints, the Activity search, RDS in engine mode, the song request Match box
(server side) and the Databank Find / end-date edits.

Rows: B-AIEMAILS-1, B-BUGREPORTS-7/8, B-JOB-105, B-REVIEWS-1/6/7/9, V2-48, V2-53,
V2-54, V2-55, V2-58, V2-84, F-MATCH-3/5/13/14/18/19, F-DATABANK-57/59/61."""
import asyncio
import json
import time
import types
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.bug_report import BugReport
from app.models.queue_entry import QueueEntry
from app.models.song_request import RequestStatus, SongRequest
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.models.user import UserRole
from app.services import bug_report_service as svc

_REAL_CLIENT = httpx.AsyncClient
from tests.test_station_permissions import _user
from tests.timing import limit

API = "/api/v1"


# ── AI emails ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ai_draft_uses_claude_with_the_request_context(client, auth_headers, db_session, monkeypatch):
    """B-AIEMAILS-1: with a key set the draft is Claude's text, and the prompt carries the
    sponsor, purpose, campaign, instructions and stats; a manager may draft, a DJ may not."""
    import anthropic

    from app.config import settings

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-fake")
    prompts = []

    class Fake:
        def __init__(self, *a, **k):
            def create(**kw):
                prompts.append(kw["messages"][0]["content"])
                return types.SimpleNamespace(content=[types.SimpleNamespace(text="Thank you, Acme!")])
            self.messages = types.SimpleNamespace(create=create)

    monkeypatch.setattr(anthropic, "Anthropic", Fake)
    body = {"sponsor_name": "Acme", "purpose": "renewal", "campaign_name": "Pesach",
            "custom_instructions": "mention the new show", "recent_stats": {"total_plays": 412}}
    r = await client.post(f"{API}/ai-emails/draft", json=body, headers=auth_headers)
    assert r.status_code == 200 and r.json() == {"draft_text": "Thank you, Acme!"}
    p = prompts[0]
    assert "Acme" in p and "renewal" in p and "Pesach" in p and "mention the new show" in p and "412" in p
    assert "Kol B'ramah" in p
    _, dj = await _user(client, db_session, UserRole.DJ)
    assert (await client.post(f"{API}/ai-emails/draft", json=body, headers=dj)).status_code == 403

    r = await client.post(f"{API}/ai-emails/draft", json={"sponsor_name": "Acme"})
    assert r.status_code in (401, 403)


# ── Bug reports: GitHub copy, pushes, follow-up ──────────────────────────────

def _gh(monkeypatch, handler, *, token="t", topic="secret-topic"):
    import app.db.engine as eng
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(eng, "async_session_factory", TestSessionLocal)
    monkeypatch.setattr(svc.settings, "BUG_REPORTS_GITHUB_TOKEN", token)
    monkeypatch.setattr(svc.settings, "BUG_REPORTS_REPO", "o/private")
    monkeypatch.setattr(svc.settings, "NTFY_TOPIC", topic)
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda **kw: _REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))


async def _report(db, number=1, **kw):
    r = BugReport(id=uuid.uuid4(), number=number, description=kw.pop("description", "Two stations play at once"),
                  **kw)
    db.add(r)
    await db.commit()
    return r


@pytest.mark.asyncio
async def test_github_copy_files_issue_body_and_failure(client, auth_headers, db_session, monkeypatch):
    """V2-53: files under reports/<00000>/, an issue labelled bug-report whose body lists
    reporter, page, station, browser, player, last 10 console errors, last 10 failed
    requests and the screenshot. A GitHub failure is saved as mirror_error (shown in the
    admin list) and the report stays "new"."""
    console = [{"level": "error", "msg": f"err {i}", "at": "t"} for i in range(14)] + [{"level": "log", "msg": "plain"}]
    network = [{"method": "GET", "url": f"/x{i}", "status": 500, "rid": f"r{i}"} for i in range(13)] + \
        [{"method": "GET", "url": "/ok", "status": 200}]
    rep = await _report(
        db_session, number=42, reporter_name="Shmuel", page_url="https://kbrlive.com/admin/dashboard",
        station_id="st-1", user_agent="Safari 18", screenshot=b"\x89PNGfake", screenshot_mime="image/png",
        server_log={"log": [{"msg": "x"}]},
        client_context={"player": {"playing": True, "station": "st-1"}, "audio": [{"src": "a.mp3", "paused": False}],
                        "console": console, "network": network},
    )
    puts, issues = [], []
    fail = {"issue": False}

    def handler(req: httpx.Request) -> httpx.Response:
        u = str(req.url)
        if "/contents/" in u:
            puts.append(u.split("/contents/")[1])
            return httpx.Response(201, json={"content": {"html_url": "https://github.com/o/private/blob/main/" + u.split("/contents/")[1]}})
        if req.method == "POST" and u.endswith("/issues"):
            if fail["issue"]:
                return httpx.Response(500, text="GitHub is down")
            issues.append(json.loads(req.content))
            return httpx.Response(201, json={"number": 9, "html_url": "https://github.com/o/private/issues/9"})
        if "ntfy" in u:
            return httpx.Response(200)
        return httpx.Response(404)

    _gh(monkeypatch, handler)
    fail["issue"] = True
    await svc.mirror_to_github(rep.id)
    await db_session.refresh(rep)
    await db_session.commit()  # end the read so the copy's own session may write
    assert rep.status == "new" and rep.github_issue is None and "HTTP 500" in rep.mirror_error
    listed = (await client.get(f"{API}/bug-reports", headers=auth_headers)).json()["reports"]
    assert listed[0]["mirror_error"] and "HTTP 500" in listed[0]["mirror_error"]

    fail["issue"] = False
    puts.clear()
    await db_session.commit()
    await svc.mirror_to_github(rep.id)
    await db_session.refresh(rep)
    await db_session.commit()
    assert puts == ["reports/00042/context.json", "reports/00042/server-log.json", "reports/00042/screenshot.png"]
    assert rep.status == "mirrored" and rep.github_issue == 9 and rep.mirror_error is None
    issue = issues[0]
    assert issue["labels"] == ["bug-report"] and issue["title"].startswith("#42 ")
    body = issue["body"]
    for want in ("**Reporter:** Shmuel", "**Page:** https://kbrlive.com/admin/dashboard", "**Station:** st-1",
                 "Safari 18", "### Player at the time", "a.mp3", "![screenshot]("):
        assert want in body, want
    errs = [ln for ln in body.splitlines() if ": err " in ln]
    assert len(errs) == 10 and "err 13" in errs[-1] and "err 4" in errs[0]   # the LAST 10
    failed = [ln for ln in body.splitlines() if ln.startswith("- GET /x")]
    assert len(failed) == 10 and "/x12" in failed[-1] and "/ok" not in body

    # A second copy does nothing (already has an issue)
    puts.clear()
    await svc.mirror_to_github(rep.id)
    assert puts == []


@pytest.mark.asyncio
async def test_resend_copies_again_admin_only(client, auth_headers, db_session, monkeypatch):
    """B-BUGREPORTS-7/8: resend without a token says so; with one it copies a report that
    was sent before the token was set; a manager is refused; unknown id is 404."""
    rep = await _report(db_session, number=5)
    monkeypatch.setattr(svc.settings, "BUG_REPORTS_GITHUB_TOKEN", "")
    r = await client.post(f"{API}/bug-reports/{rep.id}/resend", headers=auth_headers)
    assert r.json() == {"mirrored": False, "detail": "BUG_REPORTS_GITHUB_TOKEN is not set"}

    def handler(req):
        u = str(req.url)
        if "/contents/" in u:
            return httpx.Response(201, json={"content": {"html_url": "https://x/" + u.split("/contents/")[1]}})
        if u.endswith("/issues"):
            return httpx.Response(201, json={"number": 11, "html_url": "https://github.com/o/private/issues/11"})
        return httpx.Response(404)

    _gh(monkeypatch, handler)
    _, mgr = await _user(client, db_session, UserRole.MANAGER)
    assert (await client.post(f"{API}/bug-reports/{rep.id}/resend", headers=mgr)).status_code == 403
    r = await client.post(f"{API}/bug-reports/{rep.id}/resend", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "mirrored" and r.json()["github_url"].endswith("/issues/11")
    assert (await client.post(f"{API}/bug-reports/{uuid.uuid4()}/resend", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_received_push_with_and_without_github(db_session, monkeypatch):
    """V2-54: "Bug N received" goes out at once: title, first 160 characters, who, and a
    link to the issue; with no GitHub it says "It is saved in Bug Reports" and links the
    admin page."""
    pushes = []

    def handler(req):
        u = str(req.url)
        if "ntfy" in u:
            pushes.append((req.headers.get("Title"), req.content.decode(), req.headers.get("Click")))
            return httpx.Response(200)
        if "/contents/" in u:
            return httpx.Response(201, json={"content": {"html_url": "https://x/f"}})
        if u.endswith("/issues"):
            return httpx.Response(201, json={"number": 3, "html_url": "https://github.com/o/private/issues/3"})
        return httpx.Response(404)

    long = "A" * 170 + " tail"
    rep = await _report(db_session, number=7, description=long, reporter_name="Moshe")
    _gh(monkeypatch, handler)
    await svc.report_received(rep.id)
    title, body, click = pushes[-1]
    assert title == "Bug 7 received" and click == "https://github.com/o/private/issues/3"
    first_line = body.split("\n")[0]
    assert len(first_line) == 160 and first_line.endswith("…") and "tail" not in first_line
    assert "From Moshe." in body and "next hourly check" in body

    rep2 = await _report(db_session, number=8, description="No sound")
    _gh(monkeypatch, handler, token="")
    await svc.report_received(rep2.id)
    title, body, click = pushes[-1]
    assert title == "Bug 8 received" and "From a listener. It is saved in Bug Reports." in body
    assert click == "https://kbrlive.com/admin/bug-reports"


@pytest.mark.asyncio
async def test_github_follow_up_rules(db_session, monkeypatch):
    """V2-55: closed as completed -> fixed, closed otherwise -> closed; only the last 45
    days are checked; "fix ready" carries the latest PR link; a failed push leaves the
    status so the next pass retries; one push per label."""
    now = datetime.now(UTC)
    done = await _report(db_session, 1, github_issue=1, status="mirrored")
    wontfix = await _report(db_session, 2, github_issue=2, status="mirrored")
    old = await _report(db_session, 3, github_issue=3, status="mirrored")
    old.created_at = now - timedelta(days=46)
    ready = await _report(db_session, 4, github_issue=4, status="mirrored", github_url="https://github.com/o/private/issues/4")
    await db_session.commit()
    issues = {
        1: {"state": "closed", "state_reason": "completed", "labels": []},
        2: {"state": "closed", "state_reason": "not_planned", "labels": []},
        3: {"state": "closed", "state_reason": "completed", "labels": []},
        4: {"state": "open", "labels": [{"name": "auto-fix-ready"}]},
    }
    asked, pushes = [], []
    ntfy_ok = {"v": False}

    def handler(req):
        u = str(req.url)
        if "ntfy" in u:
            pushes.append((req.headers.get("Title"), req.headers.get("Click")))
            return httpx.Response(200 if ntfy_ok["v"] else 500)
        if u.endswith("/comments?per_page=100"):
            return httpx.Response(200, json=[
                {"body": "Plan https://github.com/ShmuelSokol/radioplatform/pull/1"},
                {"body": "Fix ready: https://github.com/ShmuelSokol/radioplatform/pull/2"},
                {"body": "thanks"},
            ])
        n = int(u.rsplit("/", 1)[1])
        asked.append(n)
        return httpx.Response(200, json=issues[n])

    _gh(monkeypatch, handler)
    out = await svc.check_github_once()
    assert 3 not in asked and out["checked"] == 3
    for r in (done, wontfix, old, ready):
        await db_session.refresh(r)
    assert (done.status, wontfix.status, old.status) == ("fixed", "closed", "mirrored")
    assert ready.status == "mirrored" and out["pushed"] == 0          # push failed: retried next pass
    ntfy_ok["v"] = True
    out = await svc.check_github_once()
    await db_session.refresh(ready)
    assert out["pushed"] == 1 and ready.status == "fix_ready"
    assert ready.fix_url == "https://github.com/ShmuelSokol/radioplatform/pull/2"
    assert pushes[-1] == ("Bug 4: fix ready", "https://github.com/ShmuelSokol/radioplatform/pull/2")
    n = len(pushes)
    assert (await svc.check_github_once())["pushed"] == 0 and len(pushes) == n   # never repeated
    issues[4]["labels"] = [{"name": "auto-fix-needs-owner"}]
    assert (await svc.check_github_once())["pushed"] == 1                      # a new label: one more
    assert pushes[-1][0] == "Bug 4 needs you"


@pytest.mark.asyncio
async def test_notify_loop_timing(monkeypatch):
    """B-JOB-105: first check after 60 s, then every NOTIFY_EVERY_SECONDS; a failing pass
    does not end the loop."""
    sleeps, passes = [], []

    async def fake_sleep(s):
        sleeps.append(s)
        if len(sleeps) > 3:
            raise asyncio.CancelledError

    async def check():
        passes.append(len(sleeps))
        if len(passes) == 1:
            raise RuntimeError("GitHub down")
        return {}

    monkeypatch.setattr(svc.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(svc, "check_github_once", check)
    with pytest.raises(asyncio.CancelledError):
        await svc.notify_loop()
    assert sleeps == [60, svc.NOTIFY_EVERY_SECONDS, svc.NOTIFY_EVERY_SECONDS, svc.NOTIFY_EVERY_SECONDS]
    assert passes == [1, 2, 3] and svc.NOTIFY_EVERY_SECONDS == 300


# ── Review queue endpoints ───────────────────────────────────────────────────

async def _assets(db, n):
    items = [Asset(id=uuid.uuid4(), title=f"Song {i}", file_path=f"{i}.mp3", asset_type="music", duration=180)
             for i in range(n)]
    db.add_all(items)
    await db.commit()
    return items


@pytest.mark.asyncio
async def test_create_queue(client, auth_headers, db_session, admin_user):
    """B-REVIEWS-1: a manager creates a queue (items in the given order, positions from 1,
    created_by set); a DJ is refused; an id that is not in the library is a clear 400."""
    a, b, c = await _assets(db_session, 3)
    _, mgr = await _user(client, db_session, UserRole.MANAGER)
    _, dj = await _user(client, db_session, UserRole.DJ)
    body = {"name": "Week 1", "description": "new uploads", "asset_ids": [str(c.id), str(a.id), str(b.id)]}
    assert (await client.post(f"{API}/reviews/queues", json=body, headers=dj)).status_code == 403
    r = await client.post(f"{API}/reviews/queues", json=body, headers=mgr)
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["status"] == "open" and q["total_items"] == 3 and q["description"] == "new uploads"
    items = (await client.get(f"{API}/reviews/queues/{q['id']}/items", headers=auth_headers)).json()["items"]
    assert [(i["asset_id"], i["position"], i["status"], i["version"]) for i in items] == [
        (str(c.id), 1, "pending", 1), (str(a.id), 2, "pending", 1), (str(b.id), 3, "pending", 1)]
    missing = await client.post(f"{API}/reviews/queues", json={"name": "x", "asset_ids": [str(uuid.uuid4())]},
                                headers=auth_headers)
    assert missing.status_code == 400 and "not in the library" in missing.json()["detail"]


@pytest.mark.asyncio
async def test_update_item(client, auth_headers, db_session):
    """B-REVIEWS-6: a DJ (staff) may save a decision; notes alone save and bump the version;
    an unknown status is refused (422); an unknown item is 404."""
    (a,) = await _assets(db_session, 1)
    q = (await client.post(f"{API}/reviews/queues", json={"name": "q", "asset_ids": [str(a.id)]}, headers=auth_headers)).json()
    item = (await client.get(f"{API}/reviews/queues/{q['id']}/items", headers=auth_headers)).json()["items"][0]
    _, dj = await _user(client, db_session, UserRole.DJ)
    _, sponsor = await _user(client, db_session, UserRole.SPONSOR)
    assert (await client.patch(f"{API}/reviews/items/{item['id']}", json={"notes": "x", "version": 1}, headers=sponsor)).status_code == 403
    r = await client.patch(f"{API}/reviews/items/{item['id']}", json={"notes": "check the ending", "version": 1}, headers=dj)
    assert r.status_code == 200 and r.json()["notes"] == "check the ending" and r.json()["version"] == 2
    assert r.json()["status"] == "pending"
    bad = await client.patch(f"{API}/reviews/items/{item['id']}", json={"status": "banana", "version": 2}, headers=dj)
    assert bad.status_code == 422
    r = await client.patch(f"{API}/reviews/items/{item['id']}", json={"status": "flagged", "version": 2}, headers=dj)
    assert r.status_code == 200 and r.json()["status"] == "flagged" and r.json()["version"] == 3
    assert (await client.patch(f"{API}/reviews/items/{uuid.uuid4()}", json={"version": 1}, headers=dj)).status_code == 404


@pytest.mark.asyncio
async def test_batch_update_bad_input(client, auth_headers, db_session):
    """B-REVIEWS-7: batch approve bumps each version; a malformed item id is a clear 400
    (not a server error) and changes nothing; an unknown status is 422; a DJ is refused."""
    a, b = await _assets(db_session, 2)
    q = (await client.post(f"{API}/reviews/queues", json={"name": "q", "asset_ids": [str(a.id), str(b.id)]}, headers=auth_headers)).json()
    items = (await client.get(f"{API}/reviews/queues/{q['id']}/items", headers=auth_headers)).json()["items"]
    ids = [i["id"] for i in items]
    _, dj = await _user(client, db_session, UserRole.DJ)
    assert (await client.post(f"{API}/reviews/queues/{q['id']}/batch-update", json={"item_ids": ids, "status": "approved"}, headers=dj)).status_code == 403
    r = await client.post(f"{API}/reviews/queues/{q['id']}/batch-update", json={"item_ids": [ids[0], "nope"], "status": "approved"}, headers=auth_headers)
    assert r.status_code == 400
    r = await client.post(f"{API}/reviews/queues/{q['id']}/batch-update", json={"item_ids": ids, "status": "maybe"}, headers=auth_headers)
    assert r.status_code == 422
    after = (await client.get(f"{API}/reviews/queues/{q['id']}/items", headers=auth_headers)).json()["items"]
    assert [i["status"] for i in after] == ["pending", "pending"]
    r = await client.post(f"{API}/reviews/queues/{q['id']}/batch-update", json={"item_ids": ids, "status": "approved"}, headers=auth_headers)
    assert r.json() == {"updated": 2}
    after = (await client.get(f"{API}/reviews/queues/{q['id']}/items", headers=auth_headers)).json()["items"]
    assert [(i["status"], i["version"]) for i in after] == [("approved", 2), ("approved", 2)]


@pytest.mark.asyncio
async def test_asset_comment(client, auth_headers, db_session):
    """B-REVIEWS-9: staff comment shows in the item's history with the author; a missing
    comment saves as empty; an unknown library item is 404; a sponsor is refused."""
    (a,) = await _assets(db_session, 1)
    dj_user, dj = await _user(client, db_session, UserRole.DJ)
    _, sponsor = await _user(client, db_session, UserRole.SPONSOR)
    r = await client.post(f"{API}/reviews/assets/{a.id}/comment", json={"comment": "Fades out early"}, headers=dj)
    assert r.status_code == 200 and r.json()["status"] == "created"
    assert (await client.post(f"{API}/reviews/assets/{a.id}/comment", json={}, headers=dj)).status_code == 200
    hist = (await client.get(f"{API}/reviews/assets/{a.id}/history", headers=auth_headers)).json()
    assert sorted(h["comment"] for h in hist) == ["", "Fades out early"]
    assert {h["user_email"] for h in hist} == {dj_user.email}
    assert (await client.post(f"{API}/reviews/assets/{uuid.uuid4()}/comment", json={"comment": "x"}, headers=dj)).status_code == 404
    assert (await client.post(f"{API}/reviews/assets/{a.id}/comment", json={"comment": "x"}, headers=sponsor)).status_code == 403


# ── Activity search (V2-48) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_activity_search_categories_and_range(client, auth_headers, db_session):
    """V2-48: search covers label, detail, email, name, path, IP and location; categories are
    a comma list and "other" includes rows with none; start inclusive, end exclusive; a
    time without a zone is UTC; a sponsor's page views outside /sponsor are not recorded."""
    from app.services import activity_log

    base = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    rows = [
        dict(label="Pressed Next", category="control", detail="KBR Main", ip_address="10.1.1.1"),
        dict(label="Changed a schedule", category="schedule", user_email="dovid@x.test", location="Lakewood, NJ, US"),
        dict(label="Signed in", category=None, user_name="Yossi Katz", path="/api/v1/auth/login"),
        dict(label="Opened Users", category="view"),
    ]
    for i, kw in enumerate(rows):
        db_session.add(AuditLog(id=uuid.uuid4(), action="action", resource_type="x",
                                created_at=base + timedelta(minutes=i), **kw))
    await db_session.commit()

    async def get(**p):
        await activity_log.wait_idle()
        r = await client.get(f"{API}/activity", params=p, headers=auth_headers)
        assert r.status_code == 200, r.text
        return sorted(x["label"] for x in r.json()["items"] if x["created_at"].startswith("2026-11-10"))

    assert await get(q="next") == ["Pressed Next"]                 # label
    assert await get(q="kbr main") == ["Pressed Next"]             # detail
    assert await get(q="10.1.1") == ["Pressed Next"]               # IP
    assert await get(q="DOVID@") == ["Changed a schedule"]          # email
    assert await get(q="lakewood") == ["Changed a schedule"]       # location
    assert await get(q="yossi") == ["Signed in"]                   # name
    assert await get(q="auth/login") == ["Signed in"]              # path
    assert await get(category="control,schedule") == ["Changed a schedule", "Pressed Next"]
    assert await get(category="other") == ["Signed in"]
    # start inclusive, end exclusive, zone-less = UTC
    assert await get(start="2026-11-10T12:01:00", end="2026-11-10T12:03:00") == ["Changed a schedule", "Signed in"]
    assert await get(start="2026-11-10T14:01:00+02:00", end="2026-11-10T12:02:00Z") == ["Changed a schedule"]

    _, sponsor = await _user(client, db_session, UserRole.SPONSOR)
    for path in ("/admin/stations", "/sponsor/campaigns"):
        assert (await client.post(f"{API}/activity/page-view", json={"path": path, "title": path}, headers=sponsor)).status_code == 204
    await activity_log.wait_idle()
    views = (await client.get(f"{API}/activity", params={"category": "view", "q": "Opened /"}, headers=auth_headers)).json()["items"]
    assert [v["detail"] for v in views] == ["/sponsor/campaigns"]


# ── Campaigns via the API (V2-58) ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_campaign_statuses_and_invoice_via_api(client, auth_headers, db_session, monkeypatch):
    """V2-58: with no staff screen, every campaign status and an invoice work through the API."""
    async def fake_send(*a, **k):
        return True

    monkeypatch.setattr("app.services.email_service.send_email", fake_send)
    sponsor = Sponsor(id=uuid.uuid4(), name="Acme", length_seconds=30, audio_file_path="")
    db_session.add(sponsor)
    await db_session.commit()
    r = await client.post(f"{API}/campaigns", json={"name": "Spring", "sponsor_id": str(sponsor.id)}, headers=auth_headers)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    for st in ("in_review", "approved", "active", "paused", "completed", "draft"):
        r = await client.patch(f"{API}/campaigns/{cid}/status", json={"status": st}, headers=auth_headers)
        assert r.status_code == 200 and r.json()["status"] == st, (st, r.text)
    r = await client.post(f"{API}/billing/invoices", json={"sponsor_id": str(sponsor.id), "amount_cents": 1500}, headers=auth_headers)
    assert r.status_code == 201 and r.json()["status"] == "sent"


# ── RDS in engine mode (V2-84) ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rds_follows_the_engine_start_report(client, db_session, monkeypatch):
    """V2-84: in engine mode the line names what the engine reported started, not the
    entry handed over and still pre-loaded; RDS-safe characters; no-store."""
    from app.config import settings
    from app.services import playout_engine

    st = Station(id=uuid.uuid4(), name="Kol B'ramah FM", timezone="UTC")
    a = Asset(id=uuid.uuid4(), title="Yerushalayim", artist="Avraham Fried", duration=200, file_path="https://c/a.mp3", asset_type="music")
    b = Asset(id=uuid.uuid4(), title="Ani Maamin — Live", artist="MBD", duration=200, file_path="https://c/b.mp3", asset_type="music")
    db_session.add_all([st, a, b])
    await db_session.flush()
    e1 = QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=a.id, position=1, status="pending", source="auto")
    e2 = QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=b.id, position=2, status="pending", source="auto")
    db_session.add_all([e1, e2])
    await db_session.commit()
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", str(st.id))
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", "tok")
    playout_engine.reset_state()

    async def no_cmd(*a, **k):
        return True

    import app.api.v1.queue as queue_module

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(playout_engine, "_command", no_cmd)
    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", nothing)
    h = {"X-Playout-Token": "tok"}
    url = f"{API}/stations/{st.id}/rds.txt"

    await client.get(f"{API}/playout/{st.id}/next", headers=h)                 # e1 handed over
    assert (await client.get(url)).text == "Kol B'ramah FM"                     # not on air yet
    await client.post(f"{API}/playout/{st.id}/started", headers=h, json={"entry_id": str(e1.id), "lane": "main"})
    await client.get(f"{API}/playout/{st.id}/next", headers=h)                 # e2 pre-loaded
    r = await client.get(url)
    assert r.text == "Avraham Fried - Yerushalayim" and r.headers["cache-control"] == "no-store"
    await client.post(f"{API}/playout/{st.id}/started", headers=h, json={"entry_id": str(e2.id), "lane": "main"})
    assert (await client.get(url)).text == "MBD - Ani Maamin - Live"
    assert (await client.get(url, params={"max": 10})).text == "MBD - Ani"
    playout_engine.reset_state()


# ── Song request Match (server side) ─────────────────────────────────────────

async def _match_setup(db, status=RequestStatus.PENDING, title="One day"):
    st = Station(id=uuid.uuid4(), name=f"Web {uuid.uuid4().hex[:6]}", timezone="UTC")
    songs = [Asset(id=uuid.uuid4(), title=f"Song {i}", artist="Artist", duration=200.0,
                   file_path=f"https://cdn.example.com/{i}.mp3", asset_type="music") for i in range(3)]
    db.add(st)
    db.add_all(songs)
    req = SongRequest(id=uuid.uuid4(), station_id=st.id, requester_name="Listener", song_title=title, status=status)
    db.add(req)
    await db.commit()
    return st, songs, req


async def _queued(db, st):
    rows = (await db.execute(select(QueueEntry).where(QueueEntry.station_id == st.id).order_by(QueueEntry.position)
                             .execution_options(populate_existing=True))).scalars().all()
    return [str(r.asset_id) for r in rows]


@pytest.mark.asyncio
async def test_match_pending_then_approve(client, auth_headers, db_session):
    """F-MATCH-3: a song picked on a pending request sets its song and it stays pending
    (nothing queued); Approve then queues it."""
    st, songs, req = await _match_setup(db_session)
    t0 = time.perf_counter()
    r = await client.patch(f"{API}/song-requests/{req.id}", json={"asset_id": str(songs[1].id)}, headers=auth_headers)
    assert time.perf_counter() - t0 < limit(1.0)
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["asset_id"] == str(songs[1].id)
    assert await _queued(db_session, st) == []
    r = await client.patch(f"{API}/song-requests/{req.id}", json={"status": "approved"}, headers=auth_headers)
    assert r.json()["status"] == "queued"
    assert await _queued(db_session, st) == [str(songs[1].id)]


@pytest.mark.asyncio
async def test_match_unknown_song_or_request_is_404(client, auth_headers, db_session):
    """F-MATCH-5"""
    _, songs, req = await _match_setup(db_session)
    r = await client.patch(f"{API}/song-requests/{req.id}", json={"asset_id": str(uuid.uuid4())}, headers=auth_headers)
    assert r.status_code == 404 and r.json()["detail"] == "Song not found in the library"
    r = await client.patch(f"{API}/song-requests/{req.id}", json={"asset_id": "not-an-id"}, headers=auth_headers)
    assert r.status_code == 404
    r = await client.patch(f"{API}/song-requests/{uuid.uuid4()}", json={"asset_id": str(songs[0].id)}, headers=auth_headers)
    assert r.status_code == 404 and r.json()["detail"] == "Song request not found"


@pytest.mark.asyncio
async def test_match_on_a_queued_request_only_changes_its_song(client, auth_headers, db_session):
    """F-MATCH-13: a queued request cannot be matched again; the old song stays in Up next."""
    st, songs, req = await _match_setup(db_session)
    await client.patch(f"{API}/song-requests/{req.id}", json={"asset_id": str(songs[0].id), "status": "approved"}, headers=auth_headers)
    assert await _queued(db_session, st) == [str(songs[0].id)]
    r = await client.patch(f"{API}/song-requests/{req.id}", json={"asset_id": str(songs[2].id)}, headers=auth_headers)
    # refused (409): the request keeps its song and Up next stays as it is
    assert r.status_code == 409, r.text
    await db_session.refresh(req)
    assert str(req.asset_id) == str(songs[0].id)
    assert await _queued(db_session, st) == [str(songs[0].id)]


@pytest.mark.asyncio
async def test_match_and_approve_in_one_call_queues_once(client, auth_headers, db_session):
    """F-MATCH-14 (both from pending and from approved-without-a-match)."""
    for start in (RequestStatus.PENDING, RequestStatus.APPROVED):
        st, songs, req = await _match_setup(db_session, status=start)
        r = await client.patch(f"{API}/song-requests/{req.id}", json={"asset_id": str(songs[0].id), "status": "approved"}, headers=auth_headers)
        assert r.status_code == 200 and r.json()["status"] == "queued"
        assert await _queued(db_session, st) == [str(songs[0].id)], start


@pytest.mark.asyncio
async def test_hand_matched_request_counts_like_an_automatic_one(client, auth_headers, db_session):
    """F-MATCH-19: hand matches count in "most requested" and toward the requested category."""
    from app.services.requested_category_service import refresh_requested_category

    st, songs, req = await _match_setup(db_session)
    others = [SongRequest(id=uuid.uuid4(), station_id=st.id, requester_name="L", song_title="x",
                          status=RequestStatus.PLAYED, asset_id=songs[0].id) for _ in range(2)]
    db_session.add_all(others)
    await db_session.commit()
    await client.patch(f"{API}/song-requests/{req.id}", json={"asset_id": str(songs[0].id)}, headers=auth_headers)
    top = (await client.get(f"{API}/song-requests/analytics/top-requested", headers=auth_headers)).json()["top_requested"]
    assert top[0]["asset_id"] == str(songs[0].id) and top[0]["request_count"] == 3
    assert await refresh_requested_category(db_session, threshold=3) == 1
    await db_session.refresh(songs[0])
    # tagged, and its own category is left alone (wiki/song-request-matching.md)
    assert (songs[0].metadata_extra or {}).get("requested") == "yes"


@pytest.mark.asyncio
async def test_match_on_an_engine_station(client, auth_headers, db_session, monkeypatch):
    """F-MATCH-18: a hand-matched request goes right after the song on air. If the engine
    already holds a different next track, the tick flushes it (so the request plays next),
    but never within 15 s of the last flush and never in the last 45 s of the song on air
    (then the held track plays first and the request after it). The wait shown counts
    only what is left of the song on air."""
    from app.config import settings
    from app.services import playout_engine
    from app.services.song_request_service import get_queue_position_info

    st, songs, req = await _match_setup(db_session, status=RequestStatus.APPROVED)
    fill = [Asset(id=uuid.uuid4(), title=f"Fill {i}", artist="F", duration=240.0,
                  file_path=f"https://cdn.example.com/f{i}.mp3", asset_type="music") for i in range(3)]
    db_session.add_all(fill)
    await db_session.flush()
    now = datetime.now(UTC)
    on_air = QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=fill[0].id, position=0, status="playing",
                        source="auto", started_at=now - timedelta(seconds=60))
    cued = QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=fill[1].id, position=1, status="pending", source="auto")
    later = QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=fill[2].id, position=2, status="pending", source="auto")
    db_session.add_all([on_air, cued, later])
    await db_session.commit()
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", str(st.id))
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", "tok")
    playout_engine.reset_state()
    sent = []

    async def cmd(path, payload=None, **_kw):
        sent.append(path)
        return True

    import app.api.v1.queue as queue_module

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(playout_engine, "_command", cmd)
    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", nothing)
    h = {"X-Playout-Token": "tok"}
    nxt = (await client.get(f"{API}/playout/{st.id}/next", headers=h)).json()
    assert nxt["entry_id"] == str(cued.id)

    r = await client.patch(f"{API}/song-requests/{req.id}", json={"asset_id": str(songs[0].id)}, headers=auth_headers)
    assert r.json()["status"] == "queued"
    req_entry = (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == st.id, QueueEntry.source == "request")
                                          .execution_options(populate_existing=True))).scalar_one()
    assert req_entry.position == 1
    info = await get_queue_position_info(db_session, str(st.id), req_entry.position)
    assert info["songs_ahead"] == 1 and info["estimated_wait_minutes"] == 3.0     # 240 s song, 60 s in

    await playout_engine.tick(db_session, st)
    assert sent.count("/flush") == 1
    nxt = (await client.get(f"{API}/playout/{st.id}/next", headers=h)).json()
    assert nxt["entry_id"] == str(req_entry.id)

    # Another head change straight away: no second flush within 15 s
    later_row = await db_session.get(QueueEntry, later.id)
    later_row.position = -1
    await db_session.commit()
    await playout_engine.tick(db_session, st)
    assert sent.count("/flush") == 1
    # 15 s later, but the song on air has under 45 s left: the held track stays
    playout_engine.get_state(st.id).last_auto_flush -= 16
    on_air_row = await db_session.get(QueueEntry, on_air.id)
    on_air_row.started_at = datetime.now(UTC) - timedelta(seconds=210)
    await db_session.commit()
    await playout_engine.tick(db_session, st)
    assert sent.count("/flush") == 1
    playout_engine.reset_state()


# ── Databank ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_treats_percent_and_underscore_as_typed(client, auth_headers, db_session):
    """F-DATABANK-57: % and _ are matched literally, so the count is what was typed."""
    db_session.add_all([
        Asset(id=uuid.uuid4(), title="track_01", duration=100, file_path="https://c/track_01.mp3", asset_type="music"),
        Asset(id=uuid.uuid4(), title="trackA01", duration=100, file_path="https://c/trackA01.mp3", asset_type="music"),
        Asset(id=uuid.uuid4(), title="100% Simcha", duration=100, file_path="https://c/s.mp3", asset_type="music"),
    ])
    await db_session.commit()

    async def titles(s):
        r = await client.get(f"{API}/databank/items", params={"search": s}, headers=auth_headers)
        assert r.status_code == 200
        return r.json()["total"], sorted(i["title"] for i in r.json()["items"])

    assert await titles("track_01") == (1, ["track_01"])
    assert await titles("%") == (1, ["100% Simcha"])
    assert await titles("track") == (2, ["trackA01", "track_01"])


async def _every_station_spot(db, n_stations=1):
    stations = [Station(id=uuid.uuid4(), name=f"S{i}", timezone="UTC", protected=(i == 0)) for i in range(n_stations)]
    sponsor = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, audio_file_path="")
    spot = Asset(id=uuid.uuid4(), title="Bagel spot", duration=30, file_path="https://c/b.mp3", asset_type="spot")
    db.add_all([*stations, sponsor, spot])
    await db.flush()
    sched = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=None,
                         start_date=date.today() - timedelta(days=5), end_date=date.today() + timedelta(days=5))
    db.add(sched)
    await db.commit()
    return stations, spot, sched


@pytest.mark.asyncio
async def test_every_station_end_date_needs_every_station(client, auth_headers, db_session):
    """F-DATABANK-59: a manager not assigned to the protected FM station may not move the
    end date of an "every station" schedule; an admin may."""
    stations, spot, sched = await _every_station_spot(db_session, 2)
    _, mgr = await _user(client, db_session, UserRole.MANAGER, station_ids=[str(stations[1].id)])
    new_end = (date.today() + timedelta(days=30)).isoformat()
    r = await client.patch(f"{API}/databank/spots/{spot.id}/end-date", params={"station_id": str(stations[1].id)},
                           json={"end_date": new_end}, headers=mgr)
    assert r.status_code == 403
    await db_session.refresh(sched)
    assert sched.end_date == date.today() + timedelta(days=5)
    r = await client.patch(f"{API}/databank/spots/{spot.id}/end-date", json={"end_date": new_end}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["updated"] == 1


@pytest.mark.asyncio
async def test_every_station_end_date_is_fast_with_80_stations(client, auth_headers, db_session, monkeypatch):
    """F-DATABANK-61. Each station's rebuild is held at 20 ms, about what one costs on the
    production database (admin-flows measured 1.9-3 s for 81 stations), so the result
    does not depend on how fast this machine's SQLite is."""
    import app.services.spot_schedule_service as sss

    real = sss.reconcile_spot_slots
    calls = []

    async def costly(db, station_id, *a, **k):
        calls.append(station_id)
        await asyncio.sleep(0.02)
        return await real(db, station_id, *a, **k)

    monkeypatch.setattr(sss, "reconcile_spot_slots", costly)
    import app.api.v1.spot_schedules as ss_api
    monkeypatch.setattr(ss_api, "reconcile_spot_slots", costly)
    _, spot, _ = await _every_station_spot(db_session, 80)
    new_end = (date.today() + timedelta(days=30)).isoformat()
    t0 = time.perf_counter()
    r = await client.patch(f"{API}/databank/spots/{spot.id}/end-date", json={"end_date": new_end}, headers=auth_headers)
    took = time.perf_counter() - t0
    assert r.status_code == 200
    # not limit(): the 20 ms per station is simulated, so the S-6 1 s holds on any machine
    assert took < 1.0, f"{took:.2f} s for {len(calls)} station rebuilds inside the request"


@pytest.mark.asyncio
async def test_rds_shows_the_station_name_during_a_live_show(client, db_session, monkeypatch):
    """V2-84: a live show stops the item on air, so the RDS line is the station's name."""
    from app.models.live_show import LiveShow, LiveShowStatus
    from app.services import live_show_service

    st = Station(id=uuid.uuid4(), name="Kol B'ramah Live", timezone="UTC",
                 automation_config={"live_delay_seconds": 0})  # no broadcast delay: the show takes the air at once
    song = Asset(id=uuid.uuid4(), title="Song", artist="Singer", duration=200, file_path="https://c/s.mp3", asset_type="music")
    db_session.add_all([st, song])
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, position=1, status="playing",
                              source="auto", started_at=datetime.now(UTC) - timedelta(seconds=30)))
    now = datetime.now(UTC)
    show = LiveShow(id=uuid.uuid4(), station_id=st.id, title="Motzei Shabbos", status=LiveShowStatus.SCHEDULED,
                    scheduled_start=now, scheduled_end=now + timedelta(hours=1))
    db_session.add(show)
    await db_session.commit()
    url = f"{API}/stations/{st.id}/rds.txt"
    assert (await client.get(url)).text == "Singer - Song"
    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    assert (await client.get(url)).text == "Kol B'ramah Live"
