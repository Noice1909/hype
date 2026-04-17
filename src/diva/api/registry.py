"""API router registry — single entry point for all routers.

Import ``root_router`` in main.py to mount everything.
To add a new router, just include it here — no changes needed in main.py.
"""

from __future__ import annotations

from fastapi import APIRouter

from diva.api.routes_chat import router as query_router
from diva.api.routes_sessions import router as sessions_router
from diva.api.routes_feedback import router as feedback_router
from diva.api.routes_health import router as health_router

root_router = APIRouter()
root_router.include_router(query_router, prefix="/api/v1")
root_router.include_router(sessions_router, prefix="/api/v1")
root_router.include_router(feedback_router, prefix="/api/v1")
root_router.include_router(health_router)
