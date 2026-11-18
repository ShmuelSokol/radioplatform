"""Nothing that belongs to one station shows up when another station is asked about.

Shmuel, 2026-11-17: moving a song in Main's queue showed a warning from a rule made
for All Day Stories. The move itself was tested; that a thing set for one station stays
off the others was not. This sweep makes station B one of everything a station can own,
each named with a marker, then calls EVERY read in the API that takes a station (found
from the API's own description, so a new endpoint is covered the day it is added) for
station A and fails if B's marker or id comes back. It then runs the operator's queue
actions on A and checks nothing of B is mentioned in the answers and nothing of B changed.
"""
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.alert import Alert
from app.models.asset import Asset
from app.models.audit_log import AuditLog
from app.models.channel_stream import ChannelStream
from app.models.holiday_window import HolidayWindow
from app.models.live_show import LiveShow
from app.models.play_log import PlayLog
from app.models.playlist_template import PlaylistTemplate
from app.models.program_block import ProgramBlock
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import ScheduleBlock
from app.models.schedule_rule import ScheduleRule
from app.models.show_archive import ShowArchive
from app.models.song_request import SongRequest
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.models.weather_readout import WeatherReadout
from tests.conftest import _app

B = "ZZB"   # everything station B owns carries this
A = "QQA"   # and station A's carries this

# Reads that take a station but are about the whole library or the whole site, where
# another station's things are meant to be visible. Each needs a reason.
SHARED_ON_PURPOSE = {
    "/api/v1/databank/items": "the library is shared by all stations",
    "/api/v1/databank/spots": "sponsorship recordings are library items",
    "/api/v1/playlists/generation": "a dry run that picks songs from the shared library",
}
# Reads that need more than a station to be called
NEEDS_MORE = {
    "/api/v1/playout/{station_id}/next": "engine only, guarded by the playout token",
    "/api/v1/weather-readouts/template-preview": "needs a template text",
    "/api/v1/assets/{asset_id}/station-block": "needs a song; answers only for the station asked (tests/test_station_block.py)",
    "/api/v1/sponsors/{sponsor_id}/plays": "needs a client; the station is only a filter of its plays (tests/test_sponsor_accounts.py)",
    "/api/v1/spot-schedules/for-asset/{asset_id}": "needs a spot; other stations' contracts only for staff on every station (tests/test_sponsor_panel.py)",
}


async def _one_of_everything(db, station, tag, n):
    """One of each thing a station can own, named with `tag`."""
    now = datetime.now(UTC)
    song = Asset(id=uuid.uuid4(), title=f"{tag} queued song", artist=f"{tag} artist", duration=180.0,
                 file_path=f"https://cdn.example.com/{tag}.mp3", asset_type="music")
    played = Asset(id=uuid.uuid4(), title=f"{tag} played song", duration=180.0,
                   file_path=f"https://cdn.example.com/{tag}p.mp3", asset_type="music")
    spot_asset = Asset(id=uuid.uuid4(), title=f"{tag} sponsorship", duration=30.0,
                       file_path=f"https://cdn.example.com/{tag}s.mp3", asset_type="spot")
    db.add_all([song, played, spot_asset])
    sponsor = Sponsor(id=uuid.uuid4(), name=f"{tag} sponsor", length_seconds=30.0,
                      audio_file_path=f"https://cdn.example.com/{tag}s.mp3")
    db.add(sponsor)
    await db.flush()
    sched = Schedule(id=uuid.uuid4(), station_id=station.id, name=f"{tag} schedule", is_active=True)
    db.add(sched)
    await db.flush()
    from datetime import time as _t
    db.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=played.id, position=1, status="playing",
                   source="auto", started_at=now - timedelta(seconds=20)),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=song.id, position=2, status="pending", source="auto"),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=played.id, position=3, status="pending", source="auto"),
        PlayLog(station_id=station.id, asset_id=played.id, start_utc=now - timedelta(hours=1),
                end_utc=now - timedelta(minutes=57)),
        ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name=f"{tag} block", start_time=_t(0), end_time=_t(23, 59)),
        # Station B is a stories station: its all-day rule wants stories, which no song on
        # station A matches (the rule that warned about a move on Main, 2026-11-17)
        ScheduleRule(id=uuid.uuid4(), station_id=station.id, name=f"{tag} rule", rule_type="daypart",
                     asset_type="story" if tag == B else "music", is_active=True, hour_start=0, hour_end=24),
        ProgramBlock(id=uuid.uuid4(), station_id=station.id, name=f"{tag} program block", times="10:00"),
        SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot_asset.id, station_id=station.id,
                     name=f"{tag} spot schedule"),
        PlaylistTemplate(id=uuid.uuid4(), station_id=station.id, name=f"{tag} playlist"),
        HolidayWindow(id=uuid.uuid4(), name=f"{tag} blackout", start_datetime=now + timedelta(days=2),
                      end_datetime=now + timedelta(days=3), is_blackout=True,
                      affected_stations={"station_ids": [str(station.id)]}, reason=f"{tag} reason"),
        Alert(id=uuid.uuid4(), station_id=station.id, alert_type="system", severity="warning",
              title=f"{tag} alert", message=f"{tag} alert text"),
        LiveShow(id=uuid.uuid4(), station_id=station.id, title=f"{tag} live show"),
        SongRequest(id=uuid.uuid4(), station_id=station.id, requester_name=f"{tag} listener", song_title=f"{tag} request"),
        ShowArchive(id=uuid.uuid4(), station_id=station.id, title=f"{tag} archive",
                    audio_url=f"https://cdn.example.com/{tag}a.mp3", is_published=True),
        ChannelStream(id=uuid.uuid4(), station_id=station.id, channel_name=f"{tag} channel"),
        WeatherReadout(id=uuid.uuid4(), station_id=station.id, readout_date=date.today(), script_text=f"{tag} weather text"),
        AuditLog(id=uuid.uuid4(), station_id=station.id, action="update", resource_type="queue",
                 label=f"{tag} did something", user_email=f"{tag.lower()}@example.test"),
    ])
    await db.commit()


async def _two_stations(db):
    a = Station(id=uuid.uuid4(), name=f"{A} station", timezone="America/New_York")
    b = Station(id=uuid.uuid4(), name=f"{B} station", timezone="Asia/Jerusalem")
    db.add_all([a, b])
    await db.commit()
    await _one_of_everything(db, a, A, 1)
    await _one_of_everything(db, b, B, 2)
    return a, b


def _station_reads():
    """Every GET in the API that takes a station id, from the API's own description."""
    out = []
    for path, ops in sorted(_app().openapi()["paths"].items()):
        op = ops.get("get")
        if not op:
            continue
        params = {p["name"]: p for p in op.get("parameters", [])}
        if "station_id" not in params:
            continue
        required = [p["name"] for p in params.values()
                    if p.get("required") and p["name"] != "station_id" and p["in"] != "header"]
        out.append((path, params["station_id"]["in"], required))
    return out


def test_the_sweep_sees_the_station_reads():
    reads = _station_reads()
    assert len(reads) >= 45, f"only {len(reads)} station reads found; the sweep lost its list"
    assert any(p.endswith("/stations/{station_id}/queue") for p, _, _ in reads)


@pytest.mark.asyncio
async def test_no_read_for_one_station_returns_anything_of_another(client, auth_headers, db_session):
    a, b = await _two_stations(db_session)
    leaks, errors, called = [], [], 0
    for path, where, required in _station_reads():
        if path in NEEDS_MORE or path in SHARED_ON_PURPOSE:
            continue
        if required:
            errors.append(f"{path}: needs {required}; add it to NEEDS_MORE with a reason or give the sweep a value")
            continue
        url = path.replace("{station_id}", str(a.id))
        params = {"station_id": str(a.id)} if where == "query" else None
        res = await client.get(url, params=params, headers=auth_headers)
        called += 1
        if res.status_code >= 500:
            errors.append(f"{path}: HTTP {res.status_code} {res.text[:160]}")
            continue
        text = res.text
        if B in text or str(b.id) in text:
            at = text.find(B) if B in text else text.find(str(b.id))
            leaks.append(f"{path}: ...{text[max(0, at - 60):at + 60]}...")
    assert called >= 40
    assert not errors, "reads that failed outright:\n" + "\n".join(errors)
    assert not leaks, "station A's reads returned station B's things:\n" + "\n".join(leaks)


@pytest.mark.asyncio
async def test_the_shared_reads_are_shared_for_a_reason(client, auth_headers, db_session):
    """The exceptions stay honest: each still exists and still answers."""
    a, _ = await _two_stations(db_session)
    known = {p for p, _, _ in _station_reads()}
    for path, reason in SHARED_ON_PURPOSE.items():
        assert path in known and reason
        res = await client.get(path, params={"station_id": str(a.id)}, headers=auth_headers)
        assert res.status_code == 200
        # Library songs may show, the other station's own things (its schedule, blocks,
        # rules, alerts ...) may not
        for own in ("schedule", "block", "rule", "playlist", "blackout", "alert", "live show", "request",
                    "archive", "channel", "weather text", "station"):
            assert f"{B} {own}" not in res.text, f"{path} shows station B's {own}"


def _snapshot(rows):
    return sorted((str(r.id), r.position, r.status, str(r.asset_id)) for r in rows)


@pytest.mark.asyncio
async def test_queue_actions_on_one_station_leave_the_other_alone(client, auth_headers, db_session, monkeypatch):
    """Every operator action on A: the answer names nothing of B, and B's queue, rules and
    alerts are exactly as they were."""
    import app.api.v1.queue as queue_module

    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    a, b = await _two_stations(db_session)

    async def b_state():
        q = (await db_session.execute(select(QueueEntry).where(QueueEntry.station_id == b.id)
                                      .execution_options(populate_existing=True))).scalars().all()
        alerts = (await db_session.execute(select(Alert).where(Alert.station_id == b.id))).scalars().all()
        return _snapshot(q), len(alerts)

    before = await b_state()
    a_rows = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == a.id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position))).scalars().all()
    first, second = a_rows[0], a_rows[1]
    a_song = first.asset_id
    base = f"/api/v1/stations/{a.id}/queue"
    actions = [
        ("move-up", "post", f"{base}/move-up", {"entry_id": str(second.id), "new_position": 0}),
        ("move-down", "post", f"{base}/move-down", {"entry_id": str(second.id), "new_position": 0}),
        ("reorder-dnd", "post", f"{base}/reorder-dnd", {"entry_id": str(second.id), "new_position": 2}),
        ("reorder-dnd back", "post", f"{base}/reorder-dnd", {"entry_id": str(second.id), "new_position": 3}),
        ("play-next", "post", f"{base}/play-next", {"asset_id": str(a_song)}),
        ("add", "post", f"{base}/add", {"asset_id": str(a_song)}),
        ("skip", "post", f"{base}/skip", None),
        ("queue", "get", base, None),
    ]
    said, failed = [], []
    for name, method, url, body in actions:
        res = await getattr(client, method)(url, headers=auth_headers, **({"json": body} if body is not None else {}))
        if res.status_code >= 400:
            failed.append(f"{name}: HTTP {res.status_code} {res.text[:200]}")
        if B in res.text or str(b.id) in res.text:
            at = res.text.find(B)
            said.append(f"{name}: ...{res.text[max(0, at - 80):at + 80]}...")
        try:
            warnings = res.json().get("warnings") if res.headers.get("content-type", "").startswith("application/json") else None
        except Exception:
            warnings = None
        if warnings:
            said.append(f"{name}: unexpected warnings {warnings}")
    assert not failed, "\n".join(failed)
    assert not said, "an action on station A mentioned station B, or warned without cause:\n" + "\n".join(said)
    assert await b_state() == before, "an action on station A changed station B"


@pytest.mark.asyncio
async def test_not_on_this_station_stays_on_that_station(client, auth_headers, db_session, monkeypatch):
    """"Not on this station" pressed in station B's control room (PR 257): the song leaves
    B's waiting queue only. Station A's copy stays, A does not report it blocked, and A
    can still queue it; allowing it again on B changes nothing on A either."""
    import app.api.v1.queue as queue_module

    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    a, b = await _two_stations(db_session)
    song = Asset(id=uuid.uuid4(), title="shared song", duration=180.0,
                 file_path="https://cdn.example.com/shared.mp3", asset_type="music")
    db_session.add(song)
    db_session.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=a.id, asset_id=song.id, position=9, status="pending", source="auto"),
        QueueEntry(id=uuid.uuid4(), station_id=b.id, asset_id=song.id, position=9, status="pending", source="auto"),
    ])
    await db_session.commit()

    async def waiting(station):
        rows = (await db_session.execute(
            select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.asset_id == song.id,
                                     QueueEntry.status == "pending").execution_options(populate_existing=True))).scalars().all()
        return len(rows)

    res = await client.post(f"/api/v1/assets/{song.id}/station-block", headers=auth_headers,
                            json={"station_id": str(b.id), "on": True})
    assert res.status_code == 200, res.text
    assert await waiting(b) == 0, "the blocked station still has the song waiting"
    assert await waiting(a) == 1, "blocking a song on one station took it out of another station's queue"
    for station, want in ((a, False), (b, True)):
        got = await client.get(f"/api/v1/assets/{song.id}/station-block", params={"station_id": str(station.id)},
                               headers=auth_headers)
        assert got.status_code == 200 and got.json()["blocked"] is want, got.text
    added = await client.post(f"/api/v1/stations/{a.id}/queue/add", headers=auth_headers, json={"asset_id": str(song.id)})
    assert added.status_code < 400, added.text
    assert await waiting(a) == 2
    # the library still shows it as an ordinary song: nothing global changed
    await db_session.refresh(song)
    assert (song.category or "") != "do_not_play"
    res = await client.post(f"/api/v1/assets/{song.id}/station-block", headers=auth_headers,
                            json={"station_id": str(b.id), "on": False})
    assert res.status_code == 200 and await waiting(a) == 2
