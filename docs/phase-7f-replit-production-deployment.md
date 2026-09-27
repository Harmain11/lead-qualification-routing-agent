# Phase 7F — Replit production deployment

This document packages the certified Lead Qualification & Routing Agent for a single-origin Replit Reserved VM deployment. It does not add provider functionality. Gmail inbound, Salesforce inbound, HubSpot inbound, Slack notifications, encrypted provider credentials, and the deterministic qualification pipeline remain the certified application behavior.

## Architecture

Use one always-on Replit Reserved VM deployment configured as a web server with one exposed port. Reserved VM is required because Celery worker and beat must stay alive for scheduled inbound sync. The API serves the built Vite application from `frontend/dist`, so the browser and API share one HTTPS origin.

The run command supervises three child processes:

1. FastAPI/Uvicorn on `0.0.0.0:${PORT}`.
2. One Celery worker connected to `REDIS_URL`.
3. One Celery beat scheduler protected by a filesystem lock.

The beat schedule file is disposable runtime state. PostgreSQL checkpoint rows, leases, provider connection state, encrypted secrets, audit records, and lead data are authoritative and durable.

## Replit configuration

The repository includes `.replit` with these commands:

```text
Build: bash scripts/replit_build.sh
Run:   bash scripts/replit_start.sh
```

In Replit Publishing, select **Reserved VM**, choose **Web server**, expose only the API port, and do not select Autoscale or Static Deployment. Reserved VM provides an always-on VM suitable for API servers and background tasks. Replit terminates public TLS and supplies the runtime `PORT`; the startup script binds the API to `0.0.0.0` and that port.

The build script installs the backend package, runs `npm ci`, builds Vite, and verifies `frontend/dist/index.html`. Runtime startup does not rebuild the frontend.

## Required published Secrets

Enter these names in Replit Published App Secrets. Never commit or print their values:

```text
APP_ENV
SECRET_KEY
WEBHOOK_SIGNING_SECRET
DATABASE_URL
DATABASE_SSL_MODE
REDIS_URL
REDIS_REQUIRED
RATE_LIMIT_BACKEND
SECRET_STORE_PROVIDER
MANAGED_SECRET_STORE_BACKEND
SECRET_STORE_ENCRYPTION_KEY
SECRET_STORE_KEY_VERSION
SECRET_STORE_PREVIOUS_KEYS
AUTH_DEVELOPMENT_FALLBACK
AUTH_COOKIE_SECURE
AUTH_COOKIE_SAMESITE
CORS_ALLOWED_ORIGINS
FRONTEND_PUBLIC_BASE_URL
INTEGRATION_PUBLIC_BASE_URL
OAUTH_BROKER
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
SLACK_CLIENT_ID
SLACK_CLIENT_SECRET
SALESFORCE_CLIENT_ID
SALESFORCE_CLIENT_SECRET
HUBSPOT_CLIENT_ID
HUBSPOT_CLIENT_SECRET
CRM_PROVIDER
NOTIFICATION_PROVIDER
LLM_PROVIDER
OPENAI_SUPPORTED_MODELS
OPENAI_DEFAULT_MODEL
```

Set `LLM_PROVIDER=workspace` in production. Customer OpenAI API keys are submitted through the authenticated workspace integration flow and stored in the encrypted database backend; they are not Replit Secrets and are never returned through the API. `OPENAI_SUPPORTED_MODELS` and `OPENAI_DEFAULT_MODEL` are non-secret server controls. A global `OPENAI_API_KEY` is development/test-only, and `LLM_PROVIDER=openai` is rejected by production startup validation. Missing or unavailable workspace OpenAI connections use the deterministic fallback.

Set `MANAGED_SECRET_STORE_BACKEND` to the existing encrypted database backend:

```text
lead_agent.infrastructure.encrypted_secrets:create_backend
```

Generate one stable 32-byte base64url `SECRET_STORE_ENCRYPTION_KEY` and back it up in the approved secret-management process. Do not generate a new key on boot. For rotation, place the old version in `SECRET_STORE_PREVIOUS_KEYS`, deploy the new active key/version, reconnect or rotate secrets as required, then remove the old key only after all rows have been re-encrypted and a restore test succeeds.

## Database and Redis

Use Replit's separate production SQL database when available. Current Replit documentation describes production databases as isolated PostgreSQL 16/17 databases hosted on Neon; this keeps development and production data separate. If the account cannot provision that option, use an equivalent durable managed PostgreSQL service. Set `DATABASE_URL` to PostgreSQL, not SQLite, with `DATABASE_SSL_MODE=require` or stronger. Set `REDIS_URL` to a remote `rediss://` endpoint from a managed Redis service; the Replit SQL database is not a replacement for Celery's Redis broker. The startup command runs `alembic upgrade head` exactly once before starting API, worker, or beat, and fails closed if PostgreSQL or Redis cannot be reached.

For a clean production database, provision an empty database and run:

```sh
alembic -c backend/alembic.ini upgrade head
```

The migration head is `0019_unique_lead_source_event`. Do not copy the local development database or seed demo data into production.

## Public URLs and callbacks

After Replit assigns the stable HTTPS domain, set both values to the same origin, for example `https://<app-subdomain>.replit.app`:

```text
FRONTEND_PUBLIC_BASE_URL=https://<app-subdomain>.replit.app
INTEGRATION_PUBLIC_BASE_URL=https://<app-subdomain>.replit.app
CORS_ALLOWED_ORIGINS=https://<app-subdomain>.replit.app
```

The exact callback pattern is:

```text
https://<app-subdomain>.replit.app/v1/integrations/gmail/oauth/callback
https://<app-subdomain>.replit.app/v1/integrations/slack/oauth/callback
https://<app-subdomain>.replit.app/v1/integrations/salesforce/oauth/callback
https://<app-subdomain>.replit.app/v1/integrations/hubspot/oauth/callback
```

A human with provider-console access must add these callbacks while retaining working local development callbacks where supported. Preserve current scopes: Gmail `gmail.readonly`; Slack `chat:write` and `channels:read`; Salesforce current PKCE/API/refresh-token settings; HubSpot `oauth`, `crm.objects.contacts.read`, and `crm.objects.contacts.write`. Do not add `chat:write.public`.

Google OAuth consent is currently a release dependency: the Gmail app is in Testing mode and Google may require consent-screen publishing, verification, or restricted-scope security review before normal production users can connect. Do not claim Gmail production availability until the Google console permits the intended users and scope.

## Deployment and redeploy procedure

1. Import or push the repository into Replit.
2. Select Reserved VM / Web server and configure one exposed port.
3. Set the build and run commands from `.replit`.
4. Provision durable PostgreSQL and managed Redis.
5. Add the published Secrets by name.
6. Publish and inspect build/deployment logs without printing secrets.
7. Copy the stable HTTPS origin into the three public URL settings and republish.
8. Update provider callback consoles and complete human OAuth consent.
9. Run the smoke and provider checks below.

Redeploys repeat the build and startup migration check. Alembic is idempotent at the deployment workflow level; only the supervisor invokes it. A restart must preserve database rows, encrypted secret references, the stable encryption key, OAuth tokens, provider checkpoints, and leases.

## Health checks

```text
GET /health/live  -> 200
GET /health/ready -> 200 only when PostgreSQL and required Redis respond
```

Readiness intentionally does not depend on Gmail, Salesforce, HubSpot, Slack, or OpenAI being online. Provider failures are recorded through the existing safe error/audit paths. Check Replit deployment logs for `http.request`, `background_job`, provider, operation, request/correlation ID, and safe error category fields. Tokens, API keys, cookies, authorization headers, and encryption keys must not appear in logs.

## Rollback

Publish the last known-good repository snapshot and compatible environment configuration. Do not roll back the encryption key or database schema independently. If a migration requires a forward fix, ship a new migration; do not edit an applied migration. Keep the previous encryption key in `SECRET_STORE_PREVIOUS_KEYS` until the rollback window has closed and decryptability has been tested.

## Release status and acceptance

OpenAI live runtime is deferred because API credits are unavailable. LinkedIn is removed/deferred. Outlook is deferred. These do not block the certified deterministic production pipeline.

Before declaring production success, verify public page load, authenticated login, RBAC, readiness, frontend API calls, PostgreSQL, Redis, worker, beat, no startup/migration loop, provider state `Connected — Verified`, and one controlled automatic Gmail scheduled inbound → deterministic analysis → routing → exactly one Slack notification flow. Confirm Salesforce and HubSpot scheduled inbound remain healthy and do not automatically write back. Repeat the checks after one restart/redeploy.

This repository cannot complete Replit login, billing approval, secret entry, provider OAuth consent, or DNS ownership actions on the operator's behalf.
