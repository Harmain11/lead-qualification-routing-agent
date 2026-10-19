"""Rep-confirmed calls and bounded provider history, never an automatic dial campaign."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission, require_permissions
from lead_agent.api.routes.collaboration import owned_lead
from lead_agent.application.governance import (
    OutreachBlocked,
    assert_outreach_allowed,
    assert_residency,
    audit_operation,
    operations_policy,
    outreach_allowed,
)
from lead_agent.domain.operations import StrictModel
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.communications import DIALERS, CommunicationsClient, phone
from lead_agent.infrastructure.db.calling import CallRecordORM
from lead_agent.infrastructure.db.models import LeadORM, ProviderConnectionORM, RoutingDecisionORM
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.provider_lifecycle import (
    WorkspaceAuthorizedConnectionResolver,
    get_workspace_connection_resolver,
)
from lead_agent.infrastructure.rate_limit import rate_limit_dependency

router = APIRouter(prefix="/v1/calling", tags=["calls and voice activity"])


class CallRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    lead_id: UUID | None = None
    connection_id: UUID
    provider: str
    external_id: str | None
    status: str
    direction: str
    customer_phone: str
    started_at: datetime | None
    duration_seconds: int | None
    transcript: str | None
    recording_url: str | None
    insights: dict[str, Any] | None
    message: str | None
    created_at: datetime
    updated_at: datetime


class StartCall(StrictModel):
    connection_id: UUID
    request_id: UUID
    confirmed: bool
    rep_phone: str | None = Field(default=None, max_length=32)


class DirectStartCall(StrictModel):
    connection_id: UUID
    request_id: UUID
    phone_number: str = Field(min_length=8, max_length=32)
    confirmed: bool
    consent_confirmed: bool
    rep_phone: str | None = Field(default=None, max_length=32)


class SyncCalls(StrictModel):
    connection_id: UUID


def _connection(session: Session, workspace_id: UUID, connection_id: UUID) -> ProviderConnectionORM:
    row = session.get(ProviderConnectionORM, connection_id)
    if row is None or row.workspace_id != workspace_id or row.provider_type not in DIALERS:
        raise HTTPException(404, "Calling connection not found")
    if not row.is_active or row.status != "verified":
        raise HTTPException(409, "Verify this calling connection in Integrations before use.")
    return row


def _client(
    session: Session,
    workspace: WorkspaceContext,
    connection: ProviderConnectionORM,
    resolver: WorkspaceAuthorizedConnectionResolver,
) -> CommunicationsClient:
    try:
        resolved = resolver.resolve_connection(
            session, workspace.workspace_id, connection.id, actor_id=workspace.actor_id
        )
        if resolved is None:
            raise HTTPException(
                409, "This calling connection is unavailable. Reconnect and verify it."
            )
        return CommunicationsClient(resolved.provider, resolved.credentials)
    except ProviderOperationError as error:
        raise HTTPException(409, str(error)) from None


def _target(lead: LeadORM) -> str:
    try:
        return phone(lead.phone or "")
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@router.get("/cloudtalk/recent")
def recent_cloudtalk_calls(
    page: int = Query(default=1, ge=1),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> Any:
    connections = list(
        session.scalars(
            select(ProviderConnectionORM)
            .where(
                ProviderConnectionORM.workspace_id == workspace.workspace_id,
                ProviderConnectionORM.provider_type == "cloudtalk",
                ProviderConnectionORM.status == "verified",
                ProviderConnectionORM.is_active.is_(True),
            )
            .order_by(ProviderConnectionORM.account_name)
        )
    )
    if not connections:
        return {"connections": [], "records": [], "page": page, "has_more": False}
    connection = connections[0]
    client = _client(session, workspace, connection, resolver)
    try:
        recent = client.recent_calls(None, page=page)
    except (ProviderOperationError, ValueError) as error:
        status_code = (
            429 if isinstance(error, ProviderOperationError) and error.status_code == 429 else 502
        )
        raise HTTPException(status_code, str(error)) from None

    leads = list(
        session.execute(
            select(
                LeadORM.id,
                LeadORM.full_name,
                LeadORM.company_name,
                LeadORM.phone,
                RoutingDecisionORM.assigned_rep_name,
            )
            .outerjoin(
                RoutingDecisionORM,
                and_(
                    RoutingDecisionORM.lead_id == LeadORM.id,
                    RoutingDecisionORM.is_current.is_(True),
                ),
            )
            .where(LeadORM.workspace_id == workspace.workspace_id, LeadORM.phone.is_not(None))
        )
    )
    by_phone = {
        "".join(character for character in (phone_number or "") if character.isdigit()):
        (str(lead_id), full_name, company_name, representative)
        for lead_id, full_name, company_name, phone_number, representative in leads
    }
    records = []
    for call in recent:
        digits = "".join(character for character in call.customer_phone if character.isdigit())
        lead = by_phone.get(digits)
        record = call.model_dump(mode="json")
        call_created_at = (
            call.started_at.isoformat() if call.started_at else datetime.now(UTC).isoformat()
        )
        record.update(
            id=call.external_id,
            provider="cloudtalk",
            created_at=call_created_at,
            message=None,
            lead_id=lead[0] if lead else None,
            lead_name=lead[1] if lead else None,
            company_name=lead[2] if lead else None,
            representative_name=lead[3] if lead else None,
        )
        records.append(record)
    return {
        "connections": [
            {"id": connection.id, "provider": "cloudtalk", "account_name": connection.account_name}
        ],
        "records": records,
        "page": page,
        "has_more": len(recent) == 100,
    }


@router.get("/cloudtalk/recent/{call_id}/details")
def cloudtalk_call_details(
    call_id: str,
    page: int = Query(default=1, ge=1),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> Any:
    connection = session.scalar(
        select(ProviderConnectionORM)
        .where(
            ProviderConnectionORM.workspace_id == workspace.workspace_id,
            ProviderConnectionORM.provider_type == "cloudtalk",
            ProviderConnectionORM.status == "verified",
            ProviderConnectionORM.is_active.is_(True),
        )
        .order_by(ProviderConnectionORM.account_name)
    )
    if connection is None:
        raise HTTPException(404, "CloudTalk connection not found")
    client = _client(session, workspace, connection, resolver)
    try:
        calls = client.recent_calls(None, page=page, details_for_call_id=call_id)
    except (ProviderOperationError, ValueError) as error:
        status_code = (
            429 if isinstance(error, ProviderOperationError) and error.status_code == 429 else 502
        )
        raise HTTPException(status_code, str(error)) from None
    call = next((item for item in calls if item.external_id == call_id), None)
    if call is None:
        raise HTTPException(404, "CloudTalk call not found on this history page")
    return call.model_dump(mode="json")


@router.get("/leads/{lead_id}")
def call_activity(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> Any:
    lead = owned_lead(session, workspace.workspace_id, lead_id)
    connections = list(
        session.scalars(
            select(ProviderConnectionORM)
            .where(
                ProviderConnectionORM.workspace_id == workspace.workspace_id,
                ProviderConnectionORM.provider_type.in_(DIALERS),
                ProviderConnectionORM.status == "verified",
                ProviderConnectionORM.is_active.is_(True),
            )
            .order_by(ProviderConnectionORM.account_name)
        )
    )
    records = list(
        session.scalars(
            select(CallRecordORM)
            .where(
                CallRecordORM.workspace_id == workspace.workspace_id,
                CallRecordORM.lead_id == lead_id,
            )
            .order_by(
                func.coalesce(CallRecordORM.started_at, CallRecordORM.created_at).desc(),
                CallRecordORM.created_at.desc(),
            )
            .limit(50)
        )
    )
    allowed, reason = outreach_allowed(session, workspace.workspace_id, lead.email, "call")
    public_records = []
    for record in records:
        item = CallRecord.model_validate(record).model_dump()
        item.update(transcript=None, recording_url=None, insights=None)
        public_records.append(item)
    return {
        "connections": [
            {"id": row.id, "provider": row.provider_type, "account_name": row.account_name}
            for row in connections
        ],
        "records": public_records,
        "outreach_allowed": allowed,
        "outreach_reason": reason,
    }


@router.get("/leads/{lead_id}/calls/{call_id}/details")
def call_activity_details(
    lead_id: UUID,
    call_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> Any:
    lead = owned_lead(session, workspace.workspace_id, lead_id)
    record = session.scalar(
        select(CallRecordORM).where(
            CallRecordORM.id == call_id,
            CallRecordORM.workspace_id == workspace.workspace_id,
            CallRecordORM.lead_id == lead_id,
        )
    )
    if record is None:
        raise HTTPException(404, "Call activity not found")
    if record.provider == "cloudtalk" and record.external_id:
        connection = _connection(session, workspace.workspace_id, record.connection_id)
        client = _client(session, workspace, connection, resolver)
        try:
            snapshots = client.recent_calls(
                _target(lead), details_for_call_id=record.external_id
            )
        except ProviderOperationError as error:
            raise HTTPException(429 if error.status_code == 429 else 502, str(error)) from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(502, "Call details could not be loaded from CloudTalk.") from None
        finally:
            client.close()
        snapshot = next(
            (item for item in snapshots if item.external_id == record.external_id), None
        )
        if snapshot is None:
            raise HTTPException(404, "CloudTalk call details were not found.")
        record.transcript = snapshot.transcript
        record.recording_url = snapshot.recording_url
        record.insights = snapshot.insights
        record.updated_at = datetime.now(UTC)
        session.commit()
    return CallRecord.model_validate(record)


@router.post(
    "/leads/{lead_id}/start",
    response_model=CallRecord,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def start_call(
    lead_id: UUID,
    request: StartCall,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(
        require_permissions("leads.view", "leads.edit", "agents.execute")
    ),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> CallRecordORM:
    lead = owned_lead(session, workspace.workspace_id, lead_id)
    if not request.confirmed:
        raise HTTPException(422, "Confirm this individual call before dialing.")
    target = _target(lead)
    try:
        rep = phone(request.rep_phone) if request.rep_phone else None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "lead_id": str(lead_id),
                "connection_id": str(request.connection_id),
                "target": target,
                "rep_phone": rep,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()

    def prior() -> CallRecordORM | None:
        row = session.scalar(
            select(CallRecordORM).where(
                CallRecordORM.workspace_id == workspace.workspace_id,
                CallRecordORM.request_id == request.request_id,
            )
        )
        if row and row.request_fingerprint != fingerprint:
            raise HTTPException(
                409, "This call request ID was already used with different details."
            )
        return row

    previous = prior()
    if previous:
        return previous
    try:
        assert_outreach_allowed(session, workspace.workspace_id, lead.email, "call")
    except OutreachBlocked as error:
        raise HTTPException(409, str(error)) from None
    connection = _connection(session, workspace.workspace_id, request.connection_id)
    client = _client(session, workspace, connection, resolver)
    try:
        if connection.provider_type == "twilio" and (
            not rep or rep in {target, client.credentials.get("from_number")}
        ):
            raise HTTPException(
                422, "Enter your rep number, different from the lead and caller-ID numbers."
            )
        if (
            connection.provider_type == "ringcentral"
            and (rep or client.credentials.get("from_number")) == target
        ):
            raise HTTPException(422, "Your rep number must differ from the lead number.")
        now = datetime.now(UTC)
        row = CallRecordORM(
            workspace_id=workspace.workspace_id,
            lead_id=lead_id,
            connection_id=connection.id,
            provider=connection.provider_type,
            request_id=request.request_id,
            request_fingerprint=fingerprint,
            status="submitting",
            customer_phone=target,
            actor_id=workspace.actor_id,
            started_at=now,
            message="Submission started. Check your provider if its final receipt is unavailable.",
        )
        session.add(row)
        try:
            # Durable claim before the network boundary. Concurrent requests or
            # a crashed worker cannot repeat this UUID and make a second call.
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = prior()
            if existing:
                return existing
            raise HTTPException(409, "The call request changed; refresh its status.") from None
        try:
            row.external_id, row.status = client.start_call(target, rep, str(request.request_id))
            row.message = (
                "Call request accepted. Sync history after the call to retrieve its result."
            )
        except ProviderOperationError as error:
            row.status = (
                "uncertain"
                if error.category
                in {"timeout", "temporarily_unavailable", "unknown", "malformed_response"}
                else "failed"
            )
            row.message = (
                (
                    "The provider may have accepted this call. Check its call log before "
                    "placing another call."
                )
                if row.status == "uncertain"
                else str(error)
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            row.status = "uncertain"
            row.message = (
                "No reliable call receipt was returned. Check the provider "
                "before placing another call."
            )
        row.updated_at = datetime.now(UTC)
        audit_operation(
            session,
            workspace.workspace_id,
            workspace.actor_id,
            "call.requested",
            {
                "lead_id": str(lead_id),
                "connection_id": str(connection.id),
                "request_id": str(request.request_id),
                "provider": row.provider,
                "status": row.status,
            },
        )
        session.commit()
        return row
    finally:
        client.close()


@router.post(
    "/direct/start",
    response_model=CallRecord,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def start_direct_call(
    request: DirectStartCall,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("agents.execute")),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> CallRecordORM:
    if not request.confirmed:
        raise HTTPException(422, "Confirm this individual call before dialing.")
    if not request.consent_confirmed:
        raise HTTPException(422, "Confirm that you have permission to call this number.")
    try:
        target = phone(request.phone_number)
        rep = phone(request.rep_phone) if request.rep_phone else None
    except ValueError as error:
        raise HTTPException(422, str(error)) from None

    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "direct": True,
                "connection_id": str(request.connection_id),
                "target": target,
                "rep_phone": rep,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()

    def prior() -> CallRecordORM | None:
        row = session.scalar(
            select(CallRecordORM).where(
                CallRecordORM.workspace_id == workspace.workspace_id,
                CallRecordORM.request_id == request.request_id,
            )
        )
        if row and row.request_fingerprint != fingerprint:
            raise HTTPException(
                409, "This call request ID was already used with different details."
            )
        return row

    previous = prior()
    if previous:
        return previous

    target_digits = "".join(character for character in target if character.isdigit())
    matching_leads = [
        lead
        for lead in session.scalars(
            select(LeadORM).where(
                LeadORM.workspace_id == workspace.workspace_id,
                LeadORM.phone.is_not(None),
            )
        )
        if "".join(character for character in (lead.phone or "") if character.isdigit())
        == target_digits
    ]
    policy = operations_policy(session, workspace.workspace_id)
    try:
        assert_residency(session, workspace.workspace_id)
        if not matching_leads and policy.require_recorded_consent:
            raise OutreachBlocked(
                "This workspace requires recorded call consent. Add the number to a lead and record permission before calling."
            )
        for lead in matching_leads:
            if lead.email:
                assert_outreach_allowed(session, workspace.workspace_id, lead.email, "call")
            elif policy.require_recorded_consent:
                raise OutreachBlocked(
                    "Record call permission for this lead before dialing its number."
                )
    except OutreachBlocked as error:
        raise HTTPException(409, str(error)) from None

    # Direct calls stay unlinked until a user explicitly creates a lead.
    lead_id = None
    connection = _connection(session, workspace.workspace_id, request.connection_id)
    client = _client(session, workspace, connection, resolver)
    try:
        if connection.provider_type == "twilio" and (
            not rep or rep in {target, client.credentials.get("from_number")}
        ):
            raise HTTPException(
                422, "Enter your rep number, different from the number being called and caller ID."
            )
        if connection.provider_type == "ringcentral":
            caller = rep or client.credentials.get("from_number")
            if caller == target:
                raise HTTPException(422, "Your rep number must differ from the number being called.")

        now = datetime.now(UTC)
        row = CallRecordORM(
            workspace_id=workspace.workspace_id,
            lead_id=lead_id,
            connection_id=connection.id,
            provider=connection.provider_type,
            request_id=request.request_id,
            request_fingerprint=fingerprint,
            status="submitting",
            customer_phone=target,
            actor_id=workspace.actor_id,
            started_at=now,
            message="Submission started. Check your provider if its final receipt is unavailable.",
        )
        session.add(row)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = prior()
            if existing:
                return existing
            raise HTTPException(409, "The call request changed; refresh its status.") from None
        try:
            row.external_id, row.status = client.start_call(target, rep, str(request.request_id))
            row.message = (
                "Call request accepted. Add a lead afterward if this number becomes a prospect."
            )
        except ProviderOperationError as error:
            row.status = (
                "uncertain"
                if error.category
                in {"timeout", "temporarily_unavailable", "unknown", "malformed_response"}
                else "failed"
            )
            row.message = (
                "The provider may have accepted this call. Check its call log before placing another call."
                if row.status == "uncertain"
                else str(error)
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            row.status = "uncertain"
            row.message = (
                "No reliable call receipt was returned. Check the provider before placing another call."
            )
        row.updated_at = datetime.now(UTC)
        audit_operation(
            session,
            workspace.workspace_id,
            workspace.actor_id,
            "call.direct_requested",
            {
                "connection_id": str(connection.id),
                "request_id": str(request.request_id),
                "provider": row.provider,
                "status": row.status,
            },
        )
        session.commit()
        return row
    finally:
        client.close()


@router.get("/direct/recent")
def recent_direct_calls(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("leads.view")),
) -> dict[str, list[dict[str, object]]]:
    records = list(
        session.scalars(
            select(CallRecordORM)
            .where(
                CallRecordORM.workspace_id == workspace.workspace_id,
                CallRecordORM.lead_id.is_(None),
                CallRecordORM.request_id.is_not(None),
            )
            .order_by(
                func.coalesce(CallRecordORM.started_at, CallRecordORM.created_at).desc(),
                CallRecordORM.created_at.desc(),
            )
            .limit(50)
        )
    )
    leads = list(
        session.execute(
            select(
                LeadORM.id,
                LeadORM.full_name,
                LeadORM.company_name,
                LeadORM.phone,
            ).where(
                LeadORM.workspace_id == workspace.workspace_id,
                LeadORM.phone.is_not(None),
            )
        )
    )
    by_phone = {
        "".join(character for character in (phone_number or "") if character.isdigit()):
        (str(lead_id), full_name, company_name)
        for lead_id, full_name, company_name, phone_number in leads
    }
    payload: list[dict[str, object]] = []
    for row in records:
        item = CallRecord.model_validate(row).model_dump(mode="json")
        match = by_phone.get(
            "".join(character for character in row.customer_phone if character.isdigit())
        )
        item.update(
            lead_id=match[0] if match else None,
            lead_name=match[1] if match else None,
            company_name=match[2] if match else None,
        )
        payload.append(item)
    return {"records": payload}


@router.post(
    "/leads/{lead_id}/sync", dependencies=[Depends(rate_limit_dependency("provider_actions"))]
)
def sync_calls(
    lead_id: UUID,
    request: SyncCalls,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permissions("leads.view", "leads.edit")),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> Any:
    lead = owned_lead(session, workspace.workspace_id, lead_id)
    connection = _connection(session, workspace.workspace_id, request.connection_id)
    client = _client(session, workspace, connection, resolver)
    try:
        snapshots = client.recent_calls(_target(lead))
    except ProviderOperationError as error:
        status_code = 429 if error.status_code == 429 else 502
        raise HTTPException(status_code, str(error)) from None
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(
            502, "Call history could not be read. Check the provider setup and try again."
        ) from None
    finally:
        client.close()
    refreshed = 0
    for snapshot in snapshots:
        with session.begin_nested():
            existing = session.scalar(
                select(CallRecordORM).where(
                    CallRecordORM.workspace_id == workspace.workspace_id,
                    CallRecordORM.connection_id == connection.id,
                    CallRecordORM.external_id == snapshot.external_id,
                )
            )
            # A provider ID can only attach to its original lead; never silently
            # move another contact's transcript when two leads share a number.
            if existing and existing.lead_id != lead_id:
                continue
            if existing is None:
                existing = CallRecordORM(
                    workspace_id=workspace.workspace_id,
                    lead_id=lead_id,
                    connection_id=connection.id,
                    provider=connection.provider_type,
                )
                session.add(existing)
            for key, value in snapshot.model_dump().items():
                setattr(existing, key, value)
            existing.updated_at = datetime.now(UTC)
            existing.message = "Imported from provider call history."
            try:
                session.flush()
            except IntegrityError:
                # The other sync owns the receipt. Roll back this savepoint only.
                raise HTTPException(
                    409, "Another rep synced this call. Refresh the history."
                ) from None
            refreshed += 1
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "call.history_synced",
        {"lead_id": str(lead_id), "provider": connection.provider_type, "records": refreshed},
    )
    session.commit()
    return {
        "refreshed": refreshed,
        "message": "Recent calls matching this lead number were refreshed.",
    }
