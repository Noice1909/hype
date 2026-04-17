"""Feedback endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from diva.schemas.feedback import FeedbackPayload
from diva.storage.mongo import save_feedback

router = APIRouter(tags=["feedback"])


@router.post("/feedback")
async def submit_feedback(payload: FeedbackPayload):
    """Submit user feedback (thumbs up/down) for a response."""
    await save_feedback(
        session_id=payload.session_id,
        message_id=payload.message_id,
        rating=payload.rating,
        comment=payload.comment,
    )
    return {"status": "ok"}
