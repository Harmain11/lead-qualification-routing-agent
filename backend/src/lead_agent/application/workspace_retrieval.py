from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import Session

from lead_agent.infrastructure.db.calling import CallRecordORM
from lead_agent.infrastructure.db.models import (
    LeadFollowUpEmailORM,
    LeadORM,
    PipelineEventORM,
)
from lead_agent.infrastructure.db.operations import (
    CompanyORM,
    ContactORM,
    LeadContactORM,
    LeadNoteORM,
)

_STOP_WORDS = {
    "about", "after", "again", "also", "and", "any", "are", "can", "could",
    "did", "does", "for", "from", "get", "give", "has", "have", "help",
    "how", "i", "in", "into", "is", "it", "me", "my", "of", "on", "or",
    "our", "please", "show", "tell", "that", "the", "their", "them", "there",
    "this", "to", "us", "what", "when", "where", "which", "who", "with",
    "would", "you", "your", "lead", "leads", "prospect", "prospects",
    "contact", "contacts", "email", "emails", "message", "messages", "call",
    "calls", "phone", "meeting", "meetings", "history", "activity", "activities",
    "record", "records", "workspace", "corral", "recent", "latest", "all",
}

_CATEGORY_WORDS = {
    "call", "calls", "dial", "phone", "recording", "transcript",
    "email", "emails", "message", "messages", "gmail", "inbox", "sent",
    "meeting", "meetings", "calendar", "appointment", "booking",
    "lead", "leads", "prospect", "prospects", "pipeline",
    "contact", "contacts", "account", "accounts", "company", "companies",
    "note", "notes", "progress", "activity", "activities", "history",
}


def _terms(query: str) -> list[str]:
    found: list[str] = []
    for value in re.findall(r"[\w@.+-]{3,}", query.casefold()):
        if value in _STOP_WORDS or value in _CATEGORY_WORDS or value in found:
            continue
        found.append(value[:80])
        if len(found) == 8:
            break
    return found


def _word_similarity(left: str, right: str) -> float:
    """Return an edit-distance score that treats a single transposition as one typo."""
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0

    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current

    distance = previous[-1]
    if len(left) == len(right):
        for index in range(len(left) - 1):
            transposed = (
                left[:index]
                + left[index + 1]
                + left[index]
                + left[index + 2 :]
            )
            if transposed == right:
                distance = min(distance, 1)
                break
    return 1.0 - distance / max(len(left), len(right))


def _normalize_name_tokens(value: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", value.casefold())
    unaccented = "".join(character for character in folded if not unicodedata.combining(character))
    return re.findall(r"[^\W_]{2,}", unaccented)


def _lead_name_similarity(query_terms: list[str], lead_name: str | None) -> float:
    if not lead_name:
        return 0.0
    name_tokens = _normalize_name_tokens(lead_name)
    if not name_tokens:
        return 0.0

    similarities = []
    for term in query_terms:
        if "@" in term:
            continue
        query_token = _normalize_name_tokens(term)
        if not query_token:
            continue
        best = max(
            _word_similarity(query_token[0], name_token)
            for name_token in name_tokens
        )
        if best >= 0.55:
            similarities.append(best)
    return sum(similarities) / len(similarities) if similarities else 0.0


def _similar_workspace_leads(
    session: Session,
    workspace_id: UUID,
    query_terms: list[str],
) -> list[dict[str, Any]]:
    if not query_terms:
        return []

    # Compare only names from this workspace. This never searches external providers.
    name_rows = session.execute(
        select(LeadORM.id, LeadORM.full_name, LeadORM.company_name).where(
            LeadORM.workspace_id == workspace_id
        )
    ).all()
    scored = [
        (score, lead_id, name, company)
        for lead_id, name, company in name_rows
        if (score := _lead_name_similarity(query_terms, name)) >= 0.72
        and score < 0.999
    ]
    if not scored:
        return []

    scored.sort(key=lambda item: (-item[0], (item[2] or "").casefold(), str(item[1])))
    best_score = scored[0][0]
    close_matches = [
        item for item in scored if best_score - item[0] <= 0.08
    ]
    return [
        {
            "id": str(lead_id),
            "name": name,
            "company": company,
            "similarity": round(score, 3),
        }
        for score, lead_id, name, company in close_matches
    ]


def _text_filter(columns: tuple[Any, ...], terms: list[str]) -> Any:
    return or_(*(column.ilike(f"%{term}%") for column in columns for term in terms))


def _short(value: Any, limit: int = 700) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned[:limit]


def _iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value else None


def _lead_summary(lead: LeadORM) -> dict[str, Any]:
    qualification = lead.qualification
    current_route = next(
        (item for item in lead.routing_decisions if item.is_current),
        None,
    )
    outcome = lead.outcome
    return {
        "id": str(lead.id),
        "name": lead.full_name,
        "email": lead.email,
        "phone": lead.phone,
        "company": lead.company_name,
        "job_title": lead.job_title,
        "industry": lead.industry,
        "product_or_service": lead.product_or_service,
        "geography": lead.geography,
        "source": lead.source,
        "source_detail": lead.source_detail,
        "received_at": _iso(lead.received_at),
        "inquiry": _short(lead.message, 600),
        "qualification": (
            {
                "classification": qualification.classification,
                "score": qualification.total_score,
            }
            if qualification
            else None
        ),
        "routing": (
            {
                "assigned_rep": current_route.assigned_rep_name,
                "queue": current_route.assigned_queue,
                "priority": current_route.priority,
                "manual_review": current_route.manual_review,
            }
            if current_route
            else None
        ),
        "outcome": (
            {
                "stage": outcome.pipeline_stage,
                "conversion_type": outcome.conversion_type,
                "closed_revenue": (
                    float(outcome.closed_revenue)
                    if outcome.closed_revenue is not None
                    else None
                ),
                "currency": outcome.currency,
                "closed_at": _iso(outcome.closed_at),
            }
            if outcome
            else None
        ),
    }


def _event_summary(event: PipelineEventORM) -> dict[str, Any]:
    details = event.details if isinstance(event.details, dict) else {}
    allowed = (
        "direction",
        "sender",
        "recipients",
        "subject",
        "body",
        "note",
        "meeting_title",
        "title",
        "start",
        "start_time",
        "end",
        "status",
        "provider",
        "stage",
        "timezone",
    )
    selected: dict[str, Any] = {}
    for key in allowed:
        value = details.get(key)
        if isinstance(value, str):
            selected[key] = _short(value, 700)
        elif isinstance(value, (int, float, bool)):
            selected[key] = value
    return {
        "lead_id": str(event.lead_id),
        "kind": event.stage,
        "status": event.status,
        "created_at": _iso(event.created_at),
        "details": selected,
    }


def _call_summary(call: CallRecordORM) -> dict[str, Any]:
    insights = call.insights if isinstance(call.insights, dict) else {}
    allowed_insights = (
        "summary",
        "sentiment",
        "topics",
        "action_items",
        "outcome",
        "duration_seconds",
    )
    selected_insights: dict[str, Any] = {}
    for key in allowed_insights:
        value = insights.get(key)
        if isinstance(value, str):
            selected_insights[key] = _short(value, 500)
        elif isinstance(value, (int, float, bool)):
            selected_insights[key] = value
        elif isinstance(value, list):
            selected_insights[key] = [
                _short(item, 250) for item in value[:5] if isinstance(item, str)
            ]
    return {
        "lead_id": str(call.lead_id) if call.lead_id else None,
        "provider": call.provider,
        "direction": call.direction,
        "status": call.status,
        "started_at": _iso(call.started_at),
        "duration_seconds": call.duration_seconds,
        "message": _short(call.message, 500),
        "transcript_excerpt": _short(call.transcript, 900),
        "insights": selected_insights,
    }


def build_workspace_record_context(
    session: Session,
    workspace_id: UUID,
    query: str,
    *,
    confirm_similar_lead: bool = False,
) -> dict[str, Any]:
    """Retrieve workspace-scoped context and all close lead-name candidates."""
    query = query[:3000]
    terms = _terms(query)
    folded_query = query.casefold()
    wants_leads = any(word in folded_query for word in ("lead", "prospect", "pipeline"))
    wants_contacts = any(word in folded_query for word in ("contact", "account", "company"))
    wants_calls = any(
        word in folded_query
        for word in ("call", "dial", "phone", "recording", "transcript")
    )
    wants_emails = any(
        word in folded_query
        for word in ("email", "message", "gmail", "inbox", "sent", "follow-up")
    )
    wants_meetings = any(
        word in folded_query for word in ("meeting", "calendar", "appointment", "booking")
    )
    wants_notes = any(
        word in folded_query for word in ("note", "progress", "activity", "history")
    )

    counts = {
        "leads": session.scalar(
            select(func.count()).select_from(LeadORM).where(
                LeadORM.workspace_id == workspace_id
            )
        )
        or 0,
        "contacts": session.scalar(
            select(func.count()).select_from(ContactORM).where(
                ContactORM.workspace_id == workspace_id,
                ContactORM.merged_into_id.is_(None),
            )
        )
        or 0,
        "companies": session.scalar(
            select(func.count()).select_from(CompanyORM).where(
                CompanyORM.workspace_id == workspace_id
            )
        )
        or 0,
        "calls": session.scalar(
            select(func.count()).select_from(CallRecordORM).where(
                CallRecordORM.workspace_id == workspace_id
            )
        )
        or 0,
        "emails": session.scalar(
            select(func.count()).select_from(PipelineEventORM).where(
                PipelineEventORM.workspace_id == workspace_id,
                PipelineEventORM.stage == "gmail_message",
            )
        )
        or 0,
        "meetings": session.scalar(
            select(func.count()).select_from(PipelineEventORM).where(
                PipelineEventORM.workspace_id == workspace_id,
                PipelineEventORM.stage == "meeting_scheduled",
            )
        )
        or 0,
    }

    lead_columns = (
        LeadORM.full_name,
        LeadORM.email,
        LeadORM.company_name,
        LeadORM.job_title,
        LeadORM.industry,
        LeadORM.product_or_service,
        LeadORM.message,
        LeadORM.source,
    )
    leads: list[LeadORM] = []
    if terms:
        leads = list(
            session.scalars(
                select(LeadORM)
                .where(
                    LeadORM.workspace_id == workspace_id,
                    _text_filter(lead_columns, terms),
                )
                .order_by(LeadORM.received_at.desc())
                .limit(8)
            )
        )
    if not leads and wants_leads and not terms:
        leads = list(
            session.scalars(
                select(LeadORM)
                .where(LeadORM.workspace_id == workspace_id)
                .order_by(LeadORM.received_at.desc())
                .limit(8)
            )
        )

    similar_leads = (
        _similar_workspace_leads(session, workspace_id, terms)
        if terms and (wants_leads or wants_calls or wants_emails or wants_meetings or wants_notes)
        else []
    )
    similar_ids = {lead["id"] for lead in similar_leads}
    if similar_ids:
        leads = [lead for lead in leads if str(lead.id) not in similar_ids]

    lead_match_confirmed = False
    if confirm_similar_lead and len(similar_leads) == 1:
        confirmed_id = UUID(similar_leads[0]["id"])
        confirmed_lead = session.scalar(
            select(LeadORM).where(
                LeadORM.id == confirmed_id,
                LeadORM.workspace_id == workspace_id,
            )
        )
        if confirmed_lead is not None:
            leads = [confirmed_lead]
            similar_leads = []
            lead_match_confirmed = True
    elif confirm_similar_lead:
        # A yes only confirms a still-unique candidate; do not fall back to an
        # unrelated partial text hit if the candidate disappeared or became ambiguous.
        leads = []

    company_columns = (
        CompanyORM.name,
        CompanyORM.domain,
        CompanyORM.website,
        CompanyORM.industry,
        cast(CompanyORM.profile, Text),
    )
    companies: list[CompanyORM] = []
    if terms:
        companies = list(
            session.scalars(
                select(CompanyORM)
                .where(
                    CompanyORM.workspace_id == workspace_id,
                    _text_filter(company_columns, terms),
                )
                .order_by(CompanyORM.updated_at.desc())
                .limit(6)
            )
        )
    if not companies and wants_contacts and not terms:
        companies = list(
            session.scalars(
                select(CompanyORM)
                .where(CompanyORM.workspace_id == workspace_id)
                .order_by(CompanyORM.updated_at.desc())
                .limit(6)
            )
        )

    contact_columns = (ContactORM.name, ContactORM.email, ContactORM.phone, ContactORM.job_title)
    contacts: list[ContactORM] = []
    if terms:
        contact_match = _text_filter(contact_columns, terms)
        if companies:
            contact_match = or_(
                contact_match,
                ContactORM.company_id.in_([company.id for company in companies]),
            )
        contacts = list(
            session.scalars(
                select(ContactORM)
                .where(
                    ContactORM.workspace_id == workspace_id,
                    ContactORM.merged_into_id.is_(None),
                    contact_match,
                )
                .order_by(ContactORM.updated_at.desc())
                .limit(6)
            )
        )
    if not contacts and wants_contacts and not terms:
        contacts = list(
            session.scalars(
                select(ContactORM)
                .where(
                    ContactORM.workspace_id == workspace_id,
                    ContactORM.merged_into_id.is_(None),
                )
                .order_by(ContactORM.updated_at.desc())
                .limit(6)
            )
        )

    lead_ids = {lead.id for lead in leads}
    if contacts:
        lead_ids.update(
            session.scalars(
                select(LeadContactORM.lead_id).where(
                    LeadContactORM.contact_id.in_([contact.id for contact in contacts])
                )
            )
        )
    if lead_ids and len(leads) < 8:
        related = list(
            session.scalars(
                select(LeadORM)
                .where(
                    LeadORM.workspace_id == workspace_id,
                    LeadORM.id.in_(lead_ids),
                )
                .order_by(LeadORM.received_at.desc())
            )
        )
        by_id = {lead.id: lead for lead in leads}
        by_id.update({lead.id: lead for lead in related})
        leads = list(by_id.values())[:8]
        lead_ids = {lead.id for lead in leads}

    event_rows: list[PipelineEventORM] = []
    if lead_ids:
        event_rows = list(
            session.scalars(
                select(PipelineEventORM)
                .where(
                    PipelineEventORM.workspace_id == workspace_id,
                    PipelineEventORM.lead_id.in_(lead_ids),
                )
                .order_by(PipelineEventORM.created_at.desc())
                .limit(60)
            )
        )
    if terms:
        matching_events = list(
            session.scalars(
                select(PipelineEventORM)
                .where(
                    PipelineEventORM.workspace_id == workspace_id,
                    _text_filter((cast(PipelineEventORM.details, Text),), terms),
                )
                .order_by(PipelineEventORM.created_at.desc())
                .limit(30)
            )
        )
        seen_event_ids = {event.id for event in event_rows}
        event_rows.extend(event for event in matching_events if event.id not in seen_event_ids)
    elif not lead_ids and (wants_emails or wants_meetings or wants_notes):
        event_rows = list(
            session.scalars(
                select(PipelineEventORM)
                .where(PipelineEventORM.workspace_id == workspace_id)
                .order_by(PipelineEventORM.created_at.desc())
                .limit(30)
            )
        )
    elif wants_leads:
        event_rows = []

    note_columns = (LeadNoteORM.body, LeadNoteORM.author_id)
    note_rows: list[LeadNoteORM] = []
    if lead_ids:
        note_rows = list(
            session.scalars(
                select(LeadNoteORM)
                .where(
                    LeadNoteORM.workspace_id == workspace_id,
                    LeadNoteORM.lead_id.in_(lead_ids),
                )
                .order_by(LeadNoteORM.updated_at.desc())
                .limit(12)
            )
        )
    elif terms:
        note_rows = list(
            session.scalars(
                select(LeadNoteORM)
                .where(
                    LeadNoteORM.workspace_id == workspace_id,
                    _text_filter(note_columns, terms),
                )
                .order_by(LeadNoteORM.updated_at.desc())
                .limit(8)
            )
        )
    elif wants_notes:
        note_rows = list(
            session.scalars(
                select(LeadNoteORM)
                .where(LeadNoteORM.workspace_id == workspace_id)
                .order_by(LeadNoteORM.updated_at.desc())
                .limit(8)
            )
        )

    call_columns = (
        CallRecordORM.message,
        CallRecordORM.transcript,
        CallRecordORM.provider,
        cast(CallRecordORM.insights, Text),
    )
    call_rows: list[CallRecordORM] = []
    if lead_ids:
        call_rows = list(
            session.scalars(
                select(CallRecordORM)
                .where(
                    CallRecordORM.workspace_id == workspace_id,
                    CallRecordORM.lead_id.in_(lead_ids),
                )
                .order_by(CallRecordORM.created_at.desc())
                .limit(8)
            )
        )
    if terms:
        matching_calls = list(
            session.scalars(
                select(CallRecordORM)
                .where(
                    CallRecordORM.workspace_id == workspace_id,
                    _text_filter(call_columns, terms),
                )
                .order_by(CallRecordORM.created_at.desc())
                .limit(8)
            )
        )
        seen_call_ids = {call.id for call in call_rows}
        call_rows.extend(call for call in matching_calls if call.id not in seen_call_ids)
    elif not lead_ids and wants_calls:
        call_rows = list(
            session.scalars(
                select(CallRecordORM)
                .where(CallRecordORM.workspace_id == workspace_id)
                .order_by(CallRecordORM.created_at.desc())
                .limit(8)
            )
        )

    follow_up_rows: list[LeadFollowUpEmailORM] = []
    follow_up_columns = (
        LeadFollowUpEmailORM.recipient,
        LeadFollowUpEmailORM.subject,
        LeadFollowUpEmailORM.body,
    )
    if lead_ids:
        follow_up_rows = list(
            session.scalars(
                select(LeadFollowUpEmailORM)
                .where(
                    LeadFollowUpEmailORM.workspace_id == workspace_id,
                    LeadFollowUpEmailORM.lead_id.in_(lead_ids),
                )
                .order_by(LeadFollowUpEmailORM.created_at.desc())
                .limit(6)
            )
        )
    elif terms and (wants_emails or not leads):
        follow_up_rows = list(
            session.scalars(
                select(LeadFollowUpEmailORM)
                .where(
                    LeadFollowUpEmailORM.workspace_id == workspace_id,
                    _text_filter(follow_up_columns, terms),
                )
                .order_by(LeadFollowUpEmailORM.created_at.desc())
                .limit(6)
            )
        )

    company_ids = {contact.company_id for contact in contacts if contact.company_id}
    company_ids.update(company.id for company in companies)
    related_companies = list(
        session.scalars(
            select(CompanyORM)
            .where(
                CompanyORM.workspace_id == workspace_id,
                CompanyORM.id.in_(company_ids),
            )
        )
    ) if company_ids else []
    companies_by_id = {company.id: company for company in related_companies}
    contact_summaries = []
    for contact in contacts[:6]:
        company = companies_by_id.get(contact.company_id)
        contact_summaries.append(
            {
                "name": contact.name,
                "email": contact.email,
                "phone": contact.phone,
                "job_title": contact.job_title,
                "company": (
                    {
                        "name": company.name,
                        "domain": company.domain,
                        "industry": company.industry,
                        "profile": _short(json.dumps(company.profile, ensure_ascii=False), 500),
                    }
                    if company
                    else None
                ),
            }
        )

    company_summaries = [
        {
            "name": company.name,
            "domain": company.domain,
            "website": company.website,
            "industry": company.industry,
            "profile": _short(json.dumps(company.profile, ensure_ascii=False), 700),
        }
        for company in companies[:6]
    ]

    context = {
        "query": query,
        "search_terms": terms,
        "record_counts": counts,
        "matched_leads": [_lead_summary(lead) for lead in leads[:8]],
        "similar_leads": similar_leads,
        "lead_match_confirmed": lead_match_confirmed,
        "matched_contacts": contact_summaries,
        "matched_companies": company_summaries,
        "recent_emails_and_meetings": [
            _event_summary(event)
            for event in event_rows[:16]
            if event.stage in {"gmail_message", "meeting_scheduled", "manual_progress"}
        ],
        "lead_notes": [
            {
                "lead_id": str(note.lead_id),
                "body": _short(note.body, 600),
                "updated_at": _iso(note.updated_at),
            }
            for note in note_rows[:8]
        ],
        "calls": [_call_summary(call) for call in call_rows[:6]],
        "saved_follow_up_emails": [
            {
                "lead_id": str(email.lead_id),
                "recipient": email.recipient,
                "subject": email.subject,
                "body": _short(email.body, 600),
                "status": email.status,
                "created_at": _iso(email.created_at),
            }
            for email in follow_up_rows[:4]
        ],
        "retrieval_note": (
            "These are bounded matches from records already stored in this Corral workspace, "
            "not a live search of complete external provider archives. "
            "similar_leads are tentative name matches and are not selected records until the "
            "user confirms one. "
            "They may be incomplete; do not claim a full history unless the returned counts "
            "and items support it. Ask for a narrower name, topic, or date range when needed."
        ),
    }
    # Preserve every close-name option; fuzzy matches are handled deterministically
    # by the chat route and are not sent to the reasoning provider.
    while len(json.dumps(context, ensure_ascii=False, default=str)) > 10_000:
        if context["recent_emails_and_meetings"]:
            context["recent_emails_and_meetings"].pop()
        elif context["calls"]:
            context["calls"].pop()
        elif context["lead_notes"]:
            context["lead_notes"].pop()
        elif context["matched_leads"]:
            context["matched_leads"].pop()
        elif context["saved_follow_up_emails"]:
            context["saved_follow_up_emails"].pop()
        elif context["matched_contacts"]:
            context["matched_contacts"].pop()
        elif context["matched_companies"]:
            context["matched_companies"].pop()
        else:
            break
    return context
