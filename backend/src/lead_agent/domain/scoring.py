from __future__ import annotations

import re
from collections.abc import Iterable

from lead_agent.domain.configuration import IcpConfig, RoutingConfig
from lead_agent.domain.models import NormalizedLead, QualificationResult, ScoreLine


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


def _contains_any(text: str, signals: Iterable[str]) -> str | None:
    for signal in signals:
        if signal.lower() in text:
            return signal
    return None


def _matches_list(value: str | None, options: Iterable[str]) -> str | None:
    normalized = _norm(value)
    for option in options:
        candidate = _norm(option)
        if candidate and (candidate in normalized or normalized in candidate):
            return option
    return None


def _line(
    component: str,
    key: str,
    points: int,
    max_points: int,
    reason: str,
    *evidence: str,
) -> ScoreLine:
    return ScoreLine(
        component, key, max(0, min(points, max_points)), max_points, reason, tuple(evidence)
    )


def _hard_disqualifier(lead: NormalizedLead, icp: IcpConfig) -> tuple[str, str] | None:
    searchable = " ".join(
        value
        for value in (
            lead.full_name,
            lead.email,
            lead.job_title,
            lead.company_name,
            lead.industry,
            lead.product_or_service,
            lead.message,
        )
        if value
    ).lower()
    for rule in icp.disqualifiers:
        if rule.severity.lower() != "hard":
            continue
        for keyword in rule.keywords:
            if re.search(rf"\b{re.escape(keyword.lower())}\b", searchable):
                return rule.code, f"Hard disqualifier '{rule.code}' matched keyword '{keyword}'."
    return None


def qualify_lead(
    lead: NormalizedLead, icp: IcpConfig, routing: RoutingConfig
) -> QualificationResult:
    """Score a normalized lead deterministically and produce an explainable decision."""

    disqualifier = _hard_disqualifier(lead, icp)
    if disqualifier:
        code, reason = disqualifier
        band = routing.band_for(0)
        skipped = (
            _line(
                "icp_fit",
                "component_total",
                0,
                icp.scoring.weights["icp_fit"],
                "Skipped after hard disqualifier.",
            ),
            _line(
                "intent_urgency",
                "component_total",
                0,
                icp.scoring.weights["intent_urgency"],
                "Skipped after hard disqualifier.",
            ),
            _line(
                "engagement_quality",
                "component_total",
                0,
                icp.scoring.weights["engagement_quality"],
                "Skipped after hard disqualifier.",
            ),
        )
        return QualificationResult(
            total_score=0,
            classification=band.key,
            routing_queue=band.queue,
            routing_action=band.action,
            hard_disqualified=True,
            disqualifier_code=code,
            rationale=(
                reason,
                "ICP Fit: 0/40 - skipped after hard disqualifier.",
                "Intent & Urgency: 0/35 - skipped after hard disqualifier.",
                "Engagement Quality: 0/25 - skipped after hard disqualifier.",
                "All weighted scoring was short-circuited to 0.",
            ),
            score_breakdown=skipped,
            policy_version=routing.policy_version,
        )

    points = icp.scoring.component_points
    lines: list[ScoreLine] = []

    icp_points = points.get("icp_fit", {})
    industry = _matches_list(lead.industry, icp.target_industries)
    lines.append(
        _line(
            "icp_fit",
            "industry_match",
            icp_points.get("industry_match", 0) if industry else 0,
            icp_points.get("industry_match", 0),
            f"Industry {'matches' if industry else 'does not match or is missing'} the ICP.",
            industry or "no industry match",
        )
    )

    size = icp.company_size
    size_fit = (
        lead.employee_count is not None
        and (size.employees_min <= lead.employee_count <= size.employees_max)
        and size.employees_max > 0
    )
    lines.append(
        _line(
            "icp_fit",
            "company_size_fit",
            icp_points.get("company_size_fit", 0) if size_fit else 0,
            icp_points.get("company_size_fit", 0),
            f"Employee count {'is' if size_fit else 'is not'} inside the configured ICP range.",
            str(lead.employee_count)
            if lead.employee_count is not None
            else "employee count missing",
        )
    )

    geography = _matches_list(lead.geography, icp.target_geographies)
    lines.append(
        _line(
            "icp_fit",
            "geography_match",
            icp_points.get("geography_match", 0) if geography else 0,
            icp_points.get("geography_match", 0),
            f"Geography {'is' if geography else 'is not or is missing'} in the served ICP markets.",
            geography or "no geography match",
        )
    )

    roles = [*icp.buyer_roles.get("decision_makers", []), *icp.buyer_roles.get("influencers", [])]
    role = _matches_list(lead.job_title, roles)
    lines.append(
        _line(
            "icp_fit",
            "buyer_role_match",
            icp_points.get("buyer_role_match", 0) if role else 0,
            icp_points.get("buyer_role_match", 0),
            f"Buyer title {'matches' if role else 'does not match or is missing'} "
            "configured roles.",
            role or "no role match",
        )
    )

    budget_fit = lead.budget is not None and float(lead.budget) >= icp.budget.minimum
    lines.append(
        _line(
            "icp_fit",
            "budget_fit",
            icp_points.get("budget_fit", 0) if budget_fit else 0,
            icp_points.get("budget_fit", 0),
            f"Budget {'meets' if budget_fit else 'does not meet or is missing'} "
            "the configured minimum.",
            str(lead.budget) if lead.budget is not None else "budget missing",
        )
    )

    intent_points = points.get("intent_urgency", {})
    text = _norm(f"{lead.message} {lead.product_or_service}")
    urgent = _contains_any(text, icp.scoring.signals.get("urgent", []))
    lines.append(
        _line(
            "intent_urgency",
            "urgency",
            intent_points.get("urgency", 0) if urgent else 0,
            intent_points.get("urgency", 0),
            f"Urgency signal {'found' if urgent else 'not found'}.",
            urgent or "no urgency signal",
        )
    )
    buying_stage = _contains_any(text, icp.scoring.signals.get("ready_to_buy", []))
    lines.append(
        _line(
            "intent_urgency",
            "buying_stage",
            intent_points.get("buying_stage", 0) if buying_stage else 0,
            intent_points.get("buying_stage", 0),
            f"Ready-to-buy signal {'found' if buying_stage else 'not found'}.",
            buying_stage or "no ready-to-buy signal",
        )
    )
    budget_signal = lead.budget is not None or _contains_any(
        text, icp.scoring.signals.get("budget", [])
    )
    lines.append(
        _line(
            "intent_urgency",
            "budget_signal",
            intent_points.get("budget_signal", 0) if budget_signal else 0,
            intent_points.get("budget_signal", 0),
            f"Budget signal {'present' if budget_signal else 'absent'}.",
            str(lead.budget) if lead.budget is not None else "no budget signal",
        )
    )
    specific = _contains_any(text, icp.scoring.signals.get("specific_request", []))
    lines.append(
        _line(
            "intent_urgency",
            "specific_request",
            intent_points.get("specific_request", 0) if specific else 0,
            intent_points.get("specific_request", 0),
            f"Specific request signal {'found' if specific else 'not found'}.",
            specific or "no specific request signal",
        )
    )

    engagement_points = points.get("engagement_quality", {})
    detail_max = engagement_points.get("message_detail", 0)
    detail_ratio = min(len(lead.message) / max(icp.scoring.message_detail_chars, 1), 1)
    detail_score = round(detail_max * detail_ratio)
    lines.append(
        _line(
            "engagement_quality",
            "message_detail",
            detail_score,
            detail_max,
            f"Message detail earned {detail_score}/{detail_max} points based on length "
            "and specificity.",
            f"{len(lead.message)} characters",
        )
    )
    completeness_fields = [
        lead.full_name,
        lead.email,
        lead.company_name,
        lead.job_title,
        lead.phone,
    ]
    completeness = sum(bool(value) for value in completeness_fields)
    completeness_max = engagement_points.get("contact_completeness", 0)
    completeness_score = round(completeness_max * completeness / len(completeness_fields))
    lines.append(
        _line(
            "engagement_quality",
            "contact_completeness",
            completeness_score,
            completeness_max,
            f"{completeness} of {len(completeness_fields)} contact fields were provided.",
        )
    )
    pain = _contains_any(text, icp.scoring.signals.get("pain_point", [])) or _contains_any(
        text, icp.pain_points
    )
    lines.append(
        _line(
            "engagement_quality",
            "pain_clarity",
            engagement_points.get("pain_clarity", 0) if pain else 0,
            engagement_points.get("pain_clarity", 0),
            f"Pain point {'is clear' if pain else 'is not clearly expressed'}.",
            pain or "no configured pain signal",
        )
    )
    channel_context = bool(lead.source and lead.source_detail)
    lines.append(
        _line(
            "engagement_quality",
            "channel_context",
            engagement_points.get("channel_context", 0) if channel_context else 0,
            engagement_points.get("channel_context", 0),
            f"Channel context {'is' if channel_context else 'is not'} available.",
            lead.source_detail or lead.source,
        )
    )

    total = sum(line.points for line in lines)
    total = max(0, min(total, 100))
    band = routing.band_for(total)
    rationale = tuple(
        f"{line.component.replace('_', ' ').title()} / {line.key.replace('_', ' ')}: "
        f"{line.points}/{line.max_points} - {line.reason}"
        for line in lines
    )
    return QualificationResult(
        total_score=total,
        classification=band.key,
        routing_queue=band.queue,
        routing_action=band.action,
        hard_disqualified=False,
        disqualifier_code=None,
        rationale=rationale,
        score_breakdown=tuple(lines),
        policy_version=routing.policy_version,
    )
