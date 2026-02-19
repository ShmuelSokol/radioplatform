import uuid
from datetime import datetime, timezone as tz

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import func

from app.core.dependencies import get_current_user
from app.core.security import hash_password
from app.db.session import get_db
from app.models.asset import Asset
from app.models.schedule_rule import ScheduleRule
from app.models.station import Station
from app.models.queue_entry import QueueEntry
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
    """Create or update default admin user."""
    result = await db.execute(select(User).where(User.role == UserRole.ADMIN))
    existing = result.scalar_one_or_none()
    if existing:
        existing.email = "admin"
        existing.hashed_password = hash_password("613Radio")
        existing.is_active = True
        await db.commit()
        return {"message": "Admin user updated", "email": existing.email}

    admin = User(
        id=uuid.uuid4(),
        email="admin",
        hashed_password=hash_password("613Radio"),
        role=UserRole.ADMIN,
        display_name="Admin",
        is_active=True,
    )
    db.add(admin)
    await db.commit()
    return {"message": "Admin user created", "email": admin.email}


@router.post("/migrate", status_code=200)
async def run_migrations(db: AsyncSession = Depends(get_db)):
    """Add new columns to existing tables (safe to run multiple times)."""
    migrations = [
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS asset_type VARCHAR(50) DEFAULT 'music'",
        "ALTER TABLE assets ADD COLUMN IF NOT EXISTS category VARCHAR(100)",
        "ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ",
        "ALTER TABLE queue_entries ADD COLUMN IF NOT EXISTS channel_id UUID REFERENCES channel_streams(id) ON DELETE SET NULL",
    ]
    results = []
    for sql in migrations:
        try:
            await db.execute(text(sql))
            results.append(f"OK: {sql[:60]}")
        except Exception as e:
            results.append(f"SKIP: {sql[:60]} ({e})")
    await db.commit()
    return {"message": "Migrations complete", "results": results}


# ── Test data ──────────────────────────────────────────────────

from app.data.jewish_songs import generate_songs

SAMPLE_SPOTS = [
    {"title": "Weather Sponsors", "artist": None, "album": None, "duration": 30.0, "category": "weather"},
    {"title": "CALL IN 718 298 2011", "artist": "Mordechai Weinberger", "album": None, "duration": 45.0, "category": "call_in"},
    {"title": "Judaica Plaza Super Store", "artist": None, "album": None, "duration": 30.0, "category": "retail"},
    {"title": "Hatzolah Membership Drive", "artist": None, "album": None, "duration": 60.0, "category": "community"},
    {"title": "Lakewood Heating & Cooling", "artist": None, "album": None, "duration": 30.0, "category": "service"},
    {"title": "Pomegranate Supermarket", "artist": None, "album": None, "duration": 30.0, "category": "retail"},
    {"title": "Misaskim Emergency Response", "artist": None, "album": None, "duration": 45.0, "category": "community"},
    {"title": "Emes W Heating", "artist": None, "album": None, "duration": 36.0, "category": "service"},
]

SAMPLE_SHIURIM = [
    {"title": "Hishtadlus", "artist": "Rabbi Leff", "album": "Ask Rabbi Leff", "duration": 74.0, "category": "leff_ask"},
    {"title": "Menachos 33", "artist": "Sruly Bornstein", "album": "DAF YOMI", "duration": 312.0, "category": "daf_yomi"},
    {"title": "Menachos 34", "artist": "Sruly Bornstein", "album": "DAF YOMI", "duration": 298.0, "category": "daf_yomi"},
    {"title": "Menachos 35", "artist": "Sruly Bornstein", "album": "DAF YOMI", "duration": 320.0, "category": "daf_yomi"},
    {"title": "Menachos 37", "artist": "Sruly Bornstein", "album": "DAF YOMI", "duration": 305.0, "category": "daf_yomi"},
    {"title": "Menachos 38", "artist": "Sruly Bornstein", "album": "DAF YOMI", "duration": 290.0, "category": "daf_yomi"},
    {"title": "Parshas Teruma", "artist": "Rabbi Frand", "album": "Weekly Parsha", "duration": 2700.0, "category": "parsha"},
    {"title": "Hilchos Shabbos Shiur 12", "artist": "Rabbi Ribiat", "album": "Hilchos Shabbos", "duration": 1800.0, "category": "halacha"},
    {"title": "Emunah Daily #145", "artist": "Rabbi Wallerstein", "album": "Emunah Daily", "duration": 600.0, "category": "emunah"},
    {"title": "I Don't Need Your Bakisha", "artist": "Rabbi Yoel Roth", "album": None, "duration": 210.0, "category": "mussar"},
]

SAMPLE_JINGLES = [
    {"title": "Kol Bramah Intro", "artist": None, "album": "Intros", "duration": 4.0, "category": "intro"},
    {"title": "Kol Bramah Outro", "artist": None, "album": "Outros", "duration": 3.0, "category": "outro"},
    {"title": "News Intro Sting", "artist": None, "album": "Stings", "duration": 5.0, "category": "news"},
    {"title": "Weather Jingle", "artist": None, "album": "Stings", "duration": 4.0, "category": "weather"},
    {"title": "Traffic Update Sting", "artist": None, "album": "Stings", "duration": 3.0, "category": "traffic"},
    {"title": "Kol Bramah Station ID", "artist": None, "album": "IDs", "duration": 8.0, "category": "station_id"},
    {"title": "Morning Show Opener", "artist": None, "album": "Shows", "duration": 12.0, "category": "show_open"},
    {"title": "Late Night Bumper", "artist": None, "album": "Bumpers", "duration": 5.0, "category": "bumper"},
]

# Hourly jingles — station ID + time announcement for each hour
HOURLY_JINGLES = [
    {"title": f"Kol Bramah — {'12' if h % 12 == 0 else h % 12}:00 {'AM' if h < 12 else 'PM'} Station ID",
     "artist": None, "album": "Hourly IDs", "duration": 10.0, "category": "hourly_id", "hour": h}
    for h in range(24)
]

SAMPLE_ZMANIM = [
    {"title": "Netz HaChama Announcement", "artist": None, "album": "Zmanim", "duration": 15.0, "category": "netz"},
    {"title": "Zman Krias Shema", "artist": None, "album": "Zmanim", "duration": 15.0, "category": "shema"},
    {"title": "Chatzos Announcement", "artist": None, "album": "Zmanim", "duration": 12.0, "category": "chatzos"},
    {"title": "Mincha Gedola Reminder", "artist": None, "album": "Zmanim", "duration": 15.0, "category": "mincha"},
    {"title": "Shkia Warning — 18 Minutes", "artist": None, "album": "Zmanim", "duration": 20.0, "category": "shkia"},
    {"title": "Shkia Warning — Candle Lighting", "artist": None, "album": "Zmanim", "duration": 25.0, "category": "candle_lighting"},
    {"title": "Tzeis HaKochavim", "artist": None, "album": "Zmanim", "duration": 12.0, "category": "tzeis"},
    {"title": "Shabbos Ends Announcement", "artist": None, "album": "Zmanim", "duration": 15.0, "category": "motzei"},
]

SAMPLE_RULES = [
    {"name": "Music Rotation", "description": "Default music rotation throughout the day", "rule_type": "daypart",
     "asset_type": "music", "category": None, "hour_start": 0, "hour_end": 24, "priority": 1},
    {"name": "Morning Lively Mix", "description": "Play lively music 6am-9am", "rule_type": "daypart",
     "asset_type": "music", "category": "lively", "hour_start": 6, "hour_end": 9, "priority": 20},
    {"name": "Spots Every 3 Songs", "description": "Insert a spot after every 3 songs", "rule_type": "rotation",
     "asset_type": "spot", "songs_between": 3, "hour_start": 6, "hour_end": 22, "priority": 50},
    {"name": "Jingle Every 30 Min", "description": "Station ID every 30 minutes", "rule_type": "interval",
     "asset_type": "jingle", "interval_minutes": 30, "hour_start": 0, "hour_end": 24, "priority": 40},
    {"name": "Hourly ID & Time", "description": "Station ID and time announcement on the hour, every hour", "rule_type": "interval",
     "asset_type": "jingle", "category": "hourly_id", "interval_minutes": 60, "hour_start": 0, "hour_end": 24, "priority": 95},
    {"name": "Zmanim Announcements", "description": "Zmanim at fixed times", "rule_type": "fixed_time",
     "asset_type": "zmanim", "hour_start": 5, "hour_end": 22, "priority": 90},
    {"name": "Daf Yomi Slot", "description": "Daf Yomi shiur 11am-12pm daily", "rule_type": "daypart",
     "asset_type": "shiur", "category": "daf_yomi", "hour_start": 11, "hour_end": 12, "priority": 80},
    {"name": "Friday Shabbos Music", "description": "Switch to Shabbos music Friday 2pm+", "rule_type": "daypart",
     "asset_type": "music", "category": "shabbos", "hour_start": 14, "hour_end": 24,
     "days_of_week": "4", "priority": 70},
    {"name": "Evening Relax", "description": "Relaxing music 8pm-midnight", "rule_type": "daypart",
     "asset_type": "music", "category": "relax", "hour_start": 20, "hour_end": 24, "priority": 15},
    {"name": "Parsha Shiur", "description": "Weekly parsha shiur Thursday evening", "rule_type": "daypart",
     "asset_type": "shiur", "category": "parsha", "hour_start": 20, "hour_end": 21,
     "days_of_week": "3", "priority": 75},
    {"name": "News Break", "description": "News jingle on the hour during daytime", "rule_type": "interval",
     "asset_type": "jingle", "category": "news", "interval_minutes": 60, "hour_start": 7, "hour_end": 20, "priority": 60},
]


@router.post("/seed-all", status_code=201)
async def seed_all(db: AsyncSession = Depends(get_db)):
    """Drop all data and re-seed everything: admin, station, assets, rules."""
    # Clear existing data
    await db.execute(text("DELETE FROM queue_entries"))
    await db.execute(text("DELETE FROM schedule_rules"))
    await db.execute(text("DELETE FROM assets"))
    await db.execute(text("DELETE FROM stations"))

    # Keep admin user, just ensure it exists
    result = await db.execute(select(User).where(User.role == UserRole.ADMIN))
    admin = result.scalar_one_or_none()
    if not admin:
        admin = User(
            id=uuid.uuid4(), email="admin", hashed_password=hash_password("613Radio"),
            role=UserRole.ADMIN, display_name="Admin", is_active=True,
        )
        db.add(admin)

    # Create sample users
    sample_users = [
        {"email": "manager1", "password": "manager123", "role": UserRole.MANAGER, "display_name": "Sarah Cohen"},
        {"email": "dj_moshe", "password": "moshe123", "role": UserRole.MANAGER, "display_name": "Moshe Levy"},
        {"email": "viewer1", "password": "view123", "role": UserRole.VIEWER, "display_name": "David Klein"},
    ]
    for u in sample_users:
        exists = await db.execute(select(User).where(User.email == u["email"]))
        if not exists.scalar_one_or_none():
            db.add(User(
                id=uuid.uuid4(), email=u["email"], hashed_password=hash_password(u["password"]),
                role=u["role"], display_name=u["display_name"], is_active=True,
            ))

    # Create default station
    station = Station(
        id=uuid.uuid4(), name="Kol Bramah Main", timezone="America/New_York", is_active=True,
        description="Main broadcast channel — 24/7 Jewish music, Torah, and community",
    )
    db.add(station)

    # Seed assets by type
    all_assets = []
    music_songs = generate_songs()
    for item in music_songs:
        a = Asset(id=uuid.uuid4(), title=item["title"], artist=item["artist"], album=item["album"],
                  duration=item["duration"], file_path=f"music/{item['title'].lower().replace(' ', '_')}.mp3",
                  asset_type="music", category=item.get("category"))
        db.add(a)
        all_assets.append(a)
    for item in SAMPLE_SPOTS:
        a = Asset(id=uuid.uuid4(), title=item["title"], artist=item["artist"], album=item["album"],
                  duration=item["duration"], file_path=f"spots/{item['title'].lower().replace(' ', '_')}.mp3",
                  asset_type="spot", category=item.get("category"))
        db.add(a)
        all_assets.append(a)
    for item in SAMPLE_SHIURIM:
        a = Asset(id=uuid.uuid4(), title=item["title"], artist=item["artist"], album=item["album"],
                  duration=item["duration"], file_path=f"shiurim/{item['title'].lower().replace(' ', '_')}.mp3",
                  asset_type="shiur", category=item.get("category"))
        db.add(a)
        all_assets.append(a)
    for item in SAMPLE_JINGLES:
        a = Asset(id=uuid.uuid4(), title=item["title"], artist=item["artist"], album=item["album"],
                  duration=item["duration"], file_path=f"jingles/{item['title'].lower().replace(' ', '_')}.mp3",
                  asset_type="jingle", category=item.get("category"))
        db.add(a)
        all_assets.append(a)
    for item in HOURLY_JINGLES:
        a = Asset(id=uuid.uuid4(), title=item["title"], artist=item["artist"], album=item["album"],
                  duration=item["duration"], file_path=f"jingles/hourly/{item['hour']:02d}00.mp3",
                  asset_type="jingle", category=item.get("category"))
        db.add(a)
        all_assets.append(a)
    for item in SAMPLE_ZMANIM:
        a = Asset(id=uuid.uuid4(), title=item["title"], artist=item["artist"], album=item["album"],
                  duration=item["duration"], file_path=f"zmanim/{item['title'].lower().replace(' ', '_')}.mp3",
                  asset_type="zmanim", category=item.get("category"))
        db.add(a)
        all_assets.append(a)

    # Seed schedule rules
    for r in SAMPLE_RULES:
        rule = ScheduleRule(
            id=uuid.uuid4(), name=r["name"], description=r.get("description"),
            rule_type=r["rule_type"], asset_type=r["asset_type"], category=r.get("category"),
            hour_start=r.get("hour_start", 0), hour_end=r.get("hour_end", 24),
            days_of_week=r.get("days_of_week", "0,1,2,3,4,5,6"),
            interval_minutes=r.get("interval_minutes"), songs_between=r.get("songs_between"),
            priority=r.get("priority", 10), is_active=True,
        )
        db.add(rule)

    # Pre-populate queue with first 15 music assets for the station
    music_assets = [a for a in all_assets if a.asset_type == "music"]
    for i, a in enumerate(music_assets[:15]):
        entry = QueueEntry(
            id=uuid.uuid4(), station_id=station.id, asset_id=a.id,
            position=i + 1, status="playing" if i == 0 else "pending",
            started_at=datetime.now(tz.utc) if i == 0 else None,
        )
        db.add(entry)

    await db.commit()
    return {
        "message": "Full seed complete",
        "music": len(music_songs),
        "spots": len(SAMPLE_SPOTS),
        "shiurim": len(SAMPLE_SHIURIM),
        "jingles": len(SAMPLE_JINGLES) + len(HOURLY_JINGLES),
        "zmanim": len(SAMPLE_ZMANIM),
        "rules": len(SAMPLE_RULES),
        "queue": min(15, len(music_assets)),
    }


# Keep old endpoint for backwards compat
@router.post("/seed-assets", status_code=201)
async def seed_assets(db: AsyncSession = Depends(get_db)):
    """Redirect to seed-all."""
    return await seed_all(db)
