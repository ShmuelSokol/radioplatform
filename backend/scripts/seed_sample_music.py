"""Generate and upload sample music files for queue testing."""
import asyncio
import io
import math
import struct
import uuid
import wave

from sqlalchemy import select

# Sample track definitions: (title, artist, duration_sec, frequency_hz)
TRACKS = [
    ("Morning Light", "DJ Test", 30, 440),
    ("Afternoon Breeze", "Sample Artist", 45, 523),
    ("Evening Calm", "Test Band", 35, 392),
    ("Night Drive", "Demo Singer", 40, 349),
    ("Sunrise Melody", "Audio Test", 25, 587),
]


def generate_wav(duration_sec: float, freq: float, sample_rate: int = 44100) -> bytes:
    """Generate a simple sine wave WAV file in memory."""
    num_samples = int(sample_rate * duration_sec)
    buf = io.BytesIO()

    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(sample_rate)

        # Generate sine wave with fade in/out
        fade_samples = int(sample_rate * 0.5)  # 0.5s fade
        frames = bytearray()
        for i in range(num_samples):
            t = i / sample_rate
            sample = math.sin(2 * math.pi * freq * t)

            # Add a second harmonic for richer sound
            sample += 0.3 * math.sin(2 * math.pi * freq * 2 * t)
            # Add a third for even more color
            sample += 0.1 * math.sin(2 * math.pi * freq * 3 * t)

            # Fade in
            if i < fade_samples:
                sample *= i / fade_samples
            # Fade out
            elif i > num_samples - fade_samples:
                sample *= (num_samples - i) / fade_samples

            # Normalize and convert to 16-bit
            sample = max(-1.0, min(1.0, sample * 0.5))
            frames += struct.pack("<h", int(sample * 32767))

        w.writeframes(bytes(frames))

    return buf.getvalue()


async def main():
    # Bootstrap the app
    from app.db.engine import engine
    from app.db.session import async_session_factory
    from app.models.asset import Asset
    from app.services.storage_service import upload_file, generate_asset_key

    async with async_session_factory() as db:
        # Check if we already have music assets
        result = await db.execute(
            select(Asset).where(Asset.asset_type == "music").limit(1)
        )
        if result.scalar_one_or_none():
            print("Music assets already exist, skipping seed.")
            return

        for title, artist, duration, freq in TRACKS:
            print(f"Generating: {title} ({duration}s, {freq}Hz)...")
            wav_data = generate_wav(duration, freq)

            # Upload to storage
            filename = f"{title.lower().replace(' ', '_')}.wav"
            s3_key = generate_asset_key(filename)
            await upload_file(wav_data, s3_key, "audio/wav")

            # Create asset record
            asset = Asset(
                id=uuid.uuid4(),
                title=title,
                artist=artist,
                album="Sample Music",
                duration=float(duration),
                file_path=s3_key,
                asset_type="music",
                category="music",
            )
            db.add(asset)
            print(f"  Uploaded: {s3_key}")

        await db.commit()
        print(f"\nDone! Uploaded {len(TRACKS)} sample music tracks.")


if __name__ == "__main__":
    asyncio.run(main())
