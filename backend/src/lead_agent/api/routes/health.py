from fastapi import APIRouter, Depends, HTTPException, status
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from lead_agent import __version__
from lead_agent.api.schemas import HealthResponse
from lead_agent.infrastructure.db.session import get_db
from lead_agent.settings.config import Settings, get_settings

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
def liveness() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/health/ready", response_model=HealthResponse)
def readiness(
    session: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> HealthResponse:
    try:
        session.execute(text("SELECT 1"))
        Redis.from_url(settings.redis_url, socket_connect_timeout=1).ping()
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="dependencies unavailable"
        ) from error
    return HealthResponse(status="ok", version=__version__)
