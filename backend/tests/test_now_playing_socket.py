"""The listener now-playing socket (/ws/now-playing/{station}): join cache, slow
listeners dropped, keep-alive ping and pong, and the first message (rows V1-121..124)."""
import asyncio
import time

import pytest
from fastapi import WebSocketDisconnect

import app.api.v1.websocket as ws_mod
from tests.timing import limit

pytestmark = pytest.mark.asyncio


class FakeSocket:
    """Stands in for a Starlette WebSocket. `incoming` holds what the client sends;
    once empty, receive waits (for `hang` seconds) and then the client leaves."""

    def __init__(self, incoming=(), send_delay=0.0, hang=10.0):
        self.incoming = list(incoming)
        self.sent = []
        self.send_delay = send_delay
        self.hang = hang
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        if self.send_delay:
            await asyncio.sleep(self.send_delay)
        self.sent.append(text)

    async def send_json(self, obj):
        self.sent.append(obj)

    async def receive_text(self):
        if self.incoming:
            return self.incoming.pop(0)
        await asyncio.sleep(self.hang)
        raise WebSocketDisconnect()


@pytest.fixture(autouse=True)
def _clean_state():
    for d in (ws_mod._join_cache, ws_mod._join_locks, ws_mod._last_now_playing, ws_mod._latest_start, ws_mod._np_generation):
        d.clear()
    ws_mod.manager.active_connections.clear()
    yield
    for d in (ws_mod._join_cache, ws_mod._join_locks, ws_mod._last_now_playing, ws_mod._latest_start, ws_mod._np_generation):
        d.clear()
    ws_mod.manager.active_connections.clear()


# ── V1-121: join cache ──

async def test_many_listeners_joining_at_once_read_the_database_once(monkeypatch):
    reads = []

    async def fake_read(db, station_id):
        reads.append(station_id)
        await asyncio.sleep(0.05)
        return {"asset_id": "a1", "started_at": "2026-01-01T00:00:00+00:00"}

    monkeypatch.setattr(ws_mod, "_queue_now_playing", fake_read)
    results = await asyncio.gather(*(ws_mod._cached_queue_now_playing("st", db=object()) for _ in range(50)))
    assert len(reads) == 1
    assert all(r["asset_id"] == "a1" for r in results)


async def test_join_cache_lasts_5_seconds(monkeypatch):
    reads = []

    async def fake_read(db, station_id):
        reads.append(1)
        return {"asset_id": f"a{len(reads)}"}

    clock = [1000.0]
    monkeypatch.setattr(ws_mod, "_queue_now_playing", fake_read)
    monkeypatch.setattr(ws_mod.time, "monotonic", lambda: clock[0])
    assert ws_mod.JOIN_CACHE_SECONDS == 5.0
    await ws_mod._cached_queue_now_playing("st", db=object())
    clock[0] += 4.9
    assert (await ws_mod._cached_queue_now_playing("st", db=object()))["asset_id"] == "a1"
    clock[0] += 0.2
    assert (await ws_mod._cached_queue_now_playing("st", db=object()))["asset_id"] == "a2"
    assert len(reads) == 2


async def test_song_change_empties_the_join_cache(monkeypatch):
    reads = []

    async def fake_read(db, station_id):
        reads.append(1)
        return {"asset_id": f"a{len(reads)}"}

    monkeypatch.setattr(ws_mod, "_queue_now_playing", fake_read)
    await ws_mod._cached_queue_now_playing("st", db=object())
    await ws_mod.broadcast_now_playing_update("st", {"asset_id": "a2", "started_at": "2026-01-01T00:03:00+00:00"})
    assert (await ws_mod._cached_queue_now_playing("st", db=object()))["asset_id"] == "a2"
    assert len(reads) == 2


async def test_read_that_raced_a_song_change_is_not_kept(monkeypatch):
    async def fake_read(db, station_id):
        # The scheduler announces a new song while this read is running
        await ws_mod.broadcast_now_playing_update("st", {"asset_id": "new", "started_at": "2026-01-01T00:03:00+00:00"})
        return {"asset_id": "old"}

    monkeypatch.setattr(ws_mod, "_queue_now_playing", fake_read)
    await ws_mod._cached_queue_now_playing("st", db=object())
    assert "st" not in ws_mod._join_cache


# ── V1-122: a slow listener is dropped after 5 s and does not hold up the rest ──

async def test_slow_listener_dropped_without_delaying_others(monkeypatch):
    assert ws_mod.SEND_TIMEOUT == 5.0
    monkeypatch.setattr(ws_mod, "SEND_TIMEOUT", 0.2)  # same rule, shorter wait for the test
    fast = [FakeSocket() for _ in range(3)]
    slow = FakeSocket(send_delay=5.0)
    for s in [*fast, slow]:
        await ws_mod.manager.connect("st", s)
    start = time.monotonic()
    await ws_mod.manager.broadcast_to_station("st", {"type": "now_playing", "data": {"asset_id": "a1"}})
    took = time.monotonic() - start
    assert took < limit(1.0)
    assert all(len(s.sent) == 1 for s in fast)
    assert slow not in ws_mod.manager.active_connections["st"]
    assert all(s in ws_mod.manager.active_connections["st"] for s in fast)


async def test_send_that_fails_drops_the_listener():
    class Broken(FakeSocket):
        async def send_text(self, text):
            raise RuntimeError("closed")

    ok, broken = FakeSocket(), Broken()
    await ws_mod.manager.connect("st", ok)
    await ws_mod.manager.connect("st", broken)
    await ws_mod.manager.broadcast_to_station("st", {"type": "x"})
    assert ws_mod.manager.active_connections["st"] == {ok}


# ── V1-123: ping after 30 s of silence, pong to a client message ──

async def test_ping_after_30_s_and_pong_to_a_message(monkeypatch):
    async def cached(station_id):
        return {"asset_id": "a1"}

    monkeypatch.setattr(ws_mod, "_cached_queue_now_playing", cached)
    real_wait_for = asyncio.wait_for
    timeouts = []

    async def quick_wait_for(aw, timeout):
        timeouts.append(timeout)
        return await real_wait_for(aw, 0.05)  # 30 s of silence, sped up

    monkeypatch.setattr(ws_mod.asyncio, "wait_for", quick_wait_for)
    sock = FakeSocket(incoming=["hello"], hang=0.2)
    sock_task = asyncio.create_task(ws_mod.websocket_now_playing(sock, "st"))
    await asyncio.sleep(0.15)
    monkeypatch.setattr(ws_mod.asyncio, "wait_for", real_wait_for)
    sock.hang = 0  # the client leaves
    await real_wait_for(sock_task, 2)
    assert sock.sent[0] == {"type": "now_playing", "data": {"asset_id": "a1"}}
    assert {"type": "pong", "data": "hello"} in sock.sent
    assert {"type": "ping"} in sock.sent
    assert set(timeouts) == {30.0}
    assert "st" not in ws_mod.manager.active_connections  # left cleanly


# ── V1-124: the first message when nothing plays, and when the read fails ──

class _Svc:
    result = None
    error = None

    def __init__(self, db):
        pass

    async def get_now_playing(self, station_id):
        if _Svc.error:
            raise _Svc.error
        return _Svc.result


async def _one_session():
    yield object()


async def _first_message(monkeypatch):
    async def no_queue(station_id):
        return None

    import app.db.session as session_mod
    monkeypatch.setattr(ws_mod, "_cached_queue_now_playing", no_queue)
    monkeypatch.setattr(session_mod, "get_async_session", _one_session)
    monkeypatch.setattr(ws_mod, "SchedulingService", _Svc)
    sock = FakeSocket(hang=0)
    await ws_mod.websocket_now_playing(sock, "st")
    return sock.sent


async def test_first_message_is_null_data_when_nothing_plays(monkeypatch):
    _Svc.result, _Svc.error = None, None
    sent = await _first_message(monkeypatch)
    assert sent[0] == {"type": "now_playing", "data": None}


async def test_first_message_is_an_error_when_the_read_fails(monkeypatch):
    _Svc.result, _Svc.error = None, RuntimeError("db down")
    sent = await _first_message(monkeypatch)
    assert sent[0]["type"] == "error"
    assert sent[0]["message"]
    _Svc.error = None
