"""
Alert service — create, resolve, query alerts and dispatch notifications.
"""
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert, AlertSeverity, AlertType
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

# A repeat of an open alert within this many hours of its last report updates it
# instead of adding a row (see create_alert).
REPEAT_WINDOW_HOURS = 24

# Severity threshold for SMS/WhatsApp notifications
_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


async def create_alert(
    db: AsyncSession,
    alert_type: AlertType | str,
    severity: AlertSeverity | str,
    title: str,
    message: str,
    station_id: UUID | str | None = None,
    context: dict | None = None,
) -> Alert:
    """Create an alert, notify users, and broadcast via WebSocket.

    The same problem reported again (same type, title and station) while an
    earlier report of it is still open and recent only updates that report: it
    counts the repeat in context["repeats"] and notifies nobody. Before this,
    every scheduler pass and every restart added a row (20,000 open alerts in
    November 2026, nearly all "Empty block", "Dead air" and "Server restarted").
    """
    a_type = alert_type if isinstance(alert_type, AlertType) else AlertType(alert_type)
    a_sev = severity if isinstance(severity, AlertSeverity) else AlertSeverity(severity)
    since = datetime.now(timezone.utc) - timedelta(hours=REPEAT_WINDOW_HOURS)
    existing = (await db.execute(
        select(Alert).where(
            Alert.alert_type == a_type,
            Alert.title == title,
            Alert.station_id == station_id if station_id is not None else Alert.station_id.is_(None),
            Alert.is_resolved == False,  # noqa: E712
            Alert.updated_at >= since,
        ).order_by(Alert.updated_at.desc()).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        ctx = dict(existing.context or {})
        ctx["repeats"] = int(ctx.get("repeats") or 1) + 1
        ctx["last_message"] = message
        existing.context = ctx
        existing.updated_at = datetime.now(timezone.utc)
        if _SEVERITY_ORDER.get(a_sev.value, 0) > _SEVERITY_ORDER.get(existing.severity.value, 0):
            existing.severity = a_sev
        await db.flush()
        return existing

    alert = Alert(
        station_id=station_id,
        alert_type=a_type,
        severity=a_sev,
        title=title,
        message=message,
        context=context,
        is_resolved=False,
    )
    db.add(alert)
    await db.flush()
    await db.refresh(alert)
    await _dispatch(db, alert, severity, title, message)
    return alert


async def _dispatch(db: AsyncSession, alert: Alert, severity, title: str, message: str) -> None:
    """Text/WhatsApp the people who asked for this severity and push it to open screens."""
    # Dispatch SMS/WhatsApp to users who opted in
    try:
        await _notify_users(db, alert)
    except Exception as e:
        logger.error("Alert notification dispatch failed: %s", e)

    # Broadcast via WebSocket
    try:
        from app.api.v1.websocket import broadcast_alert
        await broadcast_alert({
            "id": str(alert.id),
            "alert_type": str(alert.alert_type.value),
            "severity": str(alert.severity.value),
            "title": alert.title,
            "message": alert.message,
            "station_id": str(alert.station_id) if alert.station_id else None,
            "created_at": alert.created_at.isoformat() if alert.created_at else None,
            "is_resolved": alert.is_resolved,
        })
    except Exception as e:
        logger.error("Alert WebSocket broadcast failed: %s", e)

    logger.info("Alert created: [%s] %s — %s", severity, title, message)


async def resolve_alert(db: AsyncSession, alert_id: UUID | str, user_id: UUID | str) -> Alert:
    """Mark an alert as resolved."""
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Alert not found")

    alert.is_resolved = True
    alert.resolved_at = datetime.now(timezone.utc)
    alert.resolved_by = user_id
    await db.flush()
    await db.refresh(alert)
    return alert


async def reopen_alert(db: AsyncSession, alert_id: UUID | str) -> Alert:
    """Reopen a resolved alert."""
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if not alert:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Alert not found")

    alert.is_resolved = False
    alert.resolved_at = None
    alert.resolved_by = None
    await db.flush()
    await db.refresh(alert)
    return alert


async def resolve_all(db: AsyncSession, user_id: UUID | str | None) -> int:
    """Resolve every open alert. Returns how many were resolved."""
    from sqlalchemy import update

    result = await db.execute(
        update(Alert)
        .where(Alert.is_resolved == False)  # noqa: E712
        .values(is_resolved=True, resolved_at=datetime.now(timezone.utc), resolved_by=user_id)
        .execution_options(synchronize_session=False)
    )
    await db.flush()
    return result.rowcount or 0


async def get_unresolved_count(db: AsyncSession) -> int:
    """Return count of unresolved alerts."""
    result = await db.execute(
        select(func.count(Alert.id)).where(Alert.is_resolved == False)  # noqa: E712
    )
    return result.scalar() or 0


async def list_alerts(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 50,
    severity: str | None = None,
    alert_type: str | None = None,
    is_resolved: bool | None = None,
    station_id=None,
) -> tuple[list[Alert], int, int]:
    """List alerts with optional filters. Returns (alerts, total, unresolved_count).
    station_id: that station's alerts plus the ones tied to no station (the control
    room's Errors tab, so a busy station cannot push another's errors off the page)."""
    query = select(Alert)
    count_query = select(func.count(Alert.id))
    if station_id is not None:
        from sqlalchemy import or_

        cond = or_(Alert.station_id == station_id, Alert.station_id.is_(None))
        query = query.where(cond)
        count_query = count_query.where(cond)

    # The page sends "warning" / "queue_empty"; compare as the enum (a bare string
    # would be matched against the stored enum names and fail).
    if severity is not None:
        sev = AlertSeverity(severity.lower())
        query = query.where(Alert.severity == sev)
        count_query = count_query.where(Alert.severity == sev)
    if alert_type is not None:
        typ = AlertType(alert_type.lower())
        query = query.where(Alert.alert_type == typ)
        count_query = count_query.where(Alert.alert_type == typ)
    if is_resolved is not None:
        query = query.where(Alert.is_resolved == is_resolved)
        count_query = count_query.where(Alert.is_resolved == is_resolved)

    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        # id breaks ties so pages stay stable when alerts share a time (one transaction)
        query.order_by(Alert.created_at.desc(), Alert.id.desc()).offset(skip).limit(limit)
    )
    alerts = list(result.scalars().all())

    unresolved = await get_unresolved_count(db)

    return alerts, total, unresolved


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _block_days(block):
    """The days a block starts on: None for every day (daily, or no pattern), else
    ("wd", weekday numbers), ("md", days of the month) or ("date", (first, last))."""
    kind = getattr(block.recurrence_type, "value", block.recurrence_type)
    pattern = block.recurrence_pattern or []
    if kind == "weekly" and pattern:
        return "wd", {_WEEKDAYS.index(str(d).lower()) for d in pattern if str(d).lower() in _WEEKDAYS}
    if kind == "monthly" and pattern:
        days = set()
        for d in pattern:
            try:
                days.add(int(d))
            except (TypeError, ValueError):
                pass
        return "md", days
    if kind == "one_time" and block.start_date:
        return "date", (block.start_date, block.end_date or block.start_date)
    return None


def _next_days(days):
    """The same days moved one day on (where the part after midnight airs)."""
    if days is None:
        return None
    kind, value = days
    if kind == "wd":
        return kind, {(d + 1) % 7 for d in value}
    if kind == "md":
        return kind, {d + 1 if d < 31 else 1 for d in value}
    first, last = value
    return kind, (first + timedelta(days=1), last + timedelta(days=1))


def _days_meet(a, b) -> bool:
    if a is None or b is None or a[0] != b[0]:
        return True  # every day, or kinds that cannot be compared: assume they meet
    if a[0] == "date":
        return a[1][0] <= b[1][1] and b[1][0] <= a[1][1]
    return bool(a[1] & b[1])


def _segments(block) -> list[tuple[int, float, float]]:
    """(day offset, start minute, end minute); a block past midnight has two parts."""
    def minutes(t):
        return t.hour * 60 + t.minute + t.second / 60

    start, end = minutes(block.start_time), minutes(block.end_time)
    if end >= start:
        return [(0, start, end)]
    return [(0, start, 24 * 60), (1, 0, end)]


def blocks_overlap(a, b) -> bool:
    """Whether two blocks air at the same time on a shared day."""
    days_a, days_b = _block_days(a), _block_days(b)
    for off_a, start_a, end_a in _segments(a):
        for off_b, start_b, end_b in _segments(b):
            if not (start_a < end_b and end_a > start_b):
                continue
            da = _next_days(days_a) if off_a else days_a
            db = _next_days(days_b) if off_b else days_b
            if _days_meet(da, db):
                return True
    return False


async def detect_schedule_conflicts(
    db: AsyncSession,
    schedule_id: UUID | str,
    block_id: UUID | str,
) -> list[Alert]:
    """Check for overlapping blocks in the same schedule and create alerts."""
    from app.models.schedule_block import ScheduleBlock

    result = await db.execute(
        select(ScheduleBlock).where(ScheduleBlock.schedule_id == schedule_id)
    )
    blocks = list(result.scalars().all())

    # Find the target block
    target = None
    for b in blocks:
        if str(b.id) == str(block_id):
            target = b
            break

    if not target:
        return []

    alerts = []
    for other in blocks:
        if str(other.id) == str(target.id):
            continue
        # Start-only blocks hand over to each other; they cannot overlap.
        if getattr(target, "until_next", False) or getattr(other, "until_next", False):
            continue
        # Overlap on a shared day, counting the part of a block that runs past midnight
        if target.start_time is not None and other.start_time is not None:
            if blocks_overlap(target, other):
                alert = await create_alert(
                    db,
                    alert_type=AlertType.SCHEDULE_CONFLICT,
                    severity=AlertSeverity.WARNING,
                    title=f"Schedule conflict: {target.name} vs {other.name}",
                    message=(
                        f"Block '{target.name}' ({target.start_time}–{target.end_time}) "
                        f"overlaps with '{other.name}' ({other.start_time}–{other.end_time})"
                    ),
                    context={
                        "schedule_id": str(schedule_id),
                        "block_a_id": str(target.id),
                        "block_b_id": str(other.id),
                    },
                )
                alerts.append(alert)

    return alerts


async def _notify_users(db: AsyncSession, alert: Alert) -> None:
    """Send SMS/WhatsApp to users whose preferences match this alert's severity."""
    from app.services.sms_service import send_sms, send_whatsapp

    alert_severity_level = _SEVERITY_ORDER.get(alert.severity.value, 0)

    # Find users with phone numbers
    result = await db.execute(
        select(User).where(
            User.phone_number.isnot(None),
            User.phone_number != "",
            User.is_active == True,  # noqa: E712
        )
    )
    users = result.scalars().all()

    body = f"[{alert.severity.value.upper()}] {alert.title}\n{alert.message}"

    for user in users:
        # Sponsors never get station alerts; staff limited to some stations get only
        # theirs (an alert with no station, e.g. a server restart, goes to everyone).
        if user.role == UserRole.SPONSOR:
            continue
        if alert.station_id is not None and user.station_ids is not None \
                and str(alert.station_id) not in user.station_ids:
            continue
        prefs = user.alert_preferences or {}
        min_severity = prefs.get("min_severity", "warning")
        user_threshold = _SEVERITY_ORDER.get(min_severity, 1)

        if alert_severity_level < user_threshold:
            continue

        if prefs.get("sms_enabled", False):
            await send_sms(user.phone_number, body)

        if prefs.get("whatsapp_enabled", False):
            await send_whatsapp(user.phone_number, body)
