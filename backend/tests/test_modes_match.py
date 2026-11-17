"""HS, Spots mode, Panic roles and the song request "Match" box: the rows of
docs/test-checklist.md (F-MODES-*, F-MATCH-*, B-SONGREQUES-3) that test_station_modes.py,
test_panic.py and test_song_request_matching.py leave open. See wiki/station-modes.md.
"""
import time
import uuid
from datetime import UTC, date, datetime, timedelta

from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.config import settings
from app.core.security import hash_password
from app.models.alert import Alert
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.hs_softened import HsSoftened
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.program_block import ProgramBlock
from app.models.queue_entry import QueueEntry
from app.models.song_request import RequestStatus, SongRequest
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.models.user import User, UserRole
from app.services import activity_log, playout_engine
from app.services.station_modes import restore_hard_items, soften_hard_items
from tests.conftest import TestSessionLocal
from tests.timing import limit


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", nothing)
    queue_module._automatic_change.clear()
    yield
    queue_module._automatic_change.clear()


async def _row(db, entry_id):
    return (await db.execute(
        select(QueueEntry).where(QueueEntry.id == entry_id).execution_options(populate_existing=True)
    )).scalar_one()


async def _station(db, **kw):
    st = Station(id=uuid.uuid4(), name=f"KBR Jerusalem {uuid.uuid4().hex[:6]}", timezone="Asia/Jerusalem", **kw)
    db.add(st)
    await db.flush()
    return st


def _asset(title="Time", kind="jingle", duration=8.0, **kw):
    return Asset(id=uuid.uuid4(), title=title, duration=duration,
                 file_path=f"https://cdn.example.com/{uuid.uuid4().hex}.mp3", asset_type=kind, **kw)


def _timed(st, asset, pos, at, **kw):
    kw.setdefault("source", "block")
    return QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=asset.id, position=pos,
                      status="pending", preempt_at=at, **kw)


async def _user(client, db, role, station_ids=None):
    email = f"{role.value}-{uuid.uuid4().hex[:6]}"
    db.add(User(id=uuid.uuid4(), email=email, hashed_password=hash_password("pw12345678"),
                role=role, is_active=True, station_ids=station_ids))
    await db.commit()
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345678"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ── HS ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hs_off_never_softens_resumed_shiurim_or_live_entries(client, auth_headers, db_session):
    """F-MODES-3: Shabbos silence, a resumed shiur (resume@...) and a live-show entry keep
    their hard start; an ordinary time check next to them is softened."""
    st = await _station(db_session)
    check, silence, shiur = _asset(), _asset("Shabbos", "silence", 3600.0), _asset("Shiur", "shiur", 3000.0)
    db_session.add_all([check, silence, shiur])
    await db_session.flush()
    at = datetime.now(UTC) + timedelta(hours=1)
    plain = _timed(st, check, 1, at, timing_mode="hard")
    keep = [
        _timed(st, silence, 2, at, timing_mode="hard", source="auto"),
        _timed(st, shiur, 3, at, timing_mode="hard", source=f"resume@{uuid.uuid4()}"),
        _timed(st, shiur, 4, at, source="resume@1234"),  # old rule (no timing): hard
        _timed(st, check, 5, at, timing_mode="hard", source="live"),
    ]
    db_session.add_all([plain, *keep])
    await db_session.commit()
    r = await client.post(f"/api/v1/stations/{st.id}/queue/modes", json={"hard_start": False}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert (await _row(db_session, plain.id)).timing_mode == "soft"
    for e in keep:
        row = await _row(db_session, e.id)
        assert row.timing_mode == e.timing_mode, f"{e.source} was softened"


@pytest.mark.asyncio
async def test_hs_off_softens_an_old_time_check_with_no_timing_and_no_source(client, auth_headers, db_session):
    """HS off must never leave a time check hard. An entry from before timing modes with
    no source either (source NULL) starts on the second, but hard_timed()'s
    `source != 'ad_slot'` is not true for NULL in SQL, so it was left hard. Fixed."""
    st = await _station(db_session)
    check = _asset()
    db_session.add(check)
    await db_session.flush()
    at = datetime.now(UTC) + timedelta(hours=1)
    old = _timed(st, check, 1, at, source=None)
    db_session.add(old)
    await db_session.commit()
    url = f"/api/v1/stations/{st.id}/queue/modes"
    assert (await client.post(url, json={"hard_start": False}, headers=auth_headers)).status_code == 200
    assert (await _row(db_session, old.id)).timing_mode == "soft"
    assert (await client.post(url, json={"hard_start": True}, headers=auth_headers)).status_code == 200
    assert (await _row(db_session, old.id)).timing_mode == "hard"


@pytest.mark.asyncio
async def test_scheduler_pass_softens_items_added_while_hs_is_off(db_session, monkeypatch):
    """F-MODES-4: the scheduler's pass (every 3 s) softens a time check that arrived after HS
    went off, long before it is due."""
    from app.services import scheduler_engine

    st = await _station(db_session, automation_config={"hard_start": False})
    song, check = _asset("Song", "music", 600.0), _asset()
    db_session.add_all([song, check])
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, position=1, status="playing",
                              source="auto", started_at=datetime.now(UTC) - timedelta(seconds=10)))
    late = _timed(st, check, 2, datetime.now(UTC) + timedelta(minutes=30), timing_mode="hard")
    db_session.add(late)
    await db_session.commit()

    async with TestSessionLocal() as db:
        await scheduler_engine.get_scheduler()._check_all_stations(db)
    assert (await _row(db_session, late.id)).timing_mode == "soft"
    saved = (await db_session.execute(select(HsSoftened).where(HsSoftened.entry_id == late.id))).scalar_one()
    assert saved.timing_mode == "hard"


@pytest.mark.asyncio
async def test_replenish_softens_the_items_it_writes_while_hs_is_off(db_session):
    """HS off must never leave a time check hard, not even for the 3 s until the scheduler's
    next pass: the queue fill writes time checks, block items and ad slots hard (and puts
    a softened ad slot back to its planned timing), so it softens them before it commits."""
    from app.services.queue_replenish_service import QueueReplenishService

    st = await _station(db_session, automation_config={"hard_start": False, "hourly_time_announcement": True})
    check = _asset()
    db_session.add_all([_asset("Song", "music", 200.0), check])
    await db_session.commit()
    svc = QueueReplenishService(db_session, st.id)

    async def fake_hourly():
        db_session.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=check.id, position=99, status="pending",
                                  source="auto", timing_mode="hard",
                                  preempt_at=datetime.now(UTC) + timedelta(minutes=20)))

    svc._schedule_hourly_announcements = fake_hourly
    try:
        await svc.replenish()
    except Exception:
        pass  # later fill steps need a full station; the timed part is committed first
    rows = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == st.id, QueueEntry.preempt_at.isnot(None))
        .execution_options(populate_existing=True))).scalars().all()
    assert rows and {e.timing_mode for e in rows} == {"soft"}


@pytest.mark.asyncio
async def test_hs_off_twice_keeps_the_first_saved_timings_and_on_when_on_changes_nothing(
    client, auth_headers, db_session
):
    """F-MODES-24 and F-MODES-5 (exact restore): a second HS off (another control room) does
    not overwrite the saved timings; HS on gives each item back its own timing, limit
    time to the microsecond; HS on again changes nothing."""
    st = await _station(db_session)
    check = _asset()
    db_session.add(check)
    await db_session.flush()
    at = datetime.now(UTC).replace(microsecond=123456) + timedelta(hours=1)
    limit_at = at + timedelta(minutes=3, seconds=7)
    hard = _timed(st, check, 1, at, timing_mode="hard")
    limited = _timed(st, check, 2, at, timing_mode="soft", hard_by=limit_at)
    ad = _timed(st, check, 3, at, source="ad_slot")  # old rule: an ad slot is soft
    db_session.add_all([hard, limited, ad])
    await db_session.commit()
    url = f"/api/v1/stations/{st.id}/queue/modes"
    for _ in range(2):
        assert (await client.post(url, json={"hard_start": False}, headers=auth_headers)).status_code == 200
    assert len((await db_session.execute(select(HsSoftened))).scalars().all()) == 2  # the ad slot was already soft
    assert (await client.post(url, json={"hard_start": True}, headers=auth_headers)).status_code == 200
    assert (await _row(db_session, hard.id)).timing_mode == "hard"
    back = await _row(db_session, limited.id)
    assert back.timing_mode == "soft" and back.hard_by.replace(tzinfo=UTC) == limit_at
    assert (await _row(db_session, ad.id)).timing_mode is None
    assert (await db_session.execute(select(HsSoftened))).scalars().all() == []

    # HS on while on: nothing moves
    db_session.add(_timed(st, check, 4, at, timing_mode="soft"))
    await db_session.commit()
    before = {(e.id, e.timing_mode, e.hard_by) for e in (await db_session.execute(
        select(QueueEntry).execution_options(populate_existing=True))).scalars().all()}
    assert (await client.post(url, json={"hard_start": True}, headers=auth_headers)).status_code == 200
    after = {(e.id, e.timing_mode, e.hard_by) for e in (await db_session.execute(
        select(QueueEntry).execution_options(populate_existing=True))).scalars().all()}
    assert before == after


@pytest.mark.asyncio
async def test_hs_on_restores_only_items_still_pending(client, auth_headers, db_session):
    """F-MODES-25: items redrawn while HS was off come back hard and are softened on the next
    pass; HS on restores only what is still pending (played ones are left as they aired)."""
    st = await _station(db_session)
    check = _asset()
    db_session.add(check)
    await db_session.flush()
    at = datetime.now(UTC) + timedelta(hours=1)
    aired = _timed(st, check, 1, at, timing_mode="hard")
    redrawn = _timed(st, check, 2, at, timing_mode="hard")
    db_session.add_all([aired, redrawn])
    await db_session.commit()
    url = f"/api/v1/stations/{st.id}/queue/modes"
    assert (await client.post(url, json={"hard_start": False}, headers=auth_headers)).status_code == 200
    # One airs while HS is off; the schedule redraw replaces the other with a fresh hard item
    row = await _row(db_session, aired.id)
    row.status = "played"
    await db_session.delete(await _row(db_session, redrawn.id))
    fresh = _timed(st, check, 3, at, timing_mode="hard")
    db_session.add(fresh)
    await db_session.commit()
    st_row = await db_session.get(Station, st.id)
    assert await soften_hard_items(db_session, st_row) == 1
    await db_session.commit()
    assert (await _row(db_session, fresh.id)).timing_mode == "soft"
    assert (await client.post(url, json={"hard_start": True}, headers=auth_headers)).status_code == 200
    assert (await _row(db_session, fresh.id)).timing_mode == "hard"
    assert (await _row(db_session, aired.id)).timing_mode == "soft"  # aired: history is left alone
    assert (await db_session.execute(select(HsSoftened))).scalars().all() == []


@pytest.mark.asyncio
async def test_spots_change_while_hs_off_softens_the_rebuilt_block_items(client, auth_headers, db_session):
    """F-MODES-26: switching Spots mode rebuilds the program blocks' queue items (hard);
    while HS is off they are softened again in the same call."""
    st = await _station(db_session)
    check = _asset("Station ID", "jingle")
    db_session.add(check)
    await db_session.flush()
    hh = datetime.now(UTC) + timedelta(hours=2)
    db_session.add(ProgramBlock(id=uuid.uuid4(), station_id=st.id, name="Station ID", timing_mode="hard",
                                items=[{"kind": "asset", "asset_id": str(check.id)}],
                                times=hh.astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%H:00"),
                                is_active=True))
    await db_session.commit()
    url = f"/api/v1/stations/{st.id}/queue/modes"
    assert (await client.post(url, json={"hard_start": False}, headers=auth_headers)).status_code == 200
    assert (await client.post(url, json={"spots": False}, headers=auth_headers)).status_code == 200
    blocks = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == st.id, QueueEntry.source == "block").execution_options(populate_existing=True)
    )).scalars().all()
    assert blocks, "the block was queued"
    assert {e.timing_mode for e in blocks} == {"soft"}
    # HS on puts them back hard
    assert (await client.post(url, json={"hard_start": True}, headers=auth_headers)).status_code == 200
    blocks = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == st.id, QueueEntry.source == "block").execution_options(populate_existing=True)
    )).scalars().all()
    assert {e.timing_mode for e in blocks} == {"hard"}


# ── Spots mode ────────────────────────────────────────────────────────────────


async def _sponsored_station(db, **kw):
    st = await _station(db, **kw)
    sponsor = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, priority=1, audio_file_path="")
    spot = _asset("Bagel King", "spot", 30.0)
    db.add_all([sponsor, spot])
    await db.flush()
    db.add(SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=st.id,
                        dayparts="morning,afternoon,evening,overnight", plays_per_daypart=2,
                        start_date=date.today() - timedelta(days=1), end_date=date.today() + timedelta(days=5)))
    await db.commit()
    return st, sponsor, spot


@pytest.mark.asyncio
async def test_spots_off_plans_no_legacy_placeholder_ad_slots(client, auth_headers, db_session):
    """Spots off must plan no sponsorships. A station with no spot schedules but the legacy
    placeholder ad slot switched on (ad_slot_legacy_spot) kept its pending placeholders and
    got new ones on every fill, because the planner's "Spots off" check was never reached.
    Fixed: Spots off removes them (outside the 2-minute margin) and adds none."""
    from app.services.queue_replenish_service import QueueReplenishService

    st = await _station(db_session, automation_config={"ad_slot_legacy_spot": True})
    ad = _asset("KOL BRAMAH TEST SPONSOR", "spot", 30.0)
    db_session.add(ad)
    await db_session.flush()
    soon = _timed(st, ad, 1, datetime.now(UTC) + timedelta(seconds=60), source="ad_slot")
    later = _timed(st, ad, 2, datetime.now(UTC) + timedelta(hours=1), source="ad_slot")
    db_session.add_all([soon, later])
    await db_session.commit()
    r = await client.post(f"/api/v1/stations/{st.id}/queue/modes", json={"spots": False}, headers=auth_headers)
    assert r.status_code == 200, r.text
    left = (await db_session.execute(select(QueueEntry.id).where(
        QueueEntry.station_id == st.id, QueueEntry.source == "ad_slot"))).scalars().all()
    assert [str(i) for i in left] == [str(soon.id)]  # inside the margin: still airs (F-MODES-27)

    svc = QueueReplenishService(db_session, st.id)
    await svc._load_automation_config()
    svc.max_pos = 10
    await svc._schedule_ad_slots()
    await db_session.commit()
    left = (await db_session.execute(select(QueueEntry.id).where(
        QueueEntry.station_id == st.id, QueueEntry.source == "ad_slot"))).scalars().all()
    assert len(left) == 1


@pytest.mark.asyncio
async def test_spots_off_inserts_no_global_sponsors(db_session):
    """Spots off must plan no sponsorships: the old global sponsor insertion (Sponsor with an
    insertion policy, for stations on global rules) ignored Spots mode. Fixed."""
    from app.services.queue_replenish_service import QueueReplenishService

    st = await _station(db_session, automation_config={"spots_on": False})
    sponsor = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, priority=1,
                      audio_file_path="bk.mp3", insertion_policy="every_n_songs",
                      target_rules={"songs_between": 2, "max_per_hour": 4})
    db_session.add(sponsor)
    db_session.add(Asset(id=uuid.uuid4(), title="Bagel King", duration=30.0, file_path="bk.mp3", asset_type="spot"))
    await db_session.commit()
    svc = QueueReplenishService(db_session, st.id)
    await svc._load_automation_config()
    svc.max_pos, svc.queued_ids, svc.exclude_ids = 0, set(), set()
    await svc._insert_sponsors(3600.0)
    await db_session.flush()
    assert (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == st.id))).first() is None

    # Spots on: the same call queues the sponsor
    st.automation_config = {}
    await db_session.commit()
    await svc._load_automation_config()
    await svc._insert_sponsors(3600.0)
    await db_session.flush()
    assert (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == st.id))).first() is not None


@pytest.mark.asyncio
async def test_spots_off_leaves_block_sponsor_places_empty(client, auth_headers, db_session):
    """F-MODES-7: a program block's sponsor places are filled from the spot schedules while
    Spots mode is on and stay empty while it is off; on again fills them."""
    st, _, spot = await _sponsored_station(db_session)
    check = _asset("Station ID", "jingle")
    db_session.add(check)
    await db_session.flush()
    at = (datetime.now(UTC) + timedelta(hours=3)).astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%H:00")
    db_session.add(ProgramBlock(id=uuid.uuid4(), station_id=st.id, name="Weather Sponsors", timing_mode="hard",
                                items=[{"kind": "asset", "asset_id": str(check.id)}, {"kind": "sponsors", "count": 2}],
                                times=at, is_active=True))
    await db_session.commit()
    url = f"/api/v1/stations/{st.id}/queue/modes"

    async def sponsored_in_blocks():
        return (await db_session.execute(select(QueueEntry).where(
            QueueEntry.station_id == st.id, QueueEntry.source == "block", QueueEntry.asset_id == spot.id,
            QueueEntry.status == "pending"))).scalars().all()

    assert (await client.post(url, json={"spots": True}, headers=auth_headers)).status_code == 200
    assert await sponsored_in_blocks(), "Spots on fills the block's sponsor places"
    assert (await client.post(url, json={"spots": False}, headers=auth_headers)).status_code == 200
    assert await sponsored_in_blocks() == []
    ids = (await db_session.execute(select(QueueEntry.asset_id).where(
        QueueEntry.station_id == st.id, QueueEntry.status == "pending"))).scalars().all()
    assert str(spot.id) not in {str(i) for i in ids}, "no sponsorship anywhere in Up next"
    assert (await client.post(url, json={"spots": True}, headers=auth_headers)).status_code == 200
    assert await sponsored_in_blocks()


@pytest.mark.asyncio
async def test_spots_off_previews_say_why_nothing_is_planned(client, auth_headers, db_session):
    """F-MODES-28: with Spots mode off the Spot Schedule look-ahead and the Schedule check plan
    nothing; both answers carry spots_off so the pages say why instead of showing every
    spot as missing."""
    st, _, _ = await _sponsored_station(db_session)
    r = await client.get("/api/v1/spot-schedules/preview", params={"station_id": str(st.id)}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["planned"] and r.json()["spots_off"] is False
    await client.post(f"/api/v1/stations/{st.id}/queue/modes", json={"spots": False}, headers=auth_headers)
    r = await client.get("/api/v1/spot-schedules/preview", params={"station_id": str(st.id)}, headers=auth_headers)
    body = r.json()
    assert body["spots_off"] is True and body["planned"] == [] and body["unplaced"] == []
    r = await client.get("/api/v1/schedules/look-ahead", params={"station_id": str(st.id)}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["spots_off"] is True
    assert not [f for f in r.json()["findings"] if f["kind"] == "spot_unplaced"]


# ── API, roles, persistence, engine, activity log, speed ──────────────────────


@pytest.mark.asyncio
async def test_modes_api_empty_body_both_switches_and_unknown_station(client, auth_headers, db_session):
    """F-MODES-23: an empty body changes nothing and answers the current modes; both switches
    in one call; a station that does not exist is a 404."""
    st = await _station(db_session, automation_config={"spots_on": False, "loop_asset_id": "x"})
    await db_session.commit()
    url = f"/api/v1/stations/{st.id}/queue/modes"
    r = await client.post(url, json={}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["modes"] == {"hard_start": True, "spots": False}
    assert r.json()["queue"]["modes"] == {"hard_start": True, "spots": False}
    r = await client.post(url, json={"hard_start": False, "spots": True}, headers=auth_headers)
    assert r.json()["modes"] == {"hard_start": False, "spots": True}
    async with TestSessionLocal() as other:
        cfg = (await other.get(Station, st.id)).automation_config
    assert cfg == {"spots_on": True, "loop_asset_id": "x", "hard_start": False}
    r = await client.post(f"/api/v1/stations/{uuid.uuid4()}/queue/modes", json={"hard_start": False},
                          headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_modes_and_panic_roles_and_station_assignment(client, db_session):
    """F-MODES-15: DJ, manager and admin may switch modes and press Panic; a viewer is
    refused; staff not assigned to the station, or not assigned to a protected station,
    are refused."""
    fm = await _station(db_session, protected=True)
    web = Station(id=uuid.uuid4(), name="Web", timezone="UTC")
    other = Station(id=uuid.uuid4(), name="Other", timezone="UTC")
    db_session.add_all([web, other])
    await db_session.commit()

    async def status(headers, st, path):
        body = {"hard_start": True} if path == "modes" else None
        return (await client.post(f"/api/v1/stations/{st.id}/queue/{path}", json=body, headers=headers)).status_code

    for role in (UserRole.DJ, UserRole.MANAGER, UserRole.ADMIN):
        h = await _user(client, db_session, role)
        for path in ("modes", "panic"):
            assert await status(h, web, path) == 200, (role, path)
            want = 200 if role == UserRole.ADMIN else 403
            assert await status(h, fm, path) == want, (role, path, "protected")
    viewer = await _user(client, db_session, UserRole.VIEWER)
    for path in ("modes", "panic"):
        assert await status(viewer, web, path) == 403
    dj_web = await _user(client, db_session, UserRole.DJ, station_ids=[str(web.id)])
    dj_fm = await _user(client, db_session, UserRole.DJ, station_ids=[str(fm.id)])
    for path in ("modes", "panic"):
        assert await status(dj_web, web, path) == 200
        assert await status(dj_web, other, path) == 403
        assert await status(dj_fm, fm, path) == 200


@pytest.mark.asyncio
async def test_modes_are_kept_in_the_station_settings_for_every_session(client, auth_headers, db_session):
    """F-MODES-16 and -39: the switches live in automation_config, so a restart, deploy or
    another scheduler leader (a new session) reads the same state, and every control room's
    queue body carries it."""
    st = await _station(db_session)
    await db_session.commit()
    await client.post(f"/api/v1/stations/{st.id}/queue/modes", json={"hard_start": False, "spots": False},
                      headers=auth_headers)
    async with TestSessionLocal() as fresh:  # a new process
        from app.services.station_modes import modes
        assert modes(await fresh.get(Station, st.id)) == {"hard_start": False, "spots": False}
    body = (await client.get(f"/api/v1/stations/{st.id}/queue", headers=auth_headers)).json()
    assert body["modes"] == {"hard_start": False, "spots": False}


@pytest.mark.asyncio
async def test_mode_change_is_pushed_to_every_open_control_room(client, auth_headers, db_session, monkeypatch):
    """F-MODES-39: the mode change goes out on the station's now-playing channel at once, so
    other control rooms do not wait for their 10 s queue poll."""
    from app.api.v1 import websocket

    sent = []

    async def capture(station_id, message):
        sent.append((station_id, message))

    monkeypatch.setattr(websocket.manager, "broadcast_to_station", capture)
    st = await _station(db_session)
    await db_session.commit()
    await client.post(f"/api/v1/stations/{st.id}/queue/modes", json={"spots": False}, headers=auth_headers)
    assert sent == [(str(st.id), {"type": "modes", "data": {"station_id": str(st.id), "hard_start": True,
                                                              "spots": False}})]


@pytest.mark.asyncio
async def test_mode_change_flushes_the_engine(client, auth_headers, db_session, monkeypatch):
    """F-MODES-29: on a stream-server station a mode change flushes the pre-loaded track so a
    cued spot or timed item is asked for again; a browser station sends nothing."""
    st = await _station(db_session)
    await db_session.commit()
    flushed = []

    async def flush(station_id):
        flushed.append(str(station_id))
        return True

    monkeypatch.setattr(playout_engine, "engine_flush", flush)
    url = f"/api/v1/stations/{st.id}/queue/modes"
    await client.post(url, json={"spots": False}, headers=auth_headers)
    assert flushed == []
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", str(st.id))
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", "t" * 32)
    await client.post(url, json={"hard_start": False}, headers=auth_headers)
    assert flushed == [str(st.id)]


@pytest.mark.asyncio
async def test_mode_changes_and_panic_are_in_the_activity_log(client, auth_headers, db_session):
    """F-MODES-40: named rows with who, the station, and the station setting before/after."""
    activity_log.INLINE = True
    st = await _station(db_session)
    song = _asset("Song", "music", 200.0)
    db_session.add(song)
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, position=1, status="playing",
                              source="auto", started_at=datetime.now(UTC)))
    await db_session.commit()
    await client.post(f"/api/v1/stations/{st.id}/queue/modes", json={"hard_start": False}, headers=auth_headers)
    await client.post(f"/api/v1/stations/{st.id}/queue/panic", headers=auth_headers)
    await activity_log.wait_idle()
    items = (await client.get("/api/v1/activity", params={"category": "control"}, headers=auth_headers)).json()["items"]
    labels = {i["label"]: i for i in items}
    assert "Switched HS / Spots mode" in labels and "Pressed Panic" in labels, list(labels)
    row = labels["Switched HS / Spots mode"]
    assert row["station_id"] == str(st.id) and row["user_email"] == "testadmin@test.com"
    assert "hard_start" in str(row.get("changes")), row


@pytest.mark.asyncio
async def test_modes_and_panic_answer_within_300_ms(client, auth_headers, db_session):
    """F-MODES-19 (server half): POST /modes (Spots mode included: 7 days of ad slots and the
    block items rebuilt) and POST /panic answer within S-3 (300 ms; CI allowance via limit())."""
    st, _, _ = await _sponsored_station(db_session)
    song = _asset("Song", "music", 200.0)
    db_session.add(song)
    await db_session.flush()
    db_session.add_all([QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, position=i + 1,
                                   status="playing" if i == 0 else "pending", source="auto",
                                   started_at=datetime.now(UTC) if i == 0 else None) for i in range(30)])
    await db_session.commit()
    url = f"/api/v1/stations/{st.id}/queue"
    await client.post(f"{url}/modes", json={}, headers=auth_headers)  # warm up imports
    for body in ({"spots": False}, {"spots": True}, {"hard_start": False}, {"hard_start": True}):
        t0 = time.perf_counter()
        r = await client.post(f"{url}/modes", json=body, headers=auth_headers)
        took = time.perf_counter() - t0
        assert r.status_code == 200 and took < limit(0.3), f"{body} took {took * 1000:.0f} ms"
    t0 = time.perf_counter()
    r = await client.post(f"{url}/panic", headers=auth_headers)
    assert r.status_code == 200 and time.perf_counter() - t0 < limit(0.3)


@pytest.mark.asyncio
async def test_panic_alert_resolved_by_done(client, auth_headers, db_session):
    """F-MODES-13: one warning alert for this station naming who pressed; Done (PATCH resolve)
    takes it out of the station's open list and the Alerts page shows it resolved."""
    st = await _station(db_session)
    song = _asset("Song", "music", 200.0)
    db_session.add(song)
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, position=1, status="playing",
                              source="auto", started_at=datetime.now(UTC)))
    await db_session.commit()
    await client.post(f"/api/v1/stations/{st.id}/queue/panic", headers=auth_headers)
    open_ = (await client.get("/api/v1/alerts", params={"is_resolved": False, "station_id": str(st.id)},
                              headers=auth_headers)).json()["alerts"]
    assert [a["title"] for a in open_] == ["Panic pressed in the control room"]
    a = open_[0]
    assert a["severity"] == "warning" and a["station_id"] == str(st.id) and "testadmin@test.com" in a["message"]
    r = await client.patch(f"/api/v1/alerts/{a['id']}/resolve", headers=auth_headers)
    assert r.status_code == 200 and r.json()["is_resolved"] is True
    open_ = (await client.get("/api/v1/alerts", params={"is_resolved": False, "station_id": str(st.id)},
                              headers=auth_headers)).json()["alerts"]
    assert open_ == []
    done = (await client.get("/api/v1/alerts", params={"is_resolved": True}, headers=auth_headers)).json()["alerts"]
    assert a["id"] in [x["id"] for x in done]
    assert len((await db_session.execute(select(Alert))).scalars().all()) == 1


# ── Song request "Match" ──────────────────────────────────────────────────────


async def _request_setup(db, status=RequestStatus.PENDING, **kw):
    st = await _station(db)
    song = _asset("One Day", "music", 200.0, artist="Matisyahu")
    other = _asset("Rachem", "music", 200.0, artist="Shwekey")
    db.add_all([song, other])
    req = SongRequest(id=uuid.uuid4(), station_id=st.id, requester_name="Listener",
                      song_title="One day by matisyahu", status=status, **kw)
    db.add(req)
    await db.commit()
    return st, song, other, req


async def _queued(db, st):
    return [str(a) for a in (await db.execute(select(QueueEntry.asset_id).where(
        QueueEntry.station_id == st.id, QueueEntry.status == "pending"))).scalars().all()]


@pytest.mark.asyncio
async def test_match_on_a_pending_request_sets_the_song_then_approve_queues_it(client, auth_headers, db_session):
    """F-MATCH-3: picking a song on a pending request sets it and keeps it pending; Approve
    then queues it."""
    st, song, _, req = await _request_setup(db_session)
    r = await client.patch(f"/api/v1/song-requests/{req.id}", json={"asset_id": str(song.id)}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["asset_id"] == str(song.id)
    assert await _queued(db_session, st) == []
    r = await client.patch(f"/api/v1/song-requests/{req.id}", json={"status": "approved"}, headers=auth_headers)
    assert r.json()["status"] == "queued"
    assert await _queued(db_session, st) == [str(song.id)]


@pytest.mark.asyncio
async def test_match_an_unknown_request_is_404(client, auth_headers, db_session):
    """F-MATCH-5: a request that does not exist answers 404."""
    _, song, _, _ = await _request_setup(db_session)
    r = await client.patch(f"/api/v1/song-requests/{uuid.uuid4()}", json={"asset_id": str(song.id)},
                           headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_matching_a_queued_request_is_refused(client, auth_headers, db_session):
    """F-MATCH-13: re-matching a request already queued only changed its song: the old song
    stayed in Up next and the new one was never queued. Fixed: answered 409, nothing changes."""
    st, song, other, req = await _request_setup(db_session, status=RequestStatus.APPROVED)
    await client.patch(f"/api/v1/song-requests/{req.id}", json={"asset_id": str(song.id)}, headers=auth_headers)
    assert await _queued(db_session, st) == [str(song.id)]
    r = await client.patch(f"/api/v1/song-requests/{req.id}", json={"asset_id": str(other.id)}, headers=auth_headers)
    assert r.status_code == 409, r.text
    await db_session.refresh(req)
    assert str(req.asset_id) == str(song.id)
    assert await _queued(db_session, st) == [str(song.id)]


@pytest.mark.asyncio
async def test_song_and_approve_in_one_call_queue_once(client, auth_headers, db_session):
    """F-MATCH-14: one call with a song and status approved queues the song once."""
    st, song, _, req = await _request_setup(db_session)
    r = await client.patch(f"/api/v1/song-requests/{req.id}",
                           json={"asset_id": str(song.id), "status": "approved"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "queued"
    assert await _queued(db_session, st) == [str(song.id)]
    # approved earlier with no match, then song + approved: still once
    st2, song2, _, req2 = await _request_setup(db_session, status=RequestStatus.APPROVED)
    await client.patch(f"/api/v1/song-requests/{req2.id}", json={"asset_id": str(song2.id), "status": "approved"},
                       headers=auth_headers)
    assert await _queued(db_session, st2) == [str(song2.id)]


@pytest.mark.asyncio
async def test_hand_matched_requests_count_like_automatic_ones(client, auth_headers, db_session):
    """F-MATCH-19: hand-matched requests count in "most requested" and toward the
    "requested" category."""
    from app.services.requested_category_service import refresh_requested_category

    st, song, _, _ = await _request_setup(db_session)
    song.category = "lively"
    reqs = [SongRequest(id=uuid.uuid4(), station_id=st.id, requester_name="r", song_title="one day",
                        status=RequestStatus.PENDING) for _ in range(3)]
    db_session.add_all(reqs)
    await db_session.commit()
    for r in reqs:
        assert (await client.patch(f"/api/v1/song-requests/{r.id}", json={"asset_id": str(song.id)},
                                   headers=auth_headers)).status_code == 200
    top = (await client.get("/api/v1/song-requests/analytics/top-requested", headers=auth_headers)).json()
    first = top["top_requested"][0]
    assert first["request_count"] >= 3 and "One Day" in str(first)
    assert await refresh_requested_category(db_session) == 1


@pytest.mark.asyncio
async def test_a_song_matched_during_shabbos_or_a_live_show_does_not_air(client, auth_headers, db_session):
    """F-MATCH-20: a song matched during a blackout waits in Up next (Next/Panic start only
    the silence); during a live show nothing from the queue starts."""
    from app.services import live_show_service

    st, song, _, req = await _request_setup(db_session, status=RequestStatus.APPROVED)
    silence = _asset("Shabbos", "silence", 3600.0)
    db_session.add(silence)
    await db_session.flush()
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=silence.id, position=0, status="playing",
                              source="auto", started_at=datetime.now(UTC) - timedelta(seconds=5)))
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", reason="shabbos", is_blackout=True,
                                 start_datetime=datetime.now(UTC) - timedelta(hours=1),
                                 end_datetime=datetime.now(UTC) + timedelta(hours=20),
                                 affected_stations={"station_ids": [str(st.id)]}))
    await db_session.commit()
    r = await client.patch(f"/api/v1/song-requests/{req.id}", json={"asset_id": str(song.id)}, headers=auth_headers)
    assert r.json()["status"] == "queued"
    await client.post(f"/api/v1/stations/{st.id}/queue/panic", headers=auth_headers)
    playing = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == st.id, QueueEntry.status == "playing")
        .execution_options(populate_existing=True))).scalars().all()
    assert all(str(e.asset_id) != str(song.id) for e in playing)
    assert str(song.id) in await _queued(db_session, st)

    # Live show on another station
    st2, song2, _, req2 = await _request_setup(db_session, status=RequestStatus.APPROVED)
    now = datetime.now(UTC)
    show = LiveShow(id=uuid.uuid4(), station_id=st2.id, title="Live", status=LiveShowStatus.SCHEDULED,
                    scheduled_start=now, scheduled_end=now + timedelta(hours=1))
    db_session.add(show)
    await db_session.commit()
    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    await client.patch(f"/api/v1/song-requests/{req2.id}", json={"asset_id": str(song2.id)}, headers=auth_headers)
    await client.post(f"/api/v1/stations/{st2.id}/queue/panic", headers=auth_headers)
    playing = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == st2.id, QueueEntry.status == "playing")
        .execution_options(populate_existing=True))).scalars().all()
    assert playing == []


@pytest.mark.asyncio
async def test_auto_approve_under_the_daily_limit_queues_then_stops(client, db_session):
    """B-SONGREQUES-3: a song that allows auto-approval is queued while under
    max_requests_per_day; the request over the limit waits for review."""
    st = await _station(db_session)
    song = _asset("Lecha Dodi", "music", 200.0, artist="Carlebach",
                  metadata_extra={"auto_approve_requests": True, "max_requests_per_day": 2})
    db_session.add(song)
    await db_session.commit()
    out = []
    for i in range(3):
        r = await client.post("/api/v1/song-requests", json={
            "station_id": str(st.id), "requester_name": f"L{i}", "song_title": "Lecha Dodi", "song_artist": "Carlebach"})
        assert r.status_code == 201, r.text
        out.append(r.json()["status"])
    # Was ["queued", "pending", "pending"]: the first request counted twice (its queue
    # entry and the request row), halving the daily limit. Fixed.
    assert out == ["queued", "queued", "pending"]
    assert len(await _queued(db_session, st)) == 2


@pytest.mark.asyncio
async def test_restore_reads_nothing_when_nothing_was_softened(db_session):
    """HS on with nothing saved is a no-op (no query of the queue)."""
    st = await _station(db_session)
    await db_session.commit()
    assert await restore_hard_items(db_session, st) == 0
