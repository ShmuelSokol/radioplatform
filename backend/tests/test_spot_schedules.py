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
from app.models.holiday_window import HolidayWindow
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.sponsor import Sponsor
from app.models.station import Station
from app.models.user import User, UserRole
from app.services.queue_replenish_service import QueueReplenishService
from app.services.spot_schedule_service import Blackout, plan_spots, reconcile_spot_slots

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
        name=name,
        fixed_times=kw.pop("fixed_times", None),
        timing_mode=kw.pop("timing_mode", None),
    )


def test_exact_times_hard_on_the_hour_soft_elsewhere():
    """A spot can be pinned to clock times. On the hour is hard by default, the rest soft."""
    pinned = _sched("pinned", fixed_times="07:00,13:30,07:00", created_at="1")
    spread = _sched("spread", dayparts="afternoon", plays=18, created_at="2")
    placed, unplaced = plan_spots([pinned, spread], NY, date(2026, 11, 16), 1)
    mine = [p for p in placed if p.schedule is pinned]
    assert [(p.local.hour, p.local.minute, p.timing_mode, p.fixed) for p in mine] == [
        (7, 0, "hard", True),
        (13, 30, "soft", True),
    ]
    # the spread spots work around the pinned 1:30 and stay soft
    others = [p for p in placed if p.schedule is spread]
    assert all((p.local.hour, p.local.minute) != (13, 30) for p in others)
    assert len(others) == 17 and len(unplaced) == 1
    assert {p.timing_mode for p in others} == {"soft"} and not any(p.fixed for p in others)

    # the schedule's own choice wins over the default, and the station can change the default
    forced = _sched("forced", fixed_times="09:15", timing_mode="hard")
    placed, _ = plan_spots([forced], NY, date(2026, 11, 16), 1)
    assert placed[0].timing_mode == "hard"
    placed, _ = plan_spots(
        [pinned],
        NY,
        date(2026, 11, 16),
        1,
        timing_defaults={"on_the_hour": "soft", "other": "hard"},
    )
    assert [p.timing_mode for p in placed] == ["soft", "hard"]

    # two sponsors cannot have the same second; Shabbos is still off limits
    rival = _sched("rival", fixed_times="07:00", created_at="3")
    off = Blackout(
        datetime(2026, 11, 16, 13, 0, tzinfo=NY).astimezone(UTC),
        datetime(2026, 11, 16, 14, 0, tzinfo=NY).astimezone(UTC),
        "Shabbos",
    )
    placed, unplaced = plan_spots([pinned, rival], NY, date(2026, 11, 16), 1, blackouts=[off])
    assert [(p.schedule.id, p.local.hour) for p in placed] == [("pinned", 7)]
    assert sorted(u.reason for u in unplaced) == [
        "7:00 AM is already taken by pinned",
        "station is off the air (Shabbos)",
    ]


@pytest.mark.asyncio
async def test_exact_time_spot_reaches_the_queue_with_its_timing(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, assets, sponsors = await _setup(db_session, client, auth_headers)
    station_id, asset_id = station.id, str(assets[0].id)
    r = await client.post(
        "/api/v1/spot-schedules",
        json={
            "sponsor_id": sponsors[0],
            "asset_id": asset_id,
            "station_id": str(station_id),
            "fixed_times": "8:00, 08:30",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["fixed_times"] == "08:00,08:30" and body["timing_mode"] is None
    slots = await _ad_slots(db_session, station_id)
    assert 12 <= len(slots) <= 14
    for s in slots:
        local = (
            s.preempt_at.replace(tzinfo=UTC).astimezone(NY)
            if s.preempt_at.tzinfo is None
            else s.preempt_at.astimezone(NY)
        )
        assert (local.hour, local.minute) in {(8, 0), (8, 30)}
        assert s.timing_mode == ("hard" if local.minute == 0 else "soft")

    # switching the schedule to soft updates the entries already queued, in place
    before = sorted(str(s.id) for s in slots)
    r = await client.put(
        f"/api/v1/spot-schedules/{body['id']}", json={"timing_mode": "soft"}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    slots = await _ad_slots(db_session, station_id)
    assert sorted(str(s.id) for s in slots) == before
    assert {s.timing_mode for s in slots} == {"soft"}

    r = await client.get(
        f"/api/v1/spot-schedules/preview?station_id={station_id}&days=2", headers=auth_headers
    )
    assert all(p["fixed"] and p["timing_mode"] == "soft" for p in r.json()["planned"])

    # the station default: make the on-the-hour slot soft, and let the schedule follow it
    url = "/api/v1/spot-schedules/timing-defaults"
    r = await client.get(url, params={"station_id": str(station_id)}, headers=auth_headers)
    assert r.json() == {"on_the_hour": "hard", "other": "soft"}
    r = await client.put(
        f"/api/v1/spot-schedules/{body['id']}", json={"timing_mode": None}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    r = await client.put(
        url,
        json={"station_id": str(station_id), "on_the_hour": "soft", "other": "hard"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    db_session.expire_all()
    for s in await _ad_slots(db_session, station_id):
        minute = s.preempt_at.minute
        assert s.timing_mode == ("soft" if minute == 0 else "hard")

    for bad in ({"fixed_times": "25:00"}, {"fixed_times": "noon"}, {"timing_mode": "loud"}):
        r = await client.put(f"/api/v1/spot-schedules/{body['id']}", json=bad, headers=auth_headers)
        assert r.status_code == 422, bad


def test_spring_forward_skips_missing_times():
    # 2027-03-14 in New York: 02:00-02:59 does not exist.
    s = _sched("a", dayparts="overnight", plays=18)
    placed, unplaced = plan_spots([s], NY, date(2027, 3, 14), 1)
    assert all(p.local.hour != 2 for p in placed)
    assert len(placed) == 15 and len(unplaced) == 3
    assert len({p.at for p in placed}) == 15


def test_blackout_moves_and_skips_plays():
    # Shabbos: Friday 16:20 to Saturday 17:30 New York time (Nov 20-21, 2026).
    shabbos = Blackout(
        datetime(2026, 11, 20, 16, 20, tzinfo=NY).astimezone(UTC),
        datetime(2026, 11, 21, 17, 30, tzinfo=NY).astimezone(UTC),
        "Shabbos",
    )
    s = _sched("a", dayparts="morning,afternoon,evening", plays=2)
    placed, unplaced = plan_spots([s], NY, date(2026, 11, 20), 2, blackouts=[shabbos])
    assert not any(shabbos.covers(p.at) for p in placed)

    def on(day, part):
        return [p for p in placed if p.local.day == day and p.daypart == part]

    fri_pm = on(20, "afternoon")
    assert len(fri_pm) == 2  # squeezed in before candle lighting
    # Saturday afternoon: only the slots after Shabbos ends are used
    assert [(p.local.hour, p.local.minute) for p in on(21, "afternoon")] == [(17, 30), (17, 45)]
    # Friday evening and Saturday morning are fully off the air: 2 plays each, skipped
    assert len([u for u in unplaced if u.blackout is shabbos]) == 4
    assert len(unplaced) == 4


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
    # 3 a day for 7 days, minus whatever already passed today
    assert 18 <= len(preview["planned"]) <= 21 and preview["unplaced"] == []
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
async def test_review_follow_ups(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    station, assets, sponsors = await _setup(db_session, client, auth_headers)
    base = {"sponsor_id": sponsors[0], "asset_id": str(assets[0].id)}
    url = "/api/v1/spot-schedules"

    # start after end
    r = await client.post(
        url,
        json={**base, "start_date": "2026-12-10", "end_date": "2026-12-01"},
        headers=auth_headers,
    )
    assert r.status_code == 422

    r = await client.post(url, json={**base, "end_date": "2026-12-31"}, headers=auth_headers)
    assert r.status_code == 201
    sid = r.json()["id"]

    # explicit nulls for required fields are refused, not a 500
    for body in ({"is_active": None}, {"days_of_week": None}, {"asset_id": None}):
        r = await client.put(f"{url}/{sid}", json=body, headers=auth_headers)
        assert r.status_code == 422, body
    # start pushed past the stored end
    r = await client.put(f"{url}/{sid}", json={"start_date": "2027-01-05"}, headers=auth_headers)
    assert r.status_code == 422
    # nullable fields can still be cleared
    r = await client.put(f"{url}/{sid}", json={"end_date": None}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["end_date"] is None

    # the audio now belongs to sponsor 0; sponsor 1 cannot schedule it
    r = await client.post(
        url, json={"sponsor_id": sponsors[1], "asset_id": str(assets[0].id)}, headers=auth_headers
    )
    assert r.status_code == 422

    # preview never lists a time that already passed
    r = await client.get(f"{url}/preview?station_id={station.id}&days=1", headers=auth_headers)
    now = datetime.now(UTC)
    assert all(datetime.fromisoformat(p["scheduled_at"]) > now for p in r.json()["planned"])


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


@pytest.mark.asyncio
async def test_queue_and_preview_keep_off_shabbos(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, assets, sponsors = await _setup(db_session, client, auth_headers)
    now = datetime.now(UTC)
    start, end = now + timedelta(days=2), now + timedelta(days=3, hours=1)
    db_session.add(
        HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=start, end_datetime=end)
    )
    await db_session.commit()
    r = await client.post(
        "/api/v1/spot-schedules",
        json={"sponsor_id": sponsors[0], "asset_id": str(assets[0].id)},
        headers=auth_headers,
    )
    assert r.status_code == 201
    slots = await _ad_slots(db_session, station.id)
    assert slots
    for s in slots:
        at = s.preempt_at if s.preempt_at.tzinfo else s.preempt_at.replace(tzinfo=UTC)
        assert not (start <= at < end)

    r = await client.get(
        f"/api/v1/spot-schedules/preview?station_id={station.id}&days=7", headers=auth_headers
    )
    body = r.json()
    assert body["unplaced"] == []
    assert [w["name"] for w in body["off_air"]] == ["Shabbos"]
    assert body["off_air"][0]["skipped_plays"] >= 2


def test_passed_daypart_and_twelve_hour_clock():
    from datetime import UTC, date, datetime
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    from app.services.spot_schedule_service import Unplaced, clock_12, daypart_over

    assert clock_12(18, 15) == "6:15 PM"
    assert clock_12(0, 5) == "12:05 AM"
    assert clock_12(12, 0) == "12:00 PM"

    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 11, 15, 22, 20, tzinfo=UTC)  # 5:20 PM in New York
    sched = SimpleNamespace()
    assert daypart_over(Unplaced(date(2026, 11, 15), "morning", sched, "x"), tz, now)
    assert not daypart_over(Unplaced(date(2026, 11, 15), "evening", sched, "x"), tz, now)
    assert not daypart_over(Unplaced(date(2026, 11, 16), "morning", sched, "x"), tz, now)


@pytest.mark.asyncio
async def test_exact_time_due_within_two_minutes_still_airs_once(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Review of PR 45: an exact time set a minute before it is due used to show
    as planned but never reach the queue."""
    station, assets, sponsors = await _setup(db_session, client, auth_headers)
    station_id = station.id
    r = await client.post(
        "/api/v1/spot-schedules",
        json={
            "sponsor_id": sponsors[0],
            "asset_id": str(assets[0].id),
            "station_id": str(station_id),
            "fixed_times": "08:00",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    for e in await _ad_slots(db_session, station_id):
        await db_session.delete(e)
    await db_session.flush()

    tomorrow = datetime.now(NY).date() + timedelta(days=1)
    due = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 8, 0, tzinfo=NY)
    now = (due - timedelta(seconds=70)).astimezone(UTC)

    def at_due(entries):
        return [
            e
            for e in entries
            if (e.preempt_at if e.preempt_at.tzinfo else e.preempt_at.replace(tzinfo=UTC))
            == due.astimezone(UTC)
        ]

    await reconcile_spot_slots(db_session, station_id, now=now)
    assert len(at_due(await _ad_slots(db_session, station_id))) == 1
    # A second refill does not add it twice, nor after it has aired.
    await reconcile_spot_slots(db_session, station_id, now=now + timedelta(seconds=20))
    entries = at_due(await _ad_slots(db_session, station_id))
    assert len(entries) == 1
    entries[0].status = "played"
    await db_session.flush()
    await reconcile_spot_slots(db_session, station_id, now=now + timedelta(seconds=40))
    assert len(at_due(await _ad_slots(db_session, station_id))) == 1
