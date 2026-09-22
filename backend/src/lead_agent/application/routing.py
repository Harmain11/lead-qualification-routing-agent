from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.analysis import LeadContextAnalysis
from lead_agent.domain.configuration import (
    ActionConfig,
    RepConfig,
    RoutingConfig,
    RoutingRuleConfig,
)
from lead_agent.domain.models import QualificationResult
from lead_agent.domain.routing import (
    ActionPlan,
    NotificationDelivery,
    NotificationMessage,
    NotificationProvider,
    RoutePriority,
)
from lead_agent.infrastructure.db.models import (
    LeadORM,
    NotificationORM,
    PipelineEventORM,
    RoutingDecisionORM,
)
from lead_agent.infrastructure.notifications.mock import MockNotificationProvider
from lead_agent.infrastructure.observability import get_observability, provider_operation
from lead_agent.infrastructure.repositories import get_current_routing

_LOGGER = logging.getLogger("lead_agent.notifications")


def _normalized(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def _matches(value: str | None, options: list[str]) -> bool:
    normalized = _normalized(value)
    return bool(normalized) and any(
        _normalized(option) in normalized or normalized in _normalized(option) for option in options
    )


def _analysis(lead: LeadORM) -> LeadContextAnalysis | None:
    if lead.analysis is None:
        return None
    return LeadContextAnalysis.model_validate(lead.analysis.structured_output)


def _qualification(lead: LeadORM) -> QualificationResult:
    if lead.qualification is None:
        raise ValueError("lead has no qualification result")
    row = lead.qualification
    return QualificationResult(
        total_score=row.total_score,
        classification=row.classification,
        routing_queue=row.routing_queue,
        routing_action=row.routing_action,
        hard_disqualified=row.hard_disqualified,
        disqualifier_code=row.disqualifier_code,
        rationale=tuple(row.rationale),
        score_breakdown=tuple(),
        policy_version=row.policy_version,
        confidence=float(row.confidence),
    )


def _rule_matches(
    rule: RoutingRuleConfig,
    lead: LeadORM,
    qualification: QualificationResult,
    analysis: LeadContextAnalysis | None,
) -> bool:
    if rule.qualification_bands and qualification.classification not in rule.qualification_bands:
        return False
    if rule.geographies and not _matches(lead.geography, rule.geographies):
        return False
    if rule.industries and not _matches(lead.industry, rule.industries):
        return False
    if rule.services and not _matches(lead.product_or_service, rule.services):
        return False
    if rule.buyer_roles and not _matches(lead.job_title, rule.buyer_roles):
        return False
    if rule.sources and not _matches(lead.source, rule.sources):
        return False
    urgency = analysis.context.urgency.value if analysis else None
    if rule.urgency and not _matches(urgency, rule.urgency):
        return False
    technical = analysis.context.technical_requirements.value if analysis else None
    if rule.technical_specializations and not _matches(technical, rule.technical_specializations):
        return False
    if rule.company_size_min is not None and (
        lead.employee_count is None or lead.employee_count < rule.company_size_min
    ):
        return False
    if rule.company_size_max is not None and (
        lead.employee_count is None or lead.employee_count > rule.company_size_max
    ):
        return False
    return True


def _rule_specificity(rule: RoutingRuleConfig) -> int:
    return sum(
        bool(values)
        for values in (
            rule.qualification_bands,
            rule.geographies,
            rule.industries,
            rule.services,
            rule.buyer_roles,
            rule.sources,
            rule.urgency,
            rule.technical_specializations,
        )
    ) + int(rule.company_size_min is not None or rule.company_size_max is not None)


def _select_rule(
    rules: list[RoutingRuleConfig],
    lead: LeadORM,
    qualification: QualificationResult,
    analysis: LeadContextAnalysis | None,
) -> tuple[RoutingRuleConfig | None, list[str]]:
    matches = [rule for rule in rules if _rule_matches(rule, lead, qualification, analysis)]
    matches.sort(key=lambda rule: (rule.precedence, -_rule_specificity(rule), rule.name))
    return (matches[0], [rule.name for rule in matches]) if matches else (None, [])


def _action_for(routing: RoutingConfig, key: str) -> ActionConfig:
    configured = routing.actions.get(key)
    if configured is not None:
        return configured
    return ActionConfig(
        key=key,
        immediate_action="Review the lead and determine the next appropriate step.",
        priority="normal",
        next_step="Confirm the next step from the available evidence.",
    )


def _unknown_questions(analysis: LeadContextAnalysis | None) -> list[str]:
    if analysis is None:
        return ["What is the required timeline and budget?"]
    questions = {
        "budget_information": "What budget range has been approved?",
        "explicit_timeline": "What timeline should the project meet?",
        "decision_maker_status": "Who owns the final buying decision?",
        "company_size_or_spend": "What is the relevant company size or spend context?",
        "technical_requirements": "Are there technical or integration requirements to confirm?",
    }
    return [questions[field] for field in analysis.unknown_fields if field in questions]


def _build_action_plan(
    routing: RoutingConfig,
    qualification: QualificationResult,
    lead: LeadORM,
    analysis: LeadContextAnalysis | None,
    action_key: str,
) -> ActionPlan:
    action = _action_for(routing, action_key)
    context = analysis.context if analysis else None
    pain = context.pain_point.value if context and context.pain_point.value else "Unknown"
    urgency = (
        context.explicit_timeline.value
        if context and context.explicit_timeline.value
        else context.urgency.value
        if context and context.urgency.value
        else "Unknown"
    )
    evidence: list[str] = []
    for field_name in ("pain_point", "explicit_timeline", "technical_requirements"):
        field = getattr(context, field_name, None) if context else None
        if field and field.evidence:
            evidence.extend(f"{item.source_field}: {item.excerpt}" for item in field.evidence[:1])
    talking_points = list(action.talking_points)
    if pain != "Unknown":
        talking_points.insert(0, f"Address the stated pain: {pain[:180]}")
    return ActionPlan(
        immediate_action=action.immediate_action,
        response_priority=action.priority,
        talking_points=talking_points[:4],
        unanswered_questions=_unknown_questions(analysis)[:5],
        known_pain_point=pain,
        urgency_timeline=urgency,
        potential_objections=(
            [analysis.summary.potential_concern]
            if analysis and analysis.summary.potential_concern
            else []
        ),
        next_step_recommendation=action.next_step,
        supporting_evidence=evidence[:5],
    )


def _choose_rep(routing: RoutingConfig, queue: str, lead: LeadORM) -> tuple[RepConfig | None, str]:
    candidates = [rep for rep in routing.reps if rep.active and rep.queue == queue]
    if not candidates:
        return None, "No active configured rep is available for this queue."

    def skill_score(rep: RepConfig) -> int:
        return sum(
            (
                3 if _matches(lead.geography, rep.geographies) else 0,
                2 if _matches(lead.industry, rep.industries) else 0,
                2 if _matches(lead.product_or_service, rep.services) else 0,
            )
        )

    candidates.sort(key=lambda rep: (-skill_score(rep), rep.priority, rep.id))
    selected = candidates[0]
    return (
        selected,
        f"Selected active rep {selected.name} by deterministic skill and priority match.",
    )


def _priority(value: str) -> RoutePriority:
    allowed: tuple[RoutePriority, ...] = ("urgent", "high", "normal", "low", "none")
    return value if value in allowed else "normal"


def _notification_text(
    lead: LeadORM,
    qualification: QualificationResult,
    analysis: LeadContextAnalysis | None,
    queue: str,
    rep_name: str | None,
    action_plan: ActionPlan,
    priority: RoutePriority,
) -> str:
    marker = "[HIGH PRIORITY] " if priority in {"urgent", "high"} else ""
    intent = analysis.intent_level if analysis else "unknown"
    rationale = (
        qualification.rationale[0][:220] if qualification.rationale else "No rationale recorded."
    )
    return (
        f"{marker}{lead.company_name} — {lead.full_name}\n"
        f"Source: {lead.source} | Score: {qualification.total_score}/100 | "
        f"Band: {qualification.classification}\n"
        f"Intent: {intent} | Queue: {queue} | Rep: {rep_name or 'Unassigned'}\n"
        f"Action: {action_plan.immediate_action}\n"
        f"Why: {rationale}"
    )[:3000]


def record_pipeline_event(
    session: Session, lead_id: UUID, stage: str, status: str, details: dict[str, Any]
) -> None:
    lead = session.get(LeadORM, lead_id)
    if lead is None:
        raise ValueError("cannot record pipeline event for an unknown lead")
    session.add(
        PipelineEventORM(
            id=uuid4(),
            workspace_id=lead.workspace_id,
            lead_id=lead_id,
            stage=stage,
            status=status,
            details=details,
            created_at=datetime.now(UTC),
        )
    )


def notify_routing_decision(
    session: Session,
    decision: RoutingDecisionORM,
    lead: LeadORM,
    qualification: QualificationResult,
    analysis: LeadContextAnalysis | None,
    provider: NotificationProvider | None = None,
) -> NotificationORM:
    active_provider = provider or MockNotificationProvider()
    existing = session.scalar(
        select(NotificationORM).where(
            NotificationORM.routing_decision_id == decision.id,
            NotificationORM.provider == active_provider.provider_name,
        )
    )
    now = datetime.now(UTC)
    if existing is None:
        existing = NotificationORM(
            id=uuid4(),
            workspace_id=lead.workspace_id,
            routing_decision_id=decision.id,
            provider=active_provider.provider_name,
            status="pending",
            attempts=0,
            payload={},
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
        session.flush()
    elif existing.status == "sent":
        return existing

    message = NotificationMessage(
        deduplication_key=f"routing:{decision.id}:{active_provider.provider_name}",
        priority=_priority(decision.priority),
        text=_notification_text(
            lead,
            qualification,
            analysis,
            decision.assigned_queue,
            decision.assigned_rep_name,
            ActionPlan.model_validate(decision.action_plan),
            _priority(decision.priority),
        ),
        metadata={"lead_id": str(lead.id), "routing_decision_id": str(decision.id)},
    )
    existing.payload = message.model_dump(mode="json")
    existing.attempts += 1
    existing.status = "pending"
    existing.updated_at = now
    decision.notification_state = "pending"
    session.commit()
    try:
        connection_id = getattr(active_provider, "connection_id", None)
        with provider_operation(
            active_provider.provider_name,
            str(connection_id) if connection_id else None,
        ) as operation_context:
            _LOGGER.info(
                "provider.operation.started",
                extra={"fields": {**operation_context, "operation": "notification_send"}},
            )
            delivery = active_provider.send(message)
            _LOGGER.info(
                "provider.operation.completed",
                extra={
                    "fields": {
                        **operation_context,
                        "operation": "notification_send",
                        "status": delivery.state,
                    }
                },
            )
    except Exception as error:
        get_observability().provider_failure(
            active_provider.provider_name,
            "transient",
            connection_id=str(getattr(active_provider, "connection_id", "") or "") or None,
            operation="notification_send",
            error_type=type(error).__name__,
        )
        delivery = NotificationDelivery(
            state="retryable", error_message=str(error)[:2000], retryable=True
        )
    if delivery.state in {"failed", "retryable"}:
        get_observability().provider_failure(
            active_provider.provider_name,
            "delivery",
            connection_id=str(getattr(active_provider, "connection_id", "") or "") or None,
            operation="notification_send",
            status=delivery.state,
            error_type="provider_delivery_error",
        )
    existing.status = delivery.state
    existing.external_message_id = delivery.external_id
    existing.error_message = delivery.error_message
    existing.sent_at = now if delivery.state == "sent" else None
    existing.updated_at = datetime.now(UTC)
    decision.notification_state = delivery.state
    record_pipeline_event(
        session,
        lead.id,
        "notified",
        delivery.state,
        {"provider": active_provider.provider_name, "notification_id": str(existing.id)},
    )
    session.commit()
    return existing


def route_and_notify(
    session: Session,
    lead: LeadORM,
    routing: RoutingConfig,
    *,
    notification_provider: NotificationProvider | None = None,
    force: bool = False,
    reroute_reason: str | None = None,
) -> RoutingDecisionORM:
    qualification = _qualification(lead)
    analysis = _analysis(lead)
    current = get_current_routing(session, lead.id, lead.workspace_id)
    if current is not None and not force:
        return current
    if current is not None:
        current.is_current = False

    manual_review = bool(
        analysis
        and (
            analysis.requires_manual_review
            or analysis.status in {"provider_error", "manual_review"}
        )
    )
    selected_rule, matching_rules = _select_rule(routing.rules, lead, qualification, analysis)
    band = qualification.classification
    if manual_review:
        selected_rule = None
        queue = routing.manual_review_queue
        priority: RoutePriority = "urgent"
        action_key = "manual_review"
        reason = (
            "Manual-review precedence: analysis uncertainty or provider failure "
            "requires human review."
        )
        matched_rules = ["manual_review_precedence"]
    elif qualification.hard_disqualified or band == "disqualify":
        selected_rule = None
        band_config = next(item for item in routing.score_bands if item.key == "disqualify")
        queue = band_config.queue
        priority = "none"
        action_key = "disqualify"
        reason = (
            "Hard disqualifier takes precedence; lead is retained without sales assignment."
            if qualification.hard_disqualified
            else "Disqualify-band precedence retains the lead without sales assignment."
        )
        matched_rules = [
            "hard_disqualifier_precedence"
            if qualification.hard_disqualified
            else "disqualify_band_precedence"
        ]
    elif band == "nurture":
        selected_rule = None
        band_config = next(item for item in routing.score_bands if item.key == "nurture")
        queue = band_config.queue
        action_key = "nurture"
        priority = _priority(_action_for(routing, action_key).priority)
        reason = "Nurture-band precedence retains the lead outside active sales assignment."
        matched_rules = ["nurture_band_precedence"]
    elif selected_rule is not None:
        queue = selected_rule.queue
        priority = _priority(selected_rule.priority)
        action_key = selected_rule.action_key or band
        reason = selected_rule.reason
        matched_rules = matching_rules
        if len(matching_rules) > 1:
            reason += (
                f" Rule precedence selected '{selected_rule.name}' over "
                f"{len(matching_rules) - 1} other match(es)."
            )
    else:
        fallback_band = next((item for item in routing.score_bands if item.key == band), None)
        queue = fallback_band.queue if fallback_band else routing.safe_default_queue
        action_key = band
        action = _action_for(routing, action_key)
        priority = _priority(action.priority)
        reason = f"Safe default route selected from qualification band '{band}'."
        matched_rules = [f"default_band:{band}"]

    assigned_rep, assignment_reason = (None, "No rep assignment is applicable.")
    if (
        not manual_review
        and not qualification.hard_disqualified
        and band not in {"nurture", "disqualify"}
        and queue
        not in {
            routing.manual_review_queue,
            "nurture",
            "archive",
        }
    ):
        assigned_rep, assignment_reason = _choose_rep(routing, queue, lead)
    action_plan = _build_action_plan(routing, qualification, lead, analysis, action_key)
    sla_hours = (
        selected_rule.sla_hours
        if selected_rule is not None and selected_rule.sla_hours is not None
        else _action_for(routing, action_key).sla_hours
    )
    now = datetime.now(UTC)
    sla_target = now + timedelta(hours=sla_hours) if sla_hours is not None else None
    qualification_id = lead.qualification.id if lead.qualification else "none"
    idempotency_key = f"{lead.id}:{qualification_id}"
    if force:
        idempotency_key = f"{idempotency_key}:reroute:{uuid4()}"
    decision = RoutingDecisionORM(
        id=uuid4(),
        workspace_id=lead.workspace_id,
        lead_id=lead.id,
        qualification_id=lead.qualification.id if lead.qualification else uuid4(),
        qualification_band=band,
        assigned_queue=queue,
        assigned_rep_id=assigned_rep.id if assigned_rep else None,
        assigned_rep_name=assigned_rep.name if assigned_rep else None,
        routing_reason=f"{reason} {assignment_reason}",
        matched_rules=matched_rules,
        action_plan=action_plan.model_dump(mode="json"),
        priority=priority,
        sla_target_at=sla_target,
        notification_state="pending",
        manual_review=manual_review,
        idempotency_key=idempotency_key,
        is_current=True,
        created_at=now,
    )
    session.add(decision)
    session.flush()
    record_pipeline_event(
        session,
        lead.id,
        "routed",
        "manual_review" if manual_review else "completed",
        {"routing_decision_id": str(decision.id), "queue": queue, "band": band},
    )
    if reroute_reason:
        decision.routing_reason += f" Reroute requested: {reroute_reason[:500]}"
    session.commit()
    notify_routing_decision(
        session,
        decision,
        lead,
        qualification,
        analysis,
        notification_provider,
    )
    return decision


def retry_current_notification(
    session: Session,
    lead: LeadORM,
    provider: NotificationProvider | None = None,
) -> NotificationORM:
    decision = get_current_routing(session, lead.id, lead.workspace_id)
    if decision is None:
        raise ValueError("lead has no routing decision")
    return notify_routing_decision(
        session,
        decision,
        lead,
        _qualification(lead),
        _analysis(lead),
        provider,
    )
