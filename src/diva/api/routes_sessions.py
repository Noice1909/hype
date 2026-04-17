"""Conversation history endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from diva.storage.mongo import get_db

router = APIRouter(tags=["conversations"])


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Get conversation history."""
    db = get_db()
    messages = await db.messages.find(
        {"session_id": conversation_id},
        {"_id": 0},
    ).sort("turn_number", 1).to_list(length=1000)

    session = await db.sessions.find_one({"_id": conversation_id})
    return {
        "conversation_id": conversation_id,
        "turn_count": session.get("turn_count", 0) if session else 0,
        "messages": messages,
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Delete a conversation and its messages."""
    db = get_db()
    await db.sessions.delete_one({"_id": conversation_id})
    result = await db.messages.delete_many({"session_id": conversation_id})
    return {"deleted_messages": result.deleted_count}
