import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.sponsor import Sponsor
from app.models.station import Station
from app.models.user import User, UserRole
from app.services.queue_replenish_service import QueueReplenishService
from app.services.spot_schedule_service import plan_spots, reconcile_spot_slots

NY = ZoneInfo("America/New_York")


def _sched(name, dayparts="morning,afternoon,evening", plays=1, days="0,1,2,3,4,5,6", **kw):
    return SimpleNamespace(
        id=name,
        created_at=kw.pop("created_at", name),
        is_active=kw.pop("is_active", True),
        start_date=kw.pop("start_date", None),
        end_date=kw.pop("end_date", None),
        days_of_week=days,
        dayparts=dayparts,
        plays_per_daypart=plays,
        asset_id=f"asset-{name}",
        sponsor_id=f"sponsor-{name}",
    )


# ── planner (pure) ──────────────────────────────────────────────────


def test_morning_afternoon_evening_once_each():
    placed, unplaced = plan_spots([_sched("a")], NY, date(2026, 11, 16), 1)
    assert not unplaced
    assert [p.daypart for p in placed] == ["morning", "afternoon", "evening"]
    hours = [p.local.hour for p in placed]
    assert 6 <= hours[0] < 12 and 12 <= hours[1] < 18 and 18 <= hours[2] < 24
    # stored in UTC: New York is UTC-5 in November
    assert placed[0].at == placed[0].local.astimezone(UTC)
    assert placed[0].at.hour == placed[0].local.hour + 5


def test_two_sponsors_never_share_a_slot():
    placed, unplaced = plan_spots(
        [_sched("a", plays=3), _sched("b", plays=3)], NY, date(2026, 11, 16), 2
    )
    assert not unplaced
    times = [p.at for p in placed]
    assert len(times) == len(set(times)) == 2 * 3 * 3 * 2


def test_plan_is_stable_and_respects_days_and_dates():
    s = _sched("a", dayparts="morning", days="0", start_date=date(2026, 11, 20))
    first, _ = plan_spots([s], NY, date(2026, 11, 16), 14)
    again, _ = plan_spots([s], NY, date(2026, 11, 16), 14)
    assert [p.at for p in first] == [p.at for p in again]
    # Only Mondays on/after Nov 20 → Nov 23 only (within 14 days from Nov 16)
    assert [p.local.date() for p in first] == [date(2026, 11, 23)]


def test_full_daypart_reports_unplaced_instead_of_double_booking():
    # 6 hours × 1 slot/hour = 6 slots; ask for 4 + 4
    placed, unplaced = plan_spots(
        [_sched("a", dayparts="morning", plays=4), _sched("b", dayparts="morning", plays=4)],
        NY,
        date(2026, 11, 16),
        1,
        slot_minutes=[30],
    )
    assert len(placed) == 6 and len(unplaced) == 2
    assert {u.schedule.id for u in unplaced} == {"b"}


def test_inactive_schedule_gets_nothing():
    placed, _ = plan_spots([_sched("a", is_active=False)], NY, date(2026, 11, 16), 3)
    assert placed == []


# ── API + queue ─────────────────────────────────────────────────────


async def _setup(db: AsyncSession, client: AsyncClient, headers: dict):
    station = Station(id=uuid.uuid4(), name="KBR", timezone="America/New_York", is_active=True)
    db.add(station)
    assets = []
    for title in ("Spot A", "Spot B"):
        a = Asset(
            id=uuid.uuid4(),
            title=title,
            file_path=f"spots/{title}.mp3",
            duration=30.0,
            asset_type="spot",
        )
        db.add(a)
        assets.append(a)
    await db.commit()
    sponsors = []
    for name in ("Acme", "Widget"):
        r = await client.post(
            "/api/v1/sponsors",
            json={"name": name, "length_seconds": 30.0, "audio_file_path": ""},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        sponsors.append(r.json()["id"])
    return station, assets, sponsors


async def _ad_slots(db: AsyncSession, station_id):
    rows = await db.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station_id, QueueEntry.source == "ad_slot"
        )
    )
    return rows.scalars().all()


@pytest.mark.asyncio
async def test_schedule_fills_queue_and_preview(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, assets, sponsors = await _setup(db_session, client, auth_headers)

    r = await client.post(
        "/api/v1/spot-schedules",
        json={
            "sponsor_id": sponsors[0],
            "asset_id": str(assets[0].id),
            "station_id": str(station.id),
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    schedule_id = r.json()["id"]
    assert r.json()["dayparts"] == "morning,afternoon,evening"

    slots = await _ad_slots(db_session, station.id)
    # 3 a day for 7 days (first/last day may be partial)
    assert 18 <= len(slots) <= 21
    assert {str(s.asset_id) for s in slots} == {str(assets[0].id)}

    # Running the queue refill again must not duplicate or reshuffle anything
    before = sorted(str(s.id) for s in slots)
    svc = QueueReplenishService(db_session, station.id)
    svc.max_pos = 0
    await svc._load_automation_config()
    await svc._schedule_ad_slots()
    await db_session.commit()
    assert sorted(str(s.id) for s in await _ad_slots(db_session, station.id)) == before

    r = await client.get(
        f"/api/v1/spot-schedules/preview?station_id={station.id}&days=7", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    preview = r.json()
    assert len(preview["planned"]) == 21 and preview["unplaced"] == []
    assert preview["planned"][0]["sponsor_name"] == "Acme"

    # Evening only → morning/afternoon slots leave the queue
    r = await client.put(
        f"/api/v1/spot-schedules/{schedule_id}", json={"dayparts": "evening"}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    slots = await _ad_slots(db_session, station.id)
    assert 6 <= len(slots) <= 7
    for s in slots:
        at = s.preempt_at if s.preempt_at.tzinfo else s.preempt_at.replace(tzinfo=UTC)
        assert at.astimezone(NY).hour >= 18

    # Pause → nothing queued
    r = await client.put(
        f"/api/v1/spot-schedules/{schedule_id}", json={"is_active": False}, headers=auth_headers
    )
    assert r.status_code == 200
    assert await _ad_slots(db_session, station.id) == []

    r = await client.delete(f"/api/v1/spot-schedules/{schedule_id}", headers=auth_headers)
    assert r.status_code == 204


@pytest.mark.asyncio
async def test_validation(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    station, assets, sponsors = await _setup(db_session, client, auth_headers)
    base = {"sponsor_id": sponsors[0], "asset_id": str(assets[0].id)}
    r = await client.post(
        "/api/v1/spot-schedules", json={**base, "dayparts": "brunch"}, headers=auth_headers
    )
    assert r.status_code == 422
    r = await client.post(
        "/api/v1/spot-schedules", json={**base, "days_of_week": "7"}, headers=auth_headers
    )
    assert r.status_code == 422
    r = await client.post(
        "/api/v1/spot-schedules", json={**base, "asset_id": str(uuid.uuid4())}, headers=auth_headers
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_slots_about_to_air_are_left_alone(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, assets, sponsors = await _setup(db_session, client, auth_headers)
    soon = datetime.now(UTC) + timedelta(seconds=30)
    db_session.add(
        QueueEntry(
            id=uuid.uuid4(),
            station_id=station.id,
            asset_id=assets[1].id,
            position=1,
            status="pending",
            source="ad_slot",
            preempt_at=soon,
        )
    )
    await db_session.commit()
    r = await client.post(
        "/api/v1/spot-schedules",
        json={"sponsor_id": sponsors[0], "asset_id": str(assets[0].id)},
        headers=auth_headers,
    )
    assert r.status_code == 201
    slots = await _ad_slots(db_session, station.id)
    assert str(assets[1].id) in {str(s.asset_id) for s in slots}


@pytest.mark.asyncio
async def test_no_schedules_keeps_legacy_behaviour(db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="KBR", timezone="UTC", is_active=True)
    db_session.add(station)
    await db_session.commit()
    assert await reconcile_spot_slots(db_session, station.id) is None


@pytest.mark.asyncio
async def test_sponsor_portal_shows_only_their_real_plays(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, assets, sponsors = await _setup(db_session, client, auth_headers)

    user = User(
        id=uuid.uuid4(),
        email="acme@test.com",
        hashed_password=hash_password("pw123456"),
        role=UserRole.SPONSOR,
        is_active=True,
    )
    db_session.add(user)
    acme = (await db_session.execute(select(Sponsor).where(Sponsor.name == "Acme"))).scalar_one()
    acme.user_id = user.id
    await db_session.commit()

    for sponsor_id, asset in zip(sponsors, assets):
        r = await client.post(
            "/api/v1/spot-schedules",
            json={
                "sponsor_id": sponsor_id,
                "asset_id": str(asset.id),
                "station_id": str(station.id),
            },
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text

    now = datetime.now(UTC)
    # Acme aired twice, Widget once
    for asset, ago in ((assets[0], 3), (assets[0], 1), (assets[1], 2)):
        db_session.add(
            PlayLog(
                id=uuid.uuid4(),
                station_id=station.id,
                asset_id=asset.id,
                start_utc=now - timedelta(hours=ago),
                end_utc=now - timedelta(hours=ago) + timedelta(seconds=30),
                source="scheduler",
            )
        )
    await db_session.commit()

    login = await client.post(
        "/api/v1/auth/login", json={"email": "acme@test.com", "password": "pw123456"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    history = (await client.get("/api/v1/sponsor-portal/play-history", headers=headers)).json()
    assert history["total"] == 2
    assert {e["asset_title"] for e in history["entries"]} == {"Spot A"}

    stats = (await client.get("/api/v1/sponsor-portal/stats", headers=headers)).json()
    assert stats["total_plays_alltime"] == 2
    assert stats["last_played"] is not None and stats["next_scheduled"] is not None

    upcoming = (
        await client.get("/api/v1/sponsor-portal/upcoming-schedule", headers=headers)
    ).json()
    assert 18 <= len(upcoming) <= 21
    assert {u["asset_title"] for u in upcoming} == {"Spot A"}
    assert upcoming[0]["station_name"] == "KBR"

    spots = (await client.get("/api/v1/sponsor-portal/spots", headers=headers)).json()
    assert len(spots) == 1
    assert spots[0]["title"] == "Spot A" and spots[0]["plays_alltime"] == 2
    assert spots[0]["scheduled"] is True and spots[0]["next_play_at"] is not None
