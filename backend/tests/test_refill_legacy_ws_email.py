"""The queue top-up after a song change, the legacy Liquidsoap switch, the browser
players' mix-over / fade messages, and transactional email on or off.

Rows: B-JOB-73, B-JOB-50, B-SET-26, B-WEBSOCKET-4, B-SET-33."""
import asyncio
import json
import logging
import sys
import types
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

import app.api.v1.queue as queue_module
from app.api.v1 import websocket as ws
from app.config import settings
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from tests.test_queue_advance import _station_with_two_tracks


# ── B-JOB-73: replenish after an advance ──────────────────────────────────


@pytest.fixture
def refills(monkeypatch):
    """Record _replenish_soon calls instead of starting a background refill."""
    calls = []
    monkeypatch.setattr(queue_module, "_replenish_soon", lambda sid, rebroadcast=False: calls.append(str(sid)))
    return calls


@pytest.mark.asyncio
async def test_advance_tops_up_the_queue_but_not_during_blackout(db_session, refills):
    """B-JOB-73: a song ending starts the next one and asks for a top-up; during Shabbos
    (silence on air) the advance starts the next silence and asks for no top-up."""
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=200.0)
    started = await queue_module._check_advance(db_session, station.id)
    assert started is not None and started.id == pending.id
    assert refills == [str(station.id)]

    refills.clear()
    quiet = Station(id=uuid.uuid4(), name="Shabbos FM", timezone="America/New_York")
    silence = Asset(id=uuid.uuid4(), title="Silence (5 min)", duration=300.0, asset_type="silence",
                    file_path="system/silence_5min.mp3")
    db_session.add_all([quiet, silence])
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all([
        HolidayWindow(id=uuid.uuid4(), name="Shabbos", start_datetime=now - timedelta(hours=2),
                      end_datetime=now + timedelta(hours=20), is_blackout=True,
                      affected_stations={"station_ids": [str(quiet.id)]}),
        QueueEntry(id=uuid.uuid4(), station_id=quiet.id, asset_id=silence.id, position=1, status="playing",
                   started_at=now - timedelta(seconds=301)),
        QueueEntry(id=uuid.uuid4(), station_id=quiet.id, asset_id=silence.id, position=2, status="pending"),
    ])
    await db_session.commit()
    nxt = await queue_module._check_advance(db_session, quiet.id)
    assert nxt is not None and nxt.status == "playing" and nxt.asset_id == silence.id
    assert refills == []


class _Session:
    async def commit(self):
        return None

    async def execute(self, *a, **k):
        raise AssertionError("not used without rebroadcast")


@pytest.fixture
def fake_refill(monkeypatch):
    """_replenish_soon with a refill that takes 50 ms and records overlap per station."""
    import app.db.engine as db_engine

    @asynccontextmanager
    async def factory():
        yield _Session()

    monkeypatch.setattr(db_engine, "async_session_factory", factory)
    state = {"running": {}, "max": {}, "any_overlap": False, "total": 0}

    async def slow_refill(db, station_id):
        key = str(station_id)
        state["running"][key] = state["running"].get(key, 0) + 1
        state["max"][key] = max(state["max"].get(key, 0), state["running"][key])
        if len([k for k, v in state["running"].items() if v]) > 1:
            state["any_overlap"] = True
        await asyncio.sleep(0.05)
        state["running"][key] -= 1
        state["total"] += 1

    monkeypatch.setattr(queue_module, "_replenish_queue", slow_refill)
    return state


async def _drain():
    while queue_module._background:
        await asyncio.gather(*list(queue_module._background))


@pytest.mark.asyncio
async def test_refills_of_one_station_run_one_at_a_time(fake_refill):
    """B-JOB-73: refills of the same station are serialized (_refill_lock); another
    station's refill does not wait for them."""
    a, b = uuid.uuid4(), uuid.uuid4()
    for _ in range(3):
        queue_module._replenish_soon(a)
    queue_module._replenish_soon(b)
    await _drain()
    assert fake_refill["total"] == 4
    assert fake_refill["max"][str(a)] == 1
    assert fake_refill["any_overlap"] is True          # b ran beside a's queue of refills


@pytest.mark.asyncio
async def test_slow_refill_is_logged(fake_refill, monkeypatch, caplog):
    """B-JOB-73: a refill slower than SLOW_REFILL_SECONDS is logged as a warning; a quick one is not."""
    sid = uuid.uuid4()
    with caplog.at_level(logging.WARNING, logger="app.api.v1.queue"):
        queue_module._replenish_soon(sid)
        await _drain()
    assert "queue refill took" not in caplog.text
    monkeypatch.setattr(queue_module, "SLOW_REFILL_SECONDS", 0.01)
    with caplog.at_level(logging.WARNING, logger="app.api.v1.queue"):
        queue_module._replenish_soon(sid)
        await _drain()
    assert f"Station {sid}: queue refill took" in caplog.text


# ── B-JOB-50 / B-SET-26: legacy Liquidsoap telnet ─────────────────────────


@pytest.fixture
def telnet(monkeypatch):
    from app.services import liquidsoap_client

    sent = []

    async def fake_send(command):
        sent.append(command)
        return "ok"

    monkeypatch.setattr(liquidsoap_client, "_send_command", fake_send)
    monkeypatch.setattr(queue_module, "_replenish_queue", lambda db, sid: asyncio.sleep(0))
    return sent


async def _legacy_run(client, auth_headers, db_session):
    """One scheduler pass, a track push and an operator's Next."""
    from app.services.scheduler_engine import SchedulerEngine

    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    engine = SchedulerEngine()
    await engine._check_all_stations(db_session)
    await engine._push_to_liquidsoap("https://cdn.example.com/0.mp3", station.id)
    r = await client.post(f"/api/v1/stations/{station.id}/queue/skip", headers=auth_headers)
    assert r.status_code == 200, r.text
    for h in engine._advance_timers.values():
        h.cancel()


@pytest.mark.asyncio
async def test_legacy_liquidsoap_is_off_by_default(client, auth_headers, db_session, telnet):
    """B-SET-26 / B-JOB-50: LIQUIDSOAP_ENABLED defaults to off; then no health check,
    push or skip is ever sent over telnet."""
    from app.config import Settings

    assert Settings.model_fields["LIQUIDSOAP_ENABLED"].default is False
    assert settings.liquidsoap_enabled is False
    await _legacy_run(client, auth_headers, db_session)
    assert telnet == []


@pytest.mark.asyncio
async def test_legacy_liquidsoap_when_switched_on(client, auth_headers, db_session, telnet, monkeypatch):
    """B-SET-26 / B-JOB-50: switched on (with a host), the scheduler checks it is alive, a
    track is pushed and Next sends a skip; a host alone does not switch it on."""
    monkeypatch.setattr(settings, "LIQUIDSOAP_HOST", "ls.invalid")
    assert settings.liquidsoap_enabled is False
    monkeypatch.setattr(settings, "LIQUIDSOAP_ENABLED", True)
    await _legacy_run(client, auth_headers, db_session)
    assert "version" in telnet
    assert "main_queue.push https://cdn.example.com/0.mp3" in telnet
    assert "main_queue.skip" in telnet


# ── B-WEBSOCKET-4: mix_over and fade over the now-playing socket ──────────


class _Socket:
    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(json.loads(text))


@pytest.fixture
def listener():
    sockets = []

    def attach(station_id):
        s = _Socket()
        ws.manager.active_connections.setdefault(str(station_id), set()).add(s)
        sockets.append((str(station_id), s))
        return s

    yield attach
    for sid, s in sockets:
        ws.manager.disconnect(sid, s)
        ws._last_now_playing.pop(sid, None)
        ws._latest_start.pop(sid, None)


@pytest.mark.asyncio
async def test_mix_over_and_fade_reach_the_browser_players(client, auth_headers, db_session, listener, refills):
    """B-WEBSOCKET-4: on a browser station, mix-over sends every open player a mix_over
    message with the item's URL, and Next with a fade choice sends the new now_playing with
    change_fade_seconds (hard 0, fade 0.5, soft 3)."""
    station, playing, pending = await _station_with_two_tracks(db_session, elapsed_seconds=10.0)
    jingle = Asset(id=uuid.uuid4(), title="Sting", duration=4.0, asset_type="jingle",
                   file_path="https://cdn.example.com/sting.mp3", metadata_extra={"replay_gain_db": -2.5})
    db_session.add(jingle)
    await db_session.commit()
    sock = listener(station.id)
    other = listener(uuid.uuid4())

    r = await client.post(f"/api/v1/stations/{station.id}/queue/mix-over", headers=auth_headers,
                          json={"asset_id": str(jingle.id)})
    assert r.status_code == 200, r.text
    mixes = [m for m in sock.messages if m["type"] == "mix_over"]
    assert len(mixes) == 1
    assert mixes[0]["data"]["url"] == "https://cdn.example.com/sting.mp3"
    assert mixes[0]["data"]["asset_id"] == str(jingle.id) and mixes[0]["data"]["station_id"] == str(station.id)
    assert other.messages == []                         # only this station's players

    r = await client.post(f"/api/v1/stations/{station.id}/queue/skip?fade=soft", headers=auth_headers)
    assert r.status_code == 200, r.text
    changes = [m for m in sock.messages if m["type"] == "now_playing"]
    assert changes and changes[-1]["data"]["asset_id"] == str(pending.asset_id)
    assert changes[-1]["data"]["change_fade_seconds"] == 3.0


# ── B-SET-33: Resend ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_email_sent_only_with_resend_key(monkeypatch, caplog):
    """B-SET-33: RESEND_API_KEY unset -> send_email skips (False, logged) and never calls
    Resend; set -> one Resend call from RESEND_FROM_EMAIL; a Resend error -> False, no raise."""
    from app.services import email_service

    calls = []
    fake = types.SimpleNamespace(api_key=None, Emails=types.SimpleNamespace(send=lambda p: calls.append(p)))
    monkeypatch.setitem(sys.modules, "resend", fake)

    monkeypatch.setattr(settings, "RESEND_API_KEY", "")
    with caplog.at_level(logging.INFO, logger="app.services.email_service"):
        assert await email_service.send_email("a@example.com", "Hi", "<p>x</p>") is False
    assert calls == [] and "Email skipped" in caplog.text

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_test_only")
    assert await email_service.send_campaign_status_update("a@example.com", "Spring <b>", "in_review") is True
    assert len(calls) == 1 and fake.api_key == "re_test_only"
    assert calls[0]["to"] == ["a@example.com"] and calls[0]["from"] == settings.RESEND_FROM_EMAIL
    assert "Spring &lt;b&gt;" in calls[0]["html"]

    def boom(p):
        raise RuntimeError("resend down")

    fake.Emails.send = boom
    assert await email_service.send_email("a@example.com", "Hi", "<p>x</p>") is False
