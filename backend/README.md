# Backend

The backend contains the FastAPI HTTP process, PostgreSQL/Alembic persistence, the canonical lead/qualification domain, the durable source-event intake layer, and the reserved asynchronous worker boundary. Keep domain/application code independent of provider SDKs.

Planned package boundaries are documented in [`../docs/architecture.md`](../docs/architecture.md). Provider integrations are intentionally not implemented in this phase.

From the repository root:

```powershell
docker compose -f infra/compose/docker-compose.yml up -d
uv sync --project backend --extra dev
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend uvicorn lead_agent.api.main:app --reload --host 127.0.0.1 --port 8001
```

Use `uv run --project backend python -m lead_agent.seed` to load the five illustrative records. Use `uv run --project backend python -m lead_agent.seed --demo` for the isolated 12-lead demo workspace, or add `--workspace-id <uuid>` to seed the same illustrative records into a development workspace you are testing. Intake fixture adapters live under `src/lead_agent/infrastructure/intake`; provider SDKs are intentionally absent. Run `uv run --project backend pytest backend/tests` for backend tests, `uv run --project backend ruff check backend/src backend/tests` for linting, and `uv run --project backend mypy backend/src` for type checking.

### Opt-in assistant semantic evaluation

Run `uv run --project backend python -m lead_agent.evaluations.assistant_semantics --workspace-id <uuid> --run` to evaluate every verified reasoning provider configured for that workspace. Add `--provider openai` (or `gemini`, `claude`, `deepseek`, or `xai`) to select one provider. This makes at most 10 calls per provider across 11 scenarios (the typo-name route case does not call the model), with at most five supported providers. Provider use may incur charges.

The evaluation sends only built-in fictional `.test` conversations, a fictional workspace profile, and synthetic lead records through the real workspace assistant route and secure workspace credential resolver. It reports scenario-level semantic checks as JSON. It does not load live CRM context or execute actions; proposed lead changes are only evaluated as drafts, and no email, booking, CRM write, delete, or lead mutation is run. `--run` is mandatory so a normal invocation cannot accidentally call providers.

Lead exports are synchronous and bounded by `EXPORT_SYNC_LIMIT` (default 5,000 records). Requests above the limit are rejected with a safe message and an audit event; the export service keeps the query/render boundary suitable for a future asynchronous job without introducing a queue in this phase.
