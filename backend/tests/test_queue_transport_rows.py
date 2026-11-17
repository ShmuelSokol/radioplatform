"""Operator queue actions from the control room checklist (rows V1-42, V1-45, V1-48,
V1-49, V1-59): PUT reorder, adding odd items, Next on the engine, loop across a restart,
and the old /controls."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.asset import Asset
from app.models.queue_entry import QueueEntry
from tests.test_operator_actions import _finish_current
from tests.test_playout_engine import HEADERS as ENGINE_HEADERS
from tests.test_playout_engine import engine_station  # noqa: F401  (fixture)
from tests.test_controls_streams_scheduler import fake_redis  # noqa: F401  (fixture)
from tests.test_queue_advance import _station_with_two_tracks
from tests.test_queue_endpoints import _pending_ids, _station_with_queue, _url
from tests.test_restart_holidays_timezones import restart  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _no_replenish(monkeypatch):
    async def no_replenish(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)
    queue_module._fire_requests.clear()
    queue_module._automatic_change.clear()


async def _fresh(db, entry_id):
    return await db.get(QueueEntry, entry_id, populate_existing=True)


# ── V1-42: PUT /reorder ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reorder_makes_room_and_leaves_the_item_on_air_alone(client, auth_headers, db_session):
    """V1-42: the moved entry takes the place asked for and the others shift (no two
    waiting entries share a position, so the order and Next are clear); the item on air
    and a played item cannot be moved"""
    station, _, entries = await _station_with_queue(db_session)
    a, b, c, d = await _pending_ids(db_session, station.id)
    pos = {str(e.id): e.position for e in entries}

    res = await client.put(_url(station, "/reorder"), headers=auth_headers,
                           json={"entry_id": d, "new_position": pos[b]})
    assert res.status_code == 200
    assert await _pending_ids(db_session, station.id) == [a, d, b, c]
    rows = (await db_session.execute(
        select(QueueEntry.position).where(QueueEntry.station_id == station.id,
                                          QueueEntry.status == "pending")
        .execution_options(populate_existing=True)
    )).scalars().all()
    assert len(rows) == len(set(rows))
    q = (await client.get(_url(station), headers=auth_headers)).json()
    assert q["next_entry_id"] == a

    # To the very top: it plays next
    res = await client.put(_url(station, "/reorder"), headers=auth_headers,
                           json={"entry_id": c, "new_position": 0})
    assert res.status_code == 200
    assert (await client.get(_url(station), headers=auth_headers)).json()["next_entry_id"] == c

    # The item on air, and a played one, are refused and keep their place
    on_air = entries[0]
    res = await client.put(_url(station, "/reorder"), headers=auth_headers,
                           json={"entry_id": str(on_air.id), "new_position": 99})
    assert res.status_code == 404
    assert (await _fresh(db_session, on_air.id)).status == "playing"
    played = await _fresh(db_session, entries[1].id)
    played.status = "played"
    await db_session.commit()
    res = await client.put(_url(station, "/reorder"), headers=auth_headers,
                           json={"entry_id": str(played.id), "new_position": 0})
    assert res.status_code == 404


# ── V1-45: odd items added by the operator ────────────────────────────────


@pytest.mark.asyncio
async def test_add_unknown_or_odd_assets_never_500(client, auth_headers, db_session):
    """V1-45: an unknown asset is a 404 on add / bulk-add / play-next; a silence item
    is accepted like any other choice of the operator"""
    station, _, _ = await _station_with_queue(db_session)
    silence = Asset(id=uuid.uuid4(), title="Silence", duration=60.0, asset_type="silence",
                    file_path="https://cdn.example.com/silence.mp3")
    db_session.add(silence)
    await db_session.commit()
    ghost = str(uuid.uuid4())
    for path, body in (("/add", {"asset_id": ghost}), ("/bulk-add", {"asset_ids": [ghost]}),
                       ("/play-next", {"asset_id": ghost})):
        res = await client.post(_url(station, path), headers=auth_headers, json=body)
        assert res.status_code == 404, (path, res.status_code, res.text)
    for path, body in (("/add", {"asset_id": str(silence.id)}),
                       ("/bulk-add", {"asset_ids": [str(silence.id)]}),
                       ("/play-next", {"asset_id": str(silence.id)})):
        res = await client.post(_url(station, path), headers=auth_headers, json=body)
        assert res.status_code == 201, (path, res.status_code, res.text)


@pytest.mark.asyncio
async def test_a_placeholder_the_operator_picks_still_plays(client, auth_headers, db_session):
    """V1-45: placeholders never air from automatic picks, but one the operator queues
    (OPERATOR_SOURCES) does"""
    station, _, _ = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    placeholder = Asset(id=uuid.uuid4(), title="Kol Bramah Ad", duration=30.0, asset_type="spot",
                        file_path="https://cdn.example.com/ph.mp3")
    db_session.add(placeholder)
    await db_session.commit()
    res = await client.post(f"/api/v1/stations/{station.id}/queue/play-next", headers=auth_headers,
                            json={"asset_id": str(placeholder.id)})
    assert res.status_code == 201
    entry = await _finish_current(db_session, station)
    assert entry is not None and entry.asset_id == placeholder.id


# ── V1-48: Next on an engine station ──────────────────────────────────────


@pytest.mark.asyncio
async def test_engine_skip_with_nothing_on_air_still_asks_the_engine(
    client, auth_headers, engine_station  # noqa: F811
):
    """V1-48: no playing entry known (lost start report): Next still goes to the engine"""
    station, _, sent = engine_station
    res = await client.post(f"/api/v1/stations/{station.id}/queue/skip?fade=hard", headers=auth_headers)
    assert res.status_code == 200
    assert sent == [("/skip", {"fade": 0.0})]


@pytest.mark.asyncio
async def test_engine_skip_during_a_loop_ends_it_and_flushes_first(
    client, auth_headers, db_session, engine_station  # noqa: F811
):
    """V1-48: Next with a loop on: loop off, the pre-loaded repeat flushed, then /skip"""
    station, entries, sent = engine_station
    base = f"/api/v1/playout/{station.id}"
    first = (await client.get(f"{base}/next", headers=ENGINE_HEADERS)).json()
    await client.post(f"{base}/started", headers=ENGINE_HEADERS,
                      json={"entry_id": first["entry_id"], "lane": "main"})
    res = await client.post(f"/api/v1/stations/{station.id}/queue/loop", headers=auth_headers,
                            json={"on": True})
    assert res.status_code == 200
    sent.clear()
    res = await client.post(f"/api/v1/stations/{station.id}/queue/skip", headers=auth_headers)
    assert res.status_code == 200
    assert sent == [("/flush", None), ("/skip", {"fade": 0.5})]
    q = (await client.get(f"/api/v1/stations/{station.id}/queue", headers=auth_headers)).json()
    assert q["loop_asset_id"] is None
    nxt = (await client.get(f"{base}/next?given={first['entry_id']}", headers=ENGINE_HEADERS)).json()
    assert nxt["entry_id"] == str(entries[1].id)


# ── V1-49: loop across a backend restart ──────────────────────────────────


@pytest.mark.asyncio
async def test_loop_survives_a_restart_while_the_item_plays(client, auth_headers, db_session, restart):  # noqa: F811
    """V1-49: the loop is kept in automation_config, so after a restart in the middle of
    the item it still repeats when it ends"""
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    looped = playing.asset_id
    res = await client.post(f"/api/v1/stations/{station.id}/queue/loop", headers=auth_headers,
                            json={"on": True})
    assert res.status_code == 200
    queue_module._automatic_change.clear()  # in-memory state is gone after a restart
    await restart()
    assert (await _fresh(db_session, playing.id)).status == "playing"
    entry = await _finish_current(db_session, station)
    assert entry.asset_id == looped and entry.source == "loop"
    assert (await _fresh(db_session, pending.id)).status == "pending"


@pytest.mark.xfail(strict=True, reason=(
    "BUG: a looped item that ended while the backend was down is settled as played by "
    "_resume_playback_on_startup (main.py) without queueing the loop repeat, so the next "
    "song starts and the loop is silently broken although loop_asset_id is still set"))
@pytest.mark.asyncio
async def test_loop_survives_a_restart_after_the_item_ended(client, auth_headers, db_session, restart):  # noqa: F811
    """V1-49: the item ended during the downtime: the restart should start it again"""
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    looped = playing.asset_id
    await client.post(f"/api/v1/stations/{station.id}/queue/loop", headers=auth_headers, json={"on": True})
    row = await _fresh(db_session, playing.id)
    row.started_at = datetime.now(UTC) - timedelta(seconds=600)
    await db_session.commit()
    await restart()
    now = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "playing")
        .execution_options(populate_existing=True)
    )).scalar_one()
    assert now.asset_id == looped


# ── V1-59: the old /controls ──────────────────────────────────────────────


def _record_old_player(monkeypatch):
    """Record any use of the old HLS player (download + ffmpeg segments)."""
    from app.services import playback_service

    used = []

    async def download(path):
        used.append(("download", path))
        return b"ID3"

    async def hls(path, station_id):
        used.append(("hls", station_id))
        return "/tmp/x.m3u8", None

    monkeypatch.setattr(playback_service, "download_file", download)
    monkeypatch.setattr(playback_service, "generate_hls_segments", hls)
    return used


@pytest.mark.asyncio
async def test_old_controls_never_start_a_second_player(client, auth_headers, db_session, monkeypatch):
    """V1-59: with REDIS_URL empty (production) play / pause / stop / enqueue answer 409
    with a reason (no 500); play-now goes on air through the queue, never the old HLS path"""
    from app.config import settings

    monkeypatch.setattr(settings, "REDIS_URL", "")
    used = _record_old_player(monkeypatch)
    station, assets, _ = await _station_with_queue(db_session)
    base = f"/api/v1/stations/{station.id}/controls"
    for path, body in (("/pause", None), ("/enqueue", {"asset_id": str(assets[1].id)}),
                       ("/play", None), ("/stop", None)):
        res = await client.post(base + path, headers=auth_headers, json=body)
        assert res.status_code == 409, (path, res.status_code, res.text)
    res = await client.post(f"{base}/play-now", headers=auth_headers, json={"asset_id": str(assets[3].id)})
    assert res.status_code in (200, 201), res.text
    on_air = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "playing")
        .execution_options(populate_existing=True)
    )).scalars().all()
    assert [e.asset_id for e in on_air] == [assets[3].id]
    assert used == []


@pytest.mark.xfail(strict=True, reason=(
    "BUG: with a Redis configured, /controls/enqueue then /controls/play downloads the file "
    "and starts the old ffmpeg HLS player beside the queue (a second audio source on the "
    "station). Not live: production has no REDIS_URL, so these answer 409 there; nothing "
    "in the site calls /controls/play"))
@pytest.mark.asyncio
async def test_old_play_with_redis_never_starts_the_hls_player(
    client, auth_headers, db_session, monkeypatch, fake_redis  # noqa: F811
):
    """V1-59 with Redis set up: the old play must not start a player beside the queue"""
    used = _record_old_player(monkeypatch)
    station, assets, _ = await _station_with_queue(db_session)
    base = f"/api/v1/stations/{station.id}/controls"
    await client.post(f"{base}/enqueue", headers=auth_headers, json={"asset_id": str(assets[1].id)})
    res = await client.post(f"{base}/play", headers=auth_headers)
    assert res.status_code < 500
    assert used == []
