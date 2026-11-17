"""Panic (POST /stations/{id}/queue/panic) on both players: checklist rows F-MODES-10..14,
-31..-33 (docs/test-checklist.md section N, wiki/station-modes.md).

Panic cuts whatever is on air with no fade, even a timed item Next protects, ends Loop,
and starts the next item (filling an empty queue first). On an engine station it ends
Loop, flushes the engine's pre-loaded track and sends a hard skip.
"""
import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.api.v1.queue as queue_module
from app.models.alert import Alert
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.live_show import LiveShow, LiveShowStatus
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from tests.conftest import TestSessionLocal
from tests import test_engine_backend as _engine_tests
from tests.test_engine_backend import _add
from tests.timing import limit

eng = _engine_tests.eng  # the engine fixture (faked engine commands)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(queue_module, "_replenish_queue", nothing)
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda *a, **k: None)
    monkeypatch.setattr(queue_module, "_broadcast_now_playing_entry", nothing)
    queue_module._automatic_change.clear()
    yield
    queue_module._automatic_change.clear()


async def _browser_station(db, *, on_air_kind="music", on_air_duration=200.0, songs=3, **on_air_fields):
    station = Station(id=uuid.uuid4(), name="KBR Jerusalem", timezone="Asia/Jerusalem")
    db.add(station)
    on_air_asset = Asset(id=uuid.uuid4(), title="On air", duration=on_air_duration,
                         file_path="https://cdn.example.com/on-air.mp3", asset_type=on_air_kind)
    others = [Asset(id=uuid.uuid4(), title=f"Song {i}", duration=200.0,
                    file_path=f"https://cdn.example.com/{i}.mp3", asset_type="music") for i in range(songs)]
    db.add_all([on_air_asset, *others])
    await db.flush()
    on_air_fields.setdefault("source", "auto")
    on_air = QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=on_air_asset.id, position=1,
                        status="playing", started_at=datetime.now(UTC) - timedelta(seconds=5), **on_air_fields)
    pending = [QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=a.id, position=i + 2,
                          status="pending", source="auto") for i, a in enumerate(others)]
    db.add_all([on_air, *pending])
    await db.commit()
    return station, on_air, pending


async def _row(db, entry_id):
    return (await db.execute(
        select(QueueEntry).where(QueueEntry.id == entry_id).execution_options(populate_existing=True)
    )).scalar_one_or_none()


async def _playing(db, station_id):
    return (await db.execute(
        select(QueueEntry).where(QueueEntry.station_id == station_id, QueueEntry.status == "playing")
        .execution_options(populate_existing=True)
    )).scalars().all()


async def _panic_alerts(db, station_id):
    return (await db.execute(
        select(Alert).where(Alert.station_id == station_id, Alert.title == "Panic pressed in the control room")
        .execution_options(populate_existing=True)
    )).scalars().all()


# ── F-MODES-10 / -11 / -14: engine stations ──────────────────────────────────


@pytest.mark.asyncio
async def test_panic_on_engine_ends_loop_flushes_and_cuts_a_timed_item(client, auth_headers, db_session, eng):
    """F-MODES-10 (+ -11 engine): a time check on the engine's timed lane is on air and the
    station is looping it. Next is refused (timed item); Panic ends Loop, flushes the
    pre-loaded repeat, then sends a hard skip (fade 0), answering within 1.5 s."""
    station, entries, engine, _ = eng
    clock = await _add(db_session, station, title="Time", duration=8.0, kind="time", position=0,
                       status="playing", timing_mode="hard", preempt_at=datetime.now(UTC) - timedelta(seconds=2),
                       started_at=datetime.now(UTC) - timedelta(seconds=2))
    st = await db_session.get(Station, station.id)
    st.automation_config = {"loop_asset_id": str(clock.asset_id)}
    db_session.add(QueueEntry(id=uuid.uuid4(), station_id=station.id, asset_id=clock.asset_id, position=0,
                              status="pending", source="loop"))
    await db_session.commit()
    url = f"/api/v1/stations/{station.id}/queue"

    assert (await client.post(f"{url}/skip", headers=auth_headers)).json().get("protected") is True
    assert engine.paths() == []

    t0 = time.perf_counter()
    r = await client.post(f"{url}/panic", headers=auth_headers)
    assert time.perf_counter() - t0 < limit(1.5)
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "Skipped"
    # Flush first (the pre-loaded repeat must not come back), then the cut with no fade
    assert engine.sent == [("/flush", None), ("/skip", {"fade": 0.0})]
    assert queue_module.SKIP_FADE_SECONDS["hard"] == 0.0
    await db_session.refresh(st)
    assert "loop_asset_id" not in (st.automation_config or {})
    loops = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.source == "loop"))).scalars().all()
    assert loops == []
    assert len(await _panic_alerts(db_session, station.id)) == 1


@pytest.mark.asyncio
async def test_panic_with_engine_not_answering_says_so(client, auth_headers, db_session, eng):
    """F-MODES-14 (backend half): the engine does not answer; the response and the alert both
    say "The playout engine did not respond" (the page shows the response message as a red
    toast, Dashboard.tsx; not checked here)."""
    station, _, engine, _ = eng
    engine.ok = False
    r = await client.post(f"/api/v1/stations/{station.id}/queue/panic", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["message"] == "The playout engine did not respond"
    alerts = await _panic_alerts(db_session, station.id)
    assert len(alerts) == 1
    assert "The playout engine did not respond" in alerts[0].message
    assert "cut what was on air" not in alerts[0].message


# ── F-MODES-11 / -12: browser-player stations ────────────────────────────────


@pytest.mark.asyncio
async def test_panic_ends_loop_on_the_browser_player(client, auth_headers, db_session):
    """F-MODES-11: the looped item is cut and does not come back, not even at the next
    song's natural end."""
    station, on_air, pending = await _browser_station(db_session)
    url = f"/api/v1/stations/{station.id}/queue"
    r = await client.post(f"{url}/loop", json={"on": True}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["asset_id"] == str(on_air.asset_id)

    body = (await client.post(f"{url}/panic", headers=auth_headers)).json()
    assert body["message"] == "Skipped"
    assert await queue_module.loop_asset_id(db_session, station.id) is None
    assert (await _row(db_session, on_air.id)).status == "skipped"
    nxt = await _row(db_session, pending[0].id)
    assert nxt.status == "playing"

    # The next song ends by itself: what follows is the queue, not the looped item
    nxt.started_at = datetime.now(UTC) - timedelta(seconds=250)
    await db_session.commit()
    after = await queue_module._check_advance(db_session, station.id)
    assert after.id == pending[1].id
    looped = (await db_session.execute(select(QueueEntry).where(
        QueueEntry.station_id == station.id, QueueEntry.asset_id == on_air.asset_id,
        QueueEntry.status.in_(["pending", "playing"])))).scalars().all()
    assert looped == []


@pytest.mark.asyncio
async def test_panic_with_empty_up_next_fills_then_plays(client, auth_headers, db_session, monkeypatch):
    """F-MODES-12: nothing else in Up next: the queue is filled and its first new item
    goes on air in the same call."""
    station, on_air, _ = await _browser_station(db_session, songs=0)
    fresh = Asset(id=uuid.uuid4(), title="Fresh", duration=200.0, file_path="https://cdn.example.com/f.mp3",
                  asset_type="music")
    db_session.add(fresh)
    await db_session.commit()
    added = []

    async def refill(db, station_id):
        e = QueueEntry(id=uuid.uuid4(), station_id=station_id, asset_id=fresh.id, position=5,
                       status="pending", source="auto")
        db.add(e)
        added.append(e.id)
        await db.flush()

    monkeypatch.setattr(queue_module, "_replenish_queue", refill)
    body = (await client.post(f"/api/v1/stations/{station.id}/queue/panic", headers=auth_headers)).json()
    assert body["message"] == "Skipped"
    assert body["now_playing"] == str(fresh.id)
    assert len(added) == 1
    assert [e.id for e in await _playing(db_session, station.id)] == added


# ── F-MODES-31 / -32: blackout and live show ─────────────────────────────────


@pytest.mark.asyncio
async def test_panic_never_puts_music_inside_a_blackout(client, auth_headers, db_session):
    """F-MODES-31. Decision: Panic during a blackout cuts the silence item on air and starts
    the next silence (or nothing); music must never air inside the blackout."""
    station, on_air, pending = await _browser_station(db_session, on_air_kind="silence", on_air_duration=3600.0)
    db_session.add(HolidayWindow(id=uuid.uuid4(), name="Shabbos", reason="shabbos", is_blackout=True,
                                 start_datetime=datetime.now(UTC) - timedelta(hours=1),
                                 end_datetime=datetime.now(UTC) + timedelta(hours=20),
                                 affected_stations={"station_ids": [str(station.id)]}))
    await db_session.commit()
    await client.post(f"/api/v1/stations/{station.id}/queue/panic", headers=auth_headers)
    for e in await _playing(db_session, station.id):
        asset = await db_session.get(Asset, e.asset_id)
        assert asset.asset_type == "silence", f"{asset.title} went on air during the blackout"


@pytest.mark.asyncio
async def test_panic_during_a_live_show_starts_no_music(client, auth_headers, db_session):
    """F-MODES-32. Decision: the live show owns the air; Panic starts nothing from the queue
    while it runs (ending the show is the host's End show)."""
    from app.services import live_show_service

    station, on_air, pending = await _browser_station(db_session)
    now = datetime.now(UTC)
    show = LiveShow(id=uuid.uuid4(), station_id=station.id, title="Live", status=LiveShowStatus.SCHEDULED,
                    scheduled_start=now, scheduled_end=now + timedelta(hours=1))
    db_session.add(show)
    await db_session.commit()
    await live_show_service.start_show(db_session, show.id)
    await db_session.commit()
    assert await _playing(db_session, station.id) == []

    await client.post(f"/api/v1/stations/{station.id}/queue/panic", headers=auth_headers)
    assert await _playing(db_session, station.id) == []


# ── F-MODES-33: two control rooms at once ────────────────────────────────────


@pytest.mark.asyncio
async def test_two_panics_at_once_cut_one_item(db_session):
    """F-MODES-33: two control rooms press Panic at the same moment, each with its own
    database session: the bad item is cut and the next song stays on air."""
    station, on_air, pending = await _browser_station(db_session)
    user = SimpleNamespace(email="dj@example.com")

    async def press():
        async with TestSessionLocal() as session:
            return await queue_module.panic(station.id, db=session, user=user)

    await asyncio.gather(press(), press())
    assert (await _row(db_session, on_air.id)).status == "skipped"
    assert (await _row(db_session, pending[0].id)).status == "playing"
    assert (await _row(db_session, pending[1].id)).status == "pending"
