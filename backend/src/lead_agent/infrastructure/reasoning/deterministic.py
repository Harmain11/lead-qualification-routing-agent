from __future__ import annotations

import re
from collections.abc import Iterable

from lead_agent.domain.analysis import (
    AnalyzedField,
    ContextFields,
    EvidenceReference,
    FitStatus,
    IcpCriterionEvaluation,
    IcpEvaluation,
    ReasoningOutput,
    SalesSummary,
)
from lead_agent.domain.configuration import IcpConfig
from lead_agent.domain.models import NormalizedLead
from lead_agent.domain.operations import WorkspaceBusinessProfile


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _match(value: str | None, options: Iterable[str]) -> str | None:
    normalized = _norm(value)
    for option in options:
        candidate = _norm(option)
        if candidate and (candidate in normalized or normalized in candidate):
            return option
    return None


def _contains(text: str, signals: Iterable[str]) -> str | None:
    for signal in signals:
        if _norm(signal) in text:
            return signal
    return None


def _evidence(source_field: str, value: object) -> list[EvidenceReference]:
    return [EvidenceReference(source_field=source_field, excerpt=str(value)[:500])]


def _explicit(value: str | None, source_field: str) -> AnalyzedField:
    if not value:
        return AnalyzedField()
    return AnalyzedField(
        value=value,
        information_kind="explicit",
        confidence=1.0,
        evidence=_evidence(source_field, value),
    )


def _inferred(
    value: str | None, source_field: str, excerpt: str, confidence: float
) -> AnalyzedField:
    if not value:
        return AnalyzedField()
    return AnalyzedField(
        value=value,
        information_kind="inferred",
        confidence=confidence,
        evidence=_evidence(source_field, excerpt),
    )


def _sentence_with(text: str, signal: str | None) -> str | None:
    if not signal:
        return None
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if _norm(signal) in _norm(sentence):
            return sentence.strip()[:500]
    return text[:500]


def _criterion(
    matched: bool | None,
    rationale: str,
    field: str | None = None,
    value: object | None = None,
    *,
    partial: bool = False,
) -> IcpCriterionEvaluation:
    status: FitStatus = (
        "unknown"
        if matched is None
        else "partial"
        if partial
        else "match"
        if matched
        else "no_match"
    )
    return IcpCriterionEvaluation(
        status=status,
        confidence=0.5 if matched is None else 0.85 if partial else 1.0,
        rationale=rationale,
        evidence=_evidence(field, value) if field and value is not None else [],
    )


class DeterministicReasoningProvider:
    """Offline, repeatable context extraction for local use and tests."""

    provider_name = "deterministic"
    model_name = "rules-v1"
    prompt_version = "phase3-context-v1"

    def analyze(
        self,
        lead: NormalizedLead,
        icp: IcpConfig,
        business_profile: WorkspaceBusinessProfile | None = None,
    ) -> ReasoningOutput:
        message = lead.message
        text = _norm(f"{lead.product_or_service} {message}")

        pain_signal = _contains(
            text, [*icp.pain_points, *icp.scoring.signals.get("pain_point", [])]
        )
        pain_excerpt = _sentence_with(message, pain_signal)
        pain = (
            _inferred(pain_excerpt, "message", pain_excerpt or "", 0.86)
            if pain_excerpt
            else AnalyzedField()
        )

        urgency_signal = _contains(text, icp.scoring.signals.get("urgent", []))
        urgency_excerpt = _sentence_with(message, urgency_signal)
        urgency = (
            _inferred("high", "message", urgency_excerpt or urgency_signal or "", 0.92)
            if urgency_signal
            else _inferred("low", "message", message, 0.65)
            if _contains(text, ["someday", "just curious", "no rush"])
            else AnalyzedField()
        )

        timeline_match = re.search(
            r"\b(?:today|tomorrow|this week|next week|this month|next month|this quarter|"
            r"next quarter|within \d+ (?:day|days|week|weeks|month|months)|\d+[- ]day)\b",
            message,
            flags=re.IGNORECASE,
        )
        timeline = (
            _explicit(timeline_match.group(0), "message") if timeline_match else AnalyzedField()
        )

        budget_match = re.search(
            r"(?:\$|USD\s*)[\d,]+(?:\.\d+)?|budget(?:\s+is|\s+of|\s*:)?\s+[\d,$]+",
            message,
            flags=re.IGNORECASE,
        )
        if lead.budget is not None:
            budget = _explicit(f"{lead.budget} {icp.budget.currency}", "budget")
        elif budget_match:
            budget = _explicit(budget_match.group(0), "message")
        else:
            budget = AnalyzedField()

        size_parts: list[str] = []
        size_evidence: list[EvidenceReference] = []
        if lead.employee_count is not None:
            size_parts.append(f"{lead.employee_count} employees")
            size_evidence.extend(_evidence("employee_count", lead.employee_count))
        if lead.annual_revenue is not None:
            size_parts.append(f"annual revenue {lead.annual_revenue}")
            size_evidence.extend(_evidence("annual_revenue", lead.annual_revenue))
        company_signal = AnalyzedField(
            value="; ".join(size_parts) or None,
            information_kind="explicit" if size_parts else "unknown",
            confidence=1.0 if size_parts else 0.0,
            evidence=size_evidence,
        )

        role = _explicit(lead.job_title, "job_title")
        decision_role = _match(lead.job_title, icp.buyer_roles.get("decision_makers", []))
        influence_role = _match(lead.job_title, icp.buyer_roles.get("influencers", []))
        researcher_signal = _contains(text, ["researching", "student", "for a report", "learning"])
        if decision_role:
            decision_maker = _inferred(
                "likely decision maker", "job_title", lead.job_title or "", 0.9
            )
        elif influence_role:
            decision_maker = _inferred("likely influencer", "job_title", lead.job_title or "", 0.82)
        elif researcher_signal:
            decision_maker = _inferred(
                "researcher or non-decision-maker",
                "message",
                _sentence_with(message, researcher_signal) or message,
                0.82,
            )
        else:
            decision_maker = AnalyzedField()

        ready_signal = _contains(text, icp.scoring.signals.get("ready_to_buy", []))
        evaluation_signal = _contains(
            text, ["evaluate", "evaluating", "compare", "options", "researching"]
        )
        browsing_signal = _contains(
            text, ["just curious", "overview", "someday", "general information"]
        )
        if ready_signal:
            buying_stage = _inferred(
                "ready_to_buy", "message", _sentence_with(message, ready_signal) or message, 0.91
            )
            intent_level = "high"
        elif evaluation_signal:
            buying_stage = _inferred(
                "evaluation",
                "message",
                _sentence_with(message, evaluation_signal) or message,
                0.84,
            )
            intent_level = "medium"
        elif browsing_signal:
            buying_stage = _inferred(
                "awareness", "message", _sentence_with(message, browsing_signal) or message, 0.82
            )
            intent_level = "low"
        else:
            buying_stage = AnalyzedField()
            intent_level = "unknown"

        tool_signal = _contains(
            text,
            [
                "salesforce",
                "hubspot",
                "pipedrive",
                "dynamics",
                "marketo",
                "competitor",
                "current tool",
                "currently use",
            ],
        )
        tools = (
            _explicit(_sentence_with(message, tool_signal), "message")
            if tool_signal
            else AnalyzedField()
        )
        technical_signal = _contains(
            text,
            ["api", "integration", "webhook", "sso", "workflow", "data migration", "technical"],
        )
        technical = (
            _explicit(_sentence_with(message, technical_signal), "message")
            if technical_signal
            else AnalyzedField()
        )
        prior_signal = _contains(
            text, ["following up", "spoke before", "returning", "referral", "again"]
        )
        previous = (
            _inferred(
                "prior engagement indicated",
                "message",
                _sentence_with(message, prior_signal) or message,
                0.78,
            )
            if prior_signal
            else AnalyzedField()
        )
        channel_context = _inferred(
            f"{lead.source} inquiry",
            "source",
            f"{lead.source}: {lead.source_detail or 'no source detail'}",
            0.9,
        )

        industry_match = _match(lead.industry, icp.target_industries)
        size_known = lead.employee_count is not None
        size_fit = (
            icp.company_size.employees_min <= lead.employee_count <= icp.company_size.employees_max
            if size_known and lead.employee_count is not None
            else None
        )
        geography_match = _match(lead.geography, icp.target_geographies)
        role_match = decision_role or influence_role
        budget_fit = float(lead.budget) >= icp.budget.minimum if lead.budget is not None else None

        icp_evaluation = IcpEvaluation(
            profile_name=icp.profile_name,
            profile_version=icp.profile_version,
            industry_fit=_criterion(
                bool(industry_match) if lead.industry else None,
                "Industry matches the configured ICP."
                if industry_match
                else "Industry is outside the configured ICP."
                if lead.industry
                else "Industry was not provided.",
                "industry",
                lead.industry,
            ),
            company_size_fit=_criterion(
                size_fit,
                "Company size is inside the configured employee range."
                if size_fit
                else "Company size is outside the configured employee range."
                if size_fit is False
                else "Company size was not provided.",
                "employee_count",
                lead.employee_count,
            ),
            geography_fit=_criterion(
                bool(geography_match) if lead.geography else None,
                "Geography is served by the configured ICP."
                if geography_match
                else "Geography is outside the configured ICP."
                if lead.geography
                else "Geography was not provided.",
                "geography",
                lead.geography,
            ),
            buyer_role_fit=_criterion(
                bool(role_match) if lead.job_title else None,
                "Role matches a configured buyer role."
                if role_match
                else "Role does not match a configured buyer role."
                if lead.job_title
                else "Role was not provided.",
                "job_title",
                lead.job_title,
                partial=bool(influence_role),
            ),
            budget_fit=_criterion(
                budget_fit,
                "Budget meets the configured minimum."
                if budget_fit
                else "Budget is below the configured minimum."
                if budget_fit is False
                else "Budget was not provided.",
                "budget",
                lead.budget,
            ),
            pain_point_relevance=_criterion(
                bool(pain_signal),
                "The stated pain is relevant to the configured ICP."
                if pain_signal
                else "No configured pain point was identified.",
                "message",
                pain_excerpt,
            ),
            explicit_disqualifiers=[],
        )

        context = ContextFields(
            requested_product_service=_explicit(lead.product_or_service, "product_or_service"),
            pain_point=pain,
            urgency=urgency,
            explicit_timeline=timeline,
            budget_information=budget,
            company_size_or_spend=company_signal,
            person_role=role,
            decision_maker_status=decision_maker,
            buying_stage=buying_stage,
            competitor_or_current_tools=tools,
            technical_requirements=technical,
            previous_engagement=previous,
            source_channel_intent=channel_context,
        )
        field_map = context.model_dump()
        unknown_fields = [
            name for name, value in field_map.items() if value["information_kind"] == "unknown"
        ]
        known_confidences = [
            value["confidence"]
            for value in field_map.values()
            if value["information_kind"] != "unknown"
        ]
        confidence = sum(known_confidences) / len(known_confidences) if known_confidences else 0.0
        buying_signal = buying_stage.value or "No clear buying-stage signal"
        concern = (
            "Missing " + ", ".join(unknown_fields[:3]).replace("_", " ")
            if unknown_fields
            else "No material information gap identified"
        )
        summary = SalesSummary(
            who=f"{lead.full_name}{f', {lead.job_title}' if lead.job_title else ''}",
            company_context=f"{lead.company_name}{f' in {lead.industry}' if lead.industry else ''}",
            need=lead.product_or_service,
            pain_point=pain.value or "No explicit pain point provided",
            urgency=urgency.value or "Unknown",
            buying_signal=buying_signal,
            potential_concern=concern,
        )
        return ReasoningOutput(
            context=context,
            intent_level=intent_level,  # type: ignore[arg-type]
            overall_confidence=round(confidence, 3),
            unknown_fields=unknown_fields,
            contradictions=[],
            icp_evaluation=icp_evaluation,
            summary=summary,
            recommended_action=(
                "Review the evidence and prepare a focused discovery conversation."
                if intent_level in {"high", "medium"}
                else "Confirm need, authority, timeline, and budget before advancing."
            ),
        )
