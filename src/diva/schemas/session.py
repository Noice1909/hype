"""Session schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SessionInfo(BaseModel):
    session_id: str
    created_at: datetime
    updated_at: datetime
    turn_count: int = 0


class SessionList(BaseModel):
    sessions: list[SessionInfo] = Field(default_factory=list)
