"""Playout engine link: the engine asks what plays next and reports real starts."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import settings
from app.models.asset import Asset
from app.models.play_log import PlayLog
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services import playout_engine

_real_command = playout_engine._command

TOKEN = "test-playout-token"
HEADERS = {"X-Playout-Token": TOKEN}


@pytest_asyncio.fixture
async def engine_station(db_session, monkeypatch):
    station = Station(id=uuid.uuid4(), name="Engine FM", timezone="America/New_York")
    db_session.add(station)
    assets = []
    for i in range(4):
        asset = Asset(
            id=uuid.uuid4(),
            title=f'Song "{i}"',
            artist="Artist",
            duration=180.0,
            file_path=f"https://cdn.example.com/audio/{i}.mp3",
            asset_type="music",
        )
        db_session.add(asset)
        assets.append(asset)
    await db_session.flush()
    entries = []
    for i, asset in enumerate(assets):
        entry = QueueEntry(
            id=uuid.uuid4(),
            station_id=station.id,
            asset_id=asset.id,
            position=i + 1,
            status="pending",
            source="auto",
        )
        db_session.add(entry)
        entries.append(entry)
    await db_session.commit()

    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", str(station.id))
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", TOKEN)
    playout_engine.reset_state()

    sent = []

    async def fake_command(path, payload=None, **_kw):
        sent.append((path, payload))
        return True

    async def no_replenish(db, station_id):
        return None

    async def no_broadcast(db, station_id, entry_id):
        return None

    monkeypatch.setattr(playout_engine, "_command", fake_command)
    import app.api.v1.queue as queue_module

    monkeypatch.setattr(queue_module, "_replenish_queue", no_replenish)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", no_broadcast)
    return station, entries, sent


async def _status(db_session, entry):
    res = await db_session.execute(select(QueueEntry).where(QueueEntry.id == entry.id))
    row = res.scalar_one()
    await db_session.refresh(row)
    return row.status


@pytest.mark.asyncio
async def test_requires_token(client, engine_station):
    station, _, _ = engine_station
    assert (await client.get(f"/api/v1/playout/{station.id}/next")).status_code == 403
    bad = await client.get(
        f"/api/v1/playout/{station.id}/next", headers={"X-Playout-Token": "nope"}
    )
    assert bad.status_code == 403


@pytest.mark.asyncio
async def test_other_station_is_not_engine_driven(client, engine_station):
    resp = await client.get(f"/api/v1/playout/{uuid.uuid4()}/next", headers=HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_next_then_started_advances_queue(client, db_session, engine_station):
    station, entries, _ = engine_station

    first = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert first.status_code == 200
    body = first.json()
    assert body["entry_id"] == str(entries[0].id)
    assert body["uri"].startswith("annotate:")
    assert body["uri"].endswith(":https://cdn.example.com/audio/0.mp3")
    assert f'kbr_entry="{entries[0].id}"' in body["uri"]
    assert 'title="Song \\"0\\""' in body["uri"]  # quotes escaped

    # Asking again before the start report must not hand out the same entry twice
    second = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert second.json()["entry_id"] == str(entries[1].id)

    r = await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[0].id), "lane": "main"},
    )
    assert r.json() == {"ok": True, "known": True}
    assert await _status(db_session, entries[0]) == "playing"

    r = await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[1].id), "lane": "main"},
    )
    assert r.status_code == 200
    assert await _status(db_session, entries[0]) == "played"
    assert await _status(db_session, entries[1]) == "playing"


@pytest.mark.asyncio
async def test_skip_goes_to_engine_and_logs_manual(
    client, db_session, auth_headers, engine_station
):
    station, entries, sent = engine_station
    await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[0].id), "lane": "main"},
    )
    # Pretend it has been on air for a while
    res = await db_session.execute(select(QueueEntry).where(QueueEntry.id == entries[0].id))
    row = res.scalar_one()
    row.started_at = datetime.now(UTC) - timedelta(seconds=30)
    await db_session.commit()

    resp = await client.post(f"/api/v1/stations/{station.id}/queue/skip", headers=auth_headers)
    assert resp.status_code == 200
    assert ("/skip", {"fade": 0.5}) in sent
    # The database only changes when the engine reports the real start
    assert await _status(db_session, entries[0]) == "playing"

    await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[1].id), "lane": "main"},
    )
    assert await _status(db_session, entries[0]) == "skipped"
    logs = (
        (await db_session.execute(select(PlayLog).where(PlayLog.station_id == station.id)))
        .scalars()
        .all()
    )
    assert len(logs) == 1
    assert logs[0].asset_id == entries[0].asset_id
    assert str(getattr(logs[0].source, "value", logs[0].source)) == "manual"


@pytest.mark.asyncio
async def test_skip_reports_engine_down(client, auth_headers, engine_station, monkeypatch):
    station, _, _ = engine_station

    async def down(path, payload=None, **_kw):
        return False

    monkeypatch.setattr(playout_engine, "_command", down)
    resp = await client.post(f"/api/v1/stations/{station.id}/queue/skip", headers=auth_headers)
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_tick_flushes_when_queue_head_changes(client, db_session, engine_station):
    station, entries, sent = engine_station
    await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)  # entries[0] pre-loaded

    await playout_engine.tick(db_session, station)
    assert ("/flush", None) not in sent  # nothing changed

    # Operator moves another entry to the front
    res = await db_session.execute(select(QueueEntry).where(QueueEntry.id == entries[3].id))
    res.scalar_one().position = 0
    await db_session.commit()

    await playout_engine.tick(db_session, station)
    assert ("/flush", None) in sent
    nxt = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert nxt.json()["entry_id"] == str(entries[3].id)


@pytest.mark.asyncio
async def test_tick_hands_over_timed_entry_once(client, db_session, engine_station):
    station, entries, sent = engine_station
    air_time = datetime.now(UTC) + timedelta(seconds=20)
    res = await db_session.execute(select(QueueEntry).where(QueueEntry.id == entries[2].id))
    timed = res.scalar_one()
    timed.preempt_at = air_time
    timed.source = "auto"
    await db_session.commit()

    await playout_engine.tick(db_session, station)
    await playout_engine.tick(db_session, station)
    timed_calls = [p for path, p in sent if path == "/timed"]
    assert len(timed_calls) == 1
    assert abs(timed_calls[0]["at"] - air_time.timestamp()) < 0.01
    assert 'kbr_lane="timed"' in timed_calls[0]["uri"]

    # The main lane never hands out an entry that went to the timed lane
    handed = []
    for _ in range(3):
        handed.append(
            (await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)).json()[
                "entry_id"
            ]
        )
    assert str(entries[2].id) not in handed


@pytest.mark.asyncio
async def test_emergency_report_closes_current_entry(client, db_session, engine_station):
    station, entries, _ = engine_station
    await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[0].id), "lane": "main"},
    )
    r = await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": "", "lane": "emergency"},
    )
    assert r.json() == {"ok": True, "known": False}
    assert await _status(db_session, entries[0]) == "played"


@pytest.mark.asyncio
async def test_stream_url_only_when_live(engine_station, monkeypatch):
    station, _, _ = engine_station
    monkeypatch.setattr(settings, "ICECAST_STREAM_URL", "https://stream.example.com/live")
    monkeypatch.setattr(settings, "PLAYOUT_STREAM_LIVE", False)
    assert settings.public_stream_url(station.id) is None
    monkeypatch.setattr(settings, "PLAYOUT_STREAM_LIVE", True)
    assert settings.public_stream_url(station.id) == f"https://stream.example.com/live-{station.id}"
    assert settings.public_stream_url(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_fire_now_uses_timed_lane(client, db_session, auth_headers, engine_station):
    station, entries, sent = engine_station
    asset_id = str(entries[3].asset_id)
    resp = await client.post(
        f"/api/v1/stations/{station.id}/queue/fire-now",
        headers=auth_headers,
        json={"asset_id": asset_id},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["on_air"] is True
    timed_calls = [p for path, p in sent if path == "/timed"]
    assert len(timed_calls) == 1
    assert f'kbr_entry="{body["id"]}"' in timed_calls[0]["uri"]
    # The main lane must not also play it
    nxt = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert nxt.json()["entry_id"] != body["id"]


@pytest.mark.asyncio
async def test_fire_now_after_current_only_queues(client, auth_headers, engine_station):
    station, entries, sent = engine_station
    resp = await client.post(
        f"/api/v1/stations/{station.id}/queue/fire-now",
        headers=auth_headers,
        json={"asset_id": str(entries[3].asset_id), "mode": "after-current"},
    )
    assert resp.status_code == 201
    assert resp.json()["on_air"] is False
    assert not [p for path, p in sent if path == "/timed"]


@pytest.mark.asyncio
async def test_next_catches_up_on_lost_start_report(client, db_session, engine_station):
    station, entries, _ = engine_station
    # The engine was handed entries[0] and entries[1] and played them while we were
    # unreachable and restarted: no start reports arrived, and our memory is gone.
    playout_engine.reset_state()
    resp = await client.get(
        f"/api/v1/playout/{station.id}/next?given={entries[1].id},{entries[0].id}", headers=HEADERS
    )
    # Neither is handed out again; the older one is closed, the newest may still report
    assert resp.json()["entry_id"] == str(entries[2].id)
    assert await _status(db_session, entries[0]) == "played"
    assert await _status(db_session, entries[1]) == "pending"


@pytest.mark.asyncio
async def test_timing_mode_decides_hard_or_soft(client, db_session, engine_station):
    """`timing_mode` wins over the older rule (ad_slot = soft, anything else = hard)."""
    station, entries, sent = engine_station
    soon = datetime.now(UTC) + timedelta(seconds=20)
    res = await db_session.execute(
        select(QueueEntry).where(QueueEntry.id.in_([entries[2].id, entries[3].id]))
    )
    rows = {r.id: r for r in res.scalars().all()}
    # A sponsor slot marked hard: goes to the timed lane, on the second
    rows[entries[2].id].preempt_at = soon
    rows[entries[2].id].source = "ad_slot"
    rows[entries[2].id].timing_mode = "hard"
    # A scheduled item marked soft: never sent to the timed lane
    rows[entries[3].id].preempt_at = soon
    rows[entries[3].id].source = "auto"
    rows[entries[3].id].timing_mode = "soft"
    await db_session.commit()

    await playout_engine.tick(db_session, station)
    timed_calls = [p for path, p in sent if path == "/timed"]
    assert [c["id"] for c in timed_calls] == [str(entries[2].id)]

    # Once its time has come, the soft item is the next thing handed out
    rows[entries[3].id].preempt_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()
    picked = await playout_engine.pick_next(db_session, station.id, set())
    assert picked.id == entries[3].id


@pytest.mark.asyncio
async def test_loop_hands_the_engine_the_same_item_again(client, auth_headers, db_session, engine_station):
    station, entries, sent = engine_station
    engine = {"X-Playout-Token": TOKEN}
    base = f"/api/v1/playout/{station.id}"
    first = (await client.get(f"{base}/next", headers=engine)).json()
    await client.post(f"{base}/started", headers=engine, json={"entry_id": first["entry_id"], "lane": "main"})
    second = (await client.get(f"{base}/next?given={first['entry_id']}", headers=engine)).json()
    assert second["entry_id"] == str(entries[1].id)

    # Loop on: the pre-loaded track is dropped and the engine is handed a repeat instead
    res = await client.post(
        f"/api/v1/stations/{station.id}/queue/loop", headers=auth_headers, json={"on": True}
    )
    assert res.status_code == 200 and ("/flush", None) in sent
    given = [first["entry_id"]]
    handed = []
    for _ in range(2):
        nxt = (await client.get(f"{base}/next?given={','.join(given)}", headers=engine)).json()
        handed.append(nxt["entry_id"])
        given.insert(0, nxt["entry_id"])
        # The start report for it has not arrived yet when the engine asks again
    assert len(set(handed)) == 2 and str(entries[1].id) not in handed
    res = await db_session.execute(select(QueueEntry).where(QueueEntry.id.in_([uuid.UUID(h) for h in handed])))
    rows = res.scalars().all()
    assert {r.asset_id for r in rows} == {entries[0].asset_id} and {r.source for r in rows} == {"loop"}

    # Loop off: the repeat that has not aired is dropped and the queue carries on
    await client.post(
        f"/api/v1/stations/{station.id}/queue/loop", headers=auth_headers, json={"on": False}
    )
    nxt = (await client.get(f"{base}/next?given={given[1]}", headers=engine)).json()
    assert nxt["entry_id"] == str(entries[1].id)


async def _rows(db_session, entries):
    res = await db_session.execute(select(QueueEntry).where(QueueEntry.id.in_([e.id for e in entries])))
    return {r.id: r for r in res.scalars().all()}


@pytest.mark.asyncio
async def test_a_block_goes_to_the_engine_in_its_order(client, db_session, engine_station):
    """Items of one block share a time; they are handed over (hard) or picked (soft) by position."""
    station, entries, sent = engine_station
    rows = await _rows(db_session, entries)
    soon = datetime.now(UTC) + timedelta(seconds=20)
    # Created in one order, positioned in the reverse one
    for pos, e in zip([9, 8, 7], entries[1:]):
        rows[e.id].preempt_at = soon
        rows[e.id].source = "block"
        rows[e.id].timing_mode = "hard"
        rows[e.id].position = pos
    await db_session.commit()

    await playout_engine.tick(db_session, station)
    order = [p["id"] for path, p in sent if path == "/timed"]
    assert order == [str(entries[3].id), str(entries[2].id), str(entries[1].id)]
    assert len({p["at"] for path, p in sent if path == "/timed"}) == 1

    # The same block as soft: once due, its items come out of pick_next by position
    past = datetime.now(UTC) - timedelta(seconds=5)
    for e in entries[1:]:
        rows[e.id].preempt_at = past
        rows[e.id].timing_mode = "soft"
    await db_session.commit()
    picked = await playout_engine.pick_next(db_session, station.id, set())
    assert picked.id == entries[3].id
    picked = await playout_engine.pick_next(db_session, station.id, {str(entries[3].id)})
    assert picked.id == entries[2].id


@pytest.mark.asyncio
async def test_hard_by_fades_the_song_once_when_the_wait_runs_out(client, db_session, engine_station):
    station, entries, sent = engine_station
    # Song 0 is on air
    await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[0].id), "lane": "main"},
    )
    rows = await _rows(db_session, entries)
    now = datetime.now(UTC)
    for pos, e in zip([2, 3], entries[2:]):
        rows[e.id].preempt_at = now - timedelta(minutes=3)
        rows[e.id].hard_by = now + timedelta(minutes=2)
        rows[e.id].source = "block"
        rows[e.id].timing_mode = "soft"
        rows[e.id].position = pos
    rows[entries[1].id].position = 4
    await db_session.commit()
    sent.clear()

    # Within the limit: nothing is faded; the block is simply what the engine gets next
    await playout_engine.tick(db_session, station)
    assert "/skip" not in [path for path, _ in sent]
    nxt = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert nxt.json()["entry_id"] == str(entries[2].id)

    # Limit passed: one fade, however many ticks follow
    for e in entries[2:]:
        rows[e.id].hard_by = now - timedelta(seconds=1)
    await db_session.commit()
    sent.clear()
    for _ in range(3):
        await playout_engine.tick(db_session, station)
    assert [path for path, _ in sent] == ["/skip"]

    # Once the block is on air, the rest of it is not cut short
    await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[2].id), "lane": "main"},
    )
    playout_engine.get_state(station.id).hard_by_done.clear()
    sent.clear()
    await playout_engine.tick(db_session, station)
    assert "/skip" not in [path for path, _ in sent]


@pytest.mark.asyncio
async def test_each_listed_station_has_its_own_engine(engine_station, monkeypatch):
    """One engine per station: commands and streams are addressed by station."""
    station, _, _ = engine_station
    other, unlisted = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", f"{station.id}, {other}")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "https://stream.example.com/control/")
    assert settings.engine_drives(station.id) and settings.engine_drives(other)
    assert not settings.engine_drives(unlisted)
    assert settings.engine_control_url(other) == f"https://stream.example.com/control/{other}"

    calls = []

    class FakeResp:
        status_code = 200

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            calls.append(url)
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(playout_engine, "_command", _real_command)
    assert await playout_engine.engine_flush(other)
    assert calls == [f"https://stream.example.com/control/{other}/flush"]


@pytest.mark.asyncio
async def test_commands_reuse_one_connection_to_the_engine(monkeypatch):
    """Each command used to open a new client: a TCP + TLS handshake to the engine server
    before every Next. One client is kept and its connection reused."""
    import httpx

    made = []
    real = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, json={"ok": True})

    class Counting(real):
        def __init__(self, **kw):
            made.append(kw)
            super().__init__(transport=httpx.MockTransport(handler))

    await playout_engine.close_client()
    monkeypatch.setattr(httpx, "AsyncClient", Counting)
    monkeypatch.setattr(playout_engine, "_command", _real_command)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "https://stream.example.com/control")
    station = uuid.uuid4()
    assert await playout_engine.engine_flush(station)
    assert await playout_engine.engine_skip(station, 0.5)
    assert (await playout_engine.engine_status(station)) == {"ok": True}
    assert len(made) == 1
    assert made[0]["limits"].keepalive_expiry == 30.0
    # A closed client (shutdown, or an error that closed it) is replaced, not reused
    await playout_engine.close_client()
    assert await playout_engine.engine_flush(station)
    assert len(made) == 2
    await playout_engine.close_client()


@pytest.mark.asyncio
async def test_a_redraw_never_loses_what_the_engine_plays(client, db_session, engine_station):
    """Live, 2026-11-16 ~19:50 ET on Main: a schedule redraw deleted the unplayed songs,
    including the one the engine had loaded. Its start report then matched nothing, the
    station showed nothing on air and listeners saw "Offline" while the engine played on."""
    from app.services.schedule_refresh_service import remove_automatic_songs

    station, entries, _ = engine_station
    first = (await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)).json()
    assert first["entry_id"] == str(entries[0].id)

    # The redraw keeps the song the engine holds; the rest go
    await remove_automatic_songs(db_session, station.id)
    await db_session.commit()
    left = (await db_session.execute(
        select(QueueEntry.id).where(QueueEntry.station_id == station.id)
    )).scalars().all()
    assert [str(i) for i in left] == [str(entries[0].id)]

    # Even if it is deleted some other way, its start report still puts it on air
    held = (await db_session.execute(select(QueueEntry).where(QueueEntry.id == entries[0].id))).scalar_one()
    asset_id = held.asset_id
    await db_session.delete(held)
    await db_session.commit()
    r = await client.post(
        f"/api/v1/playout/{station.id}/started", headers=HEADERS,
        json={"entry_id": first["entry_id"], "lane": "main"},
    )
    assert r.json() == {"ok": True, "known": True}
    playing = (await db_session.execute(
        select(QueueEntry).where(QueueEntry.station_id == station.id, QueueEntry.status == "playing")
        .execution_options(populate_existing=True)
    )).scalars().all()
    assert len(playing) == 1 and str(playing[0].asset_id) == str(asset_id)


async def _shiur_on_air(db_session, station, entries, rule=None):
    """entries[0] is a 70-minute shiur 20 minutes in; entries[2] is the time check in 20 s."""
    shiur = await db_session.get(Asset, entries[0].asset_id)
    shiur.asset_type = "daf_yomi"
    shiur.duration = 70 * 60.0
    now = datetime.now(UTC)
    on_air = await db_session.get(QueueEntry, entries[0].id)
    on_air.status = "playing"
    on_air.started_at = now - timedelta(minutes=20)
    on_air.preempt_at = now - timedelta(minutes=20)  # it aired as part of an earlier block
    timed = await db_session.get(QueueEntry, entries[2].id)
    timed.preempt_at = now + timedelta(seconds=20)
    if rule:
        station.automation_config = {"timed_during_long_items": rule}
    await db_session.commit()
    return on_air, timed


async def _resumes(db_session, station, asset_id):
    res = await db_session.execute(
        select(QueueEntry).where(
            QueueEntry.station_id == station.id,
            QueueEntry.asset_id == asset_id,
            QueueEntry.status == "pending",
        )
    )
    return res.scalars().all()


@pytest.mark.asyncio
async def test_time_check_pauses_a_long_shiur_and_it_resumes(client, db_session, engine_station):
    station, entries, sent = engine_station
    on_air, timed = await _shiur_on_air(db_session, station, entries)

    await playout_engine.tick(db_session, station)
    await playout_engine.tick(db_session, station)

    # The time check goes to the engine on its second, once
    assert len([p for path, p in sent if path == "/timed"]) == 1
    # The shiur is queued once, first in the music lane, from where the time check stops it
    resumes = await _resumes(db_session, station, on_air.asset_id)
    assert len(resumes) == 1
    assert resumes[0].source.startswith("resume")
    from app.models.queue_entry import resume_offset

    assert 20 * 60 - 10 < resume_offset(resumes[0]) < 20 * 60 + 25
    nxt = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert nxt.json()["entry_id"] == str(resumes[0].id)


@pytest.mark.asyncio
async def test_wait_rule_lets_a_queued_shiur_finish(client, db_session, engine_station):
    station, entries, sent = engine_station
    on_air, timed = await _shiur_on_air(db_session, station, entries, rule="wait")
    on_air.preempt_at = None  # it came up in the music lane
    await db_session.commit()

    await playout_engine.tick(db_session, station)

    assert not [p for path, p in sent if path == "/timed"]
    await db_session.refresh(timed)
    assert timed.timing_mode == "soft"
    assert not await _resumes(db_session, station, on_air.asset_id)


@pytest.mark.asyncio
async def test_wait_rule_moves_the_group_to_the_end_of_a_block_shiur(client, db_session, engine_station):
    # The shiur aired in the timed lane (part of a hard block): the time check stays
    # hard and starts the moment the shiur ends, not after a song in the music lane.
    station, entries, sent = engine_station
    on_air, timed = await _shiur_on_air(db_session, station, entries, rule="wait")

    await playout_engine.tick(db_session, station)

    assert not [p for path, p in sent if path == "/timed"]
    await db_session.refresh(timed)
    assert timed.timing_mode != "soft"
    started = on_air.started_at if on_air.started_at.tzinfo else on_air.started_at.replace(tzinfo=UTC)
    moved = timed.preempt_at if timed.preempt_at.tzinfo else timed.preempt_at.replace(tzinfo=UTC)
    assert abs((moved - started).total_seconds() - 70 * 60) < 1
    assert not await _resumes(db_session, station, on_air.asset_id)


@pytest.mark.asyncio
async def test_cut_rule_cuts_without_resuming(client, db_session, engine_station):
    station, entries, sent = engine_station
    on_air, timed = await _shiur_on_air(db_session, station, entries, rule="cut")

    await playout_engine.tick(db_session, station)

    assert len([p for path, p in sent if path == "/timed"]) == 1
    assert not await _resumes(db_session, station, on_air.asset_id)


@pytest.mark.asyncio
async def test_a_song_on_air_is_not_resumed(client, db_session, engine_station):
    station, entries, sent = engine_station
    now = datetime.now(UTC)
    on_air = await db_session.get(QueueEntry, entries[0].id)
    on_air.status = "playing"
    on_air.started_at = now - timedelta(seconds=60)
    timed = await db_session.get(QueueEntry, entries[2].id)
    timed.preempt_at = now + timedelta(seconds=20)
    await db_session.commit()

    await playout_engine.tick(db_session, station)

    assert len([p for path, p in sent if path == "/timed"]) == 1
    assert not await _resumes(db_session, station, on_air.asset_id)


@pytest.mark.asyncio
async def test_an_item_running_past_its_end_raises_one_alert(client, db_session, engine_station):
    from app.models.alert import Alert

    station, entries, sent = engine_station
    on_air = await db_session.get(QueueEntry, entries[0].id)
    on_air.status = "playing"
    on_air.started_at = datetime.now(UTC) - timedelta(seconds=180 + 45)
    await db_session.commit()

    await playout_engine.tick(db_session, station)
    await playout_engine.tick(db_session, station)

    alerts = (await db_session.execute(
        select(Alert).where(Alert.title == "Playout engine did not move on")
    )).scalars().all()
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_start_report_puts_the_item_on_air_first_without_renumbering(client, db_session, engine_station):
    """The start report used to renumber the whole queue (about 900 rows with a 48 h
    queue, 0.9 to 1.1 s of every report on production, 2026-11-18). Now only the item
    on air moves, to just before the first waiting one; the waiting rows keep their
    places and their order."""
    station, entries, sent = engine_station
    third = await db_session.get(QueueEntry, entries[2].id)
    third.position = 40
    await db_session.commit()
    before = {e.id: (await db_session.get(QueueEntry, e.id)).position for e in entries[1:]}
    res = await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[0].id), "lane": "main", "title": "x"},
    )
    assert res.status_code == 200
    rows = (await db_session.execute(
        select(QueueEntry.id, QueueEntry.position, QueueEntry.status)
        .where(QueueEntry.station_id == station.id)
        .order_by(QueueEntry.position)
        .execution_options(populate_existing=True)
    )).all()
    assert rows[0][0] == entries[0].id and rows[0][2] == "playing"
    waiting = [r for r in rows if r[2] == "pending"]
    assert {r[0]: r[1] for r in waiting} == before
    assert rows[0][1] == waiting[0][1] - 1


@pytest.mark.asyncio
async def test_an_item_started_out_of_order_goes_before_the_waiting_ones(client, db_session, engine_station):
    """A timed item far down the queue goes on air: it comes first, and Play next still
    lands right after it (Play next takes the place after the item on air)."""
    station, entries, sent = engine_station
    late = await db_session.get(QueueEntry, entries[-1].id)
    late.position = 90
    await db_session.commit()
    res = await client.post(
        f"/api/v1/playout/{station.id}/started",
        headers=HEADERS,
        json={"entry_id": str(entries[-1].id), "lane": "timed", "title": "x"},
    )
    assert res.status_code == 200
    rows = (await db_session.execute(
        select(QueueEntry.id, QueueEntry.position, QueueEntry.status)
        .where(QueueEntry.station_id == station.id, QueueEntry.status.in_(["pending", "playing"]))
        .order_by(QueueEntry.position)
        .execution_options(populate_existing=True)
    )).all()
    assert rows[0][0] == entries[-1].id and rows[0][2] == "playing"
    assert [r[0] for r in rows[1:]] == [e.id for e in entries[:-1]]


@pytest.mark.asyncio
async def test_next_never_hands_out_what_just_started(client, db_session, engine_station):
    # The start report is still being written (not committed) when the engine asks for the
    # next track: the item on air must not come back as "next" (it would play twice).
    station, entries, sent = engine_station
    state = playout_engine.get_state(station.id)
    state.recent_started.append(str(entries[0].id))
    nxt = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert nxt.json()["entry_id"] == str(entries[1].id)


@pytest.mark.asyncio
async def test_a_due_soft_item_replaces_the_cued_song_near_the_end(client, db_session, engine_station):
    station, entries, sent = engine_station
    now = datetime.now(UTC)
    on_air = await db_session.get(QueueEntry, entries[0].id)
    on_air.status = "playing"
    on_air.started_at = now - timedelta(seconds=160)  # 20 s left
    await db_session.commit()
    nxt = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert nxt.json()["entry_id"] == str(entries[1].id)
    soft = await db_session.get(QueueEntry, entries[3].id)
    soft.source = "ad_slot"
    soft.preempt_at = now - timedelta(seconds=1)
    await db_session.commit()

    await playout_engine.tick(db_session, station)

    assert [path for path, _ in sent].count("/flush") == 1


@pytest.mark.asyncio
async def test_a_reshuffled_song_keeps_the_cued_one_near_the_end(client, db_session, engine_station):
    station, entries, sent = engine_station
    now = datetime.now(UTC)
    on_air = await db_session.get(QueueEntry, entries[0].id)
    on_air.status = "playing"
    on_air.started_at = now - timedelta(seconds=160)  # 20 s left
    await db_session.commit()
    nxt = await client.get(f"/api/v1/playout/{station.id}/next", headers=HEADERS)
    assert nxt.json()["entry_id"] == str(entries[1].id)
    moved = await db_session.get(QueueEntry, entries[3].id)
    moved.position = 0
    await db_session.commit()

    await playout_engine.tick(db_session, station)

    assert "/flush" not in [path for path, _ in sent]


async def _on_air_ending(db_session, entries, ends_in: float, hard_in: float):
    """entries[0] on air with `ends_in` s left; entries[3] a hard item due in `hard_in` s."""
    now = datetime.now(UTC)
    res = await db_session.execute(select(QueueEntry).where(QueueEntry.id.in_([entries[0].id, entries[3].id])))
    rows = {r.id: r for r in res.scalars().all()}
    rows[entries[0].id].status = "playing"
    rows[entries[0].id].started_at = now - timedelta(seconds=180.0 - ends_in)
    rows[entries[3].id].preempt_at = now + timedelta(seconds=hard_in)
    rows[entries[3].id].timing_mode = "hard"
    await db_session.commit()


async def test_no_song_is_handed_out_seconds_before_a_hard_item(client, db_session, engine_station):
    # Jerusalem 23:00Z 2026-11-17: the song before the hour ended 1.9 s early, a 266 s
    # song was handed out and chopped after 2.4 s. The engine gets nothing instead and
    # stays quiet until the time check (its hold lane).
    station, entries, sent = engine_station
    # The song on air would change (1 s crossfade) at the hard item itself
    await _on_air_ending(db_session, entries, ends_in=60.0, hard_in=59.0)
    url = f"/api/v1/playout/{station.id}/next?proto=3"
    assert (await client.get(url, headers=HEADERS)).status_code == 204
    assert await _status(db_session, entries[1]) == "pending"
    # An engine without the hold lane would play emergency music: it still gets the song
    old = await client.get(f"/api/v1/playout/{station.id}/next?proto=2", headers=HEADERS)
    assert old.json()["entry_id"] == str(entries[1].id)


async def test_next_gets_a_song_while_the_engine_is_held_before_a_hard_item(client, db_session, engine_station):
    # Next pressed while the engine holds nothing (the song on air ends just before the
    # hard item): the engine waits for a file before it skips, so it must be handed one.
    # It was handed nothing and skipped into emergency music (harness next_while_held).
    station, entries, sent = engine_station
    await _on_air_ending(db_session, entries, ends_in=60.0, hard_in=59.0)
    url = f"/api/v1/playout/{station.id}/next?proto=3"
    assert (await client.get(url, headers=HEADERS)).status_code == 204
    playout_engine.get_state(station.id).skip_requested = True   # what engine_skip sets
    assert (await client.get(url, headers=HEADERS)).json()["entry_id"] == str(entries[1].id)


async def test_a_restarted_engine_gets_a_song_although_the_cut_item_would_end_before_a_hard_item(
    client, db_session, engine_station
):
    # Harness engine_crash_restart 2026-11-18: the engine restarted while a song played
    # and that song would have ended a second before the hour. The backend kept saying
    # "it would start 1.1 s before the hard item" about a song no longer on air, so the
    # restarted engine got nothing for 20 s and played emergency music.
    station, entries, sent = engine_station
    await _on_air_ending(db_session, entries, ends_in=60.0, hard_in=59.0)
    url = f"/api/v1/playout/{station.id}/next?proto=3"
    assert (await client.post(f"/api/v1/playout/{station.id}/hello", headers=HEADERS)).status_code == 200
    assert (await client.get(url, headers=HEADERS)).json()["entry_id"] == str(entries[1].id)
    # Once something is on air again the rule is back in force
    assert (await client.post(
        f"/api/v1/playout/{station.id}/started", headers=HEADERS,
        json={"entry_id": str(entries[1].id), "lane": "main"},
    )).status_code == 200
    assert playout_engine.get_state(station.id).fresh_start is False


async def test_a_song_is_still_handed_out_when_there_is_room_or_the_hard_item_cuts_in(
    client, db_session, engine_station
):
    station, entries, sent = engine_station
    url = f"/api/v1/playout/{station.id}/next?proto=3"
    # 18 s of room after the song change: the next song is worth starting
    await _on_air_ending(db_session, entries, ends_in=60.0, hard_in=77.0)
    assert (await client.get(url, headers=HEADERS)).json()["entry_id"] == str(entries[1].id)
    # The hard item is due while the song on air still plays: the song after it is
    # needed as soon as the timed item ends, so it is handed out as before
    playout_engine.reset_state()
    await _on_air_ending(db_session, entries, ends_in=60.0, hard_in=30.0)
    assert (await client.get(url, headers=HEADERS)).json()["entry_id"] == str(entries[1].id)


async def test_the_hold_counts_a_song_whose_start_report_has_not_arrived(client, db_session, engine_station):
    # The engine asks the moment its pre-loaded song starts, before the start report
    # for it is in: that song (180 s, hands over at 179 s) is the one that ends before
    # the hard item, not the one still shown on air.
    station, entries, sent = engine_station
    url = f"/api/v1/playout/{station.id}/next?proto=3"
    await _on_air_ending(db_session, entries, ends_in=3.0, hard_in=183.0)
    first = await client.get(f"{url}&given=", headers=HEADERS)
    assert first.json()["entry_id"] == str(entries[1].id)
    # entries[1] is starting now (still pending for us) and changes 2 s before the item
    again = await client.get(f"{url}&given={entries[1].id}", headers=HEADERS)
    assert again.status_code == 204
    assert await _status(db_session, entries[2]) == "pending"


async def test_a_song_the_engine_already_holds_is_taken_back_before_a_hard_item(client, db_session, engine_station):
    # Around a Next the engine can load its next song before the one on air has even
    # started, so the rule in /next never saw it: the tick takes it back.
    station, entries, sent = engine_station
    state = playout_engine.get_state(station.id)
    state.engine_proto = 3
    await _on_air_ending(db_session, entries, ends_in=60.0, hard_in=59.5)
    state.cued_entry_id = str(entries[1].id)
    st = await db_session.get(Station, station.id)
    await playout_engine.tick(db_session, st)
    assert ("/flush", None) in sent
    assert state.cued_entry_id is None
    # With room for the song (20 s before the item) it is left alone
    sent.clear()
    await _on_air_ending(db_session, entries, ends_in=60.0, hard_in=80.0)
    state.cued_entry_id = str(entries[1].id)
    await playout_engine.tick(db_session, st)
    assert ("/flush", None) not in sent
