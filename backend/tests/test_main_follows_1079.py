"""Main follows 107.9 (Shmuel 2026-11-22): no sponsorships overnight, sponsorships
stop before Shabbos, and a block's extra pieces are skipped while they have no audio."""

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.holiday_window import HolidayWindow
from app.models.station import Station
from app.services.spot_schedule_service import (
    Blackout,
    load_sponsor_blackouts,
    plan_spots,
    sponsor_rest_windows,
)
from tests.test_program_blocks import _block, _library, _pending
from tests.test_spot_schedules import _sched

NY = ZoneInfo("America/New_York")


def test_quiet_hours_cover_every_night_and_run_past_midnight():
    start = datetime(2026, 11, 23, 5, tzinfo=UTC)
    end = start + timedelta(days=2)
    windows = sponsor_rest_windows(
        {"sponsor_quiet_hours": {"start": "00:35", "end": "06:00"}}, NY, start, end, []
    )
    local = [(w.start.astimezone(NY), w.end.astimezone(NY)) for w in windows]
    assert all((a.hour, a.minute, b.hour, b.minute) == (0, 35, 6, 0) for a, b in local)
    assert {a.date() for a, _ in local} >= {date(2026, 11, 23), date(2026, 11, 24)}
    assert all(w.sponsors_only and w.why.startswith("no sponsorships then") for w in windows)

    late = sponsor_rest_windows({"sponsor_quiet_hours": {"start": "23:00", "end": "05:00"}}, NY, start, end, [])
    a, b = late[0].start.astimezone(NY), late[0].end.astimezone(NY)
    assert (a.hour, b.hour, (b - a)) == (23, 5, timedelta(hours=6))

    # off, half set, or nonsense: nothing
    for config in ({}, {"sponsor_quiet_hours": {"start": "00:35"}},
                   {"sponsor_quiet_hours": {"start": "x", "end": "06:00"}}):
        assert sponsor_rest_windows(config, NY, start, end, []) == []


def test_no_sponsorship_overnight_and_none_in_the_45_minutes_before_shabbos():
    shabbos = Blackout(
        datetime(2026, 11, 20, 16, 18, tzinfo=NY).astimezone(UTC),
        datetime(2026, 11, 21, 17, 30, tzinfo=NY).astimezone(UTC),
        "Shabbos",
    )
    config = {"sponsor_quiet_hours": {"start": "00:35", "end": "06:00"},
              "sponsors_stop_before_blackout_minutes": 45}
    start = datetime(2026, 11, 18, 5, tzinfo=UTC)
    rest = sponsor_rest_windows(config, NY, start, start + timedelta(days=4), [shabbos])
    before = [w for w in rest if "before Shabbos" in w.name]
    assert len(before) == 1 and before[0].end == shabbos.start
    assert before[0].start == shabbos.start - timedelta(minutes=45)

    s = _sched("a", dayparts="overnight,afternoon", plays=8)
    placed, unplaced = plan_spots([s], NY, date(2026, 11, 19), 2, blackouts=[shabbos, *rest])
    for p in placed:
        local = p.local
        assert not (local.hour < 6 and (local.hour, local.minute) >= (0, 35)), local
        assert not (shabbos.start - timedelta(minutes=45) <= p.at < shabbos.start), local
    # overnight still has 0:00-0:35, so those plays are squeezed in there
    assert [p.local.minute for p in placed if p.daypart == "overnight"]
    assert all(p.local.hour == 0 and p.local.minute < 35 for p in placed if p.daypart == "overnight")
    # Friday afternoon still gets its plays, all before 3:33 PM
    fri = [p for p in placed if p.local.day == 20 and p.daypart == "afternoon"]
    assert fri and max(p.local for p in fri) < datetime(2026, 11, 20, 15, 33, tzinfo=NY)


async def test_station_setting_reaches_the_planner_and_the_api(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", is_active=True,
                      automation_config={})
    db_session.add(station)
    db_session.add(HolidayWindow(
        id=uuid.uuid4(), name="Shabbos", is_blackout=True,
        start_datetime=datetime(2026, 11, 27, 21, 13, tzinfo=UTC),
        end_datetime=datetime(2026, 11, 28, 22, 20, tzinfo=UTC),
    ))
    await db_session.commit()
    url = "/api/v1/spot-schedules/timing-defaults"
    sid = str(station.id)
    base = {"station_id": sid, "on_the_hour": "hard", "other": "soft"}
    r = await client.put(url, json={**base, "sponsor_quiet_start": "00:35", "sponsor_quiet_end": "06:00",
                                    "sponsors_stop_before_blackout_minutes": 45}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["sponsor_quiet_start"] == "00:35" and r.json()["sponsors_stop_before_blackout_minutes"] == 45
    # an older page that does not send them keeps them
    r = await client.put(url, json=base, headers=auth_headers)
    assert r.json()["sponsor_quiet_end"] == "06:00" and r.json()["sponsors_stop_before_blackout_minutes"] == 45
    for bad in ({"sponsor_quiet_start": "00:35", "sponsor_quiet_end": ""},
                {"sponsor_quiet_start": "25:00", "sponsor_quiet_end": "06:00"},
                {"sponsor_quiet_start": "06:00", "sponsor_quiet_end": "06:00"},
                {"sponsors_stop_before_blackout_minutes": -5}):
        r = await client.put(url, json={**base, **bad}, headers=auth_headers)
        assert r.status_code == 422, bad

    await db_session.refresh(station)
    now = datetime(2026, 11, 26, 12, tzinfo=UTC)
    found = await load_sponsor_blackouts(db_session, station, now, now + timedelta(days=3))
    names = [b.name for b in found]
    assert "Shabbos" in names and "45 min before Shabbos" in names
    assert names.count("quiet hours") >= 3

    # clearing both
    r = await client.put(url, json={**base, "sponsor_quiet_start": "", "sponsor_quiet_end": "",
                                    "sponsors_stop_before_blackout_minutes": 0}, headers=auth_headers)
    assert r.json()["sponsor_quiet_start"] == "" and r.json()["sponsors_stop_before_blackout_minutes"] == 0
    await db_session.refresh(station)
    found = await load_sponsor_blackouts(db_session, station, now, now + timedelta(days=3))
    assert [b.name for b in found] == ["Shabbos"]


async def test_a_piece_with_no_audio_yet_is_skipped_and_the_rest_plays(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """The hourly package after the weather (promo, kids-in-car alert, two Torah
    pieces): until their recordings exist they are skipped, never a placeholder,
    and the pieces that do exist still play."""
    station, made = await _library(db_session)
    await db_session.commit()
    body = _block(
        station, made,
        items=[
            {"kind": "asset", "asset_id": str(made["id"].id), "label": "Station promo"},
            {"kind": "category", "asset_type": "spot", "category": "kids_car", "optional": True,
             "label": "Kids in the car"},
            {"kind": "category", "asset_type": "shiur", "category": "pliskin", "optional": True,
             "label": "Rabbi Pliskin"},
            {"kind": "category", "asset_type": "spot", "category": "psa", "optional": True},
        ],
    )
    res = await client.post("/api/v1/program-blocks", json=body, headers=auth_headers)
    assert res.status_code == 201, res.text
    assert res.json()["items"][1]["optional"] is True
    queued = await _pending(db_session, station)
    first = min(e.preempt_at for e in queued)
    titles = [e.asset_id for e in queued if e.preempt_at == first]
    assert titles[0] == made["id"].id and len(titles) == 2  # promo + a PSA; kids/Pliskin skipped
    preview = await client.get(
        f"/api/v1/program-blocks/preview?station_id={station.id}&hours=2", headers=auth_headers
    )
    problems = preview.json()["occurrences"][0]["problems"]
    assert any("Kids in the car: nothing" in p for p in problems)
    assert not any("left out" in p for p in problems)
