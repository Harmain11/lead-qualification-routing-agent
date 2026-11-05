"""Conservative profile fields stated explicitly by a Gmail lead."""

import re
from email.utils import parseaddr


_COMPANY = re.compile(
    r"\b(?:my|our) company(?:'s)?(?:\s+name)?\s+(?:is called|is named|is)\s+"
    r"([^\n.!?;:,]{2,100})"
    r"|\b(?:I|we) work (?:at|for)\s+([^\n.!?;:,]{2,100})"
    r"|\b(?:I am|I'm|we are|we're) with\s+([^\n.!?;:,]{2,100})"
    r"|\b(?:founder|owner|CEO|president) of\s+([^\n.!?;:,]{2,100})",
    re.IGNORECASE,
)
_INDUSTRY_PATTERNS = (
    re.compile(r"\b(?:my|our) industry is\s+([^\n.!?;:,]{2,100})", re.IGNORECASE),
    re.compile(
        r"\b(?:we|I) (?:are|am) in the\s+([^\n.!?;:,]{2,100}?)\s+industry\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:we|I) (?:work|operate) in (?:the\s+)?"
        r"([^\n.!?;:,]{2,100}?)\s+industry\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bworking in\s+([^\n.!?;:,]{2,100})", re.IGNORECASE),
    re.compile(
        r"\b(?:my|our) (?:business|company) operates in\s+([^\n.!?;:,]{2,100})",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:my|our) field is\s+([^\n.!?;:,]{2,100})", re.IGNORECASE),
    re.compile(
        r"\b(?:we|I) (?:are|am) (?:a|an)\s+([^\n.!?;:,]{2,80}?)\s+"
        r"(?:company|business|agency|firm)\b",
        re.IGNORECASE,
    ),
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
                candidate = next((group for group in match.groups() if group), "").strip().strip('"\'')
                candidate = re.split(r"\s+(?:as|and|where|which|that)\b", candidate, maxsplit=1, flags=re.I)[0].strip()
                if candidate.lower() not in _PLACEHOLDERS:
                    company = candidate
        if industry is None:
            for pattern in _INDUSTRY_PATTERNS:
                match = pattern.search(body)
                if match:
                    candidate = match.group(1).strip()
                    if candidate and candidate.lower() not in _PLACEHOLDERS:
                        industry = candidate
                        break
    return company, industry
