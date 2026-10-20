---
name: CloudTalk API boundaries
description: CloudTalk host split, authentication, and outbound request receipt behavior.
---

Use HTTP Basic Auth with the Access Key ID as username and Access Key Secret as password on both CloudTalk API hosts. Core agents, calls, and call-history routes use `https://my.cloudtalk.io/api/`; Conversation Intelligence routes use `https://api.cloudtalk.io/v1/`.

CloudTalk's create-call endpoint accepts an agent ID and E.164 callee number but does not return a call ID or accept an idempotency key. Preserve Corral's durable request receipt before posting, never blindly retry an uncertain submission, and use call history to reconcile it.

Show assigned CloudTalk caller-ID numbers as read-only inventory in Corral. Do not add an Automatic/Smart Rotation control or claim which caller ID a Corral API call will use; those modes are managed in CloudTalk Phone and are not selectable through the create-call API.

**Why:** CloudTalk's published docs use different hosts for the core API and Conversation Intelligence, and call creation does not supply a provider-side duplicate guard.

**How to apply:** When adding CloudTalk API operations, confirm each endpoint's documented host and envelope; do not retrieve recording media when a provider call-detail link is sufficient.

**Why:** The user chose to keep calls in Corral while showing assigned numbers, without an in-app rotation switch, because CloudTalk's public call API does not expose the native rotation mode.

**How to apply:** Preserve Corral's explicit call confirmation and consent checks. Only show a specific caller ID for a call when CloudTalk returns evidence for that exact call.
