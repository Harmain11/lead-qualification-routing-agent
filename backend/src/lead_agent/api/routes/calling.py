"""Rep-confirmed calls and bounded provider history, never an automatic dial campaign."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lead_agent.api.dependencies import require_permission, require_permissions
from lead_agent.api.routes.collaboration import owned_lead
from lead_agent.application.governance import (
    OutreachBlocked,
    assert_outreach_allowed,
    audit_operation,
    outreach_allowed,
)
from lead_agent.domain.operations import StrictModel
from lead_agent.domain.provider_errors import ProviderOperationError
from lead_agent.domain.workspaces import WorkspaceContext
from lead_agent.infrastructure.communications import DIALERS, CommunicationsClient, phone
from lead_agent.infrastructure.db.calling import CallRecordORM
from lead_agent.infrastructure.db.models import LeadORM, ProviderConnectionORM
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
    message: str | None
    created_at: datetime
    updated_at: datetime


class StartCall(StrictModel):
    connection_id: UUID
    request_id: UUID
    confirmed: bool
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
    return {
        "connections": [
            {"id": row.id, "provider": row.provider_type, "account_name": row.account_name}
            for row in connections
        ],
        "records": [CallRecord.model_validate(row) for row in records],
        "outreach_allowed": allowed,
        "outreach_reason": reason,
    }


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
        raise HTTPException(502, str(error)) from None
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
