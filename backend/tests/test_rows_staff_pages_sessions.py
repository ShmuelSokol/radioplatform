"""V1-144: the same login on two devices. Signing out on one (the site only drops its own
tokens; there is no server-side logout) and renewing on one must not sign the other out:
refresh tokens are not single-use, so the other device's tokens keep working."""
import pytest
from httpx import AsyncClient

from app.models.user import User

CREDS = {"email": "testadmin@test.com", "password": "testpass123"}


async def _login(client: AsyncClient) -> dict:
    r = await client.post("/api/v1/auth/login", json=CREDS)
    assert r.status_code == 200
    return r.json()


@pytest.mark.asyncio
async def test_two_devices_renewal_on_one_keeps_the_other_signed_in(client: AsyncClient, admin_user: User):
    phone = await _login(client)
    pc = await _login(client)

    # The phone renews twice (token rotation if any would happen here)
    r1 = await client.post("/api/v1/auth/refresh", json={"refresh_token": phone["refresh_token"]})
    assert r1.status_code == 200
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": r1.json()["refresh_token"]})
    assert r2.status_code == 200

    # The PC's own access and refresh tokens still work
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {pc['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == CREDS["email"]
    again = await client.post("/api/v1/auth/refresh", json={"refresh_token": pc["refresh_token"]})
    assert again.status_code == 200

    # The phone's first refresh token was not used up by its own renewal either
    # (two tabs on one device may renew with the same token at once)
    reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": phone["refresh_token"]})
    assert reuse.status_code == 200


@pytest.mark.asyncio
async def test_logout_has_no_server_side_effect_on_other_device(client: AsyncClient, admin_user: User):
    """The site's logout (authStore.logout) only removes its own tokens; nothing is sent
    that could end the other device's session. Here: no logout route exists at all."""
    pc = await _login(client)
    r = await client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {pc['access_token']}"})
    assert r.status_code in (404, 405)
    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {pc['access_token']}"})
    assert me.status_code == 200
