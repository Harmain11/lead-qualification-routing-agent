# Lead Qualification & Routing Agent

Production-ready foundation for a channel-agnostic lead qualification, explainable routing, CRM synchronization, and sales analytics demo.

This repository currently contains the architecture, configuration contracts, documentation, and implementation scaffold. External integrations and the full application are intentionally not implemented yet.

## Project status

Phase 1 — local application foundation and first working lead-processing vertical slice.

The source build plan is preserved in [`doc/Lead Qualification & Routing Agent — Build Plan.docx`](<doc/Lead Qualification & Routing Agent — Build Plan.docx>). The normalized requirements are summarized in [`docs/requirements.md`](docs/requirements.md), and the production architecture is documented in [`docs/architecture.md`](docs/architecture.md).

## Recommended stack

- **Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, `uv`
- **Async processing:** Celery with Redis initially; keep the queue port replaceable with a managed broker later
- **Database:** PostgreSQL 16 with UUID identifiers, JSONB for provider payloads, append-only decision/audit records, and materialized analytics views as needed
- **Dashboard:** React, TypeScript, Vite, TanStack Query, React Router, and a charting library such as Recharts
- **AI boundary:** an internal `ReasoningProvider` port. The planned OpenAI adapter will use the Responses API with schema-constrained JSON outputs, but it is not part of this scaffold. See the [official OpenAI Responses API reference](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).
- **Operations:** Docker Compose for local dependencies, OpenTelemetry-compatible tracing, structured JSON logs, health/readiness endpoints, and CI checks for formatting, typing, tests, migrations, and dependency auditing

The system is intentionally a modular monolith for v1: one deployable API, one worker process, one dashboard, and one database. The domain and provider ports keep the system ready to split services later if throughput or tenant isolation requires it.

## Repository layout

```text
.
├── backend/
│   ├── src/lead_agent/
│   │   ├── api/              # HTTP routes, webhook verification, API dependencies
│   │   ├── application/      # use cases and orchestration of domain operations
│   │   ├── domain/           # entities, value objects, events, policies, ports
│   │   ├── infrastructure/  # database, queue, provider adapters, observability
│   │   ├── worker/           # asynchronous task entry points and retry policies
│   │   └── settings/         # typed runtime configuration
│   └── tests/               # backend unit, integration, contract, and end-to-end tests
├── frontend/
│   ├── src/                 # dashboard application
│   └── public/
├── config/
│   ├── icp.example.yaml     # editable, versioned ICP shape with safe example values
│   └── routing.example.yaml # score bands, weights, and routing policy shape
├── docs/
│   ├── architecture.md
│   ├── implementation-plan.md
│   └── requirements.md
├── infra/                   # local containers, deployment manifests, migrations, observability
├── scripts/                 # safe developer and demo-data commands
├── tests/                   # cross-component and contract test placeholders
├── .env.example
└── .gitignore
```

## Local setup

This phase includes a working local website/manual intake path. External providers remain disabled.

Prerequisites: Python 3.12+, Node.js 22+, Docker Desktop, and `uv`.

PowerShell from the repository root:

```powershell
Copy-Item .env.example .env
docker compose -f infra/compose/docker-compose.yml up -d
uv sync --project backend --extra dev
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend python -m lead_agent.seed
```

Run the API in one terminal:

```powershell
uv run --project backend uvicorn lead_agent.api.main:app --reload --host 127.0.0.1 --port 8000
```

Install and run the dashboard in another terminal:

```powershell
Set-Location frontend
npm.cmd install
npm.cmd run dev
```

Open <http://localhost:5173>. API documentation is available at <http://127.0.0.1:8000/docs>.

If `uv` is unavailable, create a virtual environment with `python -m venv .venv`, activate it, install with `python -m pip install -e "backend[dev]"`, set `$env:PYTHONPATH = "$pwd\backend\src"`, and replace `uv run --project backend` with the relevant command from that environment.

See [`docs/implementation-plan.md`](docs/implementation-plan.md) for the staged order of work and [`docs/architecture.md`](docs/architecture.md) for runtime boundaries, persistence, failure handling, and security requirements.

## Configuration and secrets

`.env.example` lists the configuration contract with placeholders only. Credentials should be supplied through a local ignored `.env` file or a deployment secret manager. Provider credentials are not needed for this architecture-only phase.

The default v1 CRM mode is `none` until a Salesforce or HubSpot decision and credentials are available. No Salesforce, HubSpot, Slack, LinkedIn, Gmail/Outlook, social, Clutch, or OpenAI client has been added. Redis/Celery are only structured as the future asynchronous boundary; this first slice runs synchronously so its result is immediately visible.

## Phase 1 API

- `POST /v1/leads` — create, normalize, qualify, route, and persist a website/manual lead.
- `POST /v1/intake/web` — website intake alias using the same canonical processing path.
- `GET /v1/leads` — list persisted leads and current qualification results.
- `GET /v1/leads/{lead_id}` — retrieve the canonical lead and decision.
- `GET /v1/leads/{lead_id}/qualification` — retrieve the explainable qualification result.
- `GET /health/live` and `GET /health/ready` — liveness/readiness checks.

Configuration is loaded from [`config/icp.example.yaml`](config/icp.example.yaml) and [`config/routing.example.yaml`](config/routing.example.yaml). The seed command creates illustrative Hot, Warm, Needs Qualifying, Nurture, and Disqualified records.

## Design principles

- Normalize every source into one canonical lead and preserve the immutable raw event for replay and audit.
- Run hard disqualifier checks before LLM reasoning; keep the weighted score deterministic and configurable.
- Store every score component, evidence reference, rationale, confidence, and policy version so decisions are explainable.
- Make every external side effect idempotent and retryable through an outbox; send failures to manual review rather than dropping leads.
- Treat the dashboard as a read model over lead, lifecycle, routing, and revenue events rather than as a second source of truth.
- Keep personally identifiable information out of logs and do not put secrets in Git.
