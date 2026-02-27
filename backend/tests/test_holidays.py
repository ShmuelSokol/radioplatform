import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_holiday(client: AsyncClient, auth_headers: dict):
    response = await client.post(
        "/api/v1/holidays",
        json={
            "name": "Shabbat",
            "start_datetime": "2026-02-20T17:30:00",
            "end_datetime": "2026-02-21T18:30:00",
            "is_blackout": True,
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Shabbat"
    assert data["is_blackout"] is True


@pytest.mark.asyncio
async def test_list_holidays(client: AsyncClient, auth_headers: dict):
    await client.post(
        "/api/v1/holidays",
        json={
            "name": "Pesach",
            "start_datetime": "2026-04-01T18:00:00",
            "end_datetime": "2026-04-02T19:00:00",
        },
        headers=auth_headers,
    )
    response = await client.get("/api/v1/holidays", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    assert "holidays" in data
    assert "total" in data
    assert isinstance(data["holidays"], list)
    assert len(data["holidays"]) >= 1


@pytest.mark.asyncio
async def test_update_holiday(client: AsyncClient, auth_headers: dict):
    create = await client.post(
        "/api/v1/holidays",
        json={
            "name": "Yom Kippur",
            "start_datetime": "2026-09-28T17:00:00",
            "end_datetime": "2026-09-29T18:00:00",
        },
        headers=auth_headers,
    )
    holiday_id = create.json()["id"]

    response = await client.put(
        f"/api/v1/holidays/{holiday_id}",
        json={"name": "Yom Kippur (updated)"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Yom Kippur (updated)"


@pytest.mark.asyncio
async def test_delete_holiday(client: AsyncClient, auth_headers: dict):
    create = await client.post(
        "/api/v1/holidays",
        json={
            "name": "To Delete",
            "start_datetime": "2026-12-01T17:00:00",
            "end_datetime": "2026-12-02T18:00:00",
        },
        headers=auth_headers,
    )
    holiday_id = create.json()["id"]

    response = await client.delete(
        f"/api/v1/holidays/{holiday_id}",
        headers=auth_headers,
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_holidays_unauthorized(client: AsyncClient):
    response = await client.get("/api/v1/holidays")
    assert response.status_code in (401, 403)
