from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import fsum
from statistics import median
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from lead_agent.application.manual_review import lead_manual_review_reasons
from lead_agent.infrastructure.db.models import (
    AgentToolAuditORM,
    LeadFollowUpEmailORM,
    LeadORM,
    LeadOutcomeORM,
    PipelineEventORM,
    ProviderConnectionORM,
    QualificationResultORM,
    UserORM,
    WorkspaceMembershipORM,
    WorkspaceORM,
)

RangeKey = Literal["weekly", "monthly", "annual"]
SQL_BANDS = {"hot", "warm"}
QUALIFIED_BANDS = {"hot", "warm", "needs_qualifying"}
ACTIVE_OPPORTUNITY_STAGES = {"opportunity", "proposal", "negotiation"}
CLOSED_STAGES = {"closed_won", "closed_lost"}
SALES_PIPELINE_STAGES = (
    ("new_lead", "New lead"),
    ("contacted", "Contacted"),
    ("follow_up_drafted", "Follow-up drafted"),
    ("follow_up_sent", "Follow-up sent"),
    ("responded", "Responded"),
    ("meeting_scheduled", "Meeting scheduled"),
    ("opportunity", "Opportunity"),
    ("proposal_sent", "Proposal sent"),
    ("negotiation", "Negotiation"),
    ("closed_won", "Converted → Project or Sale"),
    ("closed_lost", "Closed / lost"),
)
ENRICHMENT_FIELDS = (
    ("company_name", "Company"),
    ("job_title", "Job title"),
    ("phone", "Phone"),
    ("industry", "Industry"),
    ("employee_count", "Company size"),
    ("annual_revenue", "Annual revenue"),
    ("geography", "Geography"),
)
MISSING_ENRICHMENT_VALUES = {"", "unknown", "unknown company", "n/a", "not provided", "unspecified"}


@dataclass(frozen=True)
class AnalyticsWindow:
    key: RangeKey
    start: datetime
    end: datetime
    previous_start: datetime
    previous_end: datetime

    @classmethod
    def build(cls, key: RangeKey, now: datetime | None = None) -> AnalyticsWindow:
        end = now or datetime.now(UTC)
        days = {"weekly": 7, "monthly": 30, "annual": 365}[key]
        start = end - timedelta(days=days)
        return cls(
            key=key,
            start=start,
            end=end,
            previous_start=start - timedelta(days=days),
            previous_end=start,
        )


def _safe_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


def _latest_route(lead: LeadORM) -> Any:
    current = [item for item in lead.routing_decisions if item.is_current]
    return max(current, key=lambda item: item.created_at, default=None)


def _latest_crm(lead: LeadORM) -> Any:
    current = [item for item in lead.crm_syncs if item.is_current]
    return max(current, key=lambda item: item.requested_at, default=None)


def _money(value: Any) -> float:
    return round(float(value or 0), 2)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _qualification(lead: LeadORM) -> QualificationResultORM:
    if lead.qualification is None:
        raise ValueError(f"Lead {lead.id} has no qualification result")
    return lead.qualification


def _outcome(lead: LeadORM) -> LeadOutcomeORM:
    if lead.outcome is None:
        raise ValueError(f"Lead {lead.id} has no outcome fact")
    return lead.outcome


def _rep_name(lead: LeadORM) -> str:
    if lead.outcome and lead.outcome.credited_user:
        return lead.outcome.credited_user.display_name
    route = _latest_route(lead)
    return (
        route.assigned_rep_name if route is not None and route.assigned_rep_name else "Unassigned"
    )


def _enrichment_value_is_missing(value: Any) -> bool:
    return value is None or (
        isinstance(value, str) and value.strip().casefold() in MISSING_ENRICHMENT_VALUES
    )


def _enrichment_value_key(value: Any) -> str:
    return str(value).strip().casefold()


class AnalyticsService:
    """Workspace-scoped analytics read service.

    The service deliberately owns business metric calculations. Frontends receive
    display-ready metrics and never aggregate cross-tenant records themselves.
    """

    def __init__(self, session: Session, workspace_id: UUID) -> None:
        self.session = session
        self.workspace_id = workspace_id

    def metadata(self, window: AnalyticsWindow) -> dict[str, Any]:
        workspace = self._workspace()
        return {
            "generated_at": datetime.now(UTC),
            "range": window.key,
            "range_start": window.start,
            "range_end": window.end,
            "workspace_id": workspace.id,
            "workspace_name": workspace.name,
            "data_mode": "demo" if workspace.is_demo else "live",
        }

    def dashboard(self, range_key: RangeKey = "monthly") -> dict[str, Any]:
        window = AnalyticsWindow.build(range_key)
        leads = self._leads(window.start, window.end)
        previous = self._leads(window.previous_start, window.previous_end)
        current_values = self._summary_values(leads)
        previous_values = self._summary_values(previous)
        metrics = []
        labels = {
            "total_leads": "Total leads",
            "sqls": "Qualified leads / SQLs",
            "qualification_rate": "Qualification rate",
            "hot_leads": "Hot leads",
            "active_pipeline_value": "Active pipeline",
            "closed_won_revenue": "Closed / won revenue",
            "average_score": "Average qualification score",
            "average_routing_velocity_minutes": "Average routing velocity",
            "manual_review": "Manual review",
            "integration_attention": "Integration attention",
        }
        for key, label in labels.items():
            current = float(current_values[key])
            previous_value = float(previous_values[key])
            metrics.append(
                {
                    "key": key,
                    "label": label,
                    "value": current_values[key],
                    "change_percent": _safe_change(current, previous_value),
                    "previous_value": previous_values[key] if previous else None,
                }
            )
        return {
            "metadata": self.metadata(window),
            "metrics": metrics,
            "band_distribution": self._band_counts(leads),
            "active_pipeline_by_origin": self._active_pipeline_by_origin(leads),
            "attention_leads": self._attention_leads(leads)[:6],
            "recent_leads": [self._lead_card(lead) for lead in leads[:8]],
        }

    def channels(self, range_key: RangeKey = "monthly") -> dict[str, Any]:
        window = AnalyticsWindow.build(range_key)
        leads = self._leads(window.start, window.end)
        grouped: dict[str, list[LeadORM]] = defaultdict(list)
        for lead in leads:
            grouped[lead.source].append(lead)
        lead_ids = select(LeadORM.id).where(
            LeadORM.workspace_id == self.workspace_id,
            LeadORM.received_at >= window.start,
            LeadORM.received_at <= window.end,
        )
        meeting_events = self.session.scalars(
            select(PipelineEventORM).where(
                PipelineEventORM.workspace_id == self.workspace_id,
                PipelineEventORM.lead_id.in_(lead_ids),
                (
                    (PipelineEventORM.stage == "meeting_scheduled")
                    | (
                        (PipelineEventORM.stage == "manual_progress")
                        & (PipelineEventORM.status == "meeting_scheduled")
                    )
                ),
                PipelineEventORM.created_at >= window.start,
                PipelineEventORM.created_at <= window.end,
            )
        ).all() if leads else []
        booked_ids = {event.lead_id for event in meeting_events}
        total = len(leads)
        rows = []
        for channel, items in sorted(grouped.items(), key=lambda row: len(row[1]), reverse=True):
            qualified = len(
                [lead for lead in items if _qualification(lead).classification in QUALIFIED_BANDS]
            )
            sqls = len([lead for lead in items if _qualification(lead).classification in SQL_BANDS])
            won = len(
                [
                    lead
                    for lead in items
                    if lead.outcome is not None and lead.outcome.pipeline_stage == "closed_won"
                ]
            )
            revenue = fsum(
                _money(_outcome(lead).closed_revenue) for lead in items if lead.outcome is not None
            )
            scored = [lead for lead in items if lead.qualification is not None]
            average_score = (
                round(fsum(_qualification(lead).total_score for lead in scored) / len(scored), 1)
                if scored else 0
            )
            quality = average_score / 100
            volume = len(items) / total if total else 0
            sql_rate = sqls / len(items) if items else 0
            importance = round((quality * 0.5 + volume * 0.25 + sql_rate * 0.25) * 100, 1)
            booked = sum(lead.id in booked_ids for lead in items)
            rows.append(
                {
                    "channel": channel,
                    "lead_volume": len(items),
                    "qualified_leads": qualified,
                    "booked_leads": booked,
                    "booked_rate": round((booked / len(items)) * 100, 1) if items else 0,
                    "qualified_to_booked_rate": (
                        round((booked / qualified) * 100, 1) if qualified else None
                    ),
                    "closed_won_count": won,
                    "scored_leads": len(scored),
                    "share_percent": round((len(items) / total) * 100, 1) if total else 0,
                    "qualification_rate": round((qualified / len(items)) * 100, 1),
                    "sql_conversion_rate": round((sqls / len(items)) * 100, 1),
                    "closed_won_conversion_rate": round((won / len(items)) * 100, 1),
                    "revenue": round(revenue, 2),
                    "average_score": average_score,
                    "importance_index": importance,
                }
            )
        return {"metadata": self.metadata(window), "channels": rows}

    def pipeline(self, range_key: RangeKey = "monthly") -> dict[str, Any]:
        window = AnalyticsWindow.build(range_key)
        leads = self._leads(window.start, window.end)
        stage_members: dict[str, list[LeadORM]] = {
            "received": leads,
            "qualified": [
                lead for lead in leads if _qualification(lead).classification in QUALIFIED_BANDS
            ],
            "routed": [lead for lead in leads if _latest_route(lead) is not None],
            "opportunity": [
                lead
                for lead in leads
                if lead.outcome is not None
                and lead.outcome.pipeline_stage in ACTIVE_OPPORTUNITY_STAGES
            ],
            "closed": [
                lead
                for lead in leads
                if lead.outcome is not None and lead.outcome.pipeline_stage in CLOSED_STAGES
            ],
        }
        stages = [
            {
                "stage": stage,
                "label": stage.replace("_", " ").title(),
                "count": len(items),
                "value": round(
                    fsum(
                        _money(_outcome(item).opportunity_value)
                        for item in items
                        if item.outcome is not None
                    ),
                    2,
                ),
                "leads": [self._lead_card(item) for item in items[:12]],
            }
            for stage, items in stage_members.items()
        ]
        return {
            "metadata": self.metadata(window),
            "stages": stages,
            "sales_stages": self._sales_pipeline_stages(leads),
            "bands": self._band_counts(leads),
            "manual_review": len([lead for lead in leads if self._is_manual_review(lead)]),
        }

    def _sales_pipeline_stages(self, leads: list[LeadORM]) -> list[dict[str, Any]]:
        lead_ids = [lead.id for lead in leads]
        current_stage = {lead.id: "new_lead" for lead in leads}
        stage_rank = {stage: index for index, (stage, _) in enumerate(SALES_PIPELINE_STAGES)}
        terminal_stages = {"closed_won", "closed_lost"}
        for terminal in terminal_stages:
            stage_rank[terminal] = len(SALES_PIPELINE_STAGES)

        def advance(lead_id: UUID, candidate: str | None) -> None:
            if candidate not in stage_rank:
                return
            current = current_stage[lead_id]
            if current in terminal_stages:
                return
            if candidate in terminal_stages or stage_rank[candidate] > stage_rank[current]:
                current_stage[lead_id] = candidate

        if lead_ids:
            progress_events = self.session.scalars(
                select(PipelineEventORM)
                .where(
                    PipelineEventORM.workspace_id == self.workspace_id,
                    PipelineEventORM.lead_id.in_(lead_ids),
                    PipelineEventORM.stage.in_(("manual_progress", "meeting_scheduled")),
                )
                .order_by(PipelineEventORM.created_at.asc())
            )
            manual_stages = {
                "contacted": "contacted",
                "responded": "responded",
                "meeting_scheduled": "meeting_scheduled",
                "proposal_sent": "proposal_sent",
            }
            for event in progress_events:
                raw_stage = (
                    "meeting_scheduled"
                    if event.stage == "meeting_scheduled"
                    else str(event.details.get("stage") or event.status)
                )
                advance(event.lead_id, manual_stages.get(raw_stage))

            follow_ups = self.session.scalars(
                select(LeadFollowUpEmailORM).where(
                    LeadFollowUpEmailORM.workspace_id == self.workspace_id,
                    LeadFollowUpEmailORM.lead_id.in_(lead_ids),
                    LeadFollowUpEmailORM.kind.like("ai_follow_up%"),
                )
            )
            for follow_up in follow_ups:
                if follow_up.status == "sent":
                    advance(follow_up.lead_id, "follow_up_sent")
                elif follow_up.status in {"draft", "sending", "delivery_uncertain"}:
                    advance(follow_up.lead_id, "follow_up_drafted")

            outcome_stages = {
                "opportunity": "opportunity",
                "proposal": "proposal_sent",
                "negotiation": "negotiation",
                "closed_won": "closed_won",
                "closed_lost": "closed_lost",
            }
            for lead in leads:
                if lead.outcome is not None:
                    outcome_stage = outcome_stages.get(lead.outcome.pipeline_stage)
                    if outcome_stage in terminal_stages:
                        current_stage[lead.id] = outcome_stage
                    else:
                        advance(lead.id, outcome_stage)

        stage_members: dict[str, list[LeadORM]] = {
            stage: [] for stage, _ in SALES_PIPELINE_STAGES
        }
        for lead in leads:
            stage_members[current_stage[lead.id]].append(lead)

        return [
            {
                "stage": stage,
                "label": label,
                "count": len(items),
                "value": None,
                "leads": [self._lead_card(item) for item in items[:12]],
            }
            for stage, label in SALES_PIPELINE_STAGES
            for items in [stage_members[stage]]
        ]

    def revenue(self, range_key: RangeKey = "monthly") -> dict[str, Any]:
        window = AnalyticsWindow.build(range_key)
        leads = self._leads(window.start, window.end)
        facts = [lead for lead in leads if lead.outcome is not None]
        total_revenue = fsum(_money(_outcome(lead).closed_revenue) for lead in facts)
        pipeline_value = fsum(
            _money(_outcome(lead).opportunity_value)
            for lead in facts
            if _outcome(lead).pipeline_stage in ACTIVE_OPPORTUNITY_STAGES
        )
        source_types = {_outcome(lead).data_source for lead in facts}
        if not facts:
            status = "unavailable"
        elif source_types == {"demo"}:
            status = "demo"
        elif source_types == {"crm"}:
            status = "crm"
        elif source_types == {"manual"}:
            status = "manual"
        else:
            status = "mixed"
        return {
            "metadata": self.metadata(window),
            "data_status": status,
            "currency": "USD",
            "total_revenue": round(total_revenue, 2),
            "pipeline_value": round(pipeline_value, 2),
            "by_channel": self._revenue_group(facts, lambda lead: lead.source),
            "by_rep": self._revenue_group(facts, _rep_name),
            "by_service": self._revenue_group(facts, lambda lead: lead.product_or_service),
            "over_time": self._revenue_time_series(facts, window),
        }

    def reps(self, range_key: RangeKey = "monthly") -> dict[str, Any]:
        window = AnalyticsWindow.build(range_key)
        leads = self._leads(window.start, window.end)
        grouped: dict[str, list[LeadORM]] = defaultdict(list)
        for lead in leads:
            route = _latest_route(lead)
            if route is not None and route.assigned_rep_name:
                grouped[route.assigned_rep_name].append(lead)
        rows = []
        for name, items in sorted(grouped.items()):
            qualified = len(
                [lead for lead in items if _qualification(lead).classification in QUALIFIED_BANDS]
            )
            closed = len(
                [
                    lead
                    for lead in items
                    if lead.outcome is not None and lead.outcome.pipeline_stage == "closed_won"
                ]
            )
            velocities = self._routing_velocities(items)
            rows.append(
                {
                    "rep_name": name,
                    "assigned_leads": len(items),
                    "qualified_leads": qualified,
                    "closed_leads": closed,
                    "conversion_rate": round((closed / len(items)) * 100, 1),
                    "revenue": round(
                        fsum(
                            _money(_outcome(lead).closed_revenue)
                            for lead in items
                            if lead.outcome is not None
                        ),
                        2,
                    ),
                    "average_response_minutes": (
                        round(sum(velocities) / len(velocities), 1) if velocities else None
                    ),
                }
            )
        roster = self.session.execute(
            select(WorkspaceMembershipORM, UserORM)
            .join(UserORM, UserORM.id == WorkspaceMembershipORM.user_id)
            .where(WorkspaceMembershipORM.workspace_id == self.workspace_id)
            .order_by(UserORM.display_name, UserORM.id)
        ).all()
        identities = {str(user.id): (member, user) for member, user in roster}
        by_member: dict[str, list[LeadORM]] = defaultdict(list)
        unattributed = 0
        for lead in leads:
            route = _latest_route(lead)
            owner = (
                str(lead.outcome.credited_user_id)
                if lead.outcome and lead.outcome.credited_user_id
                else route.assigned_rep_id if route else None
            )
            if owner in identities:
                by_member[owner].append(lead)
            else:
                unattributed += 1
        members = []
        for user_id, (member, user) in identities.items():
            items = by_member[user_id]
            if member.status != "active" and not items:
                continue
            won = [
                lead for lead in items
                if lead.outcome and lead.outcome.pipeline_stage == "closed_won"
            ]
            lost = sum(
                bool(lead.outcome and lead.outcome.pipeline_stage == "closed_lost")
                for lead in items
            )
            projects = sum(lead.outcome.conversion_type == "project" for lead in won)
            sales = sum(lead.outcome.conversion_type == "sale" for lead in won)
            qualified = sum(
                _qualification(lead).classification in QUALIFIED_BANDS for lead in items
            )
            conversion_rate = round(len(won) / len(items) * 100, 1) if items else 0
            revenue = round(fsum(_money(lead.outcome.closed_revenue) for lead in won), 2)
            velocities = self._routing_velocities(items)
            members.append({
                "user_id": user_id, "display_name": user.display_name,
                "role": member.role, "status": member.status,
                "assigned_leads": len(items), "qualified_leads": qualified,
                "closed_leads": len(won), "project_wins": projects, "sale_wins": sales,
                "untyped_wins": len(won) - projects - sales, "lost_leads": lost,
                "active_leads": len(items) - len(won) - lost,
                "conversion_rate": conversion_rate, "revenue": revenue,
                "wins_without_value": sum(lead.outcome.closed_revenue is None for lead in won),
                "average_response_minutes": (
                    round(sum(velocities) / len(velocities), 1) if velocities else None
                ),
                "summary": (
                    f"{user.display_name} has {len(items)} assigned lead(s) in this period, "
                    f"{qualified} qualified lead(s), and {len(won)} win(s): "
                    f"{projects} Project(s), {sales} Sale(s)"
                    + (
                        f", {len(won) - projects - sales} earlier untyped win(s)"
                        if len(won) > projects + sales else ""
                    )
                    + f". {len(items) - len(won) - lost} remain open and {lost} were lost. "
                    f"Assigned-lead conversion is {conversion_rate:g}%."
                ),
            })
        return {
            "metadata": self.metadata(window), "reps": rows, "members": members,
            "unattributed_leads": unattributed,
            "cohort_description": (
                "Includes leads received in the selected period and their current outcomes. "
                "Closed leads use recorded member credit; open leads use their current assigned "
                "member. "
                "Conversion rate is wins divided by assigned leads."
            ),
        }

    def time_series(self, range_key: RangeKey = "monthly") -> dict[str, Any]:
        window = AnalyticsWindow.build(range_key)
        leads = self._leads(window.start, window.end)
        format_key = "%Y-%m" if range_key == "annual" else "%Y-%m-%d"
        grouped: dict[str, list[LeadORM]] = defaultdict(list)
        for lead in leads:
            grouped[lead.received_at.strftime(format_key)].append(lead)
        points = []
        for period, items in sorted(grouped.items()):
            points.append(
                {
                    "period": period,
                    "leads": len(items),
                    "sqls": len(
                        [lead for lead in items if _qualification(lead).classification in SQL_BANDS]
                    ),
                    "average_score": round(
                        fsum(_qualification(lead).total_score for lead in items) / len(items), 1
                    ),
                    "revenue": round(
                        fsum(
                            _money(_outcome(lead).closed_revenue)
                            for lead in items
                            if lead.outcome is not None
                        ),
                        2,
                    ),
                }
            )
        return {"metadata": self.metadata(window), "points": points}

    def agent_activity(self, range_key: RangeKey = "monthly") -> dict[str, Any]:
        window = AnalyticsWindow.build(range_key)
        leads = self._leads(window.start, window.end)
        previous_leads = self._leads(window.previous_start, window.previous_end)
        events = list(
            self.session.scalars(
                select(PipelineEventORM)
                .where(
                    PipelineEventORM.workspace_id == self.workspace_id,
                    PipelineEventORM.created_at >= window.start,
                    PipelineEventORM.created_at <= window.end,
                )
                .order_by(PipelineEventORM.created_at.desc())
                .limit(100)
            )
        )
        tools = list(
            self.session.scalars(
                select(AgentToolAuditORM)
                .where(
                    AgentToolAuditORM.workspace_id == self.workspace_id,
                    AgentToolAuditORM.created_at >= window.start,
                    AgentToolAuditORM.created_at <= window.end,
                )
                .order_by(AgentToolAuditORM.created_at.desc())
                .limit(50)
            )
        )
        stage_agents = {
            "received": "Intake Agent",
            "normalized": "Normalization",
            "analyzed": "Context / Intent Agent",
            "qualified": "Qualification Agent",
            "routed": "Routing Agent",
            "notified": "Notification Tool",
            "crm": "CRM Tool",
            "coach": "Coach Agent",
            "watcher": "Watcher Agent",
            "research": "Researcher Agent",
            "automation": "Playbook Agent",
        }
        activity: list[dict[str, Any]] = [
            {
                "id": str(event.id),
                "lead_id": str(event.lead_id),
                "agent": stage_agents.get(event.stage, event.stage.replace("_", " ").title()),
                "stage": event.stage,
                "status": event.status,
                "provider": event.details.get("provider"),
                "structured_result": event.details,
                "tool_name": None,
                "duration_ms": event.details.get("duration_ms"),
                "error": event.details.get("error"),
                "created_at": event.created_at,
            }
            for event in events
        ]
        activity.extend(
            {
                "id": str(tool.id),
                "lead_id": tool.input_summary.get("lead_id"),
                "agent": "Agent Tool",
                "stage": "tool",
                "status": tool.status,
                "provider": None,
                "structured_result": tool.output,
                "tool_name": tool.tool_name,
                "duration_ms": None,
                "error": tool.error_message,
                "created_at": tool.created_at,
            }
            for tool in tools
        )
        activity.sort(key=lambda row: row["created_at"], reverse=True)
        capabilities = [
            {"name": "Intake Agent", "status": "implemented"},
            {"name": "Context / Intent Agent", "status": "implemented"},
            {"name": "Qualification Agent", "status": "implemented"},
            {"name": "Routing Agent", "status": "implemented"},
            {"name": "Action Planning Agent", "status": "implemented"},
            {"name": "CRM Tool", "status": "implemented"},
            {"name": "Notification Tool", "status": "implemented"},
            {"name": "Follow-up Agent", "status": "implemented"},
            {"name": "Enrichment Agent", "status": "implemented"},
            {"name": "Analytics Agent", "status": "implemented"},
            {"name": "Researcher Agent", "status": "implemented"},
            {"name": "Coach Agent", "status": "implemented"},
            {"name": "Watcher Agent", "status": "implemented"},
            {"name": "Playbook Agent", "status": "implemented"},
        ]
        return {
            "metadata": self.metadata(window),
            "capabilities": capabilities,
            "insights": [
                self._enrichment_agent_insight(leads),
                self._analytics_agent_insight(leads, previous_leads),
            ],
            "activity": activity[:100],
        }

    @staticmethod
    def _enrichment_agent_insight(leads: list[LeadORM]) -> dict[str, Any]:
        missing_by_field: Counter[str] = Counter()
        leads_by_email: dict[str, list[LeadORM]] = defaultdict(list)
        for lead in leads:
            email_key = lead.email.strip().casefold()
            if email_key:
                leads_by_email[email_key].append(lead)
            for field, label in ENRICHMENT_FIELDS:
                if _enrichment_value_is_missing(getattr(lead, field)):
                    missing_by_field[label] += 1

        exact_email_suggestions = 0
        for matches in leads_by_email.values():
            if len(matches) < 2:
                continue
            for lead in matches:
                for field, _ in ENRICHMENT_FIELDS:
                    if not _enrichment_value_is_missing(getattr(lead, field)):
                        continue
                    available_values = {
                        _enrichment_value_key(getattr(match, field))
                        for match in matches
                        if match.id != lead.id
                        and not _enrichment_value_is_missing(getattr(match, field))
                    }
                    if len(available_values) == 1:
                        exact_email_suggestions += 1

        missing_fields = sum(missing_by_field.values())
        details = [
            f"{label}: missing from {count} lead{'s' if count != 1 else ''}."
            for label, count in missing_by_field.most_common(3)
        ]
        if exact_email_suggestions:
            details.append(
                "Suggestions use a single unambiguous value from an exact-email match "
                "in this workspace and date range."
            )
        else:
            details.append(
                "No unambiguous exact-email field matches were found in this workspace "
                "and date range."
            )
        details.append(
            "Includes CRM-imported profile fields already stored on workspace leads; "
            "this read-only scan calls no provider and changes no records."
        )

        if leads:
            summary = (
                f"Reviewed {len(leads)} leads and found {missing_fields} missing profile "
                f"fields. {exact_email_suggestions} values can be suggested from exact-email "
                "matches in this workspace and date range."
            )
        else:
            summary = (
                "No leads are available in this date range; "
                "there are no profile fields to scan."
            )

        return {
            "agent": "Enrichment Agent",
            "summary": summary,
            "metrics": [
                {"key": "leads_reviewed", "label": "Leads reviewed", "value": len(leads)},
                {"key": "profile_gaps", "label": "Profile gaps", "value": missing_fields},
                {
                    "key": "exact_email_suggestions",
                    "label": "Exact-email suggestions",
                    "value": exact_email_suggestions,
                },
            ],
            "details": details,
        }

    def _analytics_agent_insight(
        self, leads: list[LeadORM], previous_leads: list[LeadORM]
    ) -> dict[str, Any]:
        sql_ready = sum(_qualification(lead).classification in SQL_BANDS for lead in leads)
        manual_review = sum(self._is_manual_review(lead) for lead in leads)
        volume_change = _safe_change(float(len(leads)), float(len(previous_leads)))

        source_leads: dict[str, list[LeadORM]] = defaultdict(list)
        for lead in leads:
            source_leads[lead.source].append(lead)
        comparable_sources = [
            (source, items)
            for source, items in source_leads.items()
            if len(items) >= 3
        ]
        comparable_sources.sort(
            key=lambda row: (
                sum(_qualification(lead).classification in SQL_BANDS for lead in row[1])
                / len(row[1]),
                len(row[1]),
                row[0].casefold(),
            ),
            reverse=True,
        )
        details = []
        if comparable_sources:
            source, items = comparable_sources[0]
            source_sqls = sum(
                _qualification(lead).classification in SQL_BANDS for lead in items
            )
            source_rate = round((source_sqls / len(items)) * 100, 1)
            details.append(
                f"Highest SQL-ready share: {source.replace('_', ' ').title()} "
                f"({source_sqls}/{len(items)}, {source_rate:.1f}%)."
            )
        else:
            details.append(
                "Source comparisons are withheld until a source has at least three leads "
                "in this date range."
            )
        details.append(
            "Insights use existing workspace lead, qualification, routing, and review data."
        )

        if leads:
            sql_rate = round((sql_ready / len(leads)) * 100, 1)
            change_text = (
                "no prior-period baseline"
                if volume_change is None
                else f"{volume_change:+.1f}% lead volume versus the prior period"
            )
            summary = (
                f"Analyzed {len(leads)} leads: {sql_ready} are SQL-ready ({sql_rate}%), "
                f"{manual_review} need manual review, with {change_text}."
            )
        else:
            summary = (
                "No leads are available in this date range. Analytics will report "
                "workspace trends when records are present."
            )

        return {
            "agent": "Analytics Agent",
            "summary": summary,
            "metrics": [
                {"key": "leads_analyzed", "label": "Leads analyzed", "value": len(leads)},
                {"key": "sql_ready", "label": "SQL-ready", "value": sql_ready},
                {"key": "manual_review", "label": "Need review", "value": manual_review},
                {
                    "key": "lead_volume_change",
                    "label": "Lead volume change",
                    "value": "No baseline" if volume_change is None else f"{volume_change:+.1f}%",
                },
            ],
            "details": details,
        }

    def manual_review(self, range_key: RangeKey = "monthly") -> dict[str, Any]:
        window = AnalyticsWindow.build(range_key)
        leads = [
            lead for lead in self._leads(window.start, window.end) if self._is_manual_review(lead)
        ]
        items = []
        max_value = max(
            (
                _money(lead.outcome.opportunity_value)
                for lead in leads
                if lead.outcome is not None
            ),
            default=0,
        )
        for lead in leads:
            route = _latest_route(lead)
            reasons = lead_manual_review_reasons(self.session, lead, route)
            score = _qualification(lead).total_score
            priority = (route.priority if route else "normal").casefold()
            urgency = {"urgent": 100, "high": 80, "normal": 55, "low": 30}.get(
                priority, 50
            )
            target = _utc(route.sla_target_at) if route and route.sla_target_at else None
            overdue = bool(target and target < datetime.now(UTC))
            value = (
                min(100, round(_money(lead.outcome.opportunity_value) / max_value * 100))
                if max_value and lead.outcome is not None
                else 0
            )
            attention_score = round(
                score * 0.45 + max(urgency, 100 if overdue else 0) * 0.35 + value * 0.20,
                1,
            )
            items.append(
                {
                    **self._lead_card(lead),
                    "review_reasons": reasons,
                    "priority": priority,
                    "sla_overdue": overdue,
                    "attention_score": attention_score,
                }
            )
        items.sort(
            key=lambda item: (
                item["attention_score"],
                item["score"],
                item["received_at"],
            ),
            reverse=True,
        )
        return {"metadata": self.metadata(window), "items": items, "total": len(items)}

    def extended(self, range_key: RangeKey = "monthly") -> dict[str, Any]:
        """Return additional dashboard and analytics signals from recorded workspace data."""
        window = AnalyticsWindow.build(range_key)
        leads = self._leads(window.start, window.end)
        if not leads:
            return {
                "metadata": self.metadata(window),
                "speed_to_qualify": {
                    "average_minutes": None,
                    "median_minutes": None,
                    "sample_size": 0,
                },
                "qualified_to_booked": {"qualified": 0, "booked": 0, "rate": None},
                "sla": {
                    "configured": 0,
                    "compliant": 0,
                    "breached": 0,
                    "pending": 0,
                    "unverified": 0,
                    "rate": None,
                },
                "escalations": [],
                "stale_leads": [],
                "calibration": [],
                "sales_cycle_by_segment": [],
                "icp_fit_distribution": [],
                "response_heatmap": [],
                "stage_velocity": [],
                "channel_mix": [],
                "team_utilization_trend": [],
                "quarterly_scorecard": [],
                "revenue_by_conversion_type": [],
                "competitive_mentions": [],
                "data_gaps": self._analytics_data_gaps(),
            }

        now = datetime.now(UTC)
        events = list(
            self.session.scalars(
                select(PipelineEventORM)
                .where(
                    PipelineEventORM.workspace_id == self.workspace_id,
                    PipelineEventORM.lead_id.in_(
                        select(LeadORM.id).where(
                            LeadORM.workspace_id == self.workspace_id,
                            LeadORM.received_at >= window.start,
                            LeadORM.received_at <= window.end,
                        )
                    ),
                    PipelineEventORM.created_at >= window.start,
                    PipelineEventORM.created_at <= window.end,
                )
                .order_by(PipelineEventORM.created_at)
            )
        )
        events_by_lead: dict[UUID, list[PipelineEventORM]] = defaultdict(list)
        for event in events:
            events_by_lead[event.lead_id].append(event)

        qualify_minutes = [
            (_utc(lead.qualification.created_at) - _utc(lead.received_at)).total_seconds() / 60
            for lead in leads
            if lead.qualification is not None
            and _utc(lead.qualification.created_at) >= _utc(lead.received_at)
        ]
        booked_ids = {
            event.lead_id
            for event in events
            if event.stage == "meeting_scheduled"
            or (
                event.stage == "manual_progress"
                and event.status == "meeting_scheduled"
            )
        }
        qualified_leads = [
            lead for lead in leads
            if lead.qualification is not None
            and lead.qualification.classification in QUALIFIED_BANDS
        ]
        qualified_booked = sum(lead.id in booked_ids for lead in qualified_leads)

        sla_configured = 0
        sla_compliant = 0
        sla_breached = 0
        sla_pending = 0
        sla_unverified = 0
        escalation_rows = []
        stale_rows = []
        stale_after = timedelta(days=7)
        for lead in leads:
            route = _latest_route(lead)
            lead_events = events_by_lead.get(lead.id, [])
            if route and route.sla_target_at:
                target = _utc(route.sla_target_at)
                response_times = [
                    _utc(event.created_at)
                    for event in lead_events
                    if (
                        event.stage == "gmail_message" and event.status == "outbound"
                    )
                    or (
                        event.stage == "manual_progress"
                        and event.status in {"contacted", "responded", "meeting_scheduled"}
                    )
                ]
                first_response = min(
                    (time for time in response_times if time >= _utc(route.created_at)),
                    default=None,
                )
                sla_configured += 1
                if first_response and first_response <= target:
                    sla_compliant += 1
                elif first_response is not None:
                    sla_breached += 1
                    if (
                        target < now
                        and not (
                            lead.outcome
                            and lead.outcome.pipeline_stage in CLOSED_STAGES
                        )
                    ):
                        escalation_rows.append(
                            {
                                **self._lead_card(lead),
                                "sla_target_at": target,
                                "hours_overdue": round(
                                    (first_response - target).total_seconds() / 3600, 1
                                ),
                                "priority": route.priority,
                                "late_response": True,
                            }
                        )
                elif target < now and not (
                    lead.outcome and lead.outcome.pipeline_stage in CLOSED_STAGES
                ):
                    sla_breached += 1
                    escalation_rows.append(
                        {
                            **self._lead_card(lead),
                            "sla_target_at": target,
                            "hours_overdue": round((now - target).total_seconds() / 3600, 1),
                            "priority": route.priority,
                            "late_response": False,
                        }
                    )
                elif lead.outcome and lead.outcome.pipeline_stage in CLOSED_STAGES:
                    sla_unverified += 1
                else:
                    sla_pending += 1

            if not lead.outcome or lead.outcome.pipeline_stage not in CLOSED_STAGES:
                activity_times = [_utc(event.created_at) for event in lead_events]
                last_activity = max(activity_times, default=_utc(lead.received_at))
                inactive_for = now - last_activity
                if inactive_for >= stale_after:
                    stale_rows.append(
                        {
                            **self._lead_card(lead),
                            "last_activity_at": last_activity,
                            "inactive_days": round(inactive_for.total_seconds() / 86400, 1),
                        }
                    )

        escalation_rows.sort(key=lambda row: row["hours_overdue"], reverse=True)
        stale_rows.sort(key=lambda row: row["inactive_days"], reverse=True)

        closed = [
            lead for lead in leads
            if lead.outcome is not None and lead.outcome.pipeline_stage in CLOSED_STAGES
        ]
        score_buckets: dict[str, list[LeadORM]] = defaultdict(list)
        for lead in closed:
            score = lead.qualification.total_score if lead.qualification else 0
            lower = min(80, (score // 20) * 20)
            label = "80–100" if lower == 80 else f"{lower}–{lower + 19}"
            score_buckets[label].append(lead)
        calibration = []
        for label in ("0–19", "20–39", "40–59", "60–79", "80–100"):
            rows = score_buckets.get(label, [])
            wins = sum(lead.outcome.pipeline_stage == "closed_won" for lead in rows)
            calibration.append(
                {
                    "score_band": label,
                    "closed": len(rows),
                    "won": wins,
                    "win_rate": round(wins / len(rows) * 100, 1) if rows else None,
                }
            )

        cycles: dict[str, list[float]] = defaultdict(list)
        for lead in closed:
            outcome = lead.outcome
            closed_at = _utc(outcome.closed_at) if outcome.closed_at else None
            opened_at = _utc(outcome.opened_at or lead.received_at)
            if closed_at and closed_at >= opened_at:
                cycles[lead.industry or "Unknown industry"].append(
                    (closed_at - opened_at).total_seconds() / 86400
                )
        cycle_rows = [
            {
                "segment": segment,
                "sample_size": len(values),
                "average_days": round(fsum(values) / len(values), 1),
                "median_days": round(float(median(values)), 1),
            }
            for segment, values in sorted(
                cycles.items(), key=lambda row: (-len(row[1]), row[0].casefold())
            )
        ]

        icp_counts: dict[str, Counter[str]] = defaultdict(Counter)
        competitor_counts: Counter[str] = Counter()
        for lead in leads:
            structured = (
                lead.analysis.structured_output
                if lead.analysis and isinstance(lead.analysis.structured_output, dict)
                else {}
            )
            evaluation = structured.get("icp_evaluation", {})
            if isinstance(evaluation, dict):
                for criterion, result in evaluation.items():
                    if criterion == "explicit_disqualifiers" or not isinstance(result, dict):
                        continue
                    status = result.get("status")
                    if isinstance(status, str):
                        icp_counts[criterion][status] += 1
            context = structured.get("context", {})
            mention = (
                context.get("competitor_or_current_tools")
                if isinstance(context, dict)
                else None
            )
            if isinstance(mention, dict):
                value = mention.get("value")
            else:
                value = mention
            if isinstance(value, str) and value.strip().casefold() not in {
                "", "unknown", "none", "not provided", "n/a", "unspecified"
            }:
                competitor_counts[value.strip()[:120]] += 1
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item.strip():
                        competitor_counts[item.strip()[:120]] += 1
        icp_distribution = [
            {
                "criterion": criterion.replace("_", " ").title(),
                "statuses": dict(counts),
                "evaluated": sum(counts.values()),
            }
            for criterion, counts in sorted(icp_counts.items())
        ]

        heatmap_counts: Counter[tuple[int, int, str]] = Counter()
        for event in events:
            event_time = _utc(event.created_at)
            if (
                event.stage == "gmail_message"
                and event_time >= window.start
                and event.status in {"inbound", "outbound"}
            ):
                heatmap_counts[
                    (event_time.weekday(), event_time.hour, event.status)
                ] += 1
        response_heatmap = [
            {
                "weekday": weekday,
                "hour": hour,
                "inbound": heatmap_counts[(weekday, hour, "inbound")],
                "outbound": heatmap_counts[(weekday, hour, "outbound")],
            }
            for weekday in range(7)
            for hour in range(24)
            if heatmap_counts[(weekday, hour, "inbound")]
            or heatmap_counts[(weekday, hour, "outbound")]
        ]

        transitions: dict[tuple[str, str], list[float]] = defaultdict(list)
        lifecycle = {
            "contacted": 1,
            "responded": 2,
            "meeting_scheduled": 3,
            "proposal_sent": 4,
            "converted": 5,
            "lost": 5,
        }
        for lead in leads:
            last_stage = "received"
            last_time = _utc(lead.received_at)
            for event in events_by_lead.get(lead.id, []):
                if event.stage == "manual_progress":
                    next_stage = str(event.details.get("stage") or event.status)
                elif event.stage == "meeting_scheduled":
                    next_stage = "meeting_scheduled"
                else:
                    continue
                if (
                    next_stage not in lifecycle
                    or lifecycle[next_stage] < lifecycle.get(last_stage, 0)
                ):
                    continue
                event_time = _utc(event.created_at)
                if event_time < last_time:
                    continue
                if next_stage != last_stage:
                    transitions[(last_stage, next_stage)].append(
                        (event_time - last_time).total_seconds() / 3600
                    )
                    last_stage, last_time = next_stage, event_time
        stage_velocity = [
            {
                "transition": (
                    f"{start.replace('_', ' ').title()} → {end.replace('_', ' ').title()}"
                ),
                "sample_size": len(values),
                "median_hours": round(float(median(values)), 1),
            }
            for (start, end), values in sorted(
                transitions.items(), key=lambda row: (-float(median(row[1])), row[0])
            )
        ]

        channel_month_counts: Counter[tuple[str, str]] = Counter(
            (lead.source, lead.received_at.strftime("%Y-%m")) for lead in leads
        )
        channel_mix = [
            {"source": source, "month": month, "leads": count}
            for (source, month), count in sorted(channel_month_counts.items())
        ]
        assignment_counts: Counter[tuple[str, str]] = Counter()
        for lead in leads:
            for route in lead.routing_decisions:
                if route.assigned_rep_name:
                    assignment_counts[
                        (route.created_at.strftime("%Y-%m"), route.assigned_rep_name)
                    ] += 1
        team_utilization = [
            {"month": month, "rep": rep, "assignments": count}
            for (month, rep), count in sorted(assignment_counts.items())
        ]

        revenue_types: dict[str, float] = defaultdict(float)
        revenue_type_wins: Counter[str] = Counter()
        for lead in closed:
            if lead.outcome.pipeline_stage == "closed_won":
                conversion_type = lead.outcome.conversion_type or "untyped"
                revenue_types[conversion_type] += _money(
                    lead.outcome.closed_revenue
                )
                revenue_type_wins[conversion_type] += 1

        quarter_month = ((now.month - 1) // 3) * 3 + 1
        quarter_start = datetime(now.year, quarter_month, 1, tzinfo=UTC)
        previous_month = quarter_month - 3
        previous_year = now.year
        if previous_month <= 0:
            previous_month += 12
            previous_year -= 1
        previous_start = datetime(previous_year, previous_month, 1, tzinfo=UTC)
        elapsed = now - quarter_start
        previous_end = min(previous_start + elapsed, quarter_start)
        current_q = self._leads(quarter_start, now)
        previous_q = self._leads(previous_start, previous_end)
        quarterly_scorecard = self._scorecard_rows(current_q, previous_q)

        return {
            "metadata": self.metadata(window),
            "speed_to_qualify": {
                "average_minutes": round(fsum(qualify_minutes) / len(qualify_minutes), 1)
                if qualify_minutes else None,
                "median_minutes": round(float(median(qualify_minutes)), 1)
                if qualify_minutes else None,
                "sample_size": len(qualify_minutes),
            },
            "qualified_to_booked": {
                "qualified": len(qualified_leads),
                "booked": qualified_booked,
                "rate": round(qualified_booked / len(qualified_leads) * 100, 1)
                if qualified_leads else None,
            },
            "sla": {
                "configured": sla_configured,
                "compliant": sla_compliant,
                "breached": sla_breached,
                "pending": sla_pending,
                "unverified": sla_unverified,
                "rate": round(sla_compliant / (sla_compliant + sla_breached) * 100, 1)
                if sla_compliant + sla_breached else None,
                "definition": (
                    "First recorded outbound email or manual contacted/responded stage "
                    "before the routing deadline."
                ),
            },
            "escalations": escalation_rows[:12],
            "stale_leads": stale_rows[:12],
            "calibration": calibration,
            "sales_cycle_by_segment": cycle_rows[:12],
            "icp_fit_distribution": icp_distribution,
            "response_heatmap": response_heatmap,
            "stage_velocity": stage_velocity,
            "channel_mix": channel_mix,
            "team_utilization_trend": team_utilization,
            "quarterly_scorecard": quarterly_scorecard,
            "revenue_by_conversion_type": [
                {"type": label, "revenue": round(value, 2), "won": revenue_type_wins[label]}
                for label, value in sorted(revenue_types.items())
            ],
            "competitive_mentions": [
                {"mention": mention, "count": count}
                for mention, count in competitor_counts.most_common(20)
            ],
            "data_gaps": self._analytics_data_gaps(),
        }

    @staticmethod
    def _analytics_data_gaps() -> list[dict[str, str]]:
        return [
            {
                "key": "override_rate",
                "label": "Score overrides",
                "reason": "Human score overrides are not recorded as separate events.",
            },
            {
                "key": "loss_reasons",
                "label": "Win/loss reasons",
                "reason": "Closed outcomes do not store a structured loss reason.",
            },
            {
                "key": "revenue_motion",
                "label": "New vs expansion revenue",
                "reason": (
                    "Recorded wins distinguish Project and Sale, not new business versus "
                    "expansion or renewal."
                ),
            },
            {
                "key": "rep_first_response",
                "label": "Rep first-response leaderboard",
                "reason": "Email activity is not attributed to the individual sender.",
            },
        ]

    @staticmethod
    def _active_pipeline_by_origin(leads: list[LeadORM]) -> list[dict[str, Any]]:
        totals: Counter[str] = Counter()
        for lead in leads:
            if lead.outcome is None or lead.outcome.pipeline_stage not in ACTIVE_OPPORTUNITY_STAGES:
                continue
            origin = (
                "manual"
                if lead.source_detail == "manual-control-center" or lead.source == "manual"
                else "source_intake"
            )
            totals[origin] += _money(lead.outcome.opportunity_value)
        return [
            {"origin": origin, "value": round(value, 2)}
            for origin, value in sorted(totals.items())
        ]

    @staticmethod
    def _scorecard_rows(
        current: list[LeadORM], previous: list[LeadORM]
    ) -> list[dict[str, Any]]:
        def metrics(leads: list[LeadORM]) -> dict[str, float | int | None]:
            qualified = sum(
                lead.qualification is not None
                and lead.qualification.classification in QUALIFIED_BANDS
                for lead in leads
            )
            won = [
                lead for lead in leads
                if lead.outcome is not None and lead.outcome.pipeline_stage == "closed_won"
            ]
            return {
                "leads": len(leads),
                "qualification_rate": (
                    round(qualified / len(leads) * 100, 1) if leads else None
                ),
                "wins": len(won),
                "revenue": round(
                    fsum(_money(lead.outcome.closed_revenue) for lead in won), 2
                ),
            }

        now = metrics(current)
        before = metrics(previous)
        return [
            {"metric": key.replace("_", " ").title(), "current": value, "previous": before[key]}
            for key, value in now.items()
        ]

    def _workspace(self) -> WorkspaceORM:
        workspace = self.session.get(WorkspaceORM, self.workspace_id)
        if workspace is None:
            raise LookupError("workspace not found")
        return workspace

    def _leads(self, start: datetime, end: datetime) -> list[LeadORM]:
        return list(
            self.session.scalars(
                select(LeadORM)
                .options(
                    selectinload(LeadORM.qualification),
                    selectinload(LeadORM.analysis),
                    selectinload(LeadORM.routing_decisions),
                    selectinload(LeadORM.crm_syncs),
                    selectinload(LeadORM.outcome).selectinload(LeadOutcomeORM.credited_user),
                )
                .where(
                    LeadORM.workspace_id == self.workspace_id,
                    LeadORM.received_at >= start,
                    LeadORM.received_at <= end,
                )
                .order_by(LeadORM.received_at.desc())
            )
        )

    def _summary_values(self, leads: list[LeadORM]) -> dict[str, int | float]:
        total = len(leads)
        qualified = len(
            [lead for lead in leads if _qualification(lead).classification in QUALIFIED_BANDS]
        )
        scores = [_qualification(lead).total_score for lead in leads]
        velocities = self._routing_velocities(leads)
        failed_connections = self.session.scalar(
            select(func.count(ProviderConnectionORM.id)).where(
                ProviderConnectionORM.workspace_id == self.workspace_id,
                ProviderConnectionORM.status.in_({"error", "needs_attention"}),
            )
        )
        delivery_attention = len(
            [
                lead
                for lead in leads
                if (_latest_crm(lead) and _latest_crm(lead).status in {"failed", "retryable"})
                or (
                    _latest_route(lead)
                    and _latest_route(lead).notification_state in {"failed", "retryable"}
                )
            ]
        )
        return {
            "total_leads": total,
            "sqls": len(
                [lead for lead in leads if _qualification(lead).classification in SQL_BANDS]
            ),
            "qualification_rate": round((qualified / total) * 100, 1) if total else 0,
            "hot_leads": len(
                [lead for lead in leads if _qualification(lead).classification == "hot"]
            ),
            "active_pipeline_value": round(
                fsum(
                    _money(_outcome(lead).opportunity_value)
                    for lead in leads
                    if lead.outcome is not None
                    and lead.outcome.pipeline_stage in ACTIVE_OPPORTUNITY_STAGES
                ),
                2,
            ),
            "closed_won_revenue": round(
                fsum(
                    _money(_outcome(lead).closed_revenue)
                    for lead in leads
                    if lead.outcome is not None and lead.outcome.pipeline_stage == "closed_won"
                ),
                2,
            ),
            "average_score": round(fsum(scores) / len(scores), 1) if scores else 0,
            "average_routing_velocity_minutes": (
                round(fsum(velocities) / len(velocities), 1) if velocities else 0
            ),
            "manual_review": len([lead for lead in leads if self._is_manual_review(lead)]),
            "integration_attention": int(failed_connections or 0) + delivery_attention,
        }

    @staticmethod
    def _routing_velocities(leads: list[LeadORM]) -> list[float]:
        values = []
        for lead in leads:
            route = _latest_route(lead)
            if route is not None and _utc(route.created_at) >= _utc(lead.received_at):
                values.append(
                    (_utc(route.created_at) - _utc(lead.received_at)).total_seconds() / 60
                )
        return values

    @staticmethod
    def _is_manual_review(lead: LeadORM) -> bool:
        route = _latest_route(lead)
        return bool(
            (lead.analysis is not None and lead.analysis.requires_manual_review)
            or (route is not None and route.manual_review)
        )

    @staticmethod
    def _band_counts(leads: list[LeadORM]) -> list[dict[str, Any]]:
        counts = Counter(_qualification(lead).classification for lead in leads)
        order = [
            ("hot", "Hot"),
            ("warm", "Warm"),
            ("needs_qualifying", "Needs Qualifying"),
            ("nurture", "Nurture"),
            ("disqualify", "Disqualify"),
        ]
        return [{"band": display_name, "count": counts.get(key, 0)} for key, display_name in order]

    def _attention_leads(self, leads: list[LeadORM]) -> list[dict[str, Any]]:
        return [self._lead_card(lead) for lead in leads if self._is_manual_review(lead)]

    @staticmethod
    def _lead_card(lead: LeadORM) -> dict[str, Any]:
        route = _latest_route(lead)
        crm = _latest_crm(lead)
        return {
            "id": str(lead.id),
            "name": lead.full_name,
            "company": lead.company_name,
            "source": lead.source,
            "service": lead.product_or_service,
            "score": _qualification(lead).total_score,
            "band": _qualification(lead).classification,
            "received_at": lead.received_at,
            "routing_status": route.assigned_queue if route else "Awaiting route",
            "assigned_rep": route.assigned_rep_name if route else None,
            "crm_status": crm.status if crm else "not_synced",
            "manual_review": AnalyticsService._is_manual_review(lead),
            "pipeline_stage": lead.outcome.pipeline_stage if lead.outcome else "received",
        }

    @staticmethod
    def _revenue_group(leads: list[LeadORM], key: Callable[[LeadORM], str]) -> list[dict[str, Any]]:
        grouped: dict[str, float] = defaultdict(float)
        for lead in leads:
            grouped[key(lead)] += _money(_outcome(lead).closed_revenue)
        return [
            {"label": label, "value": round(value, 2)}
            for label, value in sorted(grouped.items(), key=lambda row: row[1], reverse=True)
        ]

    @staticmethod
    def _revenue_time_series(leads: list[LeadORM], window: AnalyticsWindow) -> list[dict[str, Any]]:
        format_key = "%Y-%m" if window.key == "annual" else "%Y-%m-%d"
        grouped: dict[str, float] = defaultdict(float)
        for lead in leads:
            outcome = _outcome(lead)
            if outcome.closed_at is not None:
                grouped[outcome.closed_at.strftime(format_key)] += _money(outcome.closed_revenue)
        return [
            {"period": period, "value": round(value, 2)}
            for period, value in sorted(grouped.items())
        ]
