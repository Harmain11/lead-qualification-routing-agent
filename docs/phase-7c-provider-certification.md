# Phase 7C provider certification gates

This document defines the shared lifecycle completed in Phase 7C1 and the gates each
provider phase must satisfy. The detailed baseline remains
[`phase-7c0-real-provider-readiness-audit.md`](phase-7c0-real-provider-readiness-audit.md).

## Shared connection lifecycle

Workspace-owned connections move through a deliberately small lifecycle:

1. `disconnected`: no usable local secret reference.
2. `awaiting_authorization`: OAuth state and PKCE verifier exist, but no provider token does.
3. `connected`: credentials are stored and structurally usable, but are not remotely verified.
4. `verified`: a provider-specific authenticated, low-risk probe succeeded.
5. `expired` or `needs_reauthorization`: the token cannot be refreshed or the provider rejected it.
6. `error`: verification or configuration failed without proving the credential was revoked.

The UI and API never equate “credentials stored” with “verified.” A provider without a
real validator returns `provider_validation_not_implemented` and fails closed. Provider
certification must add a low-risk probe and controlled mocked fixtures before the state can
become `verified`. Salesforce now supplies this probe through its read-only
`/services/data/{version}/limits` request; remaining providers must add their own.

## Secret ownership and storage

Operator configuration owns OAuth client IDs, OAuth client secrets, webhook/signing secrets,
and the encrypted-store master key. These values are deployment inputs and are never entered
by workspace users.

Workspace secret storage owns access tokens, refresh tokens, provider account credentials,
and token metadata. Production may use the included cloud-neutral encrypted database backend
(`lead_agent.infrastructure.encrypted_secrets:create_backend`) or another implementation of
the managed secret backend port. The included backend uses AES-256-GCM with random nonces,
workspace/provider/key-version binding as authenticated data, opaque `dbsecret://` references,
and deployment-supplied current and previous keys. Database rows contain ciphertext, never
plaintext. Rotation is performed by adding the old key to `SECRET_STORE_PREVIOUS_KEYS`, setting
a new active key/version, and replacing credentials so they are re-encrypted under the active
version. Deployment must keep master keys outside the database and retain old keys until all
records have rotated.

Secret reads and replacements require the expected workspace and provider. A copied reference
from another workspace or provider resolves to no credential. API responses, lifecycle audit
details, and provider errors contain no credential values or raw provider bodies.

## Runtime resolution and refresh

`WorkspaceAuthorizedConnectionResolver` is the common runtime boundary for CRM now and Slack
in Phase 7C4. It resolves only an active `connected` or `verified` connection in the requested
workspace. Callers must enforce the operation permission (`crm.sync` for CRM); the resolver also
offers a workspace-context method that checks an explicit required permission.

For OAuth credentials it loads the scoped secret, checks `token_expires_at` with a refresh skew,
refreshes when required, preserves or rotates the refresh token, atomically replaces the scoped
secret, updates expiration metadata, and records a correlated audit event. A per-connection lock
prevents duplicate refreshes inside one application process and the database row is selected for
update for multi-worker coordination. Provider-specific distributed behavior must still be
tested against PostgreSQL during certification. Runtime resolution never falls back to another
workspace or to global CRM credentials in production.

Tokens without provider expiration metadata are not assigned a synthetic expiry. This is
important for non-rotating Slack tokens and still permits future Gmail and Microsoft delegated
refresh-token flows.

## Validation, errors, and correlation

`ProviderConnectionValidator` receives only the provider, resolved credentials, and consented
scopes. Implementations must use an authenticated, non-mutating provider request and return a
structured result: connected, invalid credentials, expired/revoked, insufficient permissions,
rate limited, temporarily unavailable, malformed response, or configuration error.

The connection-test endpoint is workspace scoped, protected by `integrations.manage`, limited by
the existing provider-action rate limit, and persists a lifecycle audit event. Audit events carry
the existing request correlation ID, actor, workspace, connection, provider, outcome, safe error
code, and non-sensitive details. The standard provider error translator covers 400, 401, 403,
404, 409, 429, 408/425, 5xx, timeout, malformed responses, and revoked credentials without
persisting raw response bodies.

## Disconnect and revocation

Disconnect first removes the local reference, marks the connection inactive, and commits that
authoritative local state. `ProviderRevoker` then attempts provider-specific remote revocation
when supported. A failed or unsupported remote revoke is captured in the lifecycle result and
audit event, but cannot reactivate the local connection. Secret cleanup is attempted only after
a workspace/provider-scoped read, preventing a swapped reference from deleting another
workspace's credential.

## Credential model decisions

- Salesforce, HubSpot, Slack, Gmail, and Outlook connections are workspace owned. OAuth client
  credentials remain operator owned.
- OpenAI remains operator owned for this release. The runtime reads the deployment-provided
  project key and approved model; the Integrations card is informational and exposes no key-entry
  control. Phase 7C7 may revisit workspace-owned keys only as an explicit product decision.
- Slack runtime wiring and workspace channel selection remain Phase 7C4 work. It must consume the
  common resolver and must not use a temporary global-token fallback.
- Gmail and Outlook acquisition, polling/delta/subscription behavior, and mailbox checkpoints are
  intentionally deferred to their provider phases. The shared OAuth lifecycle supports delegated
  refresh tokens without selecting an acquisition mechanism.

## Provider-specific certification still required

Before any provider can be called certified, its phase must add and test: the real OAuth broker
configuration, low-risk validator, remote revoker where the provider supports one, safe mapping
of provider-specific error payloads and retry metadata, exact scopes, account identity, token
expiry/rotation semantics, and PostgreSQL concurrency behavior. Salesforce and HubSpot must also
complete the schema and real read-after-write checks from the 7C0 audit. No provider account,
application, credential, mailbox acquisition adapter, real CRM write, or Slack message was
created in Phase 7C1. Salesforce's shared-gate implementation is prepared in
[`phase-7c2a-salesforce-preparation.md`](phase-7c2a-salesforce-preparation.md), but real
Salesforce certification remains a separate controlled operation.

### OpenAI status

Phase 7C7 real-runtime certification is **PARTIAL and deferred** (2026-09-26). After
removing a stale placeholder `OPENAI_ORG_ID` override, the single zero-retry request reached
OpenAI and returned HTTP 429 with code `credit_balance_exhausted` and type
`insufficient_quota`. No successful Responses result or structured parse was obtained, so no
synthetic `process_lead()` was run. The key, model, billing, and permissions were unchanged.
Resume certification after the project credit/quota issue is resolved; this gate does not block
the next provider phase. The OpenAI runtime adapter remains implemented, with deterministic
qualification and fallback behavior kept authoritative.

### Phase 7C8 cross-provider certification status

**PASS — 2026-09-26.** The final evidence below supersedes the initial readiness notes.
All times below are UTC. Only automatic scheduled inbound was used; no Sync Now,
manual live import or agent-created Gmail reply was used. The user sent both inputs.

#### Real Gmail → pipeline → Slack results

Both messages belong to thread `1a0ddf8bea46f702` and existing local lead
`f214e0ef-0b65-46fc-ad1d-f102dd24477e`. One same-email person remained.
Workspace ICP resolution selected `system_default`; no published custom workspace ICP
was present. Email prose is not promoted to structured ICP fields by this adapter.

| Evidence | Inquiry | Meaningful inbound reply |
| --- | --- | --- |
| Gmail message | `1a0ddf8bea46f702` | `1a0de083eb5c0b0e` |
| Scheduled run | `0994c274-3ffd-4cec-8e20-04ac868440d5` | `9b8903d3-6def-480d-b550-37e484aad280` |
| ICP / intent / engagement score | 0 / 10 / 18 = **28** | 0 / 15 / 18 = **33** |
| Band / queue | nurture / nurture | nurture / nurture |
| Routing decision | `defe9e4d-057e-4e35-9233-0703ecf5ca1e` | `5afd40f6-9e2d-4063-9513-c96334973468` |
| Slack notification | `1473fd0b-08bf-475b-b56a-97416185be69` | `2c1826f1-dee8-4c7f-a103-669d0404e8b2` |
| Slack timestamp | `1790430798.667079` | `1790431543.650069` |
| Sent time | 13:53:18 | 14:05:43 |
| Shared Gmail / Slack correlation | `3545b133-d261-45bc-8ffc-29d89934e359` | `5485d383-cc31-4212-b87c-b1d0635484c6` |
| Durable `lead_notification` audit | `f979321d-cf9d-4492-9dee-3adf123be484` | `7c2ed49d-acb9-44e7-9f5c-64b80d39c8e6` |

The actor was `system:crm-inbound-scheduler`. Deterministic analysis completed for both.
The reply invoked `reprocess_lead()` and increased intent scoring. Existing source-change
policy forces a fresh routing decision even when nurture precedence retains the queue.
Exactly one second Slack notification was sent, with one attempt and provider success.
Acquisition/candidate audits share the run correlation with Slack; lead ID, ordered
normalized/analyzed/qualified/routed/notified events and routing references reconstruct
the intervening stages. Historical audit rows were not rewritten.

Unchanged scheduled run `f549d39f-138b-467b-92b4-522b403a36d7` completed at
14:11:40 with zero fetched messages and zero updates. The lead timestamp and six total
historical Gmail links, routing decisions and notifications stayed unchanged. Four
decisions/notifications predated this phase; the controlled flow added exactly two.
No third notification, duplicate person, routing decision or Gmail record appeared.
The Gmail lead had zero CRM writes throughout. Checkpoint/reconnect continuity remained intact.

#### Cross-provider live coexistence

An authorized external Description update to existing Salesforce example lead
`2b1407a3-edd0-4b11-8fd6-e88724cabada` (record `00Qaj00000y2zw5EAA`) was acquired
by scheduled run `86bac1a5-8e12-46bb-b7b4-95eb7a686996` at 14:10:39.
It reprocessed the same lead with one unique link; routing count rose three to four,
while its existing CRM write count stayed three: no new writeback.

An authorized external job-title update to existing HubSpot example contact
`555f8892-a3b0-4ca1-8b2e-16a84fdedc9c` (record `558036091640`) was acquired
by scheduled run `238f01cc-740e-4198-a12b-4aef04987ec8` at 14:11:50.
The same lead and one unique link were retained with zero CRM writes. An initial
Description update was rejected; a read-only schema check confirmed this account lacks
that Contacts property, so the existing `jobtitle` field was used.

All three inbound states remain verified/enabled and ready, with separate checkpoints,
next-sync times and error state. Gmail's real failed run at 13:58:38 retained checkpoint
`31297` while Salesforce/HubSpot continued. Scheduled recovery at 14:05:44 skipped a
deleted message and an outgoing own-message, imported the actual inbound reply and
advanced the checkpoint. No live tokens were revoked for testing.

#### Defects fixed, safety and regression

- Scheduled run correlations were not propagated into Slack delivery. A scoped request
  context now carries the correlation and restores the previous context afterward.
  Regression coverage checks both delivery/audit correlations and unchanged deduplication.
- A deleted message referenced by history blocked the batch and every Gmail 404 was
  misclassified as history expiry. History expiry is now endpoint-specific; disappearance
  during metadata/full-body acquisition is skipped as `message_no_longer_available`.
  Regression checks cover both fetch stages; 429/503 remain retryable failures.
- A worker restart from the backend directory missed the root `.env`; it was corrected
  to repository-root cwd. The worker has a process-only deterministic override. Provider
  configuration and secrets are unchanged; no migration was added.

Real acquisition/delivery audits contain safe references, counters and reasons only.
Seven resolved credential values were checked in memory against those audits and current
worker logs: none appeared. The raw meaningful Gmail body was also absent. Automated
provider-boundary tests cover transient errors/recovery, checkpoint retention, overlap,
own-writeback suppression, directionality, RBAC, secret redaction and workspace isolation.

Final focused regression: **132 passed** (two dependency deprecation warnings). Final full backend regression: **282 passed** (four dependency deprecation warnings).
Ruff passed; strict mypy passed (86 source files). Final `git diff --check`: passed.
No frontend files changed. Migration head remains `0017_gmail_mailbox_reconnect`.
One intended worker node and one beat scheduler remain active; working connections stay
verified. The pre-existing `celerybeat-schedule-wal` change was preserved.

OpenAI: **Deferred — live API blocked by unavailable API credits; deterministic fallback
certified operational.** No live OpenAI call was made or key/model/project/billing changed.
LinkedIn is deferred/removed from current scope; Outlook is deferred. Neither is certified.
Deployment/Replit configuration was not modified; no subsequent phase was started.

### Slack final status

**Both previously deferred Slack acceptance checks are COMPLETE:** the meaningful
scheduled Gmail update produced exactly one legitimate second notification, and its
real post-fix `lead_notification` lifecycle audit shares the acquisition correlation
without secret leakage. The following unchanged scheduled cycle produced no duplicates.
