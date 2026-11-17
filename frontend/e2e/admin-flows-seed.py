"""Create (or reset) the admin account used by e2e/admin-flows.mjs.

Run from backend/:
  DATABASE_URL=... E2E_ADMIN_EMAIL=... E2E_ADMIN_PASS=... uv run python ../frontend/e2e/admin-flows-seed.py
Made-up test credentials only; the password comes from the environment.
"""
import asyncio
import os
import uuid

from sqlalchemy import select

from app.core.security import hash_password
from app.db.engine import async_session_factory
from app.models.user import User, UserRole


async def main() -> None:
    email = os.environ["E2E_ADMIN_EMAIL"]
    password = os.environ["E2E_ADMIN_PASS"]
    async with async_session_factory() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(id=uuid.uuid4(), email=email, role=UserRole.ADMIN, is_active=True, display_name="E2E Admin")
            db.add(user)
        user.hashed_password = hash_password(password)
        user.role = UserRole.ADMIN
        user.is_active = True
        await db.commit()
    print("seeded", email)


asyncio.run(main())
