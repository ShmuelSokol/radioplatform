from pydantic_settings import BaseSettings
from pydantic import field_validator, model_validator
from typing import Any


_PUBLIC_JWT_SECRETS = {"", "change-me-in-production"}


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

    @model_validator(mode="after")
    def refuse_public_jwt_secret(self) -> "Settings":
        # The default is in this public repository: with it anyone can sign their own
        # admin token (and web call-in tokens). Production refuses to start; Railway
        # keeps the running instance until a new one passes its health check.
        if self.APP_ENV == "production" and self.JWT_SECRET_KEY in _PUBLIC_JWT_SECRETS:
            raise ValueError("JWT_SECRET_KEY is the public default; set a long random secret in production")
        return self

    # Password given to accounts created by the seed endpoints. No default on
    # purpose: the repository is public, so a password written here is public.
    ADMIN_SEED_PASSWORD: str = ""
    # /auth/seed-all wipes the library, stations and rules. Off in production
    # unless this is explicitly set.
    ALLOW_DESTRUCTIVE_SEED: bool = False
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
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:80", "https://kbrlive.com", "https://www.kbrlive.com"]

    # Frontend URL (used in emails)
    FRONTEND_URL: str = "https://kbrlive.com"

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

    # Emergency fallback / silence detection
    EMERGENCY_FALLBACK_CATEGORY: str = "emergency"
    SILENCE_DETECTION_SECONDS: int = 30

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
    ICECAST_BITRATE: int = 192
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

    # Bug reports (optional). Reports are always saved in the database; with a
    # token they are also copied to a PRIVATE GitHub repo, where a scheduled
    # Claude routine triages them. ntfy pushes "fix ready" to the owner's phone.
    # See wiki/bug-reports.md.
    BUG_REPORTS_GITHUB_TOKEN: str = ""
    BUG_REPORTS_REPO: str = "ShmuelSokol/kbr-bug-reports"
    NTFY_TOPIC: str = ""
    NTFY_SERVER: str = "https://ntfy.sh"

    # Sentry (optional — set DSN to enable error tracking)
    SENTRY_DSN: str = ""

    @property
    def sentry_enabled(self) -> bool:
        return bool(self.SENTRY_DSN)

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

    # Liquidsoap (remote server — dedicated VPS)
    LIQUIDSOAP_HOST: str = ""  # e.g., "stream.kbrlive.com" — empty to disable
    LIQUIDSOAP_TELNET_PORT: int = 1234
    LIQUIDSOAP_ENABLED: bool = False

    # Icecast stream URL (public, served by the VPS)
    ICECAST_STREAM_URL: str = ""  # e.g., "https://stream.kbrlive.com/live"

    @property
    def liquidsoap_enabled(self) -> bool:
        return self.LIQUIDSOAP_ENABLED and bool(self.LIQUIDSOAP_HOST)

    # Playout engine (Liquidsoap on the stream server). The engine asks the backend
    # what plays next and reports what really went on air. See app/services/playout_engine.py.
    PLAYOUT_ENGINE_ENABLED: bool = False
    # Stations the engine plays, comma-separated (one engine per station on the stream
    # server; setup.sh prints the list). A station not listed keeps the in-browser player.
    PLAYOUT_ENGINE_STATION_ID: str = ""
    PLAYOUT_ENGINE_URL: str = ""  # control base, e.g. "https://stream.kbrlive.com/control" (+ /<station id>)
    PLAYOUT_ENGINE_TOKEN: str = ""  # shared secret, sent both ways in X-Playout-Token
    # When true, listeners and the dashboard play the station's stream instead of the in-browser player
    PLAYOUT_STREAM_LIVE: bool = False

    @property
    def engine_station_ids(self) -> set[str]:
        return {s.strip() for s in self.PLAYOUT_ENGINE_STATION_ID.split(",") if s.strip()}

    @property
    def playout_engine_enabled(self) -> bool:
        return bool(
            self.PLAYOUT_ENGINE_ENABLED
            and self.engine_station_ids
            and self.PLAYOUT_ENGINE_URL
            and self.PLAYOUT_ENGINE_TOKEN
        )

    def engine_drives(self, station_id) -> bool:
        """True when this station's playback is driven by the playout engine."""
        return self.playout_engine_enabled and str(station_id) in self.engine_station_ids

    def engine_control_url(self, station_id) -> str:
        """Where this station's engine takes commands (nginx routes it to that engine)."""
        return f"{self.PLAYOUT_ENGINE_URL.rstrip('/')}/{station_id}"

    def engine_stream_url(self, station_id) -> str:
        """This station's stream: Icecast mount /live-<station id>."""
        return f"{self.ICECAST_STREAM_URL.rstrip('/')}-{station_id}"

    def public_stream_url(self, station_id) -> str | None:
        """Stream URL clients should play for this station, or None for the in-browser player."""
        if not self.ICECAST_STREAM_URL:
            return None
        if self.liquidsoap_enabled:
            return self.ICECAST_STREAM_URL
        if self.engine_drives(station_id) and self.PLAYOUT_STREAM_LIVE:
            return self.engine_stream_url(station_id)
        return None


settings = Settings()
