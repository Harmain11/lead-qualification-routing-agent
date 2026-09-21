# Lead Qualification & Routing Agent

Production-ready foundation for a channel-agnostic lead qualification, explainable routing, CRM synchronization, and sales analytics demo.

## Project status

Phases 1–4 are implemented locally: durable multi-channel intake, canonical normalization, deterministic/mock AI analysis, ICP qualification, configurable routing, deterministic rep assignment, sales action plans, and provider-neutral notifications. Salesforce, HubSpot, live channel credentials, and the final analytics dashboard remain deferred.

The source build plan is preserved in [`doc/Lead Qualification & Routing Agent — Build Plan.docx`](<doc/Lead Qualification & Routing Agent — Build Plan.docx>). The normalized requirements are in [`docs/requirements.md`](docs/requirements.md), and the production architecture is in [`docs/architecture.md`](docs/architecture.md).

## Recommended stack

- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, `uv`
- Async boundary: Celery with Redis; the current slice runs synchronously and keeps the queue boundary replaceable
- Database: PostgreSQL 16 with UUID identifiers and JSONB for raw/provider payloads and structured analysis
- Dashboard: React, TypeScript, and Vite
- AI boundary: `ReasoningProvider` with deterministic and OpenAI implementations; OpenAI is optional locally
- Notification boundary: `NotificationProvider` with deterministic/mock and Slack implementations

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
├── config/               # editable ICP and routing examples
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
- `GET /v1/leads/{lead_id}/pipeline-history` — inspect route/notify lifecycle events.
- `POST /v1/leads/{lead_id}/notifications/retry` — retry the current notification.
- `POST /v1/leads/{lead_id}/routing/reroute` — intentionally create a new route with a reason.
- `GET /health/live` and `GET /health/ready` — liveness/readiness checks.

## Configuration and secrets

Configuration is loaded from [`config/icp.example.yaml`](config/icp.example.yaml) and [`config/routing.example.yaml`](config/routing.example.yaml). Routing queues, reps, matching rules, action templates, and safe defaults are editable configuration rather than scattered code.

The default `LLM_PROVIDER=deterministic` and `NOTIFICATION_PROVIDER=mock` modes need no credentials. To use live providers, configure `OPENAI_API_KEY`/`OPENAI_MODEL` or `SLACK_BOT_TOKEN`/`SLACK_CHANNEL_ID` in the ignored local `.env`. Never commit those values. Salesforce and HubSpot are not implemented in this phase.

Every intake adapter emits the same durable source-event contract. Raw payloads, source attribution, attempts, processing state, and errors are retained before normalization, analysis, qualification, routing, and notification. Replaying the same external event does not create duplicate leads or notifications; explicit rerouting creates a new historical decision.

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
