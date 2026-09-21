# APIs, integrations, credentials, and data requirements

All items in this document are planning requirements. No provider SDK, OAuth flow, webhook handler, or API client is implemented in the current phase.

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
| Slack | Slack Web API for channel notification delivery; interactive review actions are optional | Bot token, channel ID, signing secret if receiving interactions | `SLACK_*` |
| Reasoning | OpenAI Responses API through an internal provider adapter | Project API key, approved model, data-retention decision | `OPENAI_*` |
| Observability | OTLP collector and optional error tracking | Collector endpoint and optional DSN | `OTEL_EXPORTER_OTLP_ENDPOINT`, `SENTRY_DSN` |

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

The current safe defaults are `LLM_PROVIDER=none`, `CRM_PROVIDER=none`, `MAILBOX_PROVIDER=none`, `SOCIAL_PROVIDER=none`, and `DEMO_DATA_ENABLED=false`.
