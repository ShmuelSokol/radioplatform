import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.security import hash_password
from app.db.session import get_db
from app.models.user import User, UserRole
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse, UserResponse
from app.services.auth_service import authenticate_user, create_tokens, refresh_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(db, body.email, body.password)
    return create_tokens(user)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    return await refresh_access_token(db, body.refresh_token)


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/seed", status_code=201)
async def seed_admin(db: AsyncSession = Depends(get_db)):
    """Create default admin user if none exists. Only works when no admin exists."""
    result = await db.execute(select(User).where(User.role == UserRole.ADMIN))
    existing = result.scalar_one_or_none()
    if existing:
        return {"message": "Admin user already exists", "email": existing.email}

    admin = User(
        id=uuid.uuid4(),
        email="admin@radioplatform.com",
        hashed_password=hash_password("admin123"),
        role=UserRole.ADMIN,
        display_name="Admin",
        is_active=True,
    )
    db.add(admin)
    await db.commit()
    return {"message": "Admin user created", "email": admin.email}
