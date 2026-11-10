from __future__ import annotations

import re
from dataclasses import dataclass

NEUTRAL_SENTENCE = "Let me know a time that works for you and I'll help arrange it."
NEUTRAL_SUBJECT = "Finding a time to talk"

_NOUN = r"(?:meeting|call|appointment|demo|demonstration|session|consultation|discussion|chat|slot)"
_TIME = (
    r"(?:\d{1,2}(?::\d{2})?\s*(?:am|pm)\b|tomorrow|today|tonight|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday|next week|this week|noon)"
)
_REQUEST = re.compile(
    r"\b(?:please|could you|can you|would you|kindly|let me know|if you(?:'d| would)? like|"
    r"to confirm (?:a|your|which|what|the time|a time)|not (?:yet )?(?:booked|scheduled|confirmed)|"
    r"has not been|have not|haven't|hasn't|isn't|is not|until)\b",
    re.I,
)
_CLAIMS = tuple(
    re.compile(pattern, re.I)
    for pattern in (
        rf"\bconfirm(?:ed|ing|s)?\b[^.!?\n]*\b{_NOUN}\b",
        rf"\b{_NOUN}\b[^.!?\n]*\b(?:is|are|was|were|has been|have been|been|got|'s)\s+"
        r"(?:now\s+|all\s+|already\s+)?(?:booked|scheduled|set(?: up)?|locked in|confirmed|"
        r"arranged|reserved|on (?:the|my|our) calendar|in the calendar)\b",
        rf"\b{_NOUN}\b[^.!?\n]*\b(?:confirmed|booked|scheduled)\b[^.!?\n]*\b{_TIME}",
        r"\b(?:you(?:'re| are)|we(?:'re| are)) (?:all )?set\b",
        r"\blocked in\b|\bpenciled in\b|\bon (?:the|my|our) calendar\b",
        rf"\b{_NOUN}\b[^.!?\n]*\b(?:has|have|was|were) been (?:cancel+ed|rescheduled|moved)\b",
        r"\bI(?:'ve| have)?(?:\s+(?:gone ahead(?: and)?|already|just|now))*\s+"
        r"(?:booked|scheduled|set up|reserved|put us down|blocked|confirmed|arranged)\b",
        r"\bsee you (?:then|on|at|tomorrow|next|this|monday|tuesday|wednesday|thursday|"
        r"friday|saturday|sunday)\b",
        rf"\bforward to (?:our|the) {_NOUN}\b[^.!?\n]*\b{_TIME}",
        rf"\b{_TIME}\b[^.!?\n]*\b(?:works|is set|is booked|is confirmed|is locked)\b[^.!?\n]*"
        r"\b(?:for us|for our)\b",
    )
)
_SUBJECT_CLAIM = re.compile(
    rf"\b(?:confirm(?:ed|ation)?|booked|scheduled|reserved)\b|\b{_NOUN}\b[^.!?\n]*\b{_TIME}",
    re.I,
)
_SENTENCES = re.compile(r"(?<=[.!?])[ \t]+|\n")


@dataclass(frozen=True)
class ScrubResult:
    subject: str | None
    body: str | None
    changed: bool


def sentence_claims_unverified_meeting(sentence: str) -> bool:
    if not sentence.strip() or _REQUEST.search(sentence):
        return False
    return any(pattern.search(sentence) for pattern in _CLAIMS)


def scrub_body(body: str | None) -> str | None:
    if not body:
        return body
    pieces = re.split(r"(?<=[.!?])([ \t]+)|(\n)", body)
    out: list[str] = []
    replaced = False
    for piece in pieces:
        if piece is None:
            continue
        if piece.strip() == "":
            out.append(piece)
        elif sentence_claims_unverified_meeting(piece):
            if not replaced:
                out.append(NEUTRAL_SENTENCE)
                replaced = True
            else:
                while out and out[-1].strip() == "":
                    out.pop()
        else:
            out.append(piece)
    return "".join(out)


def scrub_subject(subject: str | None) -> str | None:
    if subject and not _REQUEST.search(subject) and _SUBJECT_CLAIM.search(subject):
        prefix = "Re: " if subject.lower().startswith("re:") else ""
        return f"{prefix}{NEUTRAL_SUBJECT}"
    return subject


def guard_outgoing_email(
    subject: str | None, body: str | None, *, meeting_verified: bool
) -> ScrubResult:
    """Neutralise sentences that claim a meeting exists unless a provider verified one."""
    if meeting_verified:
        return ScrubResult(subject, body, False)
    new_subject, new_body = scrub_subject(subject), scrub_body(body)
    return ScrubResult(new_subject, new_body, (new_subject, new_body) != (subject, body))
