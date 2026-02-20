import uuid

from pydantic import BaseModel, ConfigDict


class UserCreate(BaseModel):
    email: str
    password: str
    role: str = "viewer"
    display_name: str | None = None
    phone_number: str | None = None
    title: str | None = None
    alert_preferences: dict | None = None


class UserUpdate(BaseModel):
    email: str | None = None
    password: str | None = None
    role: str | None = None
    display_name: str | None = None
    is_active: bool | None = None
    phone_number: str | None = None
    title: str | None = None
    alert_preferences: dict | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | str
    email: str
    role: str
    is_active: bool
    display_name: str | None = None
    phone_number: str | None = None
    title: str | None = None
    alert_preferences: dict | None = None


class UserListResponse(BaseModel):
    users: list[UserOut]
    total: int
