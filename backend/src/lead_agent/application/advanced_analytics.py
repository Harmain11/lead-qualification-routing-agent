"""Recorded-value forecasts, cost attribution, cohorts and transparent rep comparisons."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from math import fsum
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from lead_agent.application.coaching_privacy import personal_data_user_ids
from lead_agent.application.commercial import pipeline_value_usd
from lead_agent.application.governance import assert_residency, operations_policy
from lead_agent.application.territories import matches_territory
from lead_agent.domain.lead_sources import lead_source_category
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.db.deals import CommercialSettingsORM, DealORM
from lead_agent.infrastructure.db.models import (
    LeadORM,
    LeadOutcomeORM,
    RoutingDecisionORM,
    UserORM,
    WorkspaceMembershipORM,
)
from lead_agent.infrastructure.reasoning.factory import resolve_workspace_reasoning_provider


def advanced_analytics(
    session: Session, workspace_id: UUID, months: int = 6, narrate: bool = False,
    *, workspace: WorkspaceContext | None = None,
) -> dict[str, Any]:
    if workspace is not None and workspace.workspace_id != workspace_id:
        raise ValueError("Analytics context belongs to another workspace")
    personal_users = personal_data_user_ids(session, workspace) if workspace is not None else None
    assert_residency(session, workspace_id)
    now = datetime.now(UTC)
    since = now - timedelta(days=months * 31)
    leads = list(
        session.scalars(
            select(LeadORM)
            .where(LeadORM.workspace_id == workspace_id, LeadORM.received_at >= since)
            .order_by(LeadORM.received_at.desc())
            .limit(5000)
        )
    )
    config = operations_policy(session, workspace_id)
    closed = [
        lead
        for lead in leads
        if lead.outcome and recorded_outcome(lead).pipeline_stage in {"closed_won", "closed_lost"}
    ]
    won = [lead for lead in closed if recorded_outcome(lead).pipeline_stage == "closed_won"]
    probability = len(won) / len(closed) if len(closed) >= 20 else None
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    usd_won = float(
        session.scalar(
            select(func.sum(LeadOutcomeORM.closed_revenue))
            .join(LeadORM, LeadORM.id == LeadOutcomeORM.lead_id)
            .where(
                LeadORM.workspace_id == workspace_id,
                LeadOutcomeORM.pipeline_stage == "closed_won",
                LeadOutcomeORM.currency == "USD",
                LeadOutcomeORM.closed_at >= month_start,
                LeadOutcomeORM.closed_at <= now,
            )
        )
        or 0
    )
    deals = {row.lead_id: row.data for row in session.scalars(select(DealORM).where(DealORM.workspace_id == workspace_id))}
    current_leads = session.scalars(select(LeadORM).where(LeadORM.workspace_id == workspace_id).options(selectinload(LeadORM.outcome)))
    values = [pipeline_value_usd(lead, deals.get(lead.id)) for lead in current_leads if not lead.outcome or lead.outcome.pipeline_stage not in {"closed_won", "closed_lost"}]
    pipeline = fsum(values)
    unvalued = sum(value <= 0 for value in values)
    expected = pipeline * probability if probability is not None else None
    commercial_settings = session.get(CommercialSettingsORM, workspace_id)
    shared_goal = commercial_settings.data.get("monthlyGoal") if commercial_settings else None
    quota = shared_goal if shared_goal is not None else config.monthly_quota_usd
    forecast = {
        "closed_usd_this_month": round(usd_won, 2),
        "open_pipeline_usd": round(pipeline, 2),
        "historical_close_rate": round(100 * probability, 1) if probability is not None else None,
        "expected_open_pipeline_usd": round(expected, 2) if expected is not None else None,
        "quota_usd": quota,
        "coverage": round(pipeline / quota, 2) if quota else None,
        "closed_sample_size": len(closed),
        "method": (
            "Observed close rate applied to recorded USD opportunity "
            "values; not a prediction of closing dates."
        ),
        "unvalued_open_leads": unvalued,
        "scope": (
            "Current workspace pipeline and this month's closed revenue; "
            "close rate uses the selected intake cohort."
        ),
    }
    cost_lookup = {(cost.source, cost.month): cost.cost_usd for cost in config.channel_costs}
    channel_rows = defaultdict(list)
    cohorts = defaultdict(list)
    for lead in leads:
        channel_rows[(lead_source_category(lead.source, lead.source_detail), lead.received_at.strftime("%Y-%m"))].append(lead)
        week = lead.received_at.date() - timedelta(days=lead.received_at.weekday())
        campaign = str(
            lead.raw_payload.get("campaign")
            or lead.raw_payload.get("utm_campaign")
            or "Unattributed"
        )[:100]
        cohorts[(week.isoformat(), campaign)].append(lead)

    def cohort_metrics(rows: list[LeadORM]) -> Any:
        return {
            "leads": len(rows),
            "qualified": sum(
                bool(
                    lead.qualification
                    and lead.qualification.classification in {"hot", "warm", "needs_qualifying"}
                )
                for lead in rows
            ),
            "won": sum(
                bool(lead.outcome and recorded_outcome(lead).pipeline_stage == "closed_won")
                for lead in rows
            ),
            "lost": sum(
                bool(lead.outcome and recorded_outcome(lead).pipeline_stage == "closed_lost")
                for lead in rows
            ),
        }

    channels = []
    for key in cost_lookup:
        if since.strftime("%Y-%m") <= key[1] <= now.strftime("%Y-%m"):
            channel_rows[key]
    for (source, month), rows in sorted(channel_rows.items(), reverse=True):
        metrics = cohort_metrics(rows)
        cost = cost_lookup.get((source, month))
        revenue = sum(
            float(recorded_outcome(lead).closed_revenue or 0)
            for lead in rows
            if lead.outcome
            and recorded_outcome(lead).pipeline_stage == "closed_won"
            and recorded_outcome(lead).currency == "USD"
        )
        channels.append(
            {
                "source": source,
                "month": month,
                **metrics,
                "cost_usd": cost,
                "closed_usd": round(revenue, 2),
                "cost_per_qualified": round(cost / metrics["qualified"], 2)
                if cost is not None and metrics["qualified"]
                else None,
                "roi_percent": round(100 * (revenue - cost) / cost, 1) if cost else None,
                "method": (
                    "Revenue attributed to intake source and intake-month "
                    "cohort, not payment month."
                ),
            }
        )
    bucket_outcomes = defaultdict(list)

    def bucket(lead: LeadORM) -> Any:
        group = next(
            (item.name for item in config.territories if matches_territory(lead, item)),
            lead.geography or "Unknown region",
        )
        return group, (lead.qualification.total_score // 20 if lead.qualification else -1)

    for lead in closed:
        bucket_outcomes[bucket(lead)].append(recorded_outcome(lead).pipeline_stage == "closed_won")
    routes = list(
        session.scalars(
            select(RoutingDecisionORM).where(
                RoutingDecisionORM.workspace_id == workspace_id,
                RoutingDecisionORM.is_current.is_(True),
            )
        )
    )
    assigned = {route.lead_id: route.assigned_rep_id for route in routes}
    member_rows = session.execute(
        select(WorkspaceMembershipORM.user_id, UserORM.display_name)
        .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
        .where(
            WorkspaceMembershipORM.workspace_id == workspace_id,
            WorkspaceMembershipORM.status == "active",
            *([] if personal_users is None else [
                UserORM.id.in_([UUID(value) for value in personal_users])
            ]),
        )
    ).all()
    reps = []
    for user_id, name in member_rows:
        rows = [
            lead
            for lead in closed
            if str(recorded_outcome(lead).credited_user_id or assigned.get(lead.id)) == str(user_id)
        ]
        covered = [lead for lead in rows if len(bucket_outcomes[bucket(lead)]) >= 5]
        expected_wins = sum(
            (1 + sum(bucket_outcomes[bucket(lead)])) / (2 + len(bucket_outcomes[bucket(lead)]))
            for lead in covered
        )
        actual_wins = sum(recorded_outcome(lead).pipeline_stage == "closed_won" for lead in covered)
        reps.append(
            {
                "user_id": user_id,
                "name": name,
                "closed": len(rows),
                "wins": sum(recorded_outcome(lead).pipeline_stage == "closed_won" for lead in rows),
                "comparable_leads": len(covered),
                "expected_wins": round(expected_wins, 2),
                "adjusted_win_index": round(actual_wins / expected_wins, 2)
                if len(covered) >= 10 and expected_wins
                else None,
            }
        )
    summary = f"Recorded USD pipeline is {pipeline:,.2f}; {usd_won:,.2f} USD closed this month."
    if quota:
        summary += f" Pipeline coverage is {pipeline / quota:.2f}x the configured monthly quota."
    if probability is None:
        summary += " Fewer than 20 closed outcomes; an expected-value forecast is withheld."
    result = {
        "forecast": forecast,
        "channels": channels,
        "cohorts": [
            {"week": key[0], "campaign": key[1], **cohort_metrics(rows)}
            for key, rows in sorted(cohorts.items(), reverse=True)
        ],
        "members": reps,
        "summary": summary,
        "summary_method": "Recorded metrics",
        "fairness_method": (
            "Observed/expected wins within region and 20-point score buckets. "
            "At least 5 closed outcomes per bucket and 10 comparable leads per rep. "
            "Observational, not a performance verdict."
        ),
        "sample_size": len(leads),
        "truncated": len(leads) == 5000,
    }
    if narrate:
        provider = resolve_workspace_reasoning_provider(session, workspace_id)
        summarize = getattr(provider, "summarize_evidence", None)
        if not callable(summarize):
            raise ValueError("Connect and verify OpenAI for forecast narration")
        brief = summarize(
            {"evidence": [{"source": "recorded_metrics", "text": summary}], "metrics": forecast},
            "forecast commentary",
        )
        result["summary"], result["summary_method"] = (
            brief["summary"],
            "OpenAI, based on recorded metrics",
        )
    return result


def recorded_outcome(lead: LeadORM) -> LeadOutcomeORM:
    if lead.outcome is None:
        raise ValueError("This lead has no recorded outcome")
    return lead.outcome
