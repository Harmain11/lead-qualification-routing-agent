from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from lead_agent.application.governance import operations_policy
from lead_agent.domain.operations import Territory
from lead_agent.infrastructure.db.models import LeadORM, UserORM, WorkspaceMembershipORM


def matches_territory(lead: LeadORM, group: Territory) -> bool:
    for values, text in (
        (group.regions, lead.geography),
        (group.industries, lead.industry),
        (group.products, lead.product_or_service),
    ):
        if values and not any(
            re.search(r"(?<!\w)" + re.escape(value.casefold()) + r"(?!\w)", (text or "").casefold())
            for value in values
        ):
            return False
    return True


def lead_territory(session: Session, lead: LeadORM) -> Territory | None:
    return next(
        (
            group
            for group in operations_policy(session, lead.workspace_id).territories
            if matches_territory(lead, group)
        ),
        None,
    )


def capacity_candidates(
    session: Session,
    lead: LeadORM,
    candidates: list[tuple[WorkspaceMembershipORM, UserORM]],
    counts: dict[str, int],
) -> tuple[list[tuple[WorkspaceMembershipORM, UserORM]], str]:
    policy = operations_policy(session, lead.workspace_id)
    group = lead_territory(session, lead)
    if policy.territories and group is None:
        return [], "No territory policy matches this lead."
    allowed = set(group.member_ids) if group else None
    limits = {value.user_id: value for value in policy.capacity}
    selected = []
    for membership, user in candidates:
        if allowed is not None and user.id not in allowed:
            continue
        state = limits.get(user.id)
        if state:
            expires = state.available_until
            if expires and expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if not state.available or (expires and expires <= datetime.now(UTC)):
                continue
            if counts.get(str(user.id), 0) >= state.max_open_leads:
                continue
        selected.append((membership, user))
    return selected, (f"Territory: {group.name}. " if group else "") + (
        "Rep availability and open-lead limits applied."
        if selected
        else "No member has available capacity; retain in the queue."
    )
