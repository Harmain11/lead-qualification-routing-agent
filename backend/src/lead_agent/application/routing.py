from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.domain.analysis import LeadContextAnalysis, load_persisted_analysis
from lead_agent.domain.configuration import (
    ActionConfig,
    RepConfig,
    RoutingConfig,
    RoutingRuleConfig,
)
from lead_agent.domain.models import QualificationResult
from lead_agent.domain.rbac import effective_permissions, normalize_role
from lead_agent.domain.routing import (
    ActionPlan,
    NotificationDelivery,
    NotificationMessage,
    NotificationProvider,
    RoutePriority,
)
from lead_agent.domain.workspaces import DEFAULT_WORKSPACE_ID
from lead_agent.application.assignment_notifications import notify_assigned_member
from lead_agent.application.lead_access import assign_lead
from lead_agent.infrastructure.db.models import (
    LeadORM,
    LeadOutcomeORM,
    NotificationORM,
    PipelineEventORM,
    ProviderConnectionORM,
    RoutingDecisionORM,
    UserORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
    WorkspaceRoutingConfigORM,
)
from lead_agent.infrastructure.notifications.mock import MockNotificationProvider
from lead_agent.infrastructure.observability import get_observability, provider_operation
from lead_agent.infrastructure.provider_lifecycle import record_provider_lifecycle_audit
from lead_agent.infrastructure.repositories import get_current_routing
from lead_agent.settings.config import get_settings

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
    return load_persisted_analysis(lead.analysis.structured_output)


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
        icp_version_id=row.icp_version_id,
        icp_source=row.icp_source,
        icp_profile_version=row.icp_profile_version,
        icp_configuration_hash=row.icp_configuration_hash,
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


def _action_plan_excerpt(value: str, *, limit: int = 180) -> str:
    """Keep a compact action-plan summary without ending in a partial word."""
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    boundary = normalized.rfind(" ", 0, limit + 1)
    if boundary <= 0:
        return f"{normalized[:limit].rstrip()}…"
    return f"{normalized[:boundary].rstrip(' ,;:.')}…"


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
        talking_points.insert(0, f"Address the stated pain: {_action_plan_excerpt(pain)}")
    questions = _unknown_questions(analysis)[:5]
    if qualification.hard_disqualified or qualification.classification == "disqualify":
        strategy = [
            "Verify the disqualifying evidence before any outreach.",
            "If confirmed, close the lead with a clear reason; do not pursue a sales conversion.",
        ]
        goal = "Confirm fit and close appropriately."
    elif analysis and analysis.requires_manual_review:
        strategy = [
            "Validate the original inquiry and uncertain analysis with a human reviewer.",
            "Confirm missing facts before choosing an outreach cadence or making a promise.",
        ]
        goal = "Resolve uncertainty and decide whether to qualify."
    elif qualification.classification == "hot":
        strategy = [
            "Contact promptly, acknowledge the stated need, and offer a discovery call.",
            "On the call, confirm the buying owner, budget, timeline, and success criteria.",
            "Send a tailored recap and agreed next step; follow up after one business day.",
        ]
        goal = "Book a discovery call and agree the path to a proposal."
    elif qualification.classification == "warm":
        strategy = [
            "Reach out within one business day with a relevant question about the stated need.",
            "Share a concise, relevant example only after confirming fit and timing.",
            "Offer a discovery call; follow up in two to three business days if no reply.",
        ]
        goal = "Confirm fit and schedule a discovery call."
    elif qualification.classification == "needs_qualifying":
        strategy = [
            "Ask the highest-priority missing qualification questions before pitching.",
            "Use the answers to establish fit, timeline, and decision ownership.",
            "If qualified, propose a discovery call; otherwise provide a relevant resource.",
        ]
        goal = "Establish enough evidence to qualify or nurture the lead."
    else:
        strategy = [
            "Send one useful, relevant resource without a hard sales pitch.",
            "Ask permission to reconnect when timing or need is clearer.",
        ]
        goal = "Maintain a useful relationship until buying intent is confirmed."
    if questions and qualification.classification not in {"disqualify", "nurture"}:
        strategy[1 if len(strategy) > 1 else 0] += f" First clarify: {questions[0]}"
    return ActionPlan(
        immediate_action=action.immediate_action,
        response_priority=action.priority,
        talking_points=talking_points[:4],
        unanswered_questions=questions,
        known_pain_point=pain,
        urgency_timeline=urgency,
        potential_objections=(
            [analysis.summary.potential_concern]
            if analysis and analysis.summary.potential_concern
            else []
        ),
        next_step_recommendation=action.next_step,
        supporting_evidence=evidence[:5],
        follow_up_strategy=strategy,
        conversion_goal=goal,
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


def _choose_workspace_rep(
    session: Session, routing: RoutingConfig, queue: str, lead: LeadORM
) -> tuple[RepConfig | None, str]:
    # Lock the workspace until this routing decision is committed, so concurrent
    # workers do not read the same workload and repeatedly pick the same member.
    workspace = session.scalar(
        select(WorkspaceORM).where(WorkspaceORM.id == lead.workspace_id).with_for_update()
    )
    if workspace is None:
        if lead.workspace_id == DEFAULT_WORKSPACE_ID:
            return _choose_rep(routing, queue, lead)
        return None, "Workspace is unavailable; assignment withheld."
    if workspace.is_demo or lead.workspace_id == DEFAULT_WORKSPACE_ID:
        return _choose_rep(routing, queue, lead)
    members = session.execute(
        select(WorkspaceMembershipORM, UserORM)
        .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
        .where(
            WorkspaceMembershipORM.workspace_id == lead.workspace_id,
            WorkspaceMembershipORM.status == "active",
            UserORM.status == "active",
        )
    ).all()
    eligible = [
        (membership, user)
        for membership, user in members
        if "leads.edit" in effective_permissions(membership.role, membership.permission_overrides)
    ]
    # Prefer working sales reps; managers and owners serve as a safe fallback.
    role_rank = {"sales_rep": 0, "sales_manager": 1, "admin": 2, "owner": 3}
    if not eligible:
        return None, "No active workspace member with lead-edit access is available."
    best_rank = min(role_rank.get(normalize_role(m.role), 4) for m, _ in eligible)
    candidates = [
        (m, u) for m, u in eligible if role_rank.get(normalize_role(m.role), 4) == best_rank
    ]
    counts = dict(
        session.execute(
            select(RoutingDecisionORM.assigned_rep_id, func.count(RoutingDecisionORM.id))
            .outerjoin(
                LeadOutcomeORM,
                (LeadOutcomeORM.lead_id == RoutingDecisionORM.lead_id)
                & (LeadOutcomeORM.workspace_id == lead.workspace_id),
            )
            .where(
                RoutingDecisionORM.workspace_id == lead.workspace_id,
                RoutingDecisionORM.is_current.is_(True),
                RoutingDecisionORM.assigned_rep_id.is_not(None),
                (LeadOutcomeORM.id.is_(None))
                | (LeadOutcomeORM.pipeline_stage.not_in(("closed_won", "closed_lost"))),
            )
            .group_by(RoutingDecisionORM.assigned_rep_id)
        ).all()
    )
    from lead_agent.application.territories import capacity_candidates

    candidates, capacity_reason = capacity_candidates(session, lead, candidates, counts)
    if not candidates:
        return None, capacity_reason
    config = session.get(WorkspaceRoutingConfigORM, lead.workspace_id)
    raw_weights = config.member_weights if config is not None else {}
    weights = {
        str(member.user_id): max(float(raw_weights.get(str(member.user_id), 1)), 0.01)
        for member, _ in candidates
    }
    candidates.sort(
        key=lambda item: (
            counts.get(str(item[1].id), 0) / weights[str(item[1].id)],
            str(item[1].id),
        )
    )
    _, selected = candidates[0]
    load = counts.get(str(selected.id), 0)
    weight = weights[str(selected.id)]
    return (
        RepConfig(id=str(selected.id), name=selected.display_name or selected.email, queue=queue),
        f"Assigned active workspace {normalize_role(candidates[0][0].role).replace('_', ' ')} "
        f"{selected.display_name or selected.email} with {load} open assigned lead(s), "
        f"the lowest weighted load among {len(candidates)} eligible peer(s) "
        f"(routing weight {weight:g}). {capacity_reason}",
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
    workspace_name: str,
) -> str:
    marker = "[HIGH PRIORITY] " if priority in {"urgent", "high"} else ""

    def compact(value: str | None, limit: int) -> str:
        return " ".join((value or "").split())[:limit]

    frontend_url = get_settings().frontend_public_base_url.strip().rstrip("/")
    app_link = f"\nOpen lead: {frontend_url}/?lead={lead.id}" if frontend_url else ""
    summary = compact(analysis.summary.need if analysis else lead.message, 320)
    pain = compact(analysis.summary.pain_point if analysis else None, 220)
    first_follow_up = (
        action_plan.follow_up_strategy[0]
        if action_plan.follow_up_strategy
        else action_plan.next_step_recommendation
    )
    source_detail = f" ({compact(lead.source_detail, 90)})" if lead.source_detail else ""
    band_label = qualification.classification.replace("_", " ").title()
    icp_disclaimer = (
        "\nICP fit was not scored. Add and publish a custom ICP in Settings to enable fit scoring.\n"
        if qualification.icp_source == "not_configured"
        else ""
    )
    return (
        f"{marker}{compact(lead.company_name, 100)} — {compact(lead.full_name, 100)}\n"
        f"Workspace: {compact(workspace_name, 100)} | Source: {compact(lead.source, 60)}"
        f"{source_detail}\n"
        f"Score: {qualification.total_score}/100 | Lead temperature: {band_label}\n"
        f"{icp_disclaimer}"
        f"Summary: {summary or 'No inquiry summary available.'}\n"
        f"Pain point: {pain or 'Not confirmed'}\n"
        f"Route: {compact(queue, 80)} | Assigned: {compact(rep_name, 100) or 'Unassigned'}\n"
        f"Next step: {compact(action_plan.next_step_recommendation, 300)}\n"
        f"Follow-up: {compact(first_follow_up, 300)}\n"
        f"Conversion goal: {compact(action_plan.conversion_goal, 180) or 'Confirm fit'}{app_link}"
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


class _UnavailableNotificationProvider:
    provider_name = "slack"

    def __init__(self, message: str) -> None:
        self.message = message

    def send(self, message: NotificationMessage) -> NotificationDelivery:
        del message
        return NotificationDelivery(state="failed", error_message=self.message, retryable=False)


def notify_routing_decision(
    session: Session,
    decision: RoutingDecisionORM,
    lead: LeadORM,
    qualification: QualificationResult,
    analysis: LeadContextAnalysis | None,
    provider: NotificationProvider | None = None,
) -> NotificationORM:
    selected_provider = provider or MockNotificationProvider()
    resolver = getattr(selected_provider, "resolve_for_lead", None)
    if callable(resolver):
        active_provider = resolver(session, lead.workspace_id, lead, decision, actor_id="system")
    else:
        resolver = getattr(selected_provider, "resolve_for_workspace", None)
        active_provider = (
            resolver(session, lead.workspace_id, actor_id="system")
            if callable(resolver)
            else selected_provider
        )
    if active_provider is None:
        active_provider = _UnavailableNotificationProvider(
            "No verified Slack connection and notification channel are configured "
            "for this workspace."
        )
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
    elif existing.status == "retryable":
        retry_after_until = existing.payload.get("retry_after_until")
        if isinstance(retry_after_until, str):
            try:
                retry_at = datetime.fromisoformat(retry_after_until)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=UTC)
                if retry_at > now:
                    return existing
            except ValueError:
                pass

    if active_provider.provider_name == "slack":
        # Serialize Slack notifications per lead. A committed pending record
        # also blocks a second route from posting while the first request is in flight.
        session.execute(
            select(LeadORM.id).where(LeadORM.id == lead.id).with_for_update()
        )
        prior_notification = session.scalar(
            select(NotificationORM)
            .join(
                RoutingDecisionORM,
                NotificationORM.routing_decision_id == RoutingDecisionORM.id,
            )
            .where(
                RoutingDecisionORM.workspace_id == lead.workspace_id,
                RoutingDecisionORM.lead_id == lead.id,
                RoutingDecisionORM.id != decision.id,
                NotificationORM.provider == "slack",
                NotificationORM.status.in_(("pending", "retryable", "sent")),
            )
            .order_by(NotificationORM.created_at.asc())
        )
        if prior_notification is not None:
            existing.status = "suppressed"
            existing.attempts = 0
            existing.error_message = (
                "A Slack notification for this lead is already sent or pending."
            )
            existing.payload = {
                "suppressed_reason": "lead_already_notified_or_pending",
                "prior_notification_id": str(prior_notification.id),
            }
            existing.updated_at = now
            decision.notification_state = "suppressed"
            record_pipeline_event(
                session,
                lead.id,
                "notified",
                "suppressed",
                {
                    "provider": "slack",
                    "notification_id": str(existing.id),
                    "reason": "lead_already_notified_or_pending",
                },
            )
            session.commit()
            return existing
    if existing is not None and existing.status != "sent" and existing.attempts >= 5:
        existing.status = "failed"
        existing.error_message = (
            "Notification retry limit reached; review the connection and channel."
        )
        existing.updated_at = now
        decision.notification_state = "failed"
        session.commit()
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
            session.get(WorkspaceORM, lead.workspace_id).name
            if session.get(WorkspaceORM, lead.workspace_id)
            else "Workspace",
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
    if delivery.retry_after_seconds is not None and delivery.retryable:
        existing.payload = {
            **existing.payload,
            "retry_after_until": (
                datetime.now(UTC) + timedelta(seconds=delivery.retry_after_seconds)
            ).isoformat(),
        }
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
    if active_provider.provider_name == "slack":
        raw_connection_id = getattr(active_provider, "connection_id", None)
        try:
            connection_id = UUID(str(raw_connection_id)) if raw_connection_id else None
        except ValueError:
            connection_id = None
        connection = session.get(ProviderConnectionORM, connection_id) if connection_id else None
        if connection is not None and connection.workspace_id == lead.workspace_id:
            record_provider_lifecycle_audit(
                session,
                connection,
                action="lead_notification",
                outcome=delivery.state,
                actor_id="system",
                details={
                    "lead_id": str(lead.id),
                    "routing_decision_id": str(decision.id),
                    "notification_id": str(existing.id),
                    "channel_id": connection.connection_metadata.get("slack_channel_id"),
                },
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
    send_notification: bool = True,
    manual_review_reason: str | None = None,
) -> RoutingDecisionORM:
    qualification = _qualification(lead)
    analysis = _analysis(lead)
    current = get_current_routing(session, lead.id, lead.workspace_id)
    manually_assigned = bool(current and current.is_manually_assigned)
    if current is not None and not force:
        return current
    if current is not None:
        current.is_current = False

    manual_review = bool(
        lead.manual_review_completed_at is None
        and analysis
        and analysis.requires_manual_review
    )
    selected_rule, matching_rules = _select_rule(routing.rules, lead, qualification, analysis)
    band = qualification.classification
    if manual_review:
        selected_rule = None
        queue = routing.manual_review_queue
        priority: RoutePriority = "urgent"
        action_key = "manual_review"
        reason = manual_review_reason or (
            analysis.error_message
            if analysis and analysis.error_code == "manual_review_conditions"
            else "Lead matches an enabled manual-review condition."
        )
        matched_rules = (
            ["gmail_personal_email_review"]
            if manual_review_reason
            else ["manual_review_precedence"]
        )
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
    if manually_assigned and current is not None:
        if current.assigned_rep_id:
            assigned_rep = RepConfig(
                id=current.assigned_rep_id,
                name=current.assigned_rep_name or current.assigned_rep_id,
                queue=current.assigned_queue,
            )
            assignment_reason = "Manual representative assignment preserved."
        else:
            assignment_reason = "Manual unassignment preserved."
    elif not qualification.hard_disqualified and queue != "archive":
        assigned_rep, assignment_reason = _choose_workspace_rep(session, routing, queue, lead)
    action_plan = _build_action_plan(routing, qualification, lead, analysis, action_key)
    sla_hours = (
        selected_rule.sla_hours
        if selected_rule is not None and selected_rule.sla_hours is not None
        else _action_for(routing, action_key).sla_hours
    )
    now = datetime.now(UTC)
    from lead_agent.application.territories import lead_territory

    territory = lead_territory(session, lead)
    if territory:
        sla_hours = territory.sla_hours
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
        notification_state="pending" if send_notification else "suppressed",
        manual_review=manual_review,
        is_manually_assigned=manually_assigned,
        idempotency_key=idempotency_key,
        is_current=True,
        created_at=now,
    )
    canonical_owner_id = None
    if assigned_rep is not None:
        try:
            candidate_owner_id = UUID(assigned_rep.id)
        except ValueError:
            candidate_owner_id = None
        if candidate_owner_id is not None and session.scalar(select(WorkspaceMembershipORM.id).where(
            WorkspaceMembershipORM.workspace_id == lead.workspace_id,
            WorkspaceMembershipORM.user_id == candidate_owner_id,
            WorkspaceMembershipORM.status == "active",
        )) is not None:
            canonical_owner_id = candidate_owner_id
    if current is not None and lead.current_owner_id is None and current.assigned_rep_id:
        try:
            lead.current_owner_id = UUID(current.assigned_rep_id)
        except ValueError:
            pass
    # Legacy static routing entries that are not active workspace users remain
    # notification-only; they never gain record access through an arbitrary ID.
    assign_lead(
        session,
        lead,
        canonical_owner_id,
        reason="automatic routing" if not manually_assigned else "preserved manual assignment",
    )
    session.add(decision)
    try:
        session.flush()
    except IntegrityError:
        if not force:
            session.rollback()
            existing = get_current_routing(session, lead.id, lead.workspace_id)
            if existing is not None:
                return existing
        raise
    previous_assignee_id = current.assigned_rep_id if current is not None else None
    if (
        send_notification
        and assigned_rep is not None
        and assigned_rep.id != previous_assignee_id
    ):
        try:
            recipient_user_id = UUID(assigned_rep.id)
        except ValueError:
            recipient_user_id = None
        if recipient_user_id is not None:
            notify_assigned_member(
                session,
                workspace_id=lead.workspace_id,
                lead_id=lead.id,
                recipient_user_id=recipient_user_id,
            )
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
    if send_notification:
        notify_routing_decision(
            session,
            decision,
            lead,
            qualification,
            analysis,
            notification_provider,
        )
    else:
        record_pipeline_event(
            session,
            lead.id,
            "notified",
            "suppressed",
            {"routing_decision_id": str(decision.id), "reason": "analysis_backfill"},
        )
        session.commit()
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
