"""Checklist rows on settings, security and status endpoints:
V3-20, V3-21, V1-146, V1-147, V1-120, V2-116, B-JOB-109, B-SET-35, B-SET-45."""
import asyncio
import json
import pathlib
import re
import sys
import types
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import hash_password
from app.models.station import Station
from app.models.user import User, UserRole


async def _person(db: AsyncSession, role: UserRole, station_ids=None) -> User:
    u = User(id=uuid.uuid4(), email=f"{role.value}-{uuid.uuid4().hex[:6]}@t.com",
             hashed_password=hash_password("pw12345678"), role=role, is_active=True, station_ids=station_ids)
    db.add(u)
    await db.commit()
    return u


async def _sign_in(client: AsyncClient, user: User) -> dict:
    r = await client.post("/api/v1/auth/login", json={"email": user.email, "password": "pw12345678"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ------------------------------------------------------------------ V3-20

WINDOW = {"name": "Test", "start_datetime": "2030-01-04T21:00:00Z", "end_datetime": "2030-01-05T22:00:00Z",
          "is_blackout": True}


@pytest.mark.asyncio
async def test_station_change_applies_on_the_next_action(client: AsyncClient, db_session: AsyncSession):
    """V3-20: an admin gives a signed-in manager another station; their very next
    request may operate it (same token, no sign-in). Taking it away refuses the next one."""
    a = Station(id=uuid.uuid4(), name="A", timezone="UTC")
    b = Station(id=uuid.uuid4(), name="B", timezone="UTC")
    db_session.add_all([a, b])
    await db_session.commit()
    manager = await _person(db_session, UserRole.MANAGER, [str(a.id)])
    admin = await _person(db_session, UserRole.ADMIN)
    mh, ah = await _sign_in(client, manager), await _sign_in(client, admin)
    on_b = {**WINDOW, "affected_stations": {"station_ids": [str(b.id)]}}

    assert (await client.post("/api/v1/holidays", json=on_b, headers=mh)).status_code == 403
    r = await client.put(f"/api/v1/users/{manager.id}", json={"station_ids": [str(a.id), str(b.id)]}, headers=ah)
    assert r.status_code == 200, r.text
    assert (await client.post("/api/v1/holidays", json=on_b, headers=mh)).status_code == 201
    await client.put(f"/api/v1/users/{manager.id}", json={"station_ids": [str(a.id)]}, headers=ah)
    assert (await client.post("/api/v1/holidays", json=on_b, headers=mh)).status_code == 403


@pytest.mark.asyncio
async def test_role_change_applies_on_the_next_action(client: AsyncClient, db_session: AsyncSession):
    """V3-20: a role change signs the person out: the old token is refused at once
    (it no longer carries manager rights), and after signing in again they have the new role."""
    manager = await _person(db_session, UserRole.MANAGER)
    admin = await _person(db_session, UserRole.ADMIN)
    mh, ah = await _sign_in(client, manager), await _sign_in(client, admin)
    assert (await client.get("/api/v1/alerts", headers=mh)).status_code == 200

    r = await client.put(f"/api/v1/users/{manager.id}", json={"role": "viewer"}, headers=ah)
    assert r.status_code == 200, r.text
    old = await client.get("/api/v1/alerts", headers=mh)
    assert old.status_code == 401
    vh = await _sign_in(client, manager)
    assert (await client.get("/api/v1/alerts", headers=vh)).status_code == 403
    assert (await client.get("/api/v1/assets", headers=vh)).status_code == 200


# ------------------------------------------------------------------ V3-21

FRONTEND_SRC = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_frontend_never_renders_raw_html():
    """V3-21: React writes text as text; the only ways to run typed text as markup are
    dangerouslySetInnerHTML, innerHTML/outerHTML/insertAdjacentHTML, document.write,
    eval and new Function. None is used anywhere in the site's source, so a song
    request name, call-in topic or bug report is shown as text on every staff screen."""
    assert FRONTEND_SRC.is_dir(), FRONTEND_SRC
    risky = re.compile(r"dangerouslySetInnerHTML|\.innerHTML\s*=|\.outerHTML\s*=|insertAdjacentHTML|"
                       r"document\.write\(|\beval\(|new Function\(")
    hits = []
    for path in FRONTEND_SRC.rglob("*"):
        if path.suffix in (".ts", ".tsx", ".js", ".jsx") and ".test." not in path.name:
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if risky.search(line):
                    hits.append(f"{path.relative_to(FRONTEND_SRC)}:{n}: {line.strip()}")
    assert hits == []


@pytest.mark.asyncio
async def test_public_text_is_stored_and_returned_as_plain_text(client: AsyncClient, db_session: AsyncSession,
                                                                auth_headers: dict):
    """V3-21: markup typed by the public comes back to staff unchanged, as JSON text
    (no HTML answer that a browser could run)."""
    st = Station(id=uuid.uuid4(), name="Req", timezone="UTC")
    db_session.add(st)
    await db_session.commit()
    evil = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    r = await client.post("/api/v1/song-requests", json={
        "station_id": str(st.id), "requester_name": evil, "song_title": "Song", "requester_message": evil})
    assert r.status_code in (200, 201), r.text
    listing = await client.get("/api/v1/song-requests", headers=auth_headers)
    assert listing.status_code == 200
    assert listing.headers["content-type"].startswith("application/json")
    rows = listing.json()
    rows = rows.get("requests", rows) if isinstance(rows, dict) else rows
    assert any(row["requester_name"] == evil and row["requester_message"] == evil for row in rows)


# ------------------------------------------------------- playout status (V1-146/147)

@pytest.fixture
def engines(monkeypatch):
    from app.api.v1 import playout_status

    ids = [str(uuid.uuid4()) for _ in range(3)]
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", ",".join(ids))
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "https://engine.test/control")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", "t")
    monkeypatch.setattr(settings, "ICECAST_STREAM_URL", "")
    monkeypatch.setattr(settings, "PLAYOUT_RELAY_STATION_ID", "")
    playout_status._cache.clear()
    yield ids
    playout_status._cache.clear()


async def _playing(db: AsyncSession, station_id: str, title: str):
    from app.models.asset import Asset
    from app.models.queue_entry import QueueEntry

    st = Station(id=uuid.UUID(station_id), name=f"E{station_id[:4]}", timezone="UTC")
    song = Asset(id=uuid.uuid4(), title=title, file_path="s.mp3", duration=180, asset_type="music")
    db.add_all([st, song])
    await db.flush()
    entry = QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, position=1, status="playing")
    db.add(entry)
    await db.commit()
    return entry


@pytest.mark.asyncio
async def test_in_sync_by_entry_id_or_by_title(db_session: AsyncSession, engines, monkeypatch):
    """V1-147: in step when the engine's entry id matches the queue's playing entry,
    or (a redrawn entry) when only its title matches; out of step otherwise."""
    from app.api.v1 import playout_status
    from app.services import playout_engine

    a, b, c = engines
    ea = await _playing(db_session, a, "Song A")
    await _playing(db_session, b, "Song B")
    await _playing(db_session, c, "Song C")
    said = {a: {"ok": True, "entry_id": str(ea.id), "title": "other"},
            b: {"ok": True, "entry_id": str(uuid.uuid4()), "title": "Song B"},
            c: {"ok": True, "entry_id": str(uuid.uuid4()), "title": "Not C"}}

    async def status(sid):
        return said[sid]

    monkeypatch.setattr(playout_engine, "engine_status", status)
    got = {sid: await playout_status.station_status(db_session, sid) for sid in engines}
    assert got[a]["in_sync"] is True and got[b]["in_sync"] is True and got[c]["in_sync"] is False
    assert all(g["engine_ok"] and g["mode"] == "browser" for g in got.values())  # no public stream set


@pytest.mark.asyncio
async def test_engine_down_and_status_cached_8_seconds(db_session: AsyncSession, engines, monkeypatch):
    """V1-147: an engine that does not answer -> engine_ok false, not in step. The answer
    is kept 8 s: inside that a changed engine is not asked again (old status shown)."""
    from app.api.v1 import playout_status
    from app.services import playout_engine

    a = engines[0]
    await _playing(db_session, a, "Song A")
    asked = []
    answer = {"v": None}

    async def status(sid):
        asked.append(sid)
        return answer["v"]

    clock = {"t": 1000.0}
    monkeypatch.setattr(playout_status, "time", types.SimpleNamespace(monotonic=lambda: clock["t"]))
    monkeypatch.setattr(playout_engine, "engine_status", status)
    first = await playout_status.station_status(db_session, a)
    assert first["engine_ok"] is False and first["in_sync"] is False
    answer["v"] = {"ok": True, "title": "Song A"}
    clock["t"] += 7.9
    assert (await playout_status.station_status(db_session, a))["engine_ok"] is False
    assert len(asked) == 1
    clock["t"] += 0.2
    assert (await playout_status.station_status(db_session, a))["in_sync"] is True
    assert len(asked) == 2


@pytest.mark.asyncio
async def test_public_playout_status_lists_engine_stations(client: AsyncClient, db_session: AsyncSession, engines,
                                                           monkeypatch):
    from app.services import playout_engine

    for sid in engines:
        await _playing(db_session, sid, "x")
    monkeypatch.setattr(playout_engine, "engine_status", AsyncMock(return_value=None))
    r = await client.get("/api/v1/playout-status")
    assert r.status_code == 200
    assert set(r.json()["stations"]) == set(engines)


@pytest.mark.asyncio
async def test_playout_status_asks_engines_at_the_same_time(client: AsyncClient, db_session: AsyncSession, engines,
                                                            monkeypatch):
    """V1-146: the engines must be asked together, so the call takes one timeout at most."""
    from app.services import playout_engine

    for sid in engines:
        await _playing(db_session, sid, "x")
    state = {"now": 0, "most": 0}

    async def slow(sid):
        state["now"] += 1
        state["most"] = max(state["most"], state["now"])
        await asyncio.sleep(0.05)
        state["now"] -= 1
        return None

    monkeypatch.setattr(playout_engine, "engine_status", slow)
    r = await client.get("/api/v1/playout-status")
    assert r.status_code == 200
    assert state["most"] == len(engines)


# -------------------------------------------------------- time announcement (V1-120)

def test_time_announcement_names_the_brand():
    """V1-120 (what is there today): the text names the given brand and the FM call sign."""
    from datetime import datetime

    from app.services.weather_spot_service import _build_time_text

    text = _build_time_text(datetime(2026, 11, 19, 15, 0))
    assert "Radio Kohl Baramah" in text and "W M D I 107.9" in text


@pytest.mark.asyncio
async def test_generated_time_announcement_is_per_station(db_session: AsyncSession):
    """V1-120: a station in another region (Jerusalem) with no uploaded clip must not
    announce the FM station's call sign."""
    from app.services.weather_spot_service import get_or_create_weather_spot_assets

    tts = AsyncMock(return_value=(b"tts", 3.0))
    with patch("app.services.audio_analysis_service.normalize_audio_loudness", side_effect=lambda b: b), \
         patch("app.services.weather_spot_service.upload_to_supabase", new_callable=AsyncMock,
               side_effect=lambda data, path: f"https://storage.test/{path}"), \
         patch.object(settings, "ELEVENLABS_API_KEY", "k"), patch.object(settings, "ELEVENLABS_VOICE_ID", "v"), \
         patch.object(settings, "OPENWEATHERMAP_API_KEY", ""), \
         patch("app.services.weather_spot_service.generate_tts", tts):
        await get_or_create_weather_spot_assets(db_session, "2026-11-19T15", "jerusalem")
    assert tts.await_args_list, "no time announcement was generated"
    said = tts.await_args_list[0].args[0]
    assert "107.9" not in said and "W M D I" not in said and "Lakewood" not in said, said
    assert said.startswith("The time is 3 PM on Radio"), said


def test_generated_time_text_keeps_the_call_sign_only_in_lakewood():
    """V1-120 fix: Lakewood (default region) keeps "W M D I 107.9"; any other region names only the brand."""
    from datetime import datetime

    from app.services.weather_spot_service import _build_time_text

    at = datetime(2026, 11, 19, 15, 30)
    assert "W M D I 107.9" in _build_time_text(at)
    assert "W M D I 107.9" in _build_time_text(at, region="lakewood")
    other = _build_time_text(at, region="jerusalem")
    assert "107.9" not in other and "W M D I" not in other and other.startswith("The time is 3:30 PM on Radio")


@pytest.mark.asyncio
async def test_generated_time_clip_of_one_region_is_not_reused_by_another(db_session: AsyncSession):
    """V1-120 fix: the generated clip is kept per region, so Jerusalem never plays Lakewood's
    "W M D I 107.9" clip made for the same slot (and the other way round)."""
    from app.services.weather_spot_service import get_or_create_weather_spot_assets

    tts = AsyncMock(return_value=(b"tts", 3.0))
    with patch("app.services.audio_analysis_service.normalize_audio_loudness", side_effect=lambda b: b), \
         patch("app.services.weather_spot_service.upload_to_supabase", new_callable=AsyncMock,
               side_effect=lambda data, path: f"https://storage.test/{path}"), \
         patch.object(settings, "ELEVENLABS_API_KEY", "k"), patch.object(settings, "ELEVENLABS_VOICE_ID", "v"), \
         patch.object(settings, "OPENWEATHERMAP_API_KEY", ""), \
         patch("app.services.weather_spot_service.generate_tts", tts):
        lakewood, _ = await get_or_create_weather_spot_assets(db_session, "2026-11-19T15", "lakewood")
        jerusalem, _ = await get_or_create_weather_spot_assets(db_session, "2026-11-19T15", "jerusalem")
        again, _ = await get_or_create_weather_spot_assets(db_session, "2026-11-19T15", "jerusalem")
    assert lakewood and jerusalem and lakewood.id != jerusalem.id
    assert again.id == jerusalem.id, "the region's own clip is reused for its slot"
    assert lakewood.file_path != jerusalem.file_path
    texts = [c.args[0] for c in tts.await_args_list]
    assert len(texts) == 2 and "107.9" in texts[0] and "107.9" not in texts[1], texts


# -------------------------------------------------------------- FM feed (V2-116)

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"


def _callers(name: str) -> list[str]:
    out = []
    for path in APP_DIR.rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(rf"\b{name}\(", line) and "def " + name not in line:
                out.append(f"{path.relative_to(APP_DIR)}:{n}")
    return out


def test_nothing_in_the_backend_feeds_an_fm_source_of_its_own():
    """V2-116, decided 2026-11-19: the FM feed carries the whole station at all times,
    which is the playout engine's stream. The backend's own Icecast source (host
    microphone only, dropped between shows) is gone: nothing pushes audio to one."""
    assert _callers("push_audio") == []
    assert _callers("start_icecast_stream") == []
    assert not (APP_DIR / "services" / "icecast_service.py").exists()


# --------------------------------------------------------- site release (B-JOB-109)

class _FakeTabSocket:
    def __init__(self):
        self.got: list[dict] = []

    async def send_text(self, text):
        self.got.append(json.loads(text))


@pytest.mark.asyncio
async def test_scheduled_push_reaches_every_open_tab_and_silent_tabs_drop(client: AsyncClient, auth_headers: dict,
                                                                          monkeypatch):
    """B-JOB-109: a push for a later time goes out at once to every open tab with its
    time (each tab takes it at that time by the server clock: frontend/e2e/release-timing.mjs
    'at'); a tab silent longer than TAB_SILENT_LIMIT is left off the open-tabs list."""
    from app.api.v1 import site_release

    clock = {"t": 50_000.0}
    monkeypatch.setattr(site_release, "time", types.SimpleNamespace(time=lambda: clock["t"]))
    monkeypatch.setattr(site_release, "_tabs", {})
    socks = [_FakeTabSocket() for _ in range(3)]
    for i, ws in enumerate(socks):
        tab = site_release.Tab(ws)
        tab.info = {"client_id": f"c{i}", "page": "/listen"}
        site_release._tabs[id(ws)] = tab
    at = "2030-01-01T12:00:00+00:00"
    r = await client.post("/api/v1/site-release/push", json={"version": "v9", "at": at}, headers=auth_headers)
    assert r.status_code == 200, r.text
    for ws in socks:
        assert ws.got[-1]["type"] == "release"
        assert ws.got[-1]["data"]["push"]["at"].startswith("2030-01-01T12:00:00")
        assert ws.got[-1]["data"]["push"]["version"] == "v9"

    site_release._tabs[id(socks[0])].seen_at = clock["t"] - site_release.TAB_SILENT_LIMIT - 1
    site_release._tabs[id(socks[1])].seen_at = clock["t"] - site_release.TAB_SILENT_LIMIT + 1
    listed = (await client.get("/api/v1/site-release/tabs", headers=auth_headers)).json()["tabs"]
    assert sorted(t["client_id"] for t in listed) == ["c1", "c2"]


# -------------------------------------------------------- Twilio (B-SET-35/45)

@pytest.mark.asyncio
async def test_sms_and_whatsapp_follow_twilio_enabled(monkeypatch):
    """B-SET-35: no TWILIO_ACCOUNT_SID -> nothing is sent; with it, Twilio's client is used
    with the account and the TWILIO_PHONE_NUMBER as sender."""
    from app.services import sms_service

    made = []

    class FakeClient:
        def __init__(self, sid, token):
            made.append((sid, token))
            self.messages = types.SimpleNamespace(create=lambda **k: made.append(k))

    monkeypatch.setitem(sys.modules, "twilio.rest", types.SimpleNamespace(Client=FakeClient))
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "")
    assert settings.twilio_enabled is False
    assert await sms_service.send_sms("+15550001111", "hi") is False
    assert await sms_service.send_whatsapp("+15550001111", "hi") is False
    assert made == []

    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setattr(settings, "TWILIO_PHONE_NUMBER", "+15559990000")
    assert await sms_service.send_sms("+15550001111", "hi") is True
    assert made[-1] == {"body": "hi", "from_": "+15559990000", "to": "+15550001111"}
    assert await sms_service.send_whatsapp("+15550001111", "hi") is True
    assert made[-1]["from_"] == "whatsapp:+15559990000" and made[-1]["to"] == "whatsapp:+15550001111"


@pytest.mark.asyncio
async def test_phone_call_controls_need_twilio_voice(monkeypatch):
    """B-SET-35: twilio_voice_enabled needs both SID and auth token; without it moving or
    hanging up a phone caller does nothing."""
    from app.services import twilio_voice_service as tv

    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "")
    assert settings.twilio_enabled is True and settings.twilio_voice_enabled is False
    assert await tv.move_caller_to_conference("CA1", "live-x") is False
    assert await tv.end_caller("CA1") is False

    updates = []

    class FakeClient:
        def __init__(self, sid, token):
            pass

        def calls(self, sid):
            return types.SimpleNamespace(update=lambda **k: updates.append((sid, k)))

    monkeypatch.setitem(sys.modules, "twilio.rest", types.SimpleNamespace(Client=FakeClient))
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "tok")
    assert settings.twilio_voice_enabled is True
    assert await tv.end_caller("CA1") is True
    assert updates == [("CA1", {"status": "completed"})]


@pytest.mark.asyncio
async def test_twiml_uses_public_url_and_hold_music(client: AsyncClient, monkeypatch):
    """B-SET-45: the hold TwiML waits on LIVE_SHOW_HOLD_MUSIC_URL and calls back on
    BACKEND_PUBLIC_URL; the phone stream goes to wss on that address (none without https)."""
    from app.services import twilio_voice_service as tv

    monkeypatch.setattr(settings, "LIVE_SHOW_HOLD_MUSIC_URL", "https://cdn.test/hold.mp3")
    monkeypatch.setattr(settings, "BACKEND_PUBLIC_URL", "https://api.test")
    twiml = tv.generate_hold_twiml("show1")
    assert 'waitUrl="https://cdn.test/hold.mp3"' in twiml
    assert 'statusCallback="https://api.test/api/v1/live-shows/twilio/status"' in twiml
    assert tv.phone_stream_url("https://api.test/", "c1") == "wss://api.test/api/v1/ws/live/phone/c1"
    assert tv.phone_stream_url("http://api.test", "c1") is None
    assert tv.phone_stream_url("", "c1") is None
    r = await client.get("/api/v1/live-shows/twilio/hold-music")
    assert r.status_code == 200 and "https://cdn.test/hold.mp3" in r.text
    monkeypatch.setattr(settings, "LIVE_SHOW_HOLD_MUSIC_URL", "")
    assert "twilio.music" in tv.generate_hold_twiml("show1")  # Twilio's own hold music when unset


# ------------------------------------------------------------- V1-145

@pytest.mark.asyncio
async def test_listener_heartbeat_has_no_rate_limit(client: AsyncClient, db_session: AsyncSession):
    """V1-145: the listener heartbeat is not limited (a page beats every few seconds);
    40 beats from one address in a row are all taken. The limited public writes have
    their own tests (song requests, copy review, bug reports, web call-in, CRM PINs)."""
    st = Station(id=uuid.uuid4(), name="Beat", timezone="UTC")
    db_session.add(st)
    await db_session.commit()
    hdr = {"X-Forwarded-For": "203.0.113.77"}
    codes = {(await client.post("/api/v1/listeners/heartbeat", headers=hdr,
                                json={"station_id": str(st.id), "session_key": f"k{i % 4}"})).status_code
             for i in range(40)}
    assert codes == {200}


# ------------------------------------------------------------- V2-36

@pytest.mark.xfail(strict=True, reason="found 2026-11-19: the CRM raffle form reads Start/End in the browser time zone (new Date(datetime-local).toISOString()) and lists them in the viewer zone, not the station clock (lib/stationTime.ts)")
def test_raffle_times_use_the_station_clock():
    """V2-36: like the other staff pages (wiki/admin-page-layout.md), the raffle form must
    turn the typed time into UTC in the station's zone (fromZonedInput) and show times
    with formatInZone, so an admin abroad opens the raffle at the right local hour."""
    page = (FRONTEND_SRC / "pages" / "admin" / "CrmDashboard.tsx").read_text(encoding="utf-8")
    assert "new Date(rfStart).toISOString()" not in page and "new Date(rfEnd).toISOString()" not in page
    assert "fromZonedInput" in page and "formatInZone" in page
