"""Checklist rows whose "covered" note named only a file: the behaviours no existing
test really asserted, pinned here (scheduling, sponsors, spots, weather, jobs).

Each test's docstring starts with the checklist row ids it pins.
"""
import io
import uuid
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import hash_password
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.program_block import ProgramBlock
from app.models.queue_entry import QueueEntry, is_soft_timed
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock
from app.models.sponsor import Sponsor
from app.models.station import Station
from app.models.user import User, UserRole
from app.services.queue_replenish_service import QueueReplenishService

API = "/api/v1"
ROLES = list(UserRole)


async def _login(client: AsyncClient, db: AsyncSession, role: UserRole, station_ids=None) -> tuple[User, dict]:
    email = f"{role.value}-{uuid.uuid4().hex[:8]}@test.com"
    user = User(id=uuid.uuid4(), email=email, hashed_password=hash_password("pw12345678"),
                role=role, is_active=True, station_ids=station_ids)
    db.add(user)
    await db.commit()
    r = await client.post(f"{API}/auth/login", json={"email": email, "password": "pw12345678"})
    assert r.status_code == 200, r.text
    return user, {"Authorization": f"Bearer {r.json()['access_token']}"}


def _song(category: str, n: int, seconds: float = 240.0, asset_type: str = "music") -> Asset:
    return Asset(id=uuid.uuid4(), title=f"{category} {n}", artist=f"{category} artist {n}",
                 duration=seconds, file_path=f"{uuid.uuid4().hex}.mp3",
                 asset_type=asset_type, category=category)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ── Read guards ──────────────────────────────────────────────────────────────

MANAGERS = {"admin", "manager"}
SPONSOR_OR_MANAGERS = {"admin", "manager", "sponsor"}
SPONSOR = {"sponsor"}
PUBLIC = None  # anyone, no login needed

_SID = str(uuid.uuid4())
_ID = str(uuid.uuid4())
READS = [
    # (row ids, path, allowed roles)
    ("B-COPYREVIEW-1", "/sponsorship/rules", SPONSOR_OR_MANAGERS),
    ("B-COPYREVIEW-3", "/sponsorship/rules/history", MANAGERS),
    ("B-HOLIDAYS-1", "/holidays", MANAGERS),
    ("B-PLAYLISTS-4", f"/playlists/generation?station_id={_SID}", MANAGERS),
    ("B-PLAYLISTS-9", f"/playlists/{_ID}", MANAGERS),
    ("B-PROGRAMBLO-1", f"/program-blocks?station_id={_SID}", MANAGERS),
    ("B-PROGRAMBLO-2", "/program-blocks/capabilities", MANAGERS),
    ("B-PROGRAMBLO-3", f"/program-blocks/preview?station_id={_SID}", MANAGERS),
    ("B-SCHEDULES-4", "/schedules", PUBLIC),
    ("B-SCHEDULES-8", "/schedules/blocks", PUBLIC),
    ("B-SCHEDULES-9", f"/schedules/blocks/{_ID}", PUBLIC),
    ("B-SCHEDULES-23", f"/schedules/look-ahead?station_id={_SID}", MANAGERS),
    ("B-SCHEDULES-26", f"/schedules/{_ID}", PUBLIC),
    ("B-SPONSORPOR-1", "/sponsor-portal/spots", SPONSOR),
    ("B-SPONSORPOR-2", "/sponsor-portal/play-history", SPONSOR),
    ("B-SPONSORPOR-3", "/sponsor-portal/upcoming-schedule", SPONSOR),
    ("B-SPONSORPOR-5", "/sponsor-portal/stats", SPONSOR),
    ("B-SPOTRECORD-1", "/spot-recordings", MANAGERS),
    ("B-SPOTRECORD-6", f"/spot-recordings/{_ID}/audio", MANAGERS),
    ("B-SPOTSCHEDU-1", "/spot-schedules", MANAGERS),
    ("B-SPOTSCHEDU-2", f"/spot-schedules/preview?station_id={_SID}", MANAGERS),
    ("B-SPOTSCHEDU-3", f"/spot-schedules/timing-defaults?station_id={_SID}", MANAGERS),
    ("B-WEATHERSPO-1", "/weather-spots/2026-11-16", MANAGERS),
    ("B-WEATHERSPO-5", "/time-announcements", MANAGERS),
]


@pytest.mark.asyncio
async def test_read_routes_follow_their_guard(client: AsyncClient, db_session: AsyncSession):
    """B-COPYREVIEW-1, B-COPYREVIEW-3, B-HOLIDAYS-1, B-PLAYLISTS-4, B-PLAYLISTS-9, B-PROGRAMBLO-1,
    B-PROGRAMBLO-2, B-PROGRAMBLO-3, B-SCHEDULES-4, B-SCHEDULES-8, B-SCHEDULES-9, B-SCHEDULES-23,
    B-SCHEDULES-26, B-SPONSORPOR-1/2/3/5, B-SPOTRECORD-1, B-SPOTRECORD-6, B-SPOTSCHEDU-1/2/3,
    B-WEATHERSPO-1, B-WEATHERSPO-5: each read is refused (403) to the roles its guard
    leaves out, and reached by the roles it names. Public reads need no login."""
    headers = {role.value: (await _login(client, db_session, role))[1] for role in ROLES}
    for row, path, allowed in READS:
        anon = await client.get(API + path)
        if allowed is PUBLIC:
            assert anon.status_code not in (401, 403), f"{row}: anonymous refused {path}"
            continue
        assert anon.status_code in (401, 403), f"{row}: anonymous reached {path}"
        for role, h in headers.items():
            r = await client.get(API + path, headers=h)
            if role in allowed:
                assert r.status_code not in (401, 403), f"{row}: {role} refused {path}: {r.status_code}"
            else:
                assert r.status_code == 403, f"{row}: {role} reached {path}: {r.status_code}"


# ── CRUD that only answered 204 / never checked the list ─────────────────────


@pytest.mark.asyncio
async def test_holiday_delete_really_removes_it(client: AsyncClient, auth_headers: dict):
    """B-HOLIDAYS-4: a deleted holiday is gone from the list."""
    r = await client.post(f"{API}/holidays", json={
        "name": "Gone", "start_datetime": "2030-12-01T17:00:00", "end_datetime": "2030-12-02T18:00:00",
    }, headers=auth_headers)
    hid = r.json()["id"]
    assert (await client.delete(f"{API}/holidays/{hid}", headers=auth_headers)).status_code == 204
    listed = (await client.get(f"{API}/holidays", headers=auth_headers)).json()["holidays"]
    assert hid not in {h["id"] for h in listed}


@pytest.mark.asyncio
async def test_sponsor_delete_really_removes_it(client: AsyncClient, auth_headers: dict):
    """B-SPONSORS-4: a deleted sponsor is gone from the list."""
    r = await client.post(f"{API}/sponsors", json={"name": "Gone Co", "length_seconds": 10.0,
                                                   "audio_file_path": "x.mp3"}, headers=auth_headers)
    sid = r.json()["id"]
    assert (await client.delete(f"{API}/sponsors/{sid}", headers=auth_headers)).status_code == 204
    assert sid not in {s["id"] for s in (await client.get(f"{API}/sponsors", headers=auth_headers)).json()}


@pytest.mark.asyncio
async def test_schedule_crud_filters_updates_and_deletes(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """B-SCHEDULES-4, B-SCHEDULES-8, B-SCHEDULES-9, B-SCHEDULES-12, B-SCHEDULES-27, B-SCHEDULES-28:
    lists filter by station / schedule, a schedule can be renamed and switched off,
    and deleted schedules and blocks are really gone."""
    a = Station(id=uuid.uuid4(), name="A", timezone="UTC")
    b = Station(id=uuid.uuid4(), name="B", timezone="UTC")
    db_session.add_all([a, b])
    await db_session.flush()
    sa = Schedule(id=uuid.uuid4(), station_id=a.id, name="A week")
    sb = Schedule(id=uuid.uuid4(), station_id=b.id, name="B week")
    db_session.add_all([sa, sb])
    await db_session.flush()
    ba = ScheduleBlock(id=uuid.uuid4(), schedule_id=sa.id, name="A block", start_time=time(6),
                       end_time=time(9), recurrence_type=RecurrenceType.DAILY)
    bb = ScheduleBlock(id=uuid.uuid4(), schedule_id=sb.id, name="B block", start_time=time(6),
                       end_time=time(9), recurrence_type=RecurrenceType.DAILY)
    db_session.add_all([ba, bb])
    await db_session.commit()
    sa_id, sb_id, ba_id, bb_id = str(sa.id), str(sb.id), str(ba.id), str(bb.id)

    listed = (await client.get(f"{API}/schedules", params={"station_id": str(a.id)})).json()
    assert [s["id"] for s in listed] == [sa_id]
    blocks = (await client.get(f"{API}/schedules/blocks", params={"schedule_id": sa_id})).json()
    assert [x["id"] for x in blocks] == [ba_id]
    one = await client.get(f"{API}/schedules/blocks/{ba_id}")
    assert one.status_code == 200 and one.json()["name"] == "A block"

    r = await client.patch(f"{API}/schedules/{sa_id}", json={"name": "Renamed", "is_active": False},
                           headers=auth_headers)
    assert r.status_code == 200, r.text
    got = (await client.get(f"{API}/schedules/{sa_id}")).json()
    assert got["name"] == "Renamed" and got["is_active"] is False

    assert (await client.delete(f"{API}/schedules/blocks/{bb_id}", headers=auth_headers)).status_code == 204
    assert (await client.get(f"{API}/schedules/blocks/{bb_id}")).status_code == 404
    assert (await client.delete(f"{API}/schedules/{sb_id}", headers=auth_headers)).status_code == 204
    assert (await client.get(f"{API}/schedules/{sb_id}")).status_code == 404


@pytest.mark.asyncio
async def test_spot_schedule_list_and_delete(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    """B-SPOTSCHEDU-1, B-SPOTSCHEDU-10: schedules are listed (by sponsor), and a deleted one
    is gone with its queued slots."""
    station = Station(id=uuid.uuid4(), name="KBR", timezone="America/New_York", is_active=True)
    spot = Asset(id=uuid.uuid4(), title="Spot", file_path="s.mp3", duration=30.0, asset_type="spot")
    db_session.add_all([station, spot])
    await db_session.commit()
    station_id = station.id
    sponsor = (await client.post(f"{API}/sponsors", json={"name": "Acme", "length_seconds": 30.0,
                                                          "audio_file_path": ""}, headers=auth_headers)).json()
    r = await client.post(f"{API}/spot-schedules", json={
        "sponsor_id": sponsor["id"], "asset_id": str(spot.id), "station_id": str(station.id),
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    listed = (await client.get(f"{API}/spot-schedules", params={"sponsor_id": sponsor["id"]},
                               headers=auth_headers)).json()
    assert [s["id"] for s in listed] == [sid]
    assert (await client.get(f"{API}/spot-schedules", params={"sponsor_id": str(uuid.uuid4())},
                             headers=auth_headers)).json() == []

    assert (await client.delete(f"{API}/spot-schedules/{sid}", headers=auth_headers)).status_code == 204
    assert (await client.get(f"{API}/spot-schedules", headers=auth_headers)).json() == []
    db_session.expire_all()
    slots = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station_id, QueueEntry.source == "ad_slot",
        QueueEntry.preempt_at > datetime.now(UTC) + timedelta(minutes=5),
    ))).scalars().all()
    assert slots == []


@pytest.mark.asyncio
async def test_spot_recordings_are_listed(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    """B-SPOTRECORD-1: a new recording shows in the list with its sponsor and status."""
    sponsor = Sponsor(id=uuid.uuid4(), name="Burger Bar", length_seconds=30, priority=1, audio_file_path="")
    db_session.add(sponsor)
    await db_session.commit()
    r = await client.post(f"{API}/spot-recordings", json={
        "sponsor_id": str(sponsor.id), "title": "Opening",
        "script_text": "Kol Berama sponsor Burger Bar. Catering for events.",
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    listed = (await client.get(f"{API}/spot-recordings", headers=auth_headers)).json()
    assert [(x["id"], x["title"], x["status"]) for x in listed] == [(r.json()["id"], "Opening", "draft")]
    assert str(listed[0]["sponsor_id"]) == str(sponsor.id)


# ── Program blocks ───────────────────────────────────────────────────────────


async def _block_station(db: AsyncSession):
    station = Station(id=uuid.uuid4(), name="KBR", timezone="UTC", is_active=True)
    jingle = Asset(id=uuid.uuid4(), title="Station ID", file_path="id.mp3", duration=8.0,
                   asset_type="jingle", category="station_id")
    psa1 = Asset(id=uuid.uuid4(), title="PSA one", file_path="p1.mp3", duration=30.0,
                 asset_type="spot", category="psa")
    psa2 = Asset(id=uuid.uuid4(), title="PSA two", file_path="p2.mp3", duration=40.0,
                 asset_type="spot", category="psa")
    db.add_all([station, jingle, psa1, psa2])
    await db.flush()
    return station, jingle, psa1, psa2


@pytest.mark.asyncio
async def test_program_blocks_are_listed_per_station(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """B-PROGRAMBLO-1: the list shows this station's blocks by name, not other stations'."""
    station, jingle, *_ = await _block_station(db_session)
    other = Station(id=uuid.uuid4(), name="Other", timezone="UTC")
    db_session.add(other)
    await db_session.flush()
    for st, name in ((station, "Zebra"), (station, "Alpha"), (other, "Elsewhere")):
        db_session.add(ProgramBlock(id=uuid.uuid4(), station_id=st.id, name=name, times="10:00",
                                    timing_mode="hard", items=[{"kind": "asset", "asset_id": str(jingle.id)}]))
    await db_session.commit()
    r = await client.get(f"{API}/program-blocks", params={"station_id": str(station.id)}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert [b["name"] for b in r.json()] == ["Alpha", "Zebra"]


@pytest.mark.asyncio
async def test_block_preview_changes_nothing(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    """B-PROGRAMBLO-4: the preview lists the next 24 hours' runs with their lengths and
    queues nothing."""
    station, jingle, psa1, _ = await _block_station(db_session)
    db_session.add(ProgramBlock(
        id=uuid.uuid4(), station_id=station.id, name="Hourly ID",
        times=",".join(f"{h:02d}:00" for h in range(24)), timing_mode="hard",
        items=[{"kind": "asset", "asset_id": str(jingle.id)}, {"kind": "asset", "asset_id": str(psa1.id)}],
    ))
    await db_session.commit()
    r = await client.get(f"{API}/program-blocks/preview", params={"station_id": str(station.id)},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    runs = r.json()["occurrences"]
    assert 23 <= len(runs) <= 24
    assert all(o["seconds"] == 38 for o in runs)
    queued = (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == station.id))).all()
    assert queued == []


@pytest.mark.asyncio
async def test_block_rotation_starts_with_the_longest_rested(db_session: AsyncSession):
    """B-JOB-80: a rotating block item picks the recording that has gone longest without
    airing (by last played), then takes turns."""
    from app.services.program_block_service import reconcile_block_entries

    station, _, psa1, psa2 = await _block_station(db_session)
    db_session.add(ProgramBlock(
        id=uuid.uuid4(), station_id=station.id, name="PSA",
        times=",".join(f"{h:02d}:00" for h in range(24)), timing_mode="soft",
        items=[{"kind": "category", "asset_type": "spot", "category": "psa"}],
    ))
    # "PSA one" sorts first, but it aired ten minutes ago
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=psa1.id,
                           start_utc=datetime.now(UTC) - timedelta(minutes=10)))
    await db_session.commit()
    psa1_id, psa2_id, station_id = psa1.id, psa2.id, station.id
    await reconcile_block_entries(db_session, station_id)
    await db_session.commit()
    rows = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.source == "block")
        .order_by(QueueEntry.preempt_at)
    )).scalars().all()
    assert len(rows) >= 4
    assert [str(e.asset_id) for e in rows[:4]] == [str(psa2_id), str(psa1_id), str(psa2_id), str(psa1_id)]
    assert all(e.timing_mode == "soft" for e in rows)


# ── Playlists ────────────────────────────────────────────────────────────────


async def _playlist_station(db: AsyncSession, categories=("Fast", "Relax"), config=None):
    station = Station(id=uuid.uuid4(), name="KBR", timezone="UTC", is_active=True, automation_config=config)
    db.add(station)
    await db.flush()
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    mix = PlaylistTemplate(id=uuid.uuid4(), name="Mix", station_id=station.id, is_active=True)
    db.add_all([schedule, mix])
    await db.flush()
    for i, c in enumerate(categories):
        db.add(TemplateSlot(id=uuid.uuid4(), template_id=mix.id, position=i, asset_type="music", category=c))
    block = ScheduleBlock(id=uuid.uuid4(), schedule_id=schedule.id, name="All day", start_time=time(0),
                          end_time=time(0), until_next=True, recurrence_type=RecurrenceType.DAILY,
                          playlist_template_id=mix.id)
    block.created_at = datetime.now(UTC) - timedelta(days=30)
    db.add(block)
    songs = [_song(c, n) for c in ("Fast", "Relax", "Old") for n in range(60)]
    db.add_all(songs)
    await db.commit()
    return station, schedule, mix, block, songs


@pytest.mark.asyncio
async def test_generation_settings_include_a_dry_run_that_changes_nothing(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """B-PLAYLISTS-4: GET /playlists/generation returns the settings and a preview of what
    would be queued, without queueing anything or moving the saved cursor."""
    station, *_ = await _playlist_station(db_session)
    station_id = station.id
    r = await client.get(f"{API}/playlists/generation", params={"station_id": str(station_id), "hours": 1},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False and body["preview_hours"] == 1
    assert len(body["preview"]) >= 10
    assert {p["category"] for p in body["preview"]} == {"Fast", "Relax"}
    assert all(p["block"] == "All day" for p in body["preview"])
    db_session.expire_all()
    assert (await db_session.execute(select(QueueEntry.id))).all() == []
    st = (await db_session.execute(select(Station).where(Station.id == station_id))).scalar_one()
    assert not (st.automation_config or {}).get("playlist_generation")


@pytest.mark.asyncio
async def test_template_get_update_delete_and_slot_update(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """B-PLAYLISTS-9, B-PLAYLISTS-10, B-PLAYLISTS-11, B-PLAYLISTS-13: a template is read
    with its slots, renamed and switched off, a slot's category changed, and a deleted
    template is gone (blocks that used it keep running without a playlist)."""
    station, _, mix, block, _ = await _playlist_station(db_session)
    mix_id, block_id = str(mix.id), block.id
    got = await client.get(f"{API}/playlists/{mix_id}", headers=auth_headers)
    assert got.status_code == 200, got.text
    assert [s["category"] for s in sorted(got.json()["slots"], key=lambda s: s["position"])] == ["Fast", "Relax"]

    r = await client.patch(f"{API}/playlists/{mix_id}", json={"name": "Morning", "is_active": False},
                           headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Morning" and r.json()["is_active"] is False

    slot_id = next(s["id"] for s in got.json()["slots"] if s["category"] == "Fast")
    r = await client.patch(f"{API}/playlists/slots/{slot_id}", json={"category": "Old", "position": 5},
                           headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["category"] == "Old" and r.json()["position"] == 5
    again = (await client.get(f"{API}/playlists/{mix_id}", headers=auth_headers)).json()
    assert {s["category"] for s in again["slots"]} == {"Old", "Relax"}

    assert (await client.delete(f"{API}/playlists/{mix_id}", headers=auth_headers)).status_code == 204
    assert (await client.get(f"{API}/playlists/{mix_id}", headers=auth_headers)).status_code == 404
    db_session.expire_all()
    kept = (await db_session.execute(select(ScheduleBlock).where(ScheduleBlock.id == block_id))).scalar_one()
    assert kept.playlist_template_id is None
    assert (await db_session.execute(select(TemplateSlot).where(TemplateSlot.template_id == mix.id))).all() == []


@pytest.mark.asyncio
async def test_block_edit_refreshes_queue_keeping_manual_and_timed(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """B-SCHEDULES-6, B-SCHEDULES-10, B-JOB-75: editing a block (here: a new playlist)
    replaces the unplayed automatic songs at once; what a person queued and timed items stay."""
    station, _, mix, block, songs = await _playlist_station(db_session, categories=("Fast",))
    relax = PlaylistTemplate(id=uuid.uuid4(), name="Relax only", station_id=station.id, is_active=True)
    db_session.add(relax)
    await db_session.flush()
    db_session.add(TemplateSlot(id=uuid.uuid4(), template_id=relax.id, position=0, asset_type="music",
                                category="Relax"))
    old = [s for s in songs if s.category == "Old"]
    auto_ids = []
    for i in range(20):
        e = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=old[i].id, position=i + 1,
                       status="pending", source="auto")
        db_session.add(e)
        auto_ids.append(e.id)
    manual = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=old[30].id, position=21,
                        status="pending", source="manual")
    timed = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=old[31].id, position=22,
                       status="pending", source="auto", preempt_at=datetime.now(UTC) + timedelta(hours=3))
    db_session.add_all([manual, timed])
    station_id, manual_id, timed_id, block_id = station.id, manual.id, timed.id, str(block.id)
    category = {s.id: s.category for s in songs}
    relax_id = str(relax.id)
    await db_session.commit()

    r = await client.patch(f"{API}/schedules/blocks/{block_id}", json={"playlist_template_id": relax_id},
                           headers=auth_headers)
    assert r.status_code == 200, r.text
    assert str(r.json()["playlist_template_id"]) == relax_id
    db_session.expire_all()
    pending = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station_id, QueueEntry.status == "pending"))).scalars().all()
    ids = {e.id for e in pending}
    assert not ids & set(auto_ids)
    assert manual_id in ids and timed_id in ids
    fresh = [e for e in pending if e.source == "auto" and e.preempt_at is None]
    assert fresh and {category[e.asset_id] for e in fresh} == {"Relax"}


@pytest.mark.asyncio
async def test_block_playlists_fill_ahead_with_generation_off(db_session: AsyncSession):
    """B-JOB-53: a station whose blocks have playlists fills from them a few hours ahead
    with generation switched off, and more than twice those hours of old fill is replaced."""
    station, _, _, _, songs = await _playlist_station(db_session)
    old = [s for s in songs if s.category == "Old"]
    for i in range(300):  # 20 hours of ordinary fill
        db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=old[i % 60].id,
                                  position=i + 1, status="pending", source="auto"))
    station_id = station.id
    category = {s.id: s.category for s in songs}
    await db_session.commit()
    await QueueReplenishService(db_session, station_id).replenish()
    await db_session.commit()
    pending = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station_id, QueueEntry.status == "pending"))).scalars().all()
    assert not [e for e in pending if category[e.asset_id] == "Old"]
    assert all(e.block_id is not None for e in pending)
    hours = sum(240 for _ in pending) / 3600
    assert 5.5 <= hours <= 7  # the default six hours ahead, not a week


@pytest.mark.asyncio
async def test_small_old_fill_is_kept_when_blocks_take_over(db_session: AsyncSession):
    """B-JOB-53: under twice the generation hours of old fill is kept, and topped up from the block."""
    station, _, _, _, songs = await _playlist_station(db_session)
    old = [s for s in songs if s.category == "Old"]
    old_ids = []
    for i in range(10):
        e = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=old[i].id, position=i + 1,
                       status="pending", source="auto")
        db_session.add(e)
        old_ids.append(e.id)
    station_id = station.id
    await db_session.commit()
    await QueueReplenishService(db_session, station_id).replenish()
    await db_session.commit()
    pending = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station_id, QueueEntry.status == "pending"))).scalars().all()
    assert set(old_ids) <= {e.id for e in pending}
    assert any(e.block_id is not None for e in pending)


@pytest.mark.asyncio
async def test_generation_without_any_sequence_fills_only_its_hours(db_session: AsyncSession):
    """B-JOB-68: generation on but no block with a sequence: ordinary music keeps the
    station on air, only as far ahead as `hours` (not the 7-day fill)."""
    station = Station(id=uuid.uuid4(), name="KBR", timezone="UTC", is_active=True,
                      automation_config={"playlist_generation": {"enabled": True, "hours": 1}})
    db_session.add(station)
    db_session.add_all([_song("Any", n) for n in range(80)])
    await db_session.commit()
    station_id = station.id
    await QueueReplenishService(db_session, station_id).replenish()
    await db_session.commit()
    pending = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station_id, QueueEntry.status == "pending"))).scalars().all()
    seconds = 240 * len(pending)
    assert 3600 - 240 <= seconds <= 3600 + 2 * 240


# ── Schedules and look-ahead ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_time_block_plays_only_on_its_start_date(db_session: AsyncSession):
    """B-SCHEDULES-7: a one-time block with a start date plays on that day only."""
    from app.services.scheduling import SchedulingService

    station = Station(id=uuid.uuid4(), name="KBR", timezone="UTC", is_active=True)
    schedule = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    db_session.add_all([station, schedule])
    block = ScheduleBlock(id=uuid.uuid4(), schedule_id=schedule.id, name="Special", start_time=time(10),
                          end_time=time(12), recurrence_type=RecurrenceType.ONE_TIME,
                          start_date=date(2026, 12, 3))
    db_session.add(block)
    await db_session.commit()
    svc = SchedulingService(db_session)
    on = await svc.get_active_block_for_station(station.id, datetime(2026, 12, 3, 11, tzinfo=UTC))
    assert on is not None and on.id == block.id
    for day in (2, 4):
        assert await svc.get_active_block_for_station(station.id, datetime(2026, 12, day, 11, tzinfo=UTC)) is None


@pytest.mark.asyncio
async def test_settling_with_another_stations_block_is_refused(client: AsyncClient, db_session: AsyncSession):
    """B-SCHEDULES-25: a manager limited to one station cannot settle using another
    station's block (403), and nothing changes."""
    mine = Station(id=uuid.uuid4(), name="Mine", timezone="UTC")
    theirs = Station(id=uuid.uuid4(), name="Theirs", timezone="UTC")
    db_session.add_all([mine, theirs])
    await db_session.flush()
    s1 = Schedule(id=uuid.uuid4(), station_id=mine.id, name="M")
    s2 = Schedule(id=uuid.uuid4(), station_id=theirs.id, name="T")
    db_session.add_all([s1, s2])
    await db_session.flush()
    b1 = ScheduleBlock(id=uuid.uuid4(), schedule_id=s1.id, name="a", start_time=time(1), end_time=time(2),
                       recurrence_type=RecurrenceType.DAILY)
    b2 = ScheduleBlock(id=uuid.uuid4(), schedule_id=s2.id, name="b", start_time=time(1), end_time=time(2),
                       recurrence_type=RecurrenceType.DAILY, priority=3)
    b3 = ScheduleBlock(id=uuid.uuid4(), schedule_id=s1.id, name="c", start_time=time(1), end_time=time(2),
                       recurrence_type=RecurrenceType.DAILY, priority=2)
    db_session.add_all([b1, b2, b3])
    await db_session.commit()
    ids = (str(b1.id), str(b2.id), str(b3.id))
    _, headers = await _login(client, db_session, UserRole.MANAGER, station_ids=[str(mine.id)])
    r = await client.post(f"{API}/schedules/look-ahead/settle",
                          json={"winner_block_id": ids[0], "other_block_ids": [ids[1]]}, headers=headers)
    assert r.status_code == 403
    r = await client.post(f"{API}/schedules/look-ahead/settle",
                          json={"winner_block_id": ids[1], "other_block_ids": [ids[0]]}, headers=headers)
    assert r.status_code == 403
    db_session.expire_all()
    pri = {str(b.id): b.priority for b in (await db_session.execute(select(ScheduleBlock))).scalars()}
    assert pri == {ids[0]: 0, ids[1]: 3, ids[2]: 2}
    # within the manager's own station it works and lifts the winner above the others
    r = await client.post(f"{API}/schedules/look-ahead/settle",
                          json={"winner_block_id": ids[0], "other_block_ids": [ids[2]]}, headers=headers)
    assert r.status_code == 200 and r.json()["priority"] == 3


@pytest.mark.asyncio
async def test_look_ahead_reports_unplaced_spots(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    """B-JOB-82: sponsor plays with no free slot show in the look-ahead as unplaced."""
    station = Station(id=uuid.uuid4(), name="KBR", timezone="America/New_York", is_active=True)
    spots = [Asset(id=uuid.uuid4(), title=f"Spot {n}", file_path=f"s{n}.mp3", duration=30.0, asset_type="spot")
             for n in range(2)]
    db_session.add_all([station, *spots])
    await db_session.commit()
    for n, spot in enumerate(spots):
        sponsor = (await client.post(f"{API}/sponsors", json={"name": f"Sponsor {n}", "length_seconds": 30.0,
                                                              "audio_file_path": ""}, headers=auth_headers)).json()
        r = await client.post(f"{API}/spot-schedules", json={
            "sponsor_id": sponsor["id"], "asset_id": str(spot.id), "station_id": str(station.id),
            "dayparts": "morning", "plays_per_daypart": 12,
        }, headers=auth_headers)
        assert r.status_code == 201, r.text
    r = await client.get(f"{API}/schedules/look-ahead", params={"station_id": str(station.id), "days": 3},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    unplaced = [f for f in r.json()["findings"] if f["kind"] == "spot_unplaced"]
    assert unplaced and all(f["needs_attention"] for f in unplaced)
    assert any("with no place (morning)" in f["title"] for f in unplaced)


# ── Sponsor portal ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upcoming_schedule_is_only_mine_soonest_first(client: AsyncClient, db_session: AsyncSession):
    """B-SPONSORPOR-4: upcoming lists only this sponsor's queued spots, soonest first."""
    user, headers = await _login(client, db_session, UserRole.SPONSOR)
    station = Station(id=uuid.uuid4(), name="KBR", timezone="UTC", is_active=True)
    me = Sponsor(id=uuid.uuid4(), name="Me", length_seconds=30, priority=1, audio_file_path="", user_id=user.id)
    other = Sponsor(id=uuid.uuid4(), name="Other", length_seconds=30, priority=1, audio_file_path="")
    db_session.add_all([station, me, other])
    await db_session.flush()
    mine = Asset(id=uuid.uuid4(), title="My spot", file_path="m.mp3", duration=30.0, asset_type="spot",
                 sponsor_id=me.id)
    theirs = Asset(id=uuid.uuid4(), title="Their spot", file_path="t.mp3", duration=30.0, asset_type="spot",
                   sponsor_id=other.id)
    db_session.add_all([mine, theirs])
    now = datetime.now(UTC)
    for i, (asset, hours) in enumerate(((mine, 3), (mine, 1), (theirs, 0.5), (mine, 2))):
        db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id, position=i + 1,
                                  status="pending", source="ad_slot", preempt_at=now + timedelta(hours=hours)))
    await db_session.commit()
    up = (await client.get(f"{API}/sponsor-portal/upcoming-schedule", headers=headers)).json()
    assert [u["asset_title"] for u in up] == ["My spot"] * 3
    times = [datetime.fromisoformat(u["scheduled_at"].replace("Z", "+00:00")) for u in up]
    assert times == sorted(times)


# ── Weather and time uploads ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_weather_upload_empty_and_too_large(client: AsyncClient, auth_headers: dict, monkeypatch):
    """B-WEATHERSPO-3: an empty file is refused with 422 and one over the limit with 413."""
    from app.api.v1 import weather_spots

    def put(data: bytes):
        return client.put(f"{API}/weather-spots/2026-11-16",
                          files={"file": ("w.mp3", io.BytesIO(data), "audio/mpeg")}, headers=auth_headers)

    assert (await put(b"")).status_code == 422
    monkeypatch.setattr(weather_spots, "MAX_UPLOAD_BYTES", 10)
    assert (await put(b"x" * 11)).status_code == 413
    r = await client.put(f"{API}/time-announcements/-1",
                         files={"file": ("t.mp3", io.BytesIO(b"abc"), "audio/mpeg")}, headers=auth_headers)
    assert r.status_code == 422


# ── Hourly time and weather, ad slots ────────────────────────────────────────


async def _hourly_station(db: AsyncSession, config: dict):
    station = Station(id=uuid.uuid4(), name="KBR", timezone="UTC", is_active=True, automation_config=config)
    clock = Asset(id=uuid.uuid4(), title="Time", duration=5.0, file_path="t.mp3", asset_type="jingle",
                  category="time_announcement")
    weather = Asset(id=uuid.uuid4(), title="Weather", duration=40.0, file_path="w.mp3", asset_type="spot",
                    category="weather_spot")
    other = Asset(id=uuid.uuid4(), title="Sponsor", duration=30.0, file_path="s.mp3", asset_type="spot")
    db.add_all([station, clock, weather, other])
    await db.commit()
    return station, clock, weather, other


async def _run_hourly(db: AsyncSession, station_id, clock, weather):
    with patch("app.services.weather_spot_service.get_or_create_weather_spot_assets",
               new=AsyncMock(return_value=(clock, weather))):
        svc = QueueReplenishService(db, station_id)
        svc.max_pos = 100
        await svc._load_automation_config()
        await svc._schedule_hourly_announcements()
        await db.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("weather_enabled,interval,half", [(True, 30, True), (True, 60, False), (False, 30, False)])
async def test_hourly_time_and_weather_for_48_hours(
    db_session: AsyncSession, monkeypatch, weather_enabled, interval, half
):
    """B-JOB-55, B-JOB-56, B-JOB-57: time and weather are queued at every hh:00 for the
    next 48 hours with preempt_at, one set per hour (an ad slot or block at the same
    minute does not count), hard on the hour; a :30 weather (soft) only when weather
    is on and the interval is 30 minutes; a second pass adds nothing."""
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    station, clock, weather, other = await _hourly_station(db_session, {
        "hourly_time_announcement": True, "weather_enabled": weather_enabled,
        "weather_interval_minutes": interval,
    })
    hour = (datetime.now(UTC) + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    db_session.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=other.id, position=1, status="pending",
                   source="ad_slot", preempt_at=hour),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=other.id, position=2, status="pending",
                   source="block", preempt_at=hour + timedelta(hours=1)),
    ])
    await db_session.commit()
    station_id = station.id
    await _run_hourly(db_session, station_id, clock, weather)

    def rows():
        return db_session.execute(select(QueueEntry).where(
            QueueEntry.station_id == station_id, QueueEntry.asset_id.in_([clock.id, weather.id])))

    entries = (await rows()).scalars().all()
    tops = [e for e in entries if _utc(e.preempt_at).minute == 0]
    by_hour: dict = {}
    for e in tops:
        by_hour.setdefault(_utc(e.preempt_at), []).append(e.asset_id)
    assert 46 <= len(by_hour) <= 48
    assert all(sorted(map(str, v)) == sorted([str(clock.id), str(weather.id)]) for v in by_hour.values())
    assert hour in by_hour and hour + timedelta(hours=1) in by_hour
    assert max(by_hour) <= datetime.now(UTC) + timedelta(hours=48)
    assert all(e.timing_mode == "hard" for e in tops)
    halves = [e for e in entries if _utc(e.preempt_at).minute == 30]
    if half:
        assert len(halves) >= 46 and {str(e.asset_id) for e in halves} == {str(weather.id)}
        assert all(e.timing_mode == "soft" for e in halves)
    else:
        assert halves == []

    count = len(entries)
    await _run_hourly(db_session, station_id, clock, weather)
    assert len((await rows()).scalars().all()) == count


@pytest.mark.asyncio
async def test_hourly_timing_follows_the_station_default(db_session: AsyncSession, monkeypatch):
    """B-JOB-56: a station whose default makes the on-the-hour slot soft gets soft time checks."""
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    station, clock, weather, _ = await _hourly_station(db_session, {
        "hourly_time_announcement": True, "spot_timing": {"on_the_hour": "soft", "other": "hard"},
    })
    station_id = station.id
    await _run_hourly(db_session, station_id, clock, weather)
    modes = {e.timing_mode for e in (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id))).scalars()}
    assert modes == {"soft"}


@pytest.mark.asyncio
async def test_no_clip_and_no_elevenlabs_queues_nothing_without_crashing(db_session: AsyncSession, monkeypatch):
    """B-JOB-61: with no uploaded clip and no ElevenLabs key, no time entry is queued and
    the pass does not fail."""
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    monkeypatch.setattr(settings, "ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(settings, "OPENWEATHERMAP_API_KEY", "")
    station, *_ = await _hourly_station(db_session, {"hourly_time_announcement": True})
    station_id = station.id
    svc = QueueReplenishService(db_session, station_id)
    svc.max_pos = 0
    await svc._load_automation_config()
    await svc._schedule_hourly_announcements()
    await db_session.commit()
    assert (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == station_id))).all() == []


@pytest.mark.asyncio
async def test_legacy_ad_slots_are_soft_at_their_minutes(db_session: AsyncSession):
    """B-JOB-65: with the legacy placeholder switched on, ad slots go at :15/:30/:45 by
    default (or the station's ad_slot_minutes), soft-timed."""
    station = Station(id=uuid.uuid4(), name="KBR", timezone="UTC", is_active=True,
                      automation_config={"ad_slot_legacy_spot": True})
    placeholder = Asset(id=uuid.uuid4(), title="KOL BRAMAH TEST SPONSOR", duration=39.0,
                        file_path="test.mp3", asset_type="spot")
    db_session.add_all([station, placeholder])
    await db_session.commit()
    station_id = station.id

    async def run():
        svc = QueueReplenishService(db_session, station_id)
        svc.max_pos = 0
        await svc._load_automation_config()
        await svc._schedule_ad_slots()
        await db_session.commit()
        return (await db_session.execute(select(QueueEntry).where(
            QueueEntry.station_id == station_id, QueueEntry.source == "ad_slot"))).scalars().all()

    slots = await run()
    assert slots and {_utc(e.preempt_at).minute for e in slots} == {15, 30, 45}
    assert all(is_soft_timed(e) for e in slots)

    for e in slots:
        await db_session.delete(e)
    station.automation_config = {"ad_slot_legacy_spot": True, "ad_slot_minutes": [20]}
    await db_session.commit()
    slots = await run()
    assert slots and {_utc(e.preempt_at).minute for e in slots} == {20}


@pytest.mark.asyncio
async def test_queued_placeholder_is_skipped_at_play_time(db_session: AsyncSession):
    """B-JOB-66: a placeholder that is somehow queued automatically is skipped when its
    turn comes, by the browser player and by the engine."""
    from app.api.v1 import queue as queue_module
    from app.services.playout_engine import pick_next

    station = Station(id=uuid.uuid4(), name="KBR", timezone="UTC")
    placeholder = Asset(id=uuid.uuid4(), title="KOL BRAMAH TEST SPONSOR", duration=39.0,
                        file_path="https://cdn.example.com/p.mp3", asset_type="spot")
    good = _song("Lively", 1)
    good.file_path = "https://cdn.example.com/g.mp3"
    db_session.add_all([station, placeholder, good])
    await db_session.flush()
    first = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=placeholder.id, position=1,
                       status="pending", source="auto")
    second = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=good.id, position=2,
                        status="pending", source="auto")
    db_session.add_all([first, second])
    await db_session.commit()
    picked = await queue_module._pick_next_pending(db_session, station.id, datetime.now(UTC))
    assert picked.id == second.id
    assert (await pick_next(db_session, station.id)).id == second.id
