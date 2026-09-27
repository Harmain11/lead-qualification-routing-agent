# APIs, integrations, credentials, and data requirements

Provider SDKs and OAuth clients remain deferred except for the Phase 4 Slack notification adapter. Phase 2 implements provider-neutral adapter boundaries, fixture payloads, source-event persistence, and the website signature hook without requiring live credentials.

## Required external boundaries

| Boundary | Planned API or mechanism | Required access | Configuration |
| --- | --- | --- | --- |
| Website | Project-owned signed HTTPS webhook for contact/inquiry forms | Webhook signing secret and a source identifier | `WEBHOOK_SIGNING_SECRET` |
| Direct email | Gmail API or Microsoft Graph mail API; mailbox polling or push notifications | OAuth client, refresh token, mailbox access, least-privilege read scope | `MAILBOX_PROVIDER`, `GOOGLE_*`, `MICROSOFT_*` |
| Clutch v1 recommendation | Parse Clutch inquiry notification emails through the mailbox boundary | Mailbox access and a provider-specific parser fixture | Mailbox variables; no Clutch API key assumed |
| LinkedIn | Official permitted API path, preferably lead forms or approved inbound message access | LinkedIn app, approved products/scopes, access token, webhook verification if applicable | `LINKEDIN_*` |
| Optional social | Platform-owned webhook/API for DMs or comments | App credentials, webhook verification, approved scopes | `SOCIAL_*` |
| Salesforce | OAuth plus Salesforce REST API for Lead/Contact and custom fields | External Client App, client ID/secret, refresh token, sandbox or production login URL | `SALESFORCE_*` |
| HubSpot | OAuth or private-app CRM API for Contact/Deal properties | App credentials or private-app token, scopes for CRM objects and properties | `HUBSPOT_ACCESS_TOKEN` and future OAuth variables if needed |
| Slack | Slack Web API behind `NotificationProvider`; interactive review actions remain optional | Bot token and channel ID; signing secret only if receiving interactions | `NOTIFICATION_PROVIDER=slack`, `SLACK_*` |
| Reasoning | OpenAI Responses API through an internal provider adapter | Project API key, approved model, data-retention decision | `OPENAI_*` |
| Observability | OTLP collector and optional error tracking | Collector endpoint and optional DSN | `OTEL_EXPORTER_OTLP_ENDPOINT`, `SENTRY_DSN` |

Phase 2 fixture status:

- Website: functional through `/v1/intake/web`; HMAC-SHA256 verification is enabled when `WEBHOOK_SIGNING_SECRET` is configured.
- Email: functional through `/v1/intake/email` using a provider-neutral sender/subject/body contract; Gmail and Microsoft Graph adapters are not connected.
- LinkedIn: fixture-only through `/v1/intake/linkedin`; live use requires an officially approved lead-form, webhook, or export/import access path.
- Fourth channel: fixture-only through `/v1/intake/fourth`, currently representing the documented Clutch notification-email recommendation behind the mailbox boundary.

### Phase 3 reasoning status

- `LLM_PROVIDER=deterministic` (and legacy `none`) uses the offline provider and needs no credentials.
- `LLM_PROVIDER=workspace` resolves each lead's verified workspace OpenAI connection and allowlisted model. Customer keys are encrypted in the provider secret store and are not deployment environment variables. `LLM_PROVIDER=openai` remains development/test-only adapter mode and is rejected in production.
- `OPENAI_STORE_RESPONSES=false` is the safe local default; any change requires an approved retention/privacy decision.
- Missing credentials, timeout, provider unavailability, or invalid structured output creates a persisted fallback analysis and manual-review state.

## Credential handling

- Generate credentials in each provider’s developer/admin console; never paste them into source, fixtures, documentation, screenshots, or seed data.
- Use OAuth authorization-code flow with state and PKCE where supported. Store refresh/access tokens encrypted at rest and never return them through API responses.
- Request only the scopes required for the selected CRM, mailbox, and intake mode. Record the consented scope set and provider account identifier for support.
- Use the managed secret-store port in deployed environments. The repository includes a
  cloud-neutral AES-256-GCM database backend for staging/certification; its master key remains a
  deployment secret outside the database. Cloud secret-manager adapters remain supported.
- Rotate secrets without a code release. Keep a provider connection status and last successful health check, but do not expose credential material.
- OAuth application client credentials are operator-owned configuration. Access and refresh
  tokens are workspace-owned secrets and are resolved only through a workspace/provider-scoped
  reference.
- OpenAI is operator managed in the current release. Its workspace Integration card is
  informational and does not control the reasoning runtime.

The shared lifecycle and remaining per-provider certification gates are documented in
[`phase-7c-provider-certification.md`](phase-7c-provider-certification.md).

## Database requirements

PostgreSQL is required for production behavior and should be used locally from the first implementation phase.

- PostgreSQL 16, with migrations managed by Alembic.
- UUID primary keys; UTC `timestamptz` columns; explicit display timezone for reports.
- Relational columns for lead identity, attribution, score, lifecycle, ownership, and dates; JSONB only for versioned raw/provider payloads and structured analysis output.
- Unique idempotency constraint on `(provider, external_event_id)` and provider-specific deduplication keys.
- Append-only analysis, decision, pipeline, sync, notification, and audit records.
- Transactional outbox for CRM and Slack side effects.
- Indexes on source/date, lifecycle stage, score band, owner, review status, external IDs, and reporting dimensions.
- Foreign-key constraints with deliberate deletion/retention behavior for PII and raw events.
- Initial analytics as SQL views/materialized views. A warehouse is not required for the v1 demo.
- Backups, point-in-time recovery, migration rollback/runbook, and a retention/deletion policy before production use.

Redis is required for asynchronous jobs, locks, rate limits, and short-lived cache. It is not authoritative and must not be used to store the only copy of a lead or decision.

Object storage is optional for email attachments or provider files. If enabled, use encrypted buckets, short-lived signed URLs, malware scanning, and a retention policy.

## Environment variable contract

The complete placeholder contract is in [`.env.example`](../.env.example). Variables are grouped into:

- application/runtime and CORS;
- PostgreSQL and Redis;
- ICP/routing configuration paths;
- LLM provider settings and privacy mode;
- CRM selection and provider credentials;
- Slack notification credentials;
- Gmail/Microsoft mailbox credentials;
- LinkedIn and optional social credentials;
- webhook security, demo-data mode, and telemetry.

The current safe defaults are `LLM_PROVIDER=deterministic`, `NOTIFICATION_PROVIDER=mock`, `CRM_PROVIDER=mock`, `MAILBOX_PROVIDER=none`, `SOCIAL_PROVIDER=none`, and `DEMO_DATA_ENABLED=false`.

### Phase 4 routing and notification status

- Routing rules, queues, reps, and action plans are loaded from `config/routing.example.yaml` (or the configured routing path), not spread through business logic.
- Manual review has precedence over ordinary band routing; hard-disqualified leads are archived/disqualified with no rep assignment.
- `NOTIFICATION_PROVIDER=mock` is the default and records deterministic test deliveries without credentials.
- `NOTIFICATION_PROVIDER=slack` uses `SLACK_BOT_TOKEN` and `SLACK_CHANNEL_ID`. Missing or failed Slack delivery leaves the persisted routing decision intact and records `failed` or `retryable` state for a later retry.
- Duplicate processing reuses the current routing decision and notification record. A new notification/routing record requires an explicit reroute operation.

### Phase 5 CRM status

- `CRM_PROVIDER=mock` is deterministic, credential-free, and used by automated tests.
- `CRM_PROVIDER=salesforce` selects the Salesforce adapter. Production requests resolve an active
  workspace OAuth connection; its access/refresh tokens and instance URL are stored in the
  workspace secret store. The adapter uses the shared lifecycle's access token and does not run a
  second refresh exchange inside a CRM operation. The configured external-ID field drives REST
  sObject upsert.
- `CRM_PROVIDER=hubspot` keeps the optional private-app fallback, while workspace setup uses OAuth and encrypted per-workspace tokens. HubSpot Contacts use the provider-neutral inbound CRM service; writes remain explicit manual actions.
- A canonical payload carries lead identity, source, service, grounded summary, 40/35/25 components, total score, band, rationale, action plan, queue/rep, and urgency. Provider schemas remain inside adapters and mapping configuration.
- `crm_syncs` preserves payload hashes, attempts, provider IDs, state, errors, retryability, and history. Provider failure does not change successful lead/routing state.

### Inbound CRM synchronization

The provider-neutral inbound/bidirectional foundation is documented in
[`phase-7c2c-inbound-sync-foundation.md`](phase-7c2c-inbound-sync-foundation.md). It adds durable
provider-record links, resumable checkpoints, bounded sync runs, source-field fingerprints, and
provider-specific adapters without adding another scoring pipeline.

### Salesforce setup

1. Use a Salesforce sandbox or Developer Edition and create an External Client App with OAuth enabled. Request the OAuth scope parameters `api` and `refresh_token` (the Salesforce UI labels the latter “Perform requests at any time”), plus least-privilege read/write access to Lead. Opportunity sync is disabled by default.
2. Create a unique Text External ID on Lead named `Lead_Agent_ID__c`, or change `lead_external_id_field` in `config/crm.example.yaml`. Salesforce REST upsert requires the configured field to be an External ID; making it Unique provides database-level duplicate prevention.
3. Create or map the configured Lead fields: requested service, summary, total/component scores, ICP profile version, band, rationale, recommended action, assigned queue, and urgency/timeline. Long rationale/summary/action values should use Long Text Area fields.
4. If owner assignment is wanted, map internal rep IDs to Salesforce User IDs under `salesforce.owner_mapping`; otherwise ownership remains with Salesforce defaults.
5. Set `CRM_PROVIDER=salesforce`, `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`, `SALESFORCE_LOGIN_BASE_URL`, and `SALESFORCE_API_VERSION` outside Git. `SALESFORCE_REFRESH_TOKEN` is only an optional development fallback; production workspace tokens belong in a secret manager.

Salesforce’s current External Client App PKCE guidance and REST sObject upsert documentation are <https://developer.salesforce.com/docs/platform/data-loader-impl/guide/loader-eca-oauth-pkce.html> and <https://developer.salesforce.com/docs/platform/api-rest/guide/resources-sobject-upsert-patch.html>.

### HubSpot setup

HubSpot OAuth setup, Contacts-only object mapping, minimum scopes, actual callback, and the later
test-account certification checklist are documented in
[`phase-7c3a-hubspot-preparation.md`](phase-7c3a-hubspot-preparation.md). The default example creates
or upserts Contacts only; legacy optional Company/Deal outbound mappings remain opt-in.

### Workspace connection model

- Provider accounts belong to a workspace through one `provider_connections` table rather than provider-specific credential tables.
- Safe records contain provider/account identity, auth type, scopes, connection state, credential state, validation timestamps/results, and non-sensitive metadata.
- Credential material is submitted once to `SecretStore`; only an opaque reference is persisted. API responses deliberately omit both the reference and credential values.
- Salesforce, HubSpot, Slack, Gmail, and Outlook prefer OAuth. HubSpot private-app tokens and other approved manual credentials remain supported through the same connection boundary.
- One active CRM connection is selected per workspace. A connected workspace account takes precedence over `CRM_PROVIDER`; environment credentials remain a development fallback only.
- CRM synchronization rows retain the exact provider connection ID so entity IDs cannot bleed across workspaces or external accounts.

The development secret store and OAuth broker are intentionally process-local and rejected by production configuration. Production resolves a deployment-owned, cloud-neutral managed-secret backend through the common `SecretStore` lifecycle and configures operator-owned OAuth applications. Authorization state is hashed, expiring, actor/workspace/provider bound, and atomically single-use; PKCE uses S256, and token expiry/refresh/reconnect/disconnect paths stay behind provider ports.

The frontend setup catalog describes where credentials originate, minimum scopes, why they are needed, and links to official documentation. It never embeds or displays a user's values.

### Phase 6.5 connection experience

Salesforce, HubSpot, Slack, and Gmail present OAuth-first Connect/Reconnect actions. The customer does not create an app or paste OAuth application client secrets; application operators register one approved integration app per provider and inject those client credentials through the deployment secret system. LinkedIn and Outlook remain Coming Soon. OpenAI is workspace-managed BYOK: authorized workspace users submit a dedicated project key, choose from the server allowlist, test the Responses API connection, replace the key, or disconnect it without ever retrieving the current credential.

Connection states support connecting, awaiting authorization, validating, connected, needs attention, authorization expired, connection failed, reconnect required, and disconnected. OAuth state travels through the browser as required by the authorization flow; PKCE verifiers, provider tokens, and secret references are omitted from frontend responses.
