"""Provider-owned URLs, low-risk connection probes and explicit calling actions."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, cast
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, Field

from lead_agent.domain.integrations import ConnectionValidation, ProviderValidationStatus
from lead_agent.domain.provider_errors import (
    ProviderOperationError,
    provider_http_error,
    provider_timeout_error,
)

DIALERS = frozenset({"twilio", "aircall", "ringcentral", "vapi", "retell", "cloudtalk"})
BASE_URLS = {
    "gohighlevel": "https://services.leadconnectorhq.com",
    "twilio": "https://api.twilio.com",
    "aircall": "https://api.aircall.io",
    "ringcentral": "https://platform.ringcentral.com",
    "vapi": "https://api.vapi.ai",
    "retell": "https://api.retellai.com",
    "cloudtalk": "https://my.cloudtalk.io",
}


def phone(value: str) -> str:
    cleaned = re.sub(r"[ ()\-]", "", value or "")
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", cleaned):
        raise ValueError("Use a phone number with +country code, for example +14155550123.")
    return cleaned


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value or ""):
        raise ValueError("A valid provider resource ID is required.")
    return value


def timestamp(value: Any) -> datetime | None:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, (float, int)):
            return datetime.fromtimestamp(value / 1000 if value > 10**11 else value, UTC)
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            result = parsedate_to_datetime(str(value))
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def recording_link(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2000:
        return None
    parsed = urlsplit(value)
    return (
        value
        if parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        else None
    )


class CallSnapshot(BaseModel):
    external_id: str = Field(min_length=1, max_length=255)
    status: str = Field(default="unknown", max_length=64)
    direction: str = "unknown"
    customer_phone: str
    started_at: datetime | None = None
    duration_seconds: int | None = None
    transcript: str | None = None
    recording_url: str | None = None


class CommunicationsClient:
    def __init__(
        self, provider: str, credentials: dict[str, str], *, client: httpx.Client | None = None
    ):
        if provider not in BASE_URLS:
            raise ValueError("Unsupported communications provider.")
        self.provider, self.credentials = provider, credentials
        self.client = client
        self._token: str | None = None
        self._token_until = 0.0

    def close(self) -> None:
        pass  # Each owned HTTP transport is closed at its request boundary.

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: Any = None,
        form: Any = None,
        base_url: str | None = None,
    ) -> Any:
        headers: dict[str, str] = {"Accept": "application/json"}
        auth = None
        c = self.credentials
        if self.provider == "twilio":
            auth = httpx.BasicAuth(c["account_sid"], c["auth_token"])
        elif self.provider == "aircall":
            auth = httpx.BasicAuth(c["api_id"], c["api_token"])
        elif self.provider == "cloudtalk":
            auth = httpx.BasicAuth(c["access_key_id"], c["access_key_secret"])
        elif self.provider == "ringcentral" and path == "/restapi/oauth/token":
            auth = httpx.BasicAuth(c["client_id"], c["client_secret"])
        else:
            token = (
                self.ringcentral_token()
                if self.provider == "ringcentral"
                else c.get("access_token", c.get("api_key", ""))
            )
            headers["Authorization"] = f"Bearer {token}"
        if self.provider == "gohighlevel":
            headers["Version"] = "2021-07-28"
        try:
            transport = self.client or httpx.Client(timeout=20, follow_redirects=False)
            try:
                response = transport.request(
                    method,
                    (base_url or BASE_URLS[self.provider]) + path,
                    headers=headers,
                    auth=auth,
                    params=params,
                    json=payload,
                    data=form,
                )
            finally:
                if self.client is None:
                    transport.close()
        except httpx.TimeoutException:
            raise provider_timeout_error(self.provider) from None
        except httpx.HTTPError:
            raise ProviderOperationError(
                "The provider request could not be completed.",
                provider=self.provider,
                code="communications_transport_failed",
                category="temporarily_unavailable",
                retryable=False,
            ) from None
        if not 200 <= response.status_code < 300:
            raise provider_http_error(self.provider, response.status_code)
        if response.status_code == 204:
            return {}
        if len(response.content) > 8_000_000:
            raise ValueError("Provider response exceeded the supported size.")
        try:
            result = response.json()
        except ValueError:
            raise ValueError("Provider returned an invalid response.") from None
        if not isinstance(result, (dict, list)):
            raise ValueError("Provider returned an invalid response.")
        return result

    def ringcentral_token(self) -> str:
        if self._token and self._token_until > time.monotonic():
            return self._token
        result = self.request(
            "POST",
            "/restapi/oauth/token",
            form={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": self.credentials["jwt"],
            },
        )
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("RingCentral did not return an access token.")
        self._token, self._token_until = (
            token,
            time.monotonic() + max(1, min(int(result.get("expires_in", 3600)), 3600) - 60),
        )
        return token

    def verify(self) -> tuple[str, str]:
        c, p = self.credentials, self.provider
        if p == "gohighlevel":
            location = identifier(c["location_id"])
            data = self.request("GET", "/locations/" + location).get("location", {})
            if str(data.get("id", "")) != location:
                raise ValueError("GoHighLevel returned a different location.")
            probe = self.request(
                "POST",
                "/contacts/search",
                payload={"locationId": location, "pageLimit": 1, "page": 1},
            )
            if not isinstance(probe.get("contacts"), list):
                raise ValueError("GoHighLevel contact search permission could not be verified.")
            return location, str(data.get("name") or "GoHighLevel location")[:255]
        if p == "twilio":
            sid = identifier(c["account_sid"])
            if not re.fullmatch(r"AC[0-9a-fA-F]{32}", sid):
                raise ValueError("A Twilio Account SID beginning with AC is required.")
            data = self.request("GET", f"/2010-04-01/Accounts/{sid}.json")
            numbers = self.request(
                "GET",
                f"/2010-04-01/Accounts/{sid}/IncomingPhoneNumbers.json",
                params={"PhoneNumber": phone(c["from_number"]), "PageSize": 20},
            ).get("incoming_phone_numbers", [])
            if (
                data.get("sid") != sid
                or data.get("status") != "active"
                or not any(
                    n.get("phone_number") == phone(c["from_number"])
                    and n.get("capabilities", {}).get("voice")
                    for n in numbers
                )
            ):
                raise ValueError("Use an active Twilio account and an owned voice-enabled number.")
            return sid, str(data.get("friendly_name") or "Twilio account")[:255]
        if p == "aircall":
            user_id, number_id = identifier(c["user_id"]), identifier(c["number_id"])
            if not user_id.isdigit() or not number_id.isdigit():
                raise ValueError("Aircall user and number IDs must be numeric.")
            data = self.request("GET", f"/v1/users/{user_id}").get("user", {})
            number = self.request("GET", f"/v1/numbers/{number_id}").get("number", {})
            if str(data.get("id")) != user_id or str(number.get("id")) != number_id:
                raise ValueError("Aircall user or number could not be verified.")
            return user_id, str(data.get("name") or "Aircall user")[:255]
        if p == "ringcentral":
            phone(c["from_number"])
            data = self.request("GET", "/restapi/v1.0/account/~/extension/~")
            if not data.get("id") or data.get("status") != "Enabled":
                raise ValueError("Use an enabled RingCentral extension.")
            return str(data["id"]), str(data.get("name") or "RingCentral extension")[:255]
        if p == "vapi":
            agent_id, number_id = identifier(c["assistant_id"]), identifier(c["phone_number_id"])
            data = self.request("GET", "/assistant/" + agent_id)
            number = self.request("GET", "/phone-number/" + number_id)
            if (
                data.get("id") != agent_id
                or number.get("id") != number_id
                or (data.get("orgId") and number.get("orgId") != data["orgId"])
            ):
                raise ValueError(
                    "Verify the assistant and phone number in the same Vapi organization."
                )
            return agent_id, str(data.get("name") or "Vapi assistant")[:255]
        if p == "cloudtalk":
            agent_id = identifier(c["agent_id"])
            if not agent_id.isdigit():
                raise ValueError("CloudTalk agent ID must be numeric.")
            result = self.request(
                "GET",
                "/api/agents/index.json",
                params={"id": agent_id, "limit": 100, "page": 1},
            )
            response_data = result.get("responseData", {})
            agents = response_data.get("data", []) if isinstance(response_data, dict) else []
            agent = next(
                (
                    item
                    for item in agents
                    if isinstance(item, dict) and str(item.get("id")) == agent_id
                ),
                None,
            )
            if agent is None:
                raise ValueError("CloudTalk agent could not be verified for this account.")
            return agent_id, str(agent.get("name") or "CloudTalk agent")[:255]
        agent_id = identifier(c["agent_id"])
        data = self.request("GET", "/get-agent/" + agent_id)
        number = self.request("GET", "/get-phone-number/" + quote(phone(c["from_number"]), safe=""))
        if data.get("agent_id") != agent_id or number.get("phone_number") != phone(
            c["from_number"]
        ):
            raise ValueError("Retell agent or phone number could not be verified.")
        return agent_id, str(data.get("agent_name") or "Retell agent")[:255]

    def start_call(
        self, target: str, rep_phone: str | None, request_id: str
    ) -> tuple[str | None, str]:
        target = phone(target)
        c, p = self.credentials, self.provider
        if p == "twilio":
            rep = phone(rep_phone or "")
            if rep == target or rep == phone(c["from_number"]):
                raise ValueError("Your rep number must differ from the lead and caller-ID numbers.")
            data = self.request(
                "POST",
                f"/2010-04-01/Accounts/{identifier(c['account_sid'])}/Calls.json",
                form={
                    "To": rep,
                    "From": phone(c["from_number"]),
                    "Twiml": (
                        f'<Response><Dial callerId="{phone(c["from_number"])}">'
                        f"{target}</Dial></Response>"
                    ),
                },
            )
            result = data.get("sid")
        elif p == "aircall":
            self.request(
                "POST",
                f"/v1/users/{identifier(c['user_id'])}/calls",
                payload={"number_id": int(c["number_id"]), "to": target},
            )
            return None, "requested"
        elif p == "ringcentral":
            data = self.request(
                "POST",
                "/restapi/v1.0/account/~/extension/~/ring-out",
                payload={
                    "from": {"phoneNumber": phone(rep_phone or c["from_number"])},
                    "to": {"phoneNumber": target},
                    "playPrompt": True,
                },
            )
            result = data.get("id")
        elif p == "cloudtalk":
            agent_id = identifier(c["agent_id"])
            if not agent_id.isdigit():
                raise ValueError("CloudTalk agent ID must be numeric.")
            self.request(
                "POST",
                "/api/calls/create.json",
                payload={"agent_id": int(agent_id), "callee_number": target},
            )
            # CloudTalk accepts the request but does not return a call ID. The
            # durable local request receipt prevents retrying this POST.
            return None, "requested"
        elif p == "vapi":
            data = self.request(
                "POST",
                "/call",
                payload={
                    "assistantId": identifier(c["assistant_id"]),
                    "phoneNumberId": identifier(c["phone_number_id"]),
                    "customer": {"number": target},
                    "metadata": {"corral_request_id": request_id},
                },
            )
            result = data.get("id")
        elif p == "retell":
            data = self.request(
                "POST",
                "/v2/create-phone-call",
                payload={
                    "from_number": phone(c["from_number"]),
                    "to_number": target,
                    "override_agent_id": identifier(c["agent_id"]),
                    "idempotency_key": request_id,
                    "metadata": {"corral_request_id": request_id},
                },
            )
            result = data.get("call_id")
        else:
            raise ValueError("This provider is not a dialer.")
        if not result:
            raise ValueError(
                "Call submission was sent but the provider returned no call ID. Check the "
                "provider before trying again."
            )
        return str(result)[:255], "queued"

    def recent_calls(self, target: str) -> list[CallSnapshot]:
        target = phone(target)
        p, c = self.provider, self.credentials
        if p == "twilio":
            path = f"/2010-04-01/Accounts/{identifier(c['account_sid'])}/Calls.json"
            items = self.request("GET", path, params={"To": target, "PageSize": 50}).get(
                "calls", []
            ) + self.request("GET", path, params={"From": target, "PageSize": 50}).get("calls", [])
        elif p == "aircall":
            items = self.request(
                "GET", "/v1/calls/search", params={"phone_number": target, "per_page": 50}
            ).get("calls", [])
        elif p == "ringcentral":
            items = self.request(
                "GET",
                "/restapi/v1.0/account/~/extension/~/call-log",
                params={"phoneNumber": target, "perPage": 50, "view": "Detailed"},
            ).get("records", [])
        elif p == "vapi":
            items = self.request("GET", "/call", params={"limit": 50})
        elif p == "retell":
            items = self.request(
                "POST", "/v2/list-calls", payload={"limit": 50, "sort_order": "descending"}
            )
        elif p == "cloudtalk":
            digits = re.sub(r"\D", "", target)
            result = self.request(
                "GET",
                "/api/calls/index.json",
                params={"public_external": digits, "limit": 25, "page": 1},
            )
            response_data = result.get("responseData", {})
            items = response_data.get("data", []) if isinstance(response_data, dict) else []
        else:
            raise ValueError("This provider has no call history.")
        if not isinstance(items, list):
            raise ValueError("Provider returned invalid call history.")
        result = []
        for item in items[:100]:
            if not isinstance(item, dict):
                continue
            try:
                call = self.normalize_call(item, target)
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            if call:
                result.append(call)
        return result

    def normalize_call(self, item: dict[str, Any], target: str) -> CallSnapshot | None:
        p = self.provider
        if p == "twilio":
            numbers = [item.get("to"), item.get("from")]
            call_id, status, start, duration = (
                item.get("sid"),
                item.get("status"),
                item.get("start_time"),
                item.get("duration"),
            )
            transcript, recording = None, None
        elif p == "aircall":
            numbers = [item.get("raw_digits")]
            call_id, status, start, duration = (
                item.get("id"),
                "completed" if item.get("ended_at") else "in_progress",
                item.get("started_at"),
                item.get("duration"),
            )
            transcript, recording = None, item.get("recording")
        elif p == "ringcentral":
            numbers = [
                item.get("to", {}).get("phoneNumber"),
                item.get("from", {}).get("phoneNumber"),
            ]
            call_id, status, start, duration = (
                item.get("id"),
                item.get("result"),
                item.get("startTime"),
                item.get("duration"),
            )
            transcript, recording = None, None
        elif p == "vapi":
            numbers = [item.get("customer", {}).get("number")]
            call_id, status, start = (
                item.get("id"),
                item.get("status"),
                item.get("startedAt") or item.get("createdAt"),
            )
            begin, end = timestamp(start), timestamp(item.get("endedAt"))
            duration = max(0, int((end - begin).total_seconds())) if begin and end else None
            artifact = item.get("artifact") or {}
            transcript, recording = (
                item.get("transcript") or artifact.get("transcript"),
                item.get("recordingUrl") or artifact.get("recordingUrl"),
            )
        elif p == "cloudtalk":
            cdr = item.get("Cdr") or item.get("cdr") or item
            contact = item.get("Contact") or item.get("contact") or {}
            call_number = item.get("CallNumber") or item.get("call_number") or {}
            numbers = [
                cdr.get("public_external"),
                cdr.get("external_number"),
                contact.get("phone"),
                call_number.get("number"),
            ]
            target_digits = re.sub(r"\D", "", target)
            if not any(
                re.sub(r"\D", "", str(number or "")) == target_digits
                for number in numbers
            ):
                return None
            call_id = cdr.get("id") or item.get("id")
            status = cdr.get("status") or cdr.get("call_status") or "completed"
            start = cdr.get("started_at") or cdr.get("start_time")
            duration = cdr.get("talking_time") or cdr.get("duration")
            transcript, recording = None, cdr.get("recording_link")
            if not call_id:
                return None
            try:
                call_id = identifier(str(call_id))
            except ValueError:
                return None
            ci_path = f"/v1/ai/calls/{quote(str(call_id), safe='')}"
            try:
                chunks: list[str] = []
                offset, total = 0, 0
                while offset < total or offset == 0:
                    transcription = self.request(
                        "GET",
                        ci_path + "/transcription",
                        params={"limit": 1000, "offset": offset},
                        base_url="https://api.cloudtalk.io",
                    )
                    transcript_data = transcription.get("data", {})
                    segments = (
                        transcript_data.get("segments", [])
                        if isinstance(transcript_data, dict)
                        else []
                    )
                    chunks.extend(
                        str(segment.get("text", "")).strip()
                        for segment in segments
                        if isinstance(segment, dict) and segment.get("text")
                    )
                    pagination = transcription.get("pagination", {})
                    total = int(pagination.get("total", len(segments))) if isinstance(
                        pagination, dict
                    ) else len(segments)
                    if not segments or offset + 1000 >= total:
                        break
                    offset += 1000
                transcript = " ".join(chunks)[:100000] or None
            except ProviderOperationError as error:
                # Missing CI data is normal for calls without transcription;
                # authentication and service failures must still surface.
                if error.category not in {"not_found", "invalid_request"}:
                    raise
            except (ValueError, TypeError, AttributeError):
                transcript = None
            if not recording:
                try:
                    details = self.request(
                        "GET",
                        ci_path + "/details-link",
                        base_url="https://api.cloudtalk.io",
                    )
                    recording = details.get("link")
                except ProviderOperationError as error:
                    if error.category not in {"not_found", "invalid_request"}:
                        raise
        else:
            numbers = [item.get("from_number"), item.get("to_number")]
            call_id, status, start = (
                item.get("call_id"),
                item.get("call_status"),
                item.get("start_timestamp"),
            )
            duration = (
                int(item["duration_ms"] / 1000) if item.get("duration_ms") is not None else None
            )
            transcript, recording = item.get("transcript"), item.get("recording_url")
        matches = []
        for number in numbers:
            if p == "cloudtalk":
                matches.append(
                    re.sub(r"\D", "", str(number or "")) == re.sub(r"\D", "", target)
                )
                continue
            try:
                matches.append(phone(number or "") == target)
            except ValueError:
                matches.append(False)
        if not any(matches) or not call_id:
            return None
        return CallSnapshot(
            external_id=str(call_id)[:255],
            customer_phone=target,
            status=str(status or "unknown")[:64],
            direction=str(item.get("direction") or item.get("type") or "unknown")[:64],
            started_at=timestamp(start),
            duration_seconds=max(0, min(int(duration), 86400)) if duration is not None else None,
            transcript=transcript[:100000] if isinstance(transcript, str) else None,
            recording_url=recording_link(recording),
        )


class CommunicationsValidator:
    def __init__(self, *, client: httpx.Client | None = None):
        self.client = client

    def validate(
        self, provider: str, credentials: dict[str, str], scopes: list[str]
    ) -> ConnectionValidation:
        try:
            adapter = CommunicationsClient(provider, credentials, client=self.client)
            try:
                account_id, name = adapter.verify()
            finally:
                adapter.close()
        except ProviderOperationError as error:
            statuses = {
                "invalid_credentials": "invalid_credentials",
                "insufficient_permissions": "insufficient_permissions",
                "rate_limited": "rate_limited",
                "timeout": "transport_failure",
            }
            return ConnectionValidation(
                valid=False,
                verified=False,
                status=cast(
                    ProviderValidationStatus, statuses.get(error.category, "configuration_error")
                ),
                credential_state="invalid"
                if error.category == "invalid_credentials"
                else "configured",
                message=str(error),
                retryable=error.retryable,
                error_code=error.code,
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            return ConnectionValidation(
                valid=False,
                verified=False,
                status="configuration_error",
                credential_state="configured",
                message=(
                    "The provider account or calling resources could not be verified. "
                    "Check the setup fields and permissions."
                ),
                error_code="communications_setup_invalid",
            )
        return ConnectionValidation(
            valid=True,
            verified=True,
            status="connected",
            credential_state="configured",
            message="Provider account and configured resources verified.",
            metadata={"verified_account_id": account_id, "verified_account_name": name},
        )
