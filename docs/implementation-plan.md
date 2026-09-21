# Phased implementation plan

This plan follows the nine-week build plan while making dependencies and feasibility gates explicit. Phase 0 established the architecture; the current repository implements the local foundation and first vertical slice described below. External providers remain deferred.

## Current implementation — local foundation and vertical slice

- FastAPI application with typed lead endpoints and health checks.
- PostgreSQL SQLAlchemy models plus Alembic migration and local Docker Compose dependencies.
- Canonical lead normalization shared by the future channel adapters.
- File-backed ICP and routing policies with deterministic 40/35/25 scoring.
- Hard disqualifiers, explainable score lines, routing bands, seed records, and automated unit tests.
- React/TypeScript manual intake and decision-trail view.
- Redis/Celery boundary scaffolded without unnecessary background jobs.
- Durable source-event lifecycle with replay-safe channel adapters for website, email, LinkedIn fixtures, and the selected Clutch mailbox-boundary fixture.

## Phase 0 — foundation and architecture (complete in this change)

- Convert the build plan into testable requirements and documented decisions.
- Establish the modular monolith layout and provider-port boundaries.
- Define environment variables, ICP/routing configuration shapes, persistence requirements, and local setup guidance.
- Add repository hygiene: `.gitignore`, `.env.example`, documentation, and safe placeholder configuration.

## Phase 1 — discovery and feasibility (Week 1)

- Finalize the demo ICP with target industries, company size/revenue, geography, budget, buyer roles, pains, and hard disqualifiers.
- Confirm fourth-channel choice; recommended path is Clutch notification email parsing.
- Verify LinkedIn official API permissions; decide whether live messages or lead-form/export is viable.
- Confirm CRM default and establish Salesforce/HubSpot sandbox access.
- Approve dashboard wireframe, demo data policy, timezone, retention, and review roles.
- Produce provider access checklist and test fixtures without putting credentials in the repository.

**Gate:** no provider coding starts until the ICP schema, fourth-channel path, CRM mode, and LinkedIn scope are approved.

## Phase 2 — platform and intake (Week 2)

- Bootstrap Python/TypeScript projects, CI, Docker Compose, migrations, typed settings, and observability.
- Implement canonical domain entities, source-event idempotency, outbox, and audit events.
- Build website webhook and mailbox intake behind `ChannelIntakePort`.
- Add the LinkedIn adapter only for the approved official access path.
- Normalize all available sources into the canonical lead contract and verify deduplication.

### Phase 2 implementation status

- Completed the common `ChannelAdapter` boundary and immutable `SourceEvent` contract.
- Added source-event lifecycle fields: received, processing, processed, failed, and manual-review-capable status, attempts, error details, and timestamps.
- Added website HMAC signature support and safe JSON/schema validation.
- Added provider-neutral email, LinkedIn official-access fixture, and Clutch notification-email fixture adapters.
- Connected every adapter to the existing normalization and deterministic qualification pipeline without adding Phase 3 reasoning.
- Added replay, distinct-inquiry, processing-failure/retry, malformed-payload, attribution, and adapter mapping tests.

Live Gmail/Microsoft Graph, LinkedIn, Clutch mailbox access, queue execution, and external infrastructure remain integration-test/deployment work.

**Exit criteria:** website, email, LinkedIn path, and raw replay all produce the same canonical lead shape.

## Phase 3 — fourth channel and intelligence (Week 3)

- Add Clutch notification parsing through the mailbox boundary, or the approved social adapter if discovery proves it is viable.
- Implement context/intent schema and prompt/provider contract with fixture-based evaluation.
- Persist summaries, extracted signals, confidence, evidence, and action-plan suggestions.
- Add low-confidence and provider-error transitions to manual review.

**Exit criteria:** four representative channel fixtures produce inspectable normalized and enriched records.

### Phase 3 implementation status

- Completed the `ReasoningProvider` port with deterministic and OpenAI implementations.
- Added strict structured extraction for all context questions, explicit/inferred/unknown provenance, evidence, ICP criteria, confidence, summary, and prepared action.
- Persisted analysis runs and exposed them through the lead API and inspection UI.
- Kept hard exclusions and the 40/35/25 score and routing bands deterministic.
- Added fallback/manual-review behavior for timeout, unavailable provider, malformed schema, low confidence, and contradictory evidence.
- Added a reusable four-channel evaluation dataset. Live OpenAI execution remains credential-dependent and is not required for local tests.

## Phase 4 — ICP scoring and qualification (Week 4)

- Load versioned ICP configuration and validate its schema.
- Implement hard disqualifiers and the 40/35/25 score components.
- Implement score bands, rationale templates, evidence references, and regression tests.
- Tune against reviewed test leads, including at least one unusual lead.

**Exit criteria:** human-reviewed fixtures produce the expected score band and explanation.

## Phase 5 — routing, notifications, and review (Week 5)

- Implement rep/queue matching and next-action policy.
- Implement Slack notifications through the notification port.
- Implement the manual-review queue, reviewer assignment, override reasons, and audit trail.
- Exercise retry, duplicate, timeout, and partial-success behavior.

**Exit criteria:** a routed decision is visible to the assigned queue, Slack, and manual-review operators when applicable.

## Phase 6 — CRM abstraction and adapters (Weeks 6–7)

- Build the CRM port and normalized field mapping.
- Implement Salesforce first and test reads/writes in a sandbox.
- Add HubSpot using the same port and contract test suite.
- Persist external IDs and per-field sync errors; keep outbox retries independent from qualification.

**Exit criteria:** one configured CRM can sync the complete lead context, score, rationale, action plan, source, and lifecycle status; provider switching requires configuration only.

## Phase 7 — dashboard and demo data (Week 8)

- Build read models for channel volume/share, lead-to-SQL/closed conversion, pipeline stage, time-in-stage, rep performance, and revenue.
- Add weekly/monthly/annual period filters and period-over-period comparisons.
- Add channel and revenue filters and a clearly labeled illustrative dataset.
- Verify every dashboard metric against SQL fixtures and lifecycle events.

**Exit criteria:** the dashboard remains useful with low live volume and all named build-plan metrics render correctly.

## Phase 8 — hardening, rehearsal, and handoff (Week 9)

- Add end-to-end tests across all channels and the approved CRM mode.
- Run security, dependency, migration, accessibility, performance, and failure-injection checks.
- Rehearse a happy path and an unusual lead at least three times.
- Document operations, provider credential setup, data retention, demo reset/seed commands, and known limitations.
- Prepare the short technical write-up and obtain product sign-off.

**Definition of done:** all build-plan deliverables and success criteria are demonstrable, every external side effect is observable, and failures land in an actionable queue.
## Current Phase 4 implementation note

The current implementation treats routing, action plans, rep/queue assignment, and notifications as Phase 4 work, matching the active delivery sequence. It is complete locally with persisted `routing_decisions`, `notifications`, and `pipeline_events`, deterministic configuration precedence, manual-review routing, mock/Slack notification ports, retry state, and explicit reroute history. The CRM and dashboard work described in later phases remains intentionally untouched.
