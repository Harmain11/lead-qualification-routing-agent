from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from lead_agent.api.dependencies import get_runtime_settings
from lead_agent.api.site_access_security import (
    SITE_ACCESS_COOKIE_MAX_AGE,
    SITE_ACCESS_COOKIE_NAME,
    create_site_access_cookie,
    site_access_cookie_is_valid,
)
from lead_agent.infrastructure.rate_limit import rate_limit_dependency
from lead_agent.settings.config import Settings

router = APIRouter(prefix="/v1/site-access", tags=["site access"])


class SiteAccessStatusResponse(BaseModel):
    enabled: bool
    unlocked: bool


class SiteAccessUnlockRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class SiteAccessUnlockResponse(BaseModel):
    unlocked: bool


@router.get("/status", response_model=SiteAccessStatusResponse)
def site_access_status(
    request: Request,
    settings: Settings = Depends(get_runtime_settings),
) -> SiteAccessStatusResponse:
    configured = bool(settings.corral_site_access_password)
    enabled = settings.app_env == "production" or configured
    return SiteAccessStatusResponse(
        enabled=enabled,
        unlocked=not enabled
        or (
            configured
            and site_access_cookie_is_valid(
                request.cookies.get(SITE_ACCESS_COOKIE_NAME),
                settings,
            )
        ),
    )


@router.post(
    "/unlock",
    response_model=SiteAccessUnlockResponse,
    dependencies=[Depends(rate_limit_dependency("authentication"))],
)
def unlock_site_access(
    request: SiteAccessUnlockRequest,
    response: Response,
    settings: Settings = Depends(get_runtime_settings),
) -> SiteAccessUnlockResponse:
    expected = settings.corral_site_access_password
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Corral site access is not configured.",
        )
    if not hmac.compare_digest(request.password.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="The site password is incorrect.",
        )

    response.set_cookie(
        SITE_ACCESS_COOKIE_NAME,
        create_site_access_cookie(settings),
        max_age=SITE_ACCESS_COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.auth_cookie_secure or settings.app_env == "production",
        samesite=settings.auth_cookie_samesite,
        path="/",
    )
    return SiteAccessUnlockResponse(unlocked=True)
