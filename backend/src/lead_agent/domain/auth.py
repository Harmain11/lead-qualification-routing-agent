from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class AuthenticatedIdentity:
    user_id: UUID
    email: str
    display_name: str
    session_id: UUID
    current_workspace_id: UUID


class AuthenticationError(ValueError):
    """Raised when a credential cannot establish an authenticated identity."""


class AuthorizationError(ValueError):
    """Raised when an authenticated identity cannot access a workspace."""
