"""More sponsorships than a block has room for.

Shmuel's case: a block allowed three minutes of sponsorships, seven sponsorships
assigned to it. What airs, where the rest go, and what the station is told.
"""

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.spot_schedule_service import Break, plan_spots

NY = ZoneInfo("America/New_York")
DAY = date(2026, 11, 16)
NO_PLACE = "no sponsor place left in this time of day's blocks"


def _spot(name, dayparts="evening", plays=1, end=None, **kw):
    return SimpleNamespace(
        id=name, created_at=name, is_active=True, start_date=None, end_date=end,
        days_of_week="0,1,2,3,4,5,6", dayparts=dayparts, plays_per_daypart=plays,
        asset_id=f"asset-{name}", sponsor_id=f"sponsor-{name}", name=name,
        fixed_times=kw.pop("fixed_times", None), timing_mode=None,
    )


def _break(day, h, m=0, places=10, seconds=None):
    return Break(datetime(day.year, day.month, day.day, h, m, tzinfo=NY), places, "soft", "TEST block", None, seconds)


SEVEN = [_spot(f"s{i}") for i in range(1, 8)]
LENGTHS = {f"asset-s{i}": 30.0 for i in range(1, 8)}


def _names(placed, hhmm):
    return [p.schedule.name for p in placed if p.local.strftime("%H:%M") == hhmm]


def test_seven_sponsorships_over_two_limited_blocks_are_shared_out_evenly():
    breaks = [_break(DAY, 19, seconds=180), _break(DAY, 20, seconds=180)]
    placed, unplaced = plan_spots(SEVEN, NY, DAY, 1, breaks=breaks, durations=LENGTHS)

    assert not unplaced  # nothing is lost
    seven_pm, eight_pm = _names(placed, "19:00"), _names(placed, "20:00")
    # Each play goes to the emptier block (Shmuel 2026-11-18), so 4 and 3, not 6 and 1
    assert sorted([len(seven_pm), len(eight_pm)]) == [3, 4]
    assert sorted(seven_pm + eight_pm) == sorted(s.name for s in SEVEN)
    # Never more than three minutes in a limited block
    for names in (seven_pm, eight_pm):
        assert sum(LENGTHS[f"asset-{n}"] for n in names) <= 180


def test_the_limit_counts_real_lengths_not_a_number_of_spots():
    lengths = {**LENGTHS, "asset-s1": 45.0, "asset-s2": 45.0, "asset-s3": 45.0}
    placed, unplaced = plan_spots(SEVEN, NY, DAY, 1, breaks=[_break(DAY, 19, seconds=180)], durations=lengths)
    aired = _names(placed, "19:00")
    assert sum(lengths[f"asset-{n}"] for n in aired) <= 180
    assert len(aired) + len(unplaced) == 7 and unplaced
    assert {u.reason for u in unplaced} == {NO_PLACE}
    # A recording with no measured length counts as 30 seconds
    placed, unplaced = plan_spots(SEVEN, NY, DAY, 1, breaks=[_break(DAY, 19, seconds=180)], durations={})
    assert len(placed) == 6 and len(unplaced) == 1


def test_a_full_evening_rolls_to_no_one_elses_cost_and_says_so_when_the_day_is_full():
    """One limited block and nothing later that day: the seventh is reported, not dropped silently."""
    placed, unplaced = plan_spots(SEVEN, NY, DAY, 1, breaks=[_break(DAY, 19, seconds=180)], durations=LENGTHS)
    assert len(placed) == 6 and [u.reason for u in unplaced] == [NO_PLACE]


def test_overflow_rolls_to_a_later_time_of_day_without_taking_its_own_plays_room():
    afternoon = [_spot(f"a{i}", dayparts="afternoon") for i in range(1, 4)]
    evening = [_spot(f"e{i}") for i in range(1, 3)]
    breaks = [_break(DAY, 15, places=2), _break(DAY, 19, places=3)]
    placed, unplaced = plan_spots(afternoon + evening, NY, DAY, 1, breaks=breaks)
    assert not unplaced
    at_seven = [p for p in placed if p.local.hour == 19]
    assert sorted(p.schedule.name for p in at_seven if not p.moved_from) == ["e1", "e2"]
    moved = [p for p in placed if p.moved_from]
    assert len(moved) == 1 and moved[0].moved_from == "afternoon" and moved[0].local.hour == 19
    # One more afternoon play than the evening can take: it is reported, the evening's own plays stay
    placed, unplaced = plan_spots(
        afternoon + [_spot("a4", dayparts="afternoon")] + evening, NY, DAY, 1, breaks=breaks
    )
    assert len(unplaced) == 1 and unplaced[0].daypart == "afternoon"
    assert sorted(p.schedule.name for p in placed if p.local.hour == 19 and not p.moved_from) == ["e1", "e2"]


def test_who_waits_takes_turns_day_by_day():
    """Seven for six places every evening for a week: a different sponsorship waits each day."""
    breaks = [_break(DAY + timedelta(days=d), 19, seconds=180) for d in range(7)]
    placed, unplaced = plan_spots(SEVEN, NY, DAY, 7, breaks=breaks, durations=LENGTHS)
    assert len(unplaced) == 7
    assert sorted(u.schedule.name for u in unplaced) == sorted(s.name for s in SEVEN)
    # The same plan every time it is worked out
    again = plan_spots(SEVEN, NY, DAY, 7, breaks=breaks, durations=LENGTHS)
    assert [(p.at, p.seat, p.schedule.name) for p in placed] == [(p.at, p.seat, p.schedule.name) for p in again[0]]


def test_a_contract_about_to_end_never_waits():
    ending = _spot("s4", end=DAY + timedelta(days=2))
    wanting = [s if s.name != "s4" else ending for s in SEVEN]
    breaks = [_break(DAY + timedelta(days=d), 19, seconds=180) for d in range(7)]
    placed, unplaced = plan_spots(wanting, NY, DAY, 7, breaks=breaks, durations=LENGTHS)
    assert "s4" not in {u.schedule.name for u in unplaced}
    # It airs on its three days and never after its end date
    assert sorted({p.local.date() for p in placed if p.schedule.name == "s4"}) == [
        DAY, DAY + timedelta(days=1), DAY + timedelta(days=2)
    ]


def test_everyone_gets_one_play_before_anyone_gets_two():
    greedy = _spot("a-first-booked", plays=4)
    others = [_spot(f"s{i}") for i in range(1, 5)]
    placed, unplaced = plan_spots([greedy] + others, NY, DAY, 1, breaks=[_break(DAY, 19, places=3), _break(DAY, 20, places=2)])
    assert len(placed) == 5
    assert {p.schedule.name for p in placed} == {"a-first-booked", "s1", "s2", "s3", "s4"}
    assert [u.schedule.name for u in unplaced] == ["a-first-booked"] * 3


def test_enough_room_changes_nothing():
    breaks = [_break(DAY, 19, places=4), _break(DAY, 20, places=4)]
    with_limit = [_break(DAY, 19, places=4, seconds=600), _break(DAY, 20, places=4, seconds=600)]
    plain = plan_spots(SEVEN, NY, DAY, 1, breaks=breaks)
    limited = plan_spots(SEVEN, NY, DAY, 1, breaks=with_limit, durations=LENGTHS)
    assert not plain[1] and not limited[1]
    assert [(p.at, p.seat, p.schedule.name) for p in plain[0]] == [(p.at, p.seat, p.schedule.name) for p in limited[0]]
    assert not any(p.moved_from for p in plain[0])


# --- Through the real screens' endpoints, into the queue -------------------


async def _station_with_seven(db: AsyncSession, client: AsyncClient, auth_headers: dict, times: str):
    import uuid

    from app.models.asset import Asset
    from app.models.sponsor import Sponsor
    from app.models.station import Station

    station = Station(id=uuid.uuid4(), name=f"TEST room {uuid.uuid4().hex[:6]}", timezone="America/New_York")
    db.add(station)
    assets = []
    for i in range(1, 8):
        sponsor = Sponsor(id=uuid.uuid4(), name=f"TEST sponsor {i}", length_seconds=30, audio_file_path="")
        asset = Asset(
            id=uuid.uuid4(), title=f"TEST sponsorship {i}", file_path=f"x/t{i}.mp3",
            asset_type="spot", category="sponsorship", duration=30, sponsor_id=sponsor.id,
        )
        db.add_all([sponsor, asset])
        assets.append((sponsor, asset))
    await db.commit()
    res = await client.post(
        "/api/v1/program-blocks",
        json={
            "station_id": str(station.id), "name": "TEST 3 minute block", "times": times,
            "timing_mode": "soft", "items": [{"kind": "sponsors", "max_seconds": 180}],
        },
        headers=auth_headers,
    )
    assert res.status_code == 201, res.text
    assert res.json()["items"][0]["max_seconds"] == 180
    return station, assets


@pytest.mark.asyncio
async def test_seven_sponsorships_in_a_three_minute_block_on_the_screens_and_in_the_queue(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    from sqlalchemy import select

    from app.models.alert import Alert
    from app.models.asset import Asset
    from app.models.queue_entry import QueueEntry
    from app.services.program_block_service import reconcile_block_entries

    # One block a day, in every time of day's reach only in the evening
    station, assets = await _station_with_seven(db_session, client, auth_headers, "19:00")
    warnings = []
    for sponsor, asset in assets:
        res = await client.post(
            "/api/v1/spot-schedules",
            json={
                "sponsor_id": str(sponsor.id), "asset_id": str(asset.id), "station_id": str(station.id),
                "name": asset.title, "dayparts": "evening", "plays_per_daypart": 1,
            },
            headers=auth_headers,
        )
        assert res.status_code == 201, res.text
        warnings.append(res.json()["warnings"])
    # Six fit: nothing to say. The seventh is told at once.
    assert all(w == [] for w in warnings[:6])
    said = " ".join(warnings[6])
    assert "misses" in said and "no room left" in said and "Schedule Check" in said

    # An alert, one however many times it is saved again
    alerts = (await db_session.execute(select(Alert).where(Alert.station_id == station.id))).scalars().all()
    assert [a.title for a in alerts] == [f"More sponsorships than room on {station.name}"]

    # Spot Schedule page: one play a day has no place, a different sponsorship each day
    plan = (await client.get(
        f"/api/v1/spot-schedules/preview?station_id={station.id}&days=7", headers=auth_headers
    )).json()
    by_day: dict[str, list] = {}
    for p in plan["planned"]:
        by_day.setdefault(p["local_time"][:10], []).append(p)
    assert all(len(v) <= 6 for v in by_day.values())
    waiting = [u["sponsor_name"] for u in plan["unplaced"]]
    assert len(set(waiting)) == len(waiting) >= 6

    # Schedule Check says how far short each falls
    check = (await client.get(
        f"/api/v1/schedules/look-ahead?station_id={station.id}", headers=auth_headers
    )).json()
    mine = [f for f in check["findings"] if f["kind"] == "spot_unplaced"]
    assert mine and all("it misses 1 of 7 plays" in f["detail"] for f in mine)

    # The queue: every airing of the block holds at most three minutes of sponsorships
    await reconcile_block_entries(db_session, station.id)
    await db_session.commit()
    rows = (await db_session.execute(
        select(QueueEntry.preempt_at, Asset.duration)
        .join(Asset, Asset.id == QueueEntry.asset_id)
        .where(QueueEntry.station_id == station.id, QueueEntry.source == "block")
    )).all()
    assert rows
    per_airing: dict = {}
    for at, seconds in rows:
        per_airing[at] = per_airing.get(at, 0) + seconds
    assert max(per_airing.values()) <= 180


@pytest.mark.asyncio
async def test_a_contract_that_ends_soon_is_warned_about_its_last_plays(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Exact-time sponsorships hold the block; one more that ends in five days is
    told its plays cannot be made up. It never airs after its end date."""
    from datetime import UTC

    station, assets = await _station_with_seven(db_session, client, auth_headers, "19:00")
    today = datetime.now(UTC).astimezone(NY).date()
    for sponsor, asset in assets[:6]:
        res = await client.post(
            "/api/v1/spot-schedules",
            json={
                "sponsor_id": str(sponsor.id), "asset_id": str(asset.id), "station_id": str(station.id),
                "fixed_times": "19:00",
            },
            headers=auth_headers,
        )
        assert res.status_code == 201, res.text
    sponsor, asset = assets[6]
    end = today + timedelta(days=5)
    res = await client.post(
        "/api/v1/spot-schedules",
        json={
            "sponsor_id": str(sponsor.id), "asset_id": str(asset.id), "station_id": str(station.id),
            "dayparts": "evening", "plays_per_daypart": 1, "end_date": end.isoformat(),
        },
        headers=auth_headers,
    )
    assert res.status_code == 201, res.text
    said = " ".join(res.json()["warnings"])
    assert "This sponsorship misses" in said and "cannot be made up" in said
    plan = (await client.get(
        f"/api/v1/spot-schedules/preview?station_id={station.id}&days=14", headers=auth_headers
    )).json()
    assert all(u["local_date"] <= end.isoformat() for u in plan["unplaced"])
    assert not [p for p in plan["planned"] if p["asset_id"] == str(asset.id)]


@pytest.mark.asyncio
async def test_another_schedule_that_loses_plays_is_named_with_its_own_name(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Live on Main: two 12-play schedules of one sponsor; the red box read like the
    same warning twice. The other schedule is named 'Sponsor (schedule name)'."""
    station, assets = await _station_with_seven(db_session, client, auth_headers, "19:00")
    sponsor, asset = assets[0]
    said = []
    for name in ("TEST first", "TEST second"):
        res = await client.post(
            "/api/v1/spot-schedules",
            json={
                "sponsor_id": str(sponsor.id), "asset_id": str(asset.id), "station_id": str(station.id),
                "name": name, "dayparts": "evening", "plays_per_daypart": 5,
            },
            headers=auth_headers,
        )
        assert res.status_code == 201, res.text
        said = res.json()["warnings"]
    assert any(w.startswith(f"{station.name}: This sponsorship misses") for w in said)
    assert any("TEST sponsor 1 (TEST first) misses" in w for w in said)
