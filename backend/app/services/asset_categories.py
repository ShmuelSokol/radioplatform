"""A song in several categories (Shmuel 2026-11-19).

Every item has one main category (`Asset.category`: where it lives in the Library, and
what an "any song" step goes by) and any number of extra ones (`asset_categories`, shown
as "Also in"). A playlist step that names a category draws from every item that has it as
main or extra (`models.asset.in_category`). Holiday lists are the holiday side of the same
idea: an item on the Chanukah list is a Chanukah item, mixed in before and during Chanukah
(services/holiday_music.py); "also all year" keeps it in its ordinary categories too. The
song's categories answer shows both, so one place says where a song can play.
"""
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, asset_categories
from app.models.category import Category

MAX_EXTRA = 12


def clean_name(name: str) -> str:
    """Category names as playlist steps use them: lower case, spaces as underscores."""
    name = re.sub(r"\s+", "_", (name or "").strip().lower())
    if not name or len(name) > 100:
        raise HTTPException(status_code=422, detail="A category name is 1 to 100 characters")
    return name


async def _category_ids(db: AsyncSession, names: list[str]) -> dict[str, uuid.UUID]:
    """Category rows for these names, made when missing."""
    out: dict[str, uuid.UUID] = {}
    if not names:
        return out
    rows = (await db.execute(
        select(Category.id, Category.name).where(func.lower(Category.name).in_(names))
    )).all()
    for cid, name in rows:
        out[name.lower()] = cid
    for name in names:
        if name not in out:
            cat = Category(name=name)
            db.add(cat)
            await db.flush()
            out[name] = cat.id
    return out


async def extra_categories(db: AsyncSession, asset_ids: list) -> dict[str, list[str]]:
    """Extra category names per item id (str), sorted."""
    if not asset_ids:
        return {}
    rows = (await db.execute(
        select(asset_categories.c.asset_id, Category.name)
        .join(Category, Category.id == asset_categories.c.category_id)
        .where(asset_categories.c.asset_id.in_(asset_ids))
    )).all()
    out: dict[str, list[str]] = {}
    for aid, name in rows:
        out.setdefault(str(aid), []).append(name)
    return {k: sorted(v) for k, v in out.items()}


async def describe(db: AsyncSession, asset_id: uuid.UUID) -> dict:
    """Where one item can play: main category, extra ones, holiday lists."""
    from app.models.holiday_music import HolidayItem
    from app.models.station import Station
    from app.services.holiday_music import HOLIDAY_NAMES

    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Item not found")
    extra = (await extra_categories(db, [asset.id])).get(str(asset.id), [])
    rows = (await db.execute(
        select(HolidayItem.holiday, HolidayItem.all_year, Station.name)
        .outerjoin(Station, Station.id == HolidayItem.station_id)
        .where(HolidayItem.asset_id == asset.id)
    )).all()
    lists = [{"holiday": h, "name": HOLIDAY_NAMES.get(h, h), "all_year": bool(a),
              "station_name": s} for h, a, s in rows]
    return {"id": str(asset.id), "category": asset.category,
            "extra_categories": [c for c in extra if c != (asset.category or "").lower()],
            "holiday_lists": sorted(lists, key=lambda x: x["name"])}


async def set_extra(db: AsyncSession, asset_id: uuid.UUID, names: list[str]) -> dict:
    """Replace one item's extra categories."""
    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Item not found")
    wanted = []
    for n in names:
        c = clean_name(n)
        if c != (asset.category or "").lower() and c not in wanted:
            wanted.append(c)
    if len(wanted) > MAX_EXTRA:
        raise HTTPException(status_code=422, detail=f"At most {MAX_EXTRA} extra categories")
    ids = await _category_ids(db, wanted)
    await db.execute(delete(asset_categories).where(asset_categories.c.asset_id == asset.id))
    if ids:
        await db.execute(insert(asset_categories), [
            {"asset_id": asset.id, "category_id": cid} for cid in ids.values()])
    await db.commit()
    return await describe(db, asset.id)


async def bulk_add(db: AsyncSession, asset_ids: list[uuid.UUID], name: str, remove: bool = False) -> int:
    """Add one extra category to many items (or take it off). Returns how many changed."""
    name = clean_name(name)
    cid = (await _category_ids(db, [name]))[name]
    have = set((await db.execute(
        select(asset_categories.c.asset_id).where(
            asset_categories.c.category_id == cid, asset_categories.c.asset_id.in_(asset_ids))
    )).scalars().all())
    have = {str(a) for a in have}
    if remove:
        gone = [a for a in asset_ids if str(a) in have]
        if gone:
            await db.execute(delete(asset_categories).where(
                asset_categories.c.category_id == cid, asset_categories.c.asset_id.in_(gone)))
        await db.commit()
        return len(gone)
    known = set((await db.execute(select(Asset.id).where(Asset.id.in_(asset_ids)))).scalars().all())
    known = {str(a) for a in known}
    new = [a for a in dict.fromkeys(asset_ids) if str(a) in known and str(a) not in have]
    if new:
        await db.execute(insert(asset_categories), [{"asset_id": a, "category_id": cid} for a in new])
    await db.commit()
    return len(new)
