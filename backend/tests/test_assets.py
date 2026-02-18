import io
import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_upload_asset(client: AsyncClient, auth_headers: dict):
    fake_audio = io.BytesIO(b"fake audio content")

    with patch("app.services.storage_service.upload_file", new_callable=AsyncMock, return_value="assets/test.mp3"):
        with patch("app.workers.tasks.media_tasks.task_extract_metadata.delay"):
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
    assert response.status_code == 403
