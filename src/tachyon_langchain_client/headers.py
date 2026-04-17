"""Per-request WF header builder for Tachyon API calls."""

from __future__ import annotations

import uuid
from datetime import datetime

from .auth import ApigeeTokenManager
from .config import TachyonConfig


class HeaderBuilder:
    """Builds the full set of required Tachyon/WF headers for each request.

    x-request-id and x-correlation-id are freshly generated per call so that
    each request is independently traceable in Tachyon logs (as required by
    the Tachyon API documentation).
    """

    def __init__(self, token_manager: ApigeeTokenManager, config: TachyonConfig) -> None:
        self._token_mgr = token_manager
        self._config = config

    async def build(self) -> dict[str, str]:
        """Return a headers dict with a fresh token and unique trace IDs."""
        token = await self._token_mgr.get_token()
        client_id = self._config.client_id  # use_case_id + "_langchain" suffix

        return {
            "x-request-id": str(uuid.uuid4()),
            "x-correlation-id": str(uuid.uuid4()),
            "x-wf-client-id": client_id,
            "x-wf-request-date": datetime.now().isoformat(),
            "x-wf-api-key": self._config.api_key,
            "x-wf-usecase-id": self._config.use_case_id,
            "Authorization": f"Bearer {token}",
            "From": client_id,
        }
