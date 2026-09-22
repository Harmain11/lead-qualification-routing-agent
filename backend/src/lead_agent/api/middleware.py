from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

from lead_agent.infrastructure.observability import (
    reset_request_id,
    set_request_id,
)
from lead_agent.settings.config import Settings

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_LOGGER = logging.getLogger("lead_agent.http")


def request_id_for(value: str | None) -> str:
    return value if value and _REQUEST_ID_PATTERN.fullmatch(value) else str(uuid4())


def apply_security_headers(response: Response, *, production: bool) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
    )
    if production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


def install_request_middleware(app: FastAPI, settings: Settings) -> None:
    # Kept as a helper so all response headers and request logging share one boundary.
    @app.middleware("http")
    async def correlate_and_secure(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request_id_for(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        token = set_request_id(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            apply_security_headers(response, production=settings.app_env == "production")
            response.headers["X-Request-ID"] = request_id
            _LOGGER.info(
                "http.request",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                        "workspace_id": getattr(request.state, "workspace_id", None),
                        "actor_id": getattr(request.state, "actor_id", None),
                    }
                },
            )
            return response
        except Exception as error:
            _LOGGER.error(
                "http.request.failed",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "error_type": type(error).__name__,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    }
                },
            )
            response = JSONResponse({"detail": "internal server error"}, status_code=500)
            apply_security_headers(response, production=settings.app_env == "production")
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            reset_request_id(token)
