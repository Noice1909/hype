"""Motor async MongoDB client and collection access."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from diva.core.config import get_settings

logger = logging.getLogger(__name__)

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def init_mongo() -> AsyncIOMotorDatabase | None:
    """Initialize the MongoDB connection and return the database handle.

    Non-fatal: if MongoDB is unreachable, logs a warning and returns None.
    Session persistence will be unavailable but the core pipeline still works.
    """
    global _client, _db
    settings = get_settings()
    uri = settings.mongodb_uri
    db_name = settings.diva_db_name

    try:
        _client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        # Test connectivity
        await _client.admin.command("ping")
        _db = _client[db_name]

        # Ensure indexes
        await _db.sessions.create_index("ttl_expires_at", expireAfterSeconds=0)
        await _db.messages.create_index([("session_id", 1), ("turn_number", 1)])
        await _db.feedback.create_index("session_id")
        await _db.eval_results.create_index([("session_id", 1), ("turn_number", 1)])

        logger.info("MongoDB connected: %s/%s", uri, db_name)
        return _db
    except Exception as exc:
        logger.warning("MongoDB unavailable (%s) — session persistence disabled", exc)
        _client = None
        _db = None
        return None


def get_db() -> AsyncIOMotorDatabase | None:
    """Return the current database handle, or None if MongoDB is unavailable."""
    return _db


async def close_mongo() -> None:
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None
        logger.info("MongoDB connection closed.")


# ── Session helpers ──────────────────────────────────────────────────────────

_SESSION_TTL_HOURS = 24


async def load_session(session_id: str) -> dict | None:
    """Load a session document from MongoDB."""
    db = get_db()
    if db is None:
        return None
    return await db.sessions.find_one({"_id": session_id})


async def save_session(session_id: str, data: dict) -> None:
    """Upsert a session document."""
    db = get_db()
    if db is None:
        return
    now = datetime.now(timezone.utc)
    data.update({
        "_id": session_id,
        "updated_at": now,
        "ttl_expires_at": now + timedelta(hours=_SESSION_TTL_HOURS),
    })
    data.setdefault("created_at", now)
    await db.sessions.replace_one({"_id": session_id}, data, upsert=True)


async def save_message(
    session_id: str,
    turn_number: int,
    role: str,
    content: str,
    *,
    agents_used: list[str] | None = None,
    sources: list[str] | None = None,
    follow_ups: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Insert a message into the messages collection. Returns message_id."""
    db = get_db()
    if db is None:
        return str(uuid4())
    msg_id = str(uuid4())
    await db.messages.insert_one({
        "_id": msg_id,
        "session_id": session_id,
        "turn_number": turn_number,
        "role": role,
        "content": content,
        "agents_used": agents_used or [],
        "sources": sources or [],
        "follow_ups": follow_ups or [],
        "metadata": metadata or {},
        "created_at": datetime.now(timezone.utc),
    })
    return msg_id


async def save_feedback(
    session_id: str,
    message_id: str,
    rating: int,
    comment: str | None = None,
) -> None:
    """Insert user feedback."""
    db = get_db()
    if db is None:
        return
    await db.feedback.insert_one({
        "_id": str(uuid4()),
        "session_id": session_id,
        "message_id": message_id,
        "rating": rating,
        "comment": comment,
        "created_at": datetime.now(timezone.utc),
    })


async def save_eval_result(
    session_id: str,
    turn_number: int,
    scores: dict,
) -> None:
    """Insert DeepEval evaluation results."""
    db = get_db()
    if db is None:
        return
    await db.eval_results.insert_one({
        "_id": str(uuid4()),
        "session_id": session_id,
        "turn_number": turn_number,
        **scores,
        "created_at": datetime.now(timezone.utc),
    })
