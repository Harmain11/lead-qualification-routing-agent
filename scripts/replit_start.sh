#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_bin="${PYTHON_BIN:-python3}"
port="${PORT:-${API_PORT:-8000}}"
api_host="${REPLIT_API_HOST:-0.0.0.0}"
forwarded_allow_ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
worker_concurrency="${CELERY_WORKER_CONCURRENCY:-2}"
beat_lock_file="${CELERY_BEAT_LOCK_FILE:-/tmp/lead-agent-celerybeat.lock}"
beat_schedule_file="${CELERY_BEAT_SCHEDULE_FILE:-/tmp/lead-agent-celerybeat-schedule}"

case "$port" in
  ''|*[!0-9]*) echo "[startup] PORT must be numeric" >&2; exit 1 ;;
esac

echo "[startup] validating production configuration"
PYTHONPATH="backend/src${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" -m lead_agent.production_check

echo "[startup] checking built frontend"
test -s frontend/dist/index.html

echo "[startup] applying database migrations"
PYTHONPATH="backend/src${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" -m alembic -c backend/alembic.ini upgrade head

echo "[startup] checking PostgreSQL and Redis connectivity"
PYTHONPATH="backend/src${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" - <<'PY'
from redis import Redis
from sqlalchemy import text

from lead_agent.infrastructure.db.session import build_engine
from lead_agent.settings.config import get_settings

settings = get_settings()
with build_engine(settings).connect() as connection:
    connection.execute(text("SELECT 1"))
client = Redis.from_url(
    settings.redis_url,
    socket_connect_timeout=settings.redis_connect_timeout_seconds,
    socket_timeout=settings.redis_socket_timeout_seconds,
)
try:
    client.ping()
finally:
    client.close()
print("[startup] PostgreSQL and Redis are reachable")
PY

child_pids=()
shutdown() {
  trap - TERM INT EXIT
  if ((${#child_pids[@]})); then
    kill -TERM "${child_pids[@]}" 2>/dev/null || true
    wait "${child_pids[@]}" 2>/dev/null || true
  fi
}
trap shutdown TERM INT EXIT

echo "[startup] starting API on ${api_host}:${port}"
uvicorn lead_agent.api.main:app \
  --host "$api_host" \
  --port "$port" \
  --proxy-headers \
  --forwarded-allow-ips "$forwarded_allow_ips" &
child_pids+=("$!")

echo "[startup] starting Celery worker with concurrency ${worker_concurrency}"
celery -A lead_agent.worker.celery_app:celery_app worker \
  --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
  --concurrency "$worker_concurrency" \
  --hostname="${CELERY_WORKER_HOSTNAME:-lead-agent-worker@%h}" &
child_pids+=("$!")

echo "[startup] starting exactly one Celery beat scheduler"
if ! command -v flock >/dev/null 2>&1; then
  echo "[startup] flock is required to protect the singleton beat scheduler" >&2
  exit 1
fi
mkdir -p "$(dirname "$beat_lock_file")" "$(dirname "$beat_schedule_file")"
flock -n "$beat_lock_file" \
  celery -A lead_agent.worker.celery_app:celery_app beat \
    --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
    --schedule "$beat_schedule_file" \
    --pidfile="${CELERY_BEAT_PID_FILE:-/tmp/lead-agent-celerybeat.pid}" &
child_pids+=("$!")

wait -n "${child_pids[@]}"
exit_code=$?
echo "[startup] a supervised process exited with status ${exit_code}" >&2
exit "$exit_code"
