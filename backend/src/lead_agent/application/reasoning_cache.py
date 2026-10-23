from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from lead_agent.domain.analysis import ReasoningOutput, ReasoningProvider
from lead_agent.domain.configuration import IcpConfig, ManualReviewConfig, RoutingConfig
from lead_agent.domain.models import NormalizedLead
from lead_agent.domain.operations import WorkspaceBusinessProfile
from lead_agent.infrastructure.db.models import AnalysisRunORM, LeadORM

_LOGGER = logging.getLogger("lead_agent.reasoning_cache")


def _model_dump(value: Any) -> Any:
    if value is None:
        return None
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return value


def reasoning_request_fingerprint(
    *,
    workspace_id: UUID,
    lead: NormalizedLead,
    icp: IcpConfig,
    routing: RoutingConfig,
    provider: ReasoningProvider,
    manual_review_config: ManualReviewConfig | None,
    business_profile: WorkspaceBusinessProfile | None,
    analyze_hard_disqualified: bool,
    icp_enabled: bool,
) -> str:
    """Hash every normalized input and configuration that can affect analysis."""
    normalized_lead = asdict(lead)
    normalized_lead.pop("id", None)
    payload = {
        "cache_schema": "reasoning-analysis-v1",
        "workspace_id": str(workspace_id),
        "provider": getattr(provider, "provider_name", ""),
        "model": getattr(provider, "model_name", ""),
        "prompt_version": getattr(provider, "prompt_version", ""),
        "lead": normalized_lead,
        "icp": _model_dump(icp),
        "routing": _model_dump(routing),
        "manual_review": _model_dump(manual_review_config or routing.manual_review),
        "business_profile": _model_dump(business_profile),
        "analyze_hard_disqualified": analyze_hard_disqualified,
        "icp_enabled": icp_enabled,
    }
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def find_cached_reasoning_output(
    session: Session,
    *,
    workspace_id: UUID,
    fingerprint: str,
    provider: ReasoningProvider,
) -> ReasoningOutput | None:
    """Return only a validated successful result from this workspace/config."""
    provider_name = getattr(provider, "provider_name", "")
    model_name = getattr(provider, "model_name", "")
    prompt_version = getattr(provider, "prompt_version", "")
    if not provider_name or provider_name in {"deterministic", "deterministic-safeguard"}:
        return None

    cached = session.scalar(
        select(AnalysisRunORM)
        .join(LeadORM, LeadORM.id == AnalysisRunORM.lead_id)
        .where(
            LeadORM.workspace_id == workspace_id,
            AnalysisRunORM.analysis_request_fingerprint == fingerprint,
            AnalysisRunORM.provider == provider_name,
            AnalysisRunORM.model == model_name,
            AnalysisRunORM.prompt_version == prompt_version,
            AnalysisRunORM.status.in_(("completed", "manual_review")),
        )
        .order_by(desc(AnalysisRunORM.created_at))
        .limit(1)
    )
    if cached is None:
        return None

    data = cached.structured_output
    if not isinstance(data, dict):
        _LOGGER.warning(
            "reasoning.analysis_cache_invalid",
            extra={"fields": {"analysis_id": str(cached.id), "reason": "unexpected_output_shape"}},
        )
        return None
    reasoning_data = {
        name: data[name]
        for name in ReasoningOutput.model_fields
        if name in data
    }
    try:
        return ReasoningOutput.model_validate(reasoning_data)
    except Exception:
        _LOGGER.warning(
            "reasoning.analysis_cache_invalid",
            extra={"fields": {"analysis_id": str(cached.id), "reason": "schema_validation_failed"}},
        )
        return None
