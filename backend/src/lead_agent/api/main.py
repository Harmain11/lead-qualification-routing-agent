from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from lead_agent import __version__
from lead_agent.api.dependencies import get_runtime_settings
from lead_agent.api.middleware import install_request_middleware
from lead_agent.api.routes import (
    analytics,
    auth,
    automations,
    calling,
    calendar,
    collaboration,
    contact_records,
    crm,
    crm_inbound,
    deals,
    email,
    email_automation,
    email_templates,
    follow_up,
    governance,
    health,
    icp,
    intake,
    integrations,
    intelligence,
    lead_assistant,
    leads,
    meeting_management,
    meetings,
    meta_webhooks,
    notifications,
    routing,
    site_access,
    team,
)
from lead_agent.infrastructure.observability import configure_logging
from lead_agent.infrastructure.secrets import get_secret_store

settings = get_runtime_settings()
configure_logging(json_logs=settings.log_format == "json", level=settings.log_level)
if settings.secret_store_provider == "managed":
    # Resolve the deployment binding during startup, before accepting requests.
    get_secret_store()
_public_docs = settings.app_env != "production"
app = FastAPI(
    title="Lead Qualification & Routing Agent",
    version=__version__,
    description="Grounded context analysis and explainable lead qualification.",
    docs_url="/docs" if _public_docs else None,
    redoc_url="/redoc" if _public_docs else None,
    openapi_url="/openapi.json" if _public_docs else None,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Authorization",
        "Content-Type",
        "X-Actor-ID",
        "X-CSRF-Token",
        "X-Request-ID",
        "X-Workspace-ID",
    ],
)
install_request_middleware(app, settings)
app.include_router(health.router)
app.include_router(lead_assistant.router)
app.include_router(lead_assistant.workspace_router)
app.include_router(auth.router)
app.include_router(site_access.router)
app.include_router(routing.config_router)
app.include_router(routing.router)
app.include_router(leads.router)
app.include_router(meetings.router)
app.include_router(meeting_management.router)
app.include_router(calendar.router)
app.include_router(notifications.router)
app.include_router(follow_up.router)
app.include_router(email.router)
app.include_router(email_automation.router)
app.include_router(email_templates.router)
app.include_router(intake.router)
app.include_router(crm.router)
app.include_router(crm_inbound.router)
app.include_router(deals.router)
app.include_router(integrations.router)
app.include_router(meta_webhooks.router)
app.include_router(analytics.router)
app.include_router(team.router)
app.include_router(icp.router)
app.include_router(contact_records.router)

app.include_router(governance.router)
app.include_router(intelligence.router)
app.include_router(automations.router)
app.include_router(collaboration.router)
app.include_router(calling.router)


# Production uses one public origin so browser cookies, CSRF, and OAuth callback
# handling do not depend on a Vite development proxy. The mount is conditional so
# the backend remains independently runnable during local API development.
frontend_dist = settings.resolve_path(settings.frontend_dist_path)
class CachedStaticFiles(StaticFiles):
    """Fingerprinted assets are immutable; the HTML shell must always revalidate."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200 and "cache-control" not in response.headers:
            hashed = path.startswith("assets/")
            response.headers["Cache-Control"] = (
                "public, max-age=31536000, immutable" if hashed else "no-cache"
            )
        return response


if frontend_dist.is_dir() and (frontend_dist / "index.html").is_file():
    app.mount("/", CachedStaticFiles(directory=str(frontend_dist), html=True), name="frontend")
