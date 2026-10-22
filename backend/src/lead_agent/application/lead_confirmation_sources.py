from __future__ import annotations

from collections.abc import Sequence

LEAD_CONFIRMATION_SOURCES: Sequence[tuple[str, str]] = (
    ("website", "Web form"),
    ("gmail", "Gmail"),
    ("outlook", "Outlook"),
    ("salesforce", "Salesforce"),
    ("hubspot", "HubSpot"),
    ("gohighlevel", "GoHighLevel"),
    ("instagram", "Instagram"),
    ("messenger", "Messenger"),
    ("whatsapp_business", "WhatsApp Business"),
    ("clutch", "Clutch"),
    ("other", "Other lead sources"),
)

LEAD_CONFIRMATION_SOURCE_KEYS = {key for key, _label in LEAD_CONFIRMATION_SOURCES}


def lead_confirmation_source_key(source: str | None, source_detail: str | None) -> str:
    """Map an imported lead to a stable platform key for email automation."""

    normalized_source = (source or "").strip().casefold().replace("-", "_")
    detail = (source_detail or "").strip().casefold()

    # Preserve the existing confirmation-email rule for manually entered leads.
    if detail == "manual-control-center":
        return "website"

    if detail.startswith("crm-import:"):
        provider = detail.split(":", 2)[1].replace("-", "_")
        if provider in LEAD_CONFIRMATION_SOURCE_KEYS:
            return provider

    provider_markers = (
        ("gmail", "gmail"),
        ("outlook", "outlook"),
        ("salesforce", "salesforce"),
        ("hubspot", "hubspot"),
        ("gohighlevel", "gohighlevel"),
        ("go high level", "gohighlevel"),
        ("instagram", "instagram"),
        ("messenger", "messenger"),
        ("whatsapp", "whatsapp_business"),
        ("clutch", "clutch"),
    )
    for marker, key in provider_markers:
        if marker in detail:
            return key

    if normalized_source in LEAD_CONFIRMATION_SOURCE_KEYS:
        return normalized_source
    if normalized_source in {"web", "website_form", "webform"}:
        return "website"
    return "other"
