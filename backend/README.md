# Backend

The backend contains the FastAPI HTTP process, PostgreSQL/Alembic persistence, the canonical lead/qualification domain, the durable source-event intake layer, and the reserved asynchronous worker boundary. Keep domain/application code independent of provider SDKs.

Planned package boundaries are documented in [`../docs/architecture.md`](../docs/architecture.md). Provider integrations are intentionally not implemented in this phase.

From the repository root:

```powershell
docker compose -f infra/compose/docker-compose.yml up -d
uv sync --project backend --extra dev
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend uvicorn lead_agent.api.main:app --reload --host 127.0.0.1 --port 8000
```

Use `uv run --project backend python -m lead_agent.seed` to load the five illustrative records. Intake fixture adapters live under `src/lead_agent/infrastructure/intake`; provider SDKs are intentionally absent. Run `uv run --project backend pytest backend/tests` for backend tests, `uv run --project backend ruff check backend/src backend/tests` for linting, and `uv run --project backend mypy backend/src` for type checking.
