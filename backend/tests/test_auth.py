import pytest
from httpx import AsyncClient

from app.models.user import User


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, admin_user: User):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "testadmin@test.com", "password": "testpass123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, admin_user: User):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "testadmin@test.com", "password": "wrongpassword"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_user(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@test.com", "password": "whatever"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_endpoint(client: AsyncClient, auth_headers: dict):
    response = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "testadmin@test.com"
    assert data["role"] == "admin"


@pytest.mark.asyncio
async def test_me_without_token(client: AsyncClient):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_refresh_token(client: AsyncClient, admin_user: User):
    # First login
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "testadmin@test.com", "password": "testpass123"},
    )
    refresh_token = login_response.json()["refresh_token"]

    # Refresh
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
