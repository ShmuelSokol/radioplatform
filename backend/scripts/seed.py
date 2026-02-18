"""Seed script to populate database with initial data."""
import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import async_session_factory
from app.core.security import hash_password
from app.models.user import User, UserRole
from app.models.station import Station, StationType
from app.models.channel_stream import ChannelStream
from app.services.storage_service import ensure_bucket_exists


async def seed():
    print("Seeding database...")

    # Ensure S3 bucket exists
    try:
        await ensure_bucket_exists()
        print("S3 bucket ready")
    except Exception as e:
        print(f"Warning: Could not create S3 bucket: {e}")

    async with async_session_factory() as db:
        # Check if admin already exists
        result = await db.execute(select(User).where(User.email == "admin@radioplatform.com"))
        if result.scalar_one_or_none():
            print("Seed data already exists, skipping.")
            return

        # Create admin user
        admin = User(
            id=uuid.uuid4(),
            email="admin@radioplatform.com",
            hashed_password=hash_password("admin123"),
            role=UserRole.ADMIN,
            is_active=True,
            display_name="Admin",
        )
        db.add(admin)

        # Create a sample station
        station_id = uuid.uuid4()
        station = Station(
            id=station_id,
            name="Radio One",
            type=StationType.INTERNET,
            timezone="America/New_York",
            is_active=True,
            description="The flagship internet radio station",
        )
        db.add(station)

        # Create a channel for the station
        channel = ChannelStream(
            id=uuid.uuid4(),
            station_id=station_id,
            channel_name="main",
            bitrate=128,
            codec="aac",
        )
        db.add(channel)

        await db.commit()
        print("Seed data created:")
        print(f"  Admin: admin@radioplatform.com / admin123")
        print(f"  Station: Radio One (id={station_id})")
        print(f"  Channel: main (128kbps AAC)")


if __name__ == "__main__":
    asyncio.run(seed())
