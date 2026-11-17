"""Icecast OTA source client (checklist V2-116, V2-117, V2-125)."""
import asyncio

import httpx
import pytest

from app.config import settings
from app.services import icecast_service
from app.services.icecast_service import IcecastSourceClient


@pytest.fixture(autouse=True)
def _clean_clients():
    icecast_service._icecast_clients.clear()
    yield
    icecast_service._icecast_clients.clear()


def test_mount_defaults_to_station_and_client_is_reused(monkeypatch):
    monkeypatch.setattr(settings, "ICECAST_HOST", "ice.test")
    c = icecast_service.get_icecast_client("st-1")
    assert c.mount == "/st-1" and c.source_url == "http://ice.test:8000/st-1"
    assert icecast_service.get_icecast_client("st-1") is c
    assert icecast_service.get_icecast_client("st-2", mount="/fm").mount == "/fm"


@pytest.mark.asyncio
async def test_full_queue_drops_the_oldest_chunk():
    c = IcecastSourceClient(host="ice.test", port=8000, mount="/m", password="pw")
    c._running = True
    for i in range(101):
        await c.push_audio(bytes([i % 256]))
    assert c._audio_queue.qsize() == 100
    assert c._audio_queue.get_nowait() == bytes([1])  # chunk 0 was dropped
    c._running = False
    await c.push_audio(b"ignored")  # not running: nothing queued
    assert c._audio_queue.qsize() == 99


@pytest.mark.asyncio
async def test_start_does_nothing_without_host(monkeypatch):
    monkeypatch.setattr(settings, "ICECAST_HOST", "")
    c = IcecastSourceClient(host="", password="pw")
    await c.start()
    assert c._running is False and c._task is None


@pytest.mark.asyncio
async def test_icecast_down_is_retried_and_stop_forgets_the_client(monkeypatch):
    """Icecast down: logged and retried every 5 s until stopped; stop cancels the
    task and removes the client."""
    monkeypatch.setattr(settings, "ICECAST_HOST", "ice.test")
    attempts = []

    async def failing_put(self, url, **kw):
        attempts.append(url)
        raise httpx.ConnectError("refused")

    real_sleep = asyncio.sleep
    waits = []

    async def fast_sleep(s, *a, **k):
        waits.append(s)
        await real_sleep(0)

    monkeypatch.setattr(httpx.AsyncClient, "put", failing_put)
    monkeypatch.setattr(icecast_service.asyncio, "sleep", fast_sleep)
    await icecast_service.start_icecast_stream("st-9", "KBR")
    for _ in range(50):
        await real_sleep(0)
    assert len(attempts) >= 3 and set(attempts) == {"http://ice.test:8000/st-9"}
    assert 5 in waits
    await icecast_service.stop_icecast_stream("st-9")
    assert "st-9" not in icecast_service._icecast_clients


@pytest.mark.xfail(strict=True, reason="BUG: with ICECAST_HOST set and no password configured, the source streams with the default 'hackme'")
@pytest.mark.asyncio
async def test_default_password_is_not_used(monkeypatch):
    monkeypatch.setattr(settings, "ICECAST_HOST", "ice.test")
    monkeypatch.setattr(settings, "ICECAST_SOURCE_PASSWORD", "hackme")  # the shipped default
    c = IcecastSourceClient()
    await c.start()
    try:
        assert c._running is False, "streamed with the well-known default password"
    finally:
        await c.stop()
