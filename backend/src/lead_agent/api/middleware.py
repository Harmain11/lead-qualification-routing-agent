from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

from lead_agent.api.site_access_security import (
    SITE_ACCESS_COOKIE_NAME,
    site_access_cookie_is_valid,
)
from lead_agent.infrastructure.observability import (
    reset_request_id,
    set_request_id,
)
from lead_agent.settings.config import Settings

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_LOGGER = logging.getLogger("lead_agent.http")
_SITE_ACCESS_PUBLIC_EXACT_PATHS = {
    "/v1/site-access/status",
    "/v1/site-access/unlock",
}
_SITE_ACCESS_PUBLIC_PREFIXES = (
    "/v1/webhooks/meta",
    "/v1/intake/public/",
)


def redact_intake_path(path: str) -> str:
    return re.sub(r"(/v1/intake/public/)[^/]+", r"\1[redacted]", path)


class IntakeAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5:
            address, method, path, version, status = record.args
            record.args = (address, method, redact_intake_path(str(path)), version, status)
        return True


def request_id_for(value: str | None) -> str:
    return value if value and _REQUEST_ID_PATTERN.fullmatch(value) else str(uuid4())


def apply_security_headers(
    response: Response, *, production: bool, interactive_docs: bool = False
) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        'camera=(), microphone=(self "https://phone.cloudtalk.io"), geolocation=()'
    )
    script_src = "'self' https://i.replit.com"
    if interactive_docs:
        script_src += " 'unsafe-inline' https://cdn.jsdelivr.net"
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; script-src {script_src}; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; "
        "font-src 'self' data:; connect-src 'self'; form-action 'self'; "
        "frame-src 'self' https://phone.cloudtalk.io https://workspace.aircall.io "
        "https://apps.ringcentral.com; "
        "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
    )
    if production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


def install_request_middleware(app: FastAPI, settings: Settings) -> None:
    # Keep metadata logging while removing capability tokens from Uvicorn access records.
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, IntakeAccessLogFilter) for item in access.filters):
        access.addFilter(IntakeAccessLogFilter())

    @app.middleware("http")
    async def correlate_and_secure(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request_id_for(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        token = set_request_id(request_id)
        started = time.perf_counter()
        safe_path = redact_intake_path(request.url.path)
        try:
            site_access_required = (
                settings.app_env == "production"
                or bool(getattr(settings, "corral_site_access_password", ""))
            )
            needs_site_access = (
                request.url.path.startswith("/v1/")
                and request.url.path not in _SITE_ACCESS_PUBLIC_EXACT_PATHS
                and not request.url.path.startswith(_SITE_ACCESS_PUBLIC_PREFIXES)
                and request.method != "OPTIONS"
                and site_access_required
                and not site_access_cookie_is_valid(
                    request.cookies.get(SITE_ACCESS_COOKIE_NAME),
                    settings,
                )
            )
            if needs_site_access:
                response = JSONResponse(
                    {
                        "detail": "Enter the Corral site password to continue.",
                        "code": "site_access_required",
                    },
                    status_code=423,
                )
            else:
                response = await call_next(request)
            apply_security_headers(
                response,
                production=settings.app_env == "production",
                interactive_docs=request.url.path in {"/docs", "/redoc"},
            )
            response.headers["X-Request-ID"] = request_id
            _LOGGER.info(
                "http.request",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "method": request.method,
                        "path": safe_path,
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
                        "path": safe_path,
                        "error_type": type(error).__name__,
                        "error_message": str(error)[:500],
                        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    }
                },
            )
            response = JSONResponse({"detail": "internal server error"}, status_code=500)
            apply_security_headers(
                response,
                production=settings.app_env == "production",
                interactive_docs=request.url.path in {"/docs", "/redoc"},
            )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            reset_request_id(token)
