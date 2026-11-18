"""Emergency alerts: the override API, its rules on EAS tones, the engine's reports and
the log (wiki/emergency-alerts.md). The engine itself is tested in the harness
(scenario emergency_alert_*)."""

import asyncio
import base64
import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.emergency_alert import EmergencyAlert
from app.models.station import Station
from app.services import eas_tones, emergency_alerts

TOKEN = "emergency-test-token"
PLAYOUT = "playout-test-token"


def _wav(samples, rate: int) -> bytes:
    """16-bit mono WAV, made without ffmpeg (the CI runner has none)."""
    import io
    import wave

    import numpy as np

    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _t(seconds: float = 3.0, rate: int = 16000):
    import numpy as np

    return np.arange(int(seconds * rate)) / rate


def speech_like() -> bytes:
    # A voice-like sound: a 140 Hz buzz with harmonics, shaped by syllables
    import numpy as np

    t = _t()
    pi2 = 2 * np.pi
    return _wav(0.3 * np.sin(pi2 * 140 * t) * (0.6 + 0.4 * np.sin(pi2 * 4 * t))
                + 0.15 * np.sin(pi2 * 420 * t) + 0.1 * np.sin(pi2 * 1100 * t), 16000)


def attention_signal() -> bytes:
    import numpy as np

    t = _t()
    return _wav(0.4 * np.sin(2 * np.pi * 853 * t) + 0.4 * np.sin(2 * np.pi * 960 * t), 16000)


def same_burst() -> bytes:
    """AFSK at 520.83 baud, mark 2083.3 Hz and space 1562.5 Hz, random bits: a SAME burst."""
    import numpy as np

    rate, baud = 22050, 520.83
    bits = np.random.default_rng(7).integers(0, 2, 520)
    per = rate / baud
    freqs = np.repeat(np.where(bits == 1, 2083.3, 1562.5), int(per) + 1)[: int(len(bits) * per)]
    phase = 2 * np.pi * np.cumsum(freqs) / rate
    return _wav(0.6 * np.sin(phase), rate)


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    monkeypatch.setattr(settings, "EMERGENCY_API_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "SUPABASE_URL", "")
    sent = []
    pushes = []

    async def fake_command(path, payload=None, *, station_id, wait=4.0, slow_is_ok=False):
        sent.append((str(station_id), path, payload))
        return True

    async def fake_ntfy(message, title, click="", tags="", priority="default"):
        pushes.append((title, message, priority))
        return True

    nudges = []

    async def fake_nudge(kind):
        nudges.append(kind)

    from app.api.v1 import site_release
    from app.services import bug_report_service, playout_engine
    monkeypatch.setattr(site_release, "broadcast_event", fake_nudge)
    monkeypatch.setattr(playout_engine, "_command", fake_command)
    monkeypatch.setattr(bug_report_service, "send_ntfy", fake_ntfy)
    emergency_alerts.reset()
    yield {"sent": sent, "pushes": pushes, "nudges": nudges}
    emergency_alerts.reset()


async def _station(db, name="KBR Jerusalem"):
    s = Station(id=uuid.uuid4(), name=name, is_active=True)
    db.add(s)
    await db.commit()
    return s


def _engine(monkeypatch, *stations):
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_ENABLED", True)
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_STATION_ID", ",".join(str(s.id) for s in stations))
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_URL", "http://engine.invalid")
    monkeypatch.setattr(settings, "PLAYOUT_ENGINE_TOKEN", PLAYOUT)


def test_tone_check_finds_eas_signals_and_passes_speech():
    assert eas_tones.find(speech_like())["has_tones"] is False
    att = eas_tones.find(attention_signal())
    assert att["has_tones"] and att["attention_at"] and att["attention_at"][0] <= 0.2
    same = eas_tones.find(same_burst())
    assert same["has_tones"] and same["same_at"]


@pytest.mark.asyncio
async def test_device_needs_the_emergency_token(client, db_session):
    await _station(db_session)
    files = {"file": ("a.wav", speech_like(), "audio/wav")}
    r = await client.post("/api/v1/emergency/device/activate", files=files)
    assert r.status_code == 403
    r = await client.post("/api/v1/emergency/device/activate", files=files, headers={"X-Emergency-Token": "wrong"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_device_api_off_when_no_token_is_set(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "EMERGENCY_API_TOKEN", "")
    await _station(db_session)
    r = await client.post("/api/v1/emergency/device/activate", headers={"X-Emergency-Token": ""},
                          files={"file": ("a.wav", speech_like(), "audio/wav")})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_device_alert_goes_to_every_engine_station_with_tones(client, db_session, monkeypatch, _setup):
    jlm = await _station(db_session, "KBR Jerusalem")
    main = await _station(db_session, "Kol B'ramah Main")
    _engine(monkeypatch, jlm, main)
    basic = base64.b64encode(f"eas:{TOKEN}".encode()).decode()
    r = await client.post(
        "/api/v1/emergency/device/activate", headers={"Authorization": f"Basic {basic}"},
        files={"file": ("rmt.wav", attention_signal(), "audio/wav")}, data={"kind": "rmt"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sent"] == 2 and body["failed"] == 0
    assert {a["has_tones"] for a in body["alerts"]} == {True}
    paths = sorted((sid, path) for sid, path, _ in _setup["sent"])
    assert paths == sorted([(str(jlm.id), "/alert"), (str(main.id), "/alert")])
    for sid, _, payload in _setup["sent"]:
        assert payload["uri"].startswith("annotate:kbr_alert=")
        assert payload["id"] in payload["uri"]
        assert "/api/v1/emergency/audio/" in payload["uri"]
    # Sent, not on air until the engine says so
    assert {a["status"] for a in body["alerts"]} == {"sent"}
    # ...but shown at once (banner, Stop), and every open tab is told to look again
    assert set(emergency_alerts.all_active()) == {str(jlm.id), str(main.id)}
    await asyncio.sleep(0.4)
    assert "emergency" in _setup["nudges"]


@pytest.mark.asyncio
async def test_staff_test_refuses_eas_tones(client, db_session, auth_headers, _setup):
    s = await _station(db_session)
    for clip in (attention_signal(), same_burst()):
        r = await client.post("/api/v1/emergency/activate", headers=auth_headers,
                              files={"file": ("x.wav", clip, "audio/wav")}, data={"station_ids": str(s.id)})
        assert r.status_code == 422
        assert "11.45" in r.json()["detail"]
    assert _setup["sent"] == []
    rows = (await db_session.execute(select(EmergencyAlert))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_staff_must_name_stations_and_be_a_manager(client, db_session, auth_headers):
    await _station(db_session)
    r = await client.post("/api/v1/emergency/activate", headers=auth_headers,
                          files={"file": ("x.wav", speech_like(), "audio/wav")})
    assert r.status_code == 422
    r = await client.post("/api/v1/emergency/activate", files={"file": ("x.wav", speech_like(), "audio/wav")},
                          data={"station_ids": "all"})
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_unreadable_or_missing_audio_is_refused(client, db_session, auth_headers):
    s = await _station(db_session)
    r = await client.post("/api/v1/emergency/activate", headers=auth_headers,
                          files={"file": ("x.mp3", b"not audio at all", "audio/mpeg")}, data={"station_ids": str(s.id)})
    assert r.status_code == 422
    r = await client.post("/api/v1/emergency/activate", headers=auth_headers, data={"station_ids": str(s.id)})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_engine_reports_make_the_log_and_notify(client, db_session, auth_headers, monkeypatch, _setup):
    s = await _station(db_session)
    _engine(monkeypatch, s)
    r = await client.post("/api/v1/emergency/activate", headers=auth_headers,
                          files={"file": ("t.wav", speech_like(), "audio/wav")},
                          data={"station_ids": str(s.id), "title": "Test of the override"})
    assert r.status_code == 201, r.text
    alert_id = r.json()["alerts"][0]["id"]
    eng = {"X-Playout-Token": PLAYOUT}
    r = await client.post(f"/api/v1/playout/{s.id}/alert", headers=eng,
                          json={"phase": "started", "alert_id": alert_id, "source": "api", "at": 1_800_000_000.0})
    assert r.status_code == 200 and r.json()["ok"]
    st = (await client.get("/api/v1/emergency/status", headers=auth_headers)).json()
    mine = next(x for x in st["stations"] if x["station_id"] == str(s.id))
    assert mine["on_air"]["alert_id"] == alert_id and mine["player"] == "engine"
    r = await client.post(f"/api/v1/playout/{s.id}/alert", headers=eng,
                          json={"phase": "ended", "alert_id": alert_id, "source": "api", "at": 1_800_000_012.5})
    assert r.status_code == 200
    log = (await client.get("/api/v1/emergency", headers=auth_headers)).json()
    row = log["alerts"][0]
    assert row["status"] == "done" and row["seconds_on_air"] == 12.5 and row["station_name"] == "KBR Jerusalem"
    st = (await client.get("/api/v1/emergency/status", headers=auth_headers)).json()
    assert next(x for x in st["stations"] if x["station_id"] == str(s.id))["on_air"] is None
    titles = [t for t, _, _ in _setup["pushes"]]
    assert any("ON AIR" in t for t in titles) and any("ended" in t for t in titles)


@pytest.mark.asyncio
async def test_alert_line_spell_gets_one_log_row(client, db_session, auth_headers, monkeypatch):
    s = await _station(db_session)
    _engine(monkeypatch, s)
    eng = {"X-Playout-Token": PLAYOUT}
    t0 = 1_800_000_100.0
    for phase, at in (("started", t0), ("ended", t0 + 40)):
        r = await client.post(f"/api/v1/playout/{s.id}/alert", headers=eng,
                              json={"phase": phase, "alert_id": "", "source": "line", "at": at, "started_at": t0})
        assert r.status_code == 200 and r.json()["ok"], r.text
    rows = (await db_session.execute(select(EmergencyAlert))).scalars().all()
    assert len(rows) == 1
    assert rows[0].source == "line" and rows[0].status == "done" and rows[0].seconds_on_air == 40.0


@pytest.mark.asyncio
async def test_engine_report_needs_the_playout_token(client, db_session, monkeypatch):
    s = await _station(db_session)
    _engine(monkeypatch, s)
    r = await client.post(f"/api/v1/playout/{s.id}/alert", json={"phase": "started", "source": "line"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_stop_tells_the_engine(client, db_session, auth_headers, monkeypatch, _setup):
    s = await _station(db_session)
    _engine(monkeypatch, s)
    r = await client.post("/api/v1/emergency/activate", headers=auth_headers,
                          files={"file": ("t.wav", speech_like(), "audio/wav")}, data={"station_ids": str(s.id)})
    alert_id = r.json()["alerts"][0]["id"]
    r = await client.post(f"/api/v1/emergency/{alert_id}/stop", headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (str(s.id), "/alert_stop", {}) in _setup["sent"]


@pytest.mark.asyncio
async def test_engine_not_answering_is_a_failure_and_pushes(client, db_session, auth_headers, monkeypatch, _setup):
    s = await _station(db_session)
    _engine(monkeypatch, s)
    from app.services import playout_engine

    async def dead(path, payload=None, *, station_id, wait=4.0, slow_is_ok=False):
        return False
    monkeypatch.setattr(playout_engine, "_command", dead)
    r = await client.post("/api/v1/emergency/activate", headers=auth_headers,
                          files={"file": ("t.wav", speech_like(), "audio/wav")}, data={"station_ids": str(s.id)})
    assert r.status_code == 201
    assert r.json()["failed"] == 1 and r.json()["alerts"][0]["status"] == "failed"
    assert any("FAILED" in t for t, _, _ in _setup["pushes"])


@pytest.mark.asyncio
async def test_browser_station_gets_the_alert_over_its_socket(client, db_session, auth_headers, monkeypatch):
    s = await _station(db_session, "Kol B'ramah - All Day Stories")
    from app.api.v1.websocket import manager
    got = []

    async def capture(station_id, message):
        got.append((station_id, message))
    monkeypatch.setattr(manager, "broadcast_to_station", capture)
    r = await client.post("/api/v1/emergency/activate", headers=auth_headers,
                          files={"file": ("t.wav", speech_like(), "audio/wav")}, data={"station_ids": str(s.id)})
    assert r.status_code == 201
    assert got and got[0][1]["type"] == "emergency_alert" and got[0][1]["data"]["url"]
    # Its audio is served for the players
    url = got[0][1]["data"]["url"]
    r = await client.get(url[url.index("/api/v1/"):])
    assert r.status_code == 200 and len(r.content) > 1000


@pytest.mark.asyncio
async def test_activation_by_audio_address(client, db_session, monkeypatch, _setup):
    s = await _station(db_session)
    _engine(monkeypatch, s)
    import httpx

    clip = speech_like()

    class FakeResp:
        content = clip
        headers = {"content-type": "audio/mpeg"}

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    r = await client.post("/api/v1/emergency/device/activate", headers={"X-Emergency-Token": TOKEN},
                          data={"audio_url": "https://eas.example/alert.mp3", "station_ids": str(s.id)})
    assert r.status_code == 201, r.text
    assert _setup["sent"][0][1] == "/alert"
