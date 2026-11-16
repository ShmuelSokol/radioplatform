"""Admins choose when open tabs move onto a new website release."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User, UserRole


async def _headers(client: AsyncClient, db: AsyncSession, role: UserRole) -> dict:
    email = f"{role.value}-{uuid.uuid4().hex[:6]}"
    db.add(User(id=uuid.uuid4(), email=email, hashed_password=hash_password("pw12345678"),
                role=role, is_active=True))
    await db.commit()
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "pw12345678"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_default_is_auto_with_no_push(client: AsyncClient):
    r = await client.get("/api/v1/site-release")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "auto"
    assert body["push"] is None
    assert body["server_time"]


@pytest.mark.asyncio
async def test_admin_sets_mode_pushes_and_cancels(client: AsyncClient, db_session: AsyncSession):
    h = await _headers(client, db_session, UserRole.ADMIN)
    r = await client.put("/api/v1/site-release", json={"mode": "manual"}, headers=h)
    assert r.status_code == 200 and r.json()["mode"] == "manual"

    r = await client.post("/api/v1/site-release/push",
                          json={"version": "abc123", "at": "2026-11-17T08:00:00Z", "include_playing": True},
                          headers=h)
    assert r.status_code == 200, r.text
    push = r.json()["push"]
    assert push["version"] == "abc123"
    assert push["include_playing"] is True
    assert push["at"].startswith("2026-11-17T08:00:00")

    # Every tab reads the same policy
    body = (await client.get("/api/v1/site-release")).json()
    assert body["mode"] == "manual" and body["push"]["id"] == push["id"]

    # A second push replaces the first; "now" when no time is given
    r = await client.post("/api/v1/site-release/push", json={}, headers=h)
    second = r.json()["push"]
    assert second["id"] != push["id"] and second["version"] is None and second["include_playing"] is False

    r = await client.delete("/api/v1/site-release/push", headers=h)
    assert r.json()["push"] is None
    assert (await client.get("/api/v1/site-release")).json()["mode"] == "manual"

    r = await client.get("/api/v1/site-release/tabs", headers=h)
    assert r.status_code == 200 and isinstance(r.json()["tabs"], list)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [UserRole.MANAGER, UserRole.DJ, UserRole.VIEWER, UserRole.SPONSOR])
async def test_only_admin_controls_releases(client: AsyncClient, db_session: AsyncSession, role: UserRole):
    h = await _headers(client, db_session, role)
    assert (await client.put("/api/v1/site-release", json={"mode": "manual"}, headers=h)).status_code == 403
    assert (await client.post("/api/v1/site-release/push", json={}, headers=h)).status_code == 403
    assert (await client.delete("/api/v1/site-release/push", headers=h)).status_code == 403
    assert (await client.get("/api/v1/site-release/tabs", headers=h)).status_code == 403


@pytest.mark.asyncio
async def test_anonymous_cannot_push(client: AsyncClient):
    assert (await client.post("/api/v1/site-release/push", json={})).status_code in (401, 403)
    assert (await client.put("/api/v1/site-release", json={"mode": "manual"})).status_code in (401, 403)
