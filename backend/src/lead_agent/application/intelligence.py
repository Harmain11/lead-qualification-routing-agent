"""Evidence-backed research, conversation coaching and outcome learning."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.application.governance import assert_residency, operations_policy
from lead_agent.application.icp import create_draft, resolve_workspace_icp
from lead_agent.application.routing import record_pipeline_event
from lead_agent.domain.configuration import IcpConfig
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.company_research import fetch_company_page
from lead_agent.infrastructure.db.models import (
    LeadORM,
    LeadOutcomeORM,
    PipelineEventORM,
)
from lead_agent.infrastructure.db.operations import (
    AgentInsightORM,
    CompanyORM,
)
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def store_insight(
    session: Session,
    workspace_id: UUID,
    kind: str,
    content: dict[str, Any],
    actor_id: str,
    *,
    lead_id: UUID | None = None,
    company_id: UUID | None = None,
    fingerprint: str | None = None,
) -> AgentInsightORM:
    fingerprint = (
        fingerprint
        or hashlib.sha256(
            json.dumps(
                {
                    "kind": kind,
                    "lead": str(lead_id),
                    "company": str(company_id),
                    "content": content,
                },
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
    )
    previous = session.scalar(
        select(AgentInsightORM).where(
            AgentInsightORM.workspace_id == workspace_id, AgentInsightORM.fingerprint == fingerprint
        )
    )
    if previous:
        return previous
    row = AgentInsightORM(
        workspace_id=workspace_id,
        kind=kind,
        fingerprint=fingerprint,
        content=content,
        created_by=actor_id,
        lead_id=lead_id,
        company_id=company_id,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        previous = session.scalar(
            select(AgentInsightORM).where(
                AgentInsightORM.workspace_id == workspace_id,
                AgentInsightORM.fingerprint == fingerprint,
            )
        )
    if row not in session:
        previous = session.scalar(
            select(AgentInsightORM).where(
                AgentInsightORM.workspace_id == workspace_id,
                AgentInsightORM.fingerprint == fingerprint,
            )
        )
        if previous is None:
            raise RuntimeError("Insight conflict could not be resolved")
        return previous
    if lead_id:
        record_pipeline_event(
            session,
            lead_id,
            kind,
            "pending_review",
            {
                "insight_id": str(row.id),
                "summary": content.get("summary", ""),
                "actor_id": actor_id,
            },
        )
    session.commit()
    session.refresh(row)
    return row


def research_company(session: Session, company: CompanyORM, actor_id: str) -> AgentInsightORM:
    assert_residency(session, company.workspace_id)
    if not company.website_reviewed or not company.website:
        raise ValueError("Review and approve the company's official website before research")
    provider = resolve_workspace_reasoning_provider(session, company.workspace_id)
    summarize = getattr(provider, "summarize_evidence", None)
    if not callable(summarize):
        raise ValueError("Connect and verify OpenAI before company research")
    page = fetch_company_page(company.website)
    result = summarize(
        {"company": company.name, "evidence": [page]}, "company research and enrichment"
    )
    for fact in result["facts"]:
        if fact["source"] != page["url"] or fact["excerpt"] not in page["text"]:
            raise ValueError("Research returned an unsupported citation; no enrichment was saved")
    result["company_name"] = company.name
    result["sources"] = [{"url": page["url"], "fetched_at": datetime.now(UTC).isoformat()}]
    result["limitations"] = (
        "Only the reviewed company page was read. Missing funding, "
        "hiring or technology facts remain unknown."
    )
    row = store_insight(
        session, company.workspace_id, "research", result, actor_id, company_id=company.id
    )
    return row


def conversation_evidence(session: Session, lead: LeadORM) -> list[dict[str, Any]]:
    events = list(
        session.scalars(
            select(PipelineEventORM)
            .where(
                PipelineEventORM.workspace_id == lead.workspace_id,
                PipelineEventORM.lead_id == lead.id,
                PipelineEventORM.stage.in_(("gmail_message", "manual_progress")),
            )
            .order_by(PipelineEventORM.created_at)
            .limit(200)
        )
    )
    items = [{"source": str(lead.id), "text": lead.message[:5000], "direction": "inbound"}]
    for event in events:
        data = event.details
        text = data.get("body") or data.get("message") or data.get("note") or ""
        if text:
            items.append(
                {
                    "source": str(event.id),
                    "text": str(text)[:5000],
                    "direction": data.get("direction", "inbound"),
                    "at": event.created_at.isoformat(),
                }
            )
    return items


def coach_lead(session: Session, lead: LeadORM, actor_id: str) -> AgentInsightORM:
    assert_residency(session, lead.workspace_id)
    items = conversation_evidence(session, lead)
    outbound = [item for item in items if item["direction"] in {"outbound", "sent"}]
    inbound = [item for item in items if item not in outbound]
    objections = []
    labels = {
        "price": r"\b(expensive|cost|price|budget)\b",
        "timing": r"\b(later|not now|next quarter|delay)\b",
        "trust": r"\b(risk|security|proof|references)\b",
        "competitor": r"\b(competitor|alternative|already use)\b",
    }
    for item in inbound:
        for label, pattern in labels.items():
            match = re.search(pattern, item["text"], re.I)
            if match:
                objections.append(
                    {
                        "tag": label,
                        "excerpt": item["text"][max(0, match.start() - 80) : match.end() + 120],
                        "source": item["source"],
                        "outcome": recorded_outcome(lead).pipeline_stage
                        if lead.outcome
                        else "open",
                    }
                )
    sent_words = sum(len(item["text"].split()) for item in outbound)
    questions = sum(item["text"].count("?") for item in outbound)
    recommendations = []
    if not outbound:
        recommendations.append(
            "No representative email is recorded yet. Ask one discovery "
            "question in the first approved follow-up."
        )
    elif not questions:
        recommendations.append(
            "Your recorded emails contain no questions. Ask about the "
            "decision process or the next milestone."
        )
    for label in dict.fromkeys(item["tag"] for item in objections):
        recommendations.append(
            {
                "price": "Clarify the stated budget and expected value before discussing price.",
                "timing": (
                    "Confirm the customer's preferred timing and permission for a later follow-up."
                ),
                "trust": "Ask which evidence or security requirement would resolve the concern.",
                "competitor": "Ask which requirement their current alternative leaves unmet.",
            }[label]
        )
    battlecards = [
        card.model_dump()
        for card in operations_policy(session, lead.workspace_id).battlecards
        if any(
            re.search(r"(?<!\w)" + re.escape(card.competitor) + r"(?!\w)", item["text"], re.I)
            for item in inbound
        )
    ]
    content = {
        "summary": f"{len(items)} recorded touches; {len(objections)} objection mentions.",
        "scorecard": {
            "recorded_outbound_emails": len(outbound),
            "questions": questions,
            "questions_per_100_words": round(100 * questions / sent_words, 2)
            if sent_words
            else None,
            "talk_time_ratio": None,
            "pace": None,
        },
        "recommendations": recommendations[:5],
        "objections": objections[:30],
        "battlecards": battlecards,
        "evidence": items,
        "limitations": "Text-only coaching. Call audio, talk-time and pace are unavailable.",
    }
    return store_insight(session, lead.workspace_id, "coach", content, actor_id, lead_id=lead.id)


def propose_learning(
    session: Session, workspace_id: UUID, actor_id: str, icp: IcpConfig
) -> AgentInsightORM:
    assert_residency(session, workspace_id)
    resolved = resolve_workspace_icp(session, workspace_id, icp)
    leads = list(
        session.scalars(
            select(LeadORM)
            .join(LeadOutcomeORM)
            .where(
                LeadORM.workspace_id == workspace_id,
                LeadOutcomeORM.pipeline_stage.in_(("closed_won", "closed_lost")),
            )
            .order_by(LeadOutcomeORM.updated_at.desc())
            .limit(1000)
        )
    )
    leads = [
        lead
        for lead in leads
        if lead.qualification
        and lead.qualification.icp_configuration_hash == resolved.configuration_hash
    ]
    won = sum(recorded_outcome(lead).pipeline_stage == "closed_won" for lead in leads)
    lost = len(leads) - won
    if won < 10 or lost < 10:
        return store_insight(
            session,
            workspace_id,
            "learning",
            {
                "summary": (
                    "Record at least 10 wins and 10 losses assessed with the "
                    "current ICP before learning."
                ),
                "sample_size": len(leads),
                "wins": won,
                "losses": lost,
                "deltas": [],
                "limitations": "Insufficient comparable outcomes; no weights changed.",
            },
            actor_id,
        )
    proposed = resolved.configuration.model_dump(mode="json")
    deltas, associations = [], []
    for component, weights in proposed["scoring"]["component_points"].items():
        candidates = []
        for key, points in weights.items():
            if points <= 0:
                continue
            present = [
                lead
                for lead in leads
                if any(
                    line.get("key") == key
                    and line.get("component") == component
                    and line.get("points", 0) > 0
                    for line in (lead.qualification.score_breakdown if lead.qualification else [])
                )
            ]
            absent = [lead for lead in leads if lead not in present]
            if len(present) < 5 or len(absent) < 5:
                continue
            rate_present = (
                1 + sum(recorded_outcome(lead).pipeline_stage == "closed_won" for lead in present)
            ) / (2 + len(present))
            rate_absent = (
                1 + sum(recorded_outcome(lead).pipeline_stage == "closed_won" for lead in absent)
            ) / (2 + len(absent))
            effect = rate_present - rate_absent
            candidates.append((effect, key, points))
            associations.append(
                {
                    "component": component,
                    "criterion": key,
                    "present_n": len(present),
                    "absent_n": len(absent),
                    "win_rate_delta": round(effect, 3),
                }
            )
        if len(candidates) >= 2:
            low, high = min(candidates), max(candidates)
            if high[0] - low[0] >= 0.15 and low[2] > 1:
                weights[low[1]] -= 1
                weights[high[1]] += 1
                deltas.extend(
                    [
                        {
                            "component": component,
                            "criterion": low[1],
                            "from": low[2],
                            "to": low[2] - 1,
                        },
                        {
                            "component": component,
                            "criterion": high[1],
                            "from": high[2],
                            "to": high[2] + 1,
                        },
                    ]
                )
    content = {
        "summary": f"{won} wins and {lost} losses; {len(deltas)} proposed weight changes.",
        "sample_size": len(leads),
        "wins": won,
        "losses": lost,
        "deltas": deltas,
        "associations": associations,
        "base_hash": resolved.configuration_hash,
        "proposed_configuration": proposed,
        "limitations": (
            "Associations are not causal. Component totals remain "
            "40/35/25. Review and publish the resulting ICP draft."
        ),
    }
    return store_insight(session, workspace_id, "learning", content, actor_id)


def approve_learning(
    session: Session, row: AgentInsightORM, workspace: WorkspaceContext, icp: IcpConfig
) -> AgentInsightORM:
    if row.status != "pending_review":
        raise ValueError("This suggestion has already been reviewed")
    resolved = resolve_workspace_icp(session, workspace.workspace_id, icp)
    if not row.content.get("deltas") or row.content.get("base_hash") != resolved.configuration_hash:
        raise ValueError("No applicable changes, or the ICP changed. Generate a fresh suggestion.")
    changed = cast(
        CursorResult[Any],
        session.execute(
            update(AgentInsightORM)
            .where(AgentInsightORM.id == row.id, AgentInsightORM.status == "pending_review")
            .values(status="approved")
        ),
    )
    if not changed.rowcount:
        raise ValueError("This suggestion has already been reviewed")
    draft = create_draft(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        workspace.user_id,
        icp,
        name="Outcome-informed scoring",
        description=row.content["summary"],
        configuration=row.content["proposed_configuration"],
        commit=False,
    )
    row.status, row.reviewed_by, row.reviewed_at = "approved", workspace.actor_id, datetime.now(UTC)
    row.content = {**row.content, "draft_id": str(draft.id)}
    session.commit()
    session.refresh(row)
    return row


def prepare_digest(
    session: Session, workspace_id: UUID, user_id: UUID, actor_id: str
) -> AgentInsightORM:
    from lead_agent.infrastructure.db.models import RoutingDecisionORM

    week = (datetime.now(UTC).date() - timedelta(days=datetime.now(UTC).weekday())).isoformat()
    fingerprint = hashlib.sha256(f"digest:{workspace_id}:{user_id}:{week}".encode()).hexdigest()
    previous = session.scalar(
        select(AgentInsightORM).where(
            AgentInsightORM.workspace_id == workspace_id, AgentInsightORM.fingerprint == fingerprint
        )
    )
    if previous:
        return previous
    leads = list(
        session.scalars(
            select(LeadORM)
            .join(RoutingDecisionORM)
            .where(
                LeadORM.workspace_id == workspace_id,
                RoutingDecisionORM.is_current.is_(True),
                RoutingDecisionORM.assigned_rep_id == str(user_id),
            )
            .order_by(LeadORM.updated_at.desc())
            .limit(5)
        )
    )
    observations = []
    for lead in leads:
        report = coach_lead(session, lead, actor_id)
        for recommendation in report.content["recommendations"]:
            observations.append(
                {
                    "recommendation": recommendation,
                    "lead_id": str(lead.id),
                    "evidence": report.content["evidence"][:2],
                }
            )
    unique = list({item["recommendation"]: item for item in observations}.values())[:3]
    return store_insight(
        session,
        workspace_id,
        "digest",
        {
            "summary": f"Weekly coaching digest: {len(unique)} evidence-backed suggestions.",
            "recipient_id": str(user_id),
            "week": week,
            "suggestions": unique,
            "limitations": (
                "Manager review is required before this digest is visible to the representative."
            ),
        },
        actor_id,
        fingerprint=fingerprint,
    )


def recorded_outcome(lead: LeadORM) -> LeadOutcomeORM:
    if lead.outcome is None:
        raise ValueError("This lead has no recorded outcome")
    return lead.outcome
