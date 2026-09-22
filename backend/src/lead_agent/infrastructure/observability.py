from __future__ import annotations

import contextvars
import json
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

_REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_OPERATION_ID: contextvars.ContextVar[str] = contextvars.ContextVar("operation_id", default="-")
_PARENT_OPERATION_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "parent_operation_id", default="-"
)
_OPERATION_PROVIDER: contextvars.ContextVar[str] = contextvars.ContextVar("provider", default="-")
_OPERATION_CONNECTION: contextvars.ContextVar[str] = contextvars.ContextVar(
    "connection_id", default="-"
)
_SENSITIVE_FRAGMENTS = (
    "authorization",
    "cookie",
    "session",
    "csrf",
    "password",
    "authorization_code",
    "access_token",
    "refresh_token",
    "api_key",
    "client_secret",
    "private_token",
    "credential",
    "secret",
)
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")
_HEADER_SECRET_PATTERN = re.compile(
    r"(?i)((?:authorization|set-cookie|cookie|x-csrf-token|password|client_secret|api_key)\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+"
)
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)([?&](?:code|access_token|refresh_token|api_key|token|client_secret)=)[^&#\s]+"
)
_JSON_SECRET_PATTERN = re.compile(
    r'(?i)("(?:authorization|cookie|session_token|csrf_token|password|authorization_code|'
    r'access_token|refresh_token|api_key|token|client_secret|private_token|credential|secret)"\s*:\s*)'
    r'("[^"\\]*(?:\\.[^"\\]*)*"|[^,}\s]+)'
)


def set_request_id(value: str) -> contextvars.Token[str]:
    return _REQUEST_ID.set(value)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _REQUEST_ID.reset(token)


def current_request_id() -> str:
    return _REQUEST_ID.get()


def current_operation_id() -> str:
    return _OPERATION_ID.get()


def provider_context() -> dict[str, str]:
    return {
        "request_id": current_request_id(),
        "operation_id": current_operation_id(),
        "parent_operation_id": _PARENT_OPERATION_ID.get(),
        "provider": _OPERATION_PROVIDER.get(),
        "connection_id": _OPERATION_CONNECTION.get(),
    }


@contextmanager
def provider_operation(provider: str, connection_id: str | None = None) -> Iterator[dict[str, str]]:
    tokens = (
        _PARENT_OPERATION_ID.set(current_operation_id()),
        _OPERATION_ID.set(str(uuid4())),
        _OPERATION_PROVIDER.set(provider),
        _OPERATION_CONNECTION.set(connection_id or "-"),
    )
    try:
        yield provider_context()
    finally:
        _PARENT_OPERATION_ID.reset(tokens[0])
        _OPERATION_ID.reset(tokens[1])
        _OPERATION_PROVIDER.reset(tokens[2])
        _OPERATION_CONNECTION.reset(tokens[3])


def redact_text(value: str) -> str:
    value = _BEARER_PATTERN.sub(r"\1[redacted]", value)
    value = _HEADER_SECRET_PATTERN.sub(r"\1[redacted]", value)
    value = _KEY_VALUE_PATTERN.sub(r"\1[redacted]", value)
    return _JSON_SECRET_PATTERN.sub(r'\1"[redacted]"', value)


def redact(value: Any, key: str | None = None) -> Any:
    if key is not None and any(fragment in key.lower() for fragment in _SENSITIVE_FRAGMENTS):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(item_key): redact(item, str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, bytes):
        return "[binary data]"
    return value


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields = getattr(record, "fields", {})
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "event": redact_text(record.getMessage()),
            "request_id": current_request_id(),
        }
        if isinstance(fields, dict):
            payload.update(redact(fields))
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else "Error"
        return json.dumps(payload, separators=(",", ":"), default=str)


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            record.fields = redact(fields)
        return True


class Observability(Protocol):
    def record_error(self, event: str, **fields: Any) -> None: ...

    def record_duration(self, event: str, duration_ms: float, **fields: Any) -> None: ...

    def provider_failure(self, provider: str, category: str, **fields: Any) -> None: ...

    def retry_exhausted(self, operation: str, **fields: Any) -> None: ...

    def job_failure(self, task_name: str, **fields: Any) -> None: ...


class LoggingObservability:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("lead_agent.observability")

    def _event(self, name: str, level: int = logging.INFO, **fields: Any) -> None:
        self.logger.log(level, name, extra={"fields": redact(fields)})

    def record_error(self, event: str, **fields: Any) -> None:
        self._event(event, logging.ERROR, **fields)

    def record_duration(self, event: str, duration_ms: float, **fields: Any) -> None:
        self._event(event, duration_ms=round(duration_ms, 2), **fields)

    def provider_failure(self, provider: str, category: str, **fields: Any) -> None:
        self._event(
            "provider.failure", logging.WARNING, provider=provider, category=category, **fields
        )

    def retry_exhausted(self, operation: str, **fields: Any) -> None:
        self._event("retry.exhausted", logging.WARNING, operation=operation, **fields)

    def job_failure(self, task_name: str, **fields: Any) -> None:
        self._event("background_job.failure", logging.ERROR, task_name=task_name, **fields)


def configure_logging(*, json_logs: bool, level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactionFilter())
    if json_logs:
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_observability() -> Observability:
    return LoggingObservability()
