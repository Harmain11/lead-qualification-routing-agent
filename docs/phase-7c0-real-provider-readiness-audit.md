# Phase 7C0 — Real Provider Readiness Audit

Audit date: 2026-09-23  
Repository baseline supplied by the operator: Phase 7B PASS; migration head `0013`; no live providers connected.

## Executive result

**Phase 7C0 audit result: complete, but the repository is not yet ready for full real-provider certification.** Salesforce and HubSpot have real CRM adapters and workspace-scoped credential resolution. OpenAI has a real Responses API adapter. Slack has a real Web API adapter. However, several control-plane connections are not wired to those runtime adapters, Gmail and Outlook have no mailbox acquisition implementation, connection testing is structural rather than live, remote revocation is unimplemented, and the repository contains only the managed-secret-store port—not a deployable managed backend.

No provider registration, login, consent, credential entry, or live call was performed. No application code or provider configuration was changed. This report is the only repository addition.

## 1. Architecture traced from code

| Concern | Code-backed finding |
|---|---|
| Connection model | `ProviderConnectionORM` is workspace-scoped and stores provider/account metadata, scopes, status, an opaque `secret_reference`, validation state, and activation state. No secret value is stored in the row. |
| OAuth start | `POST /v1/integrations/{provider}/connect`; frontend calls it from the Integrations view and then assigns `window.location` to the returned authorization URL. |
| Browser callback | `GET /v1/integrations/{provider}/oauth/callback`; a separate authenticated JSON callback exists at `POST /v1/integrations/{provider}/callback`. |
| OAuth state | Random state is `connection UUID + 32-byte URL-safe token`; only its SHA-256 hash is persisted. It expires after 10 minutes, is actor-bound and provider-bound, and is claimed with a compare-and-set before token exchange. |
| PKCE | A 64-byte URL-safe verifier is stored temporarily in the secret store; S256 challenge is sent for every OAuth provider and the verifier is sent on exchange. |
| Token storage | Access/refresh tokens and the temporary PKCE verifier live behind `SecretStore`; only an opaque reference is persisted. Development storage is process-local. Production requires `ManagedSecretStore`. |
| Token refresh | Generic refresh exists in `validate_connection` when recorded expiry has passed and a refresh token exists. CRM and notification execution do not call this lifecycle service before every operation. Salesforce independently exchanges its refresh token for an access token on every CRM sync. |
| Disconnect | Local connection is deactivated first, DB reference removed, then secret invalidated/deleted. `ProviderOAuthBroker.revoke` always returns `False`, so live remote revocation is unsupported. |
| Integration APIs | List/retrieve, manual credentials, OAuth start, browser/JSON callback, structural test, disconnect, and manual reconnect are present. |
| Frontend | Integrations is an in-memory SPA view, not a durable URL route. Connect buttons exist and are permission-gated. OAuth callbacks redirect to frontend root with query parameters, but `App` defaults to Dashboard and only `IntegrationsView` consumes those parameters when mounted. |
| CRM abstraction | `CrmProvider.sync(CanonicalCrmPayload, existing)` with mock, Salesforce, and HubSpot implementations. Workspace resolver uses the active connected Salesforce/HubSpot secret before environment fallback. |
| Notification abstraction | `NotificationProvider.send(NotificationMessage)` with mock and Slack implementations. Runtime factory is global-settings based and does not resolve workspace Slack connections. |
| OpenAI abstraction | `ReasoningProvider.analyze`; OpenAI uses `client.responses.parse` with a Pydantic schema. Runtime factory is global-settings based and does not resolve workspace OpenAI connections. |
| Retry | CRM retries retryable errors up to `CRM_MAX_ATTEMPTS`, immediately, with no backoff/jitter or `Retry-After` handling. OpenAI delegates retries to the SDK. Slack returns retryable for every SDK exception; retry is user/API initiated rather than scheduled automatically. |
| Provider audit | CRM and Slack emit redacted structured operation logs; CRM persists attempt history and pipeline events. Integration lifecycle has mutable `last_validation_result`, but no immutable provider-connection audit event table. Agent-tool audit is separate. |

Primary code evidence: `application/integrations.py`, `infrastructure/oauth.py`, `infrastructure/secrets.py`, `api/routes/integrations.py`, `infrastructure/crm/*`, `infrastructure/notifications/*`, `infrastructure/reasoning/*`, and `infrastructure/intake/adapters.py`.

## 2. Provider readiness matrix

All OAuth providers share the frontend sequence: open **Integrations** in the root SPA → click **Connect** → frontend calls the start route below → browser is redirected to the provider. There is no frontend pathname dedicated to Integrations.

| Provider | Status | Authentication | Start / callback | Code-requested scopes | Current provider operations | Token/disconnect readiness |
|---|---|---|---|---|---|---|
| Salesforce | **Partially implemented** | OAuth 2.0 authorization code + PKCE; operator client secret; manual private-token contract also exists at API level | `POST /v1/integrations/salesforce/connect`; `GET /v1/integrations/salesforce/oauth/callback` | `api`, `refresh_token/offline_access` | Lead REST sObject upsert and fetch-after-204; optional Opportunity upsert is disabled by default | Access/refresh/expiry stored; CRM uses refresh token each sync; local disconnect works; remote revoke absent |
| HubSpot | **Partially implemented** | OAuth 2.0 authorization code + PKCE; private token also supported by API/runtime | `POST /v1/integrations/hubspot/connect`; `GET /v1/integrations/hubspot/oauth/callback` | contacts, companies and deals read + write (six scopes) | Batch upsert Contact, Company, Deal; default associations | Access/refresh/expiry stored; refresh occurs only via connection validation; local disconnect works; remote revoke absent |
| Slack | **Partially implemented** | Slack OAuth v2 authorization code + PKCE parameters; bot token manual contract | `POST /v1/integrations/slack/connect`; `GET /v1/integrations/slack/oauth/callback` | `chat:write` | `chat.postMessage` to one configured channel | OAuth token is stored but runtime ignores it; channel is global env; no channel selection; remote revoke absent; default one-hour expiry metadata is wrong for non-rotating Slack tokens |
| Google/Gmail | **Placeholder / OAuth foundation only** | Google web-server OAuth authorization code + PKCE, offline access, forced consent | `POST /v1/integrations/gmail/connect`; `GET /v1/integrations/gmail/oauth/callback` | `https://www.googleapis.com/auth/gmail.readonly` | None. Only a generic `POST /v1/intake/email` normalization endpoint exists. | Token storage/refresh metadata exists; no Gmail API consumer, polling, watch/Pub/Sub, history cursor, or remote revoke |
| Microsoft/Outlook | **Placeholder / OAuth foundation only** | Microsoft identity platform v2 authorization code + PKCE; delegated permissions | `POST /v1/integrations/outlook/connect`; `GET /v1/integrations/outlook/oauth/callback` | `Mail.Read`, `offline_access` | None. Only the generic email normalization endpoint exists. | Token storage/refresh metadata exists; no Graph mail consumer, subscription/delta cursor, or remote revoke |
| OpenAI | **Implemented runtime, placeholder connection card** | Project API key from environment; workspace API-key connection can be stored but is unused | No OAuth start or callback. UI posts to `POST /v1/integrations/openai/credentials`. | Responses API project access | `responses.parse` structured context/intent/ICP extraction | Global environment key only; no workspace resolver; connection “test” checks key presence only; no revoke concept |

### Scope minimization notes

- Salesforce operations require REST API access and offline refresh: `api` plus `refresh_token`/`offline_access`.
- HubSpot application calls are writes. The current OAuth metadata requests six read/write scopes. For runtime-only sync, the three `.write` scopes are the minimum apparent set; the three `.read` scopes should be retained only if the certification fetch/verify step is automated with the same token. The app registration and authorization URL must match whichever set is chosen.
- Slack can remain at `chat:write` if the bot is explicitly invited to the selected channel. Do not add `chat:write.public` merely to avoid channel installation/selection work.
- Gmail needs only `gmail.readonly`; do not request modify, send, or full-mail scopes.
- Outlook should use delegated `Mail.Read` plus `offline_access`, not application `Mail.Read`, for the current per-user Connect experience.

## 3. Exact redirect/callback requirements

The callback URI is built as:

`INTEGRATION_PUBLIC_BASE_URL.rstrip('/') + /v1/integrations/{provider}/oauth/callback`

### Development

| Provider | Exact development redirect URI |
|---|---|
| Salesforce | `http://localhost:8000/v1/integrations/salesforce/oauth/callback` |
| HubSpot | `http://localhost:8000/v1/integrations/hubspot/oauth/callback` |
| Slack | `http://localhost:8000/v1/integrations/slack/oauth/callback` |
| Google | `http://localhost:8000/v1/integrations/gmail/oauth/callback` |
| Microsoft | `http://localhost:8000/v1/integrations/outlook/oauth/callback` |

Slack requires HTTPS for configured redirect URLs outside its documented local/development allowances; use a public HTTPS development tunnel if its console rejects localhost HTTP.

### Staging and production

No staging or production API domain is present in repository configuration. Register the exact provider-specific path behind the actual API domain:

- `https://<API_DOMAIN>/v1/integrations/salesforce/oauth/callback`
- `https://<API_DOMAIN>/v1/integrations/hubspot/oauth/callback`
- `https://<API_DOMAIN>/v1/integrations/slack/oauth/callback`
- `https://<API_DOMAIN>/v1/integrations/gmail/oauth/callback`
- `https://<API_DOMAIN>/v1/integrations/outlook/oauth/callback`

Use the staging API domain for staging registrations and the production API domain for production registrations. Do not register a frontend URL. OpenAI has no redirect URI.

## 4. Environment-variable inventory

### Cross-provider control plane

| Variable | Classification | Finding |
|---|---|---|
| `OAUTH_BROKER` | production-required for OAuth | Must be `provider`; `development` is deterministic only. |
| `INTEGRATION_PUBLIC_BASE_URL` | required; production HTTPS | Source of all callback URIs. |
| `FRONTEND_PUBLIC_BASE_URL` | required; production HTTPS | OAuth completion redirect target. |
| `SECRET_STORE_PROVIDER` | production-required | Must be `managed`. |
| `MANAGED_SECRET_STORE_BACKEND` | production-required | `package.module:factory`; no real adapter exists in this repository. |
| `CRM_CONFIG_PATH` | required/defaulted | Object/property mapping file. |
| `CRM_MAX_ATTEMPTS` | optional | Default `3`. |
| `CRM_TIMEOUT_SECONDS` | optional | Default `20`. |

### Salesforce

| Variable | Classification |
|---|---|
| `SALESFORCE_CLIENT_ID` | required for provider OAuth; production validator also requires it when Salesforce is selected |
| `SALESFORCE_CLIENT_SECRET` | required by current confidential-client exchange and CRM refresh flow; production-required when selected |
| `SALESFORCE_REFRESH_TOKEN` | environment fallback only in the intended workspace design, but currently production-required by settings validation when `CRM_PROVIDER=salesforce` |
| `SALESFORCE_LOGIN_BASE_URL` | required/defaulted; `https://login.salesforce.com`, or `https://test.salesforce.com` for sandbox |
| `SALESFORCE_API_VERSION` | required/defaulted (`v68.0`) |
| `CRM_PROVIDER` | production must be `salesforce` or `hubspot`; environment fallback selector |

### HubSpot

| Variable | Classification |
|---|---|
| `HUBSPOT_CLIENT_ID` | required for OAuth; production-required when HubSpot selected |
| `HUBSPOT_CLIENT_SECRET` | required for OAuth; production-required when HubSpot selected |
| `HUBSPOT_ACCESS_TOKEN` | private-app/environment fallback, but currently production-required by settings validation when `CRM_PROVIDER=hubspot` |
| `HUBSPOT_API_BASE_URL` | required/defaulted (`https://api.hubapi.com`) |
| `CRM_PROVIDER` | production selector/fallback |

### Slack

| Variable | Classification |
|---|---|
| `SLACK_CLIENT_ID` | required for OAuth; production-required by current validation |
| `SLACK_CLIENT_SECRET` | required for OAuth; production-required by current validation |
| `SLACK_BOT_TOKEN` | currently runtime-required and production-required; this proves OAuth token wiring is incomplete |
| `SLACK_CHANNEL_ID` | currently runtime-required and production-required; no per-workspace channel selection exists |
| `NOTIFICATION_PROVIDER` | set to `slack`; production-required |

### Google

| Variable | Classification |
|---|---|
| `GOOGLE_CLIENT_ID` | required for Gmail OAuth |
| `GOOGLE_CLIENT_SECRET` | required by current exchange |
| `MAILBOX_PROVIDER` | documented/defaulted, but unused by runtime |

### Microsoft

| Variable | Classification |
|---|---|
| `MICROSOFT_TENANT_ID` | optional/default `common`; choose tenant ID or `organizations` for controlled enterprise-only testing |
| `MICROSOFT_CLIENT_ID` | required for Outlook OAuth |
| `MICROSOFT_CLIENT_SECRET` | required by current exchange |
| `MAILBOX_PROVIDER` | documented/defaulted, but unused by runtime |

### OpenAI

| Variable | Classification |
|---|---|
| `LLM_PROVIDER` | `workspace` in production; `openai` is development/test-only |
| `OPENAI_SUPPORTED_MODELS` | non-secret server allowlist |
| `OPENAI_DEFAULT_MODEL` | non-secret default workspace selection |
| `OPENAI_API_KEY` / `OPENAI_MODEL` / `OPENAI_ORG_ID` | optional development/test adapter settings; forbidden as the production customer credential path |
| `OPENAI_TIMEOUT_SECONDS` | optional, default `30` |
| `OPENAI_MAX_RETRIES` | optional, default `2` |
| `OPENAI_STORE_RESPONSES` | optional, default `false`; retain false unless retention is approved |

This original readiness finding has been superseded for OpenAI: production now resolves verified workspace connections through the encrypted `SecretStore`. OAuth application client secrets for OAuth providers remain deployment-owned.

## 5. Secret-handling review

### Ready controls

- Source contains placeholders, not provider values; provider fields are not exposed as `VITE_*` variables.
- API response schemas omit `secret_reference` and token values. Connection metadata rejects nested sensitive-key names and hides OAuth state metadata.
- Logs pass through bearer/header/query/JSON redaction and provider logs carry provider/connection/operation IDs rather than payload secrets.
- Agent-tool audit recursively redacts sensitive keys and records generic failure text.
- OAuth tokens, refresh tokens and PKCE verifiers go through the configured `SecretStore`, and only an opaque reference reaches the database.
- Disconnect makes future application use fail closed even if backend deletion later fails.

### Gaps

- The managed backend implementation is deployment-owned and absent. Production cannot start safely until `MANAGED_SECRET_STORE_BACKEND` resolves a real backend.
- Operator OAuth client secrets and the global OpenAI/Slack/CRM fallback secrets bypass `SecretStore` and depend on deployment environment/secret injection. Document the deployment secret manager as their initial configuration location.
- Provider lifecycle lacks immutable audit rows.
- Provider SDK exception text can be persisted as bounded error text in some Slack/CRM paths. Existing redaction protects logs, not every DB field; adapters should keep errors sanitized.

End users should not enter developer client secrets. The intended experience is correctly **Connect → Provider Login → Approve → Connected**. Operators configure app client ID/secret once in the deployment secret manager/environment. OpenAI is currently operator-global despite a workspace API-key UI.

## 6. Salesforce readiness

- **Recommended test environment:** isolated Salesforce Developer Edition for Phase 7C; use a sandbox only when production metadata parity is specifically required. As of Salesforce Spring '26, create an **External Client App** rather than a new Connected App.
- **OAuth:** web-server authorization code + PKCE; callback path `/v1/integrations/salesforce/oauth/callback`; scopes `api` and `refresh_token/offline_access`.
- **Login host:** Developer Edition uses `https://login.salesforce.com`; sandbox uses `https://test.salesforce.com`.
- **Object used:** `Lead`. `Opportunity` exists in code but `sync_opportunity: false` by default. No Contact object is created.
- **Standard Lead fields:** `FirstName`, `LastName`, `Company`, `Email`, `Title`, `Phone`; optional mapped `OwnerId`.
- **Required Lead custom fields:** `Lead_Agent_ID__c` (text, unique external ID), `Requested_Service__c`, `Lead_Summary__c`, `Qualification_Score__c`, `Qualification_Band__c`, `ICP_Score__c`, `Intent_Urgency_Score__c`, `Engagement_Score__c`, `Qualification_Rationale__c`, `Recommended_Action__c`, `Sales_Action_Plan__c`, `Assigned_Queue__c`, `Assigned_Rep_Name__c`, `Urgency_Timeline__c`.
- **Upsert/dedup:** PATCH `/sobjects/Lead/Lead_Agent_ID__c/{internal_lead_uuid}`. The same internal lead UUID is stable across resubmission. After a 204 without body, the adapter GETs the same external-ID URL to recover `Id`.
- **Stored identifier:** Salesforce Lead ID in `crm_syncs.provider_entity_ids.contact_id`; optional Opportunity ID in `opportunity_id`.
- **Optional Opportunity prerequisites if enabled:** external ID `Lead_Agent_ID__c`; mapped qualification/action fields from `config/crm.example.yaml`; valid StageName mapping.
- **Pre-test gates:** create all custom fields with compatible types/lengths; ensure external ID uniqueness; decide Developer Edition versus sandbox before setting the global login base; provide managed secret backend; add live connection validation and safe credential-refresh-before-operation behavior.

## 7. HubSpot readiness

- Create a HubSpot developer platform account/app and a separate developer test account. Use an OAuth-capable public/multi-account app for the Connect flow; private-app tokens only exercise the fallback path.
- Callback path: `/v1/integrations/hubspot/oauth/callback`.
- Current requested scopes: `crm.objects.contacts.read/write`, `crm.objects.companies.read/write`, `crm.objects.deals.read/write`.
- Objects: Contact, Company and Deal; default associations Contact→Company, Deal→Contact, Deal→Company.
- Contact standard mappings: `email`, `firstname`, `lastname`, `company`, `jobtitle`, `phone`, optional `hubspot_owner_id`.
- Contact custom properties: `lead_source_channel`, `requested_service`, `lead_summary`, `qualification_score`, `qualification_band`, `icp_score`, `intent_urgency_score`, `engagement_score`, `qualification_rationale`, `recommended_action`, `sales_action_plan`, `assigned_queue`, `assigned_rep_name`, `urgency_timeline`.
- Company properties: standard `name`; custom unique `lead_agent_company_key`; custom `lead_source_channel`, `qualification_band`.
- Deal properties: standard `dealname`, `pipeline`, `dealstage`; custom unique `lead_agent_lead_id`; custom `qualification_score`, `qualification_band`, `lead_summary`, `recommended_action`, `sales_action_plan`, `assigned_queue`, `assigned_rep_name`.
- Dedup: Contact by standard email; Company by SHA-256-derived `company_key`; Deal by internal lead UUID. Custom identity properties must be created as unique before batch upsert.
- Stored IDs: Contact→`contact_id`, Company→`company_id`, Deal→`opportunity_id`.
- Pre-test gates: create all properties; validate pipeline/stage IDs; ensure installing user may edit associations; decide whether read scopes are required; add refresh-before-operation/live validation. The adapter uses legacy `/crm/v3` paths while HubSpot now recommends the date-versioned 2026-03 API for new integrations—compatibility must be confirmed before certification.

## 8. Slack readiness

- Create a Slack app with a bot user, OAuth v2 redirect, and bot scope `chat:write`.
- Invite the bot to one controlled test channel. This avoids the broader `chat:write.public` scope.
- Callback: `/v1/integrations/slack/oauth/callback`.
- Message path: Slack SDK `chat_postMessage(channel=..., text=..., unfurl_links=False, unfurl_media=False)`.
- Current channel behavior: no selection/listing UI and no per-workspace channel metadata. Runtime uses global `SLACK_CHANNEL_ID`.
- Disconnect deletes local token reference but does not call Slack `auth.revoke`.
- Blockers: wire active workspace Slack connection into the notification factory; define and persist a per-workspace channel selection; handle Slack token expiry/rotation semantics correctly; implement live `auth.test`/channel validation; implement revocation.

## 9. Google/Gmail readiness

- Human creates a Google Cloud project, enables Gmail API, configures OAuth consent, creates a **Web application** OAuth client, and registers the exact callback.
- Scope: only `https://www.googleapis.com/auth/gmail.readonly`.
- Broker correctly requests `access_type=offline` and `prompt=consent`, so a refresh token is expected on first consent.
- In Testing publishing status, add only controlled test users. Google documents user limits/warnings and limited refresh-token lifetime for testing apps.
- No mailbox polling, `users.messages.list/get`, Gmail `watch`, Pub/Sub webhook, history cursor, attachment handling, or provider-to-workspace mailbox binding exists.
- Blocker: Phase 7C5 cannot certify email intake until a Gmail acquisition adapter and scheduler/push boundary feed the existing `EmailAdapter`/`process_source_event` pipeline.

## 10. Microsoft/Outlook readiness

- Human creates a Microsoft Entra app registration with Web redirect URI and a supported account type consistent with `MICROSOFT_TENANT_ID` (`common`, `organizations`, or a specific tenant ID).
- Use delegated Microsoft Graph permissions `Mail.Read` plus `offline_access`; application permission is unnecessary and broader. `Mail.Read` delegated can read the signed-in mailbox and does not require admin consent by default, although tenant policy can still require approval.
- Callback: `/v1/integrations/outlook/oauth/callback`.
- Generic refresh-token exchange exists.
- No Graph `/me/messages` acquisition, delta query, change notification subscription, subscription renewal, cursor/checkpoint, or workspace mailbox binding exists.
- Blocker: Phase 7C6 cannot certify email intake until the Graph acquisition adapter exists.

## 11. OpenAI readiness

- Environment: `LLM_PROVIDER=workspace`; supported/default model controls are non-secret. Customer keys are runtime workspace secrets.
- Factory: production lead analysis resolves workspace → verified OpenAI connection → workspace-bound encrypted secret → allowlisted selected model.
- API: Python SDK `client.responses.parse`, model from the workspace connection, Pydantic `ReasoningOutput`, `store=OPENAI_STORE_RESPONSES`.
- Structured behavior: strict Pydantic models forbid extra properties, validate enums/ranges/lengths, and output only context/ICP evidence—not the deterministic final score.
- Failure behavior: SDK timeout/unavailable/invalid output becomes deterministic fallback, `provider_error`, and manual review. SDK retry count is `OPENAI_MAX_RETRIES`; timeout is `OPENAI_TIMEOUT_SECONDS`.
- Live certification uses a controlled workspace key when available and asserts persisted analysis provider/model/status. Deterministic fallback remains enabled as a safety path.
- Workspace BYOK control-plane and runtime resolution are implemented; live certification remains dependent on a funded customer test key.

## 12. Controlled real test data

Use one synthetic identity consistently:

| Field | Value |
|---|---|
| Name | Jordan Test |
| Company | Phase7 Testing Corp |
| CRM-only email | `jordan.test@phase7-testing.example` |
| Title | VP of Operations |
| Industry | SaaS |
| Employees | 180 |
| Geography | United States |
| Budget | USD 30,000 |
| Service | Lead routing automation |
| Phone | omit unless the controlled environment owns the number |
| Message | “We are ready to implement lead routing automation this month. We need a proposal, Salesforce and HubSpot workflow integration, and a demo this week because slow response is causing missed leads. Our approved budget is USD 30,000. Please include implementation scope, timeline, reporting workflow, ownership, and marketing-to-sales handoff.” |

Against the repository example ICP and deterministic scoring policy this fixture is expected to score **100/100**, classification **hot**, components **40 ICP / 35 intent-urgency / 25 engagement**, route **senior_rep**, priority **urgent**, expected rep **Alex Morgan**. For real Gmail/Outlook delivery, use human-created controlled sender and recipient mailboxes; do not use the reserved `.example` address and do not use customer data.

## 13. Real CRM certification criteria

Run the following separately for Salesforce and HubSpot and record local IDs plus screenshots/API reads from the real provider:

1. Submit Jordan Test; assert local CRM sync `synced` and capture provider IDs.
2. Fetch in the provider UI/API by returned ID and stable identity; verify every mapped field.
3. Change at least budget/message/qualification-derived fields locally and force sync.
4. Fetch again and verify the same provider IDs contain updated values.
5. Resubmit/force-sync the same internal lead.
6. Search provider by email and external ID; assert exactly one intended Lead/Contact, one Company, and one Deal where applicable.
7. Disconnect; assert connection inactive, secret reference absent from API/DB response, and new CRM sync is blocked or resolves no usable connection.
8. Attempt forced sync and assert no provider write occurs.
9. Reconnect through OAuth and verify a new/renewed connection.
10. Force sync again and verify the existing provider record updates without duplication.

Do not treat a local `synced` response as proof; verification must occur in Salesforce/HubSpot. Current app has no provider fetch endpoint, so use provider UI or controlled read-only certification tooling.

## 14. Future email certification criteria (do not execute in 7C0)

Controlled real email → provider acquisition with immutable message ID → `EmailAdapter` normalization → OpenAI structured context extraction → published workspace ICP resolution → deterministic score → routing decision → CRM upsert → Slack notification.

Assert each persisted boundary: source-event dedup, normalized sender/body, live OpenAI provider/model, ICP version/hash, deterministic score, route/rep/SLA, provider entity IDs, Slack timestamp, and replay of the same message producing no duplicate lead/CRM record/notification.

## 15. Workspace/RBAC certification

1. Connect provider in Workspace A; attempt retrieve/test/disconnect and CRM use from Workspace B; expect 404/no connection resolution.
2. Deny `integrations.connect`; start route returns 403.
3. Deny `integrations.disconnect`; disconnect returns 403.
4. Deny `crm.sync`; sync and retry routes return 403.
5. Inspect DB rows: only opaque references, each connection has correct workspace ID, and resolver selects only that workspace's active CRM.
6. Suspend/revoke membership while a session exists; next request must fail because membership is queried as `active` without permission caching.
7. Start OAuth, then suspend the initiating member before callback; callback must fail membership/permission validation.
8. Switch workspace during OAuth; callback must still bind to the connection's original workspace (covered by an existing unit test, but repeat in real flow).

## 16. Failure cases and current behavior

| Failure | Existing behavior | Certification expectation/gap |
|---|---|---|
| Expired OAuth token | Structural test refreshes if expiry metadata passed and refresh token exists | Provider operations should refresh before use; currently inconsistent |
| Revoked token / 401 / 403 | CRM categorizes authentication, non-retryable; connection remains “connected” | Mark connection reconnect-required and block until reconnect |
| Invalid refresh token | Validation marks reconnect-required; Salesforce CRM maps many token 400s as validation | Normalize as authentication/reconnect-required |
| 429 | CRM retryable, up to configured attempts | Honor `Retry-After`, add bounded backoff/jitter |
| 5xx / 408 / 425 / timeout | CRM retryable; OpenAI SDK retries; Slack generic retryable | Verify attempt history and exhaustion; avoid retry storm |
| Malformed response | Missing CRM ID is permanent validation failure; OpenAI missing/invalid parsed output falls back/manual review | Add provider-specific malformed fixtures |
| Duplicate record / 409 | CRM treats as non-retryable validation | Verify stable identity and surface actionable duplicate error |
| Invalid CRM field / 400/422 | Non-retryable validation | Verify exact error category and no repeated writes |
| Disconnect during operation | Future resolution is blocked, but an already-instantiated provider can finish | Certify defined semantics; no in-flight cancellation exists |
| Slack provider error | All SDK exceptions retryable; exact exception text persisted | Classify auth/rate-limit/validation separately and sanitize |

## 17. Provider account creation checklist

### CODEX CAN DO

- Reconfirm callback paths/scopes from the checked-out revision.
- Generate app manifests/property lists and controlled test payloads.
- Guide the developer-console/browser sequence and stop for human-only gates.
- Validate non-secret configuration names and callback equality.
- After humans store credentials, run controlled OAuth/app/API tests and collect redacted evidence.
- Verify provider records/messages through authorized test accounts without printing secrets.

### HUMAN MUST DO

- Login, MFA, CAPTCHA, email/phone verification, legal terms, billing, marketplace/program enrollment, and admin consent.
- Create/own the Salesforce Developer Edition or sandbox, HubSpot developer/test account, Slack test workspace/channel, Google Cloud project/test mailboxes, Entra tenant/test mailbox, and OpenAI project/billing.
- Create apps/registrations, approve permissions, and decide tenant/account distribution policies.
- Create Salesforce custom fields and HubSpot custom unique properties/pipeline mappings.
- Place client secrets, API keys and fallback secrets into the approved deployment secret manager; never paste them into chat, source, frontend variables, or screenshots.
- Approve any Google verification/production publishing or Microsoft tenant consent process.

Per-provider minimum environments:

- Salesforce: Developer Edition recommended; External Client App; API-enabled test user.
- HubSpot: developer platform account + developer test account; OAuth app.
- Slack: isolated workspace + private test channel + bot app.
- Google: separate test Cloud project + OAuth consent testers + two controlled Gmail/Workspace accounts for sender/recipient.
- Microsoft: test Entra tenant or approved test users + controlled mailbox + app registration.
- OpenAI: isolated project, project-scoped key, approved model access and spending limit.

## 18. Browser workflow (future execution)

For every OAuth provider: repository revision/callback check → provider developer console → human login/MFA → create app → enter exact callback/scopes → human stores client ID/secret in deployment secret manager → restart/validate non-secret settings → app Integrations view → Connect → provider login/consent → callback → verify connection row and secret reference → perform a real provider operation → verify in provider UI.

Provider-specific additions:

- Salesforce: create custom fields/external ID before Connect; confirm login host.
- HubSpot: create unique properties and validate pipeline stages before Connect.
- Slack: install bot, invite it to the chosen channel, then select/store channel once that feature exists.
- Google: enable Gmail API, configure consent test users, then implement and run acquisition.
- Microsoft: choose tenant/account type, add delegated Graph permissions, then implement and run acquisition.
- OpenAI: no OAuth; human creates/stores project key, operator selects model, then application sends a synthetic lead and verifies persisted live provider/model.

Do not perform this workflow until the blocking code/deployment gates below are closed.

## 19. Code/deployment gaps

### BLOCKER

1. Gmail acquisition is missing.
2. Outlook acquisition is missing.
3. Slack OAuth tokens and workspace/channel selection are not wired to notification runtime.
4. No real managed-secret backend implementation is present.
5. “Test connection” never calls a provider; real credentials can be reported valid by shape alone.
6. Remote OAuth revocation is unsupported for all providers.
7. Runtime provider operations do not consistently invoke refresh/reconnect lifecycle; HubSpot can use a stale access token.

### SHOULD FIX BEFORE TESTING

1. OAuth success redirects to frontend root, while Integrations is state-only; completion notice/refresh is easily lost.
2. Provider OAuth denial/error callback is not modeled; callback requires `code` and can return raw 422 instead of redirecting safely.
3. Slack non-rotating tokens get a synthetic one-hour expiry and later appear expired.
4. OpenAI workspace API-key card is inert; Slack/OpenAI availability labels overstate runtime wiring.
5. Production validation requires environment fallback CRM/Slack secrets even for workspace OAuth connections.
6. Add immutable integration lifecycle audit events.
7. Add retry backoff/jitter and `Retry-After`; classify Slack errors.
8. Confirm/migrate HubSpot API version and 2026 write/association enforcement.
9. Add automatic connection-state transition on provider authentication failures.

### CAN WAIT

1. Salesforce Opportunity sync (disabled by default).
2. Slack interactive actions and broad channel discovery.
3. Gmail Pub/Sub and Graph webhooks if bounded polling is accepted for initial certification.
4. Provider account display-name enrichment.
5. Invitation email delivery: invitation tokens are generated/hashed and then deliberately discarded by API routes; no mail delivery adapter exists. This remains a separate deferred requirement and was not implemented.

No blocker was fixed in 7C0 because none is required merely to register an app, and the request forbids expanding this audit into provider implementation. The blockers must be addressed before their respective real certification stages.

## 20. Recommended Phase 7C execution order

The nominal order should be adjusted slightly so shared blockers are resolved before account-specific certification:

1. **7C1 — Provider/account setup plus shared readiness gate:** choose dev/staging public API domain, deploy managed secret backend, fix live validation/revocation/refresh, fix OAuth return UX, create human-owned test environments/apps, store credentials.
2. **7C2 — Salesforce:** create schema, connect, run 10-step CRM certification.
3. **7C3 — HubSpot:** create properties/pipeline, connect, run 10-step CRM certification.
4. **7C4 — Slack:** first wire workspace token/channel runtime, then connect/post/disconnect/reconnect certification.
5. **7C7 — OpenAI (move earlier):** it is independent and already has a real runtime adapter; certify before mailbox end-to-end work.
6. **7C5 — Gmail:** implement acquisition, then controlled-mail certification.
7. **7C6 — Outlook:** implement acquisition, then controlled-mail certification.
8. **7C8 — Cross-provider end-to-end:** controlled email through OpenAI, workspace ICP, deterministic scoring, routing, chosen CRM and Slack, including RBAC/failure/replay tests.

Moving OpenAI ahead of Gmail/Outlook provides a verified reasoning dependency before the mailbox adapters feed it. Provider registration and Phase 7C1 were not started in this audit.

## Official platform references checked

- Salesforce REST OAuth and External Client Apps: https://developer.salesforce.com/docs/platform/api-rest/guide/intro-oauth-and-connected-apps.html
- Salesforce PKCE/scopes: https://developer.salesforce.com/docs/platform/data-loader-impl/guide/loader-eca-oauth-pkce.html
- HubSpot OAuth/public apps: https://developers.hubspot.com/docs/guides/apps/public-apps/overview
- HubSpot current API versioning: https://developers.hubspot.com/docs/api/how-to-use-hubspot-api
- Slack OAuth v2 and revocation: https://docs.slack.dev/authentication/installing-with-oauth/
- Slack `chat:write`: https://docs.slack.dev/reference/scopes/chat.write/
- Gmail server-side OAuth: https://developers.google.com/workspace/gmail/api/auth/web-server
- Gmail scopes: https://developers.google.com/workspace/gmail/api/auth/scopes
- Microsoft Graph delegated auth: https://learn.microsoft.com/en-us/graph/auth-v2-user
- Microsoft Graph permissions: https://learn.microsoft.com/en-us/graph/permissions-reference
- OpenAI Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs

PHASE 7C0 COMPLETE
