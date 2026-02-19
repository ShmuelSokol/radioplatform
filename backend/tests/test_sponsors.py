import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_sponsor(client: AsyncClient, auth_headers: dict):
    response = await client.post(
        "/api/v1/sponsors",
        json={
            "name": "Acme Corp",
            "length_seconds": 30.0,
            "priority": 5,
            "audio_file_path": "sponsors/acme.mp3",
            "insertion_policy": "between_tracks",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Acme Corp"
    assert data["length_seconds"] == 30.0
    assert data["priority"] == 5


@pytest.mark.asyncio
async def test_list_sponsors(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/api/v1/sponsors",
        json={
            "name": "Widget Co",
            "length_seconds": 15.0,
            "audio_file_path": "sponsors/widget.mp3",
        },
        headers=auth_headers,
    )
    response = await client.get("/api/v1/sponsors", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_update_sponsor(client: AsyncClient, auth_headers: dict):
    create = await client.post(
        "/api/v1/sponsors",
        json={
            "name": "Old Name",
            "length_seconds": 20.0,
            "audio_file_path": "sponsors/old.mp3",
        },
        headers=auth_headers,
    )
    sponsor_id = create.json()["id"]

    response = await client.put(
        f"/api/v1/sponsors/{sponsor_id}",
        json={"name": "New Name", "priority": 10},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"
    assert response.json()["priority"] == 10


@pytest.mark.asyncio
async def test_delete_sponsor(client: AsyncClient, auth_headers: dict):
    create = await client.post(
        "/api/v1/sponsors",
        json={
            "name": "Delete Me",
            "length_seconds": 10.0,
            "audio_file_path": "sponsors/delete.mp3",
        },
        headers=auth_headers,
    )
    sponsor_id = create.json()["id"]

    response = await client.delete(
        f"/api/v1/sponsors/{sponsor_id}",
        headers=auth_headers,
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_sponsors_unauthorized(client: AsyncClient):
    response = await client.get("/api/v1/sponsors")
    assert response.status_code in (401, 403)
