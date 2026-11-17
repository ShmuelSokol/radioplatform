"""Queue endpoints the control room uses besides the instant buttons: reads (queue, play
history, last played), add / bulk add / play next, move up / down / reorder /
drag-and-drop, Start, and the manager-only weather and replenish triggers.

Each action is also timed: the server must answer well inside the 300 ms limit of
checklist row S-3 (generous here, the test database is SQLite on a busy machine)."""
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.category import Category
from app.models.holiday_window import HolidayWindow
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.schedule_rule import ScheduleRule
from app.models.station import Station
from app.models.user import UserRole
from tests.test_station_permissions import _user

ACTION_LIMIT_S = 0.3


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    queue_module._fire_requests.clear()


async def _timed(coro):
    started = time.perf_counter()
    res = await coro
    took = time.perf_counter() - started
    assert took < ACTION_LIMIT_S, f"took {took * 1000:.0f} ms"
    return res


async def _station_with_queue(db, n_pending=4, playing=True, name="Queue FM", **station_kw):
    station = Station(id=uuid.uuid4(), name=name, timezone="America/New_York", **station_kw)
    db.add(station)
    assets = []
    for i in range(n_pending + 1):
        a = Asset(id=uuid.uuid4(), title=f"Song {i}", duration=180.0,
                  file_path=f"https://cdn.example.com/{i}.mp3", asset_type="music")
        db.add(a)
        assets.append(a)
    await db.flush()
    entries = []
    start = 0
    if playing:
        entries.append(QueueEntry(
            id=uuid.uuid4(), station_id=station.id, asset_id=assets[0].id, position=1,
            status="playing", source="auto", started_at=datetime.now(UTC) - timedelta(seconds=20),
        ))
        start = 1
    for i in range(start, n_pending + 1):
        entries.append(QueueEntry(
            id=uuid.uuid4(), station_id=station.id, asset_id=assets[i].id, position=i + 1,
            status="pending", source="auto",
        ))
    db.add_all(entries)
    await db.commit()
    return station, assets, entries


async def _pending_ids(db, station_id):
    rows = (await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position).execution_options(populate_existing=True)
    )).scalars().all()
    return [str(r.id) for r in rows]


def _url(station, path=""):
    return f"/api/v1/stations/{station.id}/queue{path}"


# ── Reads ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_viewer_may_read_the_queue_and_a_sponsor_may_not(client, db_session):
    """B-QUEUE-5"""
    station, _, _ = await _station_with_queue(db_session)
    _, viewer = await _user(client, db_session, UserRole.VIEWER)
    _, sponsor = await _user(client, db_session, UserRole.SPONSOR)
    for path in ("", "/log", "/last-played"):
        assert (await client.get(_url(station, path), headers=viewer)).status_code == 200
        assert (await client.get(_url(station, path), headers=sponsor)).status_code == 403
        assert (await client.get(_url(station, path))).status_code in (401, 403)
    # A viewer cannot change anything
    res = await client.post(_url(station, "/skip"), headers=viewer)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_play_log_is_newest_first_and_honours_limit(client, auth_headers, db_session):
    """B-QUEUE-6, B-QUEUE-7"""
    station, assets, _ = await _station_with_queue(db_session)
    now = datetime.now(UTC)
    for i in range(5):
        db_session.add(PlayLog(
            id=uuid.uuid4(), station_id=station.id, asset_id=assets[i].id,
            start_utc=now - timedelta(minutes=10 * (5 - i)),
            end_utc=now - timedelta(minutes=10 * (5 - i)) + timedelta(minutes=3), source="scheduler",
        ))
    other, *_ = await _station_with_queue(db_session, name="Other FM")
    db_session.add(PlayLog(id=uuid.uuid4(), station_id=other.id, asset_id=assets[0].id,
                           start_utc=now, source="scheduler"))
    await db_session.commit()

    body = (await _timed(client.get(_url(station, "/log"), headers=auth_headers))).json()
    assert body["total"] == 5
    starts = [row["start_utc"] for row in body["logs"]]
    assert starts == sorted(starts, reverse=True)
    assert body["logs"][0]["title"] == "Song 4"
    assert body["logs"][0]["source"] == "scheduler"

    body = (await client.get(_url(station, "/log?limit=2"), headers=auth_headers)).json()
    assert [r["title"] for r in body["logs"]] == ["Song 4", "Song 3"]
    assert (await client.get(_url(station, "/log?limit=0"), headers=auth_headers)).status_code == 422
    assert (await client.get(_url(station, "/log?limit=201"), headers=auth_headers)).status_code == 422


@pytest.mark.asyncio
async def test_last_played_gives_the_latest_play_per_item(client, auth_headers, db_session):
    """B-QUEUE-8"""
    station, assets, _ = await _station_with_queue(db_session)
    now = datetime.now(UTC).replace(microsecond=0)
    db_session.add_all([
        PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=assets[1].id,
                start_utc=now - timedelta(hours=3), source="scheduler"),
        PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=assets[1].id,
                start_utc=now - timedelta(hours=1), source="scheduler"),
        PlayLog(id=uuid.uuid4(), station_id=station.id, asset_id=assets[2].id,
                start_utc=now - timedelta(hours=2), source="manual"),
    ])
    await db_session.commit()
    body = (await _timed(client.get(_url(station, "/last-played"), headers=auth_headers))).json()
    got = {k: datetime.fromisoformat(v).replace(tzinfo=UTC) for k, v in body["last_played"].items()}
    assert set(got) == {str(assets[1].id), str(assets[2].id)}
    assert got[str(assets[1].id)] == now - timedelta(hours=1)
    assert got[str(assets[2].id)] == now - timedelta(hours=2)


# ── Add, bulk add, play next ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_appends_at_the_end_as_manual(client, auth_headers, db_session):
    """B-QUEUE-10, B-QUEUE-11"""
    station, assets, entries = await _station_with_queue(db_session)
    res = await _timed(client.post(_url(station, "/add"), headers=auth_headers,
                                   json={"asset_id": str(assets[0].id)}))
    assert res.status_code == 201
    body = res.json()
    assert body["position"] == len(entries) + 1 and body["status"] == "pending"
    pending = await _pending_ids(db_session, station.id)
    assert pending[-1] == body["id"]
    row = await db_session.get(QueueEntry, uuid.UUID(body["id"]), populate_existing=True)
    assert row.source == "manual"
    # Bad body
    assert (await client.post(_url(station, "/add"), headers=auth_headers, json={})).status_code == 422


@pytest.mark.asyncio
async def test_unknown_asset_is_refused_with_404(client, auth_headers, db_session):
    """B-QUEUE-11: unknown asset -> 404, nothing queued (was a 500 from the foreign key;
    fire-now goes through play-next and gets the same answer)"""
    station, assets, entries = await _station_with_queue(db_session)
    before = await _pending_ids(db_session, station.id)
    ghost = str(uuid.uuid4())
    for path, body in (
        ("/add", {"asset_id": ghost}),
        ("/play-next", {"asset_id": ghost}),
        ("/fire-now", {"asset_id": ghost}),
        ("/bulk-add", {"asset_ids": [str(assets[1].id), ghost]}),
    ):
        res = await client.post(_url(station, path), headers=auth_headers, json=body)
        assert res.status_code == 404, (path, res.status_code)
    assert await _pending_ids(db_session, station.id) == before


@pytest.mark.asyncio
async def test_bulk_add_appends_all_in_order(client, auth_headers, db_session):
    """B-QUEUE-12"""
    station, assets, entries = await _station_with_queue(db_session)
    before = await _pending_ids(db_session, station.id)
    ids = [str(assets[3].id), str(assets[1].id), str(assets[2].id)]
    res = await _timed(client.post(_url(station, "/bulk-add"), headers=auth_headers,
                                   json={"asset_ids": ids}))
    assert res.status_code == 201 and res.json()["count"] == 3
    rows = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "pending")
        .order_by(QueueEntry.position).execution_options(populate_existing=True)
    )).scalars().all()
    assert [str(r.id) for r in rows[: len(before)]] == before
    assert [str(r.asset_id) for r in rows[len(before):]] == ids
    assert all(r.source == "manual" for r in rows[len(before):])
    assert len({r.position for r in rows}) == len(rows)


@pytest.mark.asyncio
async def test_play_next_goes_right_after_the_item_on_air(client, auth_headers, db_session):
    """B-QUEUE-13, B-QUEUE-14"""
    station, assets, entries = await _station_with_queue(db_session)
    before = await _pending_ids(db_session, station.id)
    res = await _timed(client.post(_url(station, "/play-next"), headers=auth_headers,
                                   json={"asset_id": str(assets[4].id)}))
    assert res.status_code == 201
    body = res.json()
    assert body["position"] == entries[0].position + 1
    assert await _pending_ids(db_session, station.id) == [body["id"], *before]
    # The GET /queue next line agrees
    q = (await client.get(_url(station), headers=auth_headers)).json()
    assert q["next_entry_id"] == body["id"]


@pytest.mark.asyncio
async def test_play_next_on_an_empty_queue_gets_position_one(client, auth_headers, db_session):
    """B-QUEUE-14: empty queue -> position 1"""
    station = Station(id=uuid.uuid4(), name="Empty FM", timezone="UTC")
    asset = Asset(id=uuid.uuid4(), title="Only", duration=60.0,
                  file_path="https://cdn.example.com/o.mp3", asset_type="music")
    db_session.add_all([station, asset])
    await db_session.commit()
    res = await client.post(_url(station, "/play-next"), headers=auth_headers,
                            json={"asset_id": str(asset.id)})
    assert res.status_code == 201 and res.json()["position"] == 1


# ── Move up / down / reorder / drag-and-drop ──────────────────────────────


@pytest.mark.asyncio
async def test_move_up_and_down_swap_with_neighbours(client, auth_headers, db_session):
    """B-QUEUE-42..45"""
    station, assets, entries = await _station_with_queue(db_session)
    order = await _pending_ids(db_session, station.id)
    a, b, c, d = order

    res = await _timed(client.post(_url(station, "/move-up"), headers=auth_headers,
                                   json={"entry_id": c, "new_position": 0}))
    assert res.status_code == 200
    assert await _pending_ids(db_session, station.id) == [a, c, b, d]

    # Top entry stays where it is
    await client.post(_url(station, "/move-up"), headers=auth_headers,
                      json={"entry_id": a, "new_position": 0})
    assert await _pending_ids(db_session, station.id) == [a, c, b, d]

    res = await _timed(client.post(_url(station, "/move-down"), headers=auth_headers,
                                   json={"entry_id": a, "new_position": 0}))
    assert res.status_code == 200
    assert await _pending_ids(db_session, station.id) == [c, a, b, d]

    # Last entry stays where it is
    await client.post(_url(station, "/move-down"), headers=auth_headers,
                      json={"entry_id": d, "new_position": 0})
    assert await _pending_ids(db_session, station.id) == [c, a, b, d]

    # The item on air cannot be moved
    playing = str(entries[0].id)
    for path in ("/move-up", "/move-down"):
        res = await client.post(_url(station, path), headers=auth_headers,
                                json={"entry_id": playing, "new_position": 0})
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_reorder_sets_the_position(client, auth_headers, db_session):
    """B-QUEUE-46, B-QUEUE-47"""
    station, assets, entries = await _station_with_queue(db_session)
    a, b, c, d = await _pending_ids(db_session, station.id)
    res = await _timed(client.put(_url(station, "/reorder"), headers=auth_headers,
                                  json={"entry_id": a, "new_position": 99}))
    assert res.status_code == 200
    assert await _pending_ids(db_session, station.id) == [b, c, d, a]
    res = await client.put(_url(station, "/reorder"), headers=auth_headers,
                           json={"entry_id": str(uuid.uuid4()), "new_position": 1})
    assert res.status_code == 404
    # The next pick follows the new order
    q = (await client.get(_url(station), headers=auth_headers)).json()
    assert q["next_entry_id"] == b


@pytest.mark.asyncio
async def test_drag_and_drop_moves_and_shifts_the_rest(client, auth_headers, db_session):
    """B-QUEUE-48, B-QUEUE-49"""
    station, assets, entries = await _station_with_queue(db_session)
    a, b, c, d = await _pending_ids(db_session, station.id)
    pos = {str(e.id): e.position for e in entries}

    # Down: a to d's place
    res = await _timed(client.post(_url(station, "/reorder-dnd"), headers=auth_headers,
                                   json={"entry_id": a, "new_position": pos[d]}))
    assert res.status_code == 200 and res.json()["warnings"] == []
    assert await _pending_ids(db_session, station.id) == [b, c, d, a]

    # Up: a back to the top
    top = min([(await db_session.get(QueueEntry, uuid.UUID(x), populate_existing=True)).position for x in (b, c, d)])
    res = await client.post(_url(station, "/reorder-dnd"), headers=auth_headers,
                            json={"entry_id": a, "new_position": top})
    assert await _pending_ids(db_session, station.id) == [a, b, c, d]
    positions = [(await db_session.get(QueueEntry, uuid.UUID(x), populate_existing=True)).position for x in (a, b, c, d)]
    assert len(set(positions)) == 4

    # Not pending (on air) or unknown -> 404
    for entry_id in (str(entries[0].id), str(uuid.uuid4())):
        res = await client.post(_url(station, "/reorder-dnd"), headers=auth_headers,
                                json={"entry_id": entry_id, "new_position": 2})
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_drag_and_drop_warns_about_rules(client, auth_headers, db_session):
    """B-QUEUE-50: category transitions, daypart rules and do_not_play"""
    station, assets, entries = await _station_with_queue(db_session)
    for a, cat in zip(assets[1:], ["slow", "lively", "lively", "do_not_play"]):
        a.category = cat
    db_session.add(Category(id=uuid.uuid4(), name="slow",
                            allowed_transitions={"after": ["slow"], "before": ["slow"]}))
    db_session.add(ScheduleRule(id=uuid.uuid4(), name="Shiurim all day", rule_type="daypart",
                                asset_type="shiur", hour_start=0, hour_end=24, is_active=True))
    await db_session.commit()
    a, b, c, d = await _pending_ids(db_session, station.id)
    pos = {str(e.id): e.position for e in entries}

    # "slow" song dropped between two lively ones
    res = await client.post(_url(station, "/reorder-dnd"), headers=auth_headers,
                            json={"entry_id": a, "new_position": pos[c]})
    warnings = res.json()["warnings"]
    assert any("Category transition" in w for w in warnings)
    assert any("Daypart rule" in w and "shiur" in w for w in warnings)

    res = await client.post(_url(station, "/reorder-dnd"), headers=auth_headers,
                            json={"entry_id": d, "new_position": pos[a]})
    assert any("do_not_play" in w for w in res.json()["warnings"])


@pytest.mark.asyncio
async def test_entries_of_another_station_cannot_be_changed_through_this_station(
    client, db_session
):
    """Station permissions (wiki/station-permissions.md): a DJ allowed on the web station
    must not move, reorder or delete entries of the protected FM station by sending
    their ids to the web station's URL."""
    fm, _, fm_entries = await _station_with_queue(db_session, name="FM", protected=True)
    web, _, _ = await _station_with_queue(db_session, name="Web")
    _, dj = await _user(client, db_session, UserRole.DJ, [str(web.id)])
    fm_before = await _pending_ids(db_session, fm.id)
    target = fm_before[1]

    assert (await client.post(_url(fm, "/move-up"), headers=dj,
                              json={"entry_id": target, "new_position": 0})).status_code == 403
    for method, path, body in (
        ("post", "/move-up", {"entry_id": target, "new_position": 0}),
        ("post", "/move-down", {"entry_id": target, "new_position": 0}),
        ("put", "/reorder", {"entry_id": target, "new_position": 99}),
        ("post", "/reorder-dnd", {"entry_id": target, "new_position": 99}),
    ):
        res = await getattr(client, method)(_url(web, path), headers=dj, json=body)
        assert res.status_code == 404, (path, res.status_code)
    res = await client.delete(_url(web, f"/{target}"), headers=dj)
    assert res.status_code == 404
    assert await _pending_ids(db_session, fm.id) == fm_before


@pytest.mark.asyncio
async def test_remove_takes_the_entry_out(client, auth_headers, db_session):
    """B-QUEUE-51 (timed)"""
    station, _, _ = await _station_with_queue(db_session)
    a, b, c, d = await _pending_ids(db_session, station.id)
    res = await _timed(client.delete(_url(station, f"/{b}"), headers=auth_headers))
    assert res.status_code == 204
    assert await _pending_ids(db_session, station.id) == [a, c, d]
    assert (await client.delete(_url(station, f"/{uuid.uuid4()}"), headers=auth_headers)).status_code == 404


# ── Start ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_during_a_blackout_queues_silence(client, auth_headers, db_session):
    """B-QUEUE-55: blackout active -> silence entries queued, nothing else started"""
    station, _, _ = await _station_with_queue(db_session, playing=False)
    silence = Asset(id=uuid.uuid4(), title="Silence", duration=300.0,
                    file_path="https://cdn.example.com/silence.mp3", asset_type="silence")
    now = datetime.now(UTC)
    db_session.add_all([silence, HolidayWindow(
        id=uuid.uuid4(), name="Shabbos", start_datetime=now - timedelta(hours=1),
        end_datetime=now + timedelta(hours=1), is_blackout=True, affected_stations=None,
    )])
    await db_session.commit()
    res = await _timed(client.post(_url(station, "/start"), headers=auth_headers))
    body = res.json()
    assert "Blackout active" in body["message"]
    assert body["now_playing"] == str(silence.id)
    rows = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.asset_id == silence.id,
        QueueEntry.status.in_(["pending", "playing"]),
    ).execution_options(populate_existing=True))).scalars().all()
    assert len(rows) >= 1
    # No song was started
    playing = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "playing",
        QueueEntry.asset_id != silence.id,
    ).execution_options(populate_existing=True))).scalars().all()
    assert playing == []


@pytest.mark.asyncio
async def test_start_on_an_engine_station_starts_nothing_locally(
    client, auth_headers, db_session, monkeypatch
):
    """B-QUEUE-56"""
    from app.config import settings

    station, _, _ = await _station_with_queue(db_session, playing=False)
    monkeypatch.setattr(type(settings), "engine_drives", lambda self, sid: str(sid) == str(station.id))
    body = (await client.post(_url(station, "/start"), headers=auth_headers)).json()
    assert body == {"message": "Playout engine will start the next track", "now_playing": None}
    playing = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "playing").execution_options(populate_existing=True))).scalars().all()
    assert playing == []


@pytest.mark.asyncio
async def test_start_cleans_duplicate_playing_rows(client, auth_headers, db_session):
    """Start with two 'playing' rows keeps the newest only (B-JOB-9 for the Start path)"""
    station, assets, entries = await _station_with_queue(db_session)
    older = entries[1]
    older.status = "playing"
    older.started_at = datetime.now(UTC) - timedelta(minutes=10)
    await db_session.commit()
    body = (await client.post(_url(station, "/start"), headers=auth_headers)).json()
    assert body["message"] == "Already playing"
    assert body["now_playing"] == str(entries[0].asset_id)
    playing = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "playing").execution_options(populate_existing=True))).scalars().all()
    assert [p.id for p in playing] == [entries[0].id]


# ── Manager triggers ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_weather_triggers_without_tts_say_so_and_insert_nothing(client, auth_headers, db_session):
    """B-QUEUE-58..61 when ElevenLabs/storage are not set up (the test environment)"""
    station, _, _ = await _station_with_queue(db_session)
    before = await _pending_ids(db_session, station.id)
    for path in ("/preview-weather", "/insert-weather"):
        res = await client.post(_url(station, path), headers=auth_headers)
        assert res.status_code == 503
        assert "not configured" in res.json()["error"]
    assert await _pending_ids(db_session, station.id) == before


@pytest.mark.asyncio
async def test_weather_triggers_with_clips(client, auth_headers, db_session, monkeypatch):
    """B-QUEUE-58..61: preview returns the clips and queues nothing; insert puts time then
    weather right after the item on air; insert is manager-only, preview staff."""
    from app.config import settings
    from app.services import weather_service, weather_spot_service

    station, _, entries = await _station_with_queue(db_session)
    time_clip = Asset(id=uuid.uuid4(), title="Time 3 PM", duration=6.0,
                      file_path="https://cdn.example.com/time.mp3", asset_type="jingle")
    weather_clip = Asset(id=uuid.uuid4(), title="Weather", duration=25.0,
                         file_path="https://cdn.example.com/weather.mp3", asset_type="jingle")
    db_session.add_all([time_clip, weather_clip])
    await db_session.commit()

    monkeypatch.setattr(type(settings), "elevenlabs_enabled", property(lambda self: True))
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))

    async def clips(db, slot_key, region=None):
        return time_clip, weather_clip

    async def weather():
        return {"temp": 70}

    monkeypatch.setattr(weather_spot_service, "get_or_create_weather_spot_assets", clips)
    monkeypatch.setattr(weather_service, "get_current_weather", weather)
    monkeypatch.setattr(weather_spot_service, "_build_weather_text", lambda data: "Sunny")

    before = await _pending_ids(db_session, station.id)
    _, viewer = await _user(client, db_session, UserRole.VIEWER)
    res = await _timed(client.post(_url(station, "/preview-weather"), headers=viewer))
    assert res.status_code == 200
    body = res.json()
    assert body["time_url"] == time_clip.file_path and body["weather_url"] == weather_clip.file_path
    assert body["time_text"] and body["weather_text"] == "Sunny"
    assert await _pending_ids(db_session, station.id) == before

    _, dj = await _user(client, db_session, UserRole.DJ)
    assert (await client.post(_url(station, "/insert-weather"), headers=dj)).status_code == 403
    res = await _timed(client.post(_url(station, "/insert-weather"), headers=auth_headers))
    assert res.json()["inserted"] == 2
    rows = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "pending"
    ).order_by(QueueEntry.position).execution_options(populate_existing=True))).scalars().all()
    assert [r.asset_id for r in rows[:2]] == [time_clip.id, weather_clip.id]
    assert [str(r.id) for r in rows[2:]] == before
    assert len({r.position for r in rows}) == len(rows)


@pytest.mark.asyncio
async def test_schedule_hourly_is_manager_only_and_does_not_duplicate(
    client, auth_headers, db_session, monkeypatch
):
    """B-QUEUE-62, B-QUEUE-63"""
    from app.services.queue_replenish_service import QueueReplenishService

    station, _, _ = await _station_with_queue(db_session)
    clip = Asset(id=uuid.uuid4(), title="Time", duration=6.0,
                 file_path="https://cdn.example.com/t.mp3", asset_type="jingle")
    db_session.add(clip)
    await db_session.commit()

    async def fake_hourly(self):
        # Same dedupe the real one uses: one entry per hour
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        for h in range(1, 4):
            at = now + timedelta(hours=h)
            exists = (await self.db.execute(select(QueueEntry.id).where(
                QueueEntry.station_id == self.station_id, QueueEntry.preempt_at == at,
                QueueEntry.status == "pending"))).first()
            if exists:
                continue
            self.max_pos += 1
            self.db.add(QueueEntry(id=uuid.uuid4(), station_id=self.station_id, asset_id=clip.id,
                                   position=self.max_pos, status="pending", preempt_at=at,
                                   timing_mode="hard"))

    monkeypatch.setattr(QueueReplenishService, "_schedule_hourly_announcements", fake_hourly)
    _, dj = await _user(client, db_session, UserRole.DJ)
    assert (await client.post(_url(station, "/schedule-hourly"), headers=dj)).status_code == 403
    one = (await client.post(_url(station, "/schedule-hourly"), headers=auth_headers)).json()
    two = (await client.post(_url(station, "/schedule-hourly"), headers=auth_headers)).json()
    assert one["preempt_entries"] == 3 and two["preempt_entries"] == 3


@pytest.mark.asyncio
async def test_schedule_hourly_with_the_real_scheduler_does_not_duplicate(
    client, auth_headers, db_session, monkeypatch
):
    """B-QUEUE-63 through the real _schedule_hourly_announcements (uploaded time clips,
    storage switched on): running it twice keeps one time check per hour."""
    from app.config import settings
    from app.services import weather_spot_service

    station, _, _ = await _station_with_queue(db_session)
    monkeypatch.setattr(type(settings), "supabase_storage_enabled", property(lambda self: True))
    monkeypatch.setattr(type(settings), "elevenlabs_enabled", property(lambda self: False))
    clip = Asset(id=uuid.uuid4(), title="Time", duration=6.0,
                 file_path="https://cdn.example.com/t.mp3", asset_type="jingle")
    db_session.add(clip)
    await db_session.commit()

    async def clips(db, slot_key, region=None):
        return clip, None

    monkeypatch.setattr(weather_spot_service, "get_or_create_weather_spot_assets", clips)

    one = await client.post(_url(station, "/schedule-hourly"), headers=auth_headers)
    two = await client.post(_url(station, "/schedule-hourly"), headers=auth_headers)
    assert one.status_code == 200 and two.status_code == 200
    assert one.json()["preempt_entries"] == two.json()["preempt_entries"]
    rows = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "pending",
        QueueEntry.preempt_at.isnot(None)).execution_options(populate_existing=True))).scalars().all()
    keys = [(r.asset_id, r.preempt_at) for r in rows]
    assert len(keys) == len(set(keys))
    assert 47 <= len(keys) <= 48  # one time check for each of the next 48 hours
    assert all(r.preempt_at.minute == 0 and r.timing_mode == "hard" for r in rows)


@pytest.mark.asyncio
async def test_no_hourly_announcements_without_storage(client, auth_headers, db_session):
    """B-JOB-58: storage not configured -> nothing scheduled, no error"""
    station, _, _ = await _station_with_queue(db_session)
    res = await client.post(_url(station, "/schedule-hourly"), headers=auth_headers)
    assert res.status_code == 200 and res.json()["preempt_entries"] == 0


@pytest.mark.asyncio
async def test_force_replenish_reports_counts(client, auth_headers, db_session):
    """B-QUEUE-64, B-QUEUE-65"""
    station, _, entries = await _station_with_queue(db_session)
    _, dj = await _user(client, db_session, UserRole.DJ)
    assert (await client.post(_url(station, "/force-replenish"), headers=dj)).status_code == 403
    body = (await client.post(_url(station, "/force-replenish"), headers=auth_headers)).json()
    assert body["message"] == "Replenish complete"
    assert body["total_entries"] == len(entries)
    assert body["total_seconds"] == 180.0 * len(entries)
    assert body["total_hours"] == round(180.0 * len(entries) / 3600, 1)


# ── Fire-now / mix-over edge cases ────────────────────────────────────────


@pytest.mark.asyncio
async def test_fire_now_during_a_timed_item_still_goes_on_air(client, auth_headers, db_session):
    """B-QUEUE-20: Next never skips a time check, but play-now does put the item on air"""
    station, assets, entries = await _station_with_queue(db_session)
    entries[0].preempt_at = datetime.now(UTC) - timedelta(seconds=5)
    entries[0].timing_mode = "hard"
    await db_session.commit()
    skip = (await client.post(_url(station, "/skip"), headers=auth_headers)).json()
    assert skip.get("protected") is True
    body = (await _timed(client.post(_url(station, "/fire-now"), headers=auth_headers,
                                     json={"asset_id": str(assets[3].id)}))).json()
    assert body["on_air"] is True
    assert body["queue"]["now_playing"]["asset_id"] == str(assets[3].id)


@pytest.mark.asyncio
async def test_fire_now_with_nothing_on_air_starts_and_the_queue_follows(client, auth_headers, db_session):
    """V1-51"""
    station, assets, entries = await _station_with_queue(db_session, playing=False)
    first_waiting = str(entries[0].id)
    body = (await client.post(_url(station, "/fire-now"), headers=auth_headers,
                              json={"asset_id": str(assets[4].id)})).json()
    q = body["queue"]
    assert q["now_playing"]["asset_id"] == str(assets[4].id)
    assert q["next_entry_id"] == first_waiting


def _aware_dt(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@pytest.mark.asyncio
async def test_mix_over_logs_once_and_ignores_a_double_tap(client, auth_headers, db_session, monkeypatch):
    """B-QUEUE-25, V1-46, V1-47: one PlayLog (source manual, length of the item), a repeat
    of the same client_request_id does nothing, the queue does not move"""
    import app.api.v1.websocket as ws

    sent = []

    async def capture(station_id, message):
        sent.append(message)

    monkeypatch.setattr(ws.manager, "broadcast_to_station", capture)
    station, assets, entries = await _station_with_queue(db_session)
    on_air_id = str(entries[0].id)
    before = await _pending_ids(db_session, station.id)
    payload = {"asset_id": str(assets[2].id), "client_request_id": "jingle-1"}
    one = (await _timed(client.post(_url(station, "/mix-over"), headers=auth_headers, json=payload))).json()
    two = (await client.post(_url(station, "/mix-over"), headers=auth_headers, json=payload)).json()
    assert one["on_air"] is True and two.get("duplicate") is True
    assert len(sent) == 1 and sent[0]["type"] == "mix_over"
    logs = (await db_session.execute(select(PlayLog).where(PlayLog.station_id == station.id)
                                     .execution_options(populate_existing=True))).scalars().all()
    assert len(logs) == 1
    assert str(getattr(logs[0].source, "value", logs[0].source)) == "manual"
    assert (_aware_dt(logs[0].end_utc) - _aware_dt(logs[0].start_utc)).total_seconds() == 180.0
    assert await _pending_ids(db_session, station.id) == before
    q = (await client.get(_url(station), headers=auth_headers)).json()
    assert q["now_playing"]["id"] == on_air_id


@pytest.mark.asyncio
async def test_ran_dry_fills_with_music_never_with_spots_or_silence(db_session):
    """V1-52: nothing left and the normal fill adds nothing -> random music as fallback;
    without music, any item that is not silence, a spot, a jingle or an announcement"""
    station = Station(id=uuid.uuid4(), name="Dry FM", timezone="UTC")
    db_session.add(station)
    for kind in ("silence", "spot", "jingle"):
        db_session.add(Asset(id=uuid.uuid4(), title=kind, duration=30.0, asset_type=kind,
                             file_path=f"https://cdn.example.com/{kind}.mp3"))
    await db_session.commit()
    station_id = station.id
    assert await queue_module._refill_for_next(db_session, station_id) is None

    talk = Asset(id=uuid.uuid4(), title="Talk", duration=600.0, asset_type="shiur",
                 file_path="https://cdn.example.com/talk.mp3")
    db_session.add(talk)
    await db_session.commit()
    picked = await queue_module._refill_for_next(db_session, station_id)
    assert picked.asset_id == talk.id and picked.source == "fallback"

    song = Asset(id=uuid.uuid4(), title="Song", duration=200.0, asset_type="music",
                 file_path="https://cdn.example.com/song.mp3")
    db_session.add(song)
    await db_session.execute(QueueEntry.__table__.delete().where(QueueEntry.station_id == station_id))
    await db_session.commit()
    picked = await queue_module._refill_for_next(db_session, station_id)
    assert picked.asset_id == song.id
