from __future__ import annotations

from functools import lru_cache
from uuid import UUID

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.crm import CrmProvider
from lead_agent.domain.workspaces import DEFAULT_WORKSPACE_ID, WorkspaceContext
from lead_agent.infrastructure.crm.factory import (
    WorkspaceCrmProviderResolver,
    get_crm_provider,
    get_workspace_crm_resolver,
)
from lead_agent.infrastructure.db.session import get_db
from lead_agent.settings.config import Settings, get_settings


@lru_cache
def get_policies() -> tuple[IcpConfig, RoutingConfig]:
    return get_settings().load_policies()


def get_runtime_settings() -> Settings:
    return get_settings()


def get_workspace_context(
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
    x_actor_id: str | None = Header(default=None, alias="X-Actor-ID"),
) -> WorkspaceContext:
    try:
        workspace_id = UUID(x_workspace_id) if x_workspace_id else DEFAULT_WORKSPACE_ID
    except ValueError as error:
        raise HTTPException(status_code=400, detail="X-Workspace-ID must be a UUID") from error
    return WorkspaceContext(
        workspace_id=workspace_id,
        actor_id=(x_actor_id or "development-user")[:255],
    )


def get_active_crm_provider(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(get_workspace_context),
    fallback: CrmProvider = Depends(get_crm_provider),
    resolver: WorkspaceCrmProviderResolver = Depends(get_workspace_crm_resolver),
) -> CrmProvider:
    return resolver.resolve(session, workspace.workspace_id, fallback)
