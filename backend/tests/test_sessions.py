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
    """V2-96: the role is read from the database on every request (not from the token), so
    the old rights are gone at once; since 2026-11-18 the session ends too."""
    u = await _person(db_session, role=UserRole.MANAGER)
    session = await _sign_in(client, u.email)
    bearer = {"Authorization": f"Bearer {session['access_token']}"}
    assert (await client.post("/api/v1/stations", json={"name": "Before"}, headers=bearer)).status_code == 201
    r = await client.put(f"/api/v1/users/{u.id}", json={"role": "viewer"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert (await client.post("/api/v1/stations", json={"name": "After"}, headers=bearer)).status_code == 401
    again = await _sign_in(client, u.email)
    bearer = {"Authorization": f"Bearer {again['access_token']}"}
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
async def test_password_change_ends_other_sessions(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    """V2-96 (decided 2026-11-18): a new password signs the person out everywhere. A stolen
    refresh token used to keep renewing for up to 7 days."""
    u = await _person(db_session)
    other_device = await _sign_in(client, u.email)
    bearer = {"Authorization": f"Bearer {other_device['access_token']}"}
    r = await client.put(f"/api/v1/users/{u.id}", json={"password": "new-pass-456"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json().get("new_tokens") is None  # tokens are only handed to the person changing their own
    assert (await client.get("/api/v1/auth/me", headers=bearer)).status_code == 401
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": other_device["refresh_token"]})
    assert r.status_code == 401
    # Signing in with the new password works, and that session is good
    again = await _sign_in(client, u.email, "new-pass-456")
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {again['access_token']}"})).status_code == 200
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": again["refresh_token"]})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_role_change_ends_other_sessions_but_saving_the_same_role_does_not(
    client: AsyncClient, auth_headers: dict, db_session: AsyncSession
):
    u = await _person(db_session, role=UserRole.MANAGER)
    session = await _sign_in(client, u.email)
    bearer = {"Authorization": f"Bearer {session['access_token']}"}
    # The Users form sends the role every time; an unchanged role, or a new name, ends nothing
    r = await client.put(f"/api/v1/users/{u.id}", json={"role": "manager", "display_name": "Same"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/auth/me", headers=bearer)).status_code == 200
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})).status_code == 200
    r = await client.put(f"/api/v1/users/{u.id}", json={"role": "dj"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/v1/auth/me", headers=bearer)).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})).status_code == 401
    again = await _sign_in(client, u.email)
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {again['access_token']}"})).json()["role"] == "dj"


@pytest.mark.asyncio
async def test_changing_your_own_password_keeps_this_browser_and_ends_the_others(
    client: AsyncClient, db_session: AsyncSession
):
    u = await _person(db_session, role=UserRole.ADMIN)
    here = await _sign_in(client, u.email)
    elsewhere = await _sign_in(client, u.email)
    r = await client.put(f"/api/v1/users/{u.id}", json={"password": "new-pass-456"},
                         headers={"Authorization": f"Bearer {here['access_token']}"})
    assert r.status_code == 200, r.text
    fresh = r.json()["new_tokens"]
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {fresh['access_token']}"})).status_code == 200
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": fresh["refresh_token"]})).status_code == 200
    for old in (here, elsewhere):
        assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old['access_token']}"})).status_code == 401
        assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": old["refresh_token"]})).status_code == 401


@pytest.mark.asyncio
async def test_tokens_from_before_this_release_still_work(client: AsyncClient, db_session: AsyncSession):
    """Nobody is signed out by the release itself: a token with no version counts as version 0."""
    from app.core.security import create_access_token, create_refresh_token

    u = await _person(db_session)
    old_access = create_access_token(str(u.id), {"role": "manager"})
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old_access}"})).status_code == 200
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": create_refresh_token(str(u.id))})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_deleted_login_is_signed_out(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    u = await _person(db_session)
    session = await _sign_in(client, u.email)
    assert (await client.delete(f"/api/v1/users/{u.id}", headers=auth_headers)).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {session['access_token']}"})).status_code == 401
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": session["refresh_token"]})).status_code == 401


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
