from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import httpx

from lead_agent.domain.integrations import ConnectionValidation
from lead_agent.domain.provider_errors import ProviderOperationError

GOOGLE_CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"
GOOGLE_CALENDAR_FREEBUSY_SCOPE = "https://www.googleapis.com/auth/calendar.events.freebusy"
GOOGLE_CALENDAR_METADATA_SCOPE = "https://www.googleapis.com/auth/calendar.calendars.readonly"
CALENDLY_SCOPES = (
    "users:read",
    "event_types:read",
    "availability:read",
    "scheduled_events:read",
    "scheduled_events:write",
)


def _http_error(provider: str, response: httpx.Response, path: str) -> ProviderOperationError:
    status_code = response.status_code
    if status_code == 401:
        code, category, message, retryable = (
            f"{provider}_reauthorization_required",
            "revoked",
            f"{provider.replace('_', ' ').title()} rejected the authorization.",
            False,
        )
    elif status_code == 403:
        code, category, message, retryable = (
            f"{provider}_insufficient_permissions",
            "insufficient_permissions",
            (
                f"{provider.replace('_', ' ').title()} denied this operation. "
                "Check the connected account’s permissions and plan."
            ),
            False,
        )
    elif status_code == 404:
        code, category, message, retryable = (
            f"{provider}_resource_not_found",
            "not_found",
            f"{provider.replace('_', ' ').title()} could not find the requested resource.",
            False,
        )
    elif status_code == 409:
        code, category, message, retryable = (
            f"{provider}_resource_conflict",
            "conflict",
            f"{provider.replace('_', ' ').title()} already has an event for this request.",
            False,
        )
    elif status_code == 429 or status_code >= 500:
        code, category, message, retryable = (
            f"{provider}_temporarily_unavailable",
            "temporarily_unavailable",
            f"{provider.replace('_', ' ').title()} is temporarily unavailable.",
            True,
        )
    else:
        code, category, message, retryable = (
            f"{provider}_request_rejected",
            "invalid_request",
            f"{provider.replace('_', ' ').title()} rejected the request.",
            False,
        )
    try:
        body = response.json()
    except ValueError:
        body = None
    provider_details: list[str] = []
    if isinstance(body, dict):
        for key in ("message", "detail", "title", "error"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                provider_details.append(value.strip())
            elif isinstance(value, dict):
                nested = value.get("message") or value.get("detail")
                if isinstance(nested, str) and nested.strip():
                    provider_details.append(nested.strip())
        for key in ("details", "errors"):
            entries = body.get(key)
            if isinstance(entries, dict):
                entries = [entries]
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, str) and entry.strip():
                    provider_details.append(entry.strip())
                    continue
                if not isinstance(entry, dict):
                    continue
                detail = entry.get("message") or entry.get("detail")
                if not isinstance(detail, str) or not detail.strip():
                    continue
                field = entry.get("parameter") or entry.get("field")
                prefix = f"{field}: " if isinstance(field, str) and field else ""
                provider_details.append(f"{prefix}{detail.strip()}")
    if provider_details:
        detail_text = " ".join(dict.fromkeys(provider_details))
        detail_text = " ".join(detail_text.split())[:320]
        if (
            provider == "calendly"
            and path == "event_type_available_times"
            and "failed to connect to external calendar" in detail_text.casefold()
        ):
            code = "calendly_external_calendar_unavailable"
            category = "configuration"
            message = (
                "Calendly cannot check availability because the calendar connected "
                "to this event type is unavailable. Reconnect that calendar in "
                "Calendly, then retry."
            )
            retryable = False
        else:
            message = f"{message} Details: {detail_text}"
    return ProviderOperationError(
        message,
        provider=provider,
        code=code,
        category=cast(Any, category),
        retryable=retryable,
        status_code=status_code,
        metadata={"path": path},
    )


class _CalendarTransport:
    def __init__(
        self,
        provider: str,
        base_url: str,
        access_token: str,
        *,
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = self.client.request(
                method,
                f"{self.base_url}/{path.lstrip('/')}",
                params=params,
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        except httpx.TimeoutException:
            raise ProviderOperationError(
                f"{self.provider.replace('_', ' ').title()} request timed out.",
                provider=self.provider,
                code=f"{self.provider}_timeout",
                category="timeout",
                retryable=True,
            ) from None
        except httpx.HTTPError:
            raise ProviderOperationError(
                f"{self.provider.replace('_', ' ').title()} is temporarily unavailable.",
                provider=self.provider,
                code=f"{self.provider}_transport_error",
                category="temporarily_unavailable",
                retryable=True,
            ) from None
        if response.status_code >= 400:
            raise _http_error(self.provider, response, path)
        try:
            result = response.json()
        except ValueError:
            raise ProviderOperationError(
                f"{self.provider.replace('_', ' ').title()} returned an unreadable response.",
                provider=self.provider,
                code=f"{self.provider}_malformed_response",
                category="malformed_response",
                retryable=False,
            ) from None
        if not isinstance(result, dict):
            raise ProviderOperationError(
                f"{self.provider.replace('_', ' ').title()} returned an unreadable response.",
                provider=self.provider,
                code=f"{self.provider}_malformed_response",
                category="malformed_response",
                retryable=False,
            )
        return cast(dict[str, Any], result)


class GoogleCalendarClient:
    def __init__(
        self,
        access_token: str,
        *,
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api = _CalendarTransport(
            "google_calendar",
            "https://www.googleapis.com/calendar/v3",
            access_token,
            timeout_seconds=timeout_seconds,
            client=client,
        )

    def primary_calendar(self) -> dict[str, Any]:
        return self.api.request("GET", "calendars/primary")

    def busy_intervals(self, time_min: datetime, time_max: datetime) -> list[dict[str, str]]:
        result = self.api.request(
            "POST",
            "freeBusy",
            payload={
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "items": [{"id": "primary"}],
            },
        )
        calendars = result.get("calendars", {})
        primary = calendars.get("primary", {}) if isinstance(calendars, dict) else {}
        busy = primary.get("busy", []) if isinstance(primary, dict) else []
        return [
            {"start": str(item.get("start", "")), "end": str(item.get("end", ""))}
            for item in busy
            if isinstance(item, dict) and item.get("start") and item.get("end")
        ]

    def events_between(self, time_min: datetime, time_max: datetime) -> list[dict[str, Any]]:
        result = self.api.request(
            "GET",
            "calendars/primary/events",
            params={
                "timeMin": time_min.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "timeMax": time_max.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "singleEvents": "true",
                "showDeleted": "false",
                "orderBy": "startTime",
                "maxResults": "2500",
            },
        )
        items = result.get("items", [])
        return [cast(dict[str, Any], item) for item in items if isinstance(item, dict)]

    def create_event(
        self,
        *,
        event_id: str,
        summary: str,
        description: str,
        start_at: datetime,
        end_at: datetime,
        time_zone: str,
        attendee_email: str,
        attendee_name: str,
        lead_id: str,
        source_message_id: str,
    ) -> dict[str, Any]:
        return self.api.request(
            "POST",
            "calendars/primary/events",
            params={"sendUpdates": "all"},
            payload={
                "id": event_id,
                "summary": summary,
                "description": description,
                "start": {"dateTime": start_at.isoformat(), "timeZone": time_zone},
                "end": {"dateTime": end_at.isoformat(), "timeZone": time_zone},
                "attendees": [{"email": attendee_email, "displayName": attendee_name}],
                "extendedProperties": {
                    "private": {
                        "corralLeadId": lead_id,
                        "corralSourceMessageId": source_message_id,
                    }
                },
            },
        )

    def event(self, event_id: str) -> dict[str, Any]:
        return self.api.request("GET", f"calendars/primary/events/{event_id}")


class CalendlyClient:
    def __init__(
        self,
        access_token: str,
        *,
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api = _CalendarTransport(
            "calendly",
            "https://api.calendly.com",
            access_token,
            timeout_seconds=timeout_seconds,
            client=client,
        )

    def current_user(self) -> dict[str, Any]:
        result = self.api.request("GET", "users/me")
        resource = result.get("resource")
        return cast(dict[str, Any], resource) if isinstance(resource, dict) else {}

    def event_types(self, user_uri: str) -> list[dict[str, Any]]:
        result = self.api.request(
            "GET",
            "event_types",
            params={"user": user_uri, "active": "true", "count": "100"},
        )
        collection = result.get("collection", [])
        return [cast(dict[str, Any], row) for row in collection if isinstance(row, dict)]

    def available_times(
        self, event_type_uri: str, start_at: datetime, end_at: datetime
    ) -> list[dict[str, Any]]:
        result = self.api.request(
            "GET",
            "event_type_available_times",
            params={
                "event_type": event_type_uri,
                "start_time": (
                    start_at.astimezone(UTC)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                ),
                "end_time": (
                    end_at.astimezone(UTC)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                ),
            },
        )
        collection = result.get("collection", [])
        return [cast(dict[str, Any], row) for row in collection if isinstance(row, dict)]

    def create_invitee(
        self,
        *,
        event_type_uri: str,
        start_at: datetime,
        name: str,
        email: str,
        time_zone: str,
        location: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event_type": event_type_uri,
            "start_time": start_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "invitee": {"name": name, "email": email, "timezone": time_zone},
        }
        if location is not None:
            payload["location"] = location
        result = self.api.request(
            "POST",
            "invitees",
            payload=payload,
        )
        resource = result.get("resource")
        return cast(dict[str, Any], resource) if isinstance(resource, dict) else {}

    def scheduled_events(
        self,
        *,
        user_uri: str,
        invitee_email: str,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        result = self.api.request(
            "GET",
            "scheduled_events",
            params={
                "user": user_uri,
                "invitee_email": invitee_email,
                "min_start_time": start_at.isoformat(),
                "max_start_time": end_at.isoformat(),
                "status": "active",
                "count": "100",
            },
        )
        collection = result.get("collection", [])
        return [cast(dict[str, Any], row) for row in collection if isinstance(row, dict)]


class GoogleCalendarConnectionValidator:
    def __init__(
        self, timeout_seconds: float = 10.0, client: httpx.Client | None = None
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.client = client

    def validate(
        self, provider: str, credentials: dict[str, str], scopes: list[str]
    ) -> ConnectionValidation:
        del provider
        required = {
            GOOGLE_CALENDAR_EVENTS_SCOPE,
            GOOGLE_CALENDAR_FREEBUSY_SCOPE,
            GOOGLE_CALENDAR_METADATA_SCOPE,
        }
        if not required.issubset(scopes):
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="insufficient_permissions",
                credential_state="configured",
                message=(
                    "Google Calendar connection is missing event, availability, or "
                    "calendar metadata permissions."
                ),
                error_code="google_calendar_insufficient_scope",
            )
        token = credentials.get("access_token", "")
        if not token:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="invalid_credentials",
                credential_state="invalid",
                message="Google Calendar access token is unavailable.",
                error_code="google_calendar_access_token_unavailable",
            )
        try:
            calendar = GoogleCalendarClient(
                token, timeout_seconds=self.timeout_seconds, client=self.client
            ).primary_calendar()
        except ProviderOperationError as error:
            return _connection_validation_error(error)
        account = str(calendar.get("summary") or calendar.get("id") or "Primary calendar")
        return ConnectionValidation(
            valid=True,
            verified=True,
            status="connected",
            credential_state="configured",
            message="Google Calendar access verified.",
            metadata={
                "google_calendar_id": str(calendar.get("id") or "primary"),
                "google_calendar_timezone": str(calendar.get("timeZone") or "UTC"),
                "google_calendar_name": account,
            },
        )


class CalendlyConnectionValidator:
    def __init__(
        self, timeout_seconds: float = 10.0, client: httpx.Client | None = None
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.client = client

    def validate(
        self, provider: str, credentials: dict[str, str], scopes: list[str]
    ) -> ConnectionValidation:
        del provider
        if not set(CALENDLY_SCOPES).issubset(scopes):
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="insufficient_permissions",
                credential_state="configured",
                message="Calendly connection is missing scheduling and availability permissions.",
                error_code="calendly_insufficient_scope",
            )
        token = credentials.get("access_token", "")
        if not token:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="invalid_credentials",
                credential_state="invalid",
                message="Calendly access token is unavailable.",
                error_code="calendly_access_token_unavailable",
            )
        try:
            user = CalendlyClient(
                token, timeout_seconds=self.timeout_seconds, client=self.client
            ).current_user()
        except ProviderOperationError as error:
            return _connection_validation_error(error)
        email = str(user.get("email", "")).strip().lower()
        user_uri = str(user.get("uri", ""))
        if not email or not user_uri:
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="malformed_response",
                credential_state="configured",
                message="Calendly returned an invalid account identity.",
                error_code="calendly_user_malformed",
            )
        return ConnectionValidation(
            valid=True,
            verified=True,
            status="connected",
            credential_state="configured",
            message="Calendly scheduling access verified.",
            metadata={
                "calendly_email": email,
                "calendly_user_uri": user_uri,
                "calendly_timezone": str(user.get("timezone") or "UTC"),
                "calendly_scheduling_url": str(user.get("scheduling_url") or ""),
            },
        )


def _connection_validation_error(error: ProviderOperationError) -> ConnectionValidation:
    status = (
        "insufficient_permissions"
        if error.category == "insufficient_permissions"
        else "revoked"
        if error.category == "revoked"
        else "temporarily_unavailable"
        if error.retryable
        else "configuration_error"
        if error.category == "configuration"
        else "invalid_credentials"
    )
    return ConnectionValidation(
        valid=False,
        verified=False,
        status=cast(Any, status),
        credential_state=(
            "invalid" if status in {"revoked", "invalid_credentials"} else "configured"
        ),
        message=str(error),
        retryable=error.retryable,
        error_code=error.code,
        metadata={"category": error.category},
    )
