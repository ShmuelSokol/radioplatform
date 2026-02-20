import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_admin
from app.core.security import hash_password
from app.core.exceptions import NotFoundError
from app.db.session import get_db
from app.models.user import User, UserRole
from app.models.user_preference import UserPreference
from app.schemas.user_mgmt import UserCreate, UserListResponse, UserOut, UserUpdate
from app.schemas.user_preference import UserPreferenceResponse, UserPreferenceUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/public/hosts")
async def list_public_hosts(db: AsyncSession = Depends(get_db)):
    """Public: list all hosts/DJs with public profiles."""
    result = await db.execute(
        select(User).where(
            User.is_public == True,
            User.is_active == True,
        ).order_by(User.display_name)
    )
    hosts = result.scalars().all()
    return {
        "hosts": [
            {
                "id": str(h.id),
                "display_name": h.display_name or h.email.split("@")[0],
                "title": h.title,
                "bio": h.bio if hasattr(h, 'bio') else None,
                "photo_url": h.photo_url if hasattr(h, 'photo_url') else None,
                "social_links": h.social_links if hasattr(h, 'social_links') else None,
            }
            for h in hosts
        ]
    }


@router.get("", response_model=UserListResponse)
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    count_result = await db.execute(select(func.count()).select_from(User))
    total = count_result.scalar() or 0
    result = await db.execute(select(User).offset(skip).limit(limit).order_by(User.created_at))
    users = result.scalars().all()
    return UserListResponse(users=users, total=total)


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    role = UserRole(body.role) if body.role in [r.value for r in UserRole] else UserRole.VIEWER
    user = User(
        id=uuid.uuid4(),
        email=body.email,
        hashed_password=hash_password(body.password),
        role=role,
        display_name=body.display_name,
        phone_number=body.phone_number,
        title=body.title,
        alert_preferences=body.alert_preferences,
        bio=body.bio,
        photo_url=body.photo_url,
        is_public=body.is_public,
        social_links=body.social_links,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    from app.services.audit_service import log_action
    await log_action(
        db, user_id=_admin.id, user_email=_admin.email, action="create",
        resource_type="user", resource_id=str(user.id),
        detail=f"Created user '{user.email}' with role '{role.value}'",
        request_id=getattr(request.state, "request_id", None),
    )
    return user


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")
    if body.email is not None:
        user.email = body.email
    if body.password is not None:
        user.hashed_password = hash_password(body.password)
    if body.role is not None:
        user.role = UserRole(body.role)
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.phone_number is not None:
        user.phone_number = body.phone_number
    if body.title is not None:
        user.title = body.title
    if body.alert_preferences is not None:
        user.alert_preferences = body.alert_preferences
    if body.bio is not None:
        user.bio = body.bio
    if body.photo_url is not None:
        user.photo_url = body.photo_url
    if body.is_public is not None:
        user.is_public = body.is_public
    if body.social_links is not None:
        user.social_links = body.social_links
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if user_id == admin.id:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("User not found")
    from app.services.audit_service import log_action
    await log_action(
        db, user_id=admin.id, user_email=admin.email, action="delete",
        resource_type="user", resource_id=str(user_id),
        detail=f"Deleted user '{user.email}'",
        request_id=getattr(request.state, "request_id", None),
    )
    await db.delete(user)
    await db.commit()


@router.get("/audit-log")
async def list_audit_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    resource_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Admin: view persistent audit log of all write actions."""
    from app.models.audit_log import AuditLog
    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    count_q = select(func.count()).select_from(AuditLog)
    if resource_type:
        count_q = count_q.where(AuditLog.resource_type == resource_type)
    total = (await db.execute(count_q)).scalar() or 0
    result = await db.execute(query.offset(skip).limit(limit))
    logs = result.scalars().all()
    return {
        "total": total,
        "logs": [
            {
                "id": str(l.id),
                "user_email": l.user_email,
                "action": l.action,
                "resource_type": l.resource_type,
                "resource_id": l.resource_id,
                "detail": l.detail,
                "changes": l.changes,
                "request_id": l.request_id,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ],
    }


@router.get("/me/preferences", response_model=UserPreferenceResponse)
async def get_my_preferences(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(UserPreference).where(UserPreference.user_id == user.id))
    pref = result.scalar_one_or_none()
    if not pref:
        return UserPreferenceResponse()
    return pref


@router.patch("/me/preferences", response_model=UserPreferenceResponse)
async def update_my_preferences(
    body: UserPreferenceUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(UserPreference).where(UserPreference.user_id == user.id))
    pref = result.scalar_one_or_none()
    if not pref:
        pref = UserPreference(user_id=user.id)
        db.add(pref)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(pref, key, value)
    await db.commit()
    await db.refresh(pref)
    return pref
