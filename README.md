# Lead Qualification & Routing Agent

Production-ready foundation for a channel-agnostic lead qualification, explainable routing, CRM synchronization, and sales analytics demo.

## Project status

Phases 1–6.5 and Phase 7A1 are implemented locally: durable multi-channel intake, canonical normalization, deterministic/mock AI analysis, ICP qualification, configurable routing, notifications, provider-neutral CRM synchronization, workspace-scoped provider connections, controlled agent tools, analytics, production configuration validation, a managed-secret boundary, and hardened authentication/OAuth foundations. Live provider validation and later Phase 7 work remain deferred.

The source build plan is preserved in [`doc/Lead Qualification & Routing Agent — Build Plan.docx`](<doc/Lead Qualification & Routing Agent — Build Plan.docx>). The normalized requirements are in [`docs/requirements.md`](docs/requirements.md), and the production architecture is in [`docs/architecture.md`](docs/architecture.md).

## Recommended stack

- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, `uv`
- Async boundary: Celery with Redis; the current slice runs synchronously and keeps the queue boundary replaceable
- Database: PostgreSQL 16 with UUID identifiers and JSONB for raw/provider payloads and structured analysis
- Dashboard: React, TypeScript, and Vite
- AI boundary: `ReasoningProvider` with deterministic and OpenAI implementations; OpenAI is optional locally
- Notification boundary: `NotificationProvider` with deterministic/mock and Slack implementations
- CRM boundary: `CrmProvider` with deterministic/mock, Salesforce, and HubSpot implementations
- Connection boundary: workspace-owned `ProviderConnection` records containing secret references, never token values
- Agent boundary: permissioned, schema-validated tools with workspace/actor context, audit records, and idempotency

The system is a modular monolith for v1: one API, one worker boundary, one dashboard, and one database. Domain and provider ports keep future service extraction possible.

## Repository layout

```text
.
├── backend/src/lead_agent/
│   ├── api/              # FastAPI routes and typed responses
│   ├── application/      # pipeline, qualification, routing, and action use cases
│   ├── domain/           # canonical models, policies, ports, and configuration
│   ├── infrastructure/  # database, intake, reasoning, and notification adapters
│   ├── worker/           # future asynchronous task entry points
│   └── settings/         # typed runtime settings
├── backend/tests/        # unit and contract tests
├── frontend/src/         # React/TypeScript inspection experience
├── config/               # editable ICP, routing, and CRM mapping examples
├── docs/                 # requirements, architecture, and phased plan
├── infra/                # Docker Compose and Alembic migrations
├── scripts/              # seed and developer commands
├── .env.example
└── .gitignore
```

## Local setup

Prerequisites: Python 3.12+, Node.js 22+, Docker Desktop, and `uv`.

PowerShell from the repository root:

```powershell
Copy-Item .env.example .env
docker compose -f infra/compose/docker-compose.yml up -d
uv sync --project backend --extra dev
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend python -m lead_agent.seed
# Optional: populate the isolated Phase 6 demo workspace used by the product UI.
uv run --project backend python -m lead_agent.seed --demo
```

Run the API:

```powershell
uv run --project backend uvicorn lead_agent.api.main:app --reload --host 127.0.0.1 --port 8000
```

Run the dashboard in another terminal:

```powershell
Set-Location frontend
npm.cmd install
npm.cmd run dev
```

Open <http://localhost:5173>. API documentation is available at <http://localhost:8000/docs>.

If `uv` is unavailable, use `python -m venv .venv`, activate it, install with `python -m pip install -e "backend[dev]"`, set `$env:PYTHONPATH = "$pwd\backend\src"`, and run the equivalent commands from that environment.

## API surface

- `POST /v1/leads` — create, normalize, analyze, qualify, route, and persist a manual lead.
- `POST /v1/intake/web` — validated website webhook with optional HMAC-SHA256 verification.
- `POST /v1/intake/email` — provider-neutral inbound email fixture/intake contract.
- `POST /v1/intake/linkedin` — official LinkedIn lead-form/export fixture contract; no scraping.
- `POST /v1/intake/fourth` — selected Clutch notification-email fixture contract.
- `GET /v1/leads` and `GET /v1/leads/{lead_id}` — inspect canonical leads and current decisions.
- `GET /v1/leads/{lead_id}/qualification` — inspect score components and rationale.
- `GET /v1/leads/{lead_id}/routing` — inspect the current route, assignment, action plan, and notification state.
- `GET /v1/leads/{lead_id}/routing/action-plan` — retrieve the sales-facing action plan.
- `GET /v1/leads/{lead_id}/routing/history` — inspect append-only routing history.
- `GET /v1/leads/{lead_id}/notifications` — inspect notification attempts.
- `GET /v1/leads/{lead_id}/pipeline-history` — inspect the ordered received-through-CRM lifecycle.
- `POST /v1/leads/{lead_id}/notifications/retry` — retry the current notification.
- `POST /v1/leads/{lead_id}/routing/reroute` — intentionally create a new route with a reason.
- `GET /v1/leads/{lead_id}/crm-sync` — inspect current CRM status and provider record IDs.
- `GET /v1/leads/{lead_id}/crm-sync/history` — inspect append-only synchronization history.
- `POST /v1/leads/{lead_id}/crm-sync` — perform an idempotent synchronization.
- `POST /v1/leads/{lead_id}/crm-sync/retry` — manually retry a retryable synchronization.
- `GET /v1/integrations` — list safe provider setup metadata and current-workspace connections.
- `POST /v1/integrations/{provider}/connect` and `/{provider}/callback` — OAuth lifecycle boundary.
- `POST /v1/integrations/{provider}/credentials` — store a manual credential through the secret port.
- `POST /v1/integrations/connections/{id}/test` — validate connection state safely.
- `POST /v1/integrations/connections/{id}/disconnect` or `/reconnect` — rotate connection state without returning secrets.
- `GET /v1/analytics/{dashboard|channels|pipeline|revenue|reps|time-series|agent-activity|manual-review}` — workspace-scoped weekly, monthly, or annual product analytics.
- `GET /health/live` and `GET /health/ready` — liveness/readiness checks.

## Configuration and secrets

Configuration is loaded from [`config/icp.example.yaml`](config/icp.example.yaml), [`config/routing.example.yaml`](config/routing.example.yaml), and [`config/crm.example.yaml`](config/crm.example.yaml). Routing policy and Salesforce/HubSpot field mappings remain editable configuration rather than scattered code.

The defaults `LLM_PROVIDER=deterministic`, `NOTIFICATION_PROVIDER=mock`, and `CRM_PROVIDER=mock` need no credentials. `CRM_PROVIDER` remains a development fallback; a workspace-owned active Salesforce or HubSpot connection takes precedence. Development connection secrets use the process-local `SecretStore` implementation and disappear on restart. Production must bind the same port to a managed secret service.

Production requests use opaque, server-side sessions in HttpOnly cookies. Every product request resolves the authenticated user, verifies an active workspace membership, and only then constructs `WorkspaceContext`; a browser-supplied workspace ID is a selection hint, never authority. Development headers remain available only when `APP_ENV` is development/test/local and `AUTH_DEVELOPMENT_FALLBACK=true`.

Every intake adapter emits the same durable source-event contract. Raw payloads and every decision remain inspectable through CRM delivery. Replaying the same lead reuses its route, notification, and CRM synchronization; changed canonical CRM payloads create a new historical sync attempt while preserving provider entity IDs.

## Validation

Backend checks run in the supported container with:

```powershell
docker exec lead-agent-api sh -lc "ruff check src tests && mypy src && pytest -q"
```

Frontend checks run with:

```powershell
docker exec lead-agent-frontend sh -lc "npm run typecheck && npm run build"
```

See [`docs/implementation-plan.md`](docs/implementation-plan.md), [`docs/architecture.md`](docs/architecture.md), and [`docs/integrations.md`](docs/integrations.md) for design boundaries and deferred integration work.

## Phase 6 analytics and product experience

Analytics is aggregated on the server and every response includes generated-at, workspace, time-window, and demo/live provenance metadata. Revenue facts remain separate from qualification evidence and are labeled `crm`, `demo`, `mixed`, or `unavailable`; seeded revenue is never represented as production data.

After sign-in, the frontend derives the active workspace from the authenticated session. `VITE_WORKSPACE_ID` is used only by the explicit local development-session path, whose default is the reserved demo workspace (`00000000-0000-0000-0000-000000000006`). The demo seed command never inserts records into the default/live workspace.

The product shell provides Dashboard, Leads, Pipeline, Agents / AI Activity, Analytics, Integrations, Manual Review, and Settings. Its signature lead-intelligence network is one lazy-loaded React Three Fiber canvas backed by actual lead state. Mobile, reduced-motion, hidden-tab, and WebGL-unavailable paths use an accessible DOM/CSS topology.

## Phase 6.5 authentication and account linking

- `POST /v1/auth/signup`, `/signin`, `/signout`, and `/development-session` manage opaque sessions; `GET /v1/auth/session` returns safe identity and membership metadata.
- `POST /v1/auth/workspace` changes the current workspace only after membership verification.
- Cookie-authenticated mutations require CSRF validation. Passwords use salted `scrypt`; session and CSRF token digests, not raw tokens, are persisted.
- Migration `0010_production_authentication` adds users, memberships, and revocable/expiring sessions.
- `OAUTH_BROKER=provider` enables operator-owned OAuth applications. Customers only click Connect, sign in, and approve scopes.
- OAuth state expires after ten minutes, is single-use, and is bound to a PKCE verifier stored through `SecretStore`. Provider tokens never enter frontend code or connection API responses.
- `OAUTH_BROKER=development` remains a deterministic local callback loop for tests.

## Phase 7A1 production configuration and security

- `APP_ENV` is explicitly `development`, `test`, or `production`. Production startup rejects development authentication, insecure cookies, wildcard/non-HTTPS CORS, deterministic/mock providers, demo mode, local secret storage, development OAuth, placeholder security secrets, and implicit local persistence endpoints.
- `DevelopmentSecretStore` remains process-local for development/test. Production resolves a deployment-owned `ManagedSecretStore` backend through `MANAGED_SECRET_STORE_BACKEND=package.module:factory`; only opaque references and safe status metadata are persisted.
- Session cookies have bounded expiry, configurable SameSite behavior, Secure/HttpOnly production enforcement, authentication-time rotation, revocation, and CSRF protection. Passwords use uniquely salted `scrypt` at the current OWASP-equivalent minimum, and revoked memberships cannot restore or access their workspace.
- OAuth state is high entropy, hashed, expiring, single-use, atomically claimed, and bound to workspace, provider, callback, and actor. PKCE uses S256 and its verifier is replaced by provider credentials after exchange.
- Provider disconnect deactivates the local connection before remote revocation. Failed or unsupported remote revocation is recorded as safe metadata and cannot leave the connection usable.
- The frontend uses optional public `VITE_API_BASE_URL`; server credentials must never use the `VITE_*` namespace. Seed commands are disabled in production, and demo records remain isolated to the reserved demo workspace.
