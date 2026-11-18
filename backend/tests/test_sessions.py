"""Sign-in sessions on the server side, and what the public hosts list shows.

V2-96 (ending sessions), V2-127 (public host with no display name).
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import User, UserRole


async def _person(db, role=UserRole.MANAGER, **kw) -> User:
    u = User(id=uuid.uuid4(), email=f"p-{uuid.uuid4().hex[:6]}@t.com", hashed_password=hash_password("old-pass-123"),
             role=role, is_active=True, **kw)
    db.add(u)
    await db.commit()
    return u


async def _sign_in(client, email, password="old-pass-123") -> dict:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_deactivating_a_user_ends_every_session_at_once(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """V2-96: deactivation is checked on every request and on every renewal."""
    u = await _person(db_session)
    other_device = await _sign_in(client, u.email)
    bearer = {"Authorization": f"Bearer {other_device['access_token']}"}
    assert (await client.get("/api/v1/auth/me", headers=bearer)).status_code == 200
    r = await client.put(f"/api/v1/users/{u.id}", json={"is_active": False}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/auth/me", headers=bearer)).status_code == 401
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": other_device["refresh_token"]})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_role_change_applies_to_open_sessions_on_the_next_request(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    """V2-96: a role change does not end the session, but the role is read from the database
    on every request (not from the token), so the other device loses the old rights at once."""
    u = await _person(db_session, role=UserRole.MANAGER)
    session = await _sign_in(client, u.email)
    bearer = {"Authorization": f"Bearer {session['access_token']}"}
    assert (await client.post("/api/v1/stations", json={"name": "Before"}, headers=bearer)).status_code == 201
    r = await client.put(f"/api/v1/users/{u.id}", json={"role": "viewer"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/auth/me", headers=bearer)).json()["role"] == "viewer"
    assert (await client.post("/api/v1/stations", json={"name": "After"}, headers=bearer)).status_code == 403


@pytest.mark.asyncio
async def test_logout_is_only_this_browser_and_old_tokens_keep_working(client: AsyncClient, db_session: AsyncSession):
    """V2-96: there is no server logout; a refresh token stays usable (7 days) after the
    browser "logs out", because logging out only deletes it from that browser."""
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert not any(p.startswith("/api/v1/auth/logout") for p in paths)
    u = await _person(db_session)
    session = await _sign_in(client, u.email)
    for _ in range(2):  # the same refresh token can be used again: renewals do not retire it
        r = await client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})
        assert r.status_code == 200


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="BUG? (owner question) Changing a person's password does not end their "
                   "other sessions: a stolen refresh token keeps renewing for up to 7 days. Should a password "
                   "change (and a role change) sign the person out everywhere else?")
async def test_password_change_ends_other_sessions(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    u = await _person(db_session)
    other_device = await _sign_in(client, u.email)
    r = await client.put(f"/api/v1/users/{u.id}", json={"password": "new-pass-456"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": other_device["refresh_token"]})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_public_host_without_display_name_never_shows_the_email(client: AsyncClient, db_session: AsyncSession):
    """V2-127 (fixed earlier): the part of the e-mail before "@" was shown as the host's name."""
    u = User(id=uuid.uuid4(), email="moshe.private@staff.test", hashed_password="x", role=UserRole.DJ,
             is_active=True, is_public=True, display_name=None)
    db_session.add(u)
    await db_session.commit()
    r = await client.get("/api/v1/users/public/hosts")
    assert r.status_code == 200
    assert "moshe.private" not in r.text and "staff.test" not in r.text
    host = next(h for h in r.json()["hosts"] if h["id"] == str(u.id))
    assert host["display_name"] == "KBR host"
