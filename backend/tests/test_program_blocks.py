import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.program_block_service import reconcile_block_entries
from app.services.spot_schedule_service import SAFETY_MARGIN, as_utc

NY = ZoneInfo("America/New_York")


async def _library(db: AsyncSession):
    station = Station(id=uuid.uuid4(), name="KBR", timezone="America/New_York")
    db.add(station)
    await db.flush()
    made = {}
    for key, title, asset_type, category, seconds in [
        ("id", "Station ID jingle", "jingle", "station_id", 8),
        ("psa1", "PSA one", "spot", "psa", 30),
        ("psa2", "PSA two", "spot", "psa", 40),
        ("sponsor", "Ruvy Tire", "spot", "sponsorship", 31),
        ("show", "Rabbi Program", "shiur", "program", 600),
    ]:
        asset = Asset(
            id=uuid.uuid4(), title=title, file_path=f"x/{key}.mp3",
            asset_type=asset_type, category=category, duration=seconds,
        )
        db.add(asset)
        made[key] = asset
    await db.flush()
    return station, made


def _block(station, made, **kw):
    return {
        "station_id": str(station.id),
        "name": "Weather Sponsors",
        "times": kw.pop("times", ",".join(f"{h:02d}:00" for h in range(24))),
        "timing_mode": kw.pop("timing_mode", "hard"),
        "items": kw.pop(
            "items",
            [
                {"kind": "asset", "asset_id": str(made["id"].id), "label": "Station ID"},
                {"kind": "category", "asset_type": "spot", "category": "psa"},
                {"kind": "asset", "asset_id": str(made["sponsor"].id)},
                {"kind": "asset", "asset_id": str(made["show"].id)},
            ],
        ),
        **kw,
    }


async def _pending(db: AsyncSession, station):
    rows = await db.execute(
        select(QueueEntry)
        .where(
            QueueEntry.station_id == getattr(station, "id", station),
            QueueEntry.source == "block",
        )
        .order_by(QueueEntry.preempt_at, QueueEntry.position)
    )
    return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_block_expands_into_its_items_on_the_hour(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """One named block becomes a run of queue entries sharing one start time."""
    station, made = await _library(db_session)
    ids = {k: a.id for k, a in made.items()}
    await db_session.commit()

    res = await client.post(
        "/api/v1/program-blocks", json=_block(station, made), headers=auth_headers
    )
    assert res.status_code == 201, res.text

    entries = await _pending(db_session, station)
    starts = sorted({e.preempt_at for e in entries})
    assert 46 <= len(starts) <= 48  # every hour over the next two days
    assert all(e.timing_mode == "hard" for e in entries)
    assert all(s.minute == 0 and s.second == 0 for s in starts)

    first = [e for e in entries if e.preempt_at == starts[0]]
    assert [e.asset_id for e in first][0] == ids["id"]
    assert [e.asset_id for e in first][2:] == [ids["sponsor"], ids["show"]]
    assert [e.position for e in first] == sorted(e.position for e in first)
    # The PSA rotates: the two take turns hour by hour.
    second = [e for e in entries if e.preempt_at == starts[1]]
    assert {first[1].asset_id, second[1].asset_id} == {ids["psa1"], ids["psa2"]}

    # Running again changes nothing (no duplicates, no reshuffle).
    before = [(e.id, e.asset_id) for e in entries]
    await reconcile_block_entries(db_session, station.id)
    await db_session.commit()
    assert [(e.id, e.asset_id) for e in await _pending(db_session, station)] == before

    preview = await client.get(
        f"/api/v1/program-blocks/preview?station_id={station.id}&hours=3", headers=auth_headers
    )
    assert preview.status_code == 200
    runs = preview.json()["occurrences"]
    # The preview shows the same turn-taking as the queue.
    assert runs[0]["items"][1]["title"] != runs[1]["items"][1]["title"]
    run = runs[0]
    assert run["block_name"] == "Weather Sponsors"
    assert run["seconds"] in (8 + 30 + 31 + 600, 8 + 40 + 31 + 600)
    assert [i["label"] for i in run["items"]][0] == "Station ID"


@pytest.mark.asyncio
async def test_editing_a_block_requeues_it_and_soft_is_kept(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, made = await _library(db_session)
    sponsor_id = made["sponsor"].id
    body = _block(station, made)
    station = station.id
    await db_session.commit()
    res = await client.post("/api/v1/program-blocks", json=body, headers=auth_headers)
    block_id = res.json()["id"]

    res = await client.put(
        f"/api/v1/program-blocks/{block_id}",
        json={
            "times": ",".join(f"{h:02d}:30" for h in range(24)),
            "timing_mode": "soft",
            "items": [{"kind": "asset", "asset_id": str(sponsor_id)}],
        },
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text
    db_session.expire_all()
    entries = await _pending(db_session, station)
    soon = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=3)
    later = [e for e in entries if e.preempt_at.replace(tzinfo=None) > soon]
    assert later and all(e.preempt_at.minute == 30 for e in later)
    assert all(e.timing_mode == "soft" and e.asset_id == sponsor_id for e in later)

    res = await client.delete(f"/api/v1/program-blocks/{block_id}", headers=auth_headers)
    assert res.status_code == 204
    db_session.expire_all()
    left = await _pending(db_session, station)
    assert all(e.preempt_at.replace(tzinfo=None) <= soon for e in left)


@pytest.mark.asyncio
async def test_blocks_stay_off_the_air_on_shabbos_and_report_missing_items(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, made = await _library(db_session)
    now = datetime.now(UTC)
    db_session.add(
        HolidayWindow(
            id=uuid.uuid4(), name="Shabbos", is_blackout=True,
            start_datetime=now + timedelta(hours=5), end_datetime=now + timedelta(hours=30),
        )
    )
    await db_session.commit()
    body = _block(
        station, made,
        items=[
            {"kind": "category", "asset_type": "spot", "category": "nothing-here"},
            {"kind": "asset", "asset_id": str(uuid.uuid4())},
            {"kind": "asset", "asset_id": str(made["id"].id)},
        ],
    )
    res = await client.post("/api/v1/program-blocks", json=body, headers=auth_headers)
    assert res.status_code == 201, res.text

    entries = await _pending(db_session, station)
    assert entries
    start = (now + timedelta(hours=5)).replace(tzinfo=None)
    end = (now + timedelta(hours=30)).replace(tzinfo=None)
    assert not [e for e in entries if start <= e.preempt_at.replace(tzinfo=None) < end]

    preview = await client.get(
        f"/api/v1/program-blocks/preview?station_id={station.id}&hours=4", headers=auth_headers
    )
    run = preview.json()["occurrences"][0]
    assert len(run["problems"]) == 2
    assert run["seconds"] == 8

    # Schedule Check names each missing item once, not once per hour.
    check = await client.get(
        f"/api/v1/schedules/look-ahead?station_id={station.id}", headers=auth_headers
    )
    assert check.status_code == 200, check.text
    mine = [f for f in check.json()["findings"] if f["kind"] == "block_item"]
    assert len(mine) == 2 and all(f["needs_attention"] for f in mine)


@pytest.mark.asyncio
async def test_a_block_needs_a_time_and_an_item(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, made = await _library(db_session)
    await db_session.commit()
    for bad in (
        _block(station, made, times=""),
        _block(station, made, times="25:00"),
        _block(station, made, items=[]),
        _block(station, made, items=[{"kind": "asset"}]),
        _block(station, made, timing_mode="firm"),
    ):
        res = await client.post("/api/v1/program-blocks", json=bad, headers=auth_headers)
        assert res.status_code == 422, bad


def _spot(name, dayparts="evening", plays=1, **kw):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=name, created_at=name, is_active=True, start_date=None, end_date=None,
        days_of_week="0,1,2,3,4,5,6", dayparts=dayparts, plays_per_daypart=plays,
        asset_id=f"asset-{name}", sponsor_id=f"sponsor-{name}", name=name,
        fixed_times=kw.pop("fixed_times", None), timing_mode=None,
    )


def test_sponsorships_fill_the_places_in_blocks():
    """A sponsorship that wants the evening goes into the first 7:00 / 7:30 block with room."""
    from datetime import date

    from app.services.spot_schedule_service import Break, plan_spots

    day = date(2026, 11, 16)
    breaks = [
        Break(datetime(2026, 11, 16, h, m, tzinfo=NY), 2, "soft", "Sponsors", 3)
        for h, m in ((19, 0), (19, 30))
    ]
    wanting = [_spot(n) for n in ("a", "b", "c", "d", "e")]
    wanting.append(_spot("pinned", fixed_times="19:30"))
    placed, unplaced = plan_spots(wanting, NY, day, 1, breaks=breaks)

    # Five want the evening and three places are left: who waits takes turns by day
    # (see tests/test_sponsorship_room.py); the exact time always gets its place.
    assert [(p.local.strftime("%H:%M"), p.seat) for p in placed] == [
        ("19:00", 0), ("19:00", 1), ("19:30", 0), ("19:30", 1),
    ]
    assert placed[2].schedule.name == "pinned"  # an exact time that is a block's start takes a place in it
    assert all(p.block == "Sponsors" and p.timing_mode == "soft" for p in placed)
    assert all(p.hard_by == p.at + timedelta(minutes=3) for p in placed)
    assert len(unplaced) == 2
    assert sorted([u.schedule.name for u in unplaced] + [p.schedule.name for p in placed]) == [
        "a", "b", "c", "d", "e", "pinned",
    ]
    assert {u.reason for u in unplaced} == {"no sponsor place left in this time of day's blocks"}

    # The morning has no block with places: said plainly, not spread over clock slots.
    placed, unplaced = plan_spots([_spot("m", dayparts="morning")], NY, day, 1, breaks=breaks)
    assert not placed and unplaced[0].reason == "no block with sponsor places in this time of day"

    # A morning wholly inside Shabbos is skipped on purpose (blackout set), not a problem.
    from app.services.spot_schedule_service import Blackout

    shabbos = Blackout(
        datetime(2026, 11, 15, 16, 0, tzinfo=NY), datetime(2026, 11, 16, 18, 0, tzinfo=NY), "Shabbos"
    )
    placed, unplaced = plan_spots(
        [_spot("m", dayparts="morning")], NY, day, 1, blackouts=[shabbos], breaks=breaks
    )
    assert not placed and unplaced and all(u.blackout is shabbos for u in unplaced)


@pytest.mark.asyncio
async def test_block_sponsor_places_reach_the_queue_in_block_order(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, made = await _library(db_session)
    station_id, sponsor_id, id_id = station.id, made["sponsor"].id, made["id"].id
    await db_session.commit()
    res = await client.post(
        "/api/v1/sponsors",
        json={"name": "Ruvy Tire", "length_seconds": 30.0, "audio_file_path": ""},
        headers=auth_headers,
    )
    assert res.status_code == 201, res.text
    res = await client.post(
        "/api/v1/spot-schedules",
        json={
            "sponsor_id": res.json()["id"], "asset_id": str(sponsor_id),
            "station_id": str(station_id), "dayparts": "overnight,morning,afternoon,evening",
            "plays_per_daypart": 2,
        },
        headers=auth_headers,
    )
    assert res.status_code == 201, res.text

    body = _block(
        station, made,
        times=",".join(f"{h:02d}:30" for h in range(24)),
        timing_mode="soft",
        latest_minutes=4,
        items=[
            {"kind": "asset", "asset_id": str(id_id)},
            {"kind": "sponsors", "count": 3},
            {"kind": "asset", "asset_id": str(id_id)},
        ],
    )
    res = await client.post("/api/v1/program-blocks", json=body, headers=auth_headers)
    assert res.status_code == 201, res.text
    assert res.json()["latest_minutes"] == 4

    db_session.expire_all()
    entries = await _pending(db_session, station_id)
    by_start: dict = {}
    for e in entries:
        by_start.setdefault(e.preempt_at, []).append(e.asset_id)
    with_sponsor = [v for v in by_start.values() if sponsor_id in v]
    assert with_sponsor and all(v == [id_id, sponsor_id, id_id] for v in with_sponsor)
    assert all(e.hard_by is not None for e in entries)
    assert all(
        (e.hard_by.replace(tzinfo=None) - e.preempt_at.replace(tzinfo=None)) == timedelta(minutes=4)
        for e in entries
    )
    # Nothing is also queued on the old :15/:30/:45 sponsor clock. (A block time less
    # than 2 minutes away is not queued, so its sponsor play goes out as a plain timed
    # spot instead of being lost: allow that one when the test runs just before :30.)
    soon = datetime.now(UTC) + SAFETY_MARGIN + timedelta(seconds=5)
    ads = await db_session.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station_id, QueueEntry.source == "ad_slot"
        )
    )
    assert not [a for a in ads.scalars().all() if as_utc(a.preempt_at) > soon]

    preview = await client.get(
        f"/api/v1/program-blocks/preview?station_id={station_id}&hours=24", headers=auth_headers
    )
    labels = {i["label"] for o in preview.json()["occurrences"] for i in o["items"]}
    assert {"Open sponsor place", "Sponsor: Ruvy Tire"} <= labels
    assert any(i.get("open_place") for o in preview.json()["occurrences"] for i in o["items"])

    plan = await client.get(
        f"/api/v1/spot-schedules/preview?station_id={station_id}", headers=auth_headers
    )
    assert plan.status_code == 200, plan.text
    assert {p["block"] for p in plan.json()["planned"]} == {"Weather Sponsors"}


@pytest.mark.asyncio
async def test_an_announcement_is_left_out_when_its_programme_is_missing(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Jerusalem 2026-11-17: "Daf Yomi" was announced and then music played. A block's
    earlier items (its announcement) are not queued when its programme, the last
    recording, has nothing to play; time and weather still are."""
    station, made = await _library(db_session)
    await db_session.commit()
    body = _block(
        station, made,
        items=[
            {"kind": "asset", "asset_id": str(made["id"].id), "label": "Announcement"},
            {"kind": "asset", "asset_id": str(uuid.uuid4()), "label": "Daf Yomi"},
        ],
    )
    res = await client.post("/api/v1/program-blocks", json=body, headers=auth_headers)
    assert res.status_code == 201, res.text
    assert await _pending(db_session, station) == []
    preview = await client.get(
        f"/api/v1/program-blocks/preview?station_id={station.id}&hours=2", headers=auth_headers
    )
    run = preview.json()["occurrences"][0]
    assert any("Announcement: left out" in p for p in run["problems"])
    assert run["seconds"] == 0


@pytest.mark.asyncio
async def test_a_rebuild_still_steers_sponsorships_around_a_shiur_it_requeues(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Main 2026-11-18: after a block was re-saved, a sponsorship was queued at 8:15
    inside the Daf Yomi. The rebuild had deleted the queued shiur before planning, so
    the planner did not see it."""
    station, made = await _library(db_session)
    made["show"].duration = 3000.0  # a 50-minute shiur at the top of every hour
    spots = [Asset(id=uuid.uuid4(), title=f"TEST spot {i}", file_path=f"x/s{i}.mp3", asset_type="spot",
                   category="sponsorship", duration=30) for i in range(4)]
    db_session.add_all(spots)
    station_id = station.id
    spot_ids = {a.id for a in spots}
    await db_session.commit()
    for i, sponsor_asset in enumerate(a.id for a in spots):  # enough plays that the quarter-past breaks would be used
        res = await client.post("/api/v1/sponsors", json={"name": f"TEST sponsor {i}", "length_seconds": 30.0, "audio_file_path": ""}, headers=auth_headers)
        assert res.status_code == 201, res.text
        res = await client.post(
            "/api/v1/spot-schedules",
            json={"sponsor_id": res.json()["id"], "asset_id": str(sponsor_asset), "station_id": str(station_id),
                  "dayparts": "overnight,morning,afternoon,evening", "plays_per_daypart": 3},
            headers=auth_headers,
        )
        assert res.status_code == 201, res.text
    shiur = _block(station, made, name="TEST Daf", items=[{"kind": "asset", "asset_id": str(made["show"].id)}])
    assert (await client.post("/api/v1/program-blocks", json=shiur, headers=auth_headers)).status_code == 201
    quarter = _block(
        station, made, name="TEST quarter", timing_mode="soft",
        times=",".join(f"{h:02d}:{m}" for h in range(24) for m in ("15", "45")),
        items=[{"kind": "sponsors", "count": 3}],
    )
    res = await client.post("/api/v1/program-blocks", json=quarter, headers=auth_headers)
    assert res.status_code == 201, res.text
    for _ in range(2):  # created, then re-saved unchanged: both rebuild the queue
        db_session.expire_all()
        entries = await _pending(db_session, station_id)
        shiur_at = {e.preempt_at for e in entries if e.asset_id == made["show"].id}
        inside = [e.preempt_at for e in entries if e.asset_id in spot_ids
                  and any(st < e.preempt_at < st + timedelta(minutes=45) for st in shiur_at)]
        assert shiur_at and not inside, inside[:3]
        res = await client.put(f"/api/v1/program-blocks/{res.json()['id']}", json=quarter, headers=auth_headers)
        assert res.status_code == 200, res.text
