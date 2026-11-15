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
