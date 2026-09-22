# Production architecture

## Overview

Use a modular monolith with asynchronous stage execution. The API accepts provider events quickly, persists the immutable input, and enqueues a pipeline job. The worker executes each stage as an independently observable transition:

```text
Provider webhook/mailbox poll
        |
        v
Raw source event -> normalize -> canonical lead -> context/intent analysis
                                      |
                                      v
                             hard disqualifiers
                                      |
                                      v
                            ICP + intent + engagement score
                                      |
                                      v
                       qualification -> routing -> action plan
                                      |
                         +------------+-------------+
                         |                          |
                         v                          v
                 CRM outbox / sync             Slack outbox
                         |                          |
                         +------------+-------------+
                                      v
                         audit, analytics, manual review
```

The seven stages remain inspectable in the UI and in the database. Each stage writes a status, started/completed timestamps, version metadata, structured output, confidence, and error details. A lead never disappears because a downstream provider is unavailable.

## Bounded modules

### Domain

Owns the business language and policies, independent of FastAPI, SQLAlchemy, Celery, provider SDKs, and UI concerns.

Core aggregates and records:

- `Lead`, `Person`, `Company`, and `Interaction`
- `SourceEvent` and `Attribution`
- `IcpProfile` / `IcpProfileVersion`
- `ContextAnalysis`, `ScoreBreakdown`, `QualificationDecision`, and `RoutingDecision`
- `ManualReviewCase`, `IntegrationSync`, `Notification`, `PipelineEvent`, and `AuditEvent`

Important domain policies:

- normalize source data and deduplicate by provider event ID plus source-specific identity keys;
- apply hard disqualifiers before invoking an LLM or weighted score;
- calculate ICP fit, intent/urgency, and engagement quality separately before composing 0–100;
- map score bands to outcomes and queues deterministically;
- lower confidence or provider failure creates a manual-review case;
- external side effects are represented as outbox commands and are safe to retry.

### Application

Owns use cases and transaction boundaries. Examples are `AcceptSourceEvent`, `NormalizeLead`, `AnalyzeLeadContext`, `QualifyLead`, `RouteLead`, `ResolveManualReview`, `SyncLeadToCrm`, and `BuildAnalyticsSnapshot`.

Use cases depend on ports, not concrete SDKs:

- `ChannelIntakePort`
- `LeadRepository`, `DecisionRepository`, `EventRepository`
- `ReasoningProvider`
- `CrmProvider`
- `NotificationProvider`
- `JobDispatcher`, `Clock`, and `IdempotencyStore`

### API

FastAPI routes should be thin. They validate input, authenticate/authorize the caller or verify a provider signature, call an application use case, and return a typed response. Planned route groups:

- `/health/live`, `/health/ready`
- `/v1/intake/web`, `/v1/intake/{provider}/webhook`
- `/v1/leads`, `/v1/leads/{id}`, `/v1/leads/{id}/decision-trail`
- `/v1/review-queue`, `/v1/review-queue/{id}/resolve`
- `/v1/analytics/overview`, `/v1/analytics/channels`, `/v1/analytics/pipeline`, `/v1/analytics/reps`
- `/v1/configuration/icp`, `/v1/configuration/routing`

### Worker

The worker consumes durable jobs and invokes application use cases. Each task has an idempotency key, bounded exponential backoff, timeout, provider-specific rate limiting, and a dead-letter/manual-review path. Long-running AI or provider calls must never run inside the synchronous webhook request.

### Provider adapters

Provider adapters translate external payloads and auth details to internal ports. They may be added under `infrastructure/providers`, but provider SDK types must not leak into domain or application modules.

Planned adapters, intentionally absent now:

- website form: signed JSON webhook;
- mailbox: Gmail or Microsoft Graph adapter, with Clutch notification email parsing reusing the same mailbox boundary;
- LinkedIn: official API/lead-form/export adapter only, subject to permissions;
- optional social adapter: platform webhook contract for a future channel;
- Salesforce and HubSpot: normalized CRM port implementations;
- Slack: bot notification adapter;
- OpenAI: reasoning provider using typed structured output.

## Data and persistence

PostgreSQL is the system of record. Use UUID primary keys and UTC timestamps. Suggested tables:

`source_events`, `leads`, `people`, `companies`, `interactions`, `icp_profiles`, `icp_profile_versions`, `analysis_runs`, `score_breakdowns`, `qualification_decisions`, `routing_decisions`, `manual_review_cases`, `crm_syncs`, `notifications`, `pipeline_events`, `outbox_messages`, `audit_events`, and `demo_data_batches`.

Implementation requirements:

- `source_events(provider, external_event_id)` is unique for idempotency;
- raw provider payloads are JSONB with access controls and a retention policy;
- canonical searchable fields are relational and indexed, not buried in JSONB;
- decision records are append-only; an override creates a new record with actor and reason;
- `outbox_messages` is written in the same transaction as the state change and marked delivered only after provider acknowledgement;
- indexes cover source/time, score band, lifecycle stage, owner, review status, and period reporting;
- analytics begin as SQL read models/materialized views; introduce a warehouse only after observed volume requires it.

Redis is for queue transport, short-lived locks, rate limiting, and cache. It is not the source of truth. Object storage is optional for raw attachments and must use encrypted, expiring URLs.

## Decision and scoring model

1. Validate and normalize the event.
2. Apply spam and explicit ICP hard disqualifiers: students/hobbyists, direct competitors, prohibited company types, and other configured exclusions immediately become score 0 with a reason.
3. Run typed context/intent extraction for non-disqualified leads.
4. Calculate:
   - ICP fit: 40 points;
   - intent and urgency: 35 points;
   - engagement quality: 25 points.
5. Persist evidence and rationale for every component.
6. Map the composite score to the configured band:

| Score | Band | Default outcome |
| ---: | --- | --- |
| 80–100 | Hot | Senior rep/closer, priority alert, scheduling recommendation |
| 60–79 | Warm | Standard rep queue and action plan |
| 40–59 | Needs qualifying | SDR/junior rep qualifying task |
| 20–39 | Nurture | CRM nurture flag, no rep assignment |
| 0–19 | Disqualify | Archive with rationale |

Scores are not enough on their own. A decision also carries the evidence references, missing-data flags, confidence, policy version, prompt/model metadata where applicable, and recommended next action.

## LLM boundary and explainability

The future reasoning adapter should accept a normalized lead, relevant prior interactions, and a pinned ICP profile version, then return a strict typed object containing extracted fields, evidence quotes/field references, uncertainty flags, summary, and action-plan suggestions. The application validates the response and never lets model text directly select an arbitrary queue or call an external API.

The planned OpenAI adapter should use schema-constrained JSON output through the Responses API and explicitly configure storage/retention according to the project’s privacy policy. The official API reference documents JSON/structured output support and response metadata: <https://developers.openai.com/api/reference/cli/resources/responses/methods/create>.

### Phase 3 reasoning implementation

`ReasoningProvider` is a domain port. The offline deterministic provider and OpenAI Responses API provider return the same strict `ReasoningOutput` schema; provider code cannot assign points, choose a score band, or initiate external actions. Hard disqualifiers run before the selected provider. The application validates provider output, applies confidence policy, and then passes only extracted signals and evidence into deterministic scoring.

Each lead persists a Phase 3 `analysis_runs` record containing structured context, explicit/inferred/unknown provenance, evidence references, ICP evaluation, summary, provider/model/prompt metadata, confidence, review status, and error details. Provider failure uses deterministic extraction as a visible fallback and marks the source event for manual review rather than dropping the lead.

## Failure handling

- Invalid signature or malformed payload: reject with an auditable ingestion error; do not create a lead.
- Duplicate event: return the existing processing status without re-running side effects.
- Normalization failure: preserve the raw event and open manual review.
- Low confidence or missing required evidence: route to manual review.
- Queue/provider timeout: retry with backoff, then dead-letter and open manual review.
- CRM failure after qualification: keep the routing decision, retry the outbox, and show sync status in the dashboard.
- Slack failure: retry independently; never block lead qualification or CRM sync.
- Operator override: require a reason, preserve prior decision history, and emit a new audit event.

## Security and privacy

- Keep secrets in a secret manager in deployed environments; `.env` is local-only and ignored.
- Encrypt traffic in transit and database backups at rest; encrypt provider refresh tokens at rest.
- Verify webhook signatures, validate OAuth state/PKCE where applicable, and request minimum provider scopes.
- Use role-based access for operators, reviewers, and demo viewers; protect PII-bearing endpoints.
- Redact email addresses, message bodies, tokens, and raw provider payloads from logs and traces.
- Define retention and deletion workflows for raw messages, attachments, analysis outputs, and audit records.
- Apply prompt-injection defenses: treat inbound content as untrusted data, isolate instructions from lead text, and allow-list tools/actions.
- Do not make outbound communication or calendar commitments autonomously in v1.

## Observability and operations

Every pipeline run should carry `correlation_id`, `lead_id`, `source_event_id`, `stage`, and `policy_version`. Measure intake success, per-stage latency, queue age, retries, manual-review rate, low-confidence rate, CRM sync success, Slack delivery, score-band distribution, and dashboard query latency.

Expose liveness and readiness separately. Readiness checks PostgreSQL, Redis, migrations, and required configuration without leaking secret values.

## Phase 6.5 identity, OAuth, and interactive rendering

`User → WorkspaceMembership → Workspace` is the authoritative request path. `AuthSession` stores a token hash, CSRF hash, current workspace, expiry, last-seen time, and revocation state. The frontend does not render protected product views until `/v1/auth/session` succeeds.

OAuth is `authenticated workspace → state + PKCE transaction → provider authorization → validated callback → server-side token exchange → SecretStore reference → validation → connected`. Provider client credentials are application/operator configuration, not customer-entered values. The deterministic broker exercises the same contract locally. Managed secret storage, distributed callback locking, and provider-specific remote validation/revocation are deployment bindings rather than domain changes.

The dashboard owns one lazy-loaded Three.js/React Three Fiber scene. Geometry is stable, animation mutates render refs rather than React state, and the loop pauses with page visibility. The scene reads persisted lead, analysis, qualification, routing, notification, and CRM state. An accessible DOM inspector and mobile/reduced-motion/WebGL fallback expose the same information.

## Phase 7A1 production security boundary

Production configuration is validated at startup and cannot select development authentication, process-local secrets, development OAuth, deterministic/mock providers, insecure cookies, wildcard/non-HTTPS credentialed CORS, demo mode, or implicit local persistence endpoints. Development and test retain deterministic local adapters.

`SecretStore` exposes store, internal retrieve, replace/rotate, invalidate, and delete operations. `DevelopmentSecretStore` is process-local. `ManagedSecretStore` delegates to a deployment-supplied cloud-neutral backend, so AWS Secrets Manager, Azure Key Vault, Google Secret Manager, or Vault can be bound without changing application services or providers. Database records retain only opaque references and safe lifecycle metadata.

OAuth authorization state is random and persisted only as a hash. Redemption is bound to workspace, provider, callback, and actor, expires after ten minutes, and uses an atomic status transition before code exchange. PKCE uses S256; the temporary verifier is replaced after exchange or discarded after terminal failure. Disconnect commits local deactivation before attempting provider revocation and records only safe revocation/cleanup outcomes.

## Phase 7A2 operations boundary

Production uses a Redis-backed fixed-window rate limiter shared across API instances; local/test execution may use the deterministic in-memory adapter. A small policy catalog groups authentication, OAuth, credential submission, provider actions, and agent tools. Redis failures fail closed in production with a safe 503 response.

Every API request receives a validated `X-Request-ID` or a generated UUID and echoes it in the response. Provider operations add an internal operation ID, provider, and connection ID to safe logs and CRM HTTP request headers. Structured production logs pass through recursive redaction. Metrics/error reporting remains behind a vendor-neutral `Observability` protocol.

The API sends anti-framing, content-type, referrer, permissions, and CSP headers. HSTS is emitted only in production, where TLS must terminate at a trusted edge. The frontend hosting layer must set its own CSP allowing the configured API, same-origin assets, required inline styles, WebGL-compatible resources, and OAuth top-level redirects.

Liveness is process-only. Readiness checks PostgreSQL and required Redis, returns only component states, and ignores optional external providers. PostgreSQL pool/TLS and Redis timeout/retry settings are explicit. Celery is configured for bounded retries, late acknowledgements, time limits, and safe task-failure events, but no application task is registered; intake, CRM sync, and notifications currently execute synchronously.

The outbox table records `lead.qualified` in the lead transaction, but there is no outbox dispatcher today. CRM sync and notification flows separately commit their durable pending/syncing records before external provider I/O. Their current retries are bounded/manual and idempotent; asynchronous dispatch and automated outbox delivery remain deployment/readiness work rather than silently claimed as active behavior.
## Phase 4 routing implementation

The routing application service consumes the persisted qualification and Phase 3 analysis; it never recalculates the score. `RoutingConfig` contains queues, active/inactive reps, ordered rules, and action templates. Matching is deterministic: manual-review precedence first, then the lowest numeric rule precedence, then greatest criterion specificity, then rule name; otherwise the qualification-band route is used. Hard-disqualified leads are archived without a sales rep. Active reps are selected by geography/industry/service skill score, then configured priority and stable rep ID.

`routing_decisions` are append-only and mark only one record as current. Each decision stores the qualification link, matched rules, assignment reason, action plan, priority, SLA target, manual-review flag, and notification state. `notifications` stores provider attempts independently so Slack failure cannot remove a lead or routing decision. `pipeline_events` preserves the ordered received, normalized, analyzed, qualified, routed, and notified transitions for the lifecycle view. The mock notification provider is the default; Slack is an adapter selected by configuration.

## Phase 5 CRM synchronization implementation

`CrmProvider` accepts one `CanonicalCrmPayload` assembled from the persisted lead, analysis, qualification, and current routing decision. Salesforce and HubSpot types never cross this port. `config/crm.example.yaml` owns provider object/property names and owner mappings; `CRM_PROVIDER` selects exactly one active adapter.

The application writes a `crm_syncs` record in `pending` state before provider I/O, commits `syncing` before each attempt, and stores `synced`, `retryable`, or `failed` independently from the lead and routing transaction. The payload hash and unique idempotency key prevent duplicate attempts for unchanged data. A changed payload creates a new current history record while carrying forward provider entity IDs. Pipeline history appends a `crm` event after `notified` for either success or inspectable failure.

Salesforce uses REST sObject upsert by a configurable unique external-ID field. HubSpot uses CRM v3 batch upsert, contact identity by email, configurable unique company/deal properties, and v4 default associations. Both adapters classify authentication/configuration, rate-limit, transient transport, mapping, and permanent validation failures. The deterministic mock follows the same contract and is the local default.

## Phase 5.5 workspace and connection architecture

`Workspace` is the lightweight tenant root. Source events, leads, routing decisions, notifications, pipeline events, CRM synchronization records, outbox messages, provider connections, and agent-tool audit events carry `workspace_id`. Qualification and analysis ownership is inherited through their lead. Repository methods include workspace predicates; API authorization does not rely on filtering results after retrieval.

`ProviderConnection` is provider-neutral and stores account identity, authentication type, status, scopes, safe metadata, validation state, and a `secret_reference`. Plaintext credentials are not database fields. The development `SecretStore` is process-local; production replaces that port with a managed secret backend. OAuth is represented as `begin → state-validated callback → secret reference → validation → connected`. The local OAuth broker proves the contract without pretending to perform live provider authorization.

The long-term CRM selection is `workspace → active CRM connection → CrmProvider`. `CRM_PROVIDER` and environment credentials remain only a default-workspace/development fallback. Every CRM sync records the selected `provider_connection_id`, and provider entity IDs are looked up within workspace and connection scope so switching accounts cannot reuse another account's identifiers.

Production identity uses opaque, revocable server-side sessions. Passwords are salted with `scrypt`; only session/CSRF hashes are persisted. `get_workspace_context` authenticates the session, verifies active `WorkspaceMembership`, validates CSRF for cookie-authenticated mutations, and then derives `WorkspaceContext`. A requested workspace ID is never trusted without membership. Development headers are honored only behind the explicit local fallback setting.

## Agentic control plane

External actions cross a controlled boundary:

`Agent → ToolRegistry → workspace/actor permission check → structured input validation → provider tool → structured output → audit event`

The first implemented capability is `crm.create_or_update_lead`. The catalog reserves `crm.lookup_customer`, `crm.update_deal`, `notification.send`, `email.read`, `email.send`, and `calendar.schedule` without granting them implementation or permission by implication. Tool idempotency and audit records are deterministic.

The intended orchestration remains:

`Orchestrator → Intake/Context Agent → Qualification Agent → Routing Agent → Action Planning Agent → CRM Tool → Notification Tool`

Future agents may add follow-up, enrichment, and analytics/insight behavior. Authentication, authorization, tenant isolation, hard disqualifiers, score limits, routing thresholds, retries, validation, and idempotency remain deterministic application policy and cannot be overridden by an LLM.

## Phase 6 analytics read model and product shell

Analytics is a workspace-scoped application read service. Product endpoints never expose cross-workspace aggregates, and the frontend consumes structured server-side metrics rather than loading all records to calculate business analytics in the browser.

`LeadOutcome` is a separate commercial fact attached one-to-one to a lead. It stores pipeline stage, opportunity value, closed revenue, currency, and provenance (`crm`, `demo`, or unavailable) without mutating qualification or routing evidence. The workspace carries an explicit `is_demo` marker; the Phase 6 seed command writes only to the reserved demo workspace.

The application shell maps Dashboard, Leads, Pipeline, Agents / AI Activity, Analytics, Integrations, Manual Review, and Settings to the same workspace context. Agent activity is derived from durable pipeline events and controlled tool-audit events and exposes business-facing structured output only—never hidden chain-of-thought.
