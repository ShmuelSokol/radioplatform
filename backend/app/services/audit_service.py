"""Audit logging service — records admin/manager write actions."""
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


async def log_action(
    db: AsyncSession,
    *,
    user_id=None,
    user_email: str | None = None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    detail: str | None = None,
    changes: dict | None = None,
    ip_address: str | None = None,
    request_id: str | None = None,
) -> None:
    """Write an audit log entry. Silently fails to avoid breaking the main request."""
    try:
        entry = AuditLog(
            user_id=user_id,
            user_email=user_email,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            detail=detail,
            changes=changes,
            ip_address=ip_address,
            request_id=request_id,
        )
        db.add(entry)
        await db.flush()
    except Exception as e:
        logger.warning("Audit log write failed: %s", e)
