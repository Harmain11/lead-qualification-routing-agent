# Running Corral on Replit

## Workspace preview

Press **Run** to start the **Corral preview** workflow, which runs
`bash scripts/replit_dev.sh`. Vite serves the React dashboard on port 5000
and proxies API requests to FastAPI on loopback port 8001. Both processes are
supervised by the single preview workflow.

The preview uses the project's supported development configuration:

- SQLite at `local-dev.db` by default (or the configured `DATABASE_URL`).
- In-memory rate limiting; Redis is not required.
- Deterministic AI and mock CRM/notification providers, not live provider delivery.
- The existing encrypted database-backed secret store, using the dedicated
  `SECRET_STORE_ENCRYPTION_KEY` Replit Secret. The key must be a unique 32-byte
  value encoded as unpadded base64url. OAuth tokens and PKCE state survive workflow
  restarts; old process-local connections must be reauthorized.
- Illustrative data seeded into the isolated demo workspace using the existing
  `python -m lead_agent.seed --demo` command.
- The existing `SESSION_SECRET` Replit secret is used as the session signing key.
- Secure SameSite=None cookies support the HTTPS Replit preview iframe.
- OAuth redirect and post-login URLs use the active Replit HTTPS preview origin,
  not localhost.

Use **Continue to local workspace** on the sign-in page to enter the demo. Development
session access is for preview only; do not put sensitive customer data in it.
The Google button uses the app's built-in development-only mock login until both
`GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are configured as Replit Secrets.
With both present, it switches to Google's real OpenID sign-in flow. Add the
preview callback
`https://${REPLIT_DEV_DOMAIN}/v1/auth/google/callback` to the Google OAuth web
client's authorized redirect URIs. Normal account creation/sign-in remains
available. Gmail inbox access and other live integrations require their own
provider configuration and user authorization; a Replit Gmail/Calendar
connection does not configure Corral's Google sign-in.

## Dependencies and rebuilding

Python 3.12 and Node.js 22 are configured. Backend dependencies are installed
in Replit's managed Python package environment from `backend/pyproject.toml`.
Frontend dependencies retain the existing `frontend/package-lock.json`.

After frontend changes run:

```sh
npm run build --prefix frontend
```

The development frontend hot-reloads source changes without a build. Builds are
useful for checking the production bundle. Backend source changes require a restart.
Database migrations are applied on startup; existing preview data is retained.

## Production

The production build installs the server-side Replit connector SDK as well as
the existing backend/frontend dependencies. Preview remains separate from publishing.
The production runner defaults to Gmail verification-email delivery using the
Replit-connected application account, with its real sender resolved from the mailbox
profile. Startup checks this connection read-only; no test email is sent automatically.
This connection does not replace customer workspace mailbox OAuth or Google sign-in.
Gmail sending limits and provider restrictions apply; use a dedicated app account.

The production runner defaults to `REDIS_DEPLOYMENT_MODE=local_vm`, with Redis
bound only to `127.0.0.1:6380`, protected mode on, and supervised alongside the API,
worker, and beat. AOF reduces loss on ordinary process restarts, but this VM's local
files are **not durable across redeploy/VM replacement**: pending jobs, cache, and
rate-limit counters can be lost. This is a single-VM, no-separate-provider-subscription
option, not managed Redis. Durable business records remain in PostgreSQL.
For managed Redis, explicitly set `REDIS_DEPLOYMENT_MODE=remote` and a remote
`rediss://` URL. SMTP remains supported with `EMAIL_PROVIDER=smtp` and SMTP settings.

Production still requires durable PostgreSQL, managed secret storage, production
security settings including `WEBHOOK_SIGNING_SECRET`, public URLs, and credentials
for live providers. These changes do not publish the app. Replit hosting costs apply.
Follow `docs/phase-7f-replit-production-deployment.md`; never publish the
development preview script or SQLite/demo configuration. The Gmail/local-VM setup
above supersedes that guide's SMTP/remote-Redis-only requirements for this deployment.