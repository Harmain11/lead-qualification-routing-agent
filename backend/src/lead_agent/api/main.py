from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lead_agent import __version__
from lead_agent.api.dependencies import get_runtime_settings
from lead_agent.api.routes import analytics, auth, crm, health, intake, integrations, leads, routing

settings = get_runtime_settings()
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
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(leads.router)
app.include_router(intake.router)
app.include_router(routing.router)
app.include_router(crm.router)
app.include_router(integrations.router)
app.include_router(analytics.router)
