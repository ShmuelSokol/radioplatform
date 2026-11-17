"""Repeated alerts update the open one instead of piling up (20,000 open alerts in Nov 2026),
the list filters by severity/type, and managers can resolve everything at once."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert, AlertSeverity, AlertType
from app.models.station import Station
from app.services.alert_service import create_alert


def _quiet():
    return (patch("app.services.alert_service._notify_users", AsyncMock()),
            patch("app.api.v1.websocket.broadcast_alert", AsyncMock()))


async def _count(db: AsyncSession, **where) -> int:
    q = select(func.count(Alert.id))
    for k, v in where.items():
        q = q.where(getattr(Alert, k) == v)
    return (await db.execute(q)).scalar()


@pytest.mark.asyncio
async def test_repeats_update_the_open_alert(db_session: AsyncSession):
    station = Station(id=uuid.uuid4(), name="S")
    db_session.add(station)
    await db_session.commit()
    n1, n2 = _quiet()
    with n1 as notify, n2:
        a = await create_alert(db_session, AlertType.QUEUE_EMPTY, AlertSeverity.WARNING, "Empty block: X", "m1", station_id=station.id)
        b = await create_alert(db_session, AlertType.QUEUE_EMPTY, AlertSeverity.CRITICAL, "Empty block: X", "m2", station_id=station.id)
        c = await create_alert(db_session, "queue_empty", "warning", "Empty block: X", "m3", station_id=station.id)
        other_station = await create_alert(db_session, AlertType.QUEUE_EMPTY, AlertSeverity.WARNING, "Empty block: X", "m", station_id=None)
        other_title = await create_alert(db_session, AlertType.QUEUE_EMPTY, AlertSeverity.WARNING, "Empty block: Y", "m", station_id=station.id)
    await db_session.commit()
    assert a.id == b.id == c.id
    assert other_station.id != a.id and other_title.id != a.id
    assert a.context["repeats"] == 3 and a.context["last_message"] == "m3"
    assert a.severity == AlertSeverity.CRITICAL                     # a worse repeat raises it
    assert notify.await_count == 4                                  # three new alerts + the escalation to critical
    assert await _count(db_session) == 3


@pytest.mark.asyncio
async def test_a_resolved_alert_is_not_reused(db_session: AsyncSession):
    n1, n2 = _quiet()
    with n1, n2:
        a = await create_alert(db_session, AlertType.SYSTEM, AlertSeverity.INFO, "Server restarted", "m")
        a.is_resolved = True
        await db_session.flush()
        b = await create_alert(db_session, AlertType.SYSTEM, AlertSeverity.INFO, "Server restarted", "m")
    assert a.id != b.id


@pytest.mark.asyncio
async def test_list_filters_and_resolve_all(client: AsyncClient, auth_headers: dict, db_session: AsyncSession):
    n1, n2 = _quiet()
    with n1, n2:
        for i in range(3):
            await create_alert(db_session, AlertType.SILENCE, AlertSeverity.CRITICAL, f"Dead air {i}", "m")
        await create_alert(db_session, AlertType.SYSTEM, AlertSeverity.INFO, "Server restarted", "m")
    await db_session.commit()

    r = await client.get("/api/v1/alerts", params={"severity": "critical", "is_resolved": False}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 3
    r = await client.get("/api/v1/alerts", params={"alert_type": "system", "limit": 1, "skip": 0}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["total"] == 1
    assert (await client.get("/api/v1/alerts", params={"severity": "loud"}, headers=auth_headers)).status_code == 422

    r = await client.post("/api/v1/alerts/resolve-all", headers=auth_headers)
    assert r.status_code == 200 and r.json() == {"resolved": 4}
    assert (await client.get("/api/v1/alerts/unresolved-count", headers=auth_headers)).json()["unresolved_count"] == 0
