"""Caller history: who has called before, how often they were on air, and what the
staff decided about them (priority, blocked, a note). See wiki/live-shows.md.

A caller is known by `CallInRequest.caller_key`: "tel:<number>" for a phone call,
"web:<id>" for a browser, where the id is a random value the call-in page keeps in
that browser. A web caller who clears their browser data is a new caller; the
screener still sees every call, so a block is a convenience, not a wall."""
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.call_in_request import CallInRequest
from app.models.caller_profile import CallerProfile
from app.schemas.live_show import CallInRequestInDB

STANDINGS = ("normal", "priority", "blocked")
_WEB_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
# Calls looked at per caller for the counts (the newest); nobody needs more
_PER_CALLER = 500


def web_key(browser_id: str | None) -> str | None:
    if browser_id and _WEB_ID.match(browser_id):
        return f"web:{browser_id}"
    return None


def phone_key(number: str | None) -> str | None:
    digits = re.sub(r"[^0-9+]", "", number or "")
    return f"tel:{digits}" if len(digits) >= 5 else None


async def standing_of(db: AsyncSession, key: str | None) -> str:
    if not key:
        return "normal"
    row = (await db.execute(select(CallerProfile.standing).where(CallerProfile.key == key))).scalar_one_or_none()
    return row or "normal"


async def histories(db: AsyncSession, calls) -> dict[str, dict]:
    """For each call (by id): the caller's earlier calls and their standing."""
    keys = {c.caller_key for c in calls if c.caller_key}
    if not keys:
        return {}
    past: dict[str, list] = {}
    rows = (await db.execute(
        select(CallInRequest.id, CallInRequest.caller_key, CallInRequest.air_start, CallInRequest.created_at)
        .where(CallInRequest.caller_key.in_(keys))
        .order_by(CallInRequest.created_at.desc())
        .limit(_PER_CALLER * len(keys))
    )).all()
    for r in rows:
        past.setdefault(r.caller_key, []).append(r)
    profiles = {p.key: p for p in (await db.execute(
        select(CallerProfile).where(CallerProfile.key.in_(keys))
    )).scalars()}
    out = {}
    for c in calls:
        if not c.caller_key:
            continue
        others = [r for r in past.get(c.caller_key, []) if str(r.id) != str(c.id)]
        p = profiles.get(c.caller_key)
        last = others[0].created_at if others else None
        out[str(c.id)] = {
            "calls": len(others),
            "on_air": sum(1 for r in others if r.air_start is not None),
            "last_at": last.isoformat() if last else None,
            "standing": p.standing if p else "normal",
            "note": p.note if p else None,
        }
    return out


async def with_history(db: AsyncSession, calls) -> list[dict]:
    """The calls as the screens get them, each with its caller's history."""
    h = await histories(db, calls)
    out = []
    for c in calls:
        row = CallInRequestInDB.model_validate(c).model_dump(mode="json")
        row["history"] = h.get(str(c.id))
        out.append(row)
    return out


async def set_standing(db: AsyncSession, key: str, name: str | None, standing: str | None,
                       note: str | None, user_id) -> CallerProfile:
    """Create or change a caller's profile. None leaves a field as it is."""
    p = (await db.execute(select(CallerProfile).where(CallerProfile.key == key))).scalar_one_or_none()
    if p is None:
        p = CallerProfile(key=key, standing="normal")
        db.add(p)
    if name:
        p.name = name
    if standing is not None:
        p.standing = standing
    if note is not None:
        p.note = note.strip() or None
    p.updated_by = user_id
    await db.flush()
    return p
