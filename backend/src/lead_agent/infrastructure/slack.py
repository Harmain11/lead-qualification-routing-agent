from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from hashlib import sha256
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from lead_agent.domain.integrations import (
    ConnectionValidation,
    ProviderRevoker,
    ProviderValidationStatus,
    RevocationResult,
    SecretStore,
)
from lead_agent.domain.provider_errors import ProviderErrorCategory, ProviderOperationError
from lead_agent.infrastructure.db.models import (
    LeadORM,
    ProviderConnectionORM,
    RoutingDecisionORM,
    WorkspaceNotificationConfigORM,
)
from lead_agent.infrastructure.provider_lifecycle import (
    ResolvedProviderConnection,
    WorkspaceAuthorizedConnectionResolver,
    record_provider_lifecycle_audit,
)

# ``im:write`` is required for conversations.open, which is how Slack opens
# a private DM before chat.postMessage can deliver the assignment alert.
SLACK_REQUIRED_SCOPES = ("chat:write", "channels:read", "im:write")
_API = "https://slack.com/api/"


def _safe_error(
    code: str,
    *,
    status_code: int | None = None,
    retry_after_seconds: int | None = None,
) -> ProviderOperationError:
    if code == "invalid_auth":
        category, message, retryable = (
            "invalid_credentials",
            "Slack rejected the connection token.",
            False,
        )
    elif code == "token_revoked":
        category, message, retryable = (
            "revoked",
            "Slack connection was revoked; reconnect it.",
            False,
        )
    elif code == "missing_scope":
        category, message, retryable = (
            "insufficient_permissions",
            "Slack connection is missing a required permission.",
            False,
        )
    elif code in {"channel_not_found", "not_in_channel"}:
        category, message, retryable = (
            "configuration",
            "Slack channel is unavailable or the bot is not a member.",
            False,
        )
    elif code in {"ratelimited", "rate_limited"} or status_code == 429:
        category, message, retryable = (
            "rate_limited",
            "Slack rate limit reached; retry later.",
            True,
        )
    elif status_code is not None and status_code >= 500:
        category, message, retryable = (
            "temporarily_unavailable",
            "Slack is temporarily unavailable.",
            True,
        )
    else:
        category, message, retryable = "unknown", "Slack request failed.", False
    category = cast(ProviderErrorCategory, category)
    return ProviderOperationError(
        message,
        provider="slack",
        code=f"slack_{code}",
        category=category,
        retryable=retryable,
        status_code=status_code,
        metadata=(
            {"retry_after_seconds": retry_after_seconds}
            if retry_after_seconds is not None
            else None
        ),
    )


class SlackWebApi:
    """Secret-safe Slack Web API boundary; injectable httpx client supports offline tests."""

    def __init__(
        self, token: str, client: httpx.Client | None = None, timeout_seconds: float = 10.0
    ) -> None:
        self.token = token
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def call(self, method: str, **data: Any) -> tuple[dict[str, Any], httpx.Headers]:
        try:
            response = self.client.post(
                _API + method,
                data=data,
                headers={"Authorization": f"Bearer {self.token}"},
            )
        except httpx.TimeoutException:
            raise ProviderOperationError(
                "Slack request timed out.",
                provider="slack",
                code="slack_timeout",
                category="timeout",
                retryable=True,
            ) from None
        except httpx.HTTPError:
            raise ProviderOperationError(
                "Slack is temporarily unavailable.",
                provider="slack",
                code="slack_transport_error",
                category="temporarily_unavailable",
                retryable=True,
            ) from None
        retry_after = response.headers.get("retry-after")
        try:
            retry_after_seconds = max(0, int(retry_after)) if retry_after else None
        except ValueError:
            retry_after_seconds = None
        if response.status_code == 429 or response.status_code >= 500:
            raise _safe_error(
                "ratelimited" if response.status_code == 429 else "server_error",
                status_code=response.status_code,
                retry_after_seconds=retry_after_seconds,
            )
        if response.status_code >= 400:
            code = {
                401: "invalid_auth",
                403: "missing_scope",
                404: "channel_not_found",
            }.get(response.status_code, "http_error")
            raise _safe_error(code, status_code=response.status_code)
        try:
            payload = response.json()
        except ValueError:
            raise ProviderOperationError(
                "Slack returned an unreadable response.",
                provider="slack",
                code="slack_malformed_response",
                category="malformed_response",
                retryable=False,
            ) from None
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            code = (
                str(payload.get("error", "unknown_error"))
                if isinstance(payload, dict)
                else "malformed_response"
            )
            raise _safe_error(code, retry_after_seconds=retry_after_seconds)
        return payload, response.headers


class SlackConnectionValidator:
    def __init__(
        self, *, timeout_seconds: float = 10.0, client: httpx.Client | None = None
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.client = client

    def validate(
        self, provider: str, credentials: dict[str, str], scopes: list[str]
    ) -> ConnectionValidation:
        del provider, scopes
        token = credentials.get("access_token") or credentials.get("bot_token", "")
        if not token:
            return ConnectionValidation(
                valid=False,
                status="configuration_error",
                credential_state="invalid",
                message="Slack access token is missing.",
                error_code="credential_fields_missing",
            )
        try:
            payload, headers = SlackWebApi(token, self.client, self.timeout_seconds).call(
                "auth.test"
            )
        except ProviderOperationError as error:
            status = (
                "revoked"
                if error.category == "revoked"
                else "invalid_credentials"
                if error.category == "invalid_credentials"
                else "insufficient_permissions"
                if error.category == "insufficient_permissions"
                else "rate_limited"
                if error.category == "rate_limited"
                else "temporarily_unavailable"
                if error.retryable
                else "configuration_error"
            )
            status = cast(ProviderValidationStatus, status)
            return ConnectionValidation(
                valid=False,
                verified=False,
                status=status,
                credential_state="expired"
                if status == "revoked"
                else "invalid"
                if status == "invalid_credentials"
                else "configured",
                message=str(error),
                retryable=error.retryable,
                error_code=error.code,
            )
        granted = {
            scope.strip() for scope in headers.get("x-oauth-scopes", "").split(",") if scope.strip()
        }
        if not granted:
            granted = {
                scope.strip() for scope in credentials.get("scope", "").split(",") if scope.strip()
            }
        missing = sorted(set(SLACK_REQUIRED_SCOPES) - granted)
        if missing:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="insufficient_permissions",
                credential_state="configured",
                message=f"Slack connection is missing required permissions: {', '.join(missing)}.",
                error_code="slack_missing_scope",
                metadata={"granted_scopes": sorted(granted)},
            )
        team_id = payload.get("team_id")
        if not isinstance(team_id, str) or not team_id:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="malformed_response",
                credential_state="configured",
                message="Slack did not identify the connected workspace.",
                error_code="slack_team_id_missing",
            )
        expected_team = credentials.get("external_account_id")
        if expected_team and expected_team != team_id:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="invalid_credentials",
                credential_state="invalid",
                message="Slack token belongs to a different workspace than the OAuth connection.",
                error_code="slack_workspace_mismatch",
            )
        metadata: dict[str, object] = {
            "slack_team_id": team_id,
            "slack_user_id": payload.get("user_id"),
            "slack_user_name": payload.get("user"),
            "slack_bot_id": payload.get("bot_id"),
            "slack_bot_user_id": credentials.get("bot_user_id"),
            "slack_team_name": payload.get("team"),
            "granted_scopes": sorted(granted),
        }
        return ConnectionValidation(
            valid=True,
            verified=True,
            status="connected",
            credential_state="configured",
            message="Slack token verified for the connected workspace.",
            metadata={key: value for key, value in metadata.items() if value is not None},
        )


class SlackProviderRevoker(ProviderRevoker):
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client

    def revoke(self, provider: str, values: dict[str, str]) -> RevocationResult:
        if provider != "slack" or not (values.get("access_token") or values.get("bot_token")):
            return RevocationResult(status="unsupported")
        try:
            payload, _ = SlackWebApi(
                values.get("access_token") or values.get("bot_token", ""), self.client
            ).call("auth.revoke")
            revoked = payload.get("revoked") is True
            return RevocationResult(
                status="confirmed" if revoked else "failed",
                error_code=None if revoked else "slack_revocation_unconfirmed",
            )
        except ProviderOperationError as error:
            if error.category in {"invalid_credentials", "revoked"}:
                return RevocationResult(status="confirmed")
            return RevocationResult(status="failed", error_code="slack_remote_revocation_failed")


class SlackNotificationProvider:
    provider_name = "slack"

    def __init__(
        self,
        *,
        connection: ResolvedProviderConnection,
        channel_id: str,
        destination_type: str = "channel",
        fallback_to_channel: bool = False,
        fallback_channel_id: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.connection_id = connection.connection_id
        self.workspace_id = connection.workspace_id
        self.channel_id = channel_id
        self.destination_type = destination_type
        self.fallback_to_channel = fallback_to_channel
        self.fallback_channel_id = fallback_channel_id
        self._api = SlackWebApi(
            connection.credentials.get("access_token")
            or connection.credentials.get("bot_token", ""),
            client,
        )

    def send(self, message: Any, *, channel_id: str | None = None) -> Any:
        from lead_agent.domain.routing import NotificationDelivery

        if self.destination_type == "direct_message" and channel_id is None:
            direct = self.send_direct(self.channel_id, message)
            if (
                direct.state in {"sent", "retryable"}
                or not self.fallback_to_channel
                or not self.fallback_channel_id
            ):
                return direct
            fallback = self._send_to_channel(message, self.fallback_channel_id)
            if fallback.state == "sent":
                return NotificationDelivery(
                    state="sent",
                    external_id=fallback.external_id,
                    error_message=(
                        f"Direct message could not be delivered ({direct.error_message or 'Slack rejected it'}); "
                        "sent to the configured fallback channel instead."
                    ),
                )
            return NotificationDelivery(
                state=fallback.state,
                error_message=(
                    f"Direct message could not be delivered ({direct.error_message or 'Slack rejected it'}); "
                    f"channel fallback also failed: {fallback.error_message or 'Slack rejected it'}."
                ),
                retryable=fallback.retryable,
                retry_after_seconds=fallback.retry_after_seconds,
            )
        return self._send_to_channel(message, channel_id or self.channel_id)

    def _send_to_channel(self, message: Any, channel_id: str) -> Any:
        from lead_agent.domain.routing import NotificationDelivery

        try:
            payload, _ = self._api.call(
                "chat.postMessage",
                channel=channel_id,
                text=message.text,
                # Slack uses this stable client id to make a retry after a
                # worker crash safe when the first request was accepted.
                client_msg_id=str(uuid5(NAMESPACE_URL, f"lead-agent:{message.deduplication_key}")),
                unfurl_links="false",
                unfurl_media="false",
            )
            return NotificationDelivery(
                state="sent", external_id=str(payload.get("ts")) if payload.get("ts") else None
            )

        except ProviderOperationError as error:
            retry_after = error.metadata.get("retry_after_seconds")
            return NotificationDelivery(
                state="retryable" if error.retryable else "failed",
                error_message=str(error),
                retryable=error.retryable,
                retry_after_seconds=(
                    retry_after
                    if isinstance(retry_after, int) and not isinstance(retry_after, bool)
                    else None
                ),
            )

    def send_direct(self, member_id: str, message: Any) -> Any:
        from lead_agent.domain.routing import NotificationDelivery

        try:
            opened, _ = self._api.call("conversations.open", users=member_id)
            conversation = opened.get("channel")
            dm_channel_id = conversation.get("id") if isinstance(conversation, dict) else None
            if not isinstance(dm_channel_id, str) or not dm_channel_id:
                return NotificationDelivery(
                    state="failed",
                    error_message=(
                        "Slack did not open a direct conversation for this member. "
                        "Check the member ID and grant im:write if direct messages are intended."
                    ),
                )
            return self._send_to_channel(message, dm_channel_id)
        except ProviderOperationError as error:
            retry_after = error.metadata.get("retry_after_seconds")
            return NotificationDelivery(
                state="retryable" if error.retryable else "failed",
                error_message=(
                    f"Could not open or send the Slack direct message: {error}. "
                    "Verify the member ID and optional im:write scope."
                ),
                retryable=error.retryable,
                retry_after_seconds=(
                    retry_after
                    if isinstance(retry_after, int) and not isinstance(retry_after, bool)
                    else None
                ),
            )


class WorkspaceSlackNotificationResolver:
    def __init__(
        self,
        secret_store: SecretStore,
        connection_resolver: WorkspaceAuthorizedConnectionResolver,
        client: httpx.Client | None = None,
    ) -> None:
        self.secret_store = secret_store
        self.connection_resolver = connection_resolver
        self.client = client

    def resolve_for_workspace(
        self, session: Session, workspace_id: UUID, actor_id: str = "system"
    ) -> SlackNotificationProvider | None:
        connection = session.scalar(
            select(ProviderConnectionORM)
            .where(
                ProviderConnectionORM.workspace_id == workspace_id,
                ProviderConnectionORM.provider_type == "slack",
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status == "verified",
            )
            .order_by(ProviderConnectionORM.updated_at.desc())
        )
        if connection is None:
            return None
        config = session.get(WorkspaceNotificationConfigORM, workspace_id)
        if config and config.default_slack_connection_id:
            preferred = session.scalar(
                select(ProviderConnectionORM).where(
                    ProviderConnectionORM.id == config.default_slack_connection_id,
                    ProviderConnectionORM.workspace_id == workspace_id,
                    ProviderConnectionORM.provider_type == "slack",
                    ProviderConnectionORM.is_active.is_(True),
                    ProviderConnectionORM.status == "verified",
                )
            )
            if preferred is not None:
                connection = preferred
        return self.provider_for_connection(session, workspace_id, connection.id, actor_id)

    def resolve_for_direct_message(
        self,
        session: Session,
        workspace_id: UUID,
        actor_id: str = "system",
        slack_team_id: str | None = None,
    ) -> SlackNotificationProvider | None:
        """Resolve the verified Slack connection used for member DMs.

        A direct message must not depend on a configured team channel. This is
        intentionally separate from ``resolve_for_workspace`` because channel
        routing is optional for assignment notifications.
        """
        connections = list(
            session.scalars(
                select(ProviderConnectionORM)
                .where(
                    ProviderConnectionORM.workspace_id == workspace_id,
                    ProviderConnectionORM.provider_type == "slack",
                    ProviderConnectionORM.is_active.is_(True),
                    ProviderConnectionORM.status == "verified",
                )
                .order_by(ProviderConnectionORM.updated_at.desc())
            )
        )
        connection = next(
            (
                row
                for row in connections
                if slack_team_id
                and row.connection_metadata.get("slack_team_id") == slack_team_id
            ),
            connections[0] if connections else None,
        )
        if connection is None:
            return None
        config = session.get(WorkspaceNotificationConfigORM, workspace_id)
        if config and config.default_slack_connection_id and not slack_team_id:
            preferred = session.scalar(
                select(ProviderConnectionORM).where(
                    ProviderConnectionORM.id == config.default_slack_connection_id,
                    ProviderConnectionORM.workspace_id == workspace_id,
                    ProviderConnectionORM.provider_type == "slack",
                    ProviderConnectionORM.is_active.is_(True),
                    ProviderConnectionORM.status == "verified",
                )
            )
            if preferred is not None:
                connection = preferred
        return self.provider_for_connection(
            session,
            workspace_id,
            connection.id,
            actor_id,
            destination_type="direct_message",
        )

    def resolve_for_lead(
        self,
        session: Session,
        workspace_id: UUID,
        lead: LeadORM,
        decision: RoutingDecisionORM,
        actor_id: str = "system",
    ) -> SlackNotificationProvider | None:
        """Resolve the Slack destination using the workspace admin's rules."""

        connections = list(
            session.scalars(
                select(ProviderConnectionORM)
                .where(
                    ProviderConnectionORM.workspace_id == workspace_id,
                    ProviderConnectionORM.provider_type == "slack",
                    ProviderConnectionORM.is_active.is_(True),
                    ProviderConnectionORM.status == "verified",
                )
                .order_by(ProviderConnectionORM.updated_at.desc())
            )
        )
        if not connections:
            return None
        by_id = {connection.id: connection for connection in connections}
        config = session.get(WorkspaceNotificationConfigORM, workspace_id)
        if config is not None:
            matching_rules: list[tuple[dict[str, Any], ProviderConnectionORM]] = []
            for raw_rule in config.slack_rules:
                if not isinstance(raw_rule, dict):
                    continue
                target_id = self._rule_target_id(raw_rule)
                target = by_id.get(target_id) if target_id is not None else None
                if target is not None and self._rule_matches(raw_rule, lead, decision):
                    matching_rules.append((raw_rule, target))
            if matching_rules:
                total_weight = sum(
                    max(0.01, float(rule.get("weight", 1)))
                    for rule, _ in matching_rules
                    if isinstance(rule.get("weight", 1), (int, float))
                ) or 1
                bucket = int(sha256(str(lead.id).encode()).hexdigest()[:12], 16) / 16**12
                selected = bucket * total_weight
                for raw_rule, target in matching_rules:
                    weight = max(0.01, float(raw_rule.get("weight", 1)))
                    selected -= weight
                    if selected <= 0:
                        return self.provider_for_connection(
                            session,
                            workspace_id,
                            target.id,
                            actor_id,
                            channel_id=self._rule_channel_id(raw_rule),
                            destination_type=str(raw_rule.get("destination_type", "channel")),
                            fallback_to_channel=raw_rule.get("fallback_to_channel") is True,
                        )
                return self.provider_for_connection(
                    session,
                    workspace_id,
                    matching_rules[-1][1].id,
                    actor_id,
                    channel_id=self._rule_channel_id(matching_rules[-1][0]),
                    destination_type=str(matching_rules[-1][0].get("destination_type", "channel")),
                    fallback_to_channel=matching_rules[-1][0].get("fallback_to_channel") is True,
                )
            if config.default_slack_connection_id in by_id:
                return self.provider_for_connection(
                    session, workspace_id, config.default_slack_connection_id, actor_id
                )
        # A single connected Slack workspace is the automatic default. With
        # several connections, the newest one remains a safe compatibility
        # fallback until an admin defines the destination rules.
        return self.provider_for_connection(session, workspace_id, connections[0].id, actor_id)

    @staticmethod
    def _rule_target_id(raw_rule: dict[str, Any]) -> UUID | None:
        try:
            value = raw_rule.get("connection_id")
            return UUID(str(value)) if value else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _rule_channel_id(raw_rule: dict[str, Any]) -> str | None:
        value = raw_rule.get("channel_id")
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _rule_matches(
        raw_rule: dict[str, Any], lead: LeadORM, decision: RoutingDecisionORM
    ) -> bool:
        def matches(value: str | None, values: Any) -> bool:
            if not isinstance(values, list) or not values:
                return True
            normalized = (value or "").strip().casefold()
            return bool(normalized) and any(
                normalized == str(option).strip().casefold()
                or str(option).strip().casefold() in normalized
                for option in values
            )

        source_filters = raw_rule.get("sources")
        source_matches = (
            not isinstance(source_filters, list)
            or not source_filters
            or any(
                matches(value, source_filters)
                for value in (lead.source, lead.source_detail)
                if value
            )
        )
        return (
            matches(decision.assigned_queue, raw_rule.get("queues"))
            and matches(decision.qualification_band, raw_rule.get("qualification_bands"))
            and source_matches
        )

    def resolve_connection(
        self, session: Session, workspace_id: UUID, connection_id: UUID, actor_id: str = "system"
    ) -> tuple[ProviderConnectionORM, ResolvedProviderConnection]:
        row = session.scalar(
            select(ProviderConnectionORM).where(
                ProviderConnectionORM.id == connection_id,
                ProviderConnectionORM.workspace_id == workspace_id,
                ProviderConnectionORM.provider_type == "slack",
                ProviderConnectionORM.is_active.is_(True),
                ProviderConnectionORM.status == "verified",
            )
        )
        if row is None:
            raise LookupError("verified Slack connection not found")
        resolved = self.connection_resolver.resolve_connection(
            session, workspace_id, connection_id, actor_id=actor_id
        )
        if resolved is None:
            raise LookupError("verified Slack connection not available")
        return row, resolved

    def provider_for_connection(
        self,
        session: Session,
        workspace_id: UUID,
        connection_id: UUID,
        actor_id: str = "system",
        channel_id: str | None = None,
        destination_type: str = "channel",
        fallback_to_channel: bool = False,
    ) -> SlackNotificationProvider | None:
        row, resolved = self.resolve_connection(session, workspace_id, connection_id, actor_id)
        selected_channel_id = channel_id or row.connection_metadata.get("slack_channel_id")
        if (
            destination_type != "direct_message"
            and (not isinstance(selected_channel_id, str) or not selected_channel_id)
        ):
            return None
        return SlackNotificationProvider(
            connection=resolved,
            channel_id=selected_channel_id or "",
            destination_type=destination_type,
            fallback_to_channel=fallback_to_channel,
            fallback_channel_id=(
                row.connection_metadata.get("slack_channel_id")
                if destination_type == "direct_message" and fallback_to_channel
                else None
            ),
            client=self.client,
        )

    def select_channel(
        self,
        session: Session,
        workspace_id: UUID,
        connection_id: UUID,
        channel_id: str,
        actor_id: str,
    ) -> dict[str, str | bool]:
        row, resolved = self.resolve_connection(session, workspace_id, connection_id, actor_id)
        api = SlackWebApi(
            resolved.credentials.get("access_token")
            or resolved.credentials.get("bot_token", ""),
            self.client,
        )
        joined = False
        try:
            payload, _ = api.call("conversations.info", channel=channel_id)
            channel = payload.get("channel", {})
            if not isinstance(channel, dict):
                raise ValueError("Slack did not return the selected channel.")
            if channel.get("is_member") is not True:
                if channel.get("is_private") is True:
                    raise ValueError(
                        "Invite the Slack bot to this private channel in Slack, "
                        "then refresh channels and select it. Slack does not allow "
                        "apps to join private channels themselves."
                    )
                if "channels:join" not in self._channel_scopes(row, resolved.credentials):
                    raise ValueError(
                        "Invite the Slack bot to the selected channel, or reconnect "
                        "Slack once to enable automatic joining of public channels."
                    )
                api.call("conversations.join", channel=channel_id)
                joined = True
                payload, _ = api.call("conversations.info", channel=channel_id)
                channel = payload.get("channel", {})
        except ProviderOperationError as error:
            raise ValueError(str(error)) from None
        if not isinstance(channel, dict) or channel.get("is_member") is not True:
            raise ValueError("Slack has not confirmed app membership; the destination was not changed.")
        metadata = dict(row.connection_metadata)
        previous_channel_id = metadata.get("slack_channel_id")
        previous_channel_name = metadata.get("slack_channel_name")
        metadata["slack_channel_id"] = channel_id
        metadata["slack_channel_name"] = str(channel.get("name") or channel_id)
        metadata["slack_channel_is_private"] = channel.get("is_private") is True
        row.connection_metadata = metadata
        row.updated_at = datetime.now(UTC)
        record_provider_lifecycle_audit(
            session,
            row,
            action="channel_selection",
            outcome="selected",
            actor_id=actor_id,
            details={
                "channel_id": channel_id,
                "channel_name": str(channel.get("name") or channel_id),
                "previous_channel_id": previous_channel_id,
                "previous_channel_name": previous_channel_name,
                "joined_public_channel": joined,
            },
        )
        session.commit()
        return {
            "id": channel_id, "name": str(channel.get("name") or channel_id),
            "is_member": True, "is_private": channel.get("is_private") is True,
        }

    @staticmethod
    def _channel_scopes(row: ProviderConnectionORM, credentials: dict[str, str]) -> set[str]:
        granted = (row.connection_metadata or {}).get("granted_scopes")
        if isinstance(granted, list):
            return set(granted)
        if credentials.get("scope"):
            return set(credentials["scope"].replace(" ", ",").split(","))
        return set(row.scopes or [])

    def list_channels(
        self, session: Session, workspace_id: UUID, connection_id: UUID, actor_id: str
    ) -> list[dict[str, str | bool]]:
        row, resolved = self.resolve_connection(session, workspace_id, connection_id, actor_id)
        include_private = "groups:read" in self._channel_scopes(row, resolved.credentials)
        try:
            api = SlackWebApi(
                resolved.credentials.get("access_token")
                or resolved.credentials.get("bot_token", ""),
                self.client,
            )
            channels: list[dict[str, str | bool]] = []
            cursor: str | None = None
            for _ in range(10):
                params: dict[str, str | int] = {
                    "types": "public_channel,private_channel" if include_private else "public_channel",
                    "exclude_archived": "true",
                    "limit": 200,
                }
                if cursor:
                    params["cursor"] = cursor
                payload, _ = api.call("conversations.list", **params)
                page = payload.get("channels", [])
                channels.extend(
                    {
                        "id": str(item["id"]),
                        "name": str(item["name"]),
                        "is_member": item.get("is_member") is True,
                        "is_private": item.get("is_private") is True,
                    }
                    for item in page
                    if isinstance(item, dict)
                    and item.get("id")
                    and item.get("name")
                )
                response_metadata = payload.get("response_metadata", {})
                next_cursor = (
                    response_metadata.get("next_cursor")
                    if isinstance(response_metadata, dict)
                    else None
                )
                cursor = next_cursor if isinstance(next_cursor, str) and next_cursor else None
                if cursor is None:
                    break
        except ProviderOperationError as error:
            raise ValueError(str(error)) from None
        return channels

    def list_channel_members(
        self,
        session: Session,
        workspace_id: UUID,
        connection_id: UUID,
        channel_id: str,
        actor_id: str,
    ) -> list[dict[str, str]]:
        _, resolved = self.resolve_connection(session, workspace_id, connection_id, actor_id)
        api = SlackWebApi(
            resolved.credentials.get("access_token")
            or resolved.credentials.get("bot_token", ""),
            self.client,
        )
        members: list[str] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, str | int] = {"channel": channel_id, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            payload, _ = api.call("conversations.members", **params)
            page = payload.get("members", [])
            members.extend(str(item) for item in page if item)
            metadata = payload.get("response_metadata", {})
            cursor = metadata.get("next_cursor") if isinstance(metadata, dict) else None
            if not isinstance(cursor, str) or not cursor:
                break
        result: list[dict[str, str]] = []
        for member_id in dict.fromkeys(members):
            name = member_id
            try:
                profile, _ = api.call("users.info", user=member_id)
                user = profile.get("user", {})
                if isinstance(user, dict):
                    name = str(user.get("real_name") or user.get("name") or member_id)
            except ProviderOperationError as error:
                if error.code != "slack_missing_scope":
                    raise
            result.append({"id": member_id, "name": name})
        return result


@lru_cache
def get_workspace_slack_notification_resolver() -> WorkspaceSlackNotificationResolver:
    from lead_agent.infrastructure.provider_lifecycle import get_workspace_connection_resolver
    from lead_agent.infrastructure.secrets import get_secret_store

    return WorkspaceSlackNotificationResolver(
        get_secret_store(), get_workspace_connection_resolver()
    )
