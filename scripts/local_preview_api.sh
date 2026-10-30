#!/usr/bin/env bash
# macOS/Linux local preview of the API with SQLite, dev auth, deterministic AI and mock CRM.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
export APP_ENV=development PYTHONPATH=backend/src
export DATABASE_URL="sqlite:///./local-dev.db"
export AUTH_DEVELOPMENT_FALLBACK=true OAUTH_BROKER=development
export AUTH_COOKIE_SECURE=false AUTH_COOKIE_SAMESITE=lax
export REDIS_REQUIRED=false RATE_LIMIT_BACKEND=memory
export CRM_PROVIDER=mock NOTIFICATION_PROVIDER=mock DEMO_DATA_ENABLED=true
export SECRET_KEY="local-preview-secret-key-0123456789abcdef"
export WEBHOOK_SIGNING_SECRET="local-preview-webhook-secret-0123456789"
PY=backend/.venv/bin/python
NEW_DB=$([[ -f local-dev.db ]] && echo 0 || echo 1)
# Migrations after 0035 are not SQLite-compatible; build the preview schema from the
# models. create_all only adds missing tables, so new models appear on the next start.
"$PY" - <<'PYEOF'
from lead_agent.infrastructure.db import models, operations  # noqa: F401
from lead_agent.infrastructure.db.session import build_engine
from lead_agent.infrastructure.db.base import Base
Base.metadata.create_all(build_engine())
PYEOF
if [[ "$NEW_DB" == 1 ]]; then
  "$PY" -m alembic -c backend/alembic.ini stamp head
fi
"$PY" -m lead_agent.seed --demo
exec "$PY" -m uvicorn lead_agent.api.main:app --host 127.0.0.1 --port 8001 --no-access-log
