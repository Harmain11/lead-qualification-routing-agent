#!/usr/bin/env bash
# Workspace preview only. Production continues to use replit_start.sh.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ "${APP_ENV:-development}" == "production" ]]; then
  echo "Use scripts/replit_start.sh for production." >&2
  exit 1
fi

export APP_ENV=development
export PYTHONPATH="backend/src${PYTHONPATH:+:$PYTHONPATH}"
export DATABASE_URL="${DATABASE_URL:-sqlite:///./local-dev.db}"
export REDIS_REQUIRED=false
export RATE_LIMIT_BACKEND=memory
export LLM_PROVIDER=deterministic
export CRM_PROVIDER=mock
export NOTIFICATION_PROVIDER=mock
export DEMO_DATA_ENABLED=true
export SECRET_KEY="${SESSION_SECRET:?SESSION_SECRET must be configured in Replit Secrets}"
export AUTH_COOKIE_SECURE=true
export AUTH_COOKIE_SAMESITE=none
preview_origin="https://${REPLIT_DEV_DOMAIN:?Replit development domain is required}"
export FRONTEND_PUBLIC_BASE_URL="$preview_origin"
export INTEGRATION_PUBLIC_BASE_URL="$preview_origin"

if [[ -n "${GOOGLE_CLIENT_ID:-}" && -z "${GOOGLE_CLIENT_SECRET:-}" ]] ||
   [[ -z "${GOOGLE_CLIENT_ID:-}" && -n "${GOOGLE_CLIENT_SECRET:-}" ]]; then
  echo "Configure both GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to enable real Google sign-in." >&2
  exit 1
elif [[ -n "${GOOGLE_CLIENT_ID:-}" ]]; then
  export OAUTH_BROKER=provider
else
  export OAUTH_BROKER=development
fi

python -m alembic -c backend/alembic.ini upgrade head
python -m lead_agent.seed --demo

# Vite's development mode exposes the existing local-workspace button.
# Only Vite is public; its existing proxy forwards API requests to FastAPI.
export VITE_ALLOWED_HOSTS="${REPLIT_DEV_DOMAIN}${VITE_ALLOWED_HOSTS:+,$VITE_ALLOWED_HOSTS}"
export VITE_API_PROXY_TARGET=http://127.0.0.1:8001
child_pids=()
shutdown() {
  trap - TERM INT EXIT
  if ((${#child_pids[@]})); then
    kill -TERM "${child_pids[@]}" 2>/dev/null || true
    wait "${child_pids[@]}" 2>/dev/null || true
  fi
}
trap shutdown TERM INT EXIT

python -m uvicorn lead_agent.api.main:app --host 127.0.0.1 --port 8001 &
child_pids+=("$!")
npm run dev --prefix frontend -- --host 0.0.0.0 --port 5000 --strictPort &
child_pids+=("$!")
wait -n "${child_pids[@]}"