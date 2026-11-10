# Vercel preview migration

This repository supports a **preview-only** Vercel deployment. The existing Replit
production deployment and public domains remain unchanged until a separate,
explicit production cutover is approved.

## Runtime layout

- `web`: the Vite application rooted at `frontend/`.
- `api`: the FastAPI application rooted at `backend/`.
- `/v1/*` and `/health/*` route to FastAPI; static assets and the SPA fallback
  route to Vite.
- Celery tasks use the Vercel Queues broker and Vercel Runtime Cache result
  backend when `VERCEL=1`. Local and Replit runtimes continue using Redis.
- PostgreSQL remains external and must use an SSL-enabled remote URL.

The API-driven Celery path is preview-compatible. The existing Celery Beat jobs
run every 30-60 seconds and cannot be validated by a preview deployment because
Vercel scheduled jobs run only from production deployments. Do not cut over
production until those recurring jobs have an approved scheduler and have been
verified independently.

## Secret handling

Never commit or print secret values. Configure these in Vercel for **Preview**
and, where useful for local linking, **Development** only:

- Application security: `SECRET_KEY`, `WEBHOOK_SIGNING_SECRET`,
  `CORRAL_SITE_ACCESS_PASSWORD`, `SECRET_STORE_ENCRYPTION_KEY`,
  `SECRET_STORE_PROVIDER`, and `MANAGED_SECRET_STORE_BACKEND`.
- Data services: `DATABASE_URL` and `REDIS_URL`. The preview database must be a
  non-production database; Redis remains necessary for application caching,
  rate limiting, and other non-Celery uses.
- URLs and browser policy: `PUBLIC_APP_URL`, `FRONTEND_BASE_URL`,
  `BACKEND_BASE_URL`, `CORS_ALLOWED_ORIGINS`, and `TRUSTED_HOSTS`.
- Provider credentials used by the preview workspace, including enabled Gmail,
  Google Calendar, Calendly, HubSpot, Salesforce, Slack, calling, and AI
  providers.

Copy values only from an authorized secret source. Replit Secrets cannot be
exported from a suspended workspace; any value that is not present in the local
secure environment must be entered manually by an authorized workspace owner.

## Preview release procedure

1. Push the tested commit to `ui/overhaul`.
2. Create a Vercel project without assigning a production domain.
3. Add only Preview/Development environment variables.
4. Create a deployment from the `ui/overhaul` branch without a production
   target.
5. Verify `/health/live`, the login page, static assets, API routing, and an
   API-triggered background job.
6. Inspect build and runtime logs for missing settings or provider credentials.

## Rollback

Delete or stop using the Vercel preview project. No DNS, public domain, Replit
deployment, or production database change is required to roll back this preview.
