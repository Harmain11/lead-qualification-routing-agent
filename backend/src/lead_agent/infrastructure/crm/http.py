from __future__ import annotations

from typing import Any, Protocol

import httpx


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
        return httpx.request(
            method,
            url,
            headers=headers,
            json=json,
            data=data,
            timeout=timeout,
        )
