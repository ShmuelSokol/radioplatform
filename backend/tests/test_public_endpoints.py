"""Every GET the site answers with no login: it must work anonymously, answer
fast, and leak nothing private (passwords, emails, phones, PINs, caller numbers,
requesters' messages, station assignments, tokens).

The list of public GETs is taken from the app itself (routes with no login
dependency), so a new public route is checked automatically.
"""
import json
import time
import uuid
from datetime import datetime, time as dtime, timedelta, timezone

import pytest
from fastapi.routing import APIRoute

from tests.route_list import app_routes
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.asset import Asset
from app.models.call_in_request import CallInRequest, CallStatus
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.playlist_entry import PlaylistEntry
from app.models.schedule import Schedule
from app.models.schedule_block import ScheduleBlock
from app.models.show_archive import ShowArchive
from app.models.song_request import RequestStatus, SongRequest
from app.models.station import Station
from app.models.user import User, UserRole

SECRET_EMAIL = "private.person@example.org"
SECRET_PHONE = "+15550001234"
SECRET_MESSAGE = "please call my cell"
PRIVATE_KEYS = {"hashed_password", "password", "email", "phone", "pin", "station_ids", "access_token",
                "refresh_token", "caller_phone", "twilio_call_sid", "requester_message", "contact_email",
                "last_ip", "ip_address"}
LOGIN_DEPS = ("require_", "get_current_user", "get_crm_member")
FAST = 0.3  # checklist S-4: reads under 300 ms server time


def _public_gets():
    from app.main import create_app

    def deps(d, acc):
        for sub in d.dependencies:
            if sub.call is not None:
                acc.append(getattr(sub.call, "__name__", ""))
            deps(sub, acc)
        return acc

    out = []
    for r in app_routes(create_app()):
        if isinstance(r.route, APIRoute) and "GET" in r.methods and r.path.startswith("/api/v1/"):
            if "/playout/" in r.path:
                continue  # engine only, X-Playout-Token (test_playout_engine.py)
            if not any(n.startswith(LOGIN_DEPS) for n in deps(r.dependant, [])):
                out.append(r.path)
    return sorted(set(out))


async def _seed(db: AsyncSession):
    station = Station(id=uuid.uuid4(), name="Public", timezone="UTC", is_active=True)
    db.add(station)
    host = User(id=uuid.uuid4(), email=SECRET_EMAIL, hashed_password=hash_password("pw12345678"),
                role=UserRole.DJ, is_active=True, is_public=True, display_name="Host",
                station_ids=[str(uuid.uuid4())])
    asset = Asset(id=uuid.uuid4(), title="Song", file_path="song.mp3", duration=100, asset_type="music")
    db.add_all([host, asset])
    await db.flush()
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Week")
    db.add(schedule)
    await db.flush()
    block = ScheduleBlock(id=uuid.uuid4(), schedule_id=schedule.id, name="Morning",
                          start_time=dtime(6, 0), end_time=dtime(9, 0))
    db.add(block)
    await db.flush()
    entry = PlaylistEntry(id=uuid.uuid4(), block_id=block.id, asset_id=asset.id, position=0)
    show = LiveShow(id=uuid.uuid4(), station_id=station.id, title="Live", status=LiveShowStatus.LIVE,
                    host_user_id=host.id, actual_start=datetime.now(timezone.utc))
    db.add_all([entry, show])
    await db.flush()
    db.add(CallInRequest(id=uuid.uuid4(), live_show_id=show.id, caller_phone=SECRET_PHONE,
                         caller_name="Caller", status=CallStatus.WAITING))
    db.add(SongRequest(id=uuid.uuid4(), station_id=station.id, requester_name="Fan", song_title="Song",
                       requester_message=SECRET_MESSAGE, status=RequestStatus.PENDING))
    published = ShowArchive(id=uuid.uuid4(), station_id=station.id, title="Published", audio_url="a.mp3",
                            is_published=True, recorded_at=datetime.now(timezone.utc) - timedelta(days=1))
    hidden = ShowArchive(id=uuid.uuid4(), station_id=station.id, title="Hidden draft", audio_url="b.mp3",
                         is_published=False)
    db.add_all([published, hidden])
    await db.commit()
    return {"station_id": station.id, "schedule_id": schedule.id, "block_id": block.id,
            "entry_id": entry.id, "archive_id": published.id, "hidden_archive_id": hidden.id}


def _keys(value, found):
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(k)
            _keys(v, found)
    elif isinstance(value, list):
        for v in value:
            _keys(v, found)
    return found


def _url(path: str, ids: dict) -> str:
    for name, value in ids.items():
        path = path.replace("{" + name + "}", str(value))
    path = path.replace("{spot_date}", "2026-01-01").replace("{hour}", "9")
    return path


def test_public_get_list_is_what_we_reviewed():
    """A new anonymous GET must be looked at (and added here) on purpose."""
    reviewed = {
        "/api/v1/init", "/api/v1/stations", "/api/v1/stations/{station_id}",
        "/api/v1/stations/{station_id}/stream", "/api/v1/stations/{station_id}/now-playing",
        "/api/v1/stations/{station_id}/live-audio", "/api/v1/stations/{station_id}/rds.txt",
        "/api/v1/users/public/hosts", "/api/v1/now-playing/{station_id}",
        "/api/v1/now-playing/{station_id}/active-block", "/api/v1/scheduler/status", "/api/v1/channels",
        "/api/v1/song-requests/station/{station_id}/count", "/api/v1/archives", "/api/v1/archives/{archive_id}",
        "/api/v1/archives/station/{station_id}/rss", "/api/v1/crm/raffles/active", "/api/v1/playout-status",
        "/api/v1/site-release",
        # schedules: the program guide is public; the other reads are used by admin screens but have
        # always been open (tests/test_schedules.py reads them with no login). They hold block names,
        # times and which library items a block plays: nothing secret (see B-ROLE-18).
        "/api/v1/schedules/epg/{station_id}", "/api/v1/schedules", "/api/v1/schedules/",
        "/api/v1/schedules/blocks", "/api/v1/schedules/blocks/{block_id}", "/api/v1/schedules/playlist-entries",
        "/api/v1/schedules/playlist-entries/{entry_id}", "/api/v1/schedules/timeline-preview",
        "/api/v1/schedules/{schedule_id}",
        "/api/v1/live-shows/station/{station_id}/active", "/api/v1/live-shows/twilio/hold-music",
    }
    assert set(_public_gets()) == reviewed


@pytest.mark.asyncio
async def test_public_gets_work_anonymously_and_leak_nothing(client: AsyncClient, db_session: AsyncSession):
    ids = await _seed(db_session)
    problems, slow = [], []
    for path in _public_gets():
        url = _url(path, ids)
        params = {"station_id": str(ids["station_id"])} if path.endswith("timeline-preview") else None
        started = time.perf_counter()
        r = await client.get(url, params=params)
        took = time.perf_counter() - started
        if path.endswith("/stream") or path.endswith("/live-audio"):
            # no stream configured / no live show audio: a polite refusal, never a crash
            if r.status_code >= 500 and r.status_code != 503:
                problems.append(f"{path}: {r.status_code}")
        elif r.status_code != 200:
            problems.append(f"{path}: {r.status_code} {r.text[:100]}")
        body = r.text
        for secret in (SECRET_EMAIL, SECRET_PHONE, SECRET_MESSAGE, "Hidden draft"):
            if secret in body:
                problems.append(f"{path} leaks {secret!r}")
        if "json" in r.headers.get("content-type", ""):
            leaked = _keys(json.loads(body), set()) & PRIVATE_KEYS
            if leaked:
                problems.append(f"{path} returns private fields {sorted(leaked)}")
        if took > FAST:
            slow.append(f"{path}: {took * 1000:.0f} ms")
    assert not problems, "\n".join(problems)
    assert not slow, "\n".join(slow)


@pytest.mark.asyncio
async def test_epg_lists_the_days_blocks_in_time_order(client: AsyncClient, db_session: AsyncSession):
    ids = await _seed(db_session)
    late = ScheduleBlock(id=uuid.uuid4(), schedule_id=ids["schedule_id"], name="Evening",
                         start_time=dtime(18, 0), end_time=dtime(20, 0))
    early = ScheduleBlock(id=uuid.uuid4(), schedule_id=ids["schedule_id"], name="Dawn",
                          start_time=dtime(4, 0), end_time=dtime(5, 0))
    db_session.add_all([late, early])
    await db_session.commit()
    r = await client.get(f"/api/v1/schedules/epg/{ids['station_id']}")
    assert r.status_code == 200, r.text
    names = [b["name"] for b in r.json()["blocks"]]
    assert names == ["Dawn", "Morning", "Evening"]


@pytest.mark.asyncio
async def test_archives_public_sees_published_only_manager_sees_all(
    client: AsyncClient, db_session: AsyncSession, auth_headers: dict
):
    ids = await _seed(db_session)
    public = await client.get("/api/v1/archives")
    titles = [a["title"] for a in public.json()["archives"]]
    assert titles == ["Published"]
    assert (await client.get(f"/api/v1/archives/{ids['hidden_archive_id']}")).status_code == 404
    admin = await client.get("/api/v1/archives?drafts=true", headers=auth_headers)
    assert sorted(a["title"] for a in admin.json()["archives"]) == ["Hidden draft", "Published"]
    rss = await client.get(f"/api/v1/archives/station/{ids['station_id']}/rss")
    assert rss.status_code == 200 and "<rss" in rss.text and "Published" in rss.text
    assert "Hidden draft" not in rss.text


@pytest.mark.asyncio
async def test_song_request_count_counts_pending_only(client: AsyncClient, db_session: AsyncSession):
    ids = await _seed(db_session)
    db_session.add(SongRequest(id=uuid.uuid4(), station_id=ids["station_id"], requester_name="X",
                               song_title="Done", status=RequestStatus.REJECTED))
    await db_session.commit()
    r = await client.get(f"/api/v1/song-requests/station/{ids['station_id']}/count")
    assert r.json() == {"count": 1}


@pytest.mark.asyncio
async def test_active_live_show_is_public_without_callers(client: AsyncClient, db_session: AsyncSession):
    ids = await _seed(db_session)
    r = await client.get(f"/api/v1/live-shows/station/{ids['station_id']}/active")
    assert r.status_code == 200
    assert r.json()["active"] is True and r.json()["show"]["title"] == "Live"
    assert SECRET_PHONE not in r.text
