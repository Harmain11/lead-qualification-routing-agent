"""One outcome vocabulary for calls from every provider."""

from __future__ import annotations

from typing import Literal

CallOutcome = Literal["answered", "missed", "no_answer", "failed", "in_progress"]

_IN_PROGRESS = {"queued", "initiated", "initial", "ringing", "in_progress", "submitting", "pending", "started", "dialing"}
_FAILED = {"failed", "error", "canceled", "cancelled", "rejected", "blocked"}
_UNANSWERED = {"no_answer", "noanswer", "busy", "missed", "unanswered", "voicemail", "abandoned", "not_answered"}
_FINISHED = {"completed", "complete", "done", "ended", "answered", "finished", "success", "successful"}


def call_outcome(status: str | None, direction: str | None, duration_seconds: int | None) -> CallOutcome:
    normalized = (status or "").strip().lower().replace("-", "_").replace(" ", "_")
    inbound = (direction or "").strip().lower() in {"inbound", "incoming", "in"}
    if normalized in _IN_PROGRESS:
        return "in_progress"
    if normalized in _FAILED:
        return "failed"
    unanswered = normalized in _UNANSWERED or (normalized in _FINISHED and not duration_seconds)
    if unanswered:
        return "missed" if inbound else "no_answer"
    if normalized in _FINISHED or duration_seconds:
        return "answered"
    return "missed" if inbound else "no_answer"
