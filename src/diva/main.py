"""FastAPI app factory for DIVA."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from diva.agents.registry import AgentRegistry
from diva.api.middleware import AuthMiddleware, RateLimitMiddleware, RequestIdMiddleware
from diva.api.registry import root_router
from diva.core.config import get_settings
from diva.dependencies import set_dependencies
from diva.graph.builder import build_graph
from diva.graph.nodes.agent_executor import configure_executor
from diva.graph.nodes.intake import configure_intake
from diva.graph.nodes.router import configure_router
from diva.graph.nodes.synthesizer import configure_synthesizer
from diva.llm.provider import get_llm
from diva.logging_config import setup_logging
from diva.mcp.client import MCPClientManager
from diva.storage.mongo import close_mongo, init_mongo

# Load settings (reads .env automatically) and apply environment-level
# configuration that must be set before downstream libraries import.
_settings = get_settings()

# Apply DeepEval / telemetry hardening to os.environ — required because
# deepeval / posthog / sentry libraries read directly from os.environ at
# import time and we cannot intercept that. setdefault preserves any
# OCP / container overrides.
for _k, _v in _settings.deepeval_env().items():
    os.environ.setdefault(_k, _v)

# Structured logging — coloured columns with sensitive-data masking
setup_logging(level=_settings.log_level, fmt=_settings.log_format)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001 — required by FastAPI lifespan API
    """App startup/shutdown lifecycle."""
    settings = get_settings()
    logger.info("DIVA starting up...")

    # 1. MongoDB
    await init_mongo()

    # 2. Agent registry
    registry = AgentRegistry.from_yaml(
        os.path.join(settings.diva_config_dir, "agents.yaml"),
    )

    # 3. MCP client manager
    mcp_config = os.path.join(settings.diva_config_dir, "mcp_servers.yaml")
    mcp_manager = MCPClientManager(mcp_config)

    # Start configured MCP servers — failures are non-fatal (graceful degradation)
    try:
        await mcp_manager.startup(server_ids=settings.mcp_servers_list)
    except Exception:
        logger.warning("MCP startup had errors — some agents may be unavailable")

    # 4. Configure context pipeline for intake node
    configure_intake(os.path.join(settings.diva_config_dir, "context.yaml"))

    # 5. Configure router + synthesizer with registry (both need agent descriptions)
    configure_router(registry)
    configure_synthesizer(registry)

    # 6. Configure agent executor with dependencies
    configure_executor(
        registry=registry,
        mcp_manager=mcp_manager,
        llm_factory=get_llm,
    )

    # 7. Build the LangGraph graph
    graph = build_graph()

    # 8. Set dependencies for FastAPI DI
    set_dependencies(registry, mcp_manager, graph)

    logger.info("DIVA ready — %d agents registered", len(registry.agent_ids))

    yield

    # Shutdown
    logger.info("DIVA shutting down...")
    await mcp_manager.shutdown()
    await close_mongo()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="DIVA",
        description="Enterprise Multi-Agent Chat System",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Middleware (outermost first)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        RateLimitMiddleware,
        max_requests=settings.diva_rate_limit,
        window_seconds=60,
    )
    app.add_middleware(AuthMiddleware, enabled=settings.diva_auth_enabled)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(root_router)
    return app


# For `uvicorn diva.main:app`
app = create_app()
