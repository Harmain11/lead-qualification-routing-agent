---
name: CloudTalk API boundaries
description: CloudTalk API hosts, authentication, outbound receipts, and caller-ID handoff behavior.
---

Use HTTP Basic Auth with the Access Key ID as username and Access Key Secret as password on both CloudTalk API hosts. Core agents, calls, and call-history routes use `https://my.cloudtalk.io/api/`; Conversation Intelligence routes use `https://api.cloudtalk.io/v1/`.

CloudTalk's create-call endpoint accepts an agent ID and E.164 callee number but does not return a call ID or accept an idempotency key. For API-created calls, preserve Corral's durable request receipt before posting, never blindly retry an uncertain submission, and use call history to reconcile it.

For Corral direct calls, use the safeguarded CloudTalk create-call API and the configured agent's verified default caller ID. The public API accepts only the agent and E.164 callee, returns no call ID, and is not a browser softphone or per-call hang-up interface. The `ct+tel` Desktop link supports per-call caller-ID selection but is not the selected Corral flow.

**Why:** CloudTalk's published docs use different hosts for the core API and Conversation Intelligence, and call creation does not supply a provider-side duplicate guard.

**How to apply:** When adding CloudTalk API operations, confirm each endpoint's documented host and envelope; do not retrieve recording media when a provider call-detail link is sufficient.

**Why:** On October 20, 2026, the user chose API-started direct calls from Corral to preserve Corral's safeguards, accepting the agent's default caller ID. The user later accepted the full embedded CloudTalk Phone for answering and ending calls, including the risk that its keypad bypasses Corral's checks.

**How to apply:** Require Corral's per-call confirmation, consent, do-not-call, and residency checks; verify and show the agent's default caller ID before starting. The embedded Phone should be loaded and signed into the same agent first, remain mounted across app navigation, and clearly warn that calls placed from its keypad bypass Corral's checks. CloudTalk's docs do not guarantee API-started calls ring that embedded session, so confirm routing with a controlled test before claiming it works. Keep the durable request receipt and never blindly retry an uncertain submission. Do not represent the VoiceAgent emergency-stop endpoint as a per-call hang-up control.
