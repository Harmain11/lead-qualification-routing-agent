# Phase 7H — Outlook / Microsoft Graph automatic inbound

Status: Coming Soon — live certification deferred by the user on 2026-09-27.
The implementation and local migration are preserved; Microsoft app registration,
credentials, and live mailbox certification remain pending.

## Architecture

The integration is strictly read-only and follows:

`Outlook mailbox → Microsoft identity platform OAuth 2.0 → Microsoft Graph → Outlook adapter → canonical intake → analysis → 40/35/25 qualification → routing → notifications`

The server is the OAuth client. The browser receives no access token, refresh token, client
secret, or Graph Authorization header. The callback is environment-derived:

`{INTEGRATION_PUBLIC_BASE_URL}/v1/integrations/outlook/oauth/callback`

For the current local `.env`, that is `http://localhost:8001/v1/integrations/outlook/oauth/callback`.
Production must use the HTTPS Replit origin instead of a hard-coded development URL.

Microsoft's v2 authorization-code flow is used with PKCE S256, one-time workspace/actor-bound
state, and the `common` authority so the same design can support personal Microsoft accounts and
organizational accounts. Microsoft documents `common` for both account families and recommends
`offline_access` when refresh tokens are required.

Official references:

- [Microsoft identity platform authorization-code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
- [Microsoft Graph delegated access on behalf of a user](https://learn.microsoft.com/en-us/graph/auth-v2-user)
- [Microsoft Graph list messages](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0)
- [Microsoft Graph message delta query](https://learn.microsoft.com/en-us/graph/api/message-delta?view=graph-rest-1.0)

## Scopes and account support

The exact delegated scope set is:

- `openid`
- `profile`
- `email`
- `offline_access`
- `Mail.Read`

No `Mail.ReadWrite`, `Mail.Send`, application permission, tenant-wide mailbox permission,
reply, forward, delete, move, archive, read-state, category, or attachment-write operation is
implemented. The Graph adapter reads only the signed-in user's Inbox.

## Connection verification and security

After code exchange, the encrypted workspace secret store receives the OAuth credential set.
The connection is not schedulable until a real Graph request succeeds for `/me` and
`/me/mailFolders/inbox`. The verified identity stores the normalized mailbox address and stable
Microsoft account identifier. Validation distinguishes revoked/401, consent or scope/403,
mailbox unavailable, throttling/429, Graph 5xx, timeout, transport, malformed response, and
refresh failures without exposing provider response bodies or secrets.

Refresh uses the shared concurrency-safe provider lifecycle. Rotated refresh tokens replace the
stored value atomically; stale credentials cannot overwrite a newer secret, and disconnect
removes the secret reference before any remote revocation attempt.

## Acquisition, filtering, and normalization

The provider adapter requests Graph message metadata from `me/mailFolders/inbox/messages/delta`
and fetches the bounded body only for a candidate record. It preserves Graph message ID,
conversation ID, Internet Message-ID, sender address/name, Reply-To, subject, received time,
mailbox identity, and safe Outlook provenance in the canonical payload.

HTML is reduced to bounded text with script/style/head/SVG content removed. Attachments are not
downloaded or ingested. The deterministic filter rejects own-mail, no-reply/automated senders,
bulk/transactional subjects, unsubscribe mail, and messages without inquiry or business intent.

## Initial and incremental sync

Initial enablement uses a configurable recent Inbox window (`OUTLOOK_INITIAL_SYNC_DAYS`, default
30 days), excludes other folders by using the selected Inbox collection, and never scans the full
mailbox. The initial Graph delta request is bounded by the configured page size.

The durable sync state stores the complete `@odata.deltaLink` URL per workspace, connection, and
message object type. `@odata.nextLink` is followed until the bounded page sequence completes;
the delta checkpoint is committed only after every fetched page and local record have been
processed. Scheduler leases prevent overlapping runs, and Gmail, Outlook, Salesforce, and HubSpot
states are independent.

If Graph rejects an expired delta token, the adapter performs a bounded recent Inbox
reconciliation, keeps provider-message deduplication in force, and establishes a fresh delta
checkpoint only after successful processing. A failed local record does not advance the durable
checkpoint past that record.

## Deduplication, conversation updates, and reconnect

Graph message ID is the stable provider record key for source links and replay protection. The
shared unique workspace/provider/source-event path handles scheduler repeats, retries, pagination
restarts, delta replay, and reconnect without duplicate lead creation. A new message from the
same sender is treated as a new provider message and exact normalized sender matching reprocesses
the existing lead without fuzzy identity merging.

Disabling automatic inbound stops future scheduled dispatches while preserving imported leads,
links, and the delta checkpoint. Re-enabling resumes the saved state. Reconnecting the same
verified mailbox rebinds its sync state and provider links using normalized mailbox identity;
connecting a different mailbox does not inherit the old checkpoint or message links.

## UI, RBAC, and audit

Outlook is currently in Coming Soon, with connection, setup, and sync controls hidden.
The existing implementation is preserved for later certification. After certification,
the available card can expose connection verification, safe mailbox identity, automatic
inbound, Sync now, last/next sync, latest result, and Disconnect. There is no send-mail
control. LinkedIn also remains in Coming Soon.

Connect requires `integrations.connect`; enable/disable, reconnect, verify, and disconnect use
the existing integration-management permissions; Sync Now requires `crm.sync`. Workspace context
is required for every connection, secret lookup, scheduler state, provider link, and audit row.

Lifecycle audit records cover OAuth initiation/completion, verification, inbound enable/disable,
initial and delta runs, candidate import/skip, token refresh, disconnect, and reconnect. Audit
details contain IDs, counts, categories, and correlation IDs only—not bodies, Authorization
headers, access tokens, refresh tokens, or client secrets.

## Microsoft setup and Replit readiness

Create an Entra app named `Ingenuity Lead Routing - Outlook Dev` (or an equivalent operator-owned
name), select the account type that supports organizational and personal Microsoft accounts, add
delegated Microsoft Graph permissions listed above, and register the exact local callback from the
active configuration. Store the client ID and secret only in local ignored environment settings
for development. Production stores the client secret in Replit Secrets; workspace OAuth tokens
remain in the encrypted database secret store.

The implementation does not hard-code a Replit hostname. The production callback is:

`https://<production-origin>/v1/integrations/outlook/oauth/callback`

## Certification evidence

### Live certification checkpoint — 2026-09-27

- Repository branch: `main`; existing Phase 7H working-tree changes preserved.
- Repository head and applied database revision: `0020_outlook_mailbox_reconnect`.
  The local database was upgraded from `0019_unique_lead_source_event` using the
  Outlook-only active-mailbox index migration.
- API `/health/live`: `ok`; `/health/ready`: `ready`, PostgreSQL and Redis `ok`.
  Frontend at `http://127.0.0.1:5173` returned HTTP 200.
- Effective callback: `http://localhost:8001/v1/integrations/outlook/oauth/callback`;
  Microsoft authority: `common`. Microsoft client ID and secret remain unconfigured.
  The local `.env` is Git-ignored; no credential values were displayed.
- No Celery worker or beat was found in the local process inventory. Scheduler
  startup and certification remain pending; unrelated provider states were not changed.
- Outlook-focused unit suite: 14 passed. `git diff --check` passed (line-ending
  warnings only). Full final certification checks have not been rerun at this checkpoint.
- Entra was opened in the app browser, but its separate Azure sign-in did not
  complete. One visible attempt reported `timed_out`; retry reached Microsoft
  Authentication. App registration, consent, token exchange, mailbox verification,
  and all scheduled synthetic-email acceptance tests remain unverified.

Release status remains live certification pending, not Certified.

### Resume later

The user deferred further Microsoft setup and live certification. Outlook was moved
back to Coming Soon; no provider state, credential, checkpoint, or implementation
was deleted. Changes are saved in the local working tree, not committed.

Resume with Entra sign-in/app registration, organizational-and-personal account support,
the exact callback and delegated scopes above, then operator entry of
`MICROSOFT_CLIENT_ID` and `MICROSOFT_CLIENT_SECRET` into the ignored local `.env`.
Keep `MICROSOFT_TENANT_ID=common`. Reload the API and start exactly one worker and
one beat only after configuration, preserving unrelated providers. Complete real
OAuth and mailbox verification, then scheduled initial sync before asking for the
first synthetic test email. Replay, same-thread update, disable/re-enable,
disconnect/reconnect, security/isolation, and final regression checks still remain.
Do not label the integration Certified or restore it to Available before those pass.

Offline evidence is provided by `backend/tests/unit/test_outlook.py`, the shared inbound scheduler
and lifecycle tests, the existing Gmail/reconnect regression suite, Ruff, strict type checking,
the frontend production build, and `git diff --check`.

Live certification remains partial until a controlled Microsoft mailbox can complete OAuth and a
synthetic sender can exercise scheduled initial sync, replay, conversation update,
disable/re-enable, and same-mailbox reconnect. No personal real-business inquiry is used.
