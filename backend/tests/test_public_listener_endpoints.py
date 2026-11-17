"""Backend endpoints the public listener site calls (checklist section R,
B-PUBLIC-1..16): song requests, the program guide, listener heartbeats, web
call-ins, now-playing (REST and socket), CRM register and raffles, public hosts
and archives."""
import json
import uuid
from datetime import UTC, date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
import app.api.v1.websocket as ws_mod
from app.api.v1 import call_in as call_in_module
from app.api.v1 import listeners
from app.models.listener_session import ListenerSession
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock
from app.models.show_archive import ShowArchive
from app.models.song_request import SongRequest
from app.models.station import Station, StationType
from app.models.user import User, UserRole
from tests.test_queue_advance import _station_with_two_tracks

pytestmark = pytest.mark.asyncio

API = "/api/v1"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    from app.api.v1.song_requests import limiter

    limiter.reset()
    call_in_module._recent.clear()
    for d in (ws_mod._join_cache, ws_mod._join_locks, ws_mod._last_now_playing, ws_mod._latest_start,
              ws_mod._np_generation):
        d.clear()
    ws_mod.manager.active_connections.clear()

    async def no_replenish(db, station_id):
        return None

    async def no_geo(ip):
        return {"country": "Local", "region": "Local", "city": "Local"}

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(listeners, "_resolve_geo", no_geo)
    queue_module._fire_requests.clear()
    yield
    limiter.reset()
    call_in_module._recent.clear()
    for d in (ws_mod._join_cache, ws_mod._join_locks, ws_mod._last_now_playing, ws_mod._latest_start,
              ws_mod._np_generation):
        d.clear()
    ws_mod.manager.active_connections.clear()


async def _station(db, name="KBR", tz="America/New_York"):
    st = Station(id=uuid.uuid4(), name=name, type=StationType.INTERNET, timezone=tz)
    db.add(st)
    await db.commit()
    return st


# ── B-PUBLIC-1 / B-PUBLIC-2: song requests ──

async def test_song_request_lengths_and_blanks_are_refused(client, db_session):
    """B-PUBLIC-1: limits match the song_requests columns; blank name or title -> 422;
    blank artist or message are stored as not given."""
    st = await _station(db_session)
    ok = {"station_id": str(st.id), "requester_name": "Dovid", "song_title": "Yerushalayim"}
    bad = [
        {"requester_name": "x" * 256},
        {"song_title": "x" * 501},
        {"song_artist": "x" * 256},
        {"requester_message": "x" * 1001},
        {"requester_name": "   "},
        {"song_title": ""},
        {"song_title": " \t "},
    ]
    for change in bad:
        r = await client.post(f"{API}/song-requests", json={**ok, **change})
        assert r.status_code == 422, (change, r.status_code, r.text[:200])
    assert (await db_session.execute(select(SongRequest))).scalars().all() == []

    r = await client.post(f"{API}/song-requests", json={
        **ok, "requester_name": "  Dovid  ", "song_artist": "  ", "requester_message": "",
        "song_title": "x" * 500,
    })
    assert r.status_code == 201, r.text
    row = (await db_session.execute(select(SongRequest))).scalar_one()
    assert row.requester_name == "Dovid" and row.song_artist is None and row.requester_message is None


async def test_song_request_unknown_station_and_rate_limit(client, db_session):
    """B-PUBLIC-2: unknown or malformed station -> 404 "Station not found"; the 11th
    request in a minute from one address -> 429."""
    for sid in (str(uuid.uuid4()), "not-a-uuid"):
        r = await client.post(f"{API}/song-requests", json={
            "station_id": sid, "requester_name": "A", "song_title": "B"})
        assert r.status_code == 404 and "Station not found" in r.text
    st = await _station(db_session)
    codes = []
    for i in range(11):
        r = await client.post(f"{API}/song-requests", json={
            "station_id": str(st.id), "requester_name": "A", "song_title": f"Song {i}"})
        codes.append(r.status_code)
    assert codes[:8].count(201) == 8 and codes[-1] == 429, codes


# ── B-PUBLIC-3..5: program guide ──

async def _schedule(db, st):
    sched = Schedule(id=uuid.uuid4(), station_id=st.id, name="Week", is_active=True)
    db.add(sched)
    await db.commit()
    return sched


async def test_epg_bad_date_is_422(client, db_session):
    """B-PUBLIC-3: a malformed ?date= is the caller's mistake (was 500)."""
    st = await _station(db_session)
    for bad in ("2026-13-40", "tomorrow", "17/11/2026"):
        r = await client.get(f"{API}/schedules/epg/{st.id}", params={"date": bad})
        assert r.status_code == 422, (bad, r.status_code)
    r = await client.get(f"{API}/schedules/epg/{st.id}", params={"date": "2026-11-17"})
    assert r.status_code == 200 and r.json()["date"] == "2026-11-17"


async def test_epg_default_date_is_the_stations_own_date(client, db_session, monkeypatch):
    """B-PUBLIC-4 (V2-104): with no date the station's local date is used. The
    endpoint called UUID() on a UUID, which raised and fell back to UTC."""
    # Pick a zone whose date differs from UTC's right now
    now_utc = datetime.now(UTC)
    tz = "Pacific/Kiritimati" if now_utc.hour >= 10 else "Pacific/Pago_Pago"
    local = datetime.now(ZoneInfo(tz)).date()
    assert local != now_utc.date()
    st = await _station(db_session, tz=tz)
    r = await client.get(f"{API}/schedules/epg/{st.id}")
    assert r.status_code == 200
    assert r.json()["date"] == local.isoformat()


async def test_epg_honours_recurring_block_dates(client, db_session):
    """B-PUBLIC-5: a recurring block only shows on dates inside its start_date..end_date
    (the guide and playout both go through scheduling._recurs_on)"""
    st = await _station(db_session)
    sched = await _schedule(db_session, st)
    db_session.add(ScheduleBlock(
        id=uuid.uuid4(), schedule_id=sched.id, name="Summer only", start_time=dtime(9), end_time=dtime(10),
        recurrence_type=RecurrenceType.DAILY, start_date=date(2026, 6, 1), end_date=date(2026, 8, 31),
    ))
    await db_session.commit()
    r = await client.get(f"{API}/schedules/epg/{st.id}", params={"date": "2026-11-17"})
    assert r.json()["blocks"] == []
    r = await client.get(f"{API}/schedules/epg/{st.id}", params={"date": "2026-07-01"})
    assert [(b["name"], b["start_time"], b["end_time"]) for b in r.json()["blocks"]] == [
        ("Summer only", "09:00", "10:00")]


# ── B-PUBLIC-6..8: listener heartbeat ──

async def _beat(client, st, key="ls_1"):
    sid = st if isinstance(st, uuid.UUID) else st.id
    return await client.post(f"{API}/listeners/heartbeat", json={"station_id": str(sid), "session_key": key})


async def test_stop_then_listen_starts_a_new_session(client, db_session):
    """B-PUBLIC-6: the Listen page keeps one session key per page, so Stop then
    Listen sends the same key again. That reopened the ended session with
    duration = now - started_at, counting the stopped gap as listening."""
    st = (await _station(db_session)).id
    assert (await _beat(client, st)).json() == {"status": "created"}
    first = (await db_session.execute(select(ListenerSession))).scalar_one()
    # Listened 60 s, then Stop
    first.started_at = datetime.now(UTC) - timedelta(hours=2)
    first.last_heartbeat = first.started_at + timedelta(seconds=60)
    await db_session.commit()
    await client.post(f"{API}/listeners/disconnect", json={"station_id": str(st), "session_key": "ls_1"})
    await db_session.refresh(first)
    assert first.ended_at is not None
    ended_duration = first.duration_seconds
    # Two hours later: Listen again on the same page
    r = await _beat(client, st)
    assert r.status_code == 200 and r.json() == {"status": "created"}, r.text
    db_session.expire_all()
    rows = (await db_session.execute(select(ListenerSession).order_by(ListenerSession.started_at))).scalars().all()
    assert len(rows) == 2
    old, new = rows
    assert old.ended_at is not None and old.duration_seconds == ended_duration
    assert new.ended_at is None and new.duration_seconds < 5
    assert new.session_key == "ls_1"
    # Later heartbeats and Stop go to the new session
    assert (await _beat(client, st)).json()["status"] == "ok"
    await client.post(f"{API}/listeners/disconnect", json={"station_id": str(st), "session_key": "ls_1"})
    await db_session.refresh(new)
    assert new.ended_at is not None and new.duration_seconds < 5


async def test_heartbeat_bad_or_unknown_station_is_4xx(client, db_session):
    """B-PUBLIC-7: malformed station id -> 422 (heartbeat, disconnect); unknown
    station -> 404 on heartbeat (was 500 from uuid.UUID() or the foreign key);
    disconnect for a station with no session is a harmless no-op; the leave beacon
    ignores garbage."""
    for path in ("heartbeat", "disconnect"):
        r = await client.post(f"{API}/listeners/{path}", json={"station_id": "nope", "session_key": "k"})
        assert r.status_code == 422, (path, r.status_code)
    r = await client.post(f"{API}/listeners/heartbeat", json={"station_id": str(uuid.uuid4()), "session_key": "k"})
    assert r.status_code == 404, r.text
    r = await client.post(f"{API}/listeners/disconnect", json={"station_id": str(uuid.uuid4()), "session_key": "k"})
    assert r.status_code == 200
    r = await client.post(f"{API}/listeners/leave", content=b'{"station_id": "nope", "session_key": "k"}',
                          headers={"Content-Type": "text/plain"})
    assert r.status_code == 200 and r.json() == {"status": "ignored"}
    st = await _station(db_session)
    for key in ("", "k" * 81):
        r = await _beat(client, st, key)
        assert r.status_code == 422


async def test_duplicate_session_rows_do_not_break_heartbeats(client, db_session):
    """B-PUBLIC-8: two first heartbeats at the same moment could create two rows
    for one key; every later heartbeat then raised MultipleResultsFound (500)."""
    st = (await _station(db_session)).id
    now = datetime.now(UTC)
    for i in range(2):
        db_session.add(ListenerSession(
            id=uuid.uuid4(), station_id=st, session_key="ls_dup", started_at=now - timedelta(seconds=30 - i),
            last_heartbeat=now - timedelta(seconds=10), duration_seconds=0,
        ))
    await db_session.commit()
    r = await _beat(client, st, "ls_dup")
    assert r.status_code == 200 and r.json()["status"] == "ok", r.text
    r = await client.post(f"{API}/listeners/disconnect", json={"station_id": str(st), "session_key": "ls_dup"})
    assert r.status_code == 200
    db_session.expire_all()
    rows = (await db_session.execute(select(ListenerSession))).scalars().all()
    assert all(s.ended_at is not None for s in rows)
    # Listen again: one new session, and it keeps working
    assert (await _beat(client, st, "ls_dup")).json() == {"status": "created"}
    assert (await _beat(client, st, "ls_dup")).json()["status"] == "ok"


# ── B-PUBLIC-9: call-in rate limit ──

async def test_refused_call_attempts_do_not_use_up_the_limit(client, db_session):
    """B-PUBLIC-9: the 5-in-10-minutes limit counted attempts refused because no
    show was live or calls were closed, so an eager caller was locked out (429)
    when the show started."""
    st = await _station(db_session)
    for _ in range(6):
        r = await client.post(f"{API}/call-in/{st.id}", json={"name": "Eager"})
        assert r.status_code == 409
    now = datetime.now(UTC)
    show = LiveShow(id=uuid.uuid4(), station_id=st.id, title="Live", status=LiveShowStatus.LIVE,
                    scheduled_start=now, scheduled_end=now + timedelta(hours=1), calls_enabled=False)
    db_session.add(show)
    await db_session.commit()
    for _ in range(3):
        assert (await client.post(f"{API}/call-in/{st.id}", json={"name": "Eager"})).status_code == 409
    show.calls_enabled = True
    await db_session.commit()
    codes = [(await client.post(f"{API}/call-in/{st.id}", json={"name": "Eager"})).status_code for _ in range(6)]
    assert codes == [201] * 5 + [429]


# ── B-PUBLIC-10..12: now playing ──

async def test_now_playing_rest_null_and_bad_id(client, db_session):
    """B-PUBLIC-10: nothing on air -> null; malformed id -> 422."""
    st = await _station(db_session)
    r = await client.get(f"{API}/now-playing/{st.id}")
    assert r.status_code == 200 and r.json() is None
    assert (await client.get(f"{API}/now-playing/not-a-uuid")).status_code == 422


class _Listener:
    def __init__(self):
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def _listen(station_id) -> _Listener:
    sock = _Listener()
    ws_mod.manager.active_connections.setdefault(str(station_id), set()).add(sock)
    return sock


def _nulls(sock):
    return [m for m in sock.sent if m == {"type": "now_playing", "data": None}]


async def test_song_ending_with_nothing_after_it_tells_listeners(db_session, monkeypatch):
    """B-PUBLIC-11: the item on air ends during a blackout with no silence queued:
    listeners get one now_playing data:null (nothing was sent before)."""
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=200.0)

    async def blacked_out(db, station_id):
        return True

    monkeypatch.setattr(queue_module, "_is_blacked_out", blacked_out)
    sock = _listen(station.id)
    assert await queue_module._check_advance(db_session, station.id) is None
    assert len(_nulls(sock)) == 1
    # The next polls change nothing and send nothing more
    assert await queue_module._check_advance(db_session, station.id) is None
    assert len(_nulls(sock)) == 1


async def test_skip_with_nothing_next_tells_listeners(client, auth_headers, db_session, monkeypatch):
    """B-PUBLIC-11: Next during a blackout with no silence queued ("Queue empty")."""
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)

    async def blacked_out(db, station_id):
        return True

    monkeypatch.setattr(queue_module, "_is_blacked_out", blacked_out)
    sock = _listen(station.id)
    r = await client.post(f"{API}/stations/{station.id}/queue/skip", headers=auth_headers)
    assert r.json()["message"] == "Queue empty"
    assert sock.sent == [{"type": "now_playing", "data": None}]
    # Next again with nothing on air: nothing to clear, nothing sent
    await client.post(f"{API}/stations/{station.id}/queue/skip", headers=auth_headers)
    assert len(sock.sent) == 1


async def test_removing_the_item_on_air_with_nothing_next_tells_listeners(client, auth_headers, db_session,
                                                                          monkeypatch):
    """B-PUBLIC-11: deleting what is on air when nothing can follow it."""
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)

    async def blacked_out(db, station_id):
        return True

    monkeypatch.setattr(queue_module, "_is_blacked_out", blacked_out)
    sock = _listen(station.id)
    r = await client.delete(f"{API}/stations/{station.id}/queue/{playing.id}", headers=auth_headers)
    assert r.status_code == 204
    assert sock.sent == [{"type": "now_playing", "data": None}]


async def test_nothing_playing_clears_the_join_cache_and_a_new_item_goes_out():
    """B-PUBLIC-11: after the null, a listener who joins does not get the old item
    from the 5 s join cache, and the next item is announced normally."""
    sock = _listen("st")
    ws_mod._join_cache["st"] = (10**9, {"asset_id": "old"})
    await ws_mod.broadcast_now_playing_update("st", {"asset_id": "a1", "started_at": "2026-01-01T00:00:00+00:00"})
    ws_mod._join_cache["st"] = (10**9, {"asset_id": "a1"})
    await ws_mod.broadcast_nothing_playing("st")
    assert "st" not in ws_mod._join_cache
    await ws_mod.broadcast_now_playing_update("st", {"asset_id": "a2", "started_at": "2026-01-01T00:05:00+00:00"})
    assert [m["data"] and m["data"]["asset_id"] for m in sock.sent] == ["a1", None, "a2"]


async def test_socket_for_an_unknown_or_bad_station_stays_open(db_session, monkeypatch):
    """B-PUBLIC-12: a station that does not exist is accepted and gets data:null; an
    id that is not a UUID gets {"type": "error"} (the first read fails). Both then get
    pong to a message and leave cleanly; nothing crashes. Decide: close such sockets
    with 4004 instead (the page reconnects on any close, so that needs a page change)."""
    import app.db.session as session_mod
    from tests.test_now_playing_socket import FakeSocket

    async def test_session():
        yield db_session

    monkeypatch.setattr(session_mod, "get_async_session", test_session)
    for sid, first in ((str(uuid.uuid4()), {"type": "now_playing", "data": None}), ("not-a-uuid", None)):
        sock = FakeSocket(incoming=["hi"], hang=0.01)
        await ws_mod.websocket_now_playing(sock, sid)
        assert sock.accepted
        if first is not None:
            assert sock.sent[0] == first, sock.sent
        else:
            assert sock.sent[0]["type"] == "error"
        assert {"type": "pong", "data": "hi"} in sock.sent
        assert sid not in ws_mod.manager.active_connections


# ── B-PUBLIC-13 / 14: CRM register and raffles ──

async def test_crm_register_length_limits(client, db_session):
    """B-PUBLIC-13: name, phone and email longer than their columns -> 422 (was 500
    from the database); blank name -> 400."""
    for body in ({"name": "x" * 256}, {"name": "A", "phone": "1" * 51}, {"name": "A", "email": "e" * 256}):
        r = await client.post(f"{API}/crm/register", json=body)
        assert r.status_code == 422, body
    assert (await client.post(f"{API}/crm/register", json={"name": "  "})).status_code == 400
    r = await client.post(f"{API}/crm/register", json={"name": "Chaya"})
    assert r.status_code == 201 and len(r.json()["pin"]) == 6


@pytest.mark.xfail(strict=True, reason=(
    "BUG (decide): POST /crm/register has no rate limit; one address can create any "
    "number of members and use up the 6-digit PIN space"))
async def test_crm_register_is_rate_limited(client, db_session):
    """B-PUBLIC-13"""
    codes = [(await client.post(f"{API}/crm/register", json={"name": f"M{i}"})).status_code for i in range(30)]
    assert 429 in codes


async def test_active_raffles_for_a_station(client, db_session):
    """B-PUBLIC-14: only this station's raffles and all-station raffles, soonest
    end first, right entry counts; malformed station_id -> 422."""
    from app.models.crm_member import CrmMember
    from app.models.raffle import Raffle, RaffleEntry

    a, b = await _station(db_session, "A"), await _station(db_session, "B")
    now = datetime.now(UTC)
    late = Raffle(id=uuid.uuid4(), title="All, late", station_id=None, starts_at=now - timedelta(hours=1),
                  ends_at=now + timedelta(hours=5))
    soon = Raffle(id=uuid.uuid4(), title="A, soon", station_id=a.id, starts_at=now - timedelta(hours=1),
                  ends_at=now + timedelta(hours=1))
    other = Raffle(id=uuid.uuid4(), title="B only", station_id=b.id, starts_at=now - timedelta(hours=1),
                   ends_at=now + timedelta(hours=2))
    db_session.add_all([late, soon, other])
    members = [CrmMember(id=uuid.uuid4(), name=f"M{i}", pin=f"10000{i}") for i in range(3)]
    db_session.add_all(members)
    await db_session.flush()
    db_session.add_all([RaffleEntry(raffle_id=soon.id, member_id=m.id) for m in members[:2]])
    db_session.add(RaffleEntry(raffle_id=late.id, member_id=members[2].id))
    await db_session.commit()
    r = await client.get(f"{API}/crm/raffles/active", params={"station_id": str(a.id)})
    assert [(x["title"], x["entry_count"]) for x in r.json()] == [("A, soon", 2), ("All, late", 1)]
    r = await client.get(f"{API}/crm/raffles/active", params={"station_id": "nope"})
    assert r.status_code == 422


# ── B-PUBLIC-15 / 16: hosts and archives ──

async def test_public_hosts_only_public_active_sorted_without_private_fields(client, db_session):
    """B-PUBLIC-15"""
    db_session.add_all([
        User(id=uuid.uuid4(), email="z@kbrlive.com", hashed_password="x", role=UserRole.DJ,
             is_active=True, is_public=True, display_name="Zalman"),
        User(id=uuid.uuid4(), email="a@kbrlive.com", hashed_password="x", role=UserRole.ADMIN,
             is_active=True, is_public=True, display_name="Avraham"),
        User(id=uuid.uuid4(), email="off@kbrlive.com", hashed_password="x", role=UserRole.DJ,
             is_active=True, is_public=False, display_name="Hidden"),
        User(id=uuid.uuid4(), email="gone@kbrlive.com", hashed_password="x", role=UserRole.DJ,
             is_active=False, is_public=True, display_name="Gone"),
    ])
    await db_session.commit()
    hosts = (await client.get(f"{API}/users/public/hosts")).json()["hosts"]
    assert [h["display_name"] for h in hosts] == ["Avraham", "Zalman"]
    for h in hosts:
        assert "email" not in h and "role" not in h


async def test_public_archives_filter_order_total_and_paging(client, db_session):
    """B-PUBLIC-16: published only, newest recorded first (no date last), total
    counts the same filter, skip/limit page through."""
    st, other = await _station(db_session, "A"), await _station(db_session, "B")
    base = datetime(2026, 11, 1, tzinfo=UTC)
    rows = [
        ShowArchive(id=uuid.uuid4(), station_id=st.id, title=f"Show {i}", audio_url="https://cdn.test/a.mp3",
                    is_published=True, recorded_at=base + timedelta(days=i))
        for i in range(4)
    ]
    rows.append(ShowArchive(id=uuid.uuid4(), station_id=st.id, title="Undated", audio_url="https://cdn.test/a.mp3",
                            is_published=True, recorded_at=None))
    rows.append(ShowArchive(id=uuid.uuid4(), station_id=st.id, title="Draft", audio_url="https://cdn.test/a.mp3",
                            is_published=False, recorded_at=base + timedelta(days=10)))
    rows.append(ShowArchive(id=uuid.uuid4(), station_id=other.id, title="Elsewhere",
                            audio_url="https://cdn.test/a.mp3", is_published=True, recorded_at=base))
    db_session.add_all(rows)
    await db_session.commit()
    r = (await client.get(f"{API}/archives", params={"station_id": str(st.id)})).json()
    assert [a["title"] for a in r["archives"]] == ["Show 3", "Show 2", "Show 1", "Show 0", "Undated"]
    assert r["total"] == 5
    page = (await client.get(f"{API}/archives", params={"station_id": str(st.id), "skip": 2, "limit": 2})).json()
    assert [a["title"] for a in page["archives"]] == ["Show 1", "Show 0"] and page["total"] == 5
