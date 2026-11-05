from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from lead_agent.settings.config import Settings

SITE_ACCESS_COOKIE_NAME = "corral_site_access"
SITE_ACCESS_COOKIE_MAX_AGE = 12 * 60 * 60


def create_site_access_cookie(settings: Settings, *, now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else int(now)
    expires_at = issued_at + SITE_ACCESS_COOKIE_MAX_AGE
    nonce = secrets.token_urlsafe(24)
    payload = f"{expires_at}.{nonce}"
    signature = _signature(payload, settings)
    return f"{payload}.{signature}"


def site_access_cookie_is_valid(
    value: str | None,
    settings: Settings,
    *,
    now: int | None = None,
) -> bool:
    if not value or not getattr(settings, "corral_site_access_password", ""):
        return False
    try:
        expires_text, nonce, signature = value.split(".", 2)
        expires_at = int(expires_text)
        current_time = int(time.time()) if now is None else int(now)
    except (TypeError, ValueError):
        return False
    if not nonce or expires_at <= current_time:
        return False
    if expires_at > current_time + SITE_ACCESS_COOKIE_MAX_AGE + 60:
        return False
    payload = f"{expires_at}.{nonce}"
    return hmac.compare_digest(signature, _signature(payload, settings))


def _signature(payload: str, settings: Settings) -> str:
    key = hashlib.sha256(
        f"{settings.secret_key}\0{settings.corral_site_access_password}".encode()
    ).digest()
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
