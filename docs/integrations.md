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
| Salesforce | OAuth plus Salesforce REST API for Lead/Contact and custom fields | Connected app, client ID/secret, refresh token, sandbox or production login URL | `SALESFORCE_*` |
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
- `LLM_PROVIDER=openai` selects the schema-constrained OpenAI Responses API adapter. `OPENAI_API_KEY` and `OPENAI_MODEL` must then be supplied outside Git.
- `OPENAI_STORE_RESPONSES=false` is the safe local default; any change requires an approved retention/privacy decision.
- Missing credentials, timeout, provider unavailability, or invalid structured output creates a persisted fallback analysis and manual-review state.

## Credential handling

- Generate credentials in each provider’s developer/admin console; never paste them into source, fixtures, documentation, screenshots, or seed data.
- Use OAuth authorization-code flow with state and PKCE where supported. Store refresh/access tokens encrypted at rest and never return them through API responses.
- Request only the scopes required for the selected CRM, mailbox, and intake mode. Record the consented scope set and provider account identifier for support.
- Use a managed secret manager in deployed environments. `.env` is for local development only and is ignored by Git.
- Rotate secrets without a code release. Keep a provider connection status and last successful health check, but do not expose credential material.

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
- `CRM_PROVIDER=salesforce` uses OAuth refresh-token authentication and REST sObject upsert through the external-ID field configured in `config/crm.example.yaml`.
- `CRM_PROVIDER=hubspot` uses a private-app bearer token, CRM v3 batch upsert, and CRM v4 default associations.
- A canonical payload carries lead identity, source, service, grounded summary, 40/35/25 components, total score, band, rationale, action plan, queue/rep, and urgency. Provider schemas remain inside adapters and mapping configuration.
- `crm_syncs` preserves payload hashes, attempts, provider IDs, state, errors, retryability, and history. Provider failure does not change successful lead/routing state.

### Salesforce setup

1. Use a Salesforce sandbox or Developer Edition and create an External Client App (or an existing Connected App) with OAuth enabled. Grant the dedicated integration user `api` and refresh-token/offline access, plus least-privilege read/write access to Lead and any enabled Opportunity fields.
2. Create a unique Text External ID on Lead named `Lead_Agent_ID__c`, or change `lead_external_id_field` in `config/crm.example.yaml`. Salesforce REST upsert requires the configured field to be an External ID; making it Unique provides database-level duplicate prevention.
3. Create or map the configured Lead fields: requested service, summary, total/component scores, band, rationale, recommended action, assigned queue, and urgency/timeline. Long rationale/summary/action values should use Long Text Area fields. If Opportunity sync is enabled, create the configured unique Opportunity external ID and mapped fields and confirm valid stage names.
4. If owner assignment is wanted, map internal rep IDs to Salesforce User IDs under `salesforce.owner_mapping`; otherwise ownership remains with Salesforce defaults.
5. Set `CRM_PROVIDER=salesforce`, `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET`, `SALESFORCE_REFRESH_TOKEN`, `SALESFORCE_LOGIN_BASE_URL`, and `SALESFORCE_API_VERSION` outside Git. Production secrets belong in a secret manager.

Salesforce’s official REST documentation describes OAuth authorization and sObject upsert by external ID: <https://developer.salesforce.com/docs/platform/api-rest/guide/intro-oauth-and-connected-apps.html> and <https://developer.salesforce.com/docs/platform/api-rest/guide/resources-sobject-upsert-patch.html>.

### HubSpot setup

1. Create a HubSpot private app for the target account. Grant least-privilege read/write scopes for contacts, companies, deals, and their associations.
2. Create the custom contact properties listed under `hubspot.contact_fields` in `config/crm.example.yaml`, or change those mappings to approved existing properties.
3. Create `lead_agent_company_key` as a unique company property and `lead_agent_lead_id` as a unique deal property (or change the configured ID property names). These identities make company/deal replay upserts deterministic. Contact upsert uses email.
4. Confirm the configured deal pipeline and stage values exist in the target portal. Map internal rep IDs to HubSpot owner IDs under `hubspot.owner_mapping` if owner assignment is wanted.
5. Set `CRM_PROVIDER=hubspot` and `HUBSPOT_ACCESS_TOKEN` outside Git. Override `HUBSPOT_API_BASE_URL` only for an approved proxy/test environment.

HubSpot documents bearer-token authentication and contact batch upsert by email; the adapter uses the corresponding CRM v3 object upsert and v4 association boundaries: <https://developers.hubspot.com/changelog/september-2024-rollup>.

### Workspace connection model

- Provider accounts belong to a workspace through one `provider_connections` table rather than provider-specific credential tables.
- Safe records contain provider/account identity, auth type, scopes, connection state, credential state, validation timestamps/results, and non-sensitive metadata.
- Credential material is submitted once to `SecretStore`; only an opaque reference is persisted. API responses deliberately omit both the reference and credential values.
- Salesforce, HubSpot, Slack, Gmail, and Outlook prefer OAuth. HubSpot private-app tokens and other approved manual credentials remain supported through the same connection boundary.
- One active CRM connection is selected per workspace. A connected workspace account takes precedence over `CRM_PROVIDER`; environment credentials remain a development fallback only.
- CRM synchronization rows retain the exact provider connection ID so entity IDs cannot bleed across workspaces or external accounts.

The development secret store and OAuth broker are intentionally process-local. The provider broker implements authorization URL construction, code exchange, PKCE, access-token expiry metadata, and refresh-token exchange behind the common port. Production must bind `SecretStore` to a managed encrypted service and configure operator-owned OAuth applications. Distributed callback locking, provider-specific remote identity validation, and provider-specific revocation calls remain Phase 7 deployment work.

The frontend setup catalog describes where credentials originate, minimum scopes, why they are needed, and links to official documentation. It never embeds or displays a user's values.

### Phase 6.5 connection experience

Salesforce, HubSpot, Slack, Gmail, and Outlook present OAuth-first Connect/Reconnect actions. The customer does not create an app or paste client secrets; application operators register one approved integration app per provider and inject its client credentials through the deployment secret system. LinkedIn remains gated to officially approved products/scopes. OpenAI retains guided one-time API-key entry because no equivalent customer-account authorization is assumed.

Connection states support connecting, awaiting authorization, validating, connected, needs attention, authorization expired, connection failed, reconnect required, and disconnected. OAuth state travels through the browser as required by the authorization flow; PKCE verifiers, provider tokens, and secret references are omitted from frontend responses.
