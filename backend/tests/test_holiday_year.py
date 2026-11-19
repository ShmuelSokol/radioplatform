"""Holiday music over a whole year, on the three real stations' setup (Shmuel 2026-11-19:
"run it in the simulator ... see that it behaves as you'd expect").

The stations' export (tests/sim/fixtures) is loaded with a list for every holiday and the
year's real Shabbos and Yom Tov silence, and then the real fills are asked for six hours
of every day of the coming year: the playlist fill for Kol B'ramah Main and KBR Jerusalem,
the rule fill for All Day Stories. For each day the holiday items in the plan must match
the station's own calendar and the ramp: none before the start, about 1 in N after, rising
toward the holiday, the holiday's own rate on its days on the air, none after it ends, a
song only for a song and a shiur or story only for one of those, nothing in the silence.

Playing it out (time checks, sponsorships, items not overlapping) is the simulator's
part: tests/test_station_sim_scenarios.py::test_holiday_*.

    cd backend && uv run pytest tests/test_holiday_year.py -s   (about 45 s; HOLIDAY_YEAR_DAYS=30 for less)
"""
import os
import uuid
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from app.models.asset import Asset
from app.models.holiday_music import HolidayItem
from app.models.holiday_window import HolidayWindow
from app.models.queue_entry import QueueEntry
from app.models.station import Station
from app.services.holiday_music import HOLIDAYS, Setting, kind_of, seasons_between, share_on
from app.services.playlist_generator_service import generate
from app.services.queue_replenish_service import QueueReplenishService
from app.services.shabbos_service import (
    generate_shabbos_windows,
    generate_yom_tov_windows,
    merge_overlapping_windows,
)
from tests.sim.stations import JERUSALEM, MAIN, STORIES, load_export, seed

EXPORT = Path(__file__).parent / "sim" / "fixtures" / "stations-2026-11-19.json"
START = date(2026, 11, 19)
DAYS = int(os.environ.get("HOLIDAY_YEAR_DAYS", "365"))
PLACES = {"America/New_York": (40.0821, -74.2097), "Asia/Jerusalem": (31.7683, 35.2137)}
WINDOW_HOURS = 6  # 10 AM to 4 PM station time each day

pytestmark = [
    pytest.mark.asyncio,
]


def _holiday_assets():
    """For every holiday: 12 songs, 5 shiurim, 5 stories (titles say which)."""
    out = []
    for key, name, *_ in HOLIDAYS:
        for i in range(12):
            out.append((key, Asset(id=uuid.uuid4(), title=f"HOLIDAY {key} song {i}", artist=f"{name} singer {i % 4}",
                                   asset_type="music", category="lively", duration=230.0,
                                   file_path=f"https://cdn.example.com/{key}-song-{i}.mp3")))
        for i in range(5):
            out.append((key, Asset(id=uuid.uuid4(), title=f"HOLIDAY {key} shiur {i}", asset_type="shiur",
                                   duration=1500.0, file_path=f"https://cdn.example.com/{key}-shiur-{i}.mp3")))
            out.append((key, Asset(id=uuid.uuid4(), title=f"HOLIDAY {key} story {i}", asset_type="stories",
                                   duration=1200.0, file_path=f"https://cdn.example.com/{key}-story-{i}.mp3")))
    return out


async def _windows(db, station: Station):
    lat, lon = PLACES[station.timezone]
    ws = merge_overlapping_windows(
        generate_shabbos_windows(lat, lon, station.timezone, START - timedelta(days=1), START + timedelta(days=DAYS + 1),
                                 [str(station.id)])
        + generate_yom_tov_windows(lat, lon, station.timezone, START - timedelta(days=1),
                                   START + timedelta(days=DAYS + 1), [str(station.id)])
    )
    for w in ws:
        db.add(HolidayWindow(id=uuid.uuid4(), name=w["name"], is_blackout=True, reason=w["reason"],
                             start_datetime=datetime.fromisoformat(w["start_datetime"]).astimezone(UTC),
                             end_datetime=datetime.fromisoformat(w["end_datetime"]).astimezone(UTC),
                             affected_stations={"station_ids": [str(station.id)]}))
    return [(datetime.fromisoformat(w["start_datetime"]), datetime.fromisoformat(w["end_datetime"])) for w in ws]


def _expected(israel: bool, day: date):
    """(holiday key, share) the mix should use on `day` with default settings, else (None, 0)."""
    active = []
    for s in seasons_between(day - timedelta(days=30), day + timedelta(days=30), israel):
        share = share_on(Setting(), s, day)
        if share > 0:
            active.append((not (s.first <= day <= s.last), s.first, s.key, share))
    if not active:
        return None, 0.0
    active.sort()
    return active[0][2], active[0][3]


async def test_a_year_of_holiday_music_on_all_three_stations(db_session, monkeypatch):
    db = db_session
    export = load_export(EXPORT)
    await seed(db, export, datetime(2026, 11, 19, 12, tzinfo=UTC), days=2)
    # The export's own stored windows are a few days only: the year's are made here
    await db.execute(delete(HolidayWindow))
    stations = {sid: await db.get(Station, uuid.UUID(sid)) for sid in (MAIN, JERUSALEM, STORIES)}
    blackouts = {sid: await _windows(db, st) for sid, st in stations.items()}
    items = _holiday_assets()
    db.add_all([a for _, a in items])
    await db.flush()
    for key, a in items:
        db.add(HolidayItem(id=uuid.uuid4(), holiday=key, asset_id=a.id))
    await db.commit()
    holiday_of = {a.id: key for key, a in items}
    type_of = {a.id: a.asset_type for _, a in items}

    problems: list[str] = []
    summary: dict = defaultdict(Counter)  # (station, holiday) -> {"days", "items", "picks"}
    stories = QueueReplenishService

    async def stories_day(start_utc: datetime):
        await db.execute(delete(QueueEntry).where(QueueEntry.station_id == uuid.UUID(STORIES)))
        await db.commit()

        async def timeline(self, now):
            self.queue_starts, self.timed_ended = [], None
            return start_utc, []

        monkeypatch.setattr(stories, "_air_timeline", timeline)
        svc = stories(db, uuid.UUID(STORIES))
        svc.exclude_ids, svc.queued_ids, svc.max_pos = set(), set(), 0
        await svc._fill_content(WINDOW_HOURS * 3600, asset_type="stories")
        rows = (await db.execute(
            select(QueueEntry.asset_id, Asset.asset_type, Asset.duration)
            .join(Asset, Asset.id == QueueEntry.asset_id)
            .where(QueueEntry.station_id == uuid.UUID(STORIES)).order_by(QueueEntry.position)
        )).all()
        # (asset id, type, expected start) along the queue
        out, t = [], start_utc
        for asset_id, asset_type, duration in rows:
            out.append((asset_id, asset_type, t))
            t += timedelta(seconds=float(duration or 180))
        return out

    for sid, station in stations.items():
        zone = ZoneInfo(station.timezone)
        israel = station.timezone == "Asia/Jerusalem"
        for n in range(DAYS):
            day = START + timedelta(days=n)
            start = datetime(day.year, day.month, day.day, 10, tzinfo=zone).astimezone(UTC)
            if sid == STORIES:
                picks = await stories_day(start)
            else:
                plan = await generate(db, station, start, WINDOW_HOURS, artist_distance=0)
                picks = [(p.asset_id, p.asset_type, p.at) for p in plan.picks]
            # each day starts fresh: a holiday item from yesterday's run does not count as rested
            key, share = _expected(israel, day)
            ordinary = [p for p in picks if kind_of(p[1])]
            got = [p for p in picks if p[0] in holiday_of]
            where = f"{station.name} {day}"
            for asset_id, asset_type, at in got:
                # (the rule fill queues through the silence; the player keeps it quiet)
                if sid != STORIES and any(s <= at < e for s, e in blackouts[sid]):
                    problems.append(f"{where}: holiday item at {at} inside Shabbos/Yom Tov silence")
                if holiday_of[asset_id] != key:
                    problems.append(f"{where}: {holiday_of[asset_id]} item, expected {key}")
                if sid == STORIES and kind_of(type_of[asset_id]) != "long":
                    problems.append(f"{where}: a song on the stories station")
            if key is None and got:
                problems.append(f"{where}: {len(got)} holiday items out of season")
            if key is not None and ordinary:
                want = share * len(ordinary)
                if abs(len(got) - want) > 2:
                    problems.append(f"{where}: {len(got)} holiday items of {len(ordinary)}, expected about {want:.1f}")
            if key is not None:
                c = summary[(station.name, key)]
                c["days"] += 1
                c["items"] += len(got)
                c["picks"] += len(ordinary)
                c["silent days"] += int(not ordinary)

    print("\nstation | holiday | days in season | holiday items / ordinary picks (6 h a day)")
    for (name, key), c in sorted(summary.items()):
        print(f"{name} | {key} | {c['days']} ({c['silent days']} silent) | {c['items']} / {c['picks']}")
    assert not problems, "\n".join(problems[:40]) + f"\n... {len(problems)} problems"
    # every holiday was in season on every station within the year
    for station in stations.values():
        for key, *_ in HOLIDAYS:
            assert summary[(station.name, key)]["items"] > 0, (station.name, key)
