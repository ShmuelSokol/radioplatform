import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict


class WeatherReadoutResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    station_id: uuid.UUID | str
    readout_date: date
    script_text: str
    weather_data: dict | None = None
    status: str
    asset_id: uuid.UUID | str | None = None
    queue_time: time | None = None
    generated_by: str
    created_at: datetime
    updated_at: datetime


class WeatherReadoutListResponse(BaseModel):
    readouts: list[WeatherReadoutResponse]
    total: int


class WeatherReadoutCreate(BaseModel):
    station_id: uuid.UUID | str
    readout_date: date | None = None
    template_override: str | None = None


class WeatherReadoutUpdate(BaseModel):
    script_text: str | None = None
    status: str | None = None
    asset_id: uuid.UUID | str | None = None
    queue_time: time | None = None
