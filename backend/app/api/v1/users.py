import uuid

from fastapi import APIRouter, Depends, Query
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
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        import logging
        logging.getLogger(__name__).error("User creation failed: %s", e, exc_info=True)
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"User creation failed: {e}")
    await db.refresh(user)
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
    await db.delete(user)
    await db.commit()


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
