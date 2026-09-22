from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol, cast

from fastapi import Depends, HTTPException, Request, status
from redis import Redis

from lead_agent.api.dependencies import get_runtime_settings
from lead_agent.infrastructure.observability import get_observability
from lead_agent.settings.config import Settings


@dataclass(frozen=True)
class RateLimitPolicy:
    limit: int
    window_seconds: int


class RateLimiter(Protocol):
    def hit(self, key: str, policy: RateLimitPolicy) -> tuple[bool, int]: ...


class MemoryRateLimiter:
    """Deterministic single-process limiter for local use and tests only."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, float]] = {}

    def hit(self, key: str, policy: RateLimitPolicy) -> tuple[bool, int]:
        now = self._clock()
        with self._lock:
            count, expires = self._windows.get(key, (0, now + policy.window_seconds))
            if expires <= now:
                count, expires = 0, now + policy.window_seconds
            count += 1
            self._windows[key] = (count, expires)
            retry_after = max(1, int(expires - now + 0.999))
            return count <= policy.limit, retry_after


class RedisRateLimiter:
    """Atomic fixed-window limiter backed by a shared Redis service."""

    _SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('TTL', KEYS[1])
return {count, ttl}
"""

    def __init__(self, client: Redis) -> None:
        self._client = client

    def hit(self, key: str, policy: RateLimitPolicy) -> tuple[bool, int]:
        result = cast(
            Any,
            self._client.eval(self._SCRIPT, 1, key, str(policy.window_seconds)),
        )
        count, ttl = int(result[0]), max(1, int(result[1]))
        return count <= policy.limit, ttl


def policy_for(settings: Settings, category: str) -> RateLimitPolicy:
    configured = {
        "authentication": (
            settings.rate_limit_authentication_max,
            settings.rate_limit_authentication_window_seconds,
        ),
        "oauth": (settings.rate_limit_oauth_max, settings.rate_limit_oauth_window_seconds),
        "credentials": (
            settings.rate_limit_credentials_max,
            settings.rate_limit_credentials_window_seconds,
        ),
        "provider_actions": (
            settings.rate_limit_provider_actions_max,
            settings.rate_limit_provider_actions_window_seconds,
        ),
        "agent": (settings.rate_limit_agent_max, settings.rate_limit_agent_window_seconds),
    }
    try:
        limit, window = configured[category]
    except KeyError as error:
        raise ValueError(f"unknown rate-limit category: {category}") from error
    return RateLimitPolicy(limit, window)


@lru_cache
def _cached_rate_limiter(
    backend: str,
    redis_url: str,
    connect_timeout: float,
    socket_timeout: float,
    retry_on_timeout: bool,
) -> RateLimiter:
    if backend == "redis":
        client = Redis.from_url(
            redis_url,
            socket_connect_timeout=connect_timeout,
            socket_timeout=socket_timeout,
            retry_on_timeout=retry_on_timeout,
            decode_responses=False,
        )
        return RedisRateLimiter(client)
    return MemoryRateLimiter()


def get_rate_limiter(settings: Settings) -> RateLimiter:
    return _cached_rate_limiter(
        settings.rate_limit_backend,
        settings.redis_url,
        settings.redis_connect_timeout_seconds,
        settings.redis_socket_timeout_seconds,
        settings.redis_retry_on_timeout,
    )


def _client_key(request: Request, category: str) -> str:
    host = request.client.host if request.client is not None else "unknown"
    digest = hashlib.sha256(host.encode()).hexdigest()
    return f"{category}:{digest}"


def rate_limit_dependency(category: str) -> Callable[..., None]:
    def enforce(
        request: Request,
        settings: Settings = Depends(get_runtime_settings),
    ) -> None:
        policy = policy_for(settings, category)
        try:
            limiter = get_rate_limiter(settings)
            allowed, retry_after = limiter.hit(
                f"{settings.app_name}:{settings.app_env}:{_client_key(request, category)}",
                policy,
            )
        except Exception as error:
            if settings.app_env == "production":
                get_observability().record_error(
                    "rate_limiter.unavailable",
                    category=category,
                    error_type=type(error).__name__,
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="request protection temporarily unavailable",
                    headers={"Retry-After": "5"},
                ) from error
            return
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(policy.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

    return enforce
