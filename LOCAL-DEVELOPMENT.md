# Separate Windows development checkout

GitHub: https://github.com/Harmain11/lead-qualification-routing-agent
Branch: `codex/corral-local-dev`, updated to `origin/main` at `79f1f1c`.

Run from this checkout in PowerShell:

```powershell
powershell.exe -ExecutionPolicy Bypass -File scripts/local_dev.ps1
```

Open http://localhost:5173 and choose **Continue to local workspace**.
The API is at http://localhost:8001/docs. React and Python source edits reload automatically.
Runtime logs and process IDs are under `runtime-logs/`.

Stop this preview from the checkout:

```powershell
Get-Content runtime-logs/local-dev.pids | ForEach-Object { taskkill.exe /PID $_ /T /F }
```

The ignored `.env` uses a separate SQLite database (`local-dev.db`), seeded demo
records, deterministic AI, mock CRM/notifications, development OAuth, and local
session cookies. Replit secrets and production data are not required. Live
provider integrations need their own credentials. The operations worker now runs
with this preview for enrolled sequences and opted-in monitoring/research/digests.
It creates follow-up drafts and never automatically sends sequence emails.
Scheduled mailbox/CRM acquisition polling is not launched by this preview.

Dependencies are already installed in `backend/.venv` and `frontend/node_modules`.
To refresh them, run `uv sync --project backend --extra dev --locked` and
`npm.cmd ci --prefix frontend`. The `.env` and `frontend/.env.local` files are
local configuration and must remain untracked.
## GitHub update on 8 October 2026

The checkout now uses GitHub main at 79f1f1c. Earlier unfinished local UI
changes are preserved in the Git stash named
Corral local UI drafts before GitHub update 2026-10-08. They have not been
reapplied over overlapping GitHub changes. Inspect that snapshot before
selectively restoring work; applying the entire stash will cause conflicts.

The Windows launcher, ignored configuration and existing SQLite database are
retained. The previous local collaboration migration and a merge revision
preserve database history while adding the new upstream sales-outcome schema.
A SQLite backup is under runtime-logs/local-dev-before-github-*.db.

The earlier requirement-status report describes the previous checkout.
The current implementation is documented in [FEATURE-STATUS.md](FEATURE-STATUS.md).


## New operations screens

Use Contacts, Companies, Research & Coaching, Sequences & Playbooks,
Territories & Capacity, Consent & Governance, Agency & Branding, and the advanced
section of Analytics. Ctrl/Cmd+K opens page and record search.

The operations worker is a separate process. Restart it after Python source changes:
the API and frontend reload automatically, while a running worker keeps its imported code.
Stop/restart all three using the launcher commands above when changing worker logic.

Migration head is 0034_operations_leases. Migrations are additive; the existing database,
lead identities, inquiry content, sessions and integration settings are retained.
Backups and test logs remain ignored under runtime-logs.
The fresh migration check used a separate migration-check.db, never the user database.

Research requires a reviewed public HTTPS company website and verified OpenAI connection.
Watcher and weekly coaching digest switches are off by default in Team policies.
Sequences require explicit enrollment and an active definition.
Stored contact timezones take priority when different from UTC; UTC-default contacts
use the sequence timezone. Suggested times are draft-review windows.

Set CORRAL_HOSTING_REGION to the actual deployed region (local/us/eu/uk/apac) before
enforcing residency. The guard controls processing in this deployment; hosting, backups,
provider contracts and infrastructure must be placed in the corresponding region separately.
A custom branding domain similarly needs DNS/TLS configured with the hosting provider.

Zapier/Make developer packages and submission instructions are under integrations/.
Both use HTTPS. No marketplace listing has been published from this local checkout.
