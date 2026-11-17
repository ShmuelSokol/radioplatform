"""Databank checklist rows (docs/test-checklist.md, F-DATABANK-*): sorting, filters,
spots end dates, the day log, roles, errors, speed and the activity log.
The screen side of these rows is checked by frontend/e2e/databank-screen.mjs."""
import os
import statistics
import time
import uuid
from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import insert, select

import app.api.v1.databank as databank
import app.api.v1.queue as queue_module
from app.core.security import hash_password
from app.models.asset import Asset
from app.models.play_log import PlayLog, PlaySource
from app.models.queue_entry import QueueEntry
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.models.user import User, UserRole
from app.services import activity_log

# Shared CI runners are 2-3x slower than the server; the 300 ms rule is held locally.
LIMIT_S = 1.0 if os.environ.get("CI") else 0.3
SAVE_LIMIT_S = 2.0 if os.environ.get("CI") else 1.0


def _asset(title, **kw):
    return Asset(id=uuid.uuid4(), title=title, duration=kw.pop("duration", 200.0),
                 file_path=kw.pop("file_path", f"https://cdn.example.com/audio/{title}.mp3"),
                 asset_type=kw.pop("asset_type", "music"), **kw)


async def _login(client, db, role, station_ids=None):
    email = f"{role.value}-{uuid.uuid4().hex[:6]}"
    db.add(User(id=uuid.uuid4(), email=email, hashed_password=hash_password("pw12345678"),
                role=role, is_active=True, station_ids=station_ids))
    await db.commit()
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345678"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _items(client, headers, **params):
    r = await client.get("/api/v1/databank/items", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _titles(body):
    return [i["title"] for i in body["items"]]


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


# ── F-DATABANK-2: server sort by every column, both directions, ties by title then id ──

@pytest.mark.asyncio
async def test_songs_sort_by_every_column_both_directions(client, auth_headers, db_session):
    st = Station(id=uuid.uuid4(), name="Main", timezone="UTC")
    rows = [
        _asset("Bet", artist="Chaim", category="b slow", vote=10, duration=300.0, intro_seconds=5.0,
               file_path="https://x/a/zz.mp3"),
        _asset("Aleph", artist="Avi", category="c fast", vote=90, duration=100.0, intro_seconds=12.0,
               file_path="https://x/a/mm.mp3"),
        _asset("Gimmel", artist="Baruch", category="a oldie", vote=50, duration=200.0, intro_seconds=1.0,
               file_path="https://x/a/aa.mp3"),
    ]
    db_session.add_all([st, *rows])
    await db_session.flush()
    for asset, days_ago in ((rows[0], 3), (rows[1], 1), (rows[2], 2)):
        db_session.add(PlayLog(id=uuid.uuid4(), station_id=st.id, asset_id=asset.id, source=PlaySource.SCHEDULER,
                               start_utc=datetime.now(UTC) - timedelta(days=days_ago)))
    await db_session.commit()

    expect = {
        "artist": ["Aleph", "Gimmel", "Bet"],
        "title": ["Aleph", "Bet", "Gimmel"],
        "type": ["Gimmel", "Bet", "Aleph"],
        "vote": ["Bet", "Gimmel", "Aleph"],
        "length": ["Aleph", "Gimmel", "Bet"],
        "intro": ["Gimmel", "Bet", "Aleph"],
        "file": ["Gimmel", "Aleph", "Bet"],
        "last_played": ["Bet", "Gimmel", "Aleph"],
    }
    for col, order in expect.items():
        up = await _items(client, auth_headers, sort=col, station_id=str(st.id))
        down = await _items(client, auth_headers, sort=col, desc="true", station_id=str(st.id))
        assert _titles(up) == order, col
        assert _titles(down) == order[::-1], col


@pytest.mark.asyncio
async def test_sort_ties_go_by_title_then_id(client, auth_headers, db_session):
    a = _asset("Same", artist="X", vote=50)
    b = _asset("Same", artist="X", vote=50)
    c = _asset("Earlier", artist="X", vote=50)
    db_session.add_all([a, b, c])
    await db_session.commit()
    ids = sorted([str(a.id), str(b.id)])
    for desc in ("false", "true"):
        body = await _items(client, auth_headers, sort="vote", desc=desc)
        assert _titles(body) == ["Earlier", "Same", "Same"]
        assert [i["id"] for i in body["items"][1:]] == ids


# ── F-DATABANK-3 / F-DATABANK-27: last played per station, never-played last both ways ──

@pytest.mark.asyncio
async def test_last_played_per_station_and_never_played_last_both_ways(client, auth_headers, db_session):
    ny = Station(id=uuid.uuid4(), name="NY", timezone="America/New_York")
    jm = Station(id=uuid.uuid4(), name="JM", timezone="Asia/Jerusalem")
    old, new, never = _asset("Old"), _asset("New"), _asset("Never")
    db_session.add_all([ny, jm, old, new, never])
    await db_session.flush()
    t = datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc)
    db_session.add_all([
        PlayLog(id=uuid.uuid4(), station_id=ny.id, asset_id=old.id, start_utc=t - timedelta(days=400),
                source=PlaySource.SCHEDULER),
        PlayLog(id=uuid.uuid4(), station_id=ny.id, asset_id=old.id, start_utc=t - timedelta(days=2),
                source=PlaySource.SCHEDULER),
        PlayLog(id=uuid.uuid4(), station_id=ny.id, asset_id=new.id, start_utc=t, source=PlaySource.SCHEDULER),
        # On the other station "Old" played latest
        PlayLog(id=uuid.uuid4(), station_id=jm.id, asset_id=old.id, start_utc=t + timedelta(hours=1),
                source=PlaySource.SCHEDULER),
    ])
    await db_session.commit()

    up = await _items(client, auth_headers, sort="last_played", station_id=str(ny.id))
    down = await _items(client, auth_headers, sort="last_played", desc="true", station_id=str(ny.id))
    assert _titles(up) == ["Old", "New", "Never"]
    assert _titles(down) == ["New", "Old", "Never"]
    # the latest play on that station, not the first one (max over the whole log, no date limit)
    assert up["items"][0]["last_played"].startswith((t - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"))

    jm_rows = await _items(client, auth_headers, sort="last_played", desc="true", station_id=str(jm.id))
    assert _titles(jm_rows) == ["Old", "Never", "New"]  # unplayed on JM: last, by title
    assert jm_rows["items"][2]["last_played"] is None

    every = await _items(client, auth_headers, sort="last_played", desc="true")
    assert _titles(every) == ["Old", "New", "Never"]  # all stations: Old's JM play is the latest


# ── F-DATABANK-6: type filter, case-insensitive, combined with Find and Mpeg/Prsnt ──

@pytest.mark.asyncio
async def test_type_filter_case_insensitive_and_combined(client, auth_headers, db_session):
    db_session.add_all([
        _asset("Fast one", category="1a Old Hit Fast", artist="Avi"),
        _asset("Fast wav", category="1a old hit fast", artist="Avi", file_path="https://x/a/f.wav"),
        _asset("Fast other", category="1a old hit fast", artist="Moshe"),
        _asset("Slow one", category="slow", artist="Avi"),
        _asset("J fast", category="1a old hit fast", asset_type="jingle"),
    ])
    await db_session.commit()
    only = await _items(client, auth_headers, category="1A OLD HIT FAST", sort="title")
    assert _titles(only) == ["Fast one", "Fast other", "Fast wav"] and only["total"] == 3
    assert _titles(await _items(client, auth_headers, category="1a old hit fast", search="avi",
                                sort="title")) == ["Fast one", "Fast wav"]
    assert _titles(await _items(client, auth_headers, category="1a old hit fast", search="avi", files="mpeg",
                                sort="title")) == ["Fast one"]
    assert _titles(await _items(client, auth_headers, category="1a old hit fast", asset_type="jingle")) == ["J fast"]


# ── F-DATABANK-28: the type list covers the whole table, not the 200 rows shown ──

@pytest.mark.asyncio
async def test_type_list_covers_the_whole_table(client, auth_headers, db_session):
    for i in range(205):
        db_session.add(_asset(f"A{i:03d}", artist="A", category="common"))
    db_session.add(_asset("Zz last", artist="Zz", category="Rare type"))
    db_session.add(_asset("Zz lower", artist="Zz", category="rare TYPE"))
    db_session.add(_asset("Time Announcement - 10:00", category="generated"))
    db_session.add(_asset("Jingle", category="ids", asset_type="jingle"))
    await db_session.commit()
    page1 = await _items(client, auth_headers, sort="artist", limit=200)
    assert "Rare type" not in {i["type"] for i in page1["items"]}
    r = await client.get("/api/v1/databank/types?asset_type=music", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["types"] == ["common", "Rare type"]  # one spelling per type, generated clips left out
    jingles = (await client.get("/api/v1/databank/types?asset_type=jingle", headers=auth_headers)).json()
    assert jingles["types"] == ["ids"]


# ── F-DATABANK-57: Find treats % and _ as letters ──

@pytest.mark.asyncio
async def test_find_treats_percent_and_underscore_as_letters(client, auth_headers, db_session):
    db_session.add_all([
        _asset("100% Simcha"),
        _asset("100 Simcha"),
        _asset("my_song", file_path="https://x/a/my_song.mp3"),
        _asset("mysong", file_path="https://x/a/mysong.mp3"),
        _asset("Back\\slash"),
    ])
    await db_session.commit()
    assert _titles(await _items(client, auth_headers, search="100%")) == ["100% Simcha"]
    assert _titles(await _items(client, auth_headers, search="my_")) == ["my_song"]
    assert _titles(await _items(client, auth_headers, search="k\\s")) == ["Back\\slash"]
    assert _titles(await _items(client, auth_headers, search="%")) == ["100% Simcha"]


# ── F-DATABANK-13: roles on every databank read and the end-date write ──

@pytest.mark.asyncio
async def test_roles_on_every_databank_route(client, db_session):
    st = Station(id=uuid.uuid4(), name="Main", timezone="UTC")
    spot = _asset("Spot", asset_type="spot")
    sponsor = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, priority=1, audio_file_path="")
    db_session.add_all([st, spot, sponsor])
    await db_session.flush()
    db_session.add(SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=st.id,
                                start_date=date.today(), end_date=date.today() + timedelta(days=5)))
    await db_session.commit()
    reads = ["/api/v1/databank/items", f"/api/v1/databank/spots?station_id={st.id}",
             f"/api/v1/databank/log?station_id={st.id}", "/api/v1/databank/types"]
    write = f"/api/v1/databank/spots/{spot.id}/end-date?station_id={st.id}"
    body = {"end_date": (date.today() + timedelta(days=9)).isoformat()}
    for role in (UserRole.ADMIN, UserRole.MANAGER, UserRole.DJ, UserRole.VIEWER):
        h = await _login(client, db_session, role)
        for url in reads:
            assert (await client.get(url, headers=h)).status_code == 200, (role, url)
        status = (await client.patch(write, json=body, headers=h)).status_code
        assert status == (200 if role in (UserRole.ADMIN, UserRole.MANAGER) else 403), role
    sponsor_h = await _login(client, db_session, UserRole.SPONSOR)
    for url in reads:
        assert (await client.get(url, headers=sponsor_h)).status_code == 403, url
        assert (await client.get(url)).status_code == 401, url
    assert (await client.patch(write, json=body, headers=sponsor_h)).status_code == 403
    assert (await client.patch(write, json=body)).status_code == 401


# ── F-DATABANK-15: generated time and weather clips never listed or counted ──

@pytest.mark.asyncio
async def test_generated_clips_left_out_of_every_tab(client, auth_headers, db_session):
    db_session.add_all([
        _asset("Real song"),
        _asset("Time Announcement - 10:00"),
        _asset("Weather Report - 2026-11-17"),
        _asset("ID", asset_type="jingle"),
        _asset("Time Announcement - 11:00", asset_type="jingle"),
        _asset("Weather Report - morning", asset_type="jingle"),
        _asset("Bagel spot", asset_type="spot"),
        _asset("Weather Report - 2026-11-18", asset_type="spot"),
        _asset("Time Announcement - 12:00", asset_type="spot"),
    ])
    await db_session.commit()
    songs = await _items(client, auth_headers)
    assert _titles(songs) == ["Real song"] and songs["total"] == 1
    jingles = await _items(client, auth_headers, asset_type="jingle")
    assert _titles(jingles) == ["ID"] and jingles["total"] == 1
    spots = (await client.get("/api/v1/databank/spots", headers=auth_headers)).json()["spots"]
    assert [s["title"] for s in spots] == ["Bagel spot"]


# ── Spots: end dates, no end, (off), station-less schedules, expired ──

async def _spot_world(db):
    ny = Station(id=uuid.uuid4(), name="NY", timezone="America/New_York")
    jm = Station(id=uuid.uuid4(), name="JM", timezone="Asia/Jerusalem")
    sponsor = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, priority=1, audio_file_path="")
    db.add_all([ny, jm, sponsor])
    await db.flush()
    return ny, jm, sponsor


def _sched(sponsor, spot, station, start=None, end=None, active=True):
    return SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id,
                        station_id=station.id if station else None,
                        start_date=start, end_date=end, is_active=active)


@pytest.mark.asyncio
async def test_spots_end_date_no_end_off_and_station_less(client, auth_headers, db_session):
    """F-DATABANK-16"""
    ny, jm, sponsor = await _spot_world(db_session)
    today = date.today()
    later, latest = today + timedelta(days=10), today + timedelta(days=30)
    a = _asset("A latest of station and station-less", asset_type="spot", category="bagel", duration=23.0)
    b = _asset("B open ended", asset_type="spot")
    c = _asset("C paused", asset_type="spot")
    d = _asset("D other station only", asset_type="spot")
    db_session.add_all([a, b, c, d])
    await db_session.flush()
    db_session.add_all([
        _sched(sponsor, a, ny, end=later), _sched(sponsor, a, None, end=latest - timedelta(days=5)),
        _sched(sponsor, a, jm, end=latest),
        _sched(sponsor, b, ny, end=later), _sched(sponsor, b, None, end=None),
        _sched(sponsor, c, ny, end=later, active=False),
        _sched(sponsor, d, jm, end=later),
    ])
    await db_session.commit()
    rows = {s["title"][0]: s for s in (await client.get(
        f"/api/v1/databank/spots?station_id={ny.id}", headers=auth_headers)).json()["spots"]}
    assert rows["A"]["end_date"] == (latest - timedelta(days=5)).isoformat()  # JM's later end not counted
    assert rows["A"]["schedules"] == 2 and rows["A"]["active"] is True
    assert rows["A"]["category"] == "bagel" and rows["A"]["length"] == 23.0
    assert rows["B"]["open_ended"] is True and rows["B"]["end_date"] is None and rows["B"]["expired"] is False
    assert rows["C"]["schedules"] == 1 and rows["C"]["active"] is False  # page shows "1 (off)"
    assert rows["D"]["schedules"] == 0 and rows["D"]["end_date"] is None  # "not scheduled" here
    every = {s["title"][0]: s for s in (await client.get(
        "/api/v1/databank/spots", headers=auth_headers)).json()["spots"]}
    assert every["A"]["end_date"] == latest.isoformat() and every["A"]["schedules"] == 3
    assert every["D"]["schedules"] == 1


class _FrozenNow(datetime):
    """2026-11-17 02:00 UTC: already the 17th in Jerusalem, still the 16th in New York."""

    @classmethod
    def now(cls, tz=None):
        t = datetime(2026, 11, 17, 2, 0, tzinfo=UTC)
        return t.astimezone(tz) if tz else t.replace(tzinfo=None)


@pytest.mark.asyncio
async def test_expired_follows_the_station_today(client, auth_headers, db_session, monkeypatch):
    """F-DATABANK-41"""
    monkeypatch.setattr(databank, "datetime", _FrozenNow)
    ny, jm, sponsor = await _spot_world(db_session)
    ends16 = _asset("Ends on the 16th", asset_type="spot")
    mixed = _asset("Expired and open", asset_type="spot")
    db_session.add_all([ends16, mixed])
    await db_session.flush()
    db_session.add_all([
        _sched(sponsor, ends16, None, end=date(2026, 11, 16)),
        _sched(sponsor, mixed, None, end=date(2026, 1, 1)), _sched(sponsor, mixed, None, end=None),
    ])
    await db_session.commit()

    async def expired(station_id=None):
        url = "/api/v1/databank/spots" + (f"?station_id={station_id}" if station_id else "")
        body = (await client.get(url, headers=auth_headers)).json()
        return body["today"], {s["title"]: s["expired"] for s in body["spots"]}

    today, rows = await expired(jm.id)
    assert today == "2026-11-17" and rows["Ends on the 16th"] is True
    today, rows = await expired(ny.id)
    assert today == "2026-11-16" and rows["Ends on the 16th"] is False
    today, rows = await expired()
    assert today == "2026-11-17" and rows["Ends on the 16th"] is True  # UTC with no station
    assert rows["Expired and open"] is False


# ── End-date edit: station choice, station-less schedules, clearing, errors, queues ──

@pytest.mark.asyncio
async def test_end_date_with_a_station_moves_that_station_and_station_less(client, auth_headers, db_session,
                                                                           monkeypatch):
    """F-DATABANK-39: and every station's queue is refreshed for a station-less schedule"""
    ny, jm, sponsor = await _spot_world(db_session)
    spot = _asset("Spot", asset_type="spot")
    db_session.add(spot)
    await db_session.flush()
    on_ny, on_jm, everywhere = (_sched(sponsor, spot, ny, end=date.today()),
                                _sched(sponsor, spot, jm, end=date.today()),
                                _sched(sponsor, spot, None, end=date.today()))
    db_session.add_all([on_ny, on_jm, everywhere])
    await db_session.commit()
    refreshed = []
    import app.api.v1.spot_schedules as spot_schedules

    async def record(db, station_ids):
        refreshed.append(set(station_ids))

    monkeypatch.setattr(spot_schedules, "_refresh_queues", record)
    new_end = date.today() + timedelta(days=20)
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={ny.id}",
                           json={"end_date": new_end.isoformat()}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 2
    ends = {s.id: s.end_date for s in (await db_session.execute(
        select(SpotSchedule).execution_options(populate_existing=True))).scalars()}
    assert ends[on_ny.id] == new_end and ends[everywhere.id] == new_end
    assert ends[on_jm.id] == date.today()  # the other station's own schedule is left alone
    # None = a station-less schedule: _refresh_queues then refreshes every active station
    assert refreshed == [{ny.id, None}]


@pytest.mark.asyncio
async def test_clearing_the_end_date_makes_every_schedule_open_ended(client, auth_headers, db_session):
    """F-DATABANK-40"""
    ny, jm, sponsor = await _spot_world(db_session)
    spot = _asset("Spot", asset_type="spot")
    db_session.add(spot)
    await db_session.flush()
    db_session.add_all([_sched(sponsor, spot, ny, end=date.today()), _sched(sponsor, spot, None, end=date.today())])
    await db_session.commit()
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={ny.id}",
                           json={"end_date": None}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 2, "end_date": None}
    row = next(s for s in (await client.get(f"/api/v1/databank/spots?station_id={ny.id}",
                                            headers=auth_headers)).json()["spots"])
    assert row["open_ended"] is True and row["end_date"] is None and row["expired"] is False


@pytest.mark.asyncio
async def test_end_date_without_a_station_moves_every_station(client, db_session):
    """F-DATABANK-60: the API with no station_id moves the spot on every station, so it is
    refused (403, nothing changed) unless the manager may operate every one of them. The page
    itself always sends the chosen station (see e2e/databank-screen.mjs)."""
    ny, jm, sponsor = await _spot_world(db_session)
    spot = _asset("Spot", asset_type="spot")
    db_session.add(spot)
    await db_session.flush()
    s1, s2 = _sched(sponsor, spot, ny, end=date.today()), _sched(sponsor, spot, jm, end=date.today())
    db_session.add_all([s1, s2])
    await db_session.commit()
    new_end = (date.today() + timedelta(days=3)).isoformat()
    ny_only = await _login(client, db_session, UserRole.MANAGER, [str(ny.id)])
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date", json={"end_date": new_end}, headers=ny_only)
    assert r.status_code == 403
    ends = [s.end_date for s in (await db_session.execute(
        select(SpotSchedule).execution_options(populate_existing=True))).scalars()]
    assert ends == [date.today(), date.today()]
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={ny.id}",
                           json={"end_date": new_end}, headers=ny_only)
    assert r.status_code == 200 and r.json()["updated"] == 1
    both = await _login(client, db_session, UserRole.MANAGER, [str(ny.id), str(jm.id)])
    r = await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date", json={"end_date": new_end}, headers=both)
    assert r.status_code == 200 and r.json()["updated"] == 2


@pytest.mark.asyncio
async def test_end_date_errors_change_nothing(client, auth_headers, db_session):
    """F-DATABANK-20: 409 no schedule, 422 before a start, 403 another manager's station"""
    ny, jm, sponsor = await _spot_world(db_session)
    spot, loose = _asset("Spot", asset_type="spot"), _asset("Loose", asset_type="spot")
    db_session.add_all([spot, loose])
    await db_session.flush()
    start = date.today() + timedelta(days=5)
    early = _sched(sponsor, spot, ny, start=date.today(), end=date.today() + timedelta(days=30))
    late = _sched(sponsor, spot, ny, start=start, end=date.today() + timedelta(days=30))
    db_session.add_all([early, late])
    await db_session.commit()
    url = f"/api/v1/databank/spots/{spot.id}/end-date?station_id={ny.id}"

    r = await client.patch(f"/api/v1/databank/spots/{loose.id}/end-date", json={"end_date": None},
                           headers=auth_headers)
    assert r.status_code == 409 and r.json()["detail"].startswith("This spot has no schedule yet")
    r = await client.patch(url, json={"end_date": (start - timedelta(days=1)).isoformat()}, headers=auth_headers)
    assert r.status_code == 422 and "before the schedule starts" in r.json()["detail"]
    jm_only = await _login(client, db_session, UserRole.MANAGER, [str(jm.id)])
    r = await client.patch(url, json={"end_date": (start + timedelta(days=1)).isoformat()}, headers=jm_only)
    assert r.status_code == 403 and r.json()["detail"] == "You are not assigned to this station"
    ends = {s.id: s.end_date for s in (await db_session.execute(
        select(SpotSchedule).execution_options(populate_existing=True))).scalars()}
    assert ends == {early.id: date.today() + timedelta(days=30), late.id: date.today() + timedelta(days=30)}


async def _ad_slots(db, station_id):
    rows = await db.execute(select(QueueEntry).where(QueueEntry.station_id == station_id,
                                                     QueueEntry.source == "ad_slot",
                                                     QueueEntry.status == "pending")
                            .execution_options(populate_existing=True))
    return rows.scalars().all()


@pytest.mark.asyncio
async def test_end_date_changes_up_next_at_once(client, auth_headers, db_session):
    """F-DATABANK-19 and F-DATABANK-26: an end of today drops the later ad slots, a later
    end brings them back, each answered (queue refresh included) within 1 s"""
    station = Station(id=uuid.uuid4(), name="KBR", timezone="America/New_York", is_active=True)
    spot = _asset("Spot A", asset_type="spot", duration=30.0)
    db_session.add_all([station, spot])
    await db_session.commit()
    sponsor = (await client.post("/api/v1/sponsors", json={"name": "Acme", "length_seconds": 30.0,
                                                           "audio_file_path": ""}, headers=auth_headers)).json()
    r = await client.post("/api/v1/spot-schedules", json={"sponsor_id": sponsor["id"], "asset_id": str(spot.id),
                                                          "station_id": str(station.id)}, headers=auth_headers)
    assert r.status_code == 201, r.text
    full = len(await _ad_slots(db_session, station.id))
    assert full >= 18

    ny = ZoneInfo("America/New_York")
    today = datetime.now(ny).date()
    url = f"/api/v1/databank/spots/{spot.id}/end-date?station_id={station.id}"
    t0 = time.perf_counter()
    r = await client.patch(url, json={"end_date": today.isoformat()}, headers=auth_headers)
    took_cut = time.perf_counter() - t0
    assert r.status_code == 200, r.text
    left = await _ad_slots(db_session, station.id)
    assert len(left) <= 3
    for e in left:
        at = e.preempt_at if e.preempt_at.tzinfo else e.preempt_at.replace(tzinfo=UTC)
        assert at.astimezone(ny).date() == today

    t0 = time.perf_counter()
    r = await client.patch(url, json={"end_date": (today + timedelta(days=30)).isoformat()}, headers=auth_headers)
    took_extend = time.perf_counter() - t0
    assert r.status_code == 200
    assert len(await _ad_slots(db_session, station.id)) == full
    assert took_cut < SAVE_LIMIT_S and took_extend < SAVE_LIMIT_S, (took_cut, took_extend)


# ── Item edits: on air, last save wins, activity log ──

@pytest.mark.asyncio
async def test_item_edit_shows_in_databank_and_up_next_and_last_save_wins(client, auth_headers, db_session):
    """F-DATABANK-36, F-DATABANK-52 (server side): nothing caches the old title"""
    from tests.test_queue_advance import _station_with_two_tracks

    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    other = await _login(client, db_session, UserRole.MANAGER)
    t0 = time.perf_counter()
    r = await client.patch(f"/api/v1/assets/{pending.asset_id}", json={"title": "First edit", "category": "slow"},
                           headers=auth_headers)
    assert r.status_code == 200 and time.perf_counter() - t0 < SAVE_LIMIT_S  # F-DATABANK-26
    r = await client.patch(f"/api/v1/assets/{pending.asset_id}", json={"title": "Second edit", "vote": 77},
                           headers=other)
    assert r.status_code == 200
    row = next(i for i in (await _items(client, auth_headers, sort="title"))["items"]
               if i["id"] == str(pending.asset_id))
    assert (row["title"], row["type"], row["vote"]) == ("Second edit", "slow", 77)
    q = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    titles = {e["asset_id"]: e["asset"]["title"] for e in q["entries"]}
    assert titles[str(pending.asset_id)] == "Second edit"
    r = await client.patch(f"/api/v1/assets/{playing.asset_id}", json={"title": "On air renamed"},
                           headers=auth_headers)
    np_ = (await client.get(f"/api/v1/now-playing/{station.id}")).json()
    assert "On air renamed" in str(np_)


@pytest.mark.asyncio
async def test_databank_changes_are_in_the_activity_log(client, auth_headers, db_session):
    """F-DATABANK-53"""
    st = Station(id=uuid.uuid4(), name="Kol B'ramah", timezone="UTC")
    song = _asset("Song", vote=50)
    spot = _asset("Spot", asset_type="spot")
    sponsor = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, priority=1, audio_file_path="")
    db_session.add_all([st, song, spot, sponsor])
    await db_session.flush()
    db_session.add(_sched(sponsor, spot, st, end=date(2026, 12, 1)))
    await db_session.commit()
    assert (await client.patch(f"/api/v1/assets/{song.id}", json={"vote": 80},
                               headers=auth_headers)).status_code == 200
    assert (await client.patch(f"/api/v1/databank/spots/{spot.id}/end-date?station_id={st.id}",
                               json={"end_date": "2027-01-31"}, headers=auth_headers)).status_code == 200
    await activity_log.wait_idle()
    items = (await client.get("/api/v1/activity", headers=auth_headers)).json()["items"]

    def fields_of(model_path):
        row = next(r for r in items if r["path"].startswith(model_path))
        return {k: v for o in row["changes"]["objects"] for k, v in o["fields"].items()}

    vote = fields_of(f"/api/v1/assets/{song.id}")["vote"]
    assert (vote["old"], vote["new"]) == (50, 80)
    end = fields_of(f"/api/v1/databank/spots/{spot.id}/end-date")["end_date"]
    assert (str(end["old"]), str(end["new"])) == ("2026-12-01", "2027-01-31")


# ── Day log ──

@pytest.mark.asyncio
async def test_day_log_midnight_edges_and_clock_change_days(client, auth_headers, db_session):
    """F-DATABANK-23"""
    st = Station(id=uuid.uuid4(), name="NY", timezone="America/New_York")
    song = _asset("Song")
    db_session.add_all([st, song])
    await db_session.flush()
    ms = timedelta(milliseconds=1)
    utc = timezone.utc
    plays = {
        # ordinary day: 16 Nov 00:00 NY = 05:00 UTC; 17 Nov 00:00 NY = 05:00 UTC on the 17th
        "16-first": datetime(2026, 11, 16, 5, 0, tzinfo=utc),
        "16-last": datetime(2026, 11, 17, 5, 0, tzinfo=utc) - ms,
        "17-first": datetime(2026, 11, 17, 5, 0, tzinfo=utc),
        # 23-hour day: 8 Mar 2026 00:00 EST = 05:00 UTC; 9 Mar 00:00 EDT = 04:00 UTC
        "mar8-first": datetime(2026, 3, 8, 5, 0, tzinfo=utc),
        "mar8-last": datetime(2026, 3, 9, 4, 0, tzinfo=utc) - ms,
        "mar9-first": datetime(2026, 3, 9, 4, 0, tzinfo=utc),
        # 25-hour day: 1 Nov 2026 00:00 EDT = 04:00 UTC; 2 Nov 00:00 EST = 05:00 UTC
        "nov1-first": datetime(2026, 11, 1, 4, 0, tzinfo=utc),
        "nov1-repeated-1am": datetime(2026, 11, 1, 6, 30, tzinfo=utc),  # the second 01:30
        "nov1-last": datetime(2026, 11, 2, 5, 0, tzinfo=utc) - ms,
        "nov2-first": datetime(2026, 11, 2, 5, 0, tzinfo=utc),
        "oct31-last": datetime(2026, 11, 1, 4, 0, tzinfo=utc) - ms,
    }
    ids = {}
    for name, t in plays.items():
        log = PlayLog(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, start_utc=t, source=PlaySource.SCHEDULER)
        ids[str(log.id)] = name
        db_session.add(log)
    await db_session.commit()

    async def day(d):
        r = await client.get(f"/api/v1/databank/log?station_id={st.id}&day={d}", headers=auth_headers)
        assert r.status_code == 200, r.text
        return [ids[x["id"]] for x in r.json()["logs"]]

    assert await day("2026-11-16") == ["16-first", "16-last"]
    assert await day("2026-11-17") == ["17-first"]
    assert await day("2026-03-08") == ["mar8-first", "mar8-last"]
    assert await day("2026-03-09") == ["mar9-first"]
    assert await day("2026-11-01") == ["nov1-first", "nov1-repeated-1am", "nov1-last"]
    assert await day("2026-10-31") == ["oct31-last"]


@pytest.mark.asyncio
async def test_day_log_unknown_item_on_air_and_5000_cap(client, auth_headers, db_session):
    """F-DATABANK-44 (server side): a deleted item reads Unknown, an item on air has no end,
    at most 5,000 plays a day"""
    st = Station(id=uuid.uuid4(), name="Main", timezone="UTC")
    song = _asset("Song", artist="Avi")
    db_session.add_all([st, song])
    await db_session.flush()
    base = datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc)
    await db_session.execute(insert(PlayLog), [
        {"id": uuid.uuid4(), "station_id": st.id, "asset_id": song.id, "start_utc": base + timedelta(seconds=i * 10),
         "end_utc": base + timedelta(seconds=i * 10 + 9), "source": PlaySource.SCHEDULER}
        for i in range(5001)
    ])
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=st.id, asset_id=None, source=PlaySource.SCHEDULER,
                           start_utc=datetime(2026, 11, 17, 1, 0, tzinfo=timezone.utc)))
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=st.id, asset_id=song.id, source=PlaySource.SCHEDULER,
                           start_utc=datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)))
    await db_session.commit()
    big = (await client.get(f"/api/v1/databank/log?station_id={st.id}&day=2026-11-16", headers=auth_headers)).json()
    assert len(big["logs"]) == 5000
    assert big["logs"][0]["start_utc"].startswith("2026-11-16T00:00:00")  # oldest first, the cap cuts the end
    logs = (await client.get(f"/api/v1/databank/log?station_id={st.id}&day=2026-11-17",
                             headers=auth_headers)).json()["logs"]
    assert logs[0]["title"] == "Unknown" and logs[0]["artist"] is None and logs[0]["file"] == ""
    assert logs[1]["end_utc"] is None and logs[1]["artist"] == "Avi"


# ── F-DATABANK-46: API errors ──

@pytest.mark.asyncio
async def test_api_errors(client, auth_headers, db_session):
    st = Station(id=uuid.uuid4(), name="Main", timezone="UTC")
    db_session.add_all([st, _asset("B", artist="b"), _asset("A", artist="a")])
    await db_session.commit()
    nowhere = uuid.uuid4()
    get = lambda url: client.get(url, headers=auth_headers)  # noqa: E731
    assert (await get(f"/api/v1/databank/log?station_id={nowhere}")).status_code == 404
    assert (await get(f"/api/v1/databank/spots?station_id={nowhere}")).status_code == 404
    assert (await get(f"/api/v1/databank/log?station_id={st.id}&day=16-11-2026")).status_code == 422
    assert (await get(f"/api/v1/databank/log?station_id={st.id}&day=2026-13-01")).status_code == 422
    assert (await get("/api/v1/databank/items?limit=501")).status_code == 422
    assert (await get("/api/v1/databank/items?skip=-1")).status_code == 422
    assert (await get("/api/v1/databank/items?limit=0")).status_code == 422
    body = (await get("/api/v1/databank/items?sort=nonsense")).json()
    assert [i["artist"] for i in body["items"]] == ["a", "b"]


# ── F-DATABANK-25 / F-DATABANK-27: speed on the full library with years of play log ──

async def _median(make, n=3):
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        r = await make()
        times.append(time.perf_counter() - t0)
        assert r.status_code == 200, r.text
    return statistics.median(times)


@pytest.mark.asyncio
async def test_items_fast_on_full_library_with_years_of_play_log(client, auth_headers, db_session):
    stations = [Station(id=uuid.uuid4(), name=f"S{i}", timezone="UTC") for i in range(2)]
    db_session.add_all(stations)
    await db_session.flush()
    asset_ids = [uuid.uuid4() for _ in range(23_000)]
    await db_session.execute(insert(Asset), [
        {"id": a, "title": f"Song {i:05d}", "artist": f"Artist {i % 700}", "category": f"cat {i % 30}",
         "duration": 180.0 + i % 120, "vote": i % 100, "file_path": f"https://x/a/{i}.mp3", "asset_type": "music"}
        for i, a in enumerate(asset_ids)
    ])
    # about three years of plays: 2 stations x 60,000 plays
    base = datetime(2023, 11, 17, tzinfo=timezone.utc)
    for st in stations:
        await db_session.execute(insert(PlayLog), [
            {"id": uuid.uuid4(), "station_id": st.id, "asset_id": asset_ids[(i * 7919) % len(asset_ids)],
             "start_utc": base + timedelta(minutes=13 * i), "source": PlaySource.SCHEDULER}
            for i in range(60_000)
        ])
    await db_session.commit()
    sid = str(stations[0].id)
    calls = {
        "first page": f"/api/v1/databank/items?station_id={sid}",
        "page 50": f"/api/v1/databank/items?station_id={sid}&skip=10000",
        "last played newest": f"/api/v1/databank/items?station_id={sid}&sort=last_played&desc=true",
        "last played all stations": "/api/v1/databank/items?sort=last_played&desc=true",
        "find": f"/api/v1/databank/items?station_id={sid}&search=Artist%2012",
        "type filter": f"/api/v1/databank/items?station_id={sid}&category=cat%207&sort=vote&desc=true",
    }
    slow = {}
    for name, url in calls.items():
        took = await _median(lambda url=url: client.get(url, headers=auth_headers))
        if took >= LIMIT_S:
            slow[name] = round(took * 1000)
    assert not slow, f"over {LIMIT_S * 1000:.0f} ms (median ms): {slow}"
