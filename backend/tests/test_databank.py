"""Databank: the KBR Studio style tables (songs, spots with end dates, the day log)."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.models.asset import Asset
from app.models.play_log import PlayLog, PlaySource
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station


def _asset(title, **kw):
    return Asset(id=uuid.uuid4(), title=title, duration=kw.pop("duration", 200.0),
                 file_path=kw.pop("file_path", f"https://cdn.example.com/audio/{title}.mp3?x=1"),
                 asset_type=kw.pop("asset_type", "music"), **kw)


@pytest.mark.asyncio
async def test_songs_sort_by_any_column_with_last_played(client, auth_headers, db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    a = _asset("Yehay Shelomo Rabah", artist="Chevra", category="1a old hit fast", vote=80)
    b = _asset("Gadol Hashem", artist="Shalsheles", category="1 oldie", vote=65)
    c = _asset("Venacha", artist="Abie", category="1 nostalgic hit", vote=75)
    jingle = _asset("10 am", asset_type="jingle")
    gen = _asset("Time Announcement - 15:00")
    db_session.add_all([station, a, b, c, jingle, gen])
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=b.id,
                           start_utc=datetime(2026, 11, 16, 15, 0, tzinfo=timezone.utc),
                           source=PlaySource.SCHEDULER))
    await db_session.commit()

    r = await client.get("/api/v1/databank/items?sort=artist", headers=auth_headers)
    body = r.json()
    assert r.status_code == 200, r.text
    assert body["total"] == 3
    assert [i["artist"] for i in body["items"]] == ["Abie", "Chevra", "Shalsheles"]
    assert body["items"][0]["file"] == "Venacha.mp3"

    by_vote = (await client.get("/api/v1/databank/items?sort=vote&desc=true", headers=auth_headers)).json()
    assert [i["vote"] for i in by_vote["items"]] == [80, 75, 65]

    played = (await client.get(f"/api/v1/databank/items?sort=last_played&desc=true&station_id={station.id}",
                               headers=auth_headers)).json()
    assert played["items"][0]["title"] == "Gadol Hashem"
    assert played["items"][0]["last_played"].startswith("2026-11-16T15:00")
    assert played["items"][1]["last_played"] is None

    found = (await client.get("/api/v1/databank/items?search=oldie", headers=auth_headers)).json()
    assert [i["title"] for i in found["items"]] == ["Gadol Hashem"]

    jingles = (await client.get("/api/v1/databank/items?asset_type=jingle", headers=auth_headers)).json()
    assert [i["title"] for i in jingles["items"]] == ["10 am"]


@pytest.mark.asyncio
async def test_spots_show_end_date_and_moving_it_moves_the_schedules(client, auth_headers, db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    sponsor = Sponsor(id=uuid.uuid4(), name="Bagel King", length_seconds=30, priority=1, audio_file_path="")
    spot = _asset("Bagel King Breakfast", asset_type="spot", category="bagel", duration=23.0)
    old = _asset("Fig Flip Pro", asset_type="spot", category="phone", duration=34.0)
    loose = _asset("Unscheduled spot", asset_type="spot")
    db_session.add_all([station, sponsor, spot, old, loose])
    await db_session.flush()
    today = date.today()
    db_session.add_all([
        SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=station.id,
                     start_date=today - timedelta(days=10), end_date=today + timedelta(days=20)),
        SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=old.id, station_id=station.id,
                     start_date=today - timedelta(days=30), end_date=today - timedelta(days=5)),
    ])
    await db_session.commit()

    r = await client.get(f"/api/v1/databank/spots?station_id={station.id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    rows = {s["title"]: s for s in r.json()["spots"]}
    assert rows["Bagel King Breakfast"]["end_date"] == (today + timedelta(days=20)).isoformat()
    assert rows["Bagel King Breakfast"]["expired"] is False
    assert rows["Fig Flip Pro"]["expired"] is True
    assert rows["Unscheduled spot"]["schedules"] == 0

    new_end = (today + timedelta(days=40)).isoformat()
    r = await client.patch(f"/api/v1/databank/spots/{old.id}/end-date?station_id={station.id}",
                           json={"end_date": new_end}, headers=auth_headers)
    assert r.status_code == 200, r.text
    rows = {s["title"]: s for s in (await client.get(
        f"/api/v1/databank/spots?station_id={station.id}", headers=auth_headers)).json()["spots"]}
    assert rows["Fig Flip Pro"]["end_date"] == new_end
    assert rows["Fig Flip Pro"]["expired"] is False

    r = await client.patch(f"/api/v1/databank/spots/{loose.id}/end-date",
                           json={"end_date": new_end}, headers=auth_headers)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_day_log_is_one_station_day_oldest_first(client, auth_headers, db_session):
    station = Station(id=uuid.uuid4(), name="Main", timezone="America/New_York")
    song = _asset("Glatstein R. Daniel - VAYEITZEI")
    db_session.add_all([station, song])
    await db_session.flush()
    # 22:09 New York on Nov 16 is 03:09 UTC on Nov 17; 23:30 UTC Nov 15 is still Nov 15 in New York
    times = [
        datetime(2026, 11, 17, 3, 9, 31, 672000, tzinfo=timezone.utc),
        datetime(2026, 11, 16, 6, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 15, 23, 30, tzinfo=timezone.utc),
    ]
    for t in times:
        db_session.add(PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, start_utc=t,
                               end_utc=t + timedelta(minutes=3), source=PlaySource.SCHEDULER))
    await db_session.commit()

    r = await client.get(f"/api/v1/databank/log?station_id={station.id}&day=2026-11-16", headers=auth_headers)
    assert r.status_code == 200, r.text
    logs = r.json()["logs"]
    assert len(logs) == 2
    assert logs[0]["start_utc"].startswith("2026-11-16T06:00")
    assert logs[1]["start_utc"].startswith("2026-11-17T03:09:31.672")
    assert logs[1]["file"] == "Glatstein R. Daniel - VAYEITZEI.mp3"


@pytest.mark.asyncio
async def test_databank_is_staff_only(client):
    r = await client.get("/api/v1/databank/items")
    assert r.status_code in (401, 403)
