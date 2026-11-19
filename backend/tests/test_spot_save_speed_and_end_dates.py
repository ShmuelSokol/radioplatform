"""Spot schedule saves stay quick with many stations (S-6), and the Databank spot
end-date edit (PATCH /databank/spots/{asset}/end-date) is safe and changes what airs.

A save that touches every station answers at once; the stations' queues are
brought in line just after the commit, in the background (spot_schedules.py).
"""
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api.v1 import spot_schedules as spot_api
from app.core.security import hash_password
from app.models.asset import Asset
from app.models.program_block import ProgramBlock
from app.models.queue_entry import QueueEntry
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.models.user import User, UserRole
from app.services.station_modes import set_modes
from tests.conftest import TestSessionLocal
from tests.timing import limit

MANY = 80


@pytest_asyncio.fixture(autouse=True)
async def _background_on_test_db(monkeypatch, db_session):
    """Background refreshes use the test database, one station at a time (SQLite
    allows one writer), and are finished before the tables are dropped."""
    monkeypatch.setattr(spot_api, "session_factory", TestSessionLocal)
    monkeypatch.setattr(spot_api, "BACKGROUND_CONCURRENCY", 1)
    yield
    await spot_api.wait_for_background()


async def _login(client, db, role, station_ids=None):
    email = f"{role.value}-{uuid.uuid4().hex[:6]}"
    db.add(User(id=uuid.uuid4(), email=email, hashed_password=hash_password("pw12345678"),
                role=role, is_active=True, station_ids=station_ids))
    await db.commit()
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345678"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _sponsor_and_spot(db):
    sponsor = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, priority=1, audio_file_path="")
    spot = Asset(id=uuid.uuid4(), title="Bagel King spot", duration=30.0, file_path="b.mp3",
                 asset_type="spot", category="sponsor")
    db.add_all([sponsor, spot])
    await db.flush()
    return sponsor, spot


async def _stations(db, n, **kw):
    rows = [Station(id=uuid.uuid4(), name=f"Station {i:02d} {uuid.uuid4().hex[:4]}", timezone="UTC", **kw) for i in range(n)]
    db.add_all(rows)
    await db.flush()
    return rows


async def _ad_slots(station_id, after=None):
    """Pending ad slots of a station, read in a fresh session (the background
    refresh writes through its own)."""
    async with TestSessionLocal() as s:
        q = select(QueueEntry).where(
            QueueEntry.station_id == station_id, QueueEntry.status == "pending", QueueEntry.source == "ad_slot"
        )
        rows = (await s.execute(q)).scalars().all()
    if after is not None:
        rows = [r for r in rows if r.preempt_at.replace(tzinfo=UTC) > after]
    return rows


class _Refreshes(list):
    """Every station refresh, and how many had run when the save committed."""
    at_commit = None


def _count_refreshes(monkeypatch):
    calls = _Refreshes()
    real = spot_api._refresh_station
    real_later = spot_api.refresh_in_background

    async def counted(db, station_id):
        calls.append(station_id)
        await real(db, station_id)

    def later(station_ids, **kw):
        calls.at_commit = len(calls)
        real_later(station_ids, **kw)

    monkeypatch.setattr(spot_api, "_refresh_station", counted)
    monkeypatch.setattr(spot_api, "refresh_in_background", later)
    return calls


EVERY_DAYPART = "morning,afternoon,evening,overnight"


@pytest.mark.asyncio
async def test_every_station_spot_schedule_save_answers_at_once(client, auth_headers, db_session, monkeypatch):
    """S-6 / F-SPOTSCHED-1: a spot schedule for all stations used to re-plan every
    station's queue inside the save (1.9-6.5 s with 80+ stations). The save now
    confirms under 1 s without re-planning any station in the request, and every
    station has the spot queued right after (create, change and remove)."""
    stations = await _stations(db_session, MANY)
    sponsor, spot = await _sponsor_and_spot(db_session)
    await db_session.commit()
    calls = _count_refreshes(monkeypatch)

    started = time.perf_counter()
    r = await client.post("/api/v1/spot-schedules", json={
        "sponsor_id": str(sponsor.id), "asset_id": str(spot.id), "dayparts": EVERY_DAYPART,
        "plays_per_daypart": 1, "start_date": date.today().isoformat(),
        "end_date": (date.today() + timedelta(days=3)).isoformat(),
    }, headers=auth_headers)
    took = time.perf_counter() - started
    assert r.status_code == 201, r.text
    assert calls.at_commit == 0, "no station is re-planned inside the save"
    assert took < limit(1.0), f"save took {took:.2f} s"
    schedule_id = r.json()["id"]

    await spot_api.wait_for_background()
    assert len(calls) == MANY
    for st in (stations[0], stations[-1]):
        assert await _ad_slots(st.id), "the on-air effect lands right after the save"

    # Change it: end today, so nothing later than today stays queued anywhere
    calls.clear()
    calls.at_commit = None
    started = time.perf_counter()
    r = await client.put(f"/api/v1/spot-schedules/{schedule_id}",
                         json={"end_date": date.today().isoformat()}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert calls.at_commit == 0 and time.perf_counter() - started < 1.0
    await spot_api.wait_for_background()
    tomorrow = datetime.combine(date.today() + timedelta(days=1), datetime.min.time(), UTC)
    for st in (stations[0], stations[-1]):
        assert await _ad_slots(st.id, after=tomorrow) == []

    # Remove it: answered at once, every station drops its slots, then the row goes
    started = time.perf_counter()
    r = await client.delete(f"/api/v1/spot-schedules/{schedule_id}", headers=auth_headers)
    assert r.status_code == 204, r.text
    assert time.perf_counter() - started < 1.0
    await spot_api.wait_for_background()
    soon = datetime.now(UTC) + timedelta(minutes=3)
    for st in (stations[0], stations[-1]):
        assert await _ad_slots(st.id, after=soon) == []
    async with TestSessionLocal() as s:
        assert (await s.get(SpotSchedule, uuid.UUID(schedule_id))) is None


@pytest.mark.asyncio
async def test_one_station_save_still_applies_inside_the_request(client, auth_headers, db_session):
    """S-6: a schedule for one station is applied before the save answers (fast
    enough), so Up next already shows it when the page reloads."""
    stations = await _stations(db_session, 3)
    sponsor, spot = await _sponsor_and_spot(db_session)
    await db_session.commit()
    r = await client.post("/api/v1/spot-schedules", json={
        "sponsor_id": str(sponsor.id), "asset_id": str(spot.id), "station_id": str(stations[1].id),
        "dayparts": EVERY_DAYPART, "plays_per_daypart": 1,
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    assert not spot_api._background
    assert await _ad_slots(stations[1].id)
    assert await _ad_slots(stations[0].id) == []


@pytest.mark.asyncio
async def test_station_less_end_date_needs_the_protected_station(client, db_session):
    """F-DATABANK-59: moving the end date of a station-less ("every station")
    schedule changes what the protected FM station airs, so a manager not
    assigned to it is refused (403) and nothing changes; one assigned to every
    station may."""
    fm, web = await _stations(db_session, 1, protected=True) + await _stations(db_session, 1)
    sponsor, spot = await _sponsor_and_spot(db_session)
    end = date.today() + timedelta(days=10)
    everywhere = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=None,
                              start_date=date.today(), end_date=end)
    db_session.add(everywhere)
    await db_session.commit()
    later = (date.today() + timedelta(days=40)).isoformat()

    unassigned = await _login(client, db_session, UserRole.MANAGER)  # every station but the protected one
    web_only = await _login(client, db_session, UserRole.MANAGER, [str(web.id)])
    for who in (unassigned, web_only):
        for q in ("", f"?station_id={web.id}"):
            r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date{q}",
                                   json={"end_date": later}, headers=who)
            assert r.status_code == 403, r.text
    await db_session.refresh(everywhere)
    assert everywhere.end_date == end

    both = await _login(client, db_session, UserRole.MANAGER, [str(fm.id), str(web.id)])
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={fm.id}",
                           json={"end_date": later}, headers=both)
    assert r.status_code == 200, r.text
    await db_session.refresh(everywhere)
    assert everywhere.end_date.isoformat() == later


@pytest.mark.asyncio
async def test_end_date_with_no_station_chosen_is_refused(client, auth_headers, db_session):
    """F-DATABANK-60: with no station chosen (station list still loading, none
    remembered) the page sends no station_id. That used to move the spot's
    schedules on every station; now it is 400 and nothing changes, unless every
    schedule of the spot is itself station-less (then it is the same edit on any
    station, and the every-station permission applies)."""
    a, b = await _stations(db_session, 2)
    sponsor, spot = await _sponsor_and_spot(db_session)
    end = date.today() + timedelta(days=10)
    on_a = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=a.id,
                        start_date=date.today(), end_date=end)
    on_b = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=b.id,
                        start_date=date.today(), end_date=end)
    db_session.add_all([on_a, on_b])
    await db_session.commit()
    later = (date.today() + timedelta(days=40)).isoformat()

    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date", json={"end_date": later},
                           headers=auth_headers)
    assert r.status_code == 400, r.text
    assert "station" in r.json()["detail"].lower()
    for s in (on_a, on_b):
        await db_session.refresh(s)
        assert s.end_date == end

    # With the station chosen, only that station's schedule moves
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={a.id}",
                           json={"end_date": later}, headers=auth_headers)
    assert r.status_code == 200, r.text
    await db_session.refresh(on_a)
    await db_session.refresh(on_b)
    assert on_a.end_date.isoformat() == later and on_b.end_date == end

    # A spot whose only schedule is station-less: no station needed (admin)
    sponsor2 = Sponsor(id=uuid.uuid4(), name="Fig Flip", length_seconds=30, priority=1, audio_file_path="")
    loose = Asset(id=uuid.uuid4(), title="Fig Flip", duration=30.0, file_path="f.mp3", asset_type="spot")
    db_session.add_all([sponsor2, loose])
    await db_session.flush()
    db_session.add(SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor2.id, asset_id=loose.id, station_id=None))
    await db_session.commit()
    r = await client.patch(f"/api/v1/databank/spots/{loose.id}/end-date", json={"end_date": later},
                           headers=auth_headers)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_station_less_end_date_with_many_stations_answers_at_once(
    client, auth_headers, db_session, monkeypatch
):
    """F-DATABANK-61 / F-DATABANK-26: the end-date edit of a station-less schedule
    rebuilt the ad slots and block items of every active station (80+) inside
    the request. It now answers under 1 s and the stations follow right after."""
    stations = await _stations(db_session, MANY)
    sponsor, spot = await _sponsor_and_spot(db_session)
    db_session.add(SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=None,
                                dayparts=EVERY_DAYPART, plays_per_daypart=1,
                                start_date=date.today(), end_date=date.today()))
    await db_session.commit()
    calls = _count_refreshes(monkeypatch)

    later = date.today() + timedelta(days=3)
    started = time.perf_counter()
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={stations[5].id}",
                           json={"end_date": later.isoformat()}, headers=auth_headers)
    took = time.perf_counter() - started
    assert r.status_code == 200, r.text
    assert calls.at_commit == 0 and took < limit(1.0), f"took {took:.2f} s"
    await spot_api.wait_for_background()
    assert len(calls) == MANY
    tomorrow = datetime.combine(date.today() + timedelta(days=1), datetime.min.time(), UTC)
    for st in (stations[0], stations[5], stations[-1]):
        assert await _ad_slots(st.id, after=tomorrow), "a later end puts the coming days' spots in Up next"


@pytest.mark.asyncio
async def test_end_date_rebuild_keeps_block_items_soft_while_hs_is_off(client, auth_headers, db_session):
    """F-DATABANK-61 (HS part): with HS off, the block items rebuilt by a spot
    change came back hard until the next scheduler pass (up to 3 s, enough to cut
    a song). They are softened again in the same call."""
    station = Station(id=uuid.uuid4(), name="KBR", timezone="UTC",
                      automation_config={"hard_start": False})
    sponsor, spot = await _sponsor_and_spot(db_session)
    psa = Asset(id=uuid.uuid4(), title="Station ID", duration=10.0, file_path="id.mp3", asset_type="jingle")
    db_session.add_all([station, psa])
    await db_session.flush()
    at = (datetime.now(UTC) + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    db_session.add(ProgramBlock(id=uuid.uuid4(), station_id=station.id, name="Weather Sponsors",
                                times=at.strftime("%H:%M"), timing_mode="hard",
                                items=[{"kind": "asset", "asset_id": str(psa.id)},
                                       {"kind": "sponsors", "count": 1}]))
    db_session.add(SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=station.id,
                                dayparts=EVERY_DAYPART, plays_per_daypart=1, start_date=date.today(),
                                end_date=date.today() + timedelta(days=1)))
    await db_session.commit()

    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={station.id}",
                           json={"end_date": (date.today() + timedelta(days=5)).isoformat()},
                           headers=auth_headers)
    assert r.status_code == 200, r.text
    async with TestSessionLocal() as s:
        block_items = (await s.execute(select(QueueEntry).where(
            QueueEntry.station_id == station.id, QueueEntry.status == "pending",
            QueueEntry.source == "block", QueueEntry.preempt_at.isnot(None),
        ))).scalars().all()
    assert block_items, "the block was rebuilt"
    assert {e.timing_mode for e in block_items} == {"soft"}
    assert all(e.hard_by is None for e in block_items)


@pytest.mark.asyncio
async def test_end_date_later_adds_and_today_removes_up_next_spots(client, auth_headers, db_session):
    """F-DATABANK-19 / F-DATABANK-39: the end-date edit changes what airs at once.
    With a station chosen, that station's schedule and the station-less one move
    (another station's own schedule does not); a later end adds the coming days'
    ad slots to Up next on every station the moved schedules play on, an end of
    today takes the later ones out."""
    a, b = await _stations(db_session, 2)
    sponsor, spot = await _sponsor_and_spot(db_session)
    today = date.today()
    mine = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=a.id,
                        dayparts=EVERY_DAYPART, plays_per_daypart=1, start_date=today, end_date=today)
    everywhere = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=None,
                              dayparts=EVERY_DAYPART, plays_per_daypart=1, start_date=today, end_date=today)
    other = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=b.id,
                         dayparts=EVERY_DAYPART, plays_per_daypart=1, start_date=today, end_date=today)
    db_session.add_all([mine, everywhere, other])
    await db_session.commit()
    tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time(), UTC)
    assert await _ad_slots(a.id, after=tomorrow) == []

    later = (today + timedelta(days=3)).isoformat()
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={a.id}",
                           json={"end_date": later}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 2
    for s in (mine, everywhere, other):
        await db_session.refresh(s)
    assert mine.end_date.isoformat() == later and everywhere.end_date.isoformat() == later
    assert other.end_date == today
    # Both stations hear the station-less schedule's later days
    assert await _ad_slots(a.id, after=tomorrow)
    assert await _ad_slots(b.id, after=tomorrow)

    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={a.id}",
                           json={"end_date": today.isoformat()}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert await _ad_slots(a.id, after=tomorrow) == []
    assert await _ad_slots(b.id, after=tomorrow) == []


@pytest.mark.asyncio
async def test_clearing_the_end_date_makes_every_schedule_open_ended(client, auth_headers, db_session):
    """F-DATABANK-40: an empty end date makes the spot's schedules open-ended."""
    (a,) = await _stations(db_session, 1)
    sponsor, spot = await _sponsor_and_spot(db_session)
    rows = [SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=sid,
                         start_date=date.today(), end_date=date.today() + timedelta(days=5))
            for sid in (a.id, None)]
    db_session.add_all(rows)
    await db_session.commit()
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={a.id}",
                           json={"end_date": None}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 2, "end_date": None}
    for s in rows:
        await db_session.refresh(s)
        assert s.end_date is None
    spots = (await client.get(f"/api/v1/databank/spots?station_id={a.id}", headers=auth_headers)).json()["spots"]
    row = next(s for s in spots if s["id"] == str(spot.id))
    assert row["open_ended"] is True and row["end_date"] is None


@pytest.mark.asyncio
async def test_end_date_errors_change_nothing(client, db_session):
    """F-DATABANK-20: an end before a schedule's start is 422 and a schedule on a
    station the manager is not assigned to is 403; nothing is changed by either."""
    a, b = await _stations(db_session, 2)
    sponsor, spot = await _sponsor_and_spot(db_session)
    start, end = date.today() + timedelta(days=5), date.today() + timedelta(days=20)
    row = SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=a.id,
                       start_date=start, end_date=end)
    db_session.add(row)
    await db_session.commit()
    manager_a = await _login(client, db_session, UserRole.MANAGER, [str(a.id)])
    manager_b = await _login(client, db_session, UserRole.MANAGER, [str(b.id)])

    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={a.id}",
                           json={"end_date": date.today().isoformat()}, headers=manager_a)
    assert r.status_code == 422, r.text
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={a.id}",
                           json={"end_date": (end + timedelta(days=9)).isoformat()}, headers=manager_b)
    assert r.status_code == 403, r.text
    await db_session.refresh(row)
    assert row.end_date == end


@pytest.mark.asyncio
async def test_spots_mode_change_with_hs_off_softens_rebuilt_block_items(db_session):
    """F-MODES-26: switching Spots mode while HS is off rebuilds the block items;
    they are softened again in the same call, so none is left hard."""
    station = Station(id=uuid.uuid4(), name="KBR", timezone="UTC", automation_config={"hard_start": False})
    psa = Asset(id=uuid.uuid4(), title="Station ID", duration=10.0, file_path="id.mp3", asset_type="jingle")
    db_session.add_all([station, psa])
    await db_session.flush()
    at = (datetime.now(UTC) + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    db_session.add(ProgramBlock(id=uuid.uuid4(), station_id=station.id, name="Top of the hour",
                                times=at.strftime("%H:%M"), timing_mode="hard",
                                items=[{"kind": "asset", "asset_id": str(psa.id)}]))
    await db_session.commit()

    await set_modes(db_session, station, hard_start=None, spots=False)
    await db_session.commit()
    rows = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.source == "block", QueueEntry.status == "pending",
    ))).scalars().all()
    assert rows, "the block was rebuilt"
    assert {e.timing_mode for e in rows} == {"soft"}


@pytest.mark.asyncio
async def test_managers_create_campaigns_for_a_sponsor(client, db_session):
    """V1-108: a manager could not create a campaign at all (always 403 "Managers
    must specify a sponsor_id", with no way to give one). Staff now name the
    sponsor; a sponsor login always creates for itself; a sponsor login with no
    sponsor record is still 403 and sees an empty list."""
    manager = await _login(client, db_session, UserRole.MANAGER)
    sponsor_user = User(id=uuid.uuid4(), email="shop@test.com", hashed_password=hash_password("pw12345678"),
                        role=UserRole.SPONSOR, is_active=True)
    db_session.add(sponsor_user)
    await db_session.flush()
    mine = Sponsor(id=uuid.uuid4(), name="A Shop", length_seconds=30, audio_file_path="", user_id=sponsor_user.id)
    other = Sponsor(id=uuid.uuid4(), name="B Shop", length_seconds=30, audio_file_path="")
    db_session.add_all([mine, other])
    await db_session.commit()

    r = await client.post("/api/v1/campaigns", json={"name": "Pesach sale"}, headers=manager)
    assert r.status_code == 422, r.text
    r = await client.post("/api/v1/campaigns", json={"name": "Pesach sale", "sponsor_id": str(uuid.uuid4())},
                          headers=manager)
    assert r.status_code == 404, r.text
    r = await client.post("/api/v1/campaigns", json={"name": "Pesach sale", "sponsor_id": str(other.id)},
                          headers=manager)
    assert r.status_code == 201, r.text
    assert r.json()["sponsor_id"] == str(other.id)

    login = await client.post("/api/v1/auth/login", json={"email": "shop@test.com", "password": "pw12345678"})
    shop = {"Authorization": f"Bearer {login.json()['access_token']}"}
    r = await client.post("/api/v1/campaigns", json={"name": "Mine", "sponsor_id": str(other.id)}, headers=shop)
    assert r.status_code == 201, r.text
    assert r.json()["sponsor_id"] == str(mine.id), "a sponsor cannot create for another sponsor"

    orphan = await _login(client, db_session, UserRole.SPONSOR)
    r = await client.post("/api/v1/campaigns", json={"name": "Nobody"}, headers=orphan)
    assert r.status_code == 403, r.text
    assert (await client.get("/api/v1/campaigns", headers=orphan)).json() == []


@pytest.mark.asyncio
async def test_copy_review_is_rate_limited(client, auth_headers, monkeypatch):
    """V1-145 (copy review part): the wording check allows 12 a minute per caller;
    the 13th is refused with 429."""
    from app.api.v1 import copy_review as copy_review_api

    async def quick(db, script_text, sponsor_name=None):
        return {"verdict": "ok", "summary": "Fine", "issues": [], "estimated_seconds": 3.0,
                "length_ok": True, "rules_version": 1, "reviewed_by": "basic_check"}

    monkeypatch.setattr(copy_review_api, "review_copy", quick)
    body = {"script_text": "Bagel King, fresh every morning"}
    codes = [(await client.post("/api/v1/sponsorship/copy-review", json=body, headers=auth_headers)).status_code
             for _ in range(13)]
    assert codes[:12] == [200] * 12, codes
    assert codes[12] == 429
