import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_admin
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.schedule_rule import ScheduleRule
from app.models.asset import Asset
from app.models.user import User
from app.schemas.rule import (
    RuleCreate, RuleListResponse, RuleOut, RuleUpdate,
    SchedulePreview, ScheduleSlot,
)

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=RuleListResponse)
async def list_rules(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    station_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    from sqlalchemy import or_
    q = select(ScheduleRule)
    if station_id is not None:
        q = q.where(or_(ScheduleRule.station_id == station_id, ScheduleRule.station_id.is_(None)))
    count_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = count_result.scalar() or 0
    result = await db.execute(q.offset(skip).limit(limit).order_by(ScheduleRule.priority.desc()))
    rules = result.scalars().all()
    return RuleListResponse(rules=rules, total=total)


@router.post("", response_model=RuleOut, status_code=201)
async def create_rule(
    body: RuleCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
):
    rule = ScheduleRule(
        id=uuid.uuid4(),
        name=body.name,
        description=body.description,
        rule_type=body.rule_type,
        asset_type=body.asset_type,
        category=body.category,
        hour_start=body.hour_start,
        hour_end=body.hour_end,
        days_of_week=body.days_of_week,
        interval_minutes=body.interval_minutes,
        songs_between=body.songs_between,
        priority=body.priority,
        is_active=body.is_active,
        constraints=body.constraints,
        station_id=body.station_id,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/{rule_id}", response_model=RuleOut)
async def update_rule(
    rule_id: uuid.UUID,
    body: RuleUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
):
    result = await db.execute(select(ScheduleRule).where(ScheduleRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise NotFoundError("Rule not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
):
    result = await db.execute(select(ScheduleRule).where(ScheduleRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise NotFoundError("Rule not found")
    await db.delete(rule)
    await db.commit()


@router.get("/preview", response_model=SchedulePreview)
async def preview_schedule(
    date: str = Query(None, description="Date in YYYY-MM-DD format"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Generate a preview of what the schedule would look like for a given date."""
    target_date = datetime.strptime(date, "%Y-%m-%d") if date else datetime.now(timezone.utc)
    day_of_week = target_date.weekday()  # 0=Mon

    # Fetch active rules for this day
    result = await db.execute(
        select(ScheduleRule).where(ScheduleRule.is_active == True).order_by(ScheduleRule.priority.desc())
    )
    rules = result.scalars().all()
    active_rules = [r for r in rules if str(day_of_week) in r.days_of_week.split(",")]

    # Fetch asset counts per type for context
    slots: list[ScheduleSlot] = []
    current_time = target_date.replace(hour=0, minute=0, second=0)
    end_of_day = current_time + timedelta(hours=24)
    song_counter = 0

    while current_time < end_of_day:
        hour = current_time.hour
        best_rule = None
        for rule in active_rules:
            if rule.hour_start <= hour < rule.hour_end:
                if rule.rule_type == "interval" and rule.interval_minutes:
                    minute_of_day = hour * 60 + current_time.minute
                    if minute_of_day % rule.interval_minutes == 0:
                        best_rule = rule
                        break
                elif rule.rule_type == "rotation":
                    if rule.songs_between and song_counter % (rule.songs_between + 1) == rule.songs_between:
                        best_rule = rule
                        break
                elif rule.rule_type == "fixed_time":
                    if current_time.minute == 0:
                        best_rule = rule
                        break
                elif rule.rule_type == "daypart":
                    best_rule = rule

        if best_rule:
            duration = best_rule.interval_minutes or 4
            slots.append(ScheduleSlot(
                time=current_time.strftime("%H:%M"),
                asset_type=best_rule.asset_type,
                category=best_rule.category,
                rule_name=best_rule.name,
                duration_minutes=duration,
            ))
            current_time += timedelta(minutes=duration)
            if best_rule.asset_type == "music":
                song_counter += 1
            else:
                song_counter = 0
        else:
            # Default: play music
            slots.append(ScheduleSlot(
                time=current_time.strftime("%H:%M"),
                asset_type="music",
                category=None,
                rule_name="Default Rotation",
                duration_minutes=4,
            ))
            current_time += timedelta(minutes=4)
            song_counter += 1

    return SchedulePreview(
        date=target_date.strftime("%Y-%m-%d"),
        slots=slots,
        total_hours=24.0,
    )
