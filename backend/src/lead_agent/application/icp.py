from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lead_agent.domain.configuration import IcpConfig, RoutingConfig, WorkspaceIcpDraftConfig
from lead_agent.domain.models import NormalizedLead, QualificationResult
from lead_agent.domain.scoring import qualify_lead
from lead_agent.infrastructure.db.models import (
    LeadORM,
    WorkspaceAdminAuditEventORM,
    WorkspaceIcpVersionORM,
)
from lead_agent.infrastructure.reasoning.deterministic import DeterministicReasoningProvider


@dataclass(frozen=True)
class ResolvedIcp:
    configuration: IcpConfig
    source: str
    version_id: UUID | None
    version_number: int | None
    configuration_hash: str

    def trace_result(self, result: QualificationResult) -> QualificationResult:
        return replace(
            result,
            icp_version_id=self.version_id,
            icp_source=self.source,
            icp_profile_version=self.configuration.profile_version,
            icp_configuration_hash=self.configuration_hash,
        )


def configuration_hash(configuration: IcpConfig | dict[str, Any]) -> str:
    payload = (
        configuration.model_dump(mode="json")
        if isinstance(configuration, IcpConfig)
        else configuration
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def resolve_workspace_icp(
    session: Session, workspace_id: UUID, system_default: IcpConfig
) -> ResolvedIcp:
    row = session.scalar(
        select(WorkspaceIcpVersionORM)
        .where(
            WorkspaceIcpVersionORM.workspace_id == workspace_id,
            WorkspaceIcpVersionORM.status == "published",
        )
        .order_by(WorkspaceIcpVersionORM.version_number.desc())
        .limit(1)
    )
    if row is None:
        return ResolvedIcp(
            configuration=system_default,
            source="system_default",
            version_id=None,
            version_number=None,
            configuration_hash=configuration_hash(system_default),
        )
    configuration = IcpConfig.model_validate(row.configuration)
    return ResolvedIcp(
        configuration=configuration,
        source="workspace",
        version_id=row.id,
        version_number=row.version_number,
        configuration_hash=configuration_hash(row.configuration),
    )


def list_versions(session: Session, workspace_id: UUID) -> list[WorkspaceIcpVersionORM]:
    return list(
        session.scalars(
            select(WorkspaceIcpVersionORM)
            .where(WorkspaceIcpVersionORM.workspace_id == workspace_id)
            .order_by(WorkspaceIcpVersionORM.version_number.desc())
        )
    )


def get_version(
    session: Session, workspace_id: UUID, version_id: UUID
) -> WorkspaceIcpVersionORM | None:
    return session.scalar(
        select(WorkspaceIcpVersionORM).where(
            WorkspaceIcpVersionORM.id == version_id,
            WorkspaceIcpVersionORM.workspace_id == workspace_id,
        )
    )


def _audit(
    session: Session,
    workspace_id: UUID,
    actor_user_id: UUID | None,
    operation: str,
    version: WorkspaceIcpVersionORM,
    **metadata: Any,
) -> None:
    session.add(
        WorkspaceAdminAuditEventORM(
            id=uuid4(),
            workspace_id=workspace_id,
            actor_id=actor_user_id,
            target_membership_id=None,
            operation=operation,
            before={},
            after={
                "icp_version_id": str(version.id),
                "version_number": version.version_number,
                "status": version.status,
                **metadata,
            },
            created_at=datetime.now(UTC),
        )
    )


def _editable_configuration(configuration: dict[str, Any], version_number: int) -> dict[str, Any]:
    draft = WorkspaceIcpDraftConfig.model_validate(configuration)
    return draft.model_copy(update={"profile_version": f"workspace-v{version_number}"}).model_dump(
        mode="json"
    )


def create_draft(
    session: Session,
    workspace_id: UUID,
    actor_label: str,
    actor_user_id: UUID | None,
    system_default: IcpConfig,
    *,
    name: str | None = None,
    description: str = "",
    based_on_version_id: UUID | None = None,
    configuration: dict[str, Any] | None = None,
    commit: bool = True,
) -> WorkspaceIcpVersionORM:
    latest = session.scalar(
        select(func.max(WorkspaceIcpVersionORM.version_number)).where(
            WorkspaceIcpVersionORM.workspace_id == workspace_id
        )
    )
    version_number = int(latest or 0) + 1
    if based_on_version_id is not None:
        base = get_version(session, workspace_id, based_on_version_id)
        if base is None:
            raise LookupError("ICP version not found")
        source_configuration = base.configuration
        source_name = base.name
        source_description = base.description
    else:
        active = resolve_workspace_icp(session, workspace_id, system_default)
        source_configuration = active.configuration.model_dump(mode="json")
        source_name = active.configuration.profile_name
        source_description = ""
    safe_configuration = _editable_configuration(
        configuration or source_configuration, version_number
    )
    now = datetime.now(UTC)
    row = WorkspaceIcpVersionORM(
        id=uuid4(),
        workspace_id=workspace_id,
        version_number=version_number,
        status="draft",
        name=(name or source_name).strip(),
        description=description.strip() if description else source_description,
        configuration=safe_configuration,
        created_by=actor_label[:255],
        created_at=now,
        updated_at=now,
        published_by=None,
        published_at=None,
        effective_at=None,
        archived_at=None,
    )
    session.add(row)
    _audit(session, workspace_id, actor_user_id, "icp.draft_created", row)
    if commit:
        session.commit()
    else:
        session.flush()
    session.refresh(row)
    return row


def update_draft(
    session: Session,
    workspace_id: UUID,
    version_id: UUID,
    actor_user_id: UUID | None,
    *,
    name: str | None = None,
    description: str | None = None,
    configuration: dict[str, Any] | None = None,
) -> WorkspaceIcpVersionORM:
    row = get_version(session, workspace_id, version_id)
    if row is None:
        raise LookupError("ICP version not found")
    if row.status != "draft":
        raise ValueError("published and archived ICP versions are immutable; create a new draft")
    if name is not None:
        if not name.strip():
            raise ValueError("ICP name is required")
        row.name = name.strip()
    if description is not None:
        row.description = description.strip()
    if configuration is not None:
        row.configuration = _editable_configuration(configuration, row.version_number)
    row.updated_at = datetime.now(UTC)
    _audit(session, workspace_id, actor_user_id, "icp.draft_updated", row)
    session.commit()
    session.refresh(row)
    return row


def validate_draft(
    session: Session,
    workspace_id: UUID,
    version_id: UUID,
    actor_user_id: UUID | None,
    *,
    record_audit: bool = True,
) -> tuple[WorkspaceIcpDraftConfig, list[str]]:
    row = get_version(session, workspace_id, version_id)
    if row is None:
        raise LookupError("ICP version not found")
    try:
        draft = WorkspaceIcpDraftConfig.model_validate(row.configuration)
        problems = draft.publication_problems()
    except ValidationError as error:
        raise ValueError("Malformed ICP configuration: " + str(error)) from error
    if record_audit:
        _audit(
            session,
            workspace_id,
            actor_user_id,
            "icp.validated",
            row,
            valid=not problems,
            problem_count=len(problems),
        )
        session.commit()
    return draft, problems


def publish_draft(
    session: Session,
    workspace_id: UUID,
    version_id: UUID,
    actor_label: str,
    actor_user_id: UUID | None,
) -> WorkspaceIcpVersionORM:
    row = get_version(session, workspace_id, version_id)
    if row is None:
        raise LookupError("ICP version not found")
    if row.status != "draft":
        raise ValueError("only a draft ICP can be published")
    draft, problems = validate_draft(
        session, workspace_id, version_id, actor_user_id, record_audit=False
    )
    if problems:
        raise ValueError("ICP cannot be published: " + " ".join(problems))
    published = draft.to_published()
    now = datetime.now(UTC)
    previous = list(
        session.scalars(
            select(WorkspaceIcpVersionORM).where(
                WorkspaceIcpVersionORM.workspace_id == workspace_id,
                WorkspaceIcpVersionORM.status == "published",
                WorkspaceIcpVersionORM.id != row.id,
            )
        )
    )
    for old in previous:
        old.status = "archived"
        old.archived_at = now
        old.updated_at = now
    row.configuration = published.model_dump(mode="json")
    row.status = "published"
    row.published_by = actor_label[:255]
    row.published_at = now
    row.effective_at = now
    row.updated_at = now
    _audit(
        session,
        workspace_id,
        actor_user_id,
        "icp.published",
        row,
        previous_versions_archived=[item.version_number for item in previous],
    )
    session.commit()
    session.refresh(row)
    return row


def archive_version(
    session: Session,
    workspace_id: UUID,
    version_id: UUID,
    actor_user_id: UUID | None,
) -> WorkspaceIcpVersionORM:
    row = get_version(session, workspace_id, version_id)
    if row is None:
        raise LookupError("ICP version not found")
    if row.status == "archived":
        return row
    row.status = "archived"
    row.archived_at = datetime.now(UTC)
    row.updated_at = row.archived_at
    _audit(session, workspace_id, actor_user_id, "icp.version_archived", row)
    session.commit()
    session.refresh(row)
    return row


def _normalized_lead(row: LeadORM) -> NormalizedLead:
    return NormalizedLead(
        id=row.id,
        source=row.source,
        source_detail=row.source_detail,
        external_id=row.external_id,
        full_name=row.full_name,
        email=row.email,
        phone=row.phone,
        job_title=row.job_title,
        company_name=row.company_name,
        industry=row.industry,
        employee_count=row.employee_count,
        annual_revenue=(
            Decimal(str(row.annual_revenue)) if row.annual_revenue is not None else None
        ),
        geography=row.geography,
        budget=Decimal(str(row.budget)) if row.budget is not None else None,
        product_or_service=row.product_or_service,
        message=row.message,
        received_at=row.received_at,
        raw_payload=row.raw_payload,
        normalization_version=row.normalization_version,
    )


def preview_draft(
    session: Session,
    workspace_id: UUID,
    version_id: UUID,
    routing: RoutingConfig,
    actor_user_id: UUID | None,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    row = get_version(session, workspace_id, version_id)
    if row is None:
        raise LookupError("ICP version not found")
    draft, problems = validate_draft(
        session, workspace_id, version_id, actor_user_id, record_audit=False
    )
    if problems:
        raise ValueError("ICP preview requires a valid 40-point draft: " + " ".join(problems))
    configuration = draft.to_published()
    leads = list(
        session.scalars(
            select(LeadORM)
            .where(LeadORM.workspace_id == workspace_id)
            .order_by(LeadORM.received_at.desc())
            .limit(max(1, min(limit, 500)))
        )
    )
    provider = DeterministicReasoningProvider()
    changes: list[dict[str, Any]] = []
    transitions: dict[str, int] = {}
    for lead_row in leads:
        lead = _normalized_lead(lead_row)
        analysis = provider.analyze(lead, configuration)
        proposed = qualify_lead(lead, configuration, routing, analysis)  # type: ignore[arg-type]
        current = lead_row.qualification
        current_score = current.total_score if current else 0
        current_band = current.classification if current else "unqualified"
        if proposed.total_score == current_score and proposed.classification == current_band:
            continue
        transition = f"{current_band} -> {proposed.classification}"
        transitions[transition] = transitions.get(transition, 0) + 1
        changed_matches = [
            line.reason
            for line in proposed.score_breakdown
            if line.component == "icp_fit" and line.points > 0
        ][:3]
        changes.append(
            {
                "lead_id": str(lead_row.id),
                "lead_name": lead_row.full_name,
                "company_name": lead_row.company_name,
                "current_score": current_score,
                "proposed_score": proposed.total_score,
                "score_delta": proposed.total_score - current_score,
                "current_classification": current_band,
                "proposed_classification": proposed.classification,
                "important_changed_matches": changed_matches,
            }
        )
    _audit(
        session,
        workspace_id,
        actor_user_id,
        "icp.preview_run",
        row,
        leads_evaluated=len(leads),
        changed_count=len(changes),
    )
    session.commit()
    return {
        "version_id": str(row.id),
        "leads_evaluated": len(leads),
        "changed_count": len(changes),
        "transitions": transitions,
        "representative_changes": changes[:20],
    }
