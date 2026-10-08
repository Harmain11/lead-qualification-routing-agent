from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import fsum
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from lead_agent.infrastructure.db.models import (
    AgentToolAuditORM,
    LeadORM,
    LeadOutcomeORM,
    PipelineEventORM,
    ProviderConnectionORM,
    QualificationResultORM,
    WorkspaceORM,
    WorkspaceMembershipORM,
    UserORM,
)

RangeKey = Literal["weekly", "monthly", "annual"]
SQL_BANDS = {"hot", "warm"}
QUALIFIED_BANDS = {"hot", "warm", "needs_qualifying"}
ACTIVE_OPPORTUNITY_STAGES = {"opportunity", "proposal", "negotiation"}
CLOSED_STAGES = {"closed_won", "closed_lost"}
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
            "attention_leads": self._attention_leads(leads)[:6],
            "recent_leads": [self._lead_card(lead) for lead in leads[:8]],
        }

    def channels(self, range_key: RangeKey = "monthly") -> dict[str, Any]:
        window = AnalyticsWindow.build(range_key)
        leads = self._leads(window.start, window.end)
        grouped: dict[str, list[LeadORM]] = defaultdict(list)
        for lead in leads:
            grouped[lead.source].append(lead)
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
            rows.append(
                {
                    "channel": channel,
                    "lead_volume": len(items),
                    "qualified_leads": qualified,
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
            "bands": self._band_counts(leads),
            "manual_review": len([lead for lead in leads if self._is_manual_review(lead)]),
        }

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
            won = [lead for lead in items if lead.outcome and lead.outcome.pipeline_stage == "closed_won"]
            lost = sum(bool(lead.outcome and lead.outcome.pipeline_stage == "closed_lost") for lead in items)
            projects = sum(lead.outcome.conversion_type == "project" for lead in won)
            sales = sum(lead.outcome.conversion_type == "sale" for lead in won)
            qualified = sum(_qualification(lead).classification in QUALIFIED_BANDS for lead in items)
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
                "average_response_minutes": round(sum(velocities) / len(velocities), 1) if velocities else None,
                "summary": (
                    f"{user.display_name} has {len(items)} assigned lead(s) in this period, "
                    f"{qualified} qualified lead(s), and {len(won)} win(s): "
                    f"{projects} Project(s), {sales} Sale(s)"
                    + (f", {len(won) - projects - sales} earlier untyped win(s)" if len(won) > projects + sales else "")
                    + f". {len(items) - len(won) - lost} remain open and {lost} were lost. "
                    f"Assigned-lead conversion is {conversion_rate:g}%."
                ),
            })
        return {
            "metadata": self.metadata(window), "reps": rows, "members": members,
            "unattributed_leads": unattributed,
            "cohort_description": (
                "Includes leads received in the selected period and their current outcomes. "
                "Closed leads use recorded member credit; open leads use their current assigned member. "
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
        for lead in leads:
            reasons: list[str] = []
            if lead.analysis and lead.analysis.requires_manual_review:
                if lead.analysis.error_message:
                    reasons.append(lead.analysis.error_message)
                structured = lead.analysis.structured_output
                reasons.extend(structured.get("contradictions", []))
                if float(lead.analysis.confidence) < 0.6:
                    reasons.append(
                        f"Low analysis confidence ({float(lead.analysis.confidence):.0%})"
                    )
            route = _latest_route(lead)
            if route and route.manual_review:
                reasons.append(route.routing_reason)
            items.append({**self._lead_card(lead), "review_reasons": list(dict.fromkeys(reasons))})
        return {"metadata": self.metadata(window), "items": items, "total": len(items)}

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
