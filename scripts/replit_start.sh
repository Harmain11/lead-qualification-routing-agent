#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_bin="${PYTHON_BIN:-python3}"
# Match Replit's public port mapping; API_PORT is for the development backend.
port="${PORT:-5000}"
internal_api_port=8001
api_host="${REPLIT_API_HOST:-0.0.0.0}"
forwarded_allow_ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
worker_concurrency="${CELERY_WORKER_CONCURRENCY:-2}"
beat_lock_file="${CELERY_BEAT_LOCK_FILE:-/tmp/lead-agent-celerybeat.lock}"
beat_schedule_file="${CELERY_BEAT_SCHEDULE_FILE:-/tmp/lead-agent-celerybeat-schedule}"

# Keep imports and module execution consistent whether Replit is using the
# editable package from the build step or a source checkout during setup.
export PYTHONPATH="backend/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1

# Production-specific defaults. The preview runner intentionally uses relaxed
# local settings; deployments must not inherit those development defaults.
export APP_ENV=production
export AUTH_DEVELOPMENT_FALLBACK=false
export AUTH_COOKIE_SECURE=true
export AUTH_REQUIRE_EMAIL_VERIFICATION=true
export LOG_FORMAT=json
export SECRET_KEY="${SESSION_SECRET:?SESSION_SECRET must be configured in Replit Secrets}"
export SECRET_STORE_PROVIDER=managed
export MANAGED_SECRET_STORE_BACKEND=lead_agent.infrastructure.encrypted_secrets:create_backend
: "${SECRET_STORE_ENCRYPTION_KEY:?SECRET_STORE_ENCRYPTION_KEY must be configured in Replit Secrets}"
export OAUTH_BROKER=provider
export EMAIL_PROVIDER="${EMAIL_PROVIDER:-gmail}"
export EMAIL_SMTP_PORT="${EMAIL_SMTP_PORT:-587}"
export EMAIL_SMTP_STARTTLS="${EMAIL_SMTP_STARTTLS:-true}"
export LLM_PROVIDER=workspace
export CRM_PROVIDER="${CRM_PROVIDER:-hubspot}"
export NOTIFICATION_PROVIDER=slack
export DATABASE_SSL_MODE=require
export RATE_LIMIT_BACKEND=redis
export REDIS_REQUIRED=true
export REDIS_DEPLOYMENT_MODE="${REDIS_DEPLOYMENT_MODE:-local_vm}"
if [[ "$REDIS_DEPLOYMENT_MODE" == "local_vm" ]]; then
  export REDIS_URL=redis://127.0.0.1:6380/0
fi
export INTEGRATION_PUBLIC_BASE_URL="${INTEGRATION_PUBLIC_BASE_URL:-https://corralai.tech}"
export FRONTEND_PUBLIC_BASE_URL="${FRONTEND_PUBLIC_BASE_URL:-https://corralai.tech}"
export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-https://corralai.tech,https://lead-qualification-routing-agent.replit.app}"
export DEMO_DATA_ENABLED=false

case "$port" in
  ''|*[!0-9]*) echo "[startup] PORT must be numeric" >&2; exit 1 ;;
esac
if [[ "$port" == "$internal_api_port" ]]; then
  echo "[startup] public and internal API ports must differ" >&2
  exit 1
fi

child_pids=()
startup_dir=""
shutdown() {
  trap - TERM INT EXIT
  if ((${#child_pids[@]})); then
    kill -TERM "${child_pids[@]}" 2>/dev/null || true
    wait "${child_pids[@]}" 2>/dev/null || true
  fi
  if [[ -n "$startup_dir" ]]; then rm -rf "$startup_dir"; fi
}
trap shutdown TERM INT EXIT

# This listener uses only Python's standard library: unlike starting Uvicorn
# early, it binds before heavy application/configuration imports on a cold VM.
# It exposes no operational API until all existing production checks pass.
if [[ "${1:-}" != "--check" ]]; then
  startup_dir="$(mktemp -d /tmp/corral-startup.XXXXXX)"
  "$python_bin" scripts/startup_gateway.py --host "$api_host" --port "$port" \
    --upstream-port "$internal_api_port" --ready-file "$startup_dir/ready" \
    --listening-file "$startup_dir/listening" &
  gateway_pid=$!
  child_pids+=("$gateway_pid")
  for attempt in {1..100}; do
    kill -0 "$gateway_pid" 2>/dev/null || { echo "[startup] public listener exited" >&2; exit 1; }
    if [[ -s "$startup_dir/listening" ]]; then break; fi
    sleep 0.1
  done
  test -s "$startup_dir/listening" || { echo "[startup] public listener did not bind" >&2; exit 1; }
fi

echo "[startup] validating production configuration"
"$python_bin" -m lead_agent.production_check

echo "[startup] checking built frontend"
test -s frontend/dist/index.html

if [[ "$REDIS_DEPLOYMENT_MODE" == "local_vm" ]]; then
  echo "[startup] local Redis: queue/cache data is not durable across VM replacement or redeploy"
  command -v redis-server >/dev/null || { echo "[startup] redis-server is missing" >&2; exit 1; }
  redis_dir="${LOCAL_REDIS_DATA_DIR:-/tmp/corral-redis}"
  mkdir -p "$redis_dir"
  redis-server --bind 127.0.0.1 --port 6380 --protected-mode yes \
    --daemonize no --dir "$redis_dir" --appendonly yes --appendfsync everysec \
    --save "" --maxmemory-policy noeviction &
  redis_pid=$!
  child_pids+=("$redis_pid")
  for attempt in {1..50}; do
    kill -0 "$redis_pid" 2>/dev/null || { echo "[startup] local Redis exited" >&2; exit 1; }
    if redis-cli -h 127.0.0.1 -p 6380 ping >/dev/null 2>&1; then break; fi
    sleep 0.1
  done
  redis-cli -h 127.0.0.1 -p 6380 ping >/dev/null
fi

# Load the private API while remote-provider probes run. The public listener
# already serves the honest startup page, without claiming API readiness.
# Any failed probe still exits this runner and stops all supervised children.
# Workers must not consume live jobs until the probes below have passed.
if [[ "${1:-}" != "--check" ]]; then
  echo "[startup] starting private API on 127.0.0.1:${internal_api_port}"
  "$python_bin" -m uvicorn lead_agent.api.main:app \
    --host 127.0.0.1 \
    --port "$internal_api_port" \
    --no-access-log \
    --proxy-headers \
    --forwarded-allow-ips "$forwarded_allow_ips" &
  api_pid=$!
  child_pids+=("$api_pid")
fi

if [[ "$EMAIL_PROVIDER" == "gmail" ]]; then
  echo "[startup] checking Gmail connection (read-only; no email sent)"
  "$python_bin" - <<'PY'
from lead_agent.infrastructure.email import EmailDeliveryError, GmailEmailProvider
from lead_agent.settings.config import get_settings

try:
    GmailEmailProvider(get_settings()).sender_address()
except EmailDeliveryError:
    raise SystemExit("[startup] Gmail connection failed; check the Replit Gmail connection")
print("[startup] Gmail mailbox is accessible")
PY
fi

echo "[startup] checking PostgreSQL and Redis connectivity"
"$python_bin" - <<'PY'
from redis import Redis
from sqlalchemy import text

from lead_agent.infrastructure.db.session import build_engine
from lead_agent.settings.config import get_settings

settings = get_settings()
engine = build_engine(settings)
try:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
except Exception:
    raise SystemExit(
        "[startup] PostgreSQL connection failed. Production requires SSL; "
        "the workspace development database is not the published production database."
    )
finally:
    engine.dispose()
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

if [[ "${1:-}" == "--check" ]]; then
  echo "[startup] production preflight passed (no migrations, workers, or email sends)"
  exit 0
fi

echo "[startup] production schema is managed by Replit Publish; no startup migrations"
kill -0 "$api_pid" 2>/dev/null || {
  echo "[startup] API exited during dependency checks" >&2
  exit 1
}

echo "[startup] starting Celery worker with concurrency ${worker_concurrency}"
"$python_bin" -m celery -A lead_agent.worker.celery_app:celery_app worker \
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
# Exec beat under the lock, so its supervised PID receives shutdown signals.
flock --no-fork -n "$beat_lock_file" \
  "$python_bin" -m celery -A lead_agent.worker.celery_app:celery_app beat \
    --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
    --schedule "$beat_schedule_file" \
    --pidfile="${CELERY_BEAT_PID_FILE:-/tmp/lead-agent-celerybeat.pid}" &
child_pids+=("$!")

touch "$startup_dir/ready"
echo "[startup] dependency checks passed; gateway will switch when the API is live"

exit_code=0
wait -n "${child_pids[@]}" || exit_code=$?
echo "[startup] a supervised process exited with status ${exit_code}" >&2
# An unexpected clean child exit is also a service failure.
if [[ "$exit_code" == "0" ]]; then exit_code=1; fi
exit "$exit_code"
