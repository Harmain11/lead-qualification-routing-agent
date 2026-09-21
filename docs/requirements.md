# Requirements and decisions

## Product objective

Create a standalone demo product that ingests inbound inquiries from multiple channels, normalizes them into one lead record, understands context and intent, scores the lead against a configurable Ideal Customer Profile (ICP), qualifies and routes it with a written rationale and next action, synchronizes the outcome to one selected CRM, notifies Slack, falls back to manual review on uncertainty or failure, and exposes channel, pipeline, conversion, rep, and revenue analytics.

## Functional requirements

| Area | Requirement | Architectural response |
| --- | --- | --- |
| Intake | Website form, direct mailbox, LinkedIn, and one additional channel | Channel adapters emit a common `LeadReceived` event; recommended fourth channel is Clutch notification email for v1 because it avoids depending on a public Clutch API |
| Attribution | Source channel must remain visible through the complete lifecycle | Immutable source event plus canonical `source`, `source_detail`, and attribution fields on the lead |
| Normalization | Different payloads become one lead shape | Versioned normalizer port with provider-specific mapping and deduplication keys |
| Context and intent | Extract product, pain, urgency, budget, role, stage, existing tools, history, and channel intent | Typed analysis schema returned by a reasoning provider and persisted as an inspectable stage result |
| ICP | Structured, editable, versioned grounding configuration | YAML/database-backed ICP profile with a version identifier and parsed rules |
| Qualification | 0–100 explainable composite score | Hard disqualifiers first; ICP fit 40, intent/urgency 35, engagement quality 25; weights remain configurable |
| Routing | Hot, warm, needs qualifying, nurture, and disqualify outcomes | Deterministic score-band policy plus rep/queue matching and action-plan generation |
| CRM | Use either Salesforce or HubSpot without duplicating application logic | `CrmProvider` port with separate adapters and normalized sync results |
| Notifications | New-lead and routing messages in a designated Slack channel | `NotificationProvider` port with an idempotent outbox event |
| Reliability | Any failed, low-confidence, or ambiguous path must be visible | Retry policy, dead-letter state, manual-review case, and operator audit trail |
| Dashboard | Channel share, conversion, pipeline, time comparisons, reps, and revenue | PostgreSQL read models/queries over lifecycle events with weekly/monthly/annual period filters |
| Demo data | Dashboard must remain credible when live volume is low | Explicitly labeled illustrative seed dataset and a separate demo-data namespace |

## Scope decisions for implementation

1. **Fourth channel recommendation:** implement Clutch inquiry notification parsing through the mailbox adapter for v1. Keep a social DM/comment adapter contract for a later provider; do not imply unsupported API access.
2. **LinkedIn feasibility gate:** verify official permissions in discovery. If message access is unavailable, ship an explicitly labeled LinkedIn lead-form/export intake path rather than scraping or bypassing platform controls.
3. **CRM mode:** one active provider per environment, selected by configuration. Build Salesforce first, then HubSpot against the same port.
4. **Scoring:** retain the 40/35/25 defaults but make weights and bands data-driven and versioned so an ICP can tune them without a code release.
5. **Autonomy boundary:** no autonomous negotiation or close. Any outbound response, scheduling, or high-impact external mutation must be an explicit, auditable use case with human approval where required.

## Non-functional requirements

- Idempotent webhook and mailbox processing; replayable raw events.
- P95 initial intake acknowledgement under two seconds; enrichment and routing are asynchronous and observable.
- At-least-once job delivery with bounded retries and dead-letter/manual review handling.
- Typed contracts at API, queue, provider, and LLM boundaries.
- Full decision history: policy version, model/provider metadata, evidence, confidence, score breakdown, and operator overrides.
- Provider secrets stored outside Git and preferably in a managed secret store in deployed environments.
- PII-safe structured logs, encrypted transport, least-privilege OAuth scopes, and configurable retention.
- Dashboard metrics must be reproducible from persisted lifecycle events and must support UTC storage with an explicit display timezone.

## Success criteria mapping

- Four channel types can be ingested and correctly attributed.
- Every lead produces a summary, action plan, score breakdown, rationale, and routing result or manual-review case.
- A single CRM provider can be switched by configuration without changing use cases.
- The dashboard supports channel performance, pipeline, conversion, rep, revenue, and weekly/monthly/annual comparisons.
- A rehearsed end-to-end flow includes at least one unusual lead whose decision trail can be inspected.
