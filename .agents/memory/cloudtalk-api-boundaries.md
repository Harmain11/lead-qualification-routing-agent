---
name: CloudTalk API boundaries
description: CloudTalk API hosts, authentication, outbound receipts, and caller-ID handoff behavior.
---

Use HTTP Basic Auth with the Access Key ID as username and Access Key Secret as password on both CloudTalk API hosts. Core agents, calls, and call-history routes use `https://my.cloudtalk.io/api/`; Conversation Intelligence routes use `https://api.cloudtalk.io/v1/`.

CloudTalk's create-call endpoint accepts an agent ID and E.164 callee number but does not return a call ID or accept an idempotency key. For API-created calls, preserve Corral's durable request receipt before posting, never blindly retry an uncertain submission, and use call history to reconcile it.

The CloudTalk create-call API accepts only the agent and E.164 callee, returns no call ID, and is not a browser softphone or per-call hang-up interface. The current CloudTalk dialer UI uses the embedded Phone directly instead of submitting this API request.

**Why:** CloudTalk's published docs use different hosts for the core API and Conversation Intelligence, and call creation does not supply a provider-side duplicate guard.

**How to apply:** When adding CloudTalk API operations, confirm each endpoint's documented host and envelope; do not retrieve recording media when a provider call-detail link is sufficient.

**Why:** On October 20, 2026, the user chose the embedded CloudTalk Phone as the direct-calling surface, replacing Corral's number-entry/API start flow. The user accepts that Phone keypad calls bypass Corral's safeguards.

**How to apply:** Show the embedded Phone only to members with `dialer.call` permission, open it when CloudTalk is selected, and keep its iframe mounted across app navigation. Clearly warn that its keypad calls bypass Corral's consent, do-not-call, and residency checks and may only appear in CloudTalk history. Preserve Corral's safeguarded API flow for other supported providers. If a future flow uses the CloudTalk create-call API, keep its durable request receipt and never blindly retry an uncertain submission. Do not represent the VoiceAgent emergency-stop endpoint as a per-call hang-up control.

CloudTalk's embedded Phone requires a minimum iframe size of 420 × 700 px. Its documented postMessage contract lists call and contact events from the iframe; it does not document parent commands for editing keypad input.

**Why:** undersized mobile frames can disrupt the provider's own keypad layout, while the cross-origin iframe prevents Corral from directly inserting or controlling its buttons.

**How to apply:** Keep the CloudTalk iframe at 420 × 700 px internally and let the surrounding dock scroll or scale to fit. Do not claim a Corral-side backspace control can edit the provider's number field unless CloudTalk documents a supported command.

On mobile, give the CloudTalk dock the full safe-area viewport and keep its notices compact so the embedded phone remains legible. Preserve the complete keypad safety warning.

**Why:** The user chose compact notices and a larger phone after the embedded UI appeared too small in a mobile screenshot.

**How to apply:** Use the available screen height for the phone dock instead of a short bottom-sheet cap, and reduce notice copy/padding without hiding safety details.

Incoming-call browser alerts are a separate Corral feature, explicitly enabled by each member for their current workspace. They listen for CloudTalk's documented `ringing` event; they do not enable CloudTalk's unsupported embedded notifications.

**Why:** the user chose Corral browser alerts as the supported alternative to CloudTalk's disabled embedded notifications.

**How to apply:** Request browser permission only after the member selects Enable alerts, keep notification text generic, and open the embedded phone when a Corral alert is clicked.
