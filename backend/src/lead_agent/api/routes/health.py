from fastapi import APIRouter, Depends, HTTPException, status
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from lead_agent import __version__
from lead_agent.api.schemas import HealthResponse
from lead_agent.infrastructure.db.session import get_db
from lead_agent.infrastructure.observability import get_observability
from lead_agent.settings.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
def liveness() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/health/ready", response_model=HealthResponse)
def readiness(
    session: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> HealthResponse:
    components: dict[str, str] = {}
    try:
        session.execute(text("SELECT 1"))
        components["postgresql"] = "ok"
    except Exception:
        components["postgresql"] = "unavailable"
    if settings.redis_required:
        try:
            client = Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=settings.redis_connect_timeout_seconds,
                socket_timeout=settings.redis_socket_timeout_seconds,
                retry_on_timeout=settings.redis_retry_on_timeout,
            )
            try:
                client.ping()
            finally:
                client.close()
            components["redis"] = "ok"
        except Exception:
            components["redis"] = "unavailable"
    else:
        components["redis"] = "not_required"
    ready = all(value in {"ok", "not_required"} for value in components.values())
    if not ready:
        get_observability().record_error(
            "health.readiness_failed",
            components=components,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "components": components},
        )
    return HealthResponse(status="ready", version=__version__, components=components)
