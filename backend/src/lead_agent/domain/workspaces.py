from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

DEFAULT_WORKSPACE_ID = UUID("00000000-0000-0000-0000-000000000001")


@dataclass(frozen=True)
class WorkspaceContext:
    workspace_id: UUID
    actor_id: str = "development-user"
