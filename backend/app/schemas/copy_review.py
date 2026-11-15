"""Schemas for sponsorship wording rules and the AI copy review."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RulesOut(BaseModel):
    version: int
    rules_text: str
    updated_at: datetime | None = None
    updated_by: UUID | str | None = None
    is_default: bool = False


class RulesUpdate(BaseModel):
    rules_text: str = Field(..., min_length=20, max_length=20000)


class CopyReviewRequest(BaseModel):
    script_text: str = Field(..., min_length=3, max_length=5000)
    sponsor_name: str | None = Field(None, max_length=255)


class CopyIssue(BaseModel):
    quote: str
    problem: str
    suggestion: str


class CopyReviewResponse(BaseModel):
    verdict: Literal["ok", "needs_changes"]
    summary: str
    issues: list[CopyIssue]
    suggested_script: str | None = None
    estimated_seconds: float
    length_ok: bool
    rules_version: int
    reviewed_by: Literal["ai", "basic_check"]
