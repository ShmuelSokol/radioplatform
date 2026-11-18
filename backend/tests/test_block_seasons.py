"""Program blocks that air only in part of the year (the kids-in-car reminders, 2026-11-18).

Shmuel: a summer version warning how hot a car gets, airing only in the summer, and a
rest-of-year version airing only outside it, both opening with a short siren. A block's
season is "MM-DD" to "MM-DD" every year, in station time; a range may wrap the new year.
"""

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, fit_for_random_pick
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.program_block_service import in_season, occurrences_between

NY = ZoneInfo("America/New_York")
JLM = ZoneInfo("Asia/Jerusalem")


def _b(start, end, times="08:20"):
    return SimpleNamespace(
        id=uuid.uuid4(), name="Kids in car", days_of_week="0,1,2,3,4,5,6",
        times=times, season_start=start, season_end=end,
    )


@pytest.mark.parametrize(
    "start,end,day,expected",
    [
        (None, None, date(2026, 1, 1), True),  # no season = all year
        ("05-15", "09-15", date(2026, 5, 14), False),
        ("05-15", "09-15", date(2026, 5, 15), True),  # first day included
        ("05-15", "09-15", date(2026, 9, 15), True),  # last day included
        ("05-15", "09-15", date(2026, 9, 16), False),
        ("05-15", "09-15", date(2026, 11, 18), False),
        # the rest of the year wraps the new year
        ("09-16", "05-14", date(2026, 9, 16), True),
        ("09-16", "05-14", date(2026, 11, 18), True),
        ("09-16", "05-14", date(2027, 1, 1), True),
        ("09-16", "05-14", date(2026, 5, 14), True),
        ("09-16", "05-14", date(2026, 5, 15), False),
        ("09-16", "05-14", date(2026, 7, 4), False),
        ("02-29", "02-29", date(2028, 2, 29), True),
        ("02-29", "02-29", date(2027, 2, 28), False),
    ],
)
def test_in_season(start, end, day, expected):
    assert in_season(_b(start, end), day) is expected


def test_summer_and_rest_of_year_never_both_and_never_neither():
    """Over two whole years (one a leap year) exactly one of the pair airs each day."""
    summer, rest = _b("05-15", "09-15"), _b("09-16", "05-14")
    day = date(2027, 1, 1)
    while day.year < 2029:
        assert in_season(summer, day) != in_season(rest, day), day
        day = date.fromordinal(day.toordinal() + 1)


def test_season_uses_station_local_day():
    """Jerusalem 00:20 on May 15 local is still May 14 in UTC: the summer block airs then."""
    block = _b("05-15", "09-15", times="00:20")
    start = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
    end = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    found = occurrences_between([block], JLM, start, end)
    assert [o.local.strftime("%m-%d %H:%M") for o in found] == ["05-15 00:20"]


def test_occurrences_follow_the_season_change():
    summer = _b("05-15", "09-15", times="08:20,17:50")
    rest = _b("09-16", "05-14", times="17:50")
    start = datetime(2026, 9, 14, 4, 0, tzinfo=UTC)
    end = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)
    found = occurrences_between([summer, rest], NY, start, end)
    days = [(o.local.strftime("%m-%d %H:%M"), o.block is summer) for o in found]
    assert days == [
        ("09-14 08:20", True), ("09-14 17:50", True),
        ("09-15 08:20", True), ("09-15 17:50", True),
        ("09-16 17:50", False), ("09-17 17:50", False),
    ]


async def _setup(db: AsyncSession):
    station = Station(id=uuid.uuid4(), name="KBR", timezone="America/New_York")
    db.add(station)
    made = {}
    for key, title, asset_type, category in [
        ("siren", "Siren intro", "jingle", "block_only"),
        ("summer", "Kids in car - summer 1", "spot", "kids_car_summer"),
        ("year", "Kids in car - all year 1", "spot", "kids_car_year"),
    ]:
        made[key] = Asset(
            id=uuid.uuid4(), title=title, file_path=f"x/{key}.mp3",
            asset_type=asset_type, category=category, duration=20,
        )
        db.add(made[key])
    await db.flush()
    return station, made


def _body(station, made, kind, start, end, times):
    return {
        "station_id": str(station.id),
        "name": f"Kids in car ({kind})",
        "times": times,
        "timing_mode": "soft",
        "season_start": start,
        "season_end": end,
        "items": [
            {"kind": "asset", "asset_id": str(made["siren"].id), "label": "Siren"},
            {"kind": "category", "asset_type": "spot", "category": f"kids_car_{kind}"},
        ],
    }


@pytest.mark.asyncio
async def test_only_the_season_of_today_is_queued(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Today (whatever the date the suite runs) exactly one of the pair is queued, siren first."""
    station, made = await _setup(db_session)
    ids = {k: a.id for k, a in made.items()}
    await db_session.commit()
    for kind, start, end in (("summer", "05-15", "09-15"), ("year", "9-16", "5/14")):
        res = await client.post(
            "/api/v1/program-blocks",
            json=_body(station, made, kind, start, end, "08:20,11:50,14:20,17:50"),
            headers=auth_headers,
        )
        assert res.status_code == 201, res.text
        assert res.json()["season_start"] in ("05-15", "09-16")  # cleaned to MM-DD
    rows = (
        await db_session.execute(
            select(QueueEntry)
            .where(QueueEntry.station_id == station.id, QueueEntry.source == "block")
            .order_by(QueueEntry.preempt_at, QueueEntry.position)
        )
    ).scalars().all()
    assert rows
    by_time: dict = {}
    for r in rows:
        by_time.setdefault(r.preempt_at, []).append(r.asset_id)
    for at, assets in by_time.items():
        local = at.replace(tzinfo=UTC).astimezone(NY) if at.tzinfo is None else at.astimezone(NY)
        want = "summer" if "05-15" <= local.strftime("%m-%d") <= "09-15" else "year"
        assert assets == [ids["siren"], ids[want]], local
        assert local.minute in (20, 50)
    assert all(r.timing_mode == "soft" for r in rows)


@pytest.mark.asyncio
async def test_season_validation(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    station, made = await _setup(db_session)
    await db_session.commit()
    bad = _body(station, made, "summer", "05-15", None, "08:20")
    res = await client.post("/api/v1/program-blocks", json=bad, headers=auth_headers)
    assert res.status_code == 422
    bad = _body(station, made, "summer", "13-01", "09-15", "08:20")
    res = await client.post("/api/v1/program-blocks", json=bad, headers=auth_headers)
    assert res.status_code == 422
    bad = _body(station, made, "summer", "02-30", "09-15", "08:20")
    res = await client.post("/api/v1/program-blocks", json=bad, headers=auth_headers)
    assert res.status_code == 422

    res = await client.post(
        "/api/v1/program-blocks",
        json=_body(station, made, "summer", "05-15", "09-15", "08:20"),
        headers=auth_headers,
    )
    block_id = res.json()["id"]
    # Clearing one end alone is refused
    res = await client.put(
        f"/api/v1/program-blocks/{block_id}", json={"season_end": None}, headers=auth_headers
    )
    assert res.status_code == 422
    # Moving one end is fine (the other is stored)
    res = await client.put(
        f"/api/v1/program-blocks/{block_id}", json={"season_end": "09-30"}, headers=auth_headers
    )
    assert res.status_code == 200, res.text
    assert (res.json()["season_start"], res.json()["season_end"]) == ("05-15", "09-30")
    # Clearing both = all year
    res = await client.put(
        f"/api/v1/program-blocks/{block_id}",
        json={"season_start": None, "season_end": None},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["season_start"] is None and res.json()["season_end"] is None
    # Other edits leave the season alone
    await client.put(
        f"/api/v1/program-blocks/{block_id}",
        json={"season_start": "05-15", "season_end": "09-15"},
        headers=auth_headers,
    )
    res = await client.put(
        f"/api/v1/program-blocks/{block_id}", json={"name": "Renamed"}, headers=auth_headers
    )
    assert (res.json()["season_start"], res.json()["season_end"]) == ("05-15", "09-15")


@pytest.mark.asyncio
async def test_reminders_are_never_drawn_as_filler(db_session: AsyncSession):
    """A summer heat warning drawn as a random spot could air in January."""
    _, made = await _setup(db_session)
    await db_session.commit()
    picked = set(
        (await db_session.execute(select(Asset.id).where(fit_for_random_pick()))).scalars().all()
    )
    assert not picked & {made["summer"].id, made["year"].id, made["siren"].id}
