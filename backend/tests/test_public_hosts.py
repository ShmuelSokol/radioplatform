"""Public DJs page (V2-127): a public host with no display name must not show the
part of their login e-mail before "@"; nothing of the e-mail leaves the server."""
import uuid

import pytest

from app.models.user import User, UserRole


@pytest.mark.asyncio
async def test_public_host_without_display_name_hides_the_email(client, db_session):
    db_session.add_all([
        User(id=uuid.uuid4(), email="rabbi.cohen@kbrlive.com", hashed_password="x", role=UserRole.DJ,
             is_active=True, is_public=True, display_name=None, title="Shiur"),
        User(id=uuid.uuid4(), email="named.host@kbrlive.com", hashed_password="x", role=UserRole.DJ,
             is_active=True, is_public=True, display_name="Reb Moshe"),
        User(id=uuid.uuid4(), email="blank.name@kbrlive.com", hashed_password="x", role=UserRole.DJ,
             is_active=True, is_public=True, display_name=""),
        User(id=uuid.uuid4(), email="private.dj@kbrlive.com", hashed_password="x", role=UserRole.DJ,
             is_active=True, is_public=False, display_name="Private"),
        User(id=uuid.uuid4(), email="gone.dj@kbrlive.com", hashed_password="x", role=UserRole.DJ,
             is_active=False, is_public=True, display_name="Gone"),
    ])
    await db_session.commit()
    r = await client.get("/api/v1/users/public/hosts")
    assert r.status_code == 200
    raw = r.text.lower()
    for leak in ("rabbi.cohen", "rabbi", "blank.name", "@kbrlive", "named.host", "private", "gone"):
        assert leak not in raw, leak
    names = sorted(h["display_name"] for h in r.json()["hosts"])
    assert names == sorted(["KBR host", "KBR host", "Reb Moshe"])
    unnamed = next(h for h in r.json()["hosts"] if h["title"] == "Shiur")
    assert unnamed["display_name"] == "KBR host"
    assert set(unnamed) == {"id", "display_name", "title", "bio", "photo_url", "social_links"}
