"""Holiday music: each holiday's list of songs and shiurim, and how each station mixes
it in before and during the holiday. See services/holiday_music.py, wiki/holiday-music.md.
"""

import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, may_operate_station, require_manager, require_staff
from app.models.asset import Asset
from app.models.holiday_music import HolidayItem, HolidaySetting
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.models.user import User
from app.services.holiday_music import (
    DEFAULTS,
    HOLIDAY_NAMES,
    HOLIDAYS,
    LIMITS,
    Season,
    Setting,
    clean_setting,
    every_n,
    kind_of,
    load_settings,
    seasons_between,
    share_on,
)
from app.services.shabbos_service import is_israel_timezone

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/holiday-music", tags=["holiday-music"])

# Words in a title, artist or album that suggest a holiday (the Suggest button)
KEYWORDS = {
    "rosh_hashana": ["rosh hashan", "rosh hashon", "shofar", "avinu malkeinu", "tekiah", "teshuva", "elul", "hayom harat", "unesaneh"],
    "yom_kippur": ["yom kippur", "kol nidre", "kol nidrei", "neilah", "viduy", "selichos", "selichot", "teshuva"],
    "sukkos": ["sukkos", "sukkot", "succos", "sukkah", "succah", "lulav", "esrog", "etrog", "simchas torah", "simchat torah", "hoshana", "ushpizin", "simchas beis", "simchat beit", "shemini atzeres"],
    "chanukah": ["chanuk", "hanuk", "hanuc", "chanuc", "maoz tzur", "maoz tsur", "dreidel", "dreidle", "menorah", "latke", "al hanisim", "haneiros", "mi yemalel", "sevivon"],
    "tu_bishvat": ["tu bishvat", "tu b'shvat", "tu bshvat", "shivas haminim", "seven species"],
    "purim": ["purim", "haman", "mordechai", "esther", "megill", "shoshanas yaakov", "shoshanat yaakov", "mishenichnas adar", "adar", "venahafoch", "ad delo yada", "grogger", "groggers", "mishloach"],
    "pesach": ["pesach", "passover", "seder", "haggad", "matzah", "matza", "maror", "chad gadya", "dayeinu", "dayenu", "echad mi yodea", "ma nishtana", "mah nishtana", "plague", "makos", "makkot", "exodus", "yetzias mitzrayim", "yetziat", "mitzrayim", "kriyas yam suf", "az yashir", "had gadya", "10 plagues", "ten plagues"],
    "lag_baomer": ["lag baomer", "lag b'omer", "lag ba'omer", "bar yochai", "meron", "rashbi", "rabbi shimon"],
    "shavuos": ["shavuos", "shavuot", "matan torah", "mattan torah", "har sinai", "mount sinai", "akdamus", "akdamut", "ruth", "bikkurim", "torah tziva"],
}


# Daf Yomi shiurim are named after their tractate ("Pesachim 110", "Sukkah 12",
# "Megillah 7"): a holiday's word in them is not about the holiday, and listing them
# would take them out of the Daf Yomi rotation.
DAF_TITLE = re.compile(
    r"^\s*(pesachim|sukkah|succah|megillah|megilah|rosh hashanah|rosh hashana|yoma|beitzah|beitza|taanis|"
    r"taanit|chagigah|moed katan|shekalim|eruvin|menachos|bikkurim)\b[\s,.:-]*\d+",
    re.I,
)
NOT_SUGGESTED_CATEGORIES = ("do_not_play", "daf_yomi", "block_only")


def _key(holiday: str) -> str:
    if holiday not in HOLIDAY_NAMES:
        raise HTTPException(status_code=404, detail="No such holiday")
    return holiday


async def _station(db: AsyncSession, station_id) -> Station:
    station = await db.get(Station, station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")
    return station


def _today(station: Station) -> date:
    return datetime.now(UTC).astimezone(ZoneInfo(station.timezone or "UTC")).date()


def _next_season(key: str, today: date, israel: bool, setting: Setting) -> Season:
    """The holiday's run that is under way or next to come (its lead time counted)."""
    for s in seasons_between(today - timedelta(days=10), today + timedelta(days=500), israel):
        if s.key == key and s.last >= today:
            return s
    raise HTTPException(status_code=500, detail="Calendar error")


def _season_out(season: Season, setting: Setting, today: date) -> dict:
    starts = season.first - timedelta(days=setting.lead_days) if setting.lead_days else season.first
    ends = season.last if setting.stop == "end" else season.first - timedelta(days=1)
    share = share_on(setting, season, today)
    return {
        "first_day": season.first.isoformat(),
        "last_day": season.last.isoformat(),
        "mix_starts": starts.isoformat(),
        "mix_ends": ends.isoformat(),
        "today_share": round(share, 4),
        "today_every": every_n(share),
        "in_season": share > 0,
        "days_until": (season.first - today).days,
    }


async def _counts(db: AsyncSession, station_id) -> dict[str, dict]:
    rows = (
        await db.execute(
            select(HolidayItem.holiday, Asset.asset_type, func.count())
            .join(Asset, Asset.id == HolidayItem.asset_id)
            .where(or_(HolidayItem.station_id.is_(None), HolidayItem.station_id == station_id))
            .group_by(HolidayItem.holiday, Asset.asset_type)
        )
    ).all()
    out: dict[str, dict] = {}
    for key, asset_type, n in rows:
        kind = kind_of(asset_type) or "other"
        c = out.setdefault(key, {"music": 0, "long": 0, "other": 0})
        c[kind] += n
    return out


@router.get("")
async def overview(
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    """Every holiday for one station: its next dates on the station's own calendar,
    when the mix starts and ends, today's rate, settings and how many items it has."""
    station = await _station(db, station_id)
    israel = is_israel_timezone(station.timezone)
    today = _today(station)
    settings = await load_settings(db, station.id)
    counts = await _counts(db, station.id)
    out = []
    for key, name, *_ in HOLIDAYS:
        setting = settings[key]
        season = _next_season(key, today, israel, setting)
        out.append({
            "key": key, "name": name, "settings": setting.as_dict(),
            "items": counts.get(key, {"music": 0, "long": 0, "other": 0}),
            **_season_out(season, setting, today),
        })
    out.sort(key=lambda h: h["mix_starts"] if not h["in_season"] else "0")
    return {
        "station_id": str(station.id), "station_name": station.name, "israel_calendar": israel,
        "today": today.isoformat(), "defaults": DEFAULTS, "limits": LIMITS, "holidays": out,
    }


@router.get("/{holiday}/days")
async def days(
    holiday: str,
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    """Day by day from the start of the mix to its end: the rate on each day."""
    key = _key(holiday)
    station = await _station(db, station_id)
    israel = is_israel_timezone(station.timezone)
    today = _today(station)
    setting = (await load_settings(db, station.id))[key]
    season = _next_season(key, today, israel, setting)
    start = season.first - timedelta(days=max(setting.lead_days, 0))
    rows = []
    d = start
    while d <= season.last:
        share = share_on(setting, season, d)
        rows.append({
            "date": d.isoformat(), "share": round(share, 4), "every": every_n(share),
            "phase": "holiday" if d >= season.first else "before",
            "today": d == today,
        })
        d += timedelta(days=1)
    return {"holiday": key, "name": HOLIDAY_NAMES[key], **_season_out(season, setting, today), "days": rows}


class SettingIn(BaseModel):
    enabled: bool | None = None
    lead_days: int | None = Field(None, ge=LIMITS["lead_days"][0], le=LIMITS["lead_days"][1])
    start_every: int | None = Field(None, ge=1, le=200)
    peak_every: int | None = Field(None, ge=1, le=200)
    during_every: int | None = Field(None, ge=1, le=200)
    include_shiurim: bool | None = None
    stop: str | None = Field(None, pattern="^(start|end)$")


@router.put("/{holiday}/settings")
async def save_settings(
    holiday: str,
    body: SettingIn,
    station_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    key = _key(holiday)
    station = await _station(db, station_id)
    if not may_operate_station(user, station):
        raise HTTPException(status_code=403, detail="You are not assigned to this station")
    current = (await load_settings(db, station.id))[key]
    new = clean_setting(body.model_dump(exclude_none=True), current)
    row = await db.get(HolidaySetting, (station.id, key))
    if row is None:
        row = HolidaySetting(station_id=station.id, holiday=key)
        db.add(row)
    for k, v in new.as_dict().items():
        setattr(row, k, v)
    await db.commit()
    return {"holiday": key, "settings": new.as_dict()}


@router.get("/{holiday}/items")
async def items(
    holiday: str,
    station_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    """The holiday's list. With station_id: what that station plays (for every
    station or for it); without: everything."""
    key = _key(holiday)
    stmt = (
        select(HolidayItem, Asset, Station.name)
        .join(Asset, Asset.id == HolidayItem.asset_id)
        .outerjoin(Station, Station.id == HolidayItem.station_id)
        .where(HolidayItem.holiday == key)
        .order_by(Asset.asset_type, Asset.title)
    )
    if station_id is not None:
        stmt = stmt.where(or_(HolidayItem.station_id.is_(None), HolidayItem.station_id == station_id))
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": str(item.id), "asset_id": str(asset.id), "title": asset.title, "artist": asset.artist,
            "album": asset.album, "duration": asset.duration, "asset_type": asset.asset_type,
            "category": asset.category, "kind": kind_of(asset.asset_type),
            "station_id": str(item.station_id) if item.station_id else None,
            "station_name": station_name, "all_year": item.all_year,
        }
        for item, asset, station_name in rows
    ]


class ItemsIn(BaseModel):
    asset_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    station_id: uuid.UUID | None = None
    all_year: bool = False


@router.post("/{holiday}/items")
async def add_items(
    holiday: str,
    body: ItemsIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    """Put items on the holiday's list (already listed ones are left as they are).
    Copies of them already waiting in a queue as ordinary filler are taken out,
    so a holiday-only item does not air outside its season from an older fill."""
    key = _key(holiday)
    if body.station_id is not None:
        station = await _station(db, body.station_id)
        if not may_operate_station(user, station):
            raise HTTPException(status_code=403, detail="You are not assigned to this station")
    found = set((await db.execute(select(Asset.id).where(Asset.id.in_(body.asset_ids)))).scalars().all())
    missing = [str(a) for a in body.asset_ids if a not in found]
    if missing:
        raise HTTPException(status_code=404, detail=f"Not in the library: {', '.join(missing[:5])}")
    have = set(
        (await db.execute(select(HolidayItem.asset_id).where(
            HolidayItem.holiday == key, HolidayItem.asset_id.in_(body.asset_ids),
            HolidayItem.station_id.is_(None) if body.station_id is None else HolidayItem.station_id == body.station_id,
        ))).scalars().all()
    )
    added = []
    for asset_id in dict.fromkeys(body.asset_ids):
        if asset_id in have:
            continue
        db.add(HolidayItem(id=uuid.uuid4(), holiday=key, asset_id=asset_id, station_id=body.station_id,
                           all_year=body.all_year))
        added.append(asset_id)
    removed = 0
    if added and not body.all_year:
        removed = (await db.execute(
            delete(QueueEntry).where(
                QueueEntry.asset_id.in_(added), QueueEntry.status == "pending",
                QueueEntry.source == "auto", QueueEntry.preempt_at.is_(None),
            )
        )).rowcount or 0
    await db.commit()
    return {"added": len(added), "already_listed": len(have), "removed_from_queues": removed}


class ItemPatch(BaseModel):
    all_year: bool | None = None
    # "" or null with every_station=True: every station
    station_id: uuid.UUID | None = None
    every_station: bool | None = None


@router.patch("/items/{item_id}")
async def update_item(
    item_id: uuid.UUID,
    body: ItemPatch,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_manager),
):
    item = await db.get(HolidayItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Not on the list")
    if body.all_year is not None:
        item.all_year = body.all_year
    if body.every_station:
        item.station_id = None
    elif body.station_id is not None:
        station = await _station(db, body.station_id)
        if not may_operate_station(user, station):
            raise HTTPException(status_code=403, detail="You are not assigned to this station")
        item.station_id = body.station_id
    await db.commit()
    return {"id": str(item.id), "station_id": str(item.station_id) if item.station_id else None,
            "all_year": item.all_year}


@router.delete("/items/{item_id}")
async def remove_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_manager),
):
    item = await db.get(HolidayItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Not on the list")
    await db.delete(item)
    await db.commit()
    return {"removed": True}


@router.get("/{holiday}/suggest")
async def suggest(
    holiday: str,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_staff),
):
    """Library items whose title, artist or album has a word that suggests the
    holiday (or `q`), not on its list yet: a starting point to pick from."""
    key = _key(holiday)
    words = [q.strip().lower()] if q and q.strip() else KEYWORDS.get(key, [])
    if not words:
        return []
    text = func.lower(func.coalesce(Asset.title, "") + " " + func.coalesce(Asset.artist, "") + " "
                      + func.coalesce(Asset.album, ""))
    listed = select(HolidayItem.asset_id).where(HolidayItem.holiday == key)
    rows = (
        await db.execute(
            select(Asset)
            .where(
                or_(*[text.like(f"%{w}%") for w in words]),
                Asset.id.notin_(listed),
                Asset.asset_type.in_(["music", "shiur", "stories", "story", "daf_yomi", "lecture"]),
                or_(Asset.category.is_(None), func.lower(Asset.category).notin_(NOT_SUGGESTED_CATEGORIES)),
            )
            .order_by(Asset.asset_type, Asset.title)
            .limit(limit * 3)
        )
    ).scalars().all()
    out = []
    for a in rows:
        if DAF_TITLE.match(a.title or "") and not (q and q.strip()):
            continue
        blob = f"{a.title or ''} {a.artist or ''} {a.album or ''}".lower()
        # whole words from their start ("haman", not "rachaman"); and Chanukas Habayis
        # (a house's dedication) is not Chanukah
        if not any(re.search(r"(?<![a-z])" + re.escape(w), blob) for w in words):
            continue
        if key == "chanukah" and re.search(r"chanuk[ae][st] habay", blob) and not (q and q.strip()):
            continue
        out.append({
            "asset_id": str(a.id), "title": a.title, "artist": a.artist, "album": a.album,
            "duration": a.duration, "asset_type": a.asset_type, "category": a.category,
            "kind": kind_of(a.asset_type),
            "matched": next((w for w in words if re.search(r"(?<![a-z])" + re.escape(w), blob)), None),
        })
    return out[:limit]
