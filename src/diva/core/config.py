"""Centralized application settings via pydantic-settings.

All environment variables flow through this single ``Settings`` class —
no ``os.getenv`` calls anywhere else in the codebase. ``.env`` is loaded
automatically; OCP / container env vars override file values.

Usage::

    from diva.core.config import get_settings
    settings = get_settings()
    print(settings.llm_provider)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root resolved from this file's location: src/diva/core/config.py → repo/
# Pinning the .env path here makes Settings load identically regardless of the
# Python process's cwd (start.ps1, uvicorn, pytest, ad-hoc scripts).
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Single source of truth for all runtime configuration."""

    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App / server ────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO", description="Root log level")
    log_format: Literal["text", "json"] = Field(default="text")
    diva_config_dir: str = Field(default="configs", description="Path to YAML configs")
    diva_rate_limit: int = Field(default=60, description="Requests per minute per IP")
    diva_auth_enabled: bool = Field(default=False)
    diva_cors_origins: str = Field(default="*", description="Comma-separated CORS origins")
    diva_mcp_servers: str = Field(
        default="",
        description=(
            "DEPRECATED — derived automatically from enabled agents. "
            "Set only as an explicit override when you need an MCP server "
            "running without an associated agent."
        ),
    )
    diva_enabled_agents: str = Field(
        default="",
        description=(
            "Comma-separated agent ids to enable. Empty = follow agents.yaml "
            "`enabled` flags. Set value overrides YAML completely (exhaustive "
            "allowlist). Unknown ids are warned and ignored."
        ),
    )
    diva_host: str = Field(default="0.0.0.0")
    diva_port: int = Field(default=8000)

    # ── LLM provider ────────────────────────────────────────────────────────
    llm_provider: Literal["ollama", "tachyon"] = Field(default="ollama")
    ollama_model: str = Field(default="llama3.1")
    ollama_base_url: str = Field(default="http://localhost:11434")
    tachyon_model: str = Field(default="gemini-2.0-flash-001")
    tachyon_base_url: str = Field(default="")
    tachyon_apigee_url: str = Field(default="")
    tachyon_consumer_key: str = Field(default="")
    tachyon_consumer_secret: str = Field(default="")
    tachyon_use_case_id: str = Field(default="")
    tachyon_certs_path: str = Field(default="")
    tachyon_use_api_gateway: str = Field(default="TRUE")

    # ── MongoDB (DIVA session storage) ──────────────────────────────────────
    mongodb_uri: str = Field(default="mongodb://localhost:27017")
    diva_db_name: str = Field(default="diva")

    # ── DDA MongoDB (collection name — read via MongoDB MCP) ─────
    dda_mongodb_mcp_url: str = Field(
        default="http://127.0.0.1:8080/mcp",
        description="MCP server endpoint the dda-agent talks to",
    )
    dda_mongodb_connection_string: str = Field(
        default="",
        description="Atlas/self-hosted connection string for the DDA cluster",
    )
    dda_mongodb_database: str = Field(
        default="collection name",
        description="Default database name for the DDA cluster",
    )

    # ── Neo4j MCP endpoint ──────────────────────────────────────────────────
    # The MCP server is single-tenant and owns its own NEO4J_URI/USER/PASSWORD/
    # DATABASE in its own environment — DIVA only needs the URL to talk to it.
    neo4j_mcp_url: str = Field(default="http://127.0.0.1:3006/mcp")

    # ── External MCP server credentials ─────────────────────────────────────
    github_token: str = Field(default="")
    jira_mcp_url: str = Field(default="")
    confluence_mcp_url: str = Field(default="")
    oracle_dsn: str = Field(default="")
    oracle_user: str = Field(default="")
    oracle_password: str = Field(default="")
    dataplex_mcp_url: str = Field(default="")
    dataplex_project: str = Field(default="")
    dataplex_location: str = Field(default="")
    google_application_credentials: str = Field(default="")
    autosys_api_url: str = Field(default="")
    autosys_token: str = Field(default="")

    # ── Evaluation (DeepEval) ───────────────────────────────────────────────
    deepeval_model: str = Field(default="gpt-4o")
    deepeval_telemetry_opt_out: str = Field(default="YES")
    deepeval_grpc_logging: str = Field(default="NO")
    error_reporting: str = Field(default="NO")
    do_not_track: str = Field(default="1")
    confident_ai_opt_out: str = Field(default="YES")
    deepeval_results_folder: str = Field(default="/tmp/deepeval")
    deepeval_file_system: str = Field(default="READ_ONLY")

    # ── Helpers ─────────────────────────────────────────────────────────────

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.diva_cors_origins.split(",") if o.strip()]

    @property
    def mcp_servers_list(self) -> list[str]:
        return [s.strip() for s in self.diva_mcp_servers.split(",") if s.strip()]

    @property
    def enabled_agents_override(self) -> set[str] | None:
        """Returns the set of agent ids requested via env, or None when the
        env var is unset/blank (meaning: defer to agents.yaml `enabled` flags).
        """
        raw = self.diva_enabled_agents.strip()
        if not raw:
            return None
        return {s.strip() for s in raw.split(",") if s.strip()}

    def deepeval_env(self) -> dict[str, str]:
        """All DeepEval/telemetry env vars — exported so downstream code
        can apply them via ``os.environ.update`` once at startup."""
        return {
            "DEEPEVAL_TELEMETRY_OPT_OUT": self.deepeval_telemetry_opt_out,
            "DEEPEVAL_GRPC_LOGGING": self.deepeval_grpc_logging,
            "ERROR_REPORTING": self.error_reporting,
            "DO_NOT_TRACK": self.do_not_track,
            "CONFIDENT_AI_OPT_OUT": self.confident_ai_opt_out,
            "DEEPEVAL_RESULTS_FOLDER": self.deepeval_results_folder,
            "DEEPEVAL_FILE_SYSTEM": self.deepeval_file_system,
        }

    def mcp_server_env(self) -> dict[str, str]:
        """Values resolved when the MCP yaml config contains ``${VAR}``
        placeholders. Keep every secret/URL here — NEVER hardcode them in
        mcp_servers.yaml."""
        return {
            # Neo4j MCP — URL only; the MCP server owns its DB credentials
            "NEO4J_MCP_URL": self.neo4j_mcp_url,
            # DIVA's own MongoDB (session storage, not the DDA cluster)
            "MONGODB_URI": self.mongodb_uri,
            # DDA cluster — MongoDB MCP server connection + headers
            "DDA_MONGODB_MCP_URL": self.dda_mongodb_mcp_url,
            "DDA_MONGODB_CONNECTION_STRING": self.dda_mongodb_connection_string,
            "DDA_MONGODB_DATABASE": self.dda_mongodb_database,
            # Platform integrations
            "GITHUB_TOKEN": self.github_token,
            "JIRA_MCP_URL": self.jira_mcp_url,
            "CONFLUENCE_MCP_URL": self.confluence_mcp_url,
            "ORACLE_DSN": self.oracle_dsn,
            "ORACLE_USER": self.oracle_user,
            "ORACLE_PASSWORD": self.oracle_password,
            "DATAPLEX_MCP_URL": self.dataplex_mcp_url,
            "DATAPLEX_PROJECT": self.dataplex_project,
            "DATAPLEX_LOCATION": self.dataplex_location,
            "GOOGLE_APPLICATION_CREDENTIALS": self.google_application_credentials,
            "AUTOSYS_API_URL": self.autosys_api_url,
            "AUTOSYS_TOKEN": self.autosys_token,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached Settings instance — loaded once per process."""
    return Settings()
