import io
import uuid
import wave

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.sponsor import Sponsor
from app.services import spot_voice_service
from app.services.spot_voice_service import placeholder_audio

URL = "/api/v1/spot-recordings"
SCRIPT = (
    "Kol Berama sponsor Burger Bar. Burger Bar had its grand opening two weeks ago "
    "and offers catering for birthdays and events."
)


async def _sponsor(db: AsyncSession) -> Sponsor:
    sponsor = Sponsor(
        id=uuid.uuid4(), name="Burger Bar", length_seconds=30, priority=1, audio_file_path=""
    )
    db.add(sponsor)
    await db.commit()
    return sponsor


def test_placeholder_is_a_wav_of_the_asked_length():
    with wave.open(io.BytesIO(placeholder_audio(12)), "rb") as w:
        assert abs(w.getnframes() / w.getframerate() - 12) < 0.01
    with wave.open(io.BytesIO(placeholder_audio(500)), "rb") as w:
        assert w.getnframes() / w.getframerate() <= 90


@pytest.mark.asyncio
async def test_script_to_placeholder_to_real_audio_to_library(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    sponsor = await _sponsor(db_session)
    r = await client.post(
        URL,
        json={"sponsor_id": str(sponsor.id), "title": "Burger Bar opening", "script_text": SCRIPT},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["status"] == "draft" and rec["has_audio"] is False
    assert rec["review"]["verdict"] == "ok"
    rid = rec["id"]

    # no audio yet
    assert (await client.get(f"{URL}/{rid}/audio", headers=auth_headers)).status_code == 404
    assert (await client.post(f"{URL}/{rid}/approve", headers=auth_headers)).status_code == 409

    # placeholder voice
    r = await client.post(f"{URL}/{rid}/generate", headers=auth_headers)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "ready" and rec["voice"] == "placeholder" and rec["duration"] > 3
    r = await client.get(f"{URL}/{rid}/audio", headers=auth_headers)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF"
    # a stand-in can be listened to, never approved for air
    assert (await client.post(f"{URL}/{rid}/approve", headers=auth_headers)).status_code == 409

    # changing the words throws the audio away and re-checks the wording
    r = await client.put(
        f"{URL}/{rid}", json={"script_text": SCRIPT + " Call now!"}, headers=auth_headers
    )
    rec = r.json()
    assert rec["status"] == "draft" and rec["has_audio"] is False
    assert rec["review"]["verdict"] == "needs_changes"

    # the real voice, once connected, goes through the same path
    async def fake_shprach(script_text):
        return b"ID3fake-mp3", 28.5, ".mp3"

    monkeypatch.setattr(spot_voice_service, "_shprach", fake_shprach)
    r = await client.post(f"{URL}/{rid}/generate", headers=auth_headers)
    rec = r.json()
    assert rec["voice"] == "shprach" and rec["duration"] == 28.5

    r = await client.post(f"{URL}/{rid}/approve", headers=auth_headers)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] == "approved" and rec["asset_id"]
    asset = (
        await db_session.execute(select(Asset).where(Asset.id == uuid.UUID(rec["asset_id"])))
    ).scalar_one()
    assert asset.asset_type == "spot" and str(asset.sponsor_id) == str(sponsor.id)
    assert asset.duration == 28.5 and asset.title == "Burger Bar opening"

    # approved spots are locked
    r = await client.put(
        f"{URL}/{rid}", json={"script_text": "Something else"}, headers=auth_headers
    )
    assert r.status_code == 409
    assert (await client.post(f"{URL}/{rid}/generate", headers=auth_headers)).status_code == 409

    # and can be scheduled like any sponsor audio
    r = await client.post(
        "/api/v1/spot-schedules",
        json={"sponsor_id": str(sponsor.id), "asset_id": rec["asset_id"]},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_managers_only_and_upload_checks(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession, monkeypatch
):
    sponsor = await _sponsor(db_session)
    assert (await client.get(URL)).status_code in (401, 403)
    r = await client.post(
        URL,
        json={"sponsor_id": str(uuid.uuid4()), "title": "x", "script_text": SCRIPT},
        headers=auth_headers,
    )
    assert r.status_code == 404
    r = await client.post(
        URL,
        json={"sponsor_id": str(sponsor.id), "title": "x", "script_text": SCRIPT},
        headers=auth_headers,
    )
    rid = r.json()["id"]
    r = await client.post(
        f"{URL}/{rid}/audio",
        files={"file": ("spot.mp3", b"", "audio/mpeg")},
        headers=auth_headers,
    )
    assert r.status_code == 422
    r = await client.post(
        f"{URL}/{rid}/audio",
        files={"file": ("spot.wav", b"RIFFxxxx", "audio/wav")},
        data={"source": "placeholder"},
        headers=auth_headers,
    )
    assert r.status_code == 422

    # a Shprach render posted from the studio PC: same shape as the weather upload
    from app.services import audio_convert_service

    monkeypatch.setattr(
        audio_convert_service, "convert_audio", lambda raw, name, fmt: (b"ID3mp3", 31.0, ".mp3")
    )
    r = await client.post(
        f"{URL}/{rid}/audio",
        files={"file": ("spot.wav", b"RIFFxxxx", "audio/wav")},
        data={"source": "shprach"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["voice"] == "shprach" and r.json()["status"] == "ready"
    r = await client.get(f"{URL}/{rid}/audio", headers=auth_headers)
    assert r.headers["content-type"] == "audio/mpeg" and r.content == b"ID3mp3"

    r = await client.post(f"{URL}/{rid}/reject", headers=auth_headers)
    assert r.json()["status"] == "rejected"
    assert (await client.delete(f"{URL}/{rid}", headers=auth_headers)).status_code == 204
    assert (await client.get(URL, headers=auth_headers)).json() == []
