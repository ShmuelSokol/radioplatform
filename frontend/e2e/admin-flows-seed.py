"""Create (or reset) the admin account used by e2e/admin-flows.mjs.

Run from backend/:
  DATABASE_URL=... E2E_ADMIN_EMAIL=... E2E_ADMIN_PASS=... uv run python ../frontend/e2e/admin-flows-seed.py
Made-up test credentials only; the password comes from the environment.

With E2E_ALERTS=<n> and E2E_ALERT_TAG=<tag> it instead adds n open "system" alerts titled
"E2E alert <tag> <i>" (the API has no way to make an alert; the Alerts page needs some).
"""
import asyncio
import os
import uuid

from sqlalchemy import select

from app.core.security import hash_password
from app.db.engine import async_session_factory
from app.models.alert import Alert, AlertSeverity, AlertType
from app.models.user import User, UserRole


async def add_alerts(n: int, tag: str) -> None:
    async with async_session_factory() as db:
        for i in range(n):
            sev = AlertSeverity.WARNING if i % 2 else AlertSeverity.INFO
            db.add(Alert(id=uuid.uuid4(), alert_type=AlertType.SYSTEM, severity=sev,
                         title=f"E2E alert {tag} {i}", message=f"Made by the e2e test ({tag})"))
        await db.commit()
    print("added", n, "alerts")


async def main() -> None:
    if os.environ.get("E2E_ALERTS"):
        await add_alerts(int(os.environ["E2E_ALERTS"]), os.environ.get("E2E_ALERT_TAG", "x"))
        return
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
