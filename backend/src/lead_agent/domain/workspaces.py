from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

DEFAULT_WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000001")
DEMO_WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000006")


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: UUID
    actor_id: str = "development-user"
    user_id: UUID | None = None
    membership_id: UUID | None = None
    role: str = "owner"
    permissions: frozenset[str] = frozenset()
