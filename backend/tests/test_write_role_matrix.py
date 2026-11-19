"""Write role matrix: every POST/PUT/PATCH/DELETE route, sent as every role
(admin, manager, dj, viewer, sponsor) and with no login.

The table below is the policy, written out by hand from the guards in
app/core/dependencies.py. A role outside a route's set must get 403 (no login:
401/403); a role inside it must get past the guard (anything but 401/403: the
request carries a broken body and impossible ids, so allowed roles mostly see
422 or 404 and nothing is changed).

A new write route that is not in the table fails `test_every_write_route_is_classified`,
so nobody can add a write without deciding who may make it.

Station permissions (User.station_ids, Station.protected) are in
test_station_write_permissions.py; this file checks roles only.
"""
import time
import uuid

import pytest
from fastapi.routing import APIRoute

from tests.route_list import app_routes
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.user import User, UserRole

ADMIN = frozenset({"admin"})
MANAGERS = frozenset({"admin", "manager"})
ON_AIR = frozenset({"admin", "manager", "dj"})
STAFF = frozenset({"admin", "manager", "dj", "viewer"})
SPONSOR = frozenset({"sponsor"})
SPONSOR_OR_MANAGERS = frozenset({"admin", "manager", "sponsor"})
ANY_LOGIN = frozenset({"admin", "manager", "dj", "viewer", "sponsor"})
ROLES = ["admin", "manager", "dj", "viewer", "sponsor"]

# Reachable with no staff login on purpose (each has its own test elsewhere:
# test_unguarded_endpoints.py, test_crm_*, test_bug_reports.py ...).
PUBLIC_WRITES = {
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),
    ("POST", "/auth/seed"),  # refuses once an admin exists
    ("POST", "/billing/webhook"),  # Stripe signature
    ("POST", "/bug-reports"),  # rate limited
    ("POST", "/call-in/{station_id}"),
    ("POST", "/crm/login"),
    ("POST", "/crm/register"),
    ("POST", "/crm/raffles/{raffle_id}/enter"),  # CRM member PIN
    ("POST", "/crm/rate"),  # CRM member PIN
    ("DELETE", "/crm/rate/{asset_id}"),  # CRM member PIN
    ("POST", "/listeners/disconnect"),
    ("POST", "/listeners/heartbeat"),
    ("POST", "/listeners/leave"),
    ("POST", "/live-shows/twilio/inbound"),  # Twilio webhook
    ("POST", "/live-shows/twilio/status"),  # Twilio webhook
    ("POST", "/song-requests"),
    ("POST", "/playout/{station_id}/started"),
    ("POST", "/playout/{station_id}/hello"),
    ("POST", "/playout/{station_id}/commands/poll"),
    ("POST", "/playout/{station_id}/alert"),  # playout token
    ("POST", "/emergency/device/activate"),  # EMERGENCY_API_TOKEN (the EAS box)
    ("POST", "/emergency/device/{alert_id}/stop"),  # EMERGENCY_API_TOKEN
}

# Allowed roles are not sent to these: with no input they would really run
# (wipe and re-seed the library, start or stop the scheduler loop, ffmpeg work).
# Refusals are still checked.
NO_ALLOWED_RUN = {
    ("POST", "/auth/seed-all"),
    ("POST", "/auth/seed-assets"),
    ("POST", "/auth/seed-reset"),
    ("POST", "/scheduler/start"),
    ("POST", "/scheduler/stop"),
    ("POST", "/holidays/ensure-silence-asset"),
    ("POST", "/assets/backfill-durations"),
    ("POST", "/assets/backfill-release-dates"),
    ("POST", "/assets/bulk-analyze"),
    ("POST", "/assets/bulk-auto-trim"),
}

WRITE_POLICY = {
    ("POST", "/auth/migrate"): ADMIN,  # was public; DDL must not be anonymous
    ("POST", "/activity/page-view"): ANY_LOGIN,
    ("POST", "/ai-emails/draft"): MANAGERS,
    ("POST", "/ai-emails/send"): MANAGERS,
    ("POST", "/alerts/resolve-all"): MANAGERS,
    ("POST", "/emergency/activate"): MANAGERS,
    ("POST", "/emergency/{alert_id}/stop"): MANAGERS,
    ("DELETE", "/alerts/{alert_id}"): ADMIN,
    ("PATCH", "/alerts/{alert_id}/reopen"): MANAGERS,
    ("PATCH", "/alerts/{alert_id}/resolve"): MANAGERS,
    ("POST", "/archives"): MANAGERS,
    ("DELETE", "/archives/{archive_id}"): MANAGERS,
    ("PATCH", "/archives/{archive_id}"): MANAGERS,
    ("POST", "/asset-types"): MANAGERS,
    ("DELETE", "/asset-types/{asset_type_id}"): MANAGERS,
    ("PATCH", "/asset-types/{asset_type_id}"): MANAGERS,
    ("POST", "/assets/backfill-durations"): MANAGERS,
    ("POST", "/assets/backfill-release-dates"): MANAGERS,
    ("POST", "/assets/bulk-analyze"): MANAGERS,
    ("POST", "/assets/bulk-auto-trim"): MANAGERS,
    ("PATCH", "/assets/bulk-category"): MANAGERS,
    ("POST", "/assets/bulk-create"): MANAGERS,
    ("POST", "/assets/refresh-requested-category"): MANAGERS,
    ("POST", "/assets/upload"): ON_AIR,  # DJs save Recording Studio takes (owner decision)
    ("DELETE", "/assets/{asset_id}"): MANAGERS,
    ("PATCH", "/assets/{asset_id}"): MANAGERS,
    ("POST", "/assets/{asset_id}/analyze-audio"): MANAGERS,
    ("POST", "/assets/{asset_id}/auto-enhance"): MANAGERS,
    ("POST", "/assets/{asset_id}/clip"): MANAGERS,
    ("POST", "/assets/{asset_id}/detect-audience"): STAFF,
    ("POST", "/assets/{asset_id}/detect-silence"): STAFF,
    ("POST", "/assets/{asset_id}/do-not-play"): MANAGERS,
    ("POST", "/assets/{asset_id}/station-block"): MANAGERS,
    ("POST", "/assets/{asset_id}/enhance"): MANAGERS,
    ("POST", "/assets/{asset_id}/enhance-preview"): STAFF,
    ("PATCH", "/assets/{asset_id}/request-settings"): MANAGERS,
    ("POST", "/assets/{asset_id}/restore-original"): MANAGERS,
    ("POST", "/assets/{asset_id}/transcode"): MANAGERS,
    ("POST", "/assets/{asset_id}/trim"): MANAGERS,
    ("POST", "/assets/{asset_id}/remove-silence"): MANAGERS,
    ("POST", "/auth/seed-all"): ADMIN,
    ("POST", "/auth/seed-assets"): ADMIN,
    ("POST", "/auth/seed-reset"): ADMIN,
    ("POST", "/billing/invoices"): MANAGERS,
    ("POST", "/billing/invoices/{invoice_id}/checkout"): SPONSOR,
    ("PATCH", "/bug-reports/{report_id}"): ADMIN,
    ("POST", "/bug-reports/{report_id}/resend"): ADMIN,
    ("POST", "/campaigns"): SPONSOR_OR_MANAGERS,
    ("PATCH", "/campaigns/{campaign_id}"): SPONSOR_OR_MANAGERS,
    ("POST", "/campaigns/{campaign_id}/comments"): SPONSOR_OR_MANAGERS,
    ("POST", "/campaigns/{campaign_id}/drafts"): SPONSOR_OR_MANAGERS,
    ("POST", "/campaigns/{campaign_id}/drafts/{draft_id}/upload-audio"): SPONSOR_OR_MANAGERS,
    ("PATCH", "/campaigns/{campaign_id}/status"): MANAGERS,
    ("POST", "/categories"): MANAGERS,
    ("DELETE", "/categories/{category_id}"): MANAGERS,
    ("PATCH", "/categories/{category_id}"): MANAGERS,
    ("POST", "/channels"): MANAGERS,
    ("DELETE", "/channels/{channel_id}"): MANAGERS,
    ("PUT", "/channels/{channel_id}"): MANAGERS,
    ("DELETE", "/crm/members/{member_id}"): MANAGERS,
    ("POST", "/crm/raffles"): MANAGERS,
    ("PATCH", "/crm/raffles/{raffle_id}"): MANAGERS,
    ("POST", "/crm/raffles/{raffle_id}/draw"): MANAGERS,
    ("DELETE", "/holiday-music/items/{item_id}"): MANAGERS,
    ("PATCH", "/holiday-music/items/{item_id}"): MANAGERS,
    ("POST", "/holiday-music/{holiday}/items"): MANAGERS,
    ("PUT", "/holiday-music/{holiday}/settings"): MANAGERS,
    ("POST", "/holidays"): MANAGERS,
    ("POST", "/holidays/auto-generate"): MANAGERS,
    ("POST", "/holidays/ensure-silence-asset"): MANAGERS,
    ("POST", "/holidays/preview"): MANAGERS,
    ("DELETE", "/holidays/{holiday_id}"): MANAGERS,
    ("PUT", "/holidays/{holiday_id}"): MANAGERS,
    ("POST", "/icecast/{station_id}/start"): ADMIN,
    ("POST", "/icecast/{station_id}/stop"): ADMIN,
    ("POST", "/live-shows"): ON_AIR,
    ("DELETE", "/live-shows/{show_id}"): ADMIN,
    ("PATCH", "/live-shows/{show_id}"): ON_AIR,
    ("PATCH", "/live-shows/{show_id}/calls/{call_id}"): ON_AIR,
    ("PUT", "/live-shows/{show_id}/calls/{call_id}/caller"): ON_AIR,
    ("POST", "/live-shows/{show_id}/calls/{call_id}/lock"): ON_AIR,
    ("POST", "/live-shows/{show_id}/chat"): ON_AIR,
    ("PUT", "/caller-profiles/{profile_id}"): ON_AIR,
    ("POST", "/live-shows/{show_id}/calls/{call_id}/approve"): ON_AIR,
    ("POST", "/live-shows/{show_id}/dump"): ON_AIR,
    ("POST", "/live-shows/{show_id}/calls/{call_id}/hold"): ON_AIR,
    ("POST", "/live-shows/{show_id}/calls/next"): ON_AIR,
    ("POST", "/live-shows/{show_id}/delay"): ON_AIR,
    ("POST", "/live-shows/{show_id}/calls/{call_id}/end-call"): ON_AIR,
    ("POST", "/live-shows/{show_id}/calls/{call_id}/on-air"): ON_AIR,
    ("POST", "/live-shows/{show_id}/calls/{call_id}/reject"): ON_AIR,
    ("POST", "/live-shows/{show_id}/end"): ON_AIR,
    ("POST", "/live-shows/{show_id}/start"): ON_AIR,
    ("DELETE", "/now-playing/{station_id}"): ON_AIR,
    ("PATCH", "/now-playing/{station_id}"): ON_AIR,
    ("POST", "/playlists"): MANAGERS,
    ("PUT", "/playlists/generation"): MANAGERS,
    ("POST", "/playlists/generation/regenerate"): MANAGERS,
    ("POST", "/playlists/slots"): MANAGERS,
    ("DELETE", "/playlists/slots/{slot_id}"): MANAGERS,
    ("PATCH", "/playlists/slots/{slot_id}"): MANAGERS,
    ("DELETE", "/playlists/{template_id}"): MANAGERS,
    ("PATCH", "/playlists/{template_id}"): MANAGERS,
    ("POST", "/program-blocks"): MANAGERS,
    ("DELETE", "/program-blocks/{block_id}"): MANAGERS,
    ("PUT", "/program-blocks/{block_id}"): MANAGERS,
    ("POST", "/reviews/assets/{asset_id}/comment"): STAFF,
    ("PATCH", "/reviews/items/{item_id}"): STAFF,
    ("POST", "/reviews/queues"): MANAGERS,
    ("POST", "/reviews/queues/{queue_id}/batch-update"): MANAGERS,
    ("POST", "/rules"): ADMIN,
    ("DELETE", "/rules/{rule_id}"): ADMIN,
    ("PUT", "/rules/{rule_id}"): ADMIN,
    ("POST", "/scheduler/start"): MANAGERS,
    ("POST", "/scheduler/stop"): MANAGERS,
    ("POST", "/schedules"): MANAGERS,
    ("POST", "/schedules/"): MANAGERS,
    ("POST", "/schedules/blocks"): MANAGERS,
    ("DELETE", "/schedules/blocks/{block_id}"): MANAGERS,
    ("PATCH", "/schedules/blocks/{block_id}"): MANAGERS,
    ("POST", "/schedules/blocks/{block_id}/set-prerecorded"): MANAGERS,
    ("POST", "/schedules/look-ahead/settle"): MANAGERS,
    ("POST", "/schedules/playlist-entries"): MANAGERS,
    ("DELETE", "/schedules/playlist-entries/{entry_id}"): MANAGERS,
    ("PATCH", "/schedules/playlist-entries/{entry_id}"): MANAGERS,
    ("DELETE", "/schedules/{schedule_id}"): MANAGERS,
    ("PATCH", "/schedules/{schedule_id}"): MANAGERS,
    ("PUT", "/site-release"): ADMIN,
    ("DELETE", "/site-release/push"): ADMIN,
    ("POST", "/site-release/push"): ADMIN,
    ("DELETE", "/song-requests/{request_id}"): MANAGERS,
    ("PATCH", "/song-requests/{request_id}"): MANAGERS,
    ("POST", "/sponsors"): MANAGERS,
    ("DELETE", "/sponsors/{sponsor_id}"): MANAGERS,
    ("PUT", "/sponsors/{sponsor_id}"): MANAGERS,
    ("POST", "/sponsorship/copy-review"): SPONSOR_OR_MANAGERS,
    ("PUT", "/sponsorship/rules"): MANAGERS,
    ("POST", "/spot-recordings"): MANAGERS,
    ("DELETE", "/spot-recordings/{recording_id}"): MANAGERS,
    ("PUT", "/spot-recordings/{recording_id}"): MANAGERS,
    ("POST", "/spot-recordings/{recording_id}/approve"): MANAGERS,
    ("POST", "/spot-recordings/{recording_id}/audio"): MANAGERS,
    ("POST", "/spot-recordings/{recording_id}/generate"): MANAGERS,
    ("POST", "/spot-recordings/{recording_id}/reject"): MANAGERS,
    ("POST", "/spot-schedules"): MANAGERS,
    ("POST", "/rds/messages"): MANAGERS,
    ("PATCH", "/rds/messages/{message_id}"): MANAGERS,
    ("DELETE", "/rds/messages/{message_id}"): MANAGERS,
    ("PUT", "/rds/stations/{station_id}/settings"): MANAGERS,
    ("POST", "/rds/check"): STAFF,  # read-only wording check for the editor
    ("PUT", "/spot-schedules/timing-defaults"): MANAGERS,
    ("DELETE", "/spot-schedules/{schedule_id}"): MANAGERS,
    ("PUT", "/spot-schedules/{schedule_id}"): MANAGERS,
    ("POST", "/stations"): MANAGERS,
    ("DELETE", "/stations/{station_id}"): MANAGERS,
    ("PUT", "/stations/{station_id}"): MANAGERS,
    ("POST", "/stations/{station_id}/controls/enqueue"): ON_AIR,
    ("POST", "/stations/{station_id}/controls/pause"): ON_AIR,
    ("POST", "/stations/{station_id}/controls/play"): ON_AIR,
    ("POST", "/stations/{station_id}/controls/play-now"): ON_AIR,
    ("POST", "/stations/{station_id}/controls/stop"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/add"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/bulk-add"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/fire-now"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/force-replenish"): MANAGERS,
    ("POST", "/stations/{station_id}/queue/insert"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/insert-weather"): MANAGERS,
    ("POST", "/stations/{station_id}/queue/loop"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/mix-over"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/move-down"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/move-up"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/play-next"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/preview-weather"): STAFF,
    ("PUT", "/stations/{station_id}/queue/reorder"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/reorder-dnd"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/schedule-hourly"): MANAGERS,
    ("POST", "/stations/{station_id}/queue/skip"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/modes"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/panic"): ON_AIR,
    ("PATCH", "/databank/spots/{asset_id}/end-date"): MANAGERS,
    ("POST", "/stations/{station_id}/queue/start"): ON_AIR,
    ("DELETE", "/stations/{station_id}/queue/{entry_id}"): ON_AIR,
    ("POST", "/stations/{station_id}/queue/{entry_id}/skip-sponsorship"): ON_AIR,
    ("POST", "/studio/mix"): MANAGERS,
    ("PUT", "/time-announcements/{hour}"): MANAGERS,
    ("POST", "/users"): ADMIN,
    ("PATCH", "/users/me/preferences"): ANY_LOGIN,
    ("DELETE", "/users/{user_id}"): ADMIN,
    ("PUT", "/users/{user_id}"): ADMIN,
    ("POST", "/weather-readouts/"): MANAGERS,
    ("DELETE", "/weather-readouts/{readout_id}"): MANAGERS,
    ("PATCH", "/weather-readouts/{readout_id}"): MANAGERS,
    ("POST", "/weather-readouts/{readout_id}/queue"): MANAGERS,
    ("POST", "/weather-readouts/{readout_id}/regenerate"): MANAGERS,
    ("PUT", "/weather-spots/{spot_date}"): MANAGERS,
}

# Every refused or allowed answer must come back quickly (checklist S-3: 300 ms
# server time, 1 s round trip). The test client has no network, so this is server
# time: the typical answer must be under 300 ms and none may take 1 s.
FAST = 0.3
SLOWEST = 1.0


def _write_routes():
    from app.main import create_app

    out = set()
    for r in app_routes(create_app()):
        if isinstance(r.route, APIRoute) and r.path.startswith("/api/v1/"):
            for m in r.methods:
                if m in ("POST", "PUT", "PATCH", "DELETE"):
                    out.add((m, r.path[len("/api/v1"):]))
    return out


def test_every_write_route_is_classified():
    routes = _write_routes()
    known = set(WRITE_POLICY) | PUBLIC_WRITES
    missing = sorted(routes - known)
    stale = sorted(known - routes)
    assert not missing, f"write routes with no role policy in this test: {missing}"
    assert not stale, f"policy rows for routes that no longer exist: {stale}"


def _fill(path: str) -> str:
    # Impossible ids and an empty JSON body: an allowed role is stopped by validation (422) or "not
    # found" (404) and nothing is changed. Guards run before validation.
    out = []
    for part in path.split("/"):
        out.append("not-an-id" if part.startswith("{") else part)
    return "/api/v1" + "/".join(out)


async def _tokens(db: AsyncSession) -> dict[str, dict]:
    headers = {}
    for role in ROLES:
        u = User(id=uuid.uuid4(), email=f"matrix-{role}@test.com", hashed_password="x",
                 role=UserRole(role), is_active=True)
        db.add(u)
        headers[role] = {"Authorization": f"Bearer {create_access_token(str(u.id))}"}
    await db.commit()
    return headers


async def _send(client: AsyncClient, method: str, url: str, headers: dict):
    started = time.perf_counter()
    r = await client.request(method, url, content=b"{}",
                             headers={**headers, "content-type": "application/json"})
    return r, time.perf_counter() - started


@pytest.mark.asyncio
async def test_write_role_matrix(client: AsyncClient, db_session: AsyncSession):
    headers = await _tokens(db_session)
    wrong = []
    slow = []
    times = []
    for (method, path), allowed in sorted(WRITE_POLICY.items()):
        url = _fill(path)
        for role in ROLES:
            if role in allowed and (method, path) in NO_ALLOWED_RUN:
                continue
            r, took = await _send(client, method, url, headers[role])
            if role in allowed:
                if r.status_code >= 500:  # a bad id must be 404/422, never a crash
                    wrong.append(f"{role} got {r.status_code} on {method} {path}")
                if r.status_code in (401, 403):
                    wrong.append(f"{role} refused on {method} {path}: {r.status_code} {r.text[:120]}")
            elif r.status_code != 403:
                wrong.append(f"{role} NOT refused on {method} {path}: {r.status_code} {r.text[:120]}")
            times.append(took)
            if took > SLOWEST:
                slow.append(f"{role} {method} {path}: {took * 1000:.0f} ms")
        r, _ = await _send(client, method, url, {})
        if r.status_code not in (401, 403):
            wrong.append(f"no login NOT refused on {method} {path}: {r.status_code}")
    assert not wrong, "\n".join(wrong)
    assert not slow, "slow answers:\n" + "\n".join(slow)
    times.sort()
    assert times[len(times) // 2] < FAST, f"median answer {times[len(times) // 2] * 1000:.0f} ms"


@pytest.mark.asyncio
async def test_refusal_says_not_allowed(client: AsyncClient, db_session: AsyncSession):
    """The frontend's ViewOnlyNotice keys on 403; the body must explain it in words."""
    headers = await _tokens(db_session)
    r = await client.post("/api/v1/rules", json={}, headers=headers["viewer"])
    assert r.status_code == 403
    assert r.json()["detail"], r.text


@pytest.mark.asyncio
async def test_inactive_user_is_refused_on_writes(client: AsyncClient, db_session: AsyncSession):
    u = User(id=uuid.uuid4(), email="gone@test.com", hashed_password="x", role=UserRole.ADMIN, is_active=False)
    db_session.add(u)
    await db_session.commit()
    h = {"Authorization": f"Bearer {create_access_token(str(u.id))}"}
    r = await client.post("/api/v1/stations", json={"name": "x"}, headers=h)
    assert r.status_code == 401
