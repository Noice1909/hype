"""Feedback schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FeedbackPayload(BaseModel):
    session_id: str
    message_id: str
    rating: int = Field(ge=1, le=5)
    comment: str | None = None
