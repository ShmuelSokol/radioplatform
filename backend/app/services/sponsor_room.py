"""How full each time slot is: room for sponsorships against what the planner booked.

Read only. Used by the "Book a sponsorship" window (pick slots with room) and the
Sponsorship board (the next days as a grid of slots). The planner itself
(spot_schedule_service) is not changed: this counts its own breaks and placements.

Room in a slot on a day:
- With program blocks that have sponsor places: the places of every airing that
  starts inside the slot, leaving out airings during Shabbos/Yom Tov and airings
  inside a shiur or story (the planner never seats sponsorships there).
- Without such blocks the planner uses clock slots (:15/:30/:45 by default), one
  sponsorship each, so the room is the number of those minutes in the slot that are
  on the air.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sponsor import Sponsor
from app.models.station import Station
from app.services.spot_schedule_service import (
    DAYPARTS,
    DEFAULT_SLOT_MINUTES,
    TIME_SLOTS,
    load_blackouts,
    plan_for_station,
    station_tz,
    talk_tier,
    talk_windows,
)

# The board's rows: the six daytime slots, then overnight
BOARD_SLOTS: list[tuple[str, int, int]] = [
    *((k, a, b) for k, (a, b) in TIME_SLOTS.items()),
    ("overnight", 0, 6),
]


@dataclass
class CellSponsor:
    sponsor_id: str
    sponsor_name: str
    schedule_id: str
    contract: str | None
    plays: int = 0


@dataclass
class Cell:
    date: date
    slot: str
    room: int = 0
    booked: int = 0
    no_room: int = 0  # plays the planner wanted here and could not seat
    off_air: bool = False  # the whole slot is in Shabbos/Yom Tov
    sponsors: dict[str, CellSponsor] = field(default_factory=dict)


def slot_of(hour: int) -> str:
    for key, a, b in BOARD_SLOTS:
        if a <= hour < b:
            return key
    return "overnight"


async def slot_room(db: AsyncSession, station: Station, days: int = 7, now: datetime | None = None) -> dict:
    from app.services.program_block_service import block_talk_windows, load_breaks
    from app.services.station_modes import spots_on

    now = now or datetime.now(UTC)
    tz = station_tz(station.timezone)
    today = now.astimezone(tz).date()
    start = now - timedelta(days=1)
    end = now + timedelta(days=days + 1)

    cells: dict[tuple[date, str], Cell] = {}
    for d in range(days):
        day = today + timedelta(days=d)
        for key, _a, _b in BOARD_SLOTS:
            cells[(day, key)] = Cell(day, key)

    blackouts = await load_blackouts(db, station.id, start, end)

    def dark(local: datetime) -> bool:
        at = local.astimezone(UTC)
        return any(b.start <= at < b.end for b in blackouts)

    # Off the air: every minute of the slot falls in a blackout
    for (day, key), cell in cells.items():
        a, b = next((a, b) for k, a, b in BOARD_SLOTS if k == key)
        first = datetime(day.year, day.month, day.day, a, tzinfo=tz)
        last = datetime(day.year, day.month, day.day, b - 1, 59, tzinfo=tz)
        cell.off_air = dark(first) and dark(last) and all(
            dark(first + timedelta(minutes=m)) for m in range(0, (b - a) * 60, 15)
        )

    breaks = await load_breaks(db, station, start, end)
    mode = "blocks" if breaks else "clock"
    if breaks:
        quiet = list(await talk_windows(db, station, tz, now)) + list(
            await block_talk_windows(db, station, start, end)
        )
        for br in breaks:
            cell = cells.get((br.local.date(), slot_of(br.local.hour)))
            if cell is None or br.capacity <= 0 or dark(br.local):
                continue
            if talk_tier(br, quiet) != 0:
                continue
            cell.room += br.capacity
    else:
        minutes = (station.automation_config or {}).get("ad_slot_minutes") or DEFAULT_SLOT_MINUTES
        for (day, key), cell in cells.items():
            a, b = next((a, b) for k, a, b in BOARD_SLOTS if k == key)
            for h in range(a, b):
                for m in minutes:
                    local = datetime(day.year, day.month, day.day, h, int(m), tzinfo=tz)
                    if not dark(local):
                        cell.room += 1

    placements, unplaced = await plan_for_station(db, station, days=days, now=now)
    sponsor_ids = {str(p.schedule.sponsor_id) for p in placements}
    names: dict[str, str] = {}
    if sponsor_ids:
        rows = await db.execute(select(Sponsor.id, Sponsor.name).where(Sponsor.id.in_(sponsor_ids)))
        names = {str(r[0]): r[1] for r in rows.all()}
    for p in placements:
        cell = cells.get((p.local.date(), slot_of(p.local.hour)))
        if cell is None:
            continue
        cell.booked += 1
        sid = str(p.schedule.id)
        entry = cell.sponsors.get(sid)
        if entry is None:
            entry = cell.sponsors[sid] = CellSponsor(
                sponsor_id=str(p.schedule.sponsor_id),
                sponsor_name=names.get(str(p.schedule.sponsor_id), "Unknown"),
                schedule_id=sid,
                contract=p.schedule.name,
            )
        entry.plays += 1
    for u in unplaced:
        if u.blackout is not None:
            continue
        a, _b = next(((a, b) for k, a, b in BOARD_SLOTS if k == u.daypart), (None, None))
        if a is None:  # a wide daypart (morning...): count it at its first slot
            a = DAYPARTS.get(u.daypart, (6, 12))[0]
        cell = cells.get((u.local_date, slot_of(a)))
        if cell is not None:
            cell.no_room += 1

    return {
        "station_id": str(station.id),
        "timezone": station.timezone,
        "days": days,
        "mode": mode,
        "spots_off": not spots_on(station),
        "slots": [{"key": k, "start_hour": a, "end_hour": b} for k, a, b in BOARD_SLOTS],
        "cells": [
            {
                "date": c.date.isoformat(),
                "slot": c.slot,
                "room": c.room,
                "booked": c.booked,
                "no_room": c.no_room,
                "off_air": c.off_air,
                "sponsors": sorted(
                    (vars(s) for s in c.sponsors.values()), key=lambda s: (-s["plays"], s["sponsor_name"])
                ),
            }
            for c in cells.values()
        ],
    }
