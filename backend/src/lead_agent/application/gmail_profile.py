"""Conservative profile fields stated explicitly by a Gmail lead."""

import re
from email.utils import parseaddr


_COMPANY = re.compile(
    r"\b(?:my|our) company(?:'s)? name is\s+([^\n.!?;:,]{2,100})",
    re.IGNORECASE,
)
_INDUSTRY = re.compile(
    r"\b(?:my|our) industry is\s+([^\n.!?;:,]{2,100})"
    r"|\bworking in\s+([^\n.!?;:,]{2,100})"
    r"|\b(?:we|I) (?:are|am) in the\s+([^\n.!?;:,]{2,100}?)\s+industry\b",
    re.IGNORECASE,
)
_PLACEHOLDERS = {"unknown", "unknown company", "n/a", "not provided", "none"}


def extract_stated_gmail_profile(
    messages: list[dict[str, str]], lead_email: str,
) -> tuple[str | None, str | None]:
    """Use only the lead's own inbound text, never a quoted or forwarded message."""
    company: str | None = None
    industry: str | None = None
    expected = lead_email.strip().lower()
    for message in messages:
        if str(message.get("direction") or "").lower() != "inbound":
            continue
        sender = parseaddr(str(message.get("sender") or ""))[1].lower()
        if sender != expected:
            continue
        body = str(message.get("body") or "")[:12_000]
        # Quoted replies, forwarded content, and signatures can describe others.
        body = re.split(r"(?im)^\s*(?:on .+wrote:|forwarded message|from:|>+)", body, maxsplit=1)[0]
        if company is None:
            match = _COMPANY.search(body)
            if match:
                candidate = match.group(1).strip().strip('"\'')
                if candidate.lower() not in _PLACEHOLDERS:
                    company = candidate
        if industry is None:
            match = _INDUSTRY.search(body)
            if match:
                candidate = next((group for group in match.groups() if group), "").strip()
                if candidate and candidate.lower() not in _PLACEHOLDERS:
                    industry = candidate
    return company, industry
