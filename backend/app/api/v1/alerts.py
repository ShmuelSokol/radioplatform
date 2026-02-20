"""
Alerts API — list, resolve, reopen, delete alerts.
"""
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_admin, require_manager
from app.core.exceptions import NotFoundError
from app.models.alert import Alert
from app.models.user import User
from app.schemas.alert import AlertInDB, AlertListResponse
from app.services import alert_service
from sqlalchemy import select

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=AlertListResponse)
async def list_alerts(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    severity: str | None = Query(None),
    alert_type: str | None = Query(None),
    is_resolved: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """List alerts with optional filters."""
    alerts, total, unresolved_count = await alert_service.list_alerts(
        db, skip=skip, limit=limit, severity=severity,
        alert_type=alert_type, is_resolved=is_resolved,
    )
    return AlertListResponse(alerts=alerts, total=total, unresolved_count=unresolved_count)


@router.get("/unresolved-count")
async def unresolved_count(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Get unresolved alert count for notification badge."""
    count = await alert_service.get_unresolved_count(db)
    return {"unresolved_count": count}


@router.patch("/{alert_id}/resolve", response_model=AlertInDB)
async def resolve_alert(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Mark an alert as resolved."""
    alert = await alert_service.resolve_alert(db, alert_id, user.id)
    await db.commit()
    return alert


@router.patch("/{alert_id}/reopen", response_model=AlertInDB)
async def reopen_alert(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_manager),
):
    """Reopen a resolved alert."""
    alert = await alert_service.reopen_alert(db, alert_id)
    await db.commit()
    return alert


@router.delete("/{alert_id}", status_code=204)
async def delete_alert(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """Delete an alert (admin only)."""
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        raise NotFoundError("Alert not found")
    await db.delete(alert)
    await db.commit()
