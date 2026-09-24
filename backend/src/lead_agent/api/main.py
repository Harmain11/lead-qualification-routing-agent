from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lead_agent import __version__
from lead_agent.api.dependencies import get_runtime_settings
from lead_agent.api.middleware import install_request_middleware
from lead_agent.api.routes import (
    analytics,
    auth,
    crm,
    crm_inbound,
    health,
    icp,
    intake,
    integrations,
    leads,
    routing,
    team,
)
from lead_agent.infrastructure.observability import configure_logging
from lead_agent.infrastructure.secrets import get_secret_store

settings = get_runtime_settings()
configure_logging(json_logs=settings.log_format == "json", level=settings.log_level)
if settings.secret_store_provider == "managed":
    # Resolve the deployment binding during startup, before accepting requests.
    get_secret_store()
app = FastAPI(
    title="Lead Qualification & Routing Agent",
    version=__version__,
    description="Grounded context analysis and explainable lead qualification.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
install_request_middleware(app, settings)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(leads.router)
app.include_router(intake.router)
app.include_router(routing.router)
app.include_router(crm.router)
app.include_router(crm_inbound.router)
app.include_router(integrations.router)
app.include_router(analytics.router)
app.include_router(team.router)
app.include_router(icp.router)
