from __future__ import annotations

from typing import Any, Protocol

import httpx

from lead_agent.infrastructure.observability import current_operation_id, current_request_id


class HttpResponse(Protocol):
    status_code: int
    headers: Any

    def json(self) -> Any: ...


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> HttpResponse: ...


class HttpxTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> httpx.Response:
        request_headers = dict(headers or {})
        if current_request_id() != "-":
            request_headers.setdefault("X-Request-ID", current_request_id())
        if current_operation_id() != "-":
            request_headers.setdefault("X-Operation-ID", current_operation_id())
        return httpx.request(
            method,
            url,
            headers=request_headers,
            json=json,
            data=data,
            timeout=timeout,
        )
