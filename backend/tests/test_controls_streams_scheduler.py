"""Old station controls (/controls/*), public stream reads (/stream, /now-playing,
/live-audio), now-playing active block, and the scheduler start/stop/status switch."""
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.config import settings
from app.models.asset import ANALYSIS_VERSION, Asset
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import RecurrenceType, ScheduleBlock, SunEvent
from app.models.station import Station
from app.models.user import UserRole
from app.services import scheduler_engine
from app.streaming.playlist_engine import PlaylistEngine
from tests.test_station_permissions import _user

NY = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(db, station_id):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)


class _FakeRedis:
    """Just the list/string calls PlaylistEngine makes."""

    def __init__(self):
        self.kv, self.lists = {}, {}

    async def rpush(self, k, v):
        self.lists.setdefault(k, []).append(v)

    async def lpop(self, k):
        items = self.lists.get(k) or []
        return items.pop(0) if items else None

    async def lrange(self, k, a, b):
        return (self.lists.get(k) or [])[a: b + 1]

    async def llen(self, k):
        return len(self.lists.get(k) or [])

    async def set(self, k, v):
        self.kv[k] = v.encode() if isinstance(v, str) else v  # Redis answers bytes

    async def get(self, k):
        return self.kv.get(k)

    async def delete(self, k):
        self.kv.pop(k, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()

    async def get_redis(self):
        return fake

    monkeypatch.setattr(PlaylistEngine, "_get_redis", get_redis)
    return fake


async def _station_playing(db, *, file_path="https://cdn.example.com/a.mp3",
                           next_path="https://cdn.example.com/b.mp3", elapsed=20.0):
    station = Station(id=uuid.uuid4(), name="Stream FM", timezone="America/New_York")
    a = Asset(id=uuid.uuid4(), title="On air", artist="A", duration=200.0, file_path=file_path,
              asset_type="music",
              metadata_extra={"audio_analysis": {"cue_in_seconds": 0.4, "cross_start_seconds": 195.0,
                                                 "version": ANALYSIS_VERSION}})
    b = Asset(id=uuid.uuid4(), title="Next up", artist="B", duration=180.0, file_path=next_path,
              asset_type="music")
    db.add_all([station, a, b])
    await db.flush()
    db.add_all([
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=a.id, position=1,
                   status="playing", source="auto",
                   started_at=datetime.now(UTC) - timedelta(seconds=elapsed)),
        QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=b.id, position=2,
                   status="pending", source="auto"),
    ])
    await db.commit()
    return station, a, b


# ── /controls (old Redis + HLS player) ────────────────────────────────────


@pytest.mark.asyncio
async def test_controls_pause_and_enqueue(client, auth_headers, db_session, fake_redis):
    """B-CONTROLS-3, B-CONTROLS-7 (with a Redis present)"""
    station, a, b = await _station_playing(db_session)
    base = f"/api/v1/stations/{station.id}/controls"
    res = await client.post(f"{base}/pause", headers=auth_headers)
    assert res.status_code == 200 and res.json() == {"status": "paused"}
    assert await PlaylistEngine(str(station.id)).get_state() == "paused"

    res = await client.post(f"{base}/enqueue", headers=auth_headers, json={"asset_id": str(b.id)})
    assert res.status_code == 200 and res.json() == {"status": "enqueued", "queue_length": 1}
    peek = await PlaylistEngine(str(station.id)).peek_queue(5)
    assert peek[0]["asset_id"] == str(b.id) and peek[0]["title"] == "Next up"

    # Unknown station / asset -> 404; viewer -> 403
    assert (await client.post(f"/api/v1/stations/{uuid.uuid4()}/controls/pause",
                              headers=auth_headers)).status_code == 404
    res = await client.post(f"{base}/enqueue", headers=auth_headers, json={"asset_id": str(uuid.uuid4())})
    assert res.status_code == 404
    _, viewer = await _user(client, db_session, UserRole.VIEWER)
    assert (await client.post(f"{base}/pause", headers=viewer)).status_code == 403


@pytest.mark.asyncio
async def test_controls_play_with_empty_redis_queue(client, auth_headers, db_session, fake_redis):
    """B-CONTROLS-1: play with nothing in the old Redis list says so"""
    station, *_ = await _station_playing(db_session)
    res = await client.post(f"/api/v1/stations/{station.id}/controls/play", headers=auth_headers)
    assert res.status_code == 200
    assert res.json() == {"status": "error", "message": "Queue is empty"}


async def _post_or_500(client, url, **kw):
    try:
        return (await client.post(url, **kw)).status_code
    except Exception:
        return 500


@pytest.mark.asyncio
async def test_controls_do_not_500_without_redis(client, auth_headers, db_session):
    """B-CONTROLS-1/3/5/7 without Redis (production has none)"""
    station, a, _ = await _station_playing(db_session)
    base = f"/api/v1/stations/{station.id}/controls"
    for path, body in (("/pause", None), ("/enqueue", {"asset_id": str(a.id)}),
                       ("/play", None), ("/stop", None)):
        status = await _post_or_500(client, base + path, headers=auth_headers, json=body)
        assert status < 500, (path, status)


@pytest.mark.asyncio
async def test_controls_play_now_goes_through_the_queue(
    client, auth_headers, db_session, fake_redis, monkeypatch
):
    """B-CONTROLS-5, B-CONTROLS-6: play-now must use the queue's single player"""
    from app.services import playback_service

    async def no_download(path):
        return b"ID3"

    async def no_hls(path, station_id):
        return "/tmp/x.m3u8", None

    monkeypatch.setattr(playback_service, "download_file", no_download)
    monkeypatch.setattr(playback_service, "generate_hls_segments", no_hls)
    station, a, b = await _station_playing(db_session)
    res = await client.post(f"/api/v1/stations/{station.id}/controls/play-now",
                            headers=auth_headers, json={"asset_id": str(b.id)})
    assert res.status_code == 200
    q = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    assert q["now_playing"]["asset_id"] == str(b.id)


# ── /stream, /now-playing, /live-audio ────────────────────────────────────


@pytest.mark.asyncio
async def test_live_audio_nothing_playing(client, db_session):
    """B-STREAMS-5/6: nothing on air -> playing false"""
    station = Station(id=uuid.uuid4(), name="Quiet", timezone="UTC")
    db_session.add(station)
    await db_session.commit()
    res = await client.get(f"/api/v1/stations/{station.id}/live-audio")
    assert res.status_code == 200 and res.json() == {"playing": False}


@pytest.mark.asyncio
async def test_live_audio_gives_url_timing_and_next(client, db_session):
    """B-STREAMS-5, B-STREAMS-6"""
    station, a, b = await _station_playing(db_session, elapsed=20.0)
    body = (await client.get(f"/api/v1/stations/{station.id}/live-audio")).json()
    assert body["playing"] is True
    assert body["asset_id"] == str(a.id) and body["audio_url"] == a.file_path
    assert 19.0 <= body["elapsed"] <= 23.0
    assert body["duration"] == 200.0
    # analysed audible end 198 s (crossfade point 195 + 3), station crossfade 1 s
    assert body["cue_in"] == 0.4 and body["cross_start"] == 197.0 and body["overlap_seconds"] == 1.0
    assert body["next_asset"]["id"] == str(b.id)
    assert body["next_asset"]["audio_url"] == b.file_path
    assert "stream_url" not in body  # Liquidsoap off


@pytest.mark.asyncio
async def test_live_audio_storage_paths_and_liquidsoap_branch(client, db_session, monkeypatch):
    """B-STREAMS-6: storage paths become public URLs (on air and next); Liquidsoap adds stream_url.
    The next item's URL used a variable only set for a stored on-air item: an on-air URL
    with a stored next item was a 500."""
    monkeypatch.setattr(settings, "SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setattr(settings, "SUPABASE_SERVICE_KEY", "test-only-not-a-key")
    station, a, b = await _station_playing(db_session, next_path="music/b.mp3")
    res = await client.get(f"/api/v1/stations/{station.id}/live-audio")
    assert res.status_code == 200
    bucket = settings.SUPABASE_STORAGE_BUCKET
    assert res.json()["next_asset"]["audio_url"] == (
        f"https://proj.supabase.co/storage/v1/object/public/{bucket}/music/b.mp3"
    )

    monkeypatch.setattr(settings, "LIQUIDSOAP_ENABLED", True)
    monkeypatch.setattr(settings, "LIQUIDSOAP_HOST", "ls.example.com")
    monkeypatch.setattr(settings, "ICECAST_STREAM_URL", "https://stream.example.com/live")
    body = (await client.get(f"/api/v1/stations/{station.id}/live-audio")).json()
    assert body["stream_url"] == "https://stream.example.com/live"


@pytest.mark.asyncio
async def test_stream_now_playing_works_without_redis(client, db_session):
    """B-STREAMS-3"""
    station, a, _ = await _station_playing(db_session)
    try:
        res = await client.get(f"/api/v1/stations/{station.id}/now-playing")
        status = res.status_code
    except Exception:
        status = 500
    assert status == 200


@pytest.mark.asyncio
async def test_stream_now_playing_with_redis_and_unknown_station(client, db_session, fake_redis):
    """B-STREAMS-3 with a Redis present; unknown station -> 404"""
    station, *_ = await _station_playing(db_session)
    body = (await client.get(f"/api/v1/stations/{station.id}/now-playing")).json()
    assert set(body) == {"state", "now_playing", "upcoming"}
    assert (await client.get(f"/api/v1/stations/{uuid.uuid4()}/now-playing")).status_code == 404


# ── /now-playing/{id}/active-block ────────────────────────────────────────


async def _schedule(db, station, blocks):
    sched = Schedule(id=uuid.uuid4(), station_id=station.id, name="Main", is_active=True)
    db.add(sched)
    await db.flush()
    out = []
    for kw in blocks:
        block = ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id,
                              recurrence_type=kw.pop("recurrence_type", RecurrenceType.DAILY), **kw)
        db.add(block)
        out.append(block)
    await db.commit()
    return out


@pytest.mark.asyncio
async def test_active_block_none_and_fixed(client, db_session):
    """B-NOWPLAYING-2"""
    station = Station(id=uuid.uuid4(), name="Blocks", timezone="America/New_York")
    db_session.add(station)
    await db_session.commit()
    url = f"/api/v1/now-playing/{station.id}/active-block"
    assert (await client.get(url)).json()["message"] == "No active block found"

    await _schedule(db_session, station, [
        {"name": "Morning", "start_time": time(6), "end_time": time(12)},
        {"name": "Afternoon", "start_time": time(12), "end_time": time(18)},
    ])
    at = datetime(2026, 11, 17, 9, 30, tzinfo=NY).astimezone(UTC).isoformat()
    body = (await client.get(url, params={"at_time": at})).json()
    assert body["name"] == "Morning" and body["recurrence_type"] == "daily"
    at = datetime(2026, 11, 17, 13, 0, tzinfo=NY).astimezone(UTC).isoformat()
    assert (await client.get(url, params={"at_time": at})).json()["name"] == "Afternoon"


@pytest.mark.asyncio
async def test_active_block_resolves_sunset_times(client, db_session):
    """B-NOWPLAYING-3: a block that starts at sunset is active after sunset, not before"""
    from app.services.sun_service import get_sun_times

    station = Station(id=uuid.uuid4(), name="Sunset FM", timezone="America/New_York",
                      latitude=40.0821, longitude=-74.2097)
    db_session.add(station)
    await db_session.commit()
    await _schedule(db_session, station, [
        {"name": "Day", "start_time": time(0), "end_time": time(23, 59), "priority": 0},
        {"name": "Evening", "start_time": time(16), "end_time": time(23, 59), "priority": 5,
         "start_sun_event": SunEvent.SUNSET, "start_sun_offset": 0},
    ])
    day = date(2026, 11, 17)
    sunset = get_sun_times(40.0821, -74.2097, "America/New_York", day)["sunset"]
    url = f"/api/v1/now-playing/{station.id}/active-block"
    before = (sunset - timedelta(minutes=20)).astimezone(UTC).isoformat()
    after = (sunset + timedelta(minutes=20)).astimezone(UTC).isoformat()
    assert (await client.get(url, params={"at_time": before})).json()["name"] == "Day"
    assert (await client.get(url, params={"at_time": after})).json()["name"] == "Evening"
    # Sunset in mid-November Lakewood is around 4:40 PM: a 4:10 PM check is still "Day"
    # although the block's fixed start_time (16:00) has passed
    four_ten = datetime(2026, 11, 17, 16, 10, tzinfo=NY).astimezone(UTC).isoformat()
    assert (await client.get(url, params={"at_time": four_ten})).json()["name"] == "Day"


def test_old_nowplaying_socket_is_not_mounted():
    """B-NOWPLAYING-6, B-NOWPLAYING-7: api/v1/nowplaying.py is dead code; the live socket
    is /ws/now-playing/{id} from websocket.py."""
    from app.main import create_app

    from tests.route_list import app_routes

    paths = {r.path for r in app_routes(create_app())}
    assert not any(p.startswith("/api/v1/ws/nowplaying") for p in paths)
    assert "/api/v1/ws/now-playing/{station_id}" in paths


# ── /scheduler ────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_scheduler(monkeypatch):
    """A scheduler whose loop does nothing (no real database in tests)."""
    engine = scheduler_engine.SchedulerEngine()
    monkeypatch.setattr(scheduler_engine, "_scheduler_instance", engine)

    async def idle_loop(self):
        import asyncio
        while self.running:
            await asyncio.sleep(0.05)

    monkeypatch.setattr(scheduler_engine.SchedulerEngine, "_run_loop", idle_loop)
    yield engine
    engine.running = False


@pytest.mark.asyncio
async def test_scheduler_start_stop_status(client, auth_headers, db_session, fresh_scheduler):
    """B-SCHEDULER-1, B-SCHEDULER-2, B-SCHEDULER-4"""
    status = (await client.get("/api/v1/scheduler/status")).json()  # public
    assert status == {"running": False, "check_interval": 3}

    _, dj = await _user(client, db_session, UserRole.DJ)
    assert (await client.post("/api/v1/scheduler/start", headers=dj)).status_code == 403
    assert (await client.post("/api/v1/scheduler/stop", headers=dj)).status_code == 403
    assert (await client.post("/api/v1/scheduler/start")).status_code in (401, 403)

    assert (await client.post("/api/v1/scheduler/start", headers=auth_headers)).json()["status"] == "started"
    assert (await client.post("/api/v1/scheduler/start", headers=auth_headers)).json()["status"] == "already_running"
    assert (await client.get("/api/v1/scheduler/status")).json()["running"] is True
    assert (await client.post("/api/v1/scheduler/stop", headers=auth_headers)).json()["status"] == "stopped"
    assert (await client.post("/api/v1/scheduler/stop", headers=auth_headers)).json()["status"] == "already_stopped"
    assert (await client.get("/api/v1/scheduler/status")).json()["running"] is False


@pytest.mark.asyncio
async def test_stopped_scheduler_no_longer_advances(db_session, fresh_scheduler, monkeypatch):
    """B-SCHEDULER-3, B-JOB-21: stop cancels the song-end and timed timers and gives up the
    lease; a timer that still fires afterwards changes nothing."""
    import asyncio

    engine = fresh_scheduler
    station, a, b = await _station_playing(db_session, elapsed=199.0)
    engine._leader_until = datetime.now(UTC) + timedelta(seconds=15)
    released = []

    async def release(self):
        released.append(True)
        self._leader_until = None

    monkeypatch.setattr(scheduler_engine.SchedulerEngine, "_release_lease", release)
    await engine.start()
    engine._schedule_precise_advance(station.id, 30.0, 3.0)
    loop = asyncio.get_running_loop()
    engine._timed_timers[str(station.id)] = (datetime.now(UTC), loop.call_later(30, lambda: None))
    await engine.stop()
    assert engine._advance_timers == {} and engine._timed_timers == {}
    assert released and engine.is_leader is False

    calls = []

    async def spy(*args, **kw):
        calls.append(args)

    monkeypatch.setattr(queue_module, "_check_advance", spy)
    await engine._precise_advance(station.id, 3.0)
    assert calls == []  # not the leader: the item on air is left alone

    # Engine stations are never advanced by the precise timer either
    engine._leader_until = datetime.now(UTC) + timedelta(seconds=15)
    monkeypatch.setattr(type(settings), "engine_drives", lambda self, sid: True)
    await engine._precise_advance(station.id, 3.0)
    assert calls == []
    rows = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.status == "playing"))).scalars().all()
    assert [r.asset_id for r in rows] == [a.id]

