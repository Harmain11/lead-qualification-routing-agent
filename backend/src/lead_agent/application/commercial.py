"""Decimal USD valuations shared by recorded commercial analytics."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from lead_agent.infrastructure.db.models import LeadORM

CENT = Decimal("0.01")


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def quote_total(quote: dict[str, Any]) -> Decimal:
    subtotal = Decimal(0)
    for line in quote.get("lines", []):
        amount = (
            Decimal(str(line["quantity"])) * money(line["unitPrice"])
            * (Decimal(1) - Decimal(str(line.get("discountPct", 0))) / 100)
        ).quantize(CENT, rounding=ROUND_HALF_UP)
        subtotal += amount * (int(quote["termMonths"]) if line["billing"] == "monthly" else 1)
    discounted = (
        subtotal * (Decimal(1) - Decimal(str(quote.get("discountPct", 0))) / 100)
    ).quantize(CENT, rounding=ROUND_HALF_UP)
    tax = (discounted * Decimal(str(quote.get("taxPct", 0))) / 100).quantize(
        CENT, rounding=ROUND_HALF_UP,
    )
    return discounted + tax


def pipeline_value_usd(lead: LeadORM, deal: dict[str, Any] | None) -> float:
    if lead.outcome and lead.outcome.pipeline_stage in {"closed_won", "closed_lost"}:
        return 0
    if deal and deal.get("stage") not in {"won", "lost"}:
        if deal.get("valueOverride") is not None:
            return float(money(deal["valueOverride"]))
        quotes = deal.get("quotes", [])
        quote = next((
            q for status in ("accepted", "sent", "draft")
            for q in quotes if q.get("status") == status
        ), None)
        if quote:
            return float(quote_total(quote)) if quote.get("currency") == "USD" else 0
    if (
        lead.outcome and lead.outcome.opportunity_value is not None
        and lead.outcome.currency == "USD"
    ):
        return float(money(lead.outcome.opportunity_value))
    # A lead's stated budget is not a recorded commercial opportunity.
    return 0
