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
    DATABASE_URL_SYNC: str = "postgresql://radio:radio@localhost:5432/radioplatform"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET_KEY: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # S3 / MinIO
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET_NAME: str = "radioplatform"
    S3_REGION: str = "us-east-1"

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:80"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # FFmpeg
    FFMPEG_PATH: str = "ffmpeg"


settings = Settings()
