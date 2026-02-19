from pydantic import BaseModel, ConfigDict


class UserPreferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    preview_start_seconds: float = 5.0
    preview_end_seconds: float = 5.0
    default_silence_threshold_db: float = -30.0
    default_silence_min_duration: float = 0.5
    extra_preferences: dict | None = None


class UserPreferenceUpdate(BaseModel):
    preview_start_seconds: float | None = None
    preview_end_seconds: float | None = None
    default_silence_threshold_db: float | None = None
    default_silence_min_duration: float | None = None
    extra_preferences: dict | None = None
