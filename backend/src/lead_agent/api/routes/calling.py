"""Rep-confirmed calls and bounded provider history, never an automatic dial campaign."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.api.data_access import require_workspace_wide_access
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
from lead_agent.application.integration_access import (
    allowed_connection_actions,
    assert_connection_action,
)
from lead_agent.application.lead_access import (
    accessible_lead_ids_query,
    assert_lead_action,
)
from lead_agent.domain.call_outcome import call_outcome
from lead_agent.domain.operations import StrictModel
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.communications import (
    DIALERS,
    CommunicationsClient,
    normalize_lead_phone,
    phone,
)
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


def call_record_for_permissions(
    row: CallRecord | CallRecordORM,
    permissions: set[str] | frozenset[str],
) -> CallRecord:
    record = row if isinstance(row, CallRecord) else CallRecord.model_validate(row)
    return record.model_copy(
        update={
            "customer_phone": (
                record.customer_phone if "fields.contact.view" in permissions else "[redacted]"
            ),
            "transcript": record.transcript if "calls.transcript.view" in permissions else None,
            "recording_url": (
                record.recording_url if "calls.recording.play" in permissions else None
            ),
            "insights": record.insights if "calls.analysis.view" in permissions else None,
            "message": (record.message if "fields.communications.view" in permissions else None),
        }
    )


def _call_snapshot_payload(snapshot: Any, permissions: set[str] | frozenset[str]) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    if "calls.transcript.view" not in permissions:
        payload["transcript"] = None
    if "calls.recording.play" not in permissions:
        payload["recording_url"] = None
    if "calls.analysis.view" not in permissions:
        payload["insights"] = None
    return payload


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


class CloudTalkHandoffRequest(DirectStartCall):
    caller_id: str = Field(min_length=8, max_length=32)


class CloudTalkLeadHandoffRequest(StrictModel):
    connection_id: UUID
    request_id: UUID
    confirmed: bool
    caller_id: str = Field(min_length=8, max_length=32)


class CloudTalkHandoffResponse(StrictModel):
    phone_number: str
    caller_id: str
    handoff_url: str
    message: str


class CloudTalkCallerNumberOptions(StrictModel):
    numbers: list[str]
    default_number: str | None = None


class SyncCalls(StrictModel):
    connection_id: UUID


class AssignCloudTalkCall(StrictModel):
    connection_id: UUID
    lead_id: UUID
    page: int = Field(default=1, ge=1)


class AssignCloudTalkCallResponse(StrictModel):
    call_id: str
    lead_id: UUID


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
    assert_connection_action(session, workspace, connection, "use")
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
        return normalize_lead_phone(lead.phone or "", lead.geography)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


def _validate_direct_call_policy(session: Session, workspace_id: UUID, target: str) -> None:
    target_digits = "".join(character for character in target if character.isdigit())
    matching_leads = [
        lead
        for lead in session.scalars(
            select(LeadORM).where(
                LeadORM.workspace_id == workspace_id,
                LeadORM.phone.is_not(None),
            )
        )
        if "".join(character for character in (lead.phone or "") if character.isdigit())
        == target_digits
    ]
    policy = operations_policy(session, workspace_id)
    try:
        assert_residency(session, workspace_id)
        if not matching_leads and policy.require_recorded_consent:
            raise OutreachBlocked(
                "This workspace requires recorded call consent. Add the number to a lead "
                "and record permission before calling."
            )
        for lead in matching_leads:
            if lead.email:
                assert_outreach_allowed(session, workspace_id, lead.email, "call")
            elif policy.require_recorded_consent:
                raise OutreachBlocked(
                    "Record call permission for this lead before dialing its number."
                )
    except OutreachBlocked as error:
        raise HTTPException(409, str(error)) from None


@router.get(
    "/cloudtalk/numbers",
    response_model=CloudTalkCallerNumberOptions,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def cloudtalk_phone_numbers(
    connection_id: UUID = Query(...),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("dialer.call")),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> CloudTalkCallerNumberOptions:
    connection = _connection(session, workspace.workspace_id, connection_id)
    if connection.provider_type != "cloudtalk":
        raise HTTPException(404, "CloudTalk connection not found")
    client = _client(session, workspace, connection, resolver)
    try:
        numbers, default_number = client.list_cloudtalk_agent_caller_ids()
    except ProviderOperationError as error:
        status_code = 429 if error.status_code == 429 else 502
        raise HTTPException(
            status_code,
            (
                "CloudTalk phone numbers could not be loaded. "
                "Check the verified connection and its access."
            ),
        ) from None
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            502,
            (
                "CloudTalk phone numbers could not be loaded. "
                "Check the verified connection and its access."
            ),
        ) from None
    finally:
        client.close()
    return CloudTalkCallerNumberOptions(numbers=numbers, default_number=default_number)


@router.get("/cloudtalk/recent")
def recent_cloudtalk_calls(
    page: int = Query(default=1, ge=1),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("calls.metadata.view")),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> Any:
    require_workspace_wide_access(workspace)
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
            .where(LeadORM.workspace_id == workspace.workspace_id)
        )
    )
    leads_by_id = {
        str(lead_id): (full_name, company_name, representative)
        for lead_id, full_name, company_name, _, representative in leads
    }
    by_phone = {
        "".join(character for character in (phone_number or "") if character.isdigit()): (
            str(lead_id),
            full_name,
            company_name,
            representative,
        )
        for lead_id, full_name, company_name, phone_number, representative in leads
        if phone_number
    }
    external_ids = [call.external_id for call in recent if call.external_id]
    linked_calls = (
        list(
            session.scalars(
                select(CallRecordORM).where(
                    CallRecordORM.workspace_id == workspace.workspace_id,
                    CallRecordORM.connection_id == connection.id,
                    CallRecordORM.external_id.in_(external_ids),
                )
            )
        )
        if external_ids
        else []
    )
    linked_lead_ids = {
        row.external_id: str(row.lead_id) for row in linked_calls if row.external_id and row.lead_id
    }
    records = []
    for call in recent:
        digits = "".join(character for character in call.customer_phone if character.isdigit())
        lead_id = linked_lead_ids.get(call.external_id)
        lead = leads_by_id.get(lead_id) if lead_id else None
        if lead is None:
            matched = by_phone.get(digits)
            lead_id = matched[0] if matched else None
            lead = matched[1:] if matched else None
        record = _call_snapshot_payload(call, workspace.permissions)
        call_created_at = (
            call.started_at.isoformat() if call.started_at else datetime.now(UTC).isoformat()
        )
        record.update(
            id=call.external_id,
            provider="cloudtalk",
            created_at=call_created_at,
            message=None,
            lead_id=lead_id,
            lead_name=lead[0] if lead else None,
            company_name=lead[1] if lead else None,
            representative_name=lead[2] if lead else None,
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
    workspace: WorkspaceContext = Depends(require_permission("calls.metadata.view")),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> Any:
    require_workspace_wide_access(workspace)
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
    return _call_snapshot_payload(call, workspace.permissions)


@router.post(
    "/cloudtalk/recent/{call_id}/assign",
    response_model=AssignCloudTalkCallResponse,
)
def assign_cloudtalk_call_to_lead(
    call_id: str,
    request: AssignCloudTalkCall,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permissions("dialer.call", "leads.edit")),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> AssignCloudTalkCallResponse:
    require_workspace_wide_access(workspace)
    lead = owned_lead(session, workspace.workspace_id, request.lead_id)
    assert_lead_action(session, workspace, lead, "edit")
    connection = _connection(session, workspace.workspace_id, request.connection_id)
    if connection.provider_type != "cloudtalk":
        raise HTTPException(404, "CloudTalk connection not found")

    client = _client(session, workspace, connection, resolver)
    try:
        snapshots = client.recent_calls(None, page=request.page)
    except ProviderOperationError as error:
        raise HTTPException(429 if error.status_code == 429 else 502, str(error)) from None
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(502, "Call history could not be read from CloudTalk.") from None
    finally:
        client.close()

    snapshot = next((item for item in snapshots if item.external_id == call_id), None)
    if snapshot is None:
        raise HTTPException(404, "CloudTalk call not found on this history page")

    record = session.scalar(
        select(CallRecordORM).where(
            CallRecordORM.workspace_id == workspace.workspace_id,
            CallRecordORM.connection_id == connection.id,
            CallRecordORM.external_id == call_id,
        )
    )
    if record is not None and record.lead_id not in (None, lead.id):
        raise HTTPException(409, "This call is already linked to another lead.")

    if record is None:
        record = CallRecordORM(
            workspace_id=workspace.workspace_id,
            connection_id=connection.id,
            provider="cloudtalk",
            external_id=snapshot.external_id,
            customer_phone=snapshot.customer_phone,
            status=snapshot.status,
            direction=snapshot.direction,
            started_at=snapshot.started_at,
            duration_seconds=snapshot.duration_seconds,
            lead_id=lead.id,
            actor_id=workspace.actor_id,
            message="Linked from CloudTalk call history.",
        )
        session.add(record)
    else:
        record.lead_id = lead.id
        record.customer_phone = snapshot.customer_phone
        record.status = snapshot.status
        record.direction = snapshot.direction
        record.started_at = snapshot.started_at
        record.duration_seconds = snapshot.duration_seconds
        record.updated_at = datetime.now(UTC)
        if not record.message:
            record.message = "Linked from CloudTalk call history."

    # A history fetch may not include AI details; never erase details already stored.
    if snapshot.transcript is not None:
        record.transcript = snapshot.transcript
    if snapshot.recording_url is not None:
        record.recording_url = snapshot.recording_url
    if snapshot.insights is not None:
        record.insights = snapshot.insights

    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "call.assigned_to_lead",
        {"call_id": call_id, "lead_id": str(lead.id), "provider": "cloudtalk"},
    )
    session.commit()
    return AssignCloudTalkCallResponse(call_id=call_id, lead_id=lead.id)


@router.get("/leads/{lead_id}")
def call_activity(
    lead_id: UUID,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permissions("leads.view", "calls.metadata.view")),
) -> Any:
    lead = owned_lead(session, workspace.workspace_id, lead_id)
    assert_lead_action(session, workspace, lead, "view")
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
        public_records.append(
            call_record_for_permissions(record, workspace.permissions).model_dump()
        )
    return {
        "connections": [
            {"id": row.id, "provider": row.provider_type, "account_name": row.account_name}
            for row in connections
            if {"view", "view_status", "use"} & allowed_connection_actions(session, workspace, row)
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
    workspace: WorkspaceContext = Depends(require_permissions("leads.view", "calls.metadata.view")),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> Any:
    lead = owned_lead(session, workspace.workspace_id, lead_id)
    assert_lead_action(session, workspace, lead, "view")
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
            snapshots = client.recent_calls(_target(lead), details_for_call_id=record.external_id)
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
    return call_record_for_permissions(record, workspace.permissions)


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
        require_permissions("leads.view", "leads.edit", "dialer.call")
    ),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> CallRecordORM:
    lead = owned_lead(session, workspace.workspace_id, lead_id)
    assert_lead_action(session, workspace, lead, "edit")
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
    workspace: WorkspaceContext = Depends(require_permission("dialer.call")),
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

    _validate_direct_call_policy(session, workspace.workspace_id, target)

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
                raise HTTPException(
                    422, "Your rep number must differ from the number being called."
                )

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
                "The provider may have accepted this call. "
                "Check its call log before placing another call."
                if row.status == "uncertain"
                else str(error)
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            row.status = "uncertain"
            row.message = (
                "No reliable call receipt was returned. "
                "Check the provider before placing another call."
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


@router.post(
    "/direct/cloudtalk-handoff",
    response_model=CloudTalkHandoffResponse,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def prepare_cloudtalk_handoff(
    request: CloudTalkHandoffRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("dialer.call")),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> CloudTalkHandoffResponse:
    if not request.confirmed:
        raise HTTPException(422, "Confirm this individual call before dialing.")
    if not request.consent_confirmed:
        raise HTTPException(422, "Confirm that you have permission to call this number.")
    try:
        target = phone(request.phone_number)
        caller_id = phone(request.caller_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None

    _validate_direct_call_policy(session, workspace.workspace_id, target)
    connection = _connection(session, workspace.workspace_id, request.connection_id)
    if connection.provider_type != "cloudtalk":
        raise HTTPException(404, "CloudTalk connection not found")

    client = _client(session, workspace, connection, resolver)
    try:
        assigned_numbers, _ = client.list_cloudtalk_agent_caller_ids()
        if caller_id not in assigned_numbers:
            raise HTTPException(
                422,
                "Select a caller ID assigned to the CloudTalk agent configured in this connection.",
            )
    except ProviderOperationError as error:
        status_code = 429 if error.status_code == 429 else 502
        raise HTTPException(
            status_code,
            (
                "CloudTalk caller-ID assignments could not be verified. "
                "Check the connection before calling."
            ),
        ) from None
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            502,
            (
                "CloudTalk caller-ID assignments could not be verified. "
                "Check the connection before calling."
            ),
        ) from None
    finally:
        client.close()

    handoff_url = f"ct+tel:{target}?from={quote(caller_id, safe='')}"
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "call.direct_handoff_prepared",
        {
            "connection_id": str(connection.id),
            "request_id": str(request.request_id),
            "provider": "cloudtalk",
            "caller_id": caller_id,
        },
    )
    session.commit()
    return CloudTalkHandoffResponse(
        phone_number=target,
        caller_id=caller_id,
        handoff_url=handoff_url,
        message=(
            "Pre-call checks passed. Open CloudTalk Desktop to call from the selected number. "
            "CloudTalk may dial immediately if its Click to Call setting is enabled."
        ),
    )


@router.post(
    "/leads/{lead_id}/cloudtalk-handoff",
    response_model=CloudTalkHandoffResponse,
    dependencies=[Depends(rate_limit_dependency("provider_actions"))],
)
def prepare_cloudtalk_lead_handoff(
    lead_id: UUID,
    request: CloudTalkLeadHandoffRequest,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(
        require_permissions("leads.view", "leads.edit", "dialer.call")
    ),
    resolver: WorkspaceAuthorizedConnectionResolver = Depends(get_workspace_connection_resolver),
) -> CloudTalkHandoffResponse:
    if not request.confirmed:
        raise HTTPException(422, "Confirm this individual call before dialing.")
    lead = owned_lead(session, workspace.workspace_id, lead_id)
    target = _target(lead)
    try:
        caller_id = phone(request.caller_id)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None

    _validate_direct_call_policy(session, workspace.workspace_id, target)
    connection = _connection(session, workspace.workspace_id, request.connection_id)
    if connection.provider_type != "cloudtalk":
        raise HTTPException(404, "CloudTalk connection not found")

    client = _client(session, workspace, connection, resolver)
    try:
        assigned_numbers, _ = client.list_cloudtalk_agent_caller_ids()
        if caller_id not in assigned_numbers:
            raise HTTPException(
                422,
                "Select a caller ID assigned to the CloudTalk agent configured in this connection.",
            )
    except ProviderOperationError as error:
        status_code = 429 if error.status_code == 429 else 502
        raise HTTPException(
            status_code,
            "CloudTalk caller-ID assignments could not be verified. "
            "Check the connection before calling.",
        ) from None
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(
            502,
            "CloudTalk caller-ID assignments could not be verified. "
            "Check the connection before calling.",
        ) from None
    finally:
        client.close()

    handoff_url = f"ct+tel:{target}?from={quote(caller_id, safe='')}"
    audit_operation(
        session,
        workspace.workspace_id,
        workspace.actor_id,
        "call.lead_handoff_prepared",
        {
            "lead_id": str(lead_id),
            "connection_id": str(connection.id),
            "request_id": str(request.request_id),
            "provider": "cloudtalk",
            "caller_id": caller_id,
        },
    )
    session.commit()
    return CloudTalkHandoffResponse(
        phone_number=target,
        caller_id=caller_id,
        handoff_url=handoff_url,
        message=(
            "Corral’s checks passed. Open CloudTalk Desktop to continue. "
            "CloudTalk may dial immediately if its Click to Call setting is enabled."
        ),
    )


@router.get("/direct/recent")
def recent_direct_calls(
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("calls.metadata.view")),
) -> dict[str, list[dict[str, object]]]:
    records = list(
        session.scalars(
            select(CallRecordORM)
            .where(
                CallRecordORM.workspace_id == workspace.workspace_id,
                CallRecordORM.lead_id.is_(None),
                CallRecordORM.request_id.is_not(None),
                CallRecordORM.actor_id == workspace.actor_id,
            )
            .order_by(
                func.coalesce(CallRecordORM.started_at, CallRecordORM.created_at).desc(),
                CallRecordORM.created_at.desc(),
            )
            .limit(50)
        )
    )
    can_view_contact = "fields.contact.view" in workspace.permissions
    leads = (
        list(
            session.execute(
                select(
                    LeadORM.id,
                    LeadORM.full_name,
                    LeadORM.company_name,
                    LeadORM.phone,
                ).where(
                    LeadORM.id.in_(accessible_lead_ids_query(workspace)),
                    LeadORM.phone.is_not(None),
                )
            )
        )
        if can_view_contact
        else []
    )
    by_phone = {
        "".join(character for character in (phone_number or "") if character.isdigit()): (
            str(lead_id),
            full_name,
            company_name,
        )
        for lead_id, full_name, company_name, phone_number in leads
    }
    payload: list[dict[str, object]] = []
    for row in records:
        item = call_record_for_permissions(row, workspace.permissions).model_dump(mode="json")
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
    workspace: WorkspaceContext = Depends(
        require_permissions("leads.view", "leads.edit", "dialer.call")
    ),
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


def _digits(value: str | None) -> str:
    return "".join(character for character in (value or "") if character.isdigit())


@router.get("/history")
def call_history(
    direction: str = Query("all", pattern="^(all|inbound|outbound|missed)$"),
    search: str = Query("", max_length=120),
    before: datetime | None = None,
    limit: int = Query(50, ge=1, le=100),
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permission("calls.metadata.view")),
) -> dict[str, object]:
    """Every call recorded in this workspace, newest first, with one outcome vocabulary."""
    when = func.coalesce(CallRecordORM.started_at, CallRecordORM.created_at)
    query = (
        select(CallRecordORM, LeadORM.full_name, LeadORM.company_name)
        .outerjoin(
            LeadORM,
            and_(
                LeadORM.id == CallRecordORM.lead_id, LeadORM.workspace_id == workspace.workspace_id
            ),
        )
        .where(
            CallRecordORM.workspace_id == workspace.workspace_id,
            or_(
                CallRecordORM.lead_id.in_(accessible_lead_ids_query(workspace)),
                and_(
                    CallRecordORM.lead_id.is_(None),
                    CallRecordORM.actor_id == workspace.actor_id,
                ),
            ),
        )
    )
    if before is not None:
        query = query.where(when < before)
    if direction in {"inbound", "outbound"}:
        query = query.where(func.lower(CallRecordORM.direction) == direction)
    if direction == "missed":
        query = query.where(func.lower(CallRecordORM.direction) == "inbound")
    term = search.strip()
    if term:
        like = f"%{term}%"
        digits = _digits(term)
        conditions = [LeadORM.company_name.ilike(like)]
        if "fields.contact.view" in workspace.permissions:
            conditions.append(LeadORM.full_name.ilike(like))
        if len(digits) >= 3 and "fields.contact.view" in workspace.permissions:
            conditions.append(CallRecordORM.customer_phone.like(f"%{digits[-7:]}%"))
        query = query.where(or_(*conditions))
    fetch = limit * 3 if direction == "missed" else limit + 1
    rows = session.execute(
        query.order_by(when.desc(), CallRecordORM.created_at.desc()).limit(fetch)
    ).all()

    unlinked_digits = {
        _digits(row[0].customer_phone)[-7:]
        for row in rows
        if row[0].lead_id is None and len(_digits(row[0].customer_phone)) >= 7
    }
    phone_matches: dict[str, tuple[str, str, str]] = {}
    if unlinked_digits and "fields.contact.view" in workspace.permissions:
        candidates = session.execute(
            select(LeadORM.id, LeadORM.full_name, LeadORM.company_name, LeadORM.phone).where(
                LeadORM.id.in_(accessible_lead_ids_query(workspace)),
                or_(*[LeadORM.phone.like(f"%{tail}%") for tail in unlinked_digits]),
            )
        ).all()
        for lead_id, full_name, company_name, phone in candidates:
            phone_matches[_digits(phone)] = (str(lead_id), full_name, company_name)

    lead_ids = {row[0].lead_id for row in rows if row[0].lead_id is not None}
    reps = (
        {
            decision.lead_id: decision.assigned_rep_name
            for decision in session.scalars(
                select(RoutingDecisionORM).where(
                    RoutingDecisionORM.workspace_id == workspace.workspace_id,
                    RoutingDecisionORM.lead_id.in_(lead_ids),
                    RoutingDecisionORM.is_current.is_(True),
                )
            )
        }
        if lead_ids
        else {}
    )

    records: list[dict[str, object]] = []
    for call, lead_name, company_name in rows:
        outcome = call_outcome(call.status, call.direction, call.duration_seconds)
        if direction == "missed" and outcome != "missed":
            continue
        item = call_record_for_permissions(call, workspace.permissions).model_dump(mode="json")
        match = None if call.lead_id else phone_matches.get(_digits(call.customer_phone))
        item.update(
            outcome=outcome,
            linked=call.lead_id is not None,
            lead_id=str(call.lead_id) if call.lead_id else (match[0] if match else None),
            lead_name=(lead_name if call.lead_id else (match[1] if match else None))
            if "fields.contact.view" in workspace.permissions
            else None,
            company_name=company_name if call.lead_id else (match[2] if match else None),
            representative_name=(reps.get(call.lead_id) if call.lead_id else None)
            if "fields.internal.view" in workspace.permissions
            else None,
        )
        records.append(item)
        if len(records) > limit:
            break
    has_more = len(records) > limit
    records = records[:limit]
    return {
        "records": records,
        "has_more": has_more,
        "next_before": records[-1]["started_at"] or records[-1]["created_at"]
        if has_more and records
        else None,
    }


class LinkCall(StrictModel):
    lead_id: UUID


@router.post("/calls/{call_id}/link")
def link_call_to_lead(
    call_id: UUID,
    request: LinkCall,
    session: Session = Depends(get_db),
    workspace: WorkspaceContext = Depends(require_permissions("dialer.call", "leads.edit")),
) -> dict[str, str]:
    call = session.scalar(
        select(CallRecordORM).where(
            CallRecordORM.id == call_id,
            CallRecordORM.workspace_id == workspace.workspace_id,
        )
    )
    if call is None:
        raise HTTPException(404, "Call not found")
    if call.lead_id is not None and call.lead_id != request.lead_id:
        raise HTTPException(409, "This call is already linked to another lead.")
    lead = session.scalar(
        select(LeadORM).where(
            LeadORM.id == request.lead_id, LeadORM.workspace_id == workspace.workspace_id
        )
    )
    if lead is None:
        raise HTTPException(404, "Lead not found")
    call.lead_id = lead.id
    call.updated_at = datetime.now(UTC)
    session.commit()
    return {"call_id": str(call.id), "lead_id": str(lead.id)}
