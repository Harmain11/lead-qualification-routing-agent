from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from lead_agent.domain.models import NormalizedLead


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())
    return cleaned or None


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("$", "").strip())
    except (InvalidOperation, ValueError):
        return None


def normalize_lead(payload: dict[str, Any], lead_id: UUID) -> NormalizedLead:
    """Map website/manual input to the stable channel-agnostic lead contract."""

    full_name = _clean(payload.get("full_name") or payload.get("name"))
    email = _clean(payload.get("email"))
    company_name = _clean(payload.get("company_name") or payload.get("company"))
    message = _clean(payload.get("message") or payload.get("inquiry"))
    product = _clean(payload.get("product_or_service") or payload.get("product"))
    gmail_phone_only = (
        _clean(payload.get("source_detail")) == "Gmail"
        and not email
        and bool(_clean(payload.get("phone")))
    )
    if (
        not full_name
        or (not email and not gmail_phone_only)
        or not company_name
        or not message
        or not product
    ):
        raise ValueError(
            "full_name, email, company_name, product_or_service, and message are required"
        )

    employee_count: int | None = None
    if payload.get("employee_count") not in (None, ""):
        try:
            employee_count = int(payload["employee_count"])
        except (TypeError, ValueError):
            raise ValueError("employee_count must be an integer") from None

    received_at = payload.get("received_at")
    if isinstance(received_at, str):
        received_at = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    if not isinstance(received_at, datetime):
        received_at = datetime.now(UTC)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)

    return NormalizedLead(
        id=lead_id,
        source=_clean(payload.get("source")) or "website",
        source_detail=_clean(payload.get("source_detail")),
        external_id=_clean(payload.get("external_id")),
        full_name=full_name,
        email=(email or "").lower(),
        phone=_clean(payload.get("phone")),
        job_title=_clean(payload.get("job_title") or payload.get("title")),
        company_name=company_name,
        industry=_clean(payload.get("industry")),
        employee_count=employee_count,
        annual_revenue=_decimal(payload.get("annual_revenue")),
        geography=_clean(payload.get("geography") or payload.get("country")),
        budget=_decimal(payload.get("budget")),
        product_or_service=product,
        message=message,
        received_at=received_at,
        raw_payload=payload,
    )
