---
name: CloudTalk API boundaries
description: CloudTalk API hosts, authentication, outbound receipts, and caller-ID handoff behavior.
---

Use HTTP Basic Auth with the Access Key ID as username and Access Key Secret as password on both CloudTalk API hosts. Core agents, calls, and call-history routes use `https://my.cloudtalk.io/api/`; Conversation Intelligence routes use `https://api.cloudtalk.io/v1/`.

CloudTalk's create-call endpoint accepts an agent ID and E.164 callee number but does not return a call ID or accept an idempotency key. For API-created calls, preserve Corral's durable request receipt before posting, never blindly retry an uncertain submission, and use call history to reconcile it.

For direct calls that need per-call caller-ID selection, use CloudTalk's documented `ct+tel` Desktop deep link with `from=` set only to a normalized number in the configured agent's `associated_numbers` (including its verified default). The public create-call API accepts only the agent and callee, so it cannot apply a caller-ID selection.

**Why:** CloudTalk's published docs use different hosts for the core API and Conversation Intelligence, and call creation does not supply a provider-side duplicate guard.

**How to apply:** When adding CloudTalk API operations, confirm each endpoint's documented host and envelope; do not retrieve recording media when a provider call-detail link is sufficient.

**Why:** The user asked to select the outbound number for each direct call and approved the CloudTalk Desktop handoff after learning that the REST API cannot select one.

**How to apply:** Preserve Corral's individual confirmation and consent, do-not-call, and residency checks before preparing the link. The Desktop app must use the configured agent, may auto-dial per its Click to Call setting, and keeps call results outside Corral's automatic call history.
