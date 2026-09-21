from __future__ import annotations

import hashlib
from functools import lru_cache
from urllib.parse import urlencode
from uuid import UUID

from lead_agent.domain.integrations import OAuthBroker


class DevelopmentOAuthBroker:
    """A deterministic local OAuth boundary, replaced by provider brokers in production."""

    def begin(self, provider: str, connection_id: UUID, state: str) -> str:
        query = urlencode(
            {"connection_id": str(connection_id), "provider": provider, "state": state}
        )
        return f"/v1/integrations/oauth/development-authorize?{query}"

    def exchange(self, provider: str, authorization_code: str) -> dict[str, str]:
        if not authorization_code.strip():
            raise ValueError("authorization code is required")
        digest = hashlib.sha256(authorization_code.encode()).hexdigest()
        return {
            "access_token": f"development-oauth-{digest}",
            "token_type": "bearer",
            "provider": provider,
        }


@lru_cache
def get_oauth_broker() -> OAuthBroker:
    return DevelopmentOAuthBroker()
