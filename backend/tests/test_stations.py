import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_station(client: AsyncClient, auth_headers: dict):
    response = await client.post(
        "/api/v1/stations",
        json={"name": "Test Station", "timezone": "US/Eastern"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Station"
    assert data["timezone"] == "US/Eastern"
    assert data["is_active"] is True
    assert data["type"] == "internet"


@pytest.mark.asyncio
async def test_list_stations(client: AsyncClient, auth_headers: dict):
    # Create a station first
    await client.post(
        "/api/v1/stations",
        json={"name": "Station A"},
        headers=auth_headers,
    )

    response = await client.get("/api/v1/stations")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["stations"]) >= 1


@pytest.mark.asyncio
async def test_get_station(client: AsyncClient, auth_headers: dict):
    create_response = await client.post(
        "/api/v1/stations",
        json={"name": "Get Me Station"},
        headers=auth_headers,
    )
    station_id = create_response.json()["id"]

    response = await client.get(f"/api/v1/stations/{station_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Get Me Station"


@pytest.mark.asyncio
async def test_update_station(client: AsyncClient, auth_headers: dict):
    create_response = await client.post(
        "/api/v1/stations",
        json={"name": "Update Me"},
        headers=auth_headers,
    )
    station_id = create_response.json()["id"]

    response = await client.put(
        f"/api/v1/stations/{station_id}",
        json={"name": "Updated Name", "description": "Now with description"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Name"
    assert response.json()["description"] == "Now with description"


@pytest.mark.asyncio
async def test_delete_station(client: AsyncClient, auth_headers: dict):
    create_response = await client.post(
        "/api/v1/stations",
        json={"name": "Delete Me"},
        headers=auth_headers,
    )
    station_id = create_response.json()["id"]

    response = await client.delete(
        f"/api/v1/stations/{station_id}",
        headers=auth_headers,
    )
    assert response.status_code == 204

    # Confirm deleted
    get_response = await client.get(f"/api/v1/stations/{station_id}")
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_create_station_duplicate_name(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/api/v1/stations",
        json={"name": "Unique Station"},
        headers=auth_headers,
    )
    response = await client.post(
        "/api/v1/stations",
        json={"name": "Unique Station"},
        headers=auth_headers,
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_station_unauthorized(client: AsyncClient):
    response = await client.post(
        "/api/v1/stations",
        json={"name": "No Auth Station"},
    )
    assert response.status_code in (401, 403)
