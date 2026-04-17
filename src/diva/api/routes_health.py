"""Health check endpoints — unauthenticated, no prefix."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from diva.dependencies import get_mcp_manager
from diva.storage.mongo import get_db

router = APIRouter(tags=["health"])


@router.get("/adcs-health")
@router.get("/adcs-health/", include_in_schema=False)
async def adcs_health():
    """Lightweight health check for Docker / OCP liveness probe."""
    return {"status": "healthy"}


@router.get("/health/ready")
async def health_ready():
    """Readiness probe — checks MongoDB and MCP connections."""
    checks: dict[str, str] = {}

    try:
        db = get_db()
        if db is not None:
            await db.command("ping")
            checks["mongodb"] = "ok"
        else:
            checks["mongodb"] = "unavailable"
    except Exception as e:
        checks["mongodb"] = f"error: {e}"

    mcp = get_mcp_manager()
    if mcp:
        for sid in mcp.server_ids:
            if mcp.is_connected(sid):
                checks[f"mcp.{sid}"] = "connected"

    is_ready = checks.get("mongodb") != "error" and any(
        v == "connected" for k, v in checks.items() if k.startswith("mcp.")
    )
    status_code = 200 if is_ready else 503
    return JSONResponse(
        {"status": "ready" if is_ready else "degraded", "checks": checks},
        status_code=status_code,
    )
