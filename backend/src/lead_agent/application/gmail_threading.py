from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


def same_gmail_subject(left: str, right: str) -> bool:
    """Gmail replies may add Re:, but a different subject starts a new thread."""
    def base(value: str) -> str:
        return re.sub(r"^(?:re:\s*)+", "", value.strip(), flags=re.IGNORECASE).strip().casefold()

    return bool(base(left)) and base(left) == base(right)


def matches_lead_thread(
    details: Iterable[Mapping[str, Any]],
    *,
    connection_id: str,
    thread_id: str,
    in_reply_to: str,
    subject: str,
) -> bool:
    thread_messages = [
        item for item in details
        if str(item.get("gmail_connection_id") or "") == connection_id
        and str(item.get("thread_id") or item.get("gmail_thread_id") or "") == thread_id
    ]
    return bool(thread_messages) and any(
        str(item.get("rfc_message_id") or "") == in_reply_to
        for item in thread_messages
    ) and any(
        same_gmail_subject(subject, str(item.get("subject") or ""))
        for item in thread_messages
    )
