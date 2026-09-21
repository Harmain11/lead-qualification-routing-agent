# Lead Qualification & Routing Agent

Production-ready foundation for a channel-agnostic lead qualification, explainable routing, CRM synchronization, and sales analytics demo.

This repository currently contains the architecture, configuration contracts, documentation, and implementation scaffold. External integrations and the full application are intentionally not implemented yet.

## Project status

Phase 0 — architecture and foundation.

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

The application runtime is not implemented yet, so this phase does not require installing dependencies or starting services.

When implementation begins:

1. Install Python 3.12, Node.js 22 LTS, Docker, and `uv`.
2. Copy `.env.example` to `.env` and replace placeholders locally. Never commit `.env`.
3. Start only local dependencies with the future Docker Compose definition in `infra/`.
4. Apply Alembic migrations, load the versioned ICP configuration, and run the API, worker, and frontend separately.
5. Use the health endpoint and seeded demo data to verify the environment before connecting any provider.

See [`docs/implementation-plan.md`](docs/implementation-plan.md) for the staged order of work and [`docs/architecture.md`](docs/architecture.md) for runtime boundaries, persistence, failure handling, and security requirements.

## Configuration and secrets

`.env.example` lists the configuration contract with placeholders only. Credentials should be supplied through a local ignored `.env` file or a deployment secret manager. Provider credentials are not needed for this architecture-only phase.

The default v1 CRM mode is `none` until a Salesforce or HubSpot decision and credentials are available. No Salesforce, HubSpot, Slack, LinkedIn, Gmail/Outlook, social, Clutch, or OpenAI client has been added.

## Design principles

- Normalize every source into one canonical lead and preserve the immutable raw event for replay and audit.
- Run hard disqualifier checks before LLM reasoning; keep the weighted score deterministic and configurable.
- Store every score component, evidence reference, rationale, confidence, and policy version so decisions are explainable.
- Make every external side effect idempotent and retryable through an outbox; send failures to manual review rather than dropping leads.
- Treat the dashboard as a read model over lead, lifecycle, routing, and revenue events rather than as a second source of truth.
- Keep personally identifiable information out of logs and do not put secrets in Git.
