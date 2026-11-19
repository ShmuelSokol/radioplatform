"""Sponsorship contracts hold: a contract never airs before its start date or after
its end date (in station time), keeps to its days, dayparts and plays per daypart,
and edits, cancellations and deletions take effect on the queue.

Built from Shmuel's ask (2026-11-17): "make sure that the contracts actually hold,
that if you say it ends tomorrow, it actually ends tomorrow, and it doesn't play
in 2 days from now automatically".
"""

import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.spot_schedule_service import (
    DAYPARTS,
    Blackout,
    daypart_of,
    plan_spots,
    reconcile_spot_slots,
)

JLM = ZoneInfo("Asia/Jerusalem")
NY = ZoneInfo("America/New_York")
ALL = "morning,afternoon,evening,overnight"


def _contract(name, dayparts=ALL, plays=1, days="0,1,2,3,4,5,6", **kw):
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


# ── the plan (pure) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("tz", [JLM, NY])
def test_end_today_and_end_tomorrow_stop_on_their_last_station_day(tz):
    today = date(2026, 11, 17)
    ends_today = _contract("t", plays=2, end_date=today, created_at="1")
    ends_tomorrow = _contract("m", end_date=today + timedelta(days=1), created_at="2")
    open_ended = _contract("o", created_at="3")
    placed, _ = plan_spots([ends_today, ends_tomorrow, open_ended], tz, today, 10)
    by = {c.name: [p.local.date() for p in placed if p.schedule is c] for c in (ends_today, ends_tomorrow, open_ended)}
    assert set(by["t"]) == {today}
    assert set(by["m"]) == {today, today + timedelta(days=1)}
    # the open-ended one keeps going, so the planner did look at later days
    assert max(by["o"]) == today + timedelta(days=9)
    # every placement is in station time on the day it says
    assert all(p.local.tzinfo is not None and p.local.utcoffset() == p.local.astimezone(tz).utcoffset() for p in placed)
    # the last play of "ends today" is before local midnight
    last = max(p.at for p in placed if p.schedule is ends_today)
    midnight = datetime(2026, 11, 18, tzinfo=tz).astimezone(UTC)
    assert last < midnight


def test_start_date_one_day_contract_and_days_of_week():
    today = date(2026, 11, 17)  # Tuesday
    future = _contract("f", start_date=date(2026, 11, 19), end_date=date(2026, 11, 21), created_at="1")
    one_day = _contract("d", start_date=date(2026, 11, 18), end_date=date(2026, 11, 18), created_at="2")
    mon_thu = _contract("w", dayparts="morning", plays=2, days="0,1,2,3", created_at="3")
    sunday = _contract("s", dayparts="afternoon,evening", days="6", created_at="4")
    placed, _ = plan_spots([future, one_day, mon_thu, sunday], JLM, today, 14)
    dates = lambda c: sorted({p.local.date() for p in placed if p.schedule is c})  # noqa: E731
    assert dates(future) == [date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 21)]
    assert dates(one_day) == [date(2026, 11, 18)]
    assert {d.weekday() for d in dates(mon_thu)} == {0, 1, 2, 3}
    assert {d.weekday() for d in dates(sunday)} == {6} and len(dates(sunday)) == 2


def test_caps_and_dayparts_are_exact():
    today = date(2026, 11, 17)
    heavy = _contract("h", dayparts="evening", plays=4, created_at="1")
    night = _contract("n", dayparts="overnight", plays=2, created_at="2")
    two_parts = _contract("p", dayparts="morning,afternoon", plays=1, created_at="3")
    placed, unplaced = plan_spots([heavy, night, two_parts], JLM, today, 7)
    assert not unplaced
    for c in (heavy, night, two_parts):
        per = Counter((p.local.date(), daypart_of(p.local.hour)) for p in placed if p.schedule is c)
        assert set(per.values()) == {c.plays_per_daypart}
        assert {part for _, part in per} == set(c.dayparts.split(","))
        for p in placed:
            if p.schedule is c:
                lo, hi = DAYPARTS[daypart_of(p.local.hour)]
                assert lo <= p.local.hour < hi
    # spread plays never sit on the hour (the time check owns hh:00)
    assert all(p.local.minute != 0 for p in placed)
    # never two sponsorships at the same minute
    assert len({p.at for p in placed}) == len(placed)


def test_overbooked_contract_is_reported_never_double_booked():
    today = date(2026, 11, 17)
    first = _contract("a", plays=3, created_at="1")
    greedy = _contract("g", plays=6, created_at="2")
    placed, unplaced = plan_spots([first, greedy], JLM, today, 2)
    # 6 hours x 3 slots = 18 places per daypart; 3 + 6 fit, so nothing is lost here
    assert not unplaced
    crowd = [_contract(f"c{i}", plays=6, created_at=f"{i:02d}") for i in range(4)]
    placed, unplaced = plan_spots(crowd, JLM, today, 1)
    assert len({p.at for p in placed}) == len(placed) == 4 * 18
    assert sum(getattr(u, "count", 1) for u in unplaced) == 4 * 6 * 4 - 4 * 18


def test_a_contract_through_shabbos_skips_the_blackout():
    fri = date(2026, 11, 20)
    c = _contract("s", start_date=fri, end_date=date(2026, 11, 23))
    start = datetime(2026, 11, 20, 16, 0, tzinfo=JLM).astimezone(UTC)
    end = datetime(2026, 11, 21, 17, 30, tzinfo=JLM).astimezone(UTC)
    placed, _ = plan_spots([c], JLM, date(2026, 11, 17), 8, blackouts=[Blackout(start, end, "Shabbos")])
    assert placed and not any(start <= p.at < end for p in placed)
    assert max(p.local.date() for p in placed) == date(2026, 11, 23)


def test_inactive_contract_plans_nothing():
    placed, _ = plan_spots([_contract("x", is_active=False)], JLM, date(2026, 11, 17), 7)
    assert placed == []


# ── the queue (database) ────────────────────────────────────────────────────


async def _station_with_contract_audio(db: AsyncSession, client: AsyncClient, headers: dict, tz="Asia/Jerusalem"):
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem test", timezone=tz, is_active=True)
    db.add(station)
    assets = []
    for i in range(2):
        a = Asset(
            id=uuid.uuid4(),
            title=f"TEST SPONSOR {i:02d}",
            file_path=f"spots/test{i}.mp3",
            duration=30.0,
            asset_type="spot",
            category="spots",
        )
        db.add(a)
        assets.append(a)
    await db.commit()
    r = await client.post(
        "/api/v1/sponsors",
        json={"name": "TEST - Lakewood sponsor", "length_seconds": 30.0, "audio_file_path": ""},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return station, assets, r.json()["id"]


async def _pending(db: AsyncSession, station_id, asset_id=None):
    q = select(QueueEntry).where(
        QueueEntry.station_id == station_id,
        QueueEntry.status == "pending",
        QueueEntry.source == "ad_slot",
    )
    if asset_id is not None:
        q = q.where(QueueEntry.asset_id == asset_id)
    db.expire_all()
    return (await db.execute(q)).scalars().all()


def _local_dates(entries, tz):
    return {e.preempt_at.replace(tzinfo=UTC).astimezone(tz).date() if e.preempt_at.tzinfo is None
            else e.preempt_at.astimezone(tz).date() for e in entries}


@pytest.mark.asyncio
async def test_edit_cancel_and_delete_take_effect_on_the_queue(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    station, assets, sponsor = await _station_with_contract_audio(db_session, client, auth_headers)
    station_id, asset_id = station.id, assets[0].id
    today = datetime.now(UTC).astimezone(JLM).date()
    url = "/api/v1/spot-schedules"
    r = await client.post(url, json={
        "sponsor_id": sponsor, "asset_id": str(asset_id), "station_id": str(station_id),
        "dayparts": ALL, "plays_per_daypart": 1,
        "start_date": today.isoformat(), "end_date": (today + timedelta(days=4)).isoformat(),
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    queued = await _pending(db_session, station_id, asset_id)
    assert max(_local_dates(queued, JLM)) == today + timedelta(days=4)

    # shortened to end tomorrow: nothing queued from the day after
    r = await client.put(f"{url}/{sid}", json={"end_date": (today + timedelta(days=1)).isoformat()},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    queued = await _pending(db_session, station_id, asset_id)
    assert queued and max(_local_dates(queued, JLM)) == today + timedelta(days=1)

    # start moved later: nothing before it
    r = await client.put(f"{url}/{sid}", json={
        "start_date": (today + timedelta(days=2)).isoformat(),
        "end_date": (today + timedelta(days=3)).isoformat()}, headers=auth_headers)
    assert r.status_code == 200
    queued = await _pending(db_session, station_id, asset_id)
    assert _local_dates(queued, JLM) == {today + timedelta(days=2), today + timedelta(days=3)}

    # cancelled (switched off): nothing left
    r = await client.put(f"{url}/{sid}", json={"is_active": False}, headers=auth_headers)
    assert r.status_code == 200
    assert await _pending(db_session, station_id, asset_id) == []

    # switched back on, then deleted: nothing left
    r = await client.put(f"{url}/{sid}", json={"is_active": True}, headers=auth_headers)
    assert await _pending(db_session, station_id, asset_id)
    r = await client.delete(f"{url}/{sid}", headers=auth_headers)
    assert r.status_code in (200, 204)
    assert await _pending(db_session, station_id, asset_id) == []


async def _contract_ending(db, client, headers, station, asset, sponsor, end: date):
    r = await client.post("/api/v1/spot-schedules", json={
        "sponsor_id": sponsor, "asset_id": str(asset.id), "station_id": str(station.id),
        "dayparts": ALL, "end_date": end.isoformat(),
    }, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_a_slot_still_waiting_after_the_last_day_never_airs(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """A 23:45 slot of a contract ending today, still waiting behind a long shiur at
    00:05, must not air: that is the day after the contract ended."""
    from app.api.v1.queue import _pick_next_pending
    from app.services.playout_engine import pick_next

    station, assets, sponsor = await _station_with_contract_audio(db_session, client, auth_headers)
    now = datetime.now(UTC)
    yesterday = now.astimezone(JLM).date() - timedelta(days=1)
    await _contract_ending(db_session, client, auth_headers, station, assets[0], sponsor, yesterday)
    late = QueueEntry(
        id=uuid.uuid4(), station_id=station.id, asset_id=assets[0].id, position=1,
        status="pending", source="ad_slot",
        preempt_at=datetime(yesterday.year, yesterday.month, yesterday.day, 23, 45, tzinfo=JLM).astimezone(UTC),
    )
    song = Asset(id=uuid.uuid4(), title="Song", file_path="m/song.mp3", duration=200.0, asset_type="music")
    db_session.add_all([late, song])
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=2,
                              status="pending", source="auto"))
    await db_session.commit()

    # playout engine: hands out the song, not the lapsed sponsorship
    picked = await pick_next(db_session, station.id)
    assert picked is not None and str(picked.asset_id) == str(song.id)

    # browser player: the lapsed slot is skipped and the song plays
    picked = await _pick_next_pending(db_session, station.id, now)
    assert picked is not None and str(picked.asset_id) == str(song.id)
    await db_session.refresh(late)
    assert late.status == "skipped"


@pytest.mark.asyncio
async def test_an_operator_can_still_play_contract_audio_by_hand(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    from app.services.playout_engine import pick_next

    station, assets, sponsor = await _station_with_contract_audio(db_session, client, auth_headers)
    yesterday = datetime.now(UTC).astimezone(JLM).date() - timedelta(days=1)
    await _contract_ending(db_session, client, auth_headers, station, assets[0], sponsor, yesterday)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=assets[0].id,
                              position=1, status="pending", source="manual"))
    await db_session.commit()
    picked = await pick_next(db_session, station.id)
    assert picked is not None and str(picked.asset_id) == str(assets[0].id)


@pytest.mark.asyncio
async def test_rotations_never_draw_contract_audio(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """"A spot every 3 songs" (or any rotation naming the Spots category) must not
    pick a sponsor's contract audio: it would air outside its dates and caps."""
    from app.models.asset import fit_for_random_pick, in_category, not_contract_audio, not_placeholder

    station, assets, sponsor = await _station_with_contract_audio(db_session, client, auth_headers)
    await _contract_ending(db_session, client, auth_headers, station, assets[0], sponsor,
                           date.today() + timedelta(days=1))
    free_spot = Asset(id=uuid.uuid4(), title="Station promo", file_path="s/promo.mp3",
                      duration=20.0, asset_type="spot", category="spots")
    # a loose copy of the contract's audio (same title, tied to nothing): Main's old
    # "Sponsor Ad Every 30min" rule drew such copies on 2026-11-18
    loose_copy = Asset(id=uuid.uuid4(), title=assets[0].title.upper(), file_path="s/copy.mp3",
                       duration=20.0, asset_type="spot", category="spots")
    db_session.add_all([free_spot, loose_copy])
    await db_session.commit()

    random_pick = (await db_session.execute(
        select(Asset.id).where(Asset.asset_type == "spot", fit_for_random_pick()))).scalars().all()
    by_category = (await db_session.execute(
        select(Asset.id).where(Asset.asset_type == "spot", in_category("spots"), not_placeholder(),
                               not_contract_audio()))).scalars().all()
    for ids in (random_pick, by_category):
        got = {str(i) for i in ids}
        assert str(assets[0].id) not in got
        assert str(loose_copy.id) not in got
        assert str(free_spot.id) in got
        # audio no contract uses yet is still an ordinary spot
        assert str(assets[1].id) in got

    # and the real refill with a "spots" rotation rule never queues it
    from app.services.queue_replenish_service import QueueReplenishService

    await db_session.commit()
    svc = QueueReplenishService(db_session, station.id)
    svc.exclude_ids = set()
    found = await svc._find_assets_for_rule(SimpleNamespace(asset_type="spot", category="spots", name="r"))
    assert str(assets[0].id) not in {str(a.id) for a in found}


@pytest.mark.asyncio
async def test_reconcile_keeps_every_queued_slot_inside_the_contract(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """Many contracts on one station, as in the live test: every queued slot is on a
    day, weekday and daypart its contract allows, and no minute holds two."""
    station, assets, sponsor = await _station_with_contract_audio(db_session, client, auth_headers)
    today = datetime.now(UTC).astimezone(JLM).date()
    extra = []
    for i in range(6):
        a = Asset(id=uuid.uuid4(), title=f"TEST SPONSOR X{i}", file_path=f"s/x{i}.mp3",
                  duration=30.0, asset_type="spot")
        db_session.add(a)
        extra.append(a)
    await db_session.commit()
    specs = [
        dict(end_date=today, dayparts=ALL, plays_per_daypart=2),
        dict(end_date=today + timedelta(days=1), dayparts="morning,evening"),
        dict(start_date=today + timedelta(days=2), end_date=today + timedelta(days=4)),
        dict(days_of_week="0,1,2,3", dayparts="morning", plays_per_daypart=2),
        dict(days_of_week="6", dayparts="afternoon,evening"),
        dict(dayparts="overnight", plays_per_daypart=2, end_date=today + timedelta(days=5)),
    ]
    made = {}
    for a, spec in zip(extra, specs):
        body = {"sponsor_id": sponsor, "asset_id": str(a.id), "station_id": str(station.id), **spec}
        for k in ("start_date", "end_date"):
            if k in body:
                body[k] = body[k].isoformat()
        r = await client.post("/api/v1/spot-schedules", json=body, headers=auth_headers)
        assert r.status_code == 201, r.text
        made[str(a.id)] = r.json()
    await reconcile_spot_slots(db_session, station.id)
    await db_session.commit()
    queued = await _pending(db_session, station.id)
    assert queued
    seen = Counter()
    for e in queued:
        at = e.preempt_at if e.preempt_at.tzinfo else e.preempt_at.replace(tzinfo=UTC)
        local = at.astimezone(JLM)
        c = made[str(e.asset_id)]
        if c.get("start_date"):
            assert local.date() >= date.fromisoformat(c["start_date"])
        if c.get("end_date"):
            assert local.date() <= date.fromisoformat(c["end_date"]), (c["name"], local)
        assert str(local.weekday()) in c["days_of_week"].split(",")
        assert daypart_of(local.hour) in c["dayparts"].split(",")
        seen[(str(e.asset_id), local.date(), daypart_of(local.hour))] += 1
        assert local.minute != 0
    for (asset_id, _, _), n in seen.items():
        assert n <= made[asset_id]["plays_per_daypart"]
    assert len({e.preempt_at for e in queued}) == len(queued)


@pytest.mark.asyncio
async def test_a_slot_about_to_air_counts_towards_the_contracts_plays(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """A slot inside the 2-minute margin is left alone. When a schedule edit had moved
    the plan away from it, the plan's own plays were added on top: three plays where
    the contract allows two (seen in CI at 23:29:45Z, 15 s before a :30 slot)."""
    station, assets, sponsor = await _station_with_contract_audio(db_session, client, auth_headers)
    asset_id, station_id = assets[0].id, station.id
    body = {"sponsor_id": sponsor, "asset_id": str(asset_id), "station_id": str(station_id),
            "dayparts": "afternoon", "plays_per_daypart": 2}
    r = await client.post("/api/v1/spot-schedules", json=body, headers=auth_headers)
    assert r.status_code == 201, r.text
    for e in await _pending(db_session, station_id):
        await db_session.delete(e)
    await db_session.commit()
    day = datetime.now(UTC).astimezone(JLM).date() + timedelta(days=1)
    now = datetime(day.year, day.month, day.day, 12, 28, 30, tzinfo=JLM).astimezone(UTC)
    # left from an earlier plan: this sponsorship, due in 90 s, at a minute the plan no longer uses
    stray_at = now.replace(second=0) + timedelta(minutes=2)
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station_id, asset_id=asset_id, position=1,
                              status="pending", source="ad_slot", preempt_at=stray_at, timing_mode="soft"))
    await db_session.commit()
    for _ in range(2):  # and it stays right when reconciled again
        await reconcile_spot_slots(db_session, station_id, now=now)
        await db_session.commit()
        plays = [e for e in await _pending(db_session, station_id, asset_id)
                 if (e.preempt_at if e.preempt_at.tzinfo else e.preempt_at.replace(tzinfo=UTC))
                 .astimezone(JLM).date() == day]
        assert len(plays) == 2, [e.preempt_at for e in plays]


@pytest.mark.asyncio
async def test_audio_swapped_out_of_its_contract_is_never_filler(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """2026-11-18: a contract moved from stand-in audio to the real voice; minutes later
    the music generator drew the stand-ins as plain spots on every station and two aired.
    Audio once tied to a sponsor stays out of every automatic pick."""
    from app.models.asset import fit_for_random_pick, in_category, not_contract_audio, not_placeholder

    station, assets, sponsor = await _station_with_contract_audio(db_session, client, auth_headers)
    sched = await _contract_ending(db_session, client, auth_headers, station, assets[0], sponsor,
                                   date.today() + timedelta(days=5))
    # the contract moves to the new recording
    r = await client.put(f"/api/v1/spot-schedules/{sched}", json={"asset_id": str(assets[1].id)},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    free_spot = Asset(id=uuid.uuid4(), title="Station promo", file_path="s/promo.mp3",
                      duration=20.0, asset_type="spot", category="spots")
    # a loose copy of the contract's audio (same title, tied to nothing): Main's old
    # "Sponsor Ad Every 30min" rule drew such copies on 2026-11-18
    loose_copy = Asset(id=uuid.uuid4(), title=assets[0].title.upper(), file_path="s/copy.mp3",
                       duration=20.0, asset_type="spot", category="spots")
    db_session.add_all([free_spot, loose_copy])
    await db_session.commit()

    random_pick = (await db_session.execute(
        select(Asset.id).where(Asset.asset_type == "spot", fit_for_random_pick()))).scalars().all()
    by_category = (await db_session.execute(
        select(Asset.id).where(Asset.asset_type == "spot", in_category("spots"), not_placeholder(),
                               not_contract_audio()))).scalars().all()
    for ids in (random_pick, by_category):
        got = {str(i) for i in ids}
        assert str(assets[0].id) not in got   # the old recording, no contract now
        assert str(assets[1].id) not in got   # the new one, in the contract
        assert str(free_spot.id) in got
