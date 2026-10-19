---
name: Slack notification safety
description: Product policy for Slack setup, credentials, direct messages, and channel fallback.
---

Slack tokens must only be entered in Corral's secure token form. Channel alerts use the selected channel. A direct message requires an explicit per-recipient or per-notification choice, the recipient's Slack member ID, and `chat:write` plus optional `im:write`. Never request `chat:write.public` by default. A failed DM may use a configured channel only when fallback was explicitly selected for that notification. Send at most one Slack lead alert per workspace and lead; later Gmail updates must not create another message once an alert is pending, retryable, or sent.

**Why:** The user requires Slack DMs never to be a surprise broadcast, Slack tokens to remain in the secure form, and repeated Gmail syncs not to re-alert the channel for a lead that already has a Slack notification.

**How to apply:** Deduplicate across routing decisions by workspace, lead, and provider. Keep retrying the original notification record; suppress a new route's Slack send when that lead already has a pending, retryable, or sent Slack delivery. Keep channel delivery as the default; label `im:write` optional; make DM and fallback choices explicit; surface useful delivery errors without silently posting elsewhere.
