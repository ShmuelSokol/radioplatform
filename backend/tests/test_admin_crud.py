"""Create / read / change / delete round trips for the admin screens' routers,
with their validation errors, each save timed (checklist S-6: under 1 s).

Covers categories, asset types, channels, rules, users (+ preferences, public
hosts), archives, alerts, song requests, live shows, playlists, schedule
playlist entries and pre-recorded blocks, timeline preview, campaigns and
billing (sponsor isolation), reviews, analytics, listeners, scheduler.
"""
import time
import uuid
from datetime import datetime, time as dtime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.asset import Asset
from app.models.station import Station
from app.models.user import User, UserRole
from tests.timing import limit as ci_limit

SAVE = 1.0  # S-6
READ = 0.3  # S-4


async def _headers(db, role=UserRole.ADMIN, **kw):
    u = User(id=uuid.uuid4(), email=f"{role.value}-{uuid.uuid4().hex[:6]}@t.com", hashed_password="x",
             role=role, is_active=True, **kw)
    db.add(u)
    await db.commit()
    return u, {"Authorization": f"Bearer {create_access_token(str(u.id))}"}


async def timed(coro, limit=SAVE):
    t = time.perf_counter()
    r = await coro
    took = time.perf_counter() - t
    # Shared CI runners are 2-3x slower and noisy: same allowance as every other speed test
    assert took < ci_limit(limit), f"{r.request.method} {r.request.url.path} took {took * 1000:.0f} ms"
    return r


async def _station(db, **kw):
    st = Station(id=uuid.uuid4(), name=kw.pop("name", f"St {uuid.uuid4().hex[:4]}"), timezone="UTC", **kw)
    db.add(st)
    await db.commit()
    return st


async def _asset(db, **kw):
    a = Asset(id=uuid.uuid4(), title=kw.pop("title", "Song"), file_path=kw.pop("file_path", "s.mp3"),
              duration=kw.pop("duration", 180), asset_type=kw.pop("asset_type", "music"), **kw)
    db.add(a)
    await db.commit()
    return a


@pytest.mark.asyncio
@pytest.mark.parametrize("base", ["/api/v1/categories", "/api/v1/asset-types"])
async def test_named_lists_round_trip(client: AsyncClient, db_session: AsyncSession, base):
    """B-CATEGORIES-2..4, B-ASSETTYPES-2..4"""
    _, h = await _headers(db_session, UserRole.MANAGER)
    assert (await client.post(base, json={}, headers=h)).status_code == 422
    r = await timed(client.post(base, json={"name": "Niggunim"}, headers=h))
    assert r.status_code == 201, r.text
    item = r.json()
    dup = await client.post(base, json={"name": "Niggunim"}, headers=h)
    assert dup.status_code in (400, 409), dup.text
    r = await timed(client.patch(f"{base}/{item['id']}", json={"name": "Nigunim"}, headers=h))
    assert r.status_code == 200 and r.json()["name"] == "Nigunim"
    names = [x["name"] for x in (await timed(client.get(base, headers=h), READ)).json()]
    assert "Nigunim" in names
    assert (await timed(client.delete(f"{base}/{item['id']}", headers=h))).status_code == 204
    assert (await client.delete(f"{base}/{item['id']}", headers=h)).status_code == 404
    assert (await client.patch(f"{base}/{uuid.uuid4()}", json={"name": "x"}, headers=h)).status_code == 404


@pytest.mark.asyncio
async def test_channels_round_trip(client: AsyncClient, db_session: AsyncSession):
    """B-CHANNELS-1..4"""
    st = await _station(db_session)
    _, h = await _headers(db_session, UserRole.MANAGER)
    assert (await client.post("/api/v1/channels", json={"channel_name": "HD"}, headers=h)).status_code == 422
    r = await timed(client.post("/api/v1/channels", json={"channel_name": "HD", "station_id": str(st.id)}, headers=h))
    assert r.status_code == 201, r.text
    ch = r.json()
    r = await timed(client.put(f"/api/v1/channels/{ch['id']}", json={"bitrate": 64}, headers=h))
    assert r.status_code == 200 and r.json()["bitrate"] == 64
    listed = (await client.get("/api/v1/channels", params={"station_id": str(st.id)})).json()  # public
    assert [c["channel_name"] for c in listed] == ["HD"]
    assert (await timed(client.delete(f"/api/v1/channels/{ch['id']}", headers=h))).status_code == 204
    assert (await client.get("/api/v1/channels", params={"station_id": str(st.id)})).json() == []
    assert (await client.put(f"/api/v1/channels/{ch['id']}", json={"bitrate": 1}, headers=h)).status_code == 404


@pytest.mark.asyncio
async def test_rules_round_trip_and_preview(client: AsyncClient, db_session: AsyncSession):
    """B-RULES-2..5"""
    _, admin = await _headers(db_session)
    _, viewer = await _headers(db_session, UserRole.VIEWER)
    assert (await client.post("/api/v1/rules", json={"description": "no name"}, headers=admin)).status_code == 422
    r = await timed(client.post("/api/v1/rules", json={"name": "Music all day", "rule_type": "rotation",
                                                       "asset_type": "music"}, headers=admin))
    assert r.status_code == 201, r.text
    rule = r.json()
    r = await timed(client.put(f"/api/v1/rules/{rule['id']}", json={"hour_start": 6, "hour_end": 22}, headers=admin))
    assert r.status_code == 200 and (r.json()["hour_start"], r.json()["hour_end"]) == (6, 22)
    r = await timed(client.get("/api/v1/rules/preview", params={"date": "2030-01-07"}, headers=viewer), 2.0)
    assert r.status_code == 200, r.text
    assert (await timed(client.delete(f"/api/v1/rules/{rule['id']}", headers=admin))).status_code == 204
    assert (await client.put(f"/api/v1/rules/{rule['id']}", json={"name": "x"}, headers=admin)).status_code == 404


@pytest.mark.asyncio
async def test_users_round_trip_preferences_and_hosts(client: AsyncClient, db_session: AsyncSession):
    """B-USERS-1, 10, 11 and CRUD"""
    _, admin = await _headers(db_session)
    assert (await client.post("/api/v1/users", json={"email": "a@t.com"}, headers=admin)).status_code == 422
    r = await timed(client.post("/api/v1/users", json={"email": "host@t.com", "password": "made-up-pw-1",
                                                       "role": "dj", "display_name": "Reb Host"}, headers=admin))
    assert r.status_code == 201, r.text
    user = r.json()
    dup = await client.post("/api/v1/users", json={"email": "host@t.com", "password": "made-up-pw-1"}, headers=admin)
    assert dup.status_code in (400, 409)
    r = await timed(client.put(f"/api/v1/users/{user['id']}", json={"is_public": True, "title": "Morning host"},
                               headers=admin))
    assert r.status_code == 200, r.text
    hosts = (await client.get("/api/v1/users/public/hosts")).json()["hosts"]
    assert [(h["display_name"], h["title"]) for h in hosts] == [("Reb Host", "Morning host")]
    # the new user signs in and keeps their own preferences
    login = await client.post("/api/v1/auth/login", json={"email": "host@t.com", "password": "made-up-pw-1"})
    me = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = await timed(client.patch("/api/v1/users/me/preferences", json={"preview_start_seconds": 8.0}, headers=me))
    assert r.status_code == 200, r.text
    got = (await client.get("/api/v1/users/me/preferences", headers=me)).json()
    assert got["preview_start_seconds"] == 8.0
    assert (await timed(client.delete(f"/api/v1/users/{user['id']}", headers=admin))).status_code in (200, 204)
    assert (await client.get("/api/v1/users/public/hosts")).json()["hosts"] == []


@pytest.mark.asyncio
async def test_archives_round_trip(client: AsyncClient, db_session: AsyncSession):
    """B-ARCHIVES-1, 4..6"""
    st = await _station(db_session)
    _, h = await _headers(db_session, UserRole.MANAGER)
    assert (await client.post("/api/v1/archives", json={"title": "No station"}, headers=h)).status_code == 422
    r = await timed(client.post("/api/v1/archives", json={"station_id": str(st.id), "title": "Motzei Shabbos",
                                                          "audio_url": "https://x/a.mp3"}, headers=h))
    assert r.status_code == 201, r.text
    arc = r.json()
    r = await timed(client.patch(f"/api/v1/archives/{arc['id']}", json={"is_published": False}, headers=h))
    assert r.status_code == 200 and r.json()["is_published"] is False
    assert (await client.get(f"/api/v1/archives/{arc['id']}")).status_code == 404  # hidden from listeners
    assert (await client.get(f"/api/v1/archives/{arc['id']}", headers=h)).status_code == 200
    assert (await timed(client.delete(f"/api/v1/archives/{arc['id']}", headers=h))).status_code == 204
    assert (await client.delete(f"/api/v1/archives/{arc['id']}", headers=h)).status_code == 404


@pytest.mark.asyncio
async def test_alerts_resolve_reopen_delete(client: AsyncClient, db_session: AsyncSession):
    """B-ALERTS-4, 5 and delete (admin only)"""
    from app.models.alert import Alert

    alert = Alert(id=uuid.uuid4(), alert_type="silence", severity="critical", title="Dead air", message="FM silent")
    db_session.add(alert)
    await db_session.commit()
    _, manager = await _headers(db_session, UserRole.MANAGER)
    _, admin = await _headers(db_session)
    r = await timed(client.patch(f"/api/v1/alerts/{alert.id}/resolve", headers=manager))
    assert r.status_code == 200 and r.json()["is_resolved"] is True
    r = await timed(client.patch(f"/api/v1/alerts/{alert.id}/reopen", headers=manager))
    assert r.status_code == 200 and r.json()["is_resolved"] is False
    assert (await client.patch(f"/api/v1/alerts/{uuid.uuid4()}/resolve", headers=manager)).status_code == 404
    assert (await client.delete(f"/api/v1/alerts/{alert.id}", headers=manager)).status_code == 403
    assert (await timed(client.delete(f"/api/v1/alerts/{alert.id}", headers=admin))).status_code == 204


@pytest.mark.asyncio
async def test_song_request_flow(client: AsyncClient, db_session: AsyncSession):
    """B-SONGREQUES-1, 2, 4..8"""
    from app.models.queue_entry import QueueEntry

    st = await _station(db_session)
    song = await _asset(db_session, title="Yerushalayim", artist="Avraham Fried")
    _, h = await _headers(db_session, UserRole.MANAGER)
    assert (await client.post("/api/v1/song-requests", json={"station_id": str(st.id)})).status_code == 422
    r = await timed(client.post("/api/v1/song-requests", json={
        "station_id": str(st.id), "requester_name": "Fan", "song_title": "Yerushalayim",
        "song_artist": "Avraham Fried"}))
    assert r.status_code in (200, 201), r.text
    r = await client.post("/api/v1/song-requests", json={
        "station_id": str(st.id), "requester_name": "Fan2", "song_title": "Zzzz unknown tune"})
    assert r.status_code in (200, 201), r.text

    listed = (await timed(client.get("/api/v1/song-requests", params={"station_id": str(st.id)}, headers=h),
                          READ)).json()
    by_title = {x["song_title"]: x for x in listed["requests"]}
    matched = by_title["Yerushalayim"]
    assert str(matched["asset_id"]) == str(song.id)  # fuzzy match to the library
    assert by_title["Zzzz unknown tune"]["asset_id"] is None and by_title["Zzzz unknown tune"]["status"] == "pending"

    if matched["status"] == "pending":
        r = await timed(client.patch(f"/api/v1/song-requests/{matched['id']}", json={"status": "approved"},
                                     headers=h))
        assert r.status_code == 200 and r.json()["status"] == "queued"
    q = (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == st.id))).scalars().all()
    assert [e.asset_id for e in q] == [song.id]

    top = (await client.get("/api/v1/song-requests/analytics/top-requested", headers=h)).json()["top_requested"]
    assert top and top[0]["asset_id"] == str(song.id)

    rej = by_title["Zzzz unknown tune"]
    r = await client.patch(f"/api/v1/song-requests/{rej['id']}", json={"status": "rejected"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert (await timed(client.delete(f"/api/v1/song-requests/{rej['id']}", headers=h))).status_code == 204
    assert (await client.delete(f"/api/v1/song-requests/{rej['id']}", headers=h)).status_code == 404


@pytest.mark.asyncio
async def test_live_show_round_trip(client: AsyncClient, db_session: AsyncSession):
    """B-LIVESHOWS-1, 3..5, 9, 11..14, 17"""
    from app.models.call_in_request import CallInRequest, CallStatus

    st = await _station(db_session)
    _, dj = await _headers(db_session, UserRole.DJ)
    _, admin = await _headers(db_session)
    assert (await client.post("/api/v1/live-shows", json={"title": "x"}, headers=dj)).status_code == 422
    end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = await timed(client.post("/api/v1/live-shows", json={"station_id": str(st.id), "title": "Call-in",
                                                            "scheduled_end": end}, headers=dj))
    assert r.status_code == 201, r.text
    show = r.json()
    sid = show["id"]
    assert (await timed(client.get(f"/api/v1/live-shows/{sid}", headers=dj), READ)).json()["title"] == "Call-in"
    r = await timed(client.patch(f"/api/v1/live-shows/{sid}", json={"title": "Call-in show"}, headers=dj))
    assert r.json()["title"] == "Call-in show"
    remaining = (await client.get(f"/api/v1/live-shows/{sid}/time-remaining", headers=dj)).json()
    assert 3000 < remaining["seconds"] <= 3600

    calls = []
    for n in range(3):
        c = CallInRequest(id=uuid.uuid4(), live_show_id=uuid.UUID(sid), caller_phone=f"+1555000{n}",
                          caller_name=f"C{n}", status=CallStatus.WAITING)
        db_session.add(c)
        calls.append(c)
    await db_session.commit()
    assert (await client.get(f"/api/v1/live-shows/{sid}/calls", headers=dj)).json()["total"] == 3
    base = f"/api/v1/live-shows/{sid}/calls"
    assert (await timed(client.post(f"{base}/{calls[0].id}/approve", headers=dj))).status_code == 200
    assert (await timed(client.post(f"{base}/{calls[1].id}/reject", headers=dj))).status_code == 200
    assert (await timed(client.post(f"{base}/{calls[0].id}/end-call", headers=dj))).status_code == 200
    for c in calls:
        await db_session.refresh(c)
    assert [c.status for c in calls] == [CallStatus.COMPLETED, CallStatus.REJECTED, CallStatus.WAITING]

    r = await timed(client.post(f"/api/v1/live-shows/{sid}/start", headers=dj))
    assert r.status_code == 200, r.text
    r = await timed(client.post(f"/api/v1/live-shows/{sid}/end", headers=dj))
    assert r.status_code == 200 and r.json()["status"] == "ended", r.text

    assert (await client.delete(f"/api/v1/live-shows/{sid}", headers=dj)).status_code == 403
    assert (await timed(client.delete(f"/api/v1/live-shows/{sid}", headers=admin))).status_code == 204
    assert (await client.get(f"/api/v1/live-shows/{sid}", headers=dj)).status_code == 404


@pytest.mark.asyncio
async def test_playlist_templates_and_slots(client: AsyncClient, db_session: AsyncSession):
    """B-PLAYLISTS-1..3, 12"""
    st = await _station(db_session)
    await _asset(db_session, asset_type="music", category="niggunim")
    _, h = await _headers(db_session, UserRole.MANAGER)
    assert (await client.post("/api/v1/playlists", json={}, headers=h)).status_code == 422
    r = await timed(client.post("/api/v1/playlists", json={"name": "Day", "station_id": str(st.id)}, headers=h))
    assert r.status_code == 201, r.text
    tpl = r.json()
    r = await timed(client.post("/api/v1/playlists/slots", json={"template_id": tpl["id"], "position": 0,
                                                                 "asset_type": "music", "category": "niggunim"},
                                headers=h))
    assert r.status_code == 201, r.text
    listed = (await timed(client.get("/api/v1/playlists", headers=h), READ)).json()
    templates = listed["templates"] if isinstance(listed, dict) else listed
    assert [t["name"] for t in templates] == ["Day"]
    combos = (await client.get("/api/v1/playlists/asset-types", headers=h)).json()
    assert "niggunim" in str(combos)
    assert (await client.post("/api/v1/playlists/slots", json={"template_id": str(uuid.uuid4()), "position": 0,
                                                               "asset_type": "music"}, headers=h)).status_code == 404


@pytest.mark.asyncio
async def test_schedule_playlist_entries_and_prerecorded_block(client: AsyncClient, db_session: AsyncSession):
    """B-SCHEDULES-13..20"""
    from app.models.schedule import Schedule
    from app.models.schedule_block import ScheduleBlock

    st = await _station(db_session)
    song = await _asset(db_session)
    show = await _asset(db_session, title="Recorded shiur", asset_type="shiur", duration=3600)
    sched = Schedule(id=uuid.uuid4(), station_id=st.id, name="Week")
    db_session.add(sched)
    await db_session.flush()
    block = ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name="Evening",
                          start_time=dtime(20, 0), end_time=dtime(21, 0))
    db_session.add(block)
    await db_session.commit()
    _, h = await _headers(db_session, UserRole.MANAGER)
    # Entry changes refresh the queue, which expires this shared session's objects
    block_id, song_id, show_id = block.id, song.id, show.id

    assert (await client.post("/api/v1/schedules/playlist-entries", json={"block_id": str(block_id)},
                              headers=h)).status_code == 422
    r = await timed(client.post("/api/v1/schedules/playlist-entries",
                                json={"block_id": str(block_id), "asset_id": str(song_id), "position": 0}, headers=h))
    assert r.status_code == 201, r.text
    entry = r.json()
    got = (await client.get("/api/v1/schedules/playlist-entries", params={"block_id": str(block_id)})).json()
    assert [e["id"] for e in got] == [entry["id"]]
    r = await timed(client.patch(f"/api/v1/schedules/playlist-entries/{entry['id']}", json={"position": 3},
                                 headers=h))
    assert r.status_code == 200 and r.json()["position"] == 3
    assert (await timed(client.delete(f"/api/v1/schedules/playlist-entries/{entry['id']}",
                                      headers=h))).status_code == 204
    assert (await client.get(f"/api/v1/schedules/playlist-entries/{entry['id']}")).status_code == 404

    r = await timed(client.post(f"/api/v1/schedules/blocks/{block_id}/set-prerecorded",
                                json={"asset_id": str(show_id)}, headers=h))
    assert r.status_code == 200, r.text
    got = (await client.get("/api/v1/schedules/playlist-entries", params={"block_id": str(block_id)})).json()
    assert [e["asset_id"] for e in got] == [str(show_id)]


@pytest.mark.asyncio
async def test_timeline_preview(client: AsyncClient, db_session: AsyncSession):
    """B-SCHEDULES-21/22, V1-154"""
    from app.models.schedule import Schedule
    from app.models.schedule_block import ScheduleBlock

    st = await _station(db_session)
    sched = Schedule(id=uuid.uuid4(), station_id=st.id, name="Week", is_active=True)
    db_session.add(sched)
    await db_session.flush()
    db_session.add(ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name="Morning",
                                 start_time=dtime(6, 0), end_time=dtime(9, 0)))
    await db_session.commit()
    r = await timed(client.get("/api/v1/schedules/timeline-preview",
                               params={"station_id": str(st.id), "at_time": "2030-01-07T07:00:00+00:00"}), READ)
    assert r.status_code == 200, r.text
    assert "Morning" in r.text
    bad = await client.get("/api/v1/schedules/timeline-preview", params={"station_id": str(st.id), "at_time": "soon"})
    assert bad.status_code == 400
    missing = await client.get("/api/v1/schedules/timeline-preview", params={"station_id": str(uuid.uuid4())})
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_campaigns_and_billing_keep_sponsors_apart(client: AsyncClient, db_session: AsyncSession, monkeypatch):
    """B-CAMPAIGNS-1..11, B-BILLING-1..4, 7, B-ROLE-6/7"""
    from app.config import settings
    from app.models.sponsor import Sponsor

    a_user, a = await _headers(db_session, UserRole.SPONSOR)
    b_user, b = await _headers(db_session, UserRole.SPONSOR)
    sa = Sponsor(id=uuid.uuid4(), name="A Shop", length_seconds=30, audio_file_path="a.mp3", user_id=a_user.id)
    sb = Sponsor(id=uuid.uuid4(), name="B Shop", length_seconds=30, audio_file_path="b.mp3", user_id=b_user.id)
    db_session.add_all([sa, sb])
    await db_session.commit()
    _, manager = await _headers(db_session, UserRole.MANAGER)
    _, dj = await _headers(db_session, UserRole.DJ)

    assert (await client.post("/api/v1/campaigns", json={}, headers=a)).status_code == 422
    r = await timed(client.post("/api/v1/campaigns", json={"name": "Pesach sale"}, headers=a))
    assert r.status_code == 201, r.text
    camp = r.json()
    cid = camp["id"]
    assert (await timed(client.post(f"/api/v1/campaigns/{cid}/drafts", json={"script_text": "v1"},
                                    headers=a))).status_code == 201
    assert (await timed(client.post(f"/api/v1/campaigns/{cid}/comments", json={"body": "Please read"},
                                    headers=a))).status_code == 201
    r = await client.patch(f"/api/v1/campaigns/{cid}", json={"budget_cents": 10000}, headers=a)
    assert r.status_code == 200 and r.json()["budget_cents"] == 10000

    # B cannot see or touch A's campaign
    assert [c["name"] for c in (await client.get("/api/v1/campaigns", headers=b)).json()] == []
    for method, path, body in [("GET", f"/api/v1/campaigns/{cid}", None),
                               ("PATCH", f"/api/v1/campaigns/{cid}", {"name": "mine now"}),
                               ("GET", f"/api/v1/campaigns/{cid}/drafts", None),
                               ("GET", f"/api/v1/campaigns/{cid}/comments", None),
                               ("POST", f"/api/v1/campaigns/{cid}/comments", {"body": "hi"})]:
        r = await client.request(method, path, json=body, headers=b)
        assert r.status_code in (403, 404), (method, path, r.status_code)
    # managers see everyone's; only managers change status; a dj may look (the admin
    # Campaigns screen, view only) but not change anything
    assert cid in [c["id"] for c in (await client.get("/api/v1/campaigns", headers=manager)).json()]
    assert (await client.patch(f"/api/v1/campaigns/{cid}/status", json={"status": "approved"},
                               headers=a)).status_code == 403
    r = await client.patch(f"/api/v1/campaigns/{cid}/status", json={"status": "approved"}, headers=manager)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/campaigns", headers=dj)).status_code == 200
    assert (await client.post(f"/api/v1/campaigns/{cid}/comments", json={"body": "hi"},
                              headers=dj)).status_code == 403
    assert (await client.post("/api/v1/sponsorship/copy-review", json={"text": "x"}, headers=dj)).status_code == 403

    # billing
    assert (await client.post("/api/v1/billing/invoices", json={"sponsor_id": str(sa.id)},
                              headers=manager)).status_code == 422
    r = await timed(client.post("/api/v1/billing/invoices", json={"sponsor_id": str(sa.id), "amount_cents": 5000},
                                headers=manager))
    assert r.status_code == 201, r.text
    inv = r.json()
    assert [i["id"] for i in (await client.get("/api/v1/billing/invoices", headers=a)).json()] == [inv["id"]]
    assert (await client.get("/api/v1/billing/invoices", headers=b)).json() == []
    assert (await client.get("/api/v1/billing/summary", headers=a)).status_code == 200
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "")
    # B cannot pay A's invoice; with Stripe off nobody can start a checkout
    assert (await client.post(f"/api/v1/billing/invoices/{inv['id']}/checkout", headers=b)).status_code in (400, 403, 404)
    assert (await client.post(f"/api/v1/billing/invoices/{inv['id']}/checkout", headers=a)).status_code == 400


@pytest.mark.asyncio
async def test_analytics_listeners_and_activity_reads(client: AsyncClient, db_session: AsyncSession):
    """B-ANALYTICS-1..5, B-LISTENERS-8..11, B-ACTIVITY-3, B-USERS-9: managers' report screens answer fast."""
    from app.models.play_log import PlayLog

    st = await _station(db_session)
    song = await _asset(db_session, category="niggunim")
    now = datetime.now(timezone.utc)
    for i in range(3):
        db_session.add(PlayLog(id=uuid.uuid4(), station_id=st.id, asset_id=song.id,
                               start_utc=now - timedelta(hours=i + 1), end_utc=now - timedelta(hours=i + 1) + timedelta(minutes=3)))
    await db_session.commit()
    _, manager = await _headers(db_session, UserRole.MANAGER)
    _, admin = await _headers(db_session)
    for path in ["/api/v1/analytics/play-counts", "/api/v1/analytics/top-assets",
                 "/api/v1/analytics/category-breakdown", "/api/v1/analytics/hourly-distribution",
                 "/api/v1/analytics/summary", "/api/v1/listeners/today", "/api/v1/listeners/history",
                 "/api/v1/listeners/regions"]:
        r = await timed(client.get(path, headers=manager), READ)
        assert r.status_code == 200, (path, r.text)
    top = (await client.get("/api/v1/analytics/top-assets", headers=manager)).json()
    assert "Song" in str(top) and "3" in str(top)
    for path in ["/api/v1/activity/filters", "/api/v1/users/audit-log"]:
        r = await timed(client.get(path, headers=admin), READ)
        assert r.status_code == 200, (path, r.text)
        assert (await client.get(path, headers=manager)).status_code == 403


@pytest.mark.asyncio
async def test_scheduler_status_is_public_and_start_stop_are_managers(client: AsyncClient, db_session: AsyncSession,
                                                                     monkeypatch):
    """B-SCHEDULER-1, 2, 4"""
    from app.services import scheduler_engine

    calls = []

    class Fake:
        running = False

        async def start(self):
            calls.append("start")
            Fake.running = True

        async def stop(self):
            calls.append("stop")
            Fake.running = False

    r = await client.get("/api/v1/scheduler/status")
    assert r.status_code == 200
    monkeypatch.setattr(scheduler_engine, "get_scheduler", lambda: Fake())
    _, manager = await _headers(db_session, UserRole.MANAGER)
    _, dj = await _headers(db_session, UserRole.DJ)
    assert (await client.post("/api/v1/scheduler/stop", headers=dj)).status_code == 403
    r1 = await client.post("/api/v1/scheduler/start", headers=manager)
    r2 = await client.post("/api/v1/scheduler/stop", headers=manager)
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)


@pytest.mark.asyncio
async def test_regenerate_refused_while_generation_is_off(client: AsyncClient, db_session: AsyncSession):
    """V1-153"""
    st = await _station(db_session)
    _, h = await _headers(db_session, UserRole.MANAGER)
    r = await client.post("/api/v1/playlists/generation/regenerate", params={"station_id": str(st.id)}, headers=h)
    assert r.status_code == 409, r.text
    assert "switched off" in r.text


@pytest.mark.asyncio
async def test_public_song_requests_are_rate_limited(client: AsyncClient, db_session: AsyncSession):
    """V1-145: 10 a minute from one address, then 429."""
    from app.api.v1.song_requests import limiter

    limiter.reset()
    st = await _station(db_session)
    codes = []
    for n in range(11):
        r = await client.post("/api/v1/song-requests", json={"station_id": str(st.id), "requester_name": f"F{n}",
                                                             "song_title": f"Tune {n}"})
        codes.append(r.status_code)
    limiter.reset()
    assert all(c in (200, 201) for c in codes[:10]), codes
    assert codes[10] == 429


@pytest.mark.asyncio
async def test_signing_in_on_a_second_device_does_not_sign_out_the_first(client: AsyncClient, db_session: AsyncSession):
    """V1-144: tokens are not rotated or revoked, so two devices keep working side by side."""
    from app.core.security import hash_password

    db_session.add(User(id=uuid.uuid4(), email="two@t.com", hashed_password=hash_password("pw-123456"),
                        role=UserRole.DJ, is_active=True))
    await db_session.commit()
    one = (await client.post("/api/v1/auth/login", json={"email": "two@t.com", "password": "pw-123456"})).json()
    two = (await client.post("/api/v1/auth/login", json={"email": "two@t.com", "password": "pw-123456"})).json()
    for tokens in (one, two):
        r = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        assert r.status_code == 200, r.text
        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert me.status_code == 200
    # the first device refreshes again with its old refresh token: still fine
    again = await client.post("/api/v1/auth/refresh", json={"refresh_token": one["refresh_token"]})
    assert again.status_code == 200
