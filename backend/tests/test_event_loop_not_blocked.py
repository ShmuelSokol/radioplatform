"""Slow outside work (ffmpeg, Twilio, Resend, the Claude API) must never block the
backend's event loop (checklist V2-1, V2-64, V2-78).

One process runs every station's scheduler ticks, queue reads and operator buttons.
A synchronous call made inside an async handler freezes all of them for as long as
it takes: an enhance of a long shiur (minutes of ffmpeg), a text to ten people or a
Claude draft (seconds each). Each test makes the slow call take SLOW seconds and
measures the longest gap of a 10 ms ticker running beside it, i.e. how long a
cheap request or scheduler tick would have waited."""
import asyncio
import time
import types
import uuid

import pytest

from app.models.asset import Asset

SLOW = 0.6       # how long the fake outside call takes
MAX_STALL = 0.2  # longest the event loop may be held (S-3: operator calls answer in 300 ms)


async def _max_stall_while(coro) -> tuple[float, object]:
    """Run coro; meanwhile tick every 10 ms and return the longest gap seen."""
    worst = 0.0
    done = False

    async def ticker():
        nonlocal worst
        last = time.perf_counter()
        while not done:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            worst = max(worst, now - last)
            last = now

    t = asyncio.create_task(ticker())
    await asyncio.sleep(0.02)
    try:
        result = await coro
    finally:
        done = True
        await t
    return worst, result


def _slow(ret):
    def fn(*args, **kwargs):
        time.sleep(SLOW)
        return ret(*args, **kwargs) if callable(ret) else ret
    return fn


# --- ffmpeg inside request handlers (V2-1) ---

@pytest.mark.asyncio
async def test_detect_silence_does_not_block_other_requests(client, auth_headers, db_session, monkeypatch):
    """While ffmpeg scans one item for silence, a health check must still answer at once."""
    from app.services import silence_service, storage_service

    asset = Asset(id=uuid.uuid4(), title="Long shiur", file_path="assets/x.mp3", asset_type="shiur", duration=3600)
    db_session.add(asset)
    await db_session.commit()

    async def fake_download(key):
        return b"\x00" * 100

    monkeypatch.setattr(storage_service, "download_file", fake_download)
    monkeypatch.setattr(silence_service, "detect_silence", _slow([]))

    async def both():
        slow = asyncio.create_task(
            client.post(f"/api/v1/assets/{asset.id}/detect-silence", headers=auth_headers)
        )
        t0 = time.perf_counter()
        await asyncio.sleep(0.05)
        await client.get("/health")
        health_wait = time.perf_counter() - t0 - 0.05
        r = await slow
        return health_wait, r

    stall, (health_wait, r) = await _max_stall_while(both())
    assert r.status_code == 200, r.text
    assert health_wait < MAX_STALL, f"health check waited {health_wait:.2f}s behind ffmpeg"
    assert stall < MAX_STALL, f"event loop blocked {stall:.2f}s by ffmpeg"


@pytest.mark.asyncio
async def test_upload_conversion_and_bpm_do_not_block(db_session, monkeypatch):
    """Upload conversion and BPM detection (create_asset) run ffmpeg/numpy off the loop."""
    from app.services import asset_service, bpm_service

    monkeypatch.setattr(asset_service, "convert_audio", _slow((b"ID3fake", 10.0, "mp3")))
    monkeypatch.setattr(bpm_service, "detect_bpm", _slow((100.0, "med_fast")))

    async def fake_upload(data, key, content_type="audio/mpeg"):
        return key

    monkeypatch.setattr(asset_service, "upload_file", fake_upload)

    stall, asset = await _max_stall_while(
        asset_service.create_asset(
            db_session, title="Song", filename="song.wav", file_data=b"RIFF",
            content_type="audio/wav", asset_type="music",
        )
    )
    assert asset.title == "Song"
    assert stall < MAX_STALL, f"event loop blocked {stall:.2f}s by upload conversion/BPM"


# --- Twilio (V2-78) ---

class _FakeTwilioClient:
    def __init__(self, *a, **k):
        self.messages = types.SimpleNamespace(create=_slow(None))

    def calls(self, sid):
        return types.SimpleNamespace(update=_slow(None))


@pytest.mark.asyncio
async def test_sms_and_whatsapp_do_not_block(monkeypatch):
    from app.config import settings
    from app.services import sms_service

    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC_fake")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "fake")
    monkeypatch.setattr("twilio.rest.Client", _FakeTwilioClient)

    stall, ok = await _max_stall_while(sms_service.send_sms("+15550000000", "Dead air on KBR"))
    assert ok is True
    assert stall < MAX_STALL, f"SMS blocked the event loop {stall:.2f}s"
    stall, ok = await _max_stall_while(sms_service.send_whatsapp("+15550000000", "Dead air on KBR"))
    assert ok is True
    assert stall < MAX_STALL, f"WhatsApp blocked the event loop {stall:.2f}s"


@pytest.mark.asyncio
async def test_twilio_call_control_does_not_block(monkeypatch):
    from app.config import settings
    from app.services import twilio_voice_service

    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC_fake")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "fake")
    monkeypatch.setattr("twilio.rest.Client", _FakeTwilioClient)

    stall, ok = await _max_stall_while(twilio_voice_service.move_caller_to_conference("CA1", "live-show-x"))
    assert ok is True
    assert stall < MAX_STALL, f"moving a caller blocked the event loop {stall:.2f}s"
    stall, ok = await _max_stall_while(twilio_voice_service.end_caller("CA1"))
    assert ok is True
    assert stall < MAX_STALL, f"hanging up blocked the event loop {stall:.2f}s"


# --- Resend and Claude (V2-64) ---

@pytest.mark.asyncio
async def test_email_send_does_not_block(client, auth_headers, monkeypatch):
    import resend

    from app.config import settings

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_fake")
    monkeypatch.setattr(resend.Emails, "send", _slow({"id": "1"}))

    async def call():
        return await client.post(
            "/api/v1/ai-emails/send",
            json={"to": "sponsor@example.com", "subject": "Hi", "body": "Hello"},
            headers=auth_headers,
        )

    stall, r = await _max_stall_while(call())
    assert r.json()["success"] is True
    assert stall < MAX_STALL, f"Resend blocked the event loop {stall:.2f}s"


@pytest.mark.asyncio
async def test_ai_draft_does_not_block(client, auth_headers, monkeypatch):
    import anthropic

    from app.config import settings

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-fake")

    class FakeAnthropic:
        def __init__(self, *a, **k):
            msg = types.SimpleNamespace(content=[types.SimpleNamespace(text="Drafted body")])
            self.messages = types.SimpleNamespace(create=_slow(msg))

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)

    async def call():
        return await client.post(
            "/api/v1/ai-emails/draft", json={"sponsor_name": "Acme"}, headers=auth_headers
        )

    stall, r = await _max_stall_while(call())
    assert r.json()["draft_text"] == "Drafted body"
    assert stall < MAX_STALL, f"Claude draft blocked the event loop {stall:.2f}s"
