"""Independent people/company records and explicit duplicate review."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.domain.operations import email_key
from lead_agent.infrastructure.db.models import LeadORM
from lead_agent.infrastructure.db.operations import CompanyORM, ContactORM, LeadContactORM

_PUBLIC_MAIL = {
    "gmail.com",
    "outlook.com",
    "hotmail.com",
    "yahoo.com",
    "icloud.com",
    "live.com",
    "proton.me",
    "protonmail.com",
}


def company_identity(name: str, domain: str | None) -> str:
    return (
        domain.casefold().removeprefix("www.")
        if domain
        else "name:" + re.sub(r"\s+", " ", name.casefold()).strip()
    )


def attach_contact(session: Session, lead: LeadORM, *, relink: bool = False) -> ContactORM:
    linked = session.get(LeadContactORM, lead.id)
    if linked and not relink:
        contact = session.get(ContactORM, linked.contact_id)
        if contact:
            return contact
    try:
        email = email_key(lead.email) if lead.email else None
    except ValueError:
        email = None
    domain = email.split("@")[1] if email else None
    if domain in _PUBLIC_MAIL:
        domain = None
    key = company_identity(lead.company_name, domain)
    company = session.scalar(
        select(CompanyORM).where(
            CompanyORM.workspace_id == lead.workspace_id, CompanyORM.identity_key == key
        )
    )
    if company is None:
        try:
            with session.begin_nested():
                company = CompanyORM(
                    workspace_id=lead.workspace_id,
                    identity_key=key,
                    name=lead.company_name,
                    domain=domain,
                    industry=lead.industry,
                )
                session.add(company)
                session.flush()
        except IntegrityError:
            company = session.scalar(
                select(CompanyORM).where(
                    CompanyORM.workspace_id == lead.workspace_id, CompanyORM.identity_key == key
                )
            )
    if company is None:
        raise RuntimeError("Company identity could not be resolved")
    contact = (
        session.scalar(
            select(ContactORM).where(
                ContactORM.workspace_id == lead.workspace_id, ContactORM.email == email
            )
        )
        if email
        else None
    )
    if contact is None:
        try:
            with session.begin_nested():
                contact = ContactORM(
                    workspace_id=lead.workspace_id,
                    name=lead.full_name,
                    email=email,
                    company_id=company.id,
                    phone=lead.phone,
                    job_title=lead.job_title,
                )
                session.add(contact)
                session.flush()
        except IntegrityError:
            contact = session.scalar(
                select(ContactORM).where(
                    ContactORM.workspace_id == lead.workspace_id, ContactORM.email == email
                )
            )
    if contact is None:
        raise RuntimeError("Contact identity could not be resolved")
    visited: set[UUID] = set()
    while contact.merged_into_id:
        if contact.id in visited:
            raise ValueError("Contact merge history contains a cycle; review the records")
        visited.add(contact.id)
        target = session.get(ContactORM, contact.merged_into_id)
        if target is None or target.workspace_id != lead.workspace_id:
            raise ValueError("Contact merge target is unavailable")
        contact = target
    if linked:
        linked.contact_id = contact.id
        return contact
    try:
        with session.begin_nested():
            session.add(LeadContactORM(lead_id=lead.id, contact_id=contact.id))
            session.flush()
    except IntegrityError:
        linked = session.get(LeadContactORM, lead.id)
        if linked is None:
            raise RuntimeError("Contact link could not be resolved") from None
        contact = session.get(ContactORM, linked.contact_id)
    if contact is None:
        raise RuntimeError("Contact link is unavailable")
    return contact


def duplicate_suggestions(contacts: list[ContactORM]) -> list[dict[str, Any]]:
    # Bounded review candidates; never silently merge people on company name alone.
    suggestions = []
    for index, left in enumerate(contacts):
        for right in contacts[index + 1 :]:
            if left.company_id is None or left.company_id != right.company_id:
                continue
            name_similarity = SequenceMatcher(
                None, left.name.casefold(), right.name.casefold()
            ).ratio()
            same_phone = bool(
                left.phone and re.sub(r"\D", "", left.phone) == re.sub(r"\D", "", right.phone or "")
            )
            if same_phone or name_similarity >= 0.88:
                suggestions.append(
                    {
                        "left_id": left.id,
                        "right_id": right.id,
                        "left_name": left.name,
                        "right_name": right.name,
                        "reason": "Same company and phone"
                        if same_phone
                        else "Same company and similar name",
                        "confidence": 1 if same_phone else round(name_similarity, 2),
                    }
                )
    return suggestions[:100]
