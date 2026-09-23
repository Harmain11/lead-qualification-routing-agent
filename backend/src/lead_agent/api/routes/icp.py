from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import get_policies, require_permission
from lead_agent.application.icp import (
    archive_version,
    create_draft,
    get_version,
    list_versions,
    preview_draft,
    publish_draft,
    resolve_workspace_icp,
    update_draft,
    validate_draft,
)
from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.models import WorkspaceIcpVersionORM
from lead_agent.infrastructure.db.session import get_db

router = APIRouter(prefix="/v1/icp", tags=["workspace-icp"])


class DraftCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    description: str = Field(default="", max_length=4000)
    based_on_version_id: UUID | None = None
    configuration: dict[str, Any] | None = None


class DraftUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)
    configuration: dict[str, Any] | None = None


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=100, ge=1, le=500)


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_new_leads_use_version: bool
    confirm_historical_results_preserved: bool
    confirm_exclusion_changes: bool = False


class ValidationResponse(BaseModel):
    valid: bool
    weight_total: int
    required_weight_total: int = 40
    problems: list[str]


class IcpVersionResponse(BaseModel):
    id: UUID | None
    workspace_id: UUID
    source: str
    version_number: int | None
    status: str
    name: str
    description: str
    configuration: dict[str, Any]
    weight_total: int
    created_by: str | None
    created_at: datetime | None
    updated_at: datetime | None
    published_by: str | None
    published_at: datetime | None
    effective_at: datetime | None
    archived_at: datetime | None
    editable: bool


def _weight_total(configuration: dict[str, Any]) -> int:
    scoring = configuration.get("scoring", {})
    points = scoring.get("component_points", {}) if isinstance(scoring, dict) else {}
    icp_points = points.get("icp_fit", {}) if isinstance(points, dict) else {}
    return sum(int(value) for value in icp_points.values()) if isinstance(icp_points, dict) else 0


def _version_response(row: WorkspaceIcpVersionORM) -> IcpVersionResponse:
    return IcpVersionResponse(
        id=row.id,
        workspace_id=row.workspace_id,
        source="workspace",
        version_number=row.version_number,
        status=row.status,
        name=row.name,
        description=row.description,
        configuration=row.configuration,
        weight_total=_weight_total(row.configuration),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        published_by=row.published_by,
        published_at=row.published_at,
        effective_at=row.effective_at,
        archived_at=row.archived_at,
        editable=row.status == "draft",
    )


def _system_response(workspace_id: UUID, configuration: IcpConfig) -> IcpVersionResponse:
    payload = configuration.model_dump(mode="json")
    return IcpVersionResponse(
        id=None,
        workspace_id=workspace_id,
        source="system_default",
        version_number=None,
        status="published",
        name=configuration.profile_name,
        description="System default ICP used until this workspace publishes a custom version.",
        configuration=payload,
        weight_total=_weight_total(payload),
        created_by="system",
        created_at=None,
        updated_at=None,
        published_by="system",
        published_at=None,
        effective_at=None,
        archived_at=None,
        editable=False,
    )


def _actor_label(workspace: WorkspaceContext) -> str:
    return workspace.actor_id or "system"


def _handle(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(status_code=404, detail=str(error))
    return HTTPException(status_code=422, detail=str(error))


@router.get("/current", response_model=IcpVersionResponse)
def current_icp(
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    workspace: WorkspaceContext = Depends(require_permission("icp.view")),
) -> IcpVersionResponse:
    resolved = resolve_workspace_icp(session, workspace.workspace_id, policies[0])
    if resolved.version_id is None:
        return _system_response(workspace.workspace_id, resolved.configuration)
    row = get_version(session, workspace.workspace_id, resolved.version_id)
    if row is None:  # pragma: no cover - protected by the resolver query
        raise HTTPException(status_code=500, detail="resolved ICP version is unavailable")
    return _version_response(row)


@router.get("/versions", response_model=list[IcpVersionResponse])
def versions(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("icp.view")),
) -> list[IcpVersionResponse]:
    return [_version_response(row) for row in list_versions(session, workspace.workspace_id)]


@router.get("/versions/{version_id}", response_model=IcpVersionResponse)
def version(
    version_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("icp.view")),
) -> IcpVersionResponse:
    row = get_version(session, workspace.workspace_id, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ICP version not found")
    return _version_response(row)


@router.post("/drafts", response_model=IcpVersionResponse, status_code=status.HTTP_201_CREATED)
def new_draft(
    request: DraftCreateRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    workspace: WorkspaceContext = Depends(require_permission("icp.manage")),
) -> IcpVersionResponse:
    try:
        row = create_draft(
            session,
            workspace.workspace_id,
            _actor_label(workspace),
            workspace.user_id,
            policies[0],
            **request.model_dump(),
        )
    except (ValueError, LookupError) as error:
        raise _handle(error) from error
    return _version_response(row)


@router.patch("/versions/{version_id}", response_model=IcpVersionResponse)
def edit_draft(
    version_id: UUID,
    request: DraftUpdateRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("icp.manage")),
) -> IcpVersionResponse:
    try:
        row = update_draft(
            session,
            workspace.workspace_id,
            version_id,
            workspace.user_id,
            **request.model_dump(exclude_unset=True),
        )
    except (ValueError, LookupError) as error:
        raise _handle(error) from error
    return _version_response(row)


@router.post("/versions/{version_id}/validate", response_model=ValidationResponse)
def validate(
    version_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("icp.manage")),
) -> ValidationResponse:
    try:
        draft, problems = validate_draft(
            session, workspace.workspace_id, version_id, workspace.user_id
        )
    except (ValueError, LookupError) as error:
        raise _handle(error) from error
    total = sum(draft.scoring.component_points.get("icp_fit", {}).values())
    return ValidationResponse(valid=not problems, weight_total=total, problems=problems)


@router.post("/versions/{version_id}/preview")
def preview(
    version_id: UUID,
    request: PreviewRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    workspace: WorkspaceContext = Depends(require_permission("icp.manage")),
) -> dict[str, Any]:
    try:
        return preview_draft(
            session,
            workspace.workspace_id,
            version_id,
            policies[1],
            workspace.user_id,
            limit=request.limit,
        )
    except (ValueError, LookupError) as error:
        raise _handle(error) from error


@router.post("/versions/{version_id}/publish", response_model=IcpVersionResponse)
def publish(
    version_id: UUID,
    request: PublishRequest,
    session: Session = Depends(get_db),
    policies: tuple[IcpConfig, RoutingConfig] = Depends(get_policies),
    workspace: WorkspaceContext = Depends(require_permission("icp.manage")),
) -> IcpVersionResponse:
    if (
        not request.confirm_new_leads_use_version
        or not request.confirm_historical_results_preserved
    ):
        raise HTTPException(status_code=422, detail="publication confirmations are required")
    row = get_version(session, workspace.workspace_id, version_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ICP version not found")
    current = resolve_workspace_icp(session, workspace.workspace_id, policies[0])
    draft_exclusions = row.configuration.get("disqualifiers", [])
    current_exclusions = current.configuration.model_dump(mode="json").get("disqualifiers", [])
    if draft_exclusions != current_exclusions and not request.confirm_exclusion_changes:
        raise HTTPException(
            status_code=422,
            detail="confirm_exclusion_changes is required because hard disqualifiers changed",
        )
    try:
        return _version_response(
            publish_draft(
                session,
                workspace.workspace_id,
                version_id,
                _actor_label(workspace),
                workspace.user_id,
            )
        )
    except (ValueError, LookupError) as error:
        raise _handle(error) from error


@router.post("/versions/{version_id}/archive", response_model=IcpVersionResponse)
def archive(
    version_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("icp.manage")),
) -> IcpVersionResponse:
    try:
        return _version_response(
            archive_version(session, workspace.workspace_id, version_id, workspace.user_id)
        )
    except (ValueError, LookupError) as error:
        raise _handle(error) from error
