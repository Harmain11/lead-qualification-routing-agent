---
name: CloudTalk API boundaries
description: CloudTalk host split, authentication, and outbound request receipt behavior.
---

Use HTTP Basic Auth with the Access Key ID as username and Access Key Secret as password on both CloudTalk API hosts. Core agents, calls, and call-history routes use `https://my.cloudtalk.io/api/`; Conversation Intelligence routes use `https://api.cloudtalk.io/v1/`.

CloudTalk's create-call endpoint accepts an agent ID and E.164 callee number but does not return a call ID or accept an idempotency key. Preserve Corral's durable request receipt before posting, never blindly retry an uncertain submission, and use call history to reconcile it.

**Why:** CloudTalk's published docs use different hosts for the core API and Conversation Intelligence, and call creation does not supply a provider-side duplicate guard.

**How to apply:** When adding CloudTalk API operations, confirm each endpoint's documented host and envelope; do not retrieve recording media when a provider call-detail link is sufficient.
