"""Activity log: every change by a signed-in user is recorded with who, what, where and before/after."""
import pytest

import app.api.v1.queue as queue_module
from app.services import activity_log
from tests.test_queue_advance import _station_with_two_tracks


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    queue_module._fire_requests.clear()


async def _activity(client, auth_headers, **params):
    await activity_log.wait_idle()
    res = await client.get("/api/v1/activity", params=params, headers=auth_headers)
    assert res.status_code == 200, res.text
    return res.json()


IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")


@pytest.mark.asyncio
async def test_sign_in_and_failed_sign_in_are_recorded(client, admin_user, auth_headers):
    bad = await client.post("/api/v1/auth/login", json={"email": admin_user.email, "password": "nope"})
    assert bad.status_code == 401
    data = await _activity(client, auth_headers, category="signin")
    labels = [r["label"] for r in data["items"]]
    assert "Signed in" in labels and "Sign-in failed" in labels
    failed = next(r for r in data["items"] if r["label"] == "Sign-in failed")
    assert failed["user_id"] == str(admin_user.id)  # filter by person finds failed attempts too
    assert failed["status_code"] == 401


@pytest.mark.asyncio
async def test_station_change_shows_before_and_after_and_device(client, auth_headers):
    headers = {**auth_headers, "User-Agent": IPHONE, "X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
    created = (await client.post("/api/v1/stations", json={"name": "Kol B'ramah Test"}, headers=headers)).json()
    res = await client.put(f"/api/v1/stations/{created['id']}", json={"timezone": "Asia/Jerusalem"}, headers=headers)
    assert res.status_code == 200

    data = await _activity(client, auth_headers, station_id=created["id"])
    labels = [r["label"] for r in data["items"]]
    assert labels[:2] == ["Changed station", "Added station"]
    change = data["items"][0]
    assert change["station_name"] == "Kol B'ramah Test"
    assert change["detail"] == "Kol B'ramah Test"
    assert change["device"] == "iPhone · Safari"
    assert change["ip_address"] == "203.0.113.7"
    assert change["user_email"] == "testadmin@test.com"
    fields = change["changes"]["objects"][0]["fields"]
    assert fields["timezone"]["old"] == "UTC"
    assert fields["timezone"]["new"] == "Asia/Jerusalem"
    assert "updated_at" not in fields


@pytest.mark.asyncio
async def test_control_room_next_is_named_and_tied_to_the_station(client, auth_headers, db_session):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    from app.models.asset import Asset
    playing_title = (await db_session.get(Asset, playing.asset_id)).title
    pending_title = (await db_session.get(Asset, pending.asset_id)).title
    assert (await client.post(f"/api/v1/stations/{station.id}/queue/skip", headers=auth_headers)).status_code == 200

    data = await _activity(client, auth_headers, category="control")
    assert data["total"] == 1
    row = data["items"][0]
    assert row["label"] == "Pressed Next"
    assert row["detail"] == f"{playing_title} → {pending_title}"
    assert row["station_id"] == str(station.id)
    assert row["status_code"] == 200


@pytest.mark.asyncio
async def test_refused_actions_are_recorded_and_passwords_are_never_stored(client, auth_headers, admin_user):
    res = await client.post("/api/v1/users", json={
        "email": "dj@test.com", "password": "secret-pass-1", "role": "dj", "display_name": "Moshe",
    }, headers=auth_headers)
    assert res.status_code in (200, 201), res.text
    data = await _activity(client, auth_headers, category="users")
    row = data["items"][0]
    assert row["label"] == "Added a user"
    assert "secret-pass-1" not in str(row)
    assert row["changes"]["request"]["password"] == "•••"

    # The DJ tries something only managers may do
    login = await client.post("/api/v1/auth/login", json={"email": "dj@test.com", "password": "secret-pass-1"})
    dj_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.post("/api/v1/stations", json={"name": "Nope"}, headers=dj_headers)).status_code == 403

    failed = await _activity(client, auth_headers, failed_only="true")
    refused = next(r for r in failed["items"] if r["label"] == "Added station")
    assert refused["status_code"] == 403
    assert refused["user_email"] == "dj@test.com"
    assert refused["user_name"] == "Moshe"


@pytest.mark.asyncio
async def test_page_views_search_paging_and_live_cursor(client, auth_headers):
    for path, title in [("/admin/dashboard", "Control room"), ("/admin/playlists", "Playlists")]:
        r = await client.post("/api/v1/activity/page-view", json={"path": path, "title": title}, headers=auth_headers)
        assert r.status_code == 204
    data = await _activity(client, auth_headers, category="view")
    assert [r["label"] for r in data["items"]] == ["Opened Playlists", "Opened Control room"]

    assert (await _activity(client, auth_headers, q="playlists", category="view"))["total"] == 1
    assert (await _activity(client, auth_headers, include_views="false", category="view"))["total"] == 0
    page2 = await _activity(client, auth_headers, category="view", skip=1, limit=1)
    assert page2["items"][0]["label"] == "Opened Control room"

    newest = data["items"][0]["created_at"]
    await client.post("/api/v1/activity/page-view", json={"path": "/admin/users", "title": "Users"}, headers=auth_headers)
    live = await _activity(client, auth_headers, after=newest)
    assert [r["label"] for r in live["items"]] == ["Opened Users"]
    assert live["total"] is None


@pytest.mark.asyncio
async def test_only_admins_can_read_activity(client, auth_headers):
    await client.post("/api/v1/users", json={
        "email": "mgr@test.com", "password": "secret-pass-2", "role": "manager",
    }, headers=auth_headers)
    login = await client.post("/api/v1/auth/login", json={"email": "mgr@test.com", "password": "secret-pass-2"})
    mgr = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/api/v1/activity", headers=mgr)).status_code == 403
    assert (await client.get("/api/v1/activity/listeners", headers=mgr)).status_code == 403


@pytest.mark.asyncio
async def test_listener_sessions_list(client, auth_headers, db_session):
    from app.models.listener_session import ListenerSession
    from app.models.station import Station

    station = Station(name="Kol B'ramah Main")
    db_session.add(station)
    await db_session.flush()
    db_session.add(ListenerSession(station_id=station.id, session_key="abc", ip_address="198.51.100.4",
                                   city="Lakewood", region="New Jersey", country="United States",
                                   duration_seconds=1250, user_agent=IPHONE))
    await db_session.commit()
    res = await client.get("/api/v1/activity/listeners", params={"q": "lakewood"}, headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["station_name"] == "Kol B'ramah Main"
    assert item["location"] == "Lakewood, New Jersey, United States"
    assert item["device"] == "iPhone · Safari"
    assert item["duration_seconds"] == 1250


def test_names_for_unlisted_routes():
    assert activity_log.describe("POST", "/assets/{asset_id}/trim") == ("Trim (library item)", "library")
    assert activity_log.describe("PATCH", "/schedules/blocks/{block_id}") == ("Changed schedule block", "schedule")
    assert activity_log.describe("DELETE", "/playlists/{template_id}") == ("Deleted playlist", "schedule")
    assert activity_log.describe("POST", "/stations/{station_id}/queue/skip") == ("Pressed Next", "control")
    assert activity_log.describe_device(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ) == "Windows · Chrome"


@pytest.mark.asyncio
async def test_next_that_changes_nothing_says_why(client, auth_headers, db_session):
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    playing.source = "block"  # a timed item plays to the end
    await db_session.commit()
    from app.models.asset import Asset
    title = (await db_session.get(Asset, playing.asset_id)).title
    assert (await client.post(f"/api/v1/stations/{station.id}/queue/skip", headers=auth_headers)).status_code == 200
    row = (await _activity(client, auth_headers, category="control"))["items"][0]
    assert row["label"] == "Pressed Next"
    assert row["detail"] == f"Timed item plays to the end: {title}"


@pytest.mark.asyncio
async def test_people_summary_counts_each_person(client, auth_headers, admin_user):
    for path in ("/admin/dashboard", "/admin/playlists"):
        await client.post("/api/v1/activity/page-view", json={"path": path, "title": path}, headers=auth_headers)
    await client.post("/api/v1/stations", json={"name": "Kol B'ramah People"}, headers=auth_headers)
    await client.post("/api/v1/auth/login", json={"email": "stranger@test.com", "password": "x"})
    await activity_log.wait_idle()
    res = await client.get("/api/v1/activity/people", headers=auth_headers)
    assert res.status_code == 200
    people = {p["user_email"]: p for p in res.json()["people"]}
    me = people["testadmin@test.com"]
    assert me["user_id"] == str(admin_user.id)
    assert me["views"] == 2
    assert me["actions"] == 2  # signed in + added station
    assert me["by_category"]["settings"] == 1
    stranger = people["stranger@test.com"]
    assert stranger["user_id"] is None and stranger["failed"] == 1
    # The stranger's own timeline
    rows = await _activity(client, auth_headers, user_email="stranger@test.com")
    assert [r["label"] for r in rows["items"]] == ["Sign-in failed"]


@pytest.mark.asyncio
async def test_server_errors_keep_cors_headers():
    from app.main import create_app
    from httpx import ASGITransport, AsyncClient

    app = create_app()

    @app.get("/api/v1/_boom")
    async def _boom():
        raise RuntimeError("database went away")

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as ac:
        res = await ac.get("/api/v1/_boom", headers={"Origin": "https://kbrlive.com"})
    assert res.status_code == 500
    assert res.headers.get("access-control-allow-origin") == "https://kbrlive.com"
