"""Databank checklist rows (docs/test-checklist.md, F-DATABANK-*): sorting, filters,
spots end dates and what they do to the queue, the day log's edges, roles and errors."""
import time as _time
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.asset import Asset
from app.models.play_log import PlayLog, PlaySource
from app.models.queue_entry import QueueEntry
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.models.user import User, UserRole
from app.services import activity_log

NY = ZoneInfo("America/New_York")
JLM = ZoneInfo("Asia/Jerusalem")


def _asset(title, **kw):
    return Asset(id=kw.pop("id", uuid.uuid4()), title=title, duration=kw.pop("duration", 200.0),
                 file_path=kw.pop("file_path", f"https://cdn.example.com/audio/{title}.mp3"),
                 asset_type=kw.pop("asset_type", "music"), **kw)


def _play(station, asset, start, end=None):
    return PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=asset.id if asset else None,
                   start_utc=start, end_utc=end, source=PlaySource.SCHEDULER)


async def _login(client, db, role, station_ids=None):
    email = f"{role.value}-{uuid.uuid4().hex[:6]}"
    db.add(User(id=uuid.uuid4(), email=email, hashed_password=hash_password("pw12345678"),
                role=role, is_active=True, station_ids=station_ids))
    await db.commit()
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345678"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _items(client, headers, **params):
    r = await client.get("/api/v1/databank/items", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ── F-DATABANK-2 / 3: sort by every column, both directions; last played ──


@pytest.mark.asyncio
async def test_sort_every_column_both_directions_ties_and_last_played(client, auth_headers, db_session):
    main = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    other = Station(id=uuid.uuid4(), name="Other", timezone="Asia/Jerusalem")
    a = _asset("Alef", artist="Chevra", category="b fast", vote=50, duration=100.0, intro_seconds=5.0,
               file_path="https://x/a/zz.mp3")
    b = _asset("Beis", artist="Abie", category="a slow", vote=90, duration=300.0, intro_seconds=None,
               file_path="https://x/a/mm.mp3")
    c = _asset("Gimmel", artist="Benny", category="c mid", vote=10, duration=200.0, intro_seconds=12.0,
               file_path="https://x/a/aa.mp3")
    # Ties: same artist as c, so title then id decide
    d = _asset("Dalet", artist="Benny", category="c mid", vote=10, duration=200.0, intro_seconds=12.0,
               file_path="https://x/a/aa2.mp3")
    db_session.add_all([main, other, a, b, c, d])
    await db_session.flush()
    db_session.add_all([
        _play(main, a, datetime(2026, 11, 10, 12, tzinfo=UTC)),
        _play(main, a, datetime(2026, 11, 12, 12, tzinfo=UTC)),  # latest on main
        _play(main, b, datetime(2026, 11, 11, 12, tzinfo=UTC)),
        _play(other, c, datetime(2026, 11, 15, 12, tzinfo=UTC)),  # only on the other station
    ])
    await db_session.commit()

    cases = {
        "artist": ["Beis", "Dalet", "Gimmel", "Alef"],
        "title": ["Alef", "Beis", "Dalet", "Gimmel"],
        "type": ["Beis", "Alef", "Dalet", "Gimmel"],
        "vote": ["Dalet", "Gimmel", "Alef", "Beis"],
        "length": ["Alef", "Dalet", "Gimmel", "Beis"],
        "file": ["Gimmel", "Dalet", "Beis", "Alef"],
    }
    for col, asc in cases.items():
        got = [i["title"] for i in (await _items(client, auth_headers, sort=col))["items"]]
        assert got == asc, f"{col} ascending: {got}"
        got = [i["title"] for i in (await _items(client, auth_headers, sort=col, desc="true"))["items"]]
        # Ties are always title then id, ascending, in both directions
        tied = {"artist": ["Alef", "Dalet", "Gimmel", "Beis"],
                "type": ["Dalet", "Gimmel", "Alef", "Beis"],
                "vote": ["Beis", "Alef", "Dalet", "Gimmel"],
                "length": ["Beis", "Dalet", "Gimmel", "Alef"]}.get(col, list(reversed(asc)))
        assert got == tied, f"{col} descending: {got}"

    # Intro: empty intro comes last both ways
    got = [i["title"] for i in (await _items(client, auth_headers, sort="intro"))["items"]]
    assert got == ["Alef", "Dalet", "Gimmel", "Beis"]
    got = [i["title"] for i in (await _items(client, auth_headers, sort="intro", desc="true"))["items"]]
    assert got == ["Dalet", "Gimmel", "Alef", "Beis"]

    # Last played on the chosen station: never-played (on that station) last in BOTH directions
    on_main = await _items(client, auth_headers, sort="last_played", desc="true", station_id=str(main.id))
    assert [i["title"] for i in on_main["items"]] == ["Alef", "Beis", "Dalet", "Gimmel"]
    assert on_main["items"][0]["last_played"].startswith("2026-11-12T12:00")
    on_main = await _items(client, auth_headers, sort="last_played", station_id=str(main.id))
    assert [i["title"] for i in on_main["items"]] == ["Beis", "Alef", "Dalet", "Gimmel"]
    assert on_main["items"][2]["last_played"] is None
    # All stations when none is chosen: Gimmel's play on the other station counts
    every = await _items(client, auth_headers, sort="last_played", desc="true")
    assert [i["title"] for i in every["items"]][:3] == ["Gimmel", "Alef", "Beis"]

    # Paging keeps the order across pages
    p1 = await _items(client, auth_headers, sort="title", limit=2)
    p2 = await _items(client, auth_headers, sort="title", limit=2, skip=2)
    assert [i["title"] for i in p1["items"] + p2["items"]] == ["Alef", "Beis", "Dalet", "Gimmel"]
    assert p1["total"] == p2["total"] == 4


# ── F-DATABANK-4 / 6 / 7: Find, type filter, Mpeg / Prsnt, together ──


@pytest.mark.asyncio
async def test_type_filter_find_and_files_work_together(client, auth_headers, db_session):
    db_session.add_all([
        _asset("Lively one", artist="Abie", category="Lively", file_path="https://x/a/one.MP3?token=1"),
        _asset("Lively two", artist="Benny", category="lively", file_path="https://x/a/two.wav"),
        _asset("Slow one", artist="Abie", category="slow", file_path="https://x/a/three.mp2"),
        _asset("Lively gone", artist="Abie", category="lively", file_path=""),
        _asset("Bad", artist="Abie", category="do_not_play", file_path="https://x/a/bad.mp3"),
        _asset("Lively jingle", asset_type="jingle", category="lively"),
    ])
    await db_session.commit()

    t = lambda body: sorted(i["title"] for i in body["items"])  # noqa: E731
    lively = await _items(client, auth_headers, category="LIVELY")
    assert t(lively) == ["Lively gone", "Lively one", "Lively two"] and lively["total"] == 3
    # Find is case-insensitive over artist, title, type and file
    assert t(await _items(client, auth_headers, search="abie")) == ["Bad", "Lively gone", "Lively one", "Slow one"]
    assert t(await _items(client, auth_headers, search="THREE.mp2")) == ["Slow one"]
    assert t(await _items(client, auth_headers, search="do_not")) == ["Bad"]
    # Type + Find
    assert t(await _items(client, auth_headers, category="lively", search="abie")) == ["Lively gone", "Lively one"]
    # Type + Mpeg (upper-case extension and a ?query on the address count)
    assert t(await _items(client, auth_headers, category="lively", files="mpeg")) == ["Lively one"]
    assert t(await _items(client, auth_headers, files="mpeg")) == ["Bad", "Lively one", "Slow one"]
    # Prsnt: a file, and not do_not_play
    assert t(await _items(client, auth_headers, files="present")) == ["Lively one", "Lively two", "Slow one"]
    assert t(await _items(client, auth_headers, files="present", category="lively", search="benny")) == ["Lively two"]
    # The jingle never shows on Songs, and the Jingles tab filters by group
    assert t(await _items(client, auth_headers, asset_type="jingle", category="Lively")) == ["Lively jingle"]


# ── F-DATABANK-13: every staff role reads spots and the log; sponsors and strangers do not ──


@pytest.mark.asyncio
async def test_spots_and_log_are_staff_only(client, db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="UTC")
    db_session.add(station)
    await db_session.commit()
    paths = ["/api/v1/databank/items", "/api/v1/databank/spots",
             f"/api/v1/databank/log?station_id={station.id}"]
    for role in UserRole:
        headers = await _login(client, db_session, role)
        for p in paths:
            r = await client.get(p, headers=headers)
            if role == UserRole.SPONSOR:
                assert r.status_code == 403, f"sponsor read {p}"
            else:
                assert r.status_code == 200, f"{role.value} {p}: {r.status_code}"
        # Only managers move end dates or edit items
        r = await client.patch(f"/api/v1/databank/spots/{uuid.uuid4()}/end-date", json={"end_date": None},
                               headers=headers)
        if role in (UserRole.ADMIN, UserRole.MANAGER):
            assert r.status_code == 409  # gets past the guard; the spot just has no schedule
        else:
            assert r.status_code == 403
    for p in paths:
        assert (await client.get(p)).status_code in (401, 403)


# ── F-DATABANK-15: generated time/weather clips never listed ──


@pytest.mark.asyncio
async def test_generated_clips_left_out_of_every_tab_and_counts(client, auth_headers, db_session):
    db_session.add_all([
        _asset("10 am", asset_type="jingle"),
        _asset("Time Announcement - 10:00", asset_type="jingle"),
        _asset("Weather Report - 2026-11-17", asset_type="jingle"),
        _asset("Bagel spot", asset_type="spot"),
        _asset("Weather Report - 2026-11-17 08:00", asset_type="spot"),
        _asset("Time Announcement - 11:00", asset_type="spot"),
    ])
    await db_session.commit()
    jingles = await _items(client, auth_headers, asset_type="jingle")
    assert [i["title"] for i in jingles["items"]] == ["10 am"] and jingles["total"] == 1
    spots = (await client.get("/api/v1/databank/spots", headers=auth_headers)).json()["spots"]
    assert [s["title"] for s in spots] == ["Bagel spot"]


# ── F-DATABANK-16 / 17 / 41: spot end date, "no end", "(off)", station-less, expired by station day ──


async def _sponsor(db):
    s = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, priority=1, audio_file_path="")
    db.add(s)
    await db.flush()
    return s


def _sched(sponsor, spot, station, start, end, **kw):
    return SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id,
                        station_id=station.id if station else None, start_date=start, end_date=end, **kw)


@pytest.mark.asyncio
async def test_spot_end_date_rules(client, auth_headers, db_session):
    main = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    other = Station(id=uuid.uuid4(), name="Other", timezone="America/New_York")
    db_session.add_all([main, other])
    sponsor = await _sponsor(db_session)
    today = datetime.now(NY).date()
    s_open = _asset("Open ended", asset_type="spot")
    s_off = _asset("Switched off", asset_type="spot")
    s_less = _asset("Every station", asset_type="spot")
    s_mixed = _asset("Mixed", asset_type="spot")
    s_elsewhere = _asset("Elsewhere", asset_type="spot")
    db_session.add_all([s_open, s_off, s_less, s_mixed, s_elsewhere])
    await db_session.flush()
    db_session.add_all([
        _sched(sponsor, s_open, main, today - timedelta(days=3), today + timedelta(days=3)),
        _sched(sponsor, s_open, main, today - timedelta(days=3), None),
        _sched(sponsor, s_off, main, today - timedelta(days=3), today + timedelta(days=9), is_active=False),
        _sched(sponsor, s_less, None, today - timedelta(days=3), today + timedelta(days=4)),
        _sched(sponsor, s_less, main, today - timedelta(days=3), today + timedelta(days=2)),
        _sched(sponsor, s_mixed, main, today - timedelta(days=30), today - timedelta(days=2)),
        _sched(sponsor, s_mixed, main, today - timedelta(days=30), None),
        _sched(sponsor, s_elsewhere, other, today - timedelta(days=30), today + timedelta(days=50)),
    ])
    await db_session.commit()

    body = (await client.get(f"/api/v1/databank/spots?station_id={main.id}", headers=auth_headers)).json()
    assert body["today"] == today.isoformat()
    rows = {s["title"]: s for s in body["spots"]}
    assert rows["Open ended"]["open_ended"] is True and rows["Open ended"]["end_date"] is None
    assert rows["Open ended"]["schedules"] == 2 and rows["Open ended"]["expired"] is False
    assert rows["Switched off"]["active"] is False and rows["Switched off"]["schedules"] == 1
    # station-less schedule counts on this station: the latest of the two ends
    assert rows["Every station"]["end_date"] == (today + timedelta(days=4)).isoformat()
    assert rows["Every station"]["schedules"] == 2
    # one expired + one open-ended: not red
    assert rows["Mixed"]["expired"] is False
    # scheduled only on another station: "not scheduled" here
    assert rows["Elsewhere"]["schedules"] == 0 and rows["Elsewhere"]["expired"] is False
    # no station chosen: every station's schedules
    rows = {s["title"]: s for s in (await client.get("/api/v1/databank/spots", headers=auth_headers)).json()["spots"]}
    assert rows["Elsewhere"]["end_date"] == (today + timedelta(days=50)).isoformat()


@pytest.mark.asyncio
async def test_expired_is_judged_by_the_stations_own_today(client, auth_headers, db_session, monkeypatch):
    """At 23:30 on Nov 17 in New York it is already Nov 18 in Jerusalem: a spot that
    ended on Nov 17 is red on the Jerusalem station only."""
    import app.api.v1.databank as databank

    moment = datetime(2026, 11, 18, 4, 30, tzinfo=UTC)  # 23:30 NY Nov 17, 06:30 Jerusalem Nov 18

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(databank, "datetime", Frozen)
    ny = Station(id=uuid.uuid4(), name="NY", timezone="America/New_York")
    jlm = Station(id=uuid.uuid4(), name="JLM", timezone="Asia/Jerusalem")
    db_session.add_all([ny, jlm])
    sponsor = await _sponsor(db_session)
    spot = _asset("Ends Nov 17", asset_type="spot")
    db_session.add(spot)
    await db_session.flush()
    db_session.add_all([
        _sched(sponsor, spot, ny, date(2026, 11, 1), date(2026, 11, 17)),
        _sched(sponsor, spot, jlm, date(2026, 11, 1), date(2026, 11, 17)),
    ])
    await db_session.commit()
    on_ny = (await client.get(f"/api/v1/databank/spots?station_id={ny.id}", headers=auth_headers)).json()
    on_jlm = (await client.get(f"/api/v1/databank/spots?station_id={jlm.id}", headers=auth_headers)).json()
    assert on_ny["today"] == "2026-11-17" and on_ny["spots"][0]["expired"] is False
    assert on_jlm["today"] == "2026-11-18" and on_jlm["spots"][0]["expired"] is True
    # no station: UTC (Nov 18)
    anywhere = (await client.get("/api/v1/databank/spots", headers=auth_headers)).json()
    assert anywhere["today"] == "2026-11-18" and anywhere["spots"][0]["expired"] is True


# ── F-DATABANK-19 / 39 / 40: moving the end date changes what airs on every station ──


async def _ad_slots(db, station_id):
    rows = await db.execute(select(QueueEntry).where(
        QueueEntry.station_id == station_id, QueueEntry.source == "ad_slot", QueueEntry.status == "pending"
    ).execution_options(populate_existing=True))
    return rows.scalars().all()


def _local_day(entry, tz):
    at = entry.preempt_at if entry.preempt_at.tzinfo else entry.preempt_at.replace(tzinfo=UTC)
    return at.astimezone(tz).date()


@pytest.mark.asyncio
async def test_end_date_refreshes_up_next_on_every_station(client, auth_headers, db_session):
    main = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York", is_active=True)
    second = Station(id=uuid.uuid4(), name="Second", timezone="America/New_York", is_active=True)
    db_session.add_all([main, second])
    sponsor = await _sponsor(db_session)
    spot = _asset("Bagel King", asset_type="spot", duration=30.0)
    db_session.add(spot)
    await db_session.flush()
    today = datetime.now(NY).date()
    on_main = _sched(sponsor, spot, main, today - timedelta(days=10), today - timedelta(days=1),
                     fixed_times="03:00,09:00,15:00,21:00")
    everywhere = _sched(sponsor, spot, None, today - timedelta(days=10), today - timedelta(days=1),
                        fixed_times="04:00,10:00,16:00,22:00")
    db_session.add_all([on_main, everywhere])
    await db_session.commit()
    spot_id, main_id, second_id = spot.id, main.id, second.id
    assert await _ad_slots(db_session, main_id) == []

    # A later end: the pending slots show up in Up next at once, on both stations
    later = today + timedelta(days=3)
    t0 = _time.perf_counter()
    r = await client.patch(f"/api/v1/databank/spots/{spot_id}/end-date?station_id={main_id}",
                           json={"end_date": later.isoformat()}, headers=auth_headers)
    took = _time.perf_counter() - t0
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 2, "end_date": later.isoformat()}
    assert took < 1.0, f"end-date save with queue refresh took {took:.2f}s (S-6)"
    main_slots = await _ad_slots(db_session, main_id)
    second_slots = await _ad_slots(db_session, second_id)
    assert main_slots and second_slots, "the station-less schedule must refresh every station"
    assert max(_local_day(e, NY) for e in main_slots) == later
    assert {e.preempt_at.astimezone(NY).hour if e.preempt_at.tzinfo else e.preempt_at.replace(tzinfo=UTC).astimezone(NY).hour
            for e in second_slots} <= {4, 10, 16, 22}

    # Ends today: the slots of later days go
    r = await client.patch(f"/api/v1/databank/spots/{spot_id}/end-date?station_id={main_id}",
                           json={"end_date": today.isoformat()}, headers=auth_headers)
    assert r.status_code == 200, r.text
    for st in (main_id, second_id):
        assert all(_local_day(e, NY) <= today for e in await _ad_slots(db_session, st))

    # Cleared: open-ended on every schedule (F-DATABANK-40)
    r = await client.patch(f"/api/v1/databank/spots/{spot_id}/end-date?station_id={main_id}",
                           json={"end_date": None}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["end_date"] is None
    ends = (await db_session.execute(select(SpotSchedule.end_date).where(SpotSchedule.asset_id == spot_id))).scalars().all()
    assert ends == [None, None]
    rows = (await client.get(f"/api/v1/databank/spots?station_id={main_id}", headers=auth_headers)).json()["spots"]
    assert rows[0]["open_ended"] is True and rows[0]["expired"] is False
    assert max(_local_day(e, NY) for e in await _ad_slots(db_session, main_id)) > today


@pytest.mark.asyncio
async def test_end_date_with_a_station_moves_only_that_station_and_station_less(client, auth_headers, db_session):
    main = Station(id=uuid.uuid4(), name="Main", timezone="UTC")
    other = Station(id=uuid.uuid4(), name="Other", timezone="UTC")
    db_session.add_all([main, other])
    sponsor = await _sponsor(db_session)
    spot = _asset("Shared spot", asset_type="spot")
    db_session.add(spot)
    await db_session.flush()
    start, end = date(2026, 11, 1), date(2026, 11, 30)
    a = _sched(sponsor, spot, main, start, end)
    b = _sched(sponsor, spot, other, start, end)
    c = _sched(sponsor, spot, None, start, end)
    db_session.add_all([a, b, c])
    await db_session.commit()
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={main.id}",
                           json={"end_date": "2026-12-31"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["updated"] == 2
    got = {s.id: s.end_date for s in (await db_session.execute(
        select(SpotSchedule).execution_options(populate_existing=True))).scalars()}
    assert got[a.id] == date(2026, 12, 31) and got[c.id] == date(2026, 12, 31)
    assert got[b.id] == end


# ── F-DATABANK-20: refused end dates change nothing ──


@pytest.mark.asyncio
async def test_end_date_errors_change_nothing(client, auth_headers, db_session):
    mine = Station(id=uuid.uuid4(), name="Mine", timezone="UTC")
    theirs = Station(id=uuid.uuid4(), name="Theirs", timezone="UTC")
    db_session.add_all([mine, theirs])
    sponsor = await _sponsor(db_session)
    spot = _asset("Two-station spot", asset_type="spot")
    db_session.add(spot)
    await db_session.flush()
    s1 = _sched(sponsor, spot, mine, date(2026, 11, 10), date(2026, 11, 30))
    s2 = _sched(sponsor, spot, theirs, date(2026, 11, 1), date(2026, 11, 30))
    db_session.add_all([s1, s2])
    await db_session.commit()
    spot_id, mine_id = spot.id, mine.id

    async def ends():
        return sorted((await db_session.execute(
            select(SpotSchedule.end_date).where(SpotSchedule.asset_id == spot_id))).scalars().all())

    # Before one schedule's start: 422, neither moves
    r = await client.patch(f"/api/v1/databank/spots/{spot_id}/end-date", json={"end_date": "2026-11-05"},
                           headers=auth_headers)
    assert r.status_code == 422 and "before the schedule starts" in r.json()["detail"]
    assert await ends() == [date(2026, 11, 30)] * 2

    # A manager assigned to one station only: 403 for the spot on both, nothing changed
    manager = await _login(client, db_session, UserRole.MANAGER, [str(mine_id)])
    r = await client.patch(f"/api/v1/databank/spots/{spot_id}/end-date", json={"end_date": "2026-12-31"},
                           headers=manager)
    assert r.status_code == 403 and r.json()["detail"] == "You are not assigned to this station"
    assert await ends() == [date(2026, 11, 30)] * 2
    # ... but their own station works
    r = await client.patch(f"/api/v1/databank/spots/{spot_id}/end-date?station_id={mine_id}",
                           json={"end_date": "2026-12-31"}, headers=manager)
    assert r.status_code == 200, r.text
    assert await ends() == [date(2026, 11, 30), date(2026, 12, 31)]

    # Bad date: 422 from validation
    r = await client.patch(f"/api/v1/databank/spots/{spot_id}/end-date", json={"end_date": "31/12/2026"},
                           headers=auth_headers)
    assert r.status_code == 422


# ── F-DATABANK-22 / 23 / 44: day log edges, clock changes, lines ──


@pytest.mark.asyncio
async def test_day_log_midnight_edges_and_clock_change_days(client, auth_headers, db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    song = _asset("Edge song")
    db_session.add_all([station, song])
    await db_session.flush()

    def local(y, m, d, hh, mm, ss=0, us=0):
        return datetime(y, m, d, hh, mm, ss, us, tzinfo=NY).astimezone(UTC)

    plays = [
        local(2026, 11, 16, 23, 59, 59, 999000),   # last moment of Nov 16
        local(2026, 11, 17, 0, 0, 0, 0),           # first moment of Nov 17
        # 23-hour day (Mar 8, 2026) and 25-hour day (Nov 1, 2026)
        local(2026, 3, 8, 0, 0, 0), local(2026, 3, 8, 23, 59, 59),
        datetime(2026, 11, 1, 5, 30, tzinfo=UTC),   # 01:30 EDT (first 01:30)
        datetime(2026, 11, 1, 6, 30, tzinfo=UTC),   # 01:30 EST (second 01:30)
        local(2026, 11, 1, 0, 0, 0), local(2026, 11, 1, 23, 59, 59),
        local(2026, 11, 2, 0, 0, 0),
    ]
    for t in plays:
        db_session.add(_play(station, song, t, t + timedelta(minutes=3)))
    await db_session.commit()

    async def day(d):
        r = await client.get(f"/api/v1/databank/log?station_id={station.id}&day={d}", headers=auth_headers)
        assert r.status_code == 200, r.text
        return r.json()["logs"]

    nov16 = await day("2026-11-16")
    assert [log["start_utc"][:23] for log in nov16] == ["2026-11-17T04:59:59.999"]
    nov17 = await day("2026-11-17")
    assert [log["start_utc"][:19] for log in nov17] == ["2026-11-17T05:00:00"]
    assert len(await day("2026-03-08")) == 2
    nov1 = await day("2026-11-01")
    assert len(nov1) == 4, [x["start_utc"] for x in nov1]
    assert [x["start_utc"] for x in nov1] == sorted(x["start_utc"] for x in nov1)
    assert len(await day("2026-11-02")) == 1


@pytest.mark.asyncio
async def test_day_log_lines_on_air_deleted_and_today(client, auth_headers, db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="UTC")
    song = _asset("Still on", artist="Abie")
    gone = _asset("Deleted later")
    db_session.add_all([station, song, gone])
    await db_session.flush()
    now = datetime.now(UTC).replace(microsecond=0)
    db_session.add_all([
        _play(station, gone, now - timedelta(minutes=10), now - timedelta(minutes=5)),
        _play(station, song, now - timedelta(minutes=1)),  # on air: no end yet
    ])
    await db_session.commit()
    await db_session.delete(gone)
    await db_session.commit()
    r = await client.get(f"/api/v1/databank/log?station_id={station.id}", headers=auth_headers)  # today
    body = r.json()
    assert body["day"] == now.date().isoformat() and body["timezone"] == "UTC"
    # In the first ten minutes of the UTC day the earlier play belongs to yesterday's
    # log (this failed in CI at 00:06Z), and in the first minute so does the one on air.
    expected = [
        row for row, minutes_ago in (
            (("Unknown", None, False), 10), (("Still on", "Abie", True), 1),
        ) if (now - timedelta(minutes=minutes_ago)).date() == now.date()
    ]
    assert [(x["title"], x["artist"], x["end_utc"] is None) for x in body["logs"]] == expected
    files = {x["title"]: x["file"] for x in body["logs"]}
    assert files.get("Unknown", "") == "" and files.get("Still on", "Still on.mp3") == "Still on.mp3"


# ── F-DATABANK-46: API errors ──


@pytest.mark.asyncio
async def test_databank_api_errors(client, auth_headers, db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="UTC")
    db_session.add_all([station, _asset("B title", artist="Abie"), _asset("A title", artist="Benny")])
    await db_session.commit()
    missing = uuid.uuid4()
    assert (await client.get(f"/api/v1/databank/log?station_id={missing}", headers=auth_headers)).status_code == 404
    assert (await client.get(f"/api/v1/databank/spots?station_id={missing}", headers=auth_headers)).status_code == 404
    for bad in ("17-11-2026", "2026-13-01", "today"):
        r = await client.get(f"/api/v1/databank/log?station_id={station.id}&day={bad}", headers=auth_headers)
        assert r.status_code == 422, bad
    assert (await client.get("/api/v1/databank/log", headers=auth_headers)).status_code == 422
    for params in ({"limit": 501}, {"limit": 0}, {"skip": -1}):
        assert (await client.get("/api/v1/databank/items", params=params, headers=auth_headers)).status_code == 422
    body = await _items(client, auth_headers, sort="nonsense")
    assert [i["artist"] for i in body["items"]] == ["Abie", "Benny"]


# ── F-DATABANK-12 (server side): a title cannot be emptied ──


@pytest.mark.asyncio
async def test_asset_edit_refuses_an_empty_title_and_clears_artist_and_type(client, auth_headers, db_session):
    a = _asset("Keep me", artist="Abie", category="lively", vote=50, intro_seconds=8.0)
    db_session.add(a)
    await db_session.commit()
    for bad in ({"title": None}, {"title": "  "}, {"vote": 101}, {"intro_seconds": 601}):
        r = await client.patch(f"/api/v1/assets/{a.id}", json=bad, headers=auth_headers)
        assert r.status_code == 422, (bad, r.status_code, r.text)
    r = await client.patch(f"/api/v1/assets/{a.id}", json={"artist": None, "category": None, "intro_seconds": None},
                           headers=auth_headers)
    assert r.status_code == 200, r.text
    row = (await _items(client, auth_headers))["items"][0]
    assert (row["title"], row["artist"], row["type"], row["intro"], row["vote"]) == ("Keep me", None, None, None, 50)


# ── F-DATABANK-53: every change made here is in the Activity log with before and after ──


@pytest.mark.asyncio
async def test_item_edit_and_end_date_are_in_the_activity_log(client, auth_headers, db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="UTC")
    db_session.add(station)
    sponsor = await _sponsor(db_session)
    song = _asset("Logged song", vote=50)
    spot = _asset("Logged spot", asset_type="spot")
    db_session.add_all([song, spot])
    await db_session.flush()
    db_session.add(_sched(sponsor, spot, station, date(2026, 11, 1), date(2026, 11, 30)))
    await db_session.commit()

    r = await client.patch(f"/api/v1/assets/{song.id}", json={"vote": 77}, headers=auth_headers)
    assert r.status_code == 200
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={station.id}",
                           json={"end_date": "2026-12-25"}, headers=auth_headers)
    assert r.status_code == 200
    await activity_log.wait_idle()
    items = (await client.get("/api/v1/activity", headers=auth_headers)).json()["items"]

    def fields_of(pred):
        for row in items:
            for obj in ((row.get("changes") or {}).get("objects") or []):
                if pred(row, obj):
                    return obj.get("fields") or {}
        return None

    vote = fields_of(lambda row, obj: "vote" in (obj.get("fields") or {}))
    assert vote and vote["vote"] == {"old": 50, "new": 77}, items[:3]
    end = fields_of(lambda row, obj: "end_date" in (obj.get("fields") or {}))
    assert end and end["end_date"]["old"] == "2026-11-30" and end["end_date"]["new"] == "2026-12-25", items[:3]


# ── F-DATABANK-25 / 27: speed on a big library with a long play log (SQLite stand-in) ──


@pytest.mark.asyncio
async def test_items_page_speed_with_a_long_play_log(client, auth_headers, db_session):
    """A smaller stand-in for S-4 (the real check is Postgres with ~23,000 songs): 3,000
    songs and 30,000 plays; a first page, a Last played sort and a Find each answer
    within 300 ms of server time."""
    from sqlalchemy import insert

    station = Station(id=uuid.uuid4(), name="Main", timezone="UTC")
    db_session.add(station)
    await db_session.flush()
    ids = [uuid.uuid4() for _ in range(3000)]
    await db_session.execute(insert(Asset), [
        {"id": i, "title": f"Song {n}", "artist": f"Artist {n % 300}", "duration": 200.0, "file_path": f"s{n}.mp3",
         "asset_type": "music", "category": f"cat {n % 12}", "vote": n % 100}
        for n, i in enumerate(ids)])
    base = datetime(2024, 1, 1, tzinfo=UTC)
    await db_session.execute(insert(PlayLog), [
        {"id": uuid.uuid4(), "station_id": station.id, "asset_id": ids[(n * 7) % 3000],
         "start_utc": base + timedelta(minutes=4 * n), "source": PlaySource.SCHEDULER}
        for n in range(30000)])
    await db_session.commit()
    for params in ({}, {"sort": "last_played", "desc": "true", "station_id": str(station.id)},
                   {"search": "artist 12"}, {"sort": "vote", "skip": 2800}):
        await _items(client, auth_headers, **params)  # warm
        # the best of three: the limit is on what the code costs (about 45 ms here), and one
        # stalled call on a shared test machine (337 ms seen once in CI) is not that
        took = 9.0
        for _ in range(3):
            t0 = _time.perf_counter()
            body = await _items(client, auth_headers, **params)
            took = min(took, _time.perf_counter() - t0)
            assert body["items"], params
        assert took < 0.3, f"{params}: {took * 1000:.0f} ms"
