import io
import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_upload_asset(client: AsyncClient, auth_headers: dict):
    fake_audio = io.BytesIO(b"fake audio content")

    # The bytes are not real audio, so conversion is stubbed (unreadable files are refused: see
    # tests/test_library_assets.py::test_upload_of_a_non_audio_file_is_refused_and_nothing_is_stored)
    with patch("app.services.asset_service.upload_file", new_callable=AsyncMock, return_value="assets/test.mp3"), \
            patch("app.services.asset_service.convert_audio", return_value=(b"fake audio content", 3.0, ".mp3")):
        with patch("app.api.v1.assets._save_album_art", new_callable=AsyncMock):
            response = await client.post(
                "/api/v1/assets/upload",
                files={"file": ("test.mp3", fake_audio, "audio/mpeg")},
                data={"title": "Test Track"},
                headers=auth_headers,
            )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test Track"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_assets_empty(client: AsyncClient, auth_headers: dict):
    response = await client.get("/api/v1/assets", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["assets"] == []


@pytest.mark.asyncio
async def test_list_assets_unauthorized(client: AsyncClient):
    response = await client.get("/api/v1/assets")
    assert response.status_code in (401, 403)
