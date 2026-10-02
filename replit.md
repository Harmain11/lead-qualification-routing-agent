# Running Corral on Replit

## Workspace preview

Press **Run** to start the **Corral preview** workflow, which runs
`bash scripts/replit_dev.sh`. Vite serves the React dashboard on port 5000
and proxies API requests to FastAPI on loopback port 8001. Both processes are
supervised by the single preview workflow.

The preview uses the project's supported development configuration:

- SQLite at `local-dev.db` (ignored by Git).
- In-memory rate limiting; Redis is not required.
- Deterministic AI and mock CRM/notification providers, not live provider delivery.
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

The existing `scripts/replit_build.sh` and `scripts/replit_start.sh` production
commands are unchanged. Preview setup does **not** configure production publishing.
Production requires durable PostgreSQL, Redis, managed secret storage, production
security settings, public URLs, and credentials for any live providers.
Follow `docs/phase-7f-replit-production-deployment.md`; never publish the
development preview script or SQLite/demo configuration.