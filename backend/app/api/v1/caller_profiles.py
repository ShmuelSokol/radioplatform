"""Callers the staff marked: priority (listed first) or blocked (calls refused).
The marking itself happens on a call (PUT /live-shows/{show}/calls/{call}/caller);
this is the list, so a block can be lifted when the person is not calling.
See app/services/caller_history.py and wiki/live-shows.md."""
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, require_dj_or_manager
from app.models.caller_profile import CallerProfile
from app.models.user import User

router = APIRouter(prefix="/caller-profiles", tags=["live-shows"])


def _row(p: CallerProfile) -> dict:
    return {
        "id": str(p.id), "name": p.name, "standing": p.standing, "note": p.note,
        "kind": "phone" if p.key.startswith("tel:") else "web",
        # A phone caller is shown by number; a browser's id means nothing to a person
        "phone": p.key[4:] if p.key.startswith("tel:") else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("")
async def list_marked_callers(db: AsyncSession = Depends(get_db),
                              _user: User = Depends(require_dj_or_manager)):
    """Priority and blocked callers, newest change first."""
    rows = (await db.execute(
        select(CallerProfile).where(CallerProfile.standing != "normal")
        .order_by(CallerProfile.updated_at.desc()).limit(500)
    )).scalars().all()
    return {"callers": [_row(p) for p in rows]}


class ProfileChange(BaseModel):
    standing: Literal["normal", "priority", "blocked"] | None = None
    note: str | None = Field(default=None, max_length=500)


@router.put("/{profile_id}")
async def change_marked_caller(profile_id: uuid.UUID, body: ProfileChange,
                               db: AsyncSession = Depends(get_db),
                               user: User = Depends(require_dj_or_manager)):
    p = await db.get(CallerProfile, profile_id)
    if p is None:
        raise HTTPException(404, "Caller not found")
    if body.standing is not None:
        p.standing = body.standing
    if body.note is not None:
        p.note = body.note.strip() or None
    p.updated_by = user.id
    await db.commit()
    await db.refresh(p)
    return _row(p)
