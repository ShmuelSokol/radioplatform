"""Seed the simulator's database from an export of the real stations' setup.

The export (prod-config.json, written by the PC thread through Supabase REST, read only)
holds the three stations, their program blocks, schedules, playlists, rules, spot
schedules, sponsors, blackouts and the assets they use. Fields the export blanked
("<removed>": switches whose key looked like a secret) are filled with what the stations
are known to use; see BLANKED below. Audio has no files: every asset gets a made-up URL,
since the simulated engine only needs lengths.

Time clips and weather spots are made here (the export has no region/hour metadata):
one clip per hour and half hour for each region, one weather spot per day and region.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import types as sa_types

from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.playlist_template import PlaylistTemplate, TemplateSlot
from app.models.program_block import ProgramBlock
from app.models.schedule import Schedule
from app.models.schedule_block import ScheduleBlock
from app.models.schedule_rule import ScheduleRule
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station

# Switches the export blanked because their names look like keys. All three stations air
# the hourly time, weather and station ID (memory: "KBR Jerusalem: own time checks";
# Main and Jerusalem keep the built-in hour beside their blocks).
BLANKED = {"hourly_station_id": True, "hourly_time_announcement": True, "blocks_keep_hourly": True}

MAIN = "95358e09-1260-48d5-85b5-f851000773c0"
JERUSALEM = "d0740efe-3f25-4067-96f8-d8bc84c64dc3"
STORIES = "13c2697e-b9f8-42e8-b97a-4e1afa3b5a0e"
DAF_BLOCK = {MAIN: "af3e8634-7c23-4c08-9742-ad93f516f147", JERUSALEM: "ef92a1fa-9833-4022-b9af-e4df9c08d606"}
NAMES = {MAIN: "Kol B'ramah Main", JERUSALEM: "KBR Jerusalem", STORIES: "Kol B'ramah - All Day Stories"}
TIME_CLIP_SECONDS = 5.5
WEATHER_SECONDS = 38.0


def _convert(column, value):
    if value is None:
        return None
    t = column.type
    try:
        py = t.python_type
    except NotImplementedError:
        py = None
    if isinstance(t, sa_types.Enum) and isinstance(value, str):
        enum_cls = t.enum_class
        if enum_cls is not None:
            try:
                return enum_cls[value]
            except KeyError:
                return enum_cls(value)
        return value
    if py is uuid.UUID and isinstance(value, str):
        return uuid.UUID(value)
    if py is datetime and isinstance(value, str):
        return datetime.fromisoformat(value)
    if py is date and isinstance(value, str):
        return date.fromisoformat(value)
    if py is time and isinstance(value, str):
        return time.fromisoformat(value)
    return value


def _row(model, data: dict):
    cols = {c.key: c for c in sa_inspect(model).columns}
    kwargs = {k: _convert(cols[k], v) for k, v in data.items() if k in cols}
    return model(**kwargs)


def load_export(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


async def seed(db, export: dict, start: datetime, days: int = 2, stations: set[str] | None = None,
               config_overrides: dict | None = None, block_overrides: dict | None = None,
               extra_assets: list[dict] | None = None, regenerate_windows: bool = False,
               holiday_items: list[dict] | None = None) -> dict:
    """Write the export into an empty database. Returns {station_id: name}."""
    keep = set(stations or NAMES)
    for s in export["stations"]:
        if s["id"] not in keep:
            continue
        config = dict(s.get("automation_config") or {})
        for k, v in list(config.items()):
            if v == "<removed>":
                config[k] = BLANKED.get(k, True)
        # A fresh database: the generator's place in each playlist starts at the top
        config.get("playlist_generation", {}).pop("cursor", None)
        config.update((config_overrides or {}).get(s["id"], {}))
        db.add(Station(id=uuid.UUID(s["id"]), name=s["name"], timezone=s["timezone"],
                       is_active=True, automation_config=config))
    await db.flush()

    for a in export["assets"]:
        db.add(Asset(id=uuid.UUID(a["id"]), title=a["title"] or "untitled", asset_type=a["asset_type"],
                     category=a["category"], duration=a["duration"],
                     file_path=f"https://cdn.example.com/audio/{a['id']}.mp3",
                     metadata_extra={"cues": a.get("cues") or {}} if a.get("cues") else None))
    for a in extra_assets or []:
        # Items a scenario needs that the export lacks, with real analysis if given
        db.add(Asset(id=uuid.UUID(a["id"]), title=a["title"], asset_type=a.get("asset_type", "music"),
                     category=a.get("category"), duration=a["duration"],
                     file_path=f"https://cdn.example.com/audio/{a['id']}.mp3",
                     metadata_extra=({"audio_analysis": a["analysis"]} if a.get("analysis") else None)))
    await db.flush()
    from app.models.holiday_music import HolidayItem

    for h in holiday_items or []:
        # {"holiday": "chanukah", "asset_id": ..., "station_id": None}: a holiday list (holiday music)
        db.add(HolidayItem(id=uuid.uuid4(), holiday=h["holiday"], asset_id=uuid.UUID(h["asset_id"]),
                           station_id=uuid.UUID(h["station_id"]) if h.get("station_id") else None,
                           all_year=bool(h.get("all_year"))))
    await db.flush()
    _add_announcements(db, start, days)
    await db.flush()

    for s in export["sponsors"]:
        db.add(Sponsor(id=uuid.UUID(s["id"]), name=s["name"], length_seconds=s.get("length_seconds") or 30.0,
                       audio_file_path=f"https://cdn.example.com/sponsor/{s['id']}.mp3"))
    for t in export["playlist_templates"]:
        if t.get("station_id") in keep or t.get("station_id") is None:
            db.add(_row(PlaylistTemplate, t))
    await db.flush()
    template_ids = {t["id"] for t in export["playlist_templates"]
                    if t.get("station_id") in keep or t.get("station_id") is None}
    for t in export["template_slots"]:
        if t["template_id"] in template_ids:
            db.add(_row(TemplateSlot, t))
    for s in export["schedules"]:
        if s["station_id"] in keep:
            db.add(_row(Schedule, s))
    await db.flush()
    schedule_ids = {s["id"] for s in export["schedules"] if s["station_id"] in keep}
    for b in export["schedule_blocks"]:
        if b["schedule_id"] in schedule_ids:
            if b.get("playlist_template_id") and b["playlist_template_id"] not in template_ids:
                b = {**b, "playlist_template_id": None}
            db.add(_row(ScheduleBlock, b))
    for r in export["schedule_rules"]:
        if r.get("station_id") is None or r["station_id"] in keep:
            db.add(_row(ScheduleRule, r))
    for b in export["program_blocks"]:
        if b["station_id"] in keep:
            # e.g. {"af3e...": {"is_active": True, "days_of_week": "2"}}: the daily Daf Yomi
            # block is made each day by the PC job and switched off after it aired
            b = {**b, **(block_overrides or {}).get(b["id"], {})}
            db.add(_row(ProgramBlock, b))
    sponsor_ids = {s["id"] for s in export["sponsors"]}
    asset_ids = {a["id"] for a in export["assets"]}
    for s in export["spot_schedules"]:
        if (s.get("station_id") is None or s["station_id"] in keep) and s["sponsor_id"] in sponsor_ids \
                and s["asset_id"] in asset_ids:
            db.add(_row(SpotSchedule, s))
    if regenerate_windows:
        # Windows as the backend makes them today (the export holds what was stored then)
        from app.services.shabbos_service import (
            generate_shabbos_windows,
            generate_yom_tov_windows,
            merge_overlapping_windows,
        )
        from tests.sim.checks import PLACES

        tz_of = {s["id"]: s["timezone"] for s in export["stations"]}
        for sid in sorted(keep):
            _, lat, lon, _ = PLACES[tz_of[sid]]
            first = (start - timedelta(days=1)).date()
            last = first + timedelta(days=days + 2)
            for w in merge_overlapping_windows(
                generate_shabbos_windows(lat, lon, tz_of[sid], first, last, [sid])
                + generate_yom_tov_windows(lat, lon, tz_of[sid], first, last, [sid])
            ):
                db.add(HolidayWindow(id=uuid.uuid4(), name=w["name"],
                                     start_datetime=datetime.fromisoformat(w["start_datetime"]).astimezone(UTC),
                                     end_datetime=datetime.fromisoformat(w["end_datetime"]).astimezone(UTC),
                                     is_blackout=True, affected_stations=w["affected_stations"], reason=w["reason"]))
    else:
        for h in export["holiday_windows"]:
            affected = (h.get("affected_stations") or {}).get("station_ids") or []
            if not affected or set(affected) & keep:
                db.add(_row(HolidayWindow, h))
    await db.commit()
    return {k: NAMES[k] for k in keep}


def _add_announcements(db, start: datetime, days: int) -> None:
    """The uploaded Shprach clips: time of day per hour and half hour, weather per day."""
    for region in ("lakewood", "jerusalem"):
        for hour in range(24):
            for minute in (0, 30):
                meta = {"time_of_day": f"{hour:02d}", "region": region, "source": "upload"}
                if minute == 30:
                    meta["time_minute"] = "30"
                db.add(Asset(id=uuid.uuid4(), title=f"The time is {hour:02d}:{minute:02d} ({region})",
                             asset_type="jingle", category="time_announcement",
                             duration=TIME_CLIP_SECONDS, metadata_extra=meta,
                             file_path=f"https://cdn.example.com/time/{region}-{hour:02d}{minute:02d}.mp3"))
        first = (start - timedelta(days=1)).date()
        for n in range(days + 3):
            day = first + timedelta(days=n)
            db.add(Asset(id=uuid.uuid4(), title=f"Weather {day} ({region})", asset_type="spot",
                         category="weather_spot", duration=WEATHER_SECONDS,
                         metadata_extra={"slot_key": day.isoformat(), "region": region, "source": "upload"},
                         file_path=f"https://cdn.example.com/weather/{region}-{day}.mp3"))
