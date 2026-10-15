"""Durable, bounded playbooks and sequences; scheduled outreach only creates drafts."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.application.contacts import attach_contact
from lead_agent.application.follow_up import _current_email, generate_follow_up_draft
from lead_agent.application.governance import (
    assert_outreach_allowed,
    assert_residency,
    operations_policy,
)
from lead_agent.application.intelligence import (
    coach_lead,
    prepare_digest,
    research_company,
    store_insight,
    utc,
)
from lead_agent.application.routing import record_pipeline_event
from lead_agent.domain.operations import AutomationInput
from lead_agent.infrastructure.db.models import (
    LeadORM,
    PipelineEventORM,
    UserORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)
from lead_agent.infrastructure.db.operations import (
    AgentInsightORM,
    AutomationDefinitionORM,
    AutomationRunORM,
    CompanyORM,
    LeadNoteORM,
    OperationsJobORM,
    OperationsPolicyORM,
)


def next_contact_window(
    session: Session, lead: LeadORM, specification: dict[str, Any], after: datetime
) -> tuple[datetime, dict[str, Any]]:
    contact = attach_contact(session, lead)
    zone = ZoneInfo(
        contact.timezone if contact.timezone != "UTC" else specification.get("timezone", "UTC")
    )
    local = utc(after).astimezone(zone)
    hour = specification.get("preferred_hour", 9)
    method = "Configured contact-local hour"
    if specification.get("optimize_time", True):
        events = list(
            session.scalars(
                select(PipelineEventORM)
                .where(
                    PipelineEventORM.workspace_id == lead.workspace_id,
                    PipelineEventORM.lead_id == lead.id,
                    PipelineEventORM.stage == "gmail_message",
                    PipelineEventORM.status == "inbound",
                )
                .order_by(PipelineEventORM.created_at.desc())
                .limit(100)
            )
        )
        observed = Counter(utc(event.created_at).astimezone(zone).hour for event in events)
        business = {value: count for value, count in observed.items() if 8 <= value <= 18}
        if sum(business.values()) >= 3:
            hour = max(business, key=lambda value: business[value])
            method = "Most frequent observed inbound-reply hour"
    chosen = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if chosen < local:
        chosen += timedelta(days=1)
    while chosen.weekday() >= 5:
        chosen += timedelta(days=1)
    return chosen.astimezone(UTC), {
        "timezone": str(zone),
        "hour": hour,
        "method": method,
        "limitations": "A suggested draft-review window, not automatic email sending.",
    }


def latest_reply(session: Session, lead: LeadORM, since: datetime) -> PipelineEventORM | None:
    return session.scalar(
        select(PipelineEventORM)
        .where(
            PipelineEventORM.workspace_id == lead.workspace_id,
            PipelineEventORM.lead_id == lead.id,
            PipelineEventORM.stage == "gmail_message",
            PipelineEventORM.status == "inbound",
            PipelineEventORM.created_at >= since,
        )
        .order_by(PipelineEventORM.created_at.desc())
        .limit(1)
    )


def condition_matches(
    session: Session, lead: LeadORM, node: dict[str, Any], run: AutomationRunORM
) -> bool:
    since = datetime.fromisoformat(run.state["enrolled_at"])
    reply = latest_reply(session, lead, since)
    qualification = lead.qualification.classification if lead.qualification else ""
    outcome = lead.outcome.pipeline_stage if lead.outcome else ""
    test = str(node["condition"])
    if test in {"hot", "warm"}:
        return bool(qualification == test)
    if test == "replied":
        return reply is not None
    if test == "silent":
        return reply is None
    if test in {"won", "lost"}:
        return bool(outcome == "closed_" + test)
    if test == "stalled":
        return utc(lead.updated_at) < datetime.now(UTC) - timedelta(
            hours=node.get("wait_hours", 24)
        )
    if test == "competitor":
        text = lead.message + " " + (str(reply.details.get("body", "")) if reply else "")
        return any(
            re.search(r"(?<!\w)" + re.escape(card.competitor) + r"(?!\w)", text, re.I)
            for card in operations_policy(session, lead.workspace_id).battlecards
        )
    return False


def run_event(
    session: Session, run: AutomationRunORM, status: str, details: dict[str, Any]
) -> None:
    record_pipeline_event(
        session,
        run.lead_id,
        "automation",
        status,
        {"run_id": str(run.id), "node": run.cursor, **details},
    )


def advance_run(
    session: Session, run: AutomationRunORM, actor_id: str = "scheduler"
) -> AutomationRunORM:
    now = datetime.now(UTC)
    definition = session.get(AutomationDefinitionORM, run.definition_id)
    kind_snapshot = run.specification.get("kind", definition.kind if definition else "playbook")
    lead = session.get(LeadORM, run.lead_id)
    if definition is None or not definition.active or lead is None:
        run.status, run.last_error = (
            "paused",
            "The definition is inactive or the lead is unavailable.",
        )
        session.commit()
        session.refresh(run)
        return run
    workspace = session.get(WorkspaceORM, run.workspace_id)
    if workspace is None or workspace.status != "active":
        run.status, run.last_error = "paused", "Workspace is inactive"
        session.commit()
        session.refresh(run)
        return run
    from lead_agent.application.auth import membership_permissions

    try:
        creator_id = UUID(run.created_by)
    except ValueError:
        creator_id = None
    membership = session.scalar(
        select(WorkspaceMembershipORM)
        .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
        .where(
            WorkspaceMembershipORM.workspace_id == run.workspace_id,
            WorkspaceMembershipORM.user_id == creator_id,
            WorkspaceMembershipORM.status == "active",
            UserORM.status == "active",
        )
    )
    if membership is None or "leads.edit" not in membership_permissions(membership):
        run.status, run.last_error = (
            "paused",
            "The enrolling member no longer has permission to run this workflow",
        )
        session.commit()
        session.refresh(run)
        return run
    assert_residency(session, run.workspace_id)
    if (
        kind_snapshot == "sequence"
        and lead.outcome
        and lead.outcome.pipeline_stage in {"closed_won", "closed_lost"}
    ):
        run.status = "completed"
        run_event(session, run, "completed", {"reason": "Lead closed; outreach stopped."})
        session.commit()
        session.refresh(run)
        return run
    if run.status == "awaiting_draft":
        from lead_agent.infrastructure.db.models import LeadFollowUpEmailORM

        draft_id = run.state.get("draft_id")
        draft = session.get(LeadFollowUpEmailORM, UUID(draft_id)) if draft_id else None
        if draft is None or draft.status != "sent":
            session.refresh(run)
            return run
        node = next(node for node in run.specification["nodes"] if node["id"] == run.cursor)
        changed = cast(
            CursorResult[Any],
            session.execute(
                update(AutomationRunORM)
                .where(AutomationRunORM.id == run.id, AutomationRunORM.status == "awaiting_draft")
                .execution_options(synchronize_session=False)
                .values(
                    cursor=node.get("next"),
                    status="queued",
                    due_at=now,
                    state={key: value for key, value in run.state.items() if key != "draft_id"},
                )
            ),
        )
        session.commit()
        session.refresh(run)
        if not changed.rowcount:
            return run
    if run.status not in {"queued", "approved"} or utc(run.due_at) > now:
        session.refresh(run)
        return run
    claimed = cast(
        CursorResult[Any],
        session.execute(
            update(AutomationRunORM)
            .where(
                AutomationRunORM.id == run.id,
                AutomationRunORM.status.in_(("queued", "approved")),
                AutomationRunORM.due_at <= now,
            )
            .execution_options(synchronize_session=False)
            .values(status="processing", updated_at=now, attempts=AutomationRunORM.attempts + 1)
        ),
    )
    session.commit()
    if not claimed.rowcount:
        session.refresh(run)
        return run
    session.refresh(run)
    try:
        specification = AutomationInput.model_validate(run.specification)
        nodes = {node.id: node.model_dump() for node in specification.nodes}
        if kind_snapshot == "sequence":
            reply = latest_reply(session, lead, datetime.fromisoformat(run.state["enrolled_at"]))
            if reply and run.state.get("acknowledged_reply") != str(reply.id):
                run.status, run.last_error = (
                    "paused",
                    "A reply arrived. Review it before continuing the sequence.",
                )
                session.commit()
                session.refresh(run)
                return run
        for _ in range(50):
            node = nodes.get(run.cursor or "")
            if node is None or node["type"] == "end":
                run.status, run.cursor = "completed", None
                run_event(session, run, "completed", {})
                break
            kind = node["type"]
            if (
                kind == "draft"
                and lead.outcome
                and lead.outcome.pipeline_stage in {"closed_won", "closed_lost"}
            ):
                run.status, run.cursor = "completed", None
                run_event(session, run, "completed", {"reason": "Lead closed; draft stopped."})
                break
            if kind in {"coach", "research"} and "agents.execute" not in membership_permissions(
                membership
            ):
                raise ValueError("The enrolling member no longer has agent-execution permission")
            if kind == "start":
                run.cursor = node["next"]
                continue
            if kind == "condition":
                run.cursor = (
                    node["next"]
                    if condition_matches(session, lead, node, run)
                    else node["otherwise"]
                )
                continue
            if kind == "wait":
                after = now + timedelta(hours=node["wait_hours"])
                due, suggestion = next_contact_window(session, lead, run.specification, after)
                run.due_at, run.cursor, run.status = due, node["next"], "queued"
                run.state = {**run.state, "timing": suggestion}
                run_event(
                    session, run, "scheduled", {"due_at": due.isoformat(), "timing": suggestion}
                )
                break
            tier = operations_policy(session, run.workspace_id).approval_tiers.get(kind, "approve")
            if (
                (kind != "draft" or tier == "full_review")
                and tier != "automatic"
                and run.state.get("approved_node") != node["id"]
            ):
                run.status = "awaiting_approval"
                run_event(session, run, "awaiting_approval", {"action": kind, "tier": tier})
                break
            if kind == "draft":
                assert_outreach_allowed(session, run.workspace_id, lead.email)
                existing = _current_email(session, lead.id, lead.workspace_id)
                if existing and existing.status in {"draft", "sending", "delivery_uncertain"}:
                    draft = existing
                else:
                    draft = generate_follow_up_draft(session, lead, actor_id=actor_id)
                run.status = "awaiting_draft"
                run.state = {**run.state, "draft_id": str(draft.id)}
                run_event(
                    session,
                    run,
                    "draft_ready",
                    {"draft_id": str(draft.id), "approval_required": True},
                )
                break
            if kind == "coach":
                coach_lead(session, lead, actor_id)
            elif kind == "research":
                contact = attach_contact(session, lead)
                if not contact.company_id:
                    raise ValueError("No company is linked to this lead")
                company = session.get(CompanyORM, contact.company_id)
                if company is None:
                    raise ValueError("The linked company is unavailable")
                research_company(session, company, actor_id)
            elif kind == "note":
                note_id = uuid5(NAMESPACE_URL, f"corral:{run.id}:{node['id']}")
                if session.get(LeadNoteORM, note_id) is None:
                    session.add(
                        LeadNoteORM(
                            id=note_id,
                            workspace_id=run.workspace_id,
                            lead_id=lead.id,
                            body=node["text"] or "Playbook review required.",
                            author_id=actor_id,
                            updated_by=actor_id,
                        )
                    )
            run_event(
                session, run, "step_completed", {"action": kind, "approved_by": run.approved_by}
            )
            run.cursor, run.state = (
                node["next"],
                {key: value for key, value in run.state.items() if key != "approved_node"},
            )
            session.commit()
        run.updated_at, run.last_error = datetime.now(UTC), None
        session.commit()
    except Exception as error:
        session.rollback()
        session.refresh(run)
        run.status, run.last_error = (
            "blocked",
            (
                str(error)[:1000]
                if isinstance(error, ValueError)
                else "This step failed. Review its evidence, permissions and provider connection."
            ),
        )
        run.updated_at = datetime.now(UTC)
        run_event(session, run, "blocked", {"error": run.last_error})
        session.commit()
    session.refresh(run)
    return run


def watch_lead(session: Session, lead: LeadORM, actor_id: str = "watcher") -> AgentInsightORM:
    from lead_agent.application.services import reprocess_lead
    from lead_agent.settings.config import get_settings

    signal = session.scalar(
        select(PipelineEventORM)
        .where(
            PipelineEventORM.workspace_id == lead.workspace_id,
            PipelineEventORM.lead_id == lead.id,
            PipelineEventORM.stage.in_(("gmail_message", "manual_progress")),
        )
        .order_by(PipelineEventORM.created_at.desc())
        .limit(1)
    )
    previous = session.scalar(
        select(AgentInsightORM)
        .where(
            AgentInsightORM.workspace_id == lead.workspace_id,
            AgentInsightORM.lead_id == lead.id,
            AgentInsightORM.kind == "watcher",
        )
        .order_by(AgentInsightORM.created_at.desc())
        .limit(1)
    )
    key = str(signal.id) if signal else "initial"
    fingerprint = hashlib.sha256(f"watcher:{lead.id}:{key}".encode()).hexdigest()
    if previous and previous.fingerprint == fingerprint:
        return previous
    score_before = lead.qualification.total_score if lead.qualification else None
    if signal and (previous is None or previous.content.get("last_signal_id") != key):
        # Reuse the canonical pipeline; monitoring never sends email or external notifications.
        payload = dict(lead.raw_payload)
        original = lead.message
        events = list(
            session.scalars(
                select(PipelineEventORM)
                .where(
                    PipelineEventORM.workspace_id == lead.workspace_id,
                    PipelineEventORM.lead_id == lead.id,
                    PipelineEventORM.stage.in_(("gmail_message", "manual_progress")),
                )
                .order_by(PipelineEventORM.created_at.desc())
                .limit(20)
            )
        )
        history = [
            str(event.details.get("body") or event.details.get("note") or "")[:2000]
            for event in reversed(events)
        ]
        payload.update(
            source=lead.source,
            full_name=lead.full_name,
            email=lead.email,
            company_name=lead.company_name,
            product_or_service=lead.product_or_service,
            message=(original + "\nRecorded evidence:\n" + "\n".join(history))[:20000],
        )
        icp, routing = get_settings().load_policies()
        reprocess_lead(
            session,
            lead,
            payload,
            icp,
            routing,
            send_notification=False,
            send_confirmation_email=False,
            preserve_inquiry=True,
        )
    score = lead.qualification.total_score if lead.qualification else None
    delta = score - score_before if score is not None and score_before is not None else None
    return store_insight(
        session,
        lead.workspace_id,
        "watcher",
        {
            "summary": (
                f"New signal observed. Score movement: {delta}."
                if signal
                else "Monitoring enabled; waiting for a new recorded buying signal."
            ),
            "last_signal_id": key,
            "score": score,
            "previous_score": score_before,
            "score_delta": delta,
            "evidence": [
                {
                    "source": str(signal.id),
                    "text": str(signal.details.get("body") or signal.details.get("note") or ""),
                    "direction": signal.details.get("direction", "inbound"),
                }
            ]
            if signal
            else [],
            "limitations": (
                "Monitors acquired email/progress evidence; website visits are not tracked."
            ),
        },
        actor_id,
        lead_id=lead.id,
        fingerprint=fingerprint,
    )


def claim_job(session: Session, workspace_id: UUID, key: str) -> bool:
    now = datetime.now(UTC)
    row = session.get(OperationsJobORM, key)
    if row is None:
        try:
            with session.begin_nested():
                row = OperationsJobORM(id=key, workspace_id=workspace_id, next_at=now)
                session.add(row)
                session.flush()
        except IntegrityError:
            session.rollback()
    claimed = cast(
        CursorResult[Any],
        session.execute(
            update(OperationsJobORM)
            .where(OperationsJobORM.id == key, OperationsJobORM.next_at <= now)
            .execution_options(synchronize_session=False)
            .values(next_at=now + timedelta(minutes=10))
        ),
    )
    session.commit()
    return bool(claimed.rowcount)


def periodic_job(
    session: Session, workspace_id: UUID, key: str, callback: Callable[[], Any], interval: timedelta
) -> bool:
    if not claim_job(session, workspace_id, key):
        return False
    error = None
    try:
        callback()
    except Exception:
        session.rollback()
        error = (
            "Agent work failed. Check its source evidence, workspace "
            "policy and provider connection."
        )
    delay = max(interval, timedelta(hours=1)) if error else interval
    session.execute(
        update(OperationsJobORM)
        .where(OperationsJobORM.id == key)
        .values(next_at=datetime.now(UTC) + delay, last_error=error)
    )
    session.commit()
    return error is None


def poll_operations_once(session: Session) -> dict[str, int]:
    now = datetime.now(UTC)
    # Interrupted work needs explicit review before any action can repeat.
    session.execute(
        update(AutomationRunORM)
        .where(
            AutomationRunORM.status == "processing",
            AutomationRunORM.updated_at < now - timedelta(minutes=15),
        )
        .execution_options(synchronize_session=False)
        .values(
            status="blocked",
            last_error="Worker was interrupted. Review the lead history before resuming.",
            updated_at=now,
        )
    )
    session.commit()
    runs = list(
        session.scalars(
            select(AutomationRunORM)
            .where(
                AutomationRunORM.status.in_(("queued", "approved", "awaiting_draft")),
                AutomationRunORM.due_at <= now,
            )
            .order_by(AutomationRunORM.due_at)
            .limit(50)
        )
    )
    for run in runs:
        try:
            advance_run(session, run)
        except Exception:
            session.rollback()
            run.status, run.last_error = (
                "blocked",
                "Workspace policy prevents this run; review before resuming",
            )
            session.commit()
    watched = 0
    workspace_ids = list(session.scalars(select(OperationsPolicyORM.workspace_id)))
    for workspace_id in workspace_ids:
        workspace = session.get(WorkspaceORM, workspace_id)
        if workspace is None or workspace.status != "active":
            continue
        policy = operations_policy(session, workspace_id)
        try:
            assert_residency(session, workspace_id)
        except ValueError:
            continue
        if policy.watcher_enabled:
            leads = session.scalars(
                select(LeadORM)
                .where(LeadORM.workspace_id == workspace_id)
                .order_by(LeadORM.updated_at.desc())
            )
            for lead in list(leads):
                if lead.outcome and lead.outcome.pipeline_stage in {"closed_won", "closed_lost"}:
                    continue
                watched += periodic_job(
                    session,
                    workspace_id,
                    f"watch:{lead.id}",
                    partial(watch_lead, session, lead),
                    timedelta(seconds=30),
                )
        if policy.researcher_enabled:
            companies = list(
                session.scalars(
                    select(CompanyORM).where(
                        CompanyORM.workspace_id == workspace_id,
                        CompanyORM.website_reviewed.is_(True),
                    )
                )
            )
            for company in companies:
                periodic_job(
                    session,
                    workspace_id,
                    f"research:{company.id}",
                    partial(research_company, session, company, "researcher"),
                    timedelta(days=1),
                )
        if policy.coach_digest_enabled:
            members = list(
                session.scalars(
                    select(WorkspaceMembershipORM)
                    .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
                    .where(
                        WorkspaceMembershipORM.workspace_id == workspace_id,
                        WorkspaceMembershipORM.status == "active",
                        UserORM.status == "active",
                        WorkspaceMembershipORM.role.in_(
                            ("sales_rep", "sales_manager", "owner", "admin")
                        ),
                    )
                )
            )
            week = (now.date() - timedelta(days=now.weekday())).isoformat()
            for member in members:
                periodic_job(
                    session,
                    workspace_id,
                    f"digest:{workspace_id}:{member.user_id}:{week}",
                    partial(prepare_digest, session, workspace_id, member.user_id, "coach"),
                    timedelta(days=7),
                )
    return {"runs_checked": len(runs), "leads_watched": watched}
