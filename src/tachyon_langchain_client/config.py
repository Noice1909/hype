"""Tachyon configuration — reads TACHYON_* environment variables."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

_REQUIRED = (
    "TACHYON_BASE_URL",
    "TACHYON_API_KEY",
    "TACHYON_APIGEE_URL",
    "TACHYON_CONSUMER_KEY",
    "TACHYON_CONSUMER_SECRET",
    "TACHYON_USE_CASE_ID",
)


@dataclass
class TachyonConfig:
    base_url: str
    api_key: str
    apigee_url: str
    consumer_key: str
    consumer_secret: str
    use_case_id: str
    certs_path: str = field(default="")
    use_api_gateway: str = field(default="TRUE")

    # ── factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> TachyonConfig:
        """Load config from environment variables, reading .env if not already set."""
        load_dotenv(override=False)
        return cls(
            base_url=os.getenv("TACHYON_BASE_URL", ""),
            api_key=os.getenv("TACHYON_API_KEY", ""),
            apigee_url=os.getenv("TACHYON_APIGEE_URL", ""),
            consumer_key=os.getenv("TACHYON_CONSUMER_KEY", ""),
            consumer_secret=os.getenv("TACHYON_CONSUMER_SECRET", ""),
            use_case_id=os.getenv("TACHYON_USE_CASE_ID", ""),
            certs_path=os.getenv("TACHYON_CERTS_PATH", ""),
            use_api_gateway=os.getenv("TACHYON_USE_API_GATEWAY", "TRUE"),
        )

    # ── validation ────────────────────────────────────────────────────────────

    def validate(self) -> None:
        """Raise ValueError if any required field is missing."""
        missing = [k for k in _REQUIRED if not getattr(self, k.replace("TACHYON_", "").lower())]
        if missing:
            raise ValueError(
                f"Missing required Tachyon env vars: {', '.join(missing)}. "
                "Set them before starting the server."
            )

    # ── helpers ───────────────────────────────────────────────────────────────

    @property
    def gateway_enabled(self) -> bool:
        return self.use_api_gateway.upper() != "FALSE"

    @property
    def client_id(self) -> str:
        """use_case_id with '_langchain' suffix (matches reference impl)."""
        uid = self.use_case_id
        return uid if "langchain" in uid.lower() else f"{uid}_langchain"
