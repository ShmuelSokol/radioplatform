from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Any


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # App
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://radio:radio@localhost:5432/radioplatform"
    DATABASE_URL_SYNC: str = ""

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def fix_database_url(cls, v: str) -> str:
        # Render/Neon give postgres:// but asyncpg needs postgresql+asyncpg://
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+asyncpg://", 1)
        elif v.startswith("postgresql://") and "+asyncpg" not in v:
            v = v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    # Redis (optional — set to empty string to disable)
    REDIS_URL: str = ""

    @property
    def redis_enabled(self) -> bool:
        return bool(self.REDIS_URL)

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # S3 / MinIO (optional — set S3_ENDPOINT_URL to empty to disable)
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_BUCKET_NAME: str = "radioplatform"
    S3_REGION: str = "us-east-1"

    @property
    def s3_enabled(self) -> bool:
        return bool(self.S3_ENDPOINT_URL)

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:80"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [origin.strip() for origin in v.split(",")]
        return v

    # Celery (derived from REDIS_URL if set)
    CELERY_BROKER_URL: str = ""
    CELERY_RESULT_BACKEND: str = ""

    @field_validator("CELERY_BROKER_URL", mode="before")
    @classmethod
    def derive_celery_broker(cls, v: str, info: Any) -> str:
        if v:
            return v
        redis = info.data.get("REDIS_URL", "")
        return f"{redis}/1" if redis else ""

    @field_validator("CELERY_RESULT_BACKEND", mode="before")
    @classmethod
    def derive_celery_backend(cls, v: str, info: Any) -> str:
        if v:
            return v
        redis = info.data.get("REDIS_URL", "")
        return f"{redis}/2" if redis else ""

    # FFmpeg
    FFMPEG_PATH: str = "ffmpeg"

    # ElevenLabs TTS (optional — set API key to empty to disable)
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_VOICE_ID: str = ""

    @property
    def elevenlabs_enabled(self) -> bool:
        return bool(self.ELEVENLABS_API_KEY and self.ELEVENLABS_VOICE_ID)

    # OpenWeatherMap (optional — set API key to empty to disable)
    OPENWEATHERMAP_API_KEY: str = ""

    @property
    def weather_enabled(self) -> bool:
        return bool(self.OPENWEATHERMAP_API_KEY)

    # Icecast (optional — set host to empty to disable)
    ICECAST_HOST: str = ""
    ICECAST_PORT: int = 8000
    ICECAST_SOURCE_PASSWORD: str = "hackme"
    ICECAST_MOUNT: str = "/live"
    ICECAST_BITRATE: int = 128
    ICECAST_FORMAT: str = "mp3"  # "mp3" or "ogg"

    @property
    def icecast_enabled(self) -> bool:
        return bool(self.ICECAST_HOST)

    # Supabase Storage (optional — set URL to empty to disable)
    SUPABASE_URL: str = ""
    SUPABASE_SERVICE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "audio"

    @property
    def supabase_storage_enabled(self) -> bool:
        return bool(self.SUPABASE_URL and self.SUPABASE_SERVICE_KEY)

    # Stripe (optional — set secret key to empty to disable)
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_ID: str = ""

    @property
    def stripe_enabled(self) -> bool:
        return bool(self.STRIPE_SECRET_KEY)

    # Resend (optional — set API key to empty to disable)
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "noreply@kolbramah.com"

    @property
    def resend_enabled(self) -> bool:
        return bool(self.RESEND_API_KEY)

    # Anthropic / Claude API (optional — for AI email drafting)
    ANTHROPIC_API_KEY: str = ""

    @property
    def anthropic_enabled(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY)

    # Twilio (optional — for SMS/WhatsApp alert notifications)
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""
    TWILIO_VOICE_NUMBER: str = ""  # dedicated voice number for live call-ins
    LIVE_SHOW_HOLD_MUSIC_URL: str = ""  # public URL to hold music MP3
    BACKEND_PUBLIC_URL: str = ""  # for Twilio callbacks

    @property
    def twilio_enabled(self) -> bool:
        return bool(self.TWILIO_ACCOUNT_SID)

    @property
    def twilio_voice_enabled(self) -> bool:
        return bool(self.TWILIO_ACCOUNT_SID and self.TWILIO_AUTH_TOKEN)


settings = Settings()
