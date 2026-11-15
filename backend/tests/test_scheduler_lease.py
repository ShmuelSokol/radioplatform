"""Only one server process may run the playout scheduler at a time."""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.scheduler_engine import SchedulerEngine


@pytest.mark.asyncio
async def test_second_process_waits_until_the_first_lets_go(db_session: AsyncSession):
    first, second = SchedulerEngine(), SchedulerEngine()

    assert await first._hold_lease(db_session) is True
    assert await second._hold_lease(db_session) is False
    assert first.is_leader and not second.is_leader

    # Renewing keeps it; the other still waits.
    assert await first._hold_lease(db_session) is True
    assert await second._hold_lease(db_session) is False

    # The first process shuts down (lease expired): the second takes over.
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text
    await db_session.execute(
        text("UPDATE scheduler_leases SET expires_at = :past"),
        {"past": datetime.now(timezone.utc) - timedelta(seconds=1)},
    )
    await db_session.commit()
    assert await second._hold_lease(db_session) is True
    assert await first._hold_lease(db_session) is False
    assert second.is_leader and not first.is_leader
