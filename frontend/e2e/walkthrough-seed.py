"""Seed for e2e/walkthrough.mjs: three stations like the real ones, each with its own
queue, rules, schedule, blackout, alert, live show, request and archive, every one named
after its station so a screenshot shows at a glance when something of another station
appears. Made-up data and a made-up admin whose password comes from the environment.

Run from backend/:
  DATABASE_URL=... E2E_ADMIN_EMAIL=... E2E_ADMIN_PASS=... AUDIO_BASE=http://localhost:4331 \
    uv run python ../frontend/e2e/walkthrough-seed.py
Prints one JSON line: {"stations": [{"id", "name", "tag"}...]}
"""
import asyncio
import json
import os
import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select

from app.core.security import hash_password
from app.db.engine import async_session_factory
from app.models.alert import Alert
from app.models.asset import Asset
from app.models.holiday_window import HolidayWindow
from app.models.live_show import LiveShow
from app.models.playlist_template import PlaylistTemplate
from app.models.program_block import ProgramBlock
from app.models.queue_entry import QueueEntry
from app.models.schedule import Schedule
from app.models.schedule_block import ScheduleBlock
from app.models.schedule_rule import ScheduleRule
from app.models.show_archive import ShowArchive
from app.models.song_request import SongRequest
from app.models.sponsor import Sponsor
from app.models.spot_schedule import SpotSchedule
from app.models.station import Station
from app.models.user import User, UserRole

AUDIO = os.environ.get("AUDIO_BASE", "http://localhost:4331")
# (name, tag on everything it owns, timezone, what its all-day rule wants)
STATIONS = [
    ("Kol B'ramah Main", "MAINX", "America/New_York", "music"),
    ("Kol B'ramah - All Day Stories", "STORYX", "America/New_York", "story"),
    ("KBR Jerusalem", "JERUX", "Asia/Jerusalem", "music"),
]


async def main() -> None:
    now = datetime.now(UTC)
    out = []
    async with async_session_factory() as db:
        email, password = os.environ["E2E_ADMIN_EMAIL"], os.environ["E2E_ADMIN_PASS"]
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(id=uuid.uuid4(), email=email, role=UserRole.ADMIN, is_active=True, display_name="Walk Admin")
            db.add(user)
        user.hashed_password = hash_password(password)
        user.role, user.is_active = UserRole.ADMIN, True
        for name, tag, tz, wants in STATIONS:
            st = Station(id=uuid.uuid4(), name=name, timezone=tz)
            db.add(st)
            await db.flush()
            kind = "story" if wants == "story" else "music"
            lib = tag.lower()   # library items are shared by all stations: lower case, so only
            # things a station OWNS carry the upper-case tag the walk-through looks for
            songs = []
            for i in range(8):
                a = Asset(id=uuid.uuid4(), title=f"{lib} {kind} {i + 1}", artist=f"{lib} artist {i % 3 + 1}",
                          duration=180.0 + i * 7, file_path=f"{AUDIO}/tone{i % 4}.mp3", asset_type=kind)
                db.add(a)
                songs.append(a)
            spot = Asset(id=uuid.uuid4(), title=f"{lib} sponsorship", duration=30.0,
                         file_path=f"{AUDIO}/tone1.mp3", asset_type="spot")
            db.add(spot)
            sponsor = Sponsor(id=uuid.uuid4(), name=f"{lib} sponsor", length_seconds=30.0,
                              audio_file_path=f"{AUDIO}/tone1.mp3")
            db.add(sponsor)
            sched = Schedule(id=uuid.uuid4(), station_id=st.id, name=f"{tag} schedule", is_active=True)
            db.add(sched)
            await db.flush()
            db.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=songs[0].id, position=1, status="playing",
                              source="auto", started_at=now - timedelta(seconds=15)))
            for i, a in enumerate(songs[1:], start=2):
                db.add(QueueEntry(id=uuid.uuid4(), station_id=st.id, asset_id=a.id, position=i, status="pending", source="auto"))
            db.add_all([
                ScheduleBlock(id=uuid.uuid4(), schedule_id=sched.id, name=f"{tag} block", start_time=time(0), end_time=time(23, 59)),
                ScheduleRule(id=uuid.uuid4(), station_id=st.id, name=f"{tag} all-day rule", rule_type="daypart",
                             asset_type=wants, is_active=True, hour_start=0, hour_end=24),
                ProgramBlock(id=uuid.uuid4(), station_id=st.id, name=f"{tag} program block", times="10:00"),
                SpotSchedule(id=uuid.uuid4(), sponsor_id=sponsor.id, asset_id=spot.id, station_id=st.id, name=f"{tag} spot schedule"),
                PlaylistTemplate(id=uuid.uuid4(), station_id=st.id, name=f"{tag} playlist"),
                HolidayWindow(id=uuid.uuid4(), name=f"{tag} blackout", start_datetime=now + timedelta(days=2),
                              end_datetime=now + timedelta(days=3), is_blackout=True,
                              affected_stations={"station_ids": [str(st.id)]}, reason="Manual"),
                Alert(id=uuid.uuid4(), station_id=st.id, alert_type="system", severity="warning",
                      title=f"{tag} alert", message=f"{tag} alert text"),
                LiveShow(id=uuid.uuid4(), station_id=st.id, title=f"{tag} live show",
                         scheduled_start=now + timedelta(hours=5), scheduled_end=now + timedelta(hours=6)),
                SongRequest(id=uuid.uuid4(), station_id=st.id, requester_name=f"{tag} listener", song_title=f"{tag} request"),
                ShowArchive(id=uuid.uuid4(), station_id=st.id, title=f"{tag} archive",
                            audio_url=f"{AUDIO}/tone2.mp3", is_published=True),
            ])
            out.append({"id": str(st.id), "name": name, "tag": tag})
        await db.commit()
    _ = date
    print(json.dumps({"stations": out}))


asyncio.run(main())
