import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AlertCreate(BaseModel):
    alert_type: str
    severity: str
    title: str
    message: str
    station_id: uuid.UUID | str | None = None
    context: dict | None = None


class AlertUpdate(BaseModel):
    is_resolved: bool | None = None


class AlertInDB(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    station_id: uuid.UUID | str | None = None
    alert_type: str
    severity: str
    title: str
    message: str
    context: dict | None = None
    is_resolved: bool
    resolved_at: datetime | None = None
    resolved_by: uuid.UUID | str | None = None
    created_at: datetime
    updated_at: datetime


class AlertListResponse(BaseModel):
    alerts: list[AlertInDB]
    total: int
    unresolved_count: int
