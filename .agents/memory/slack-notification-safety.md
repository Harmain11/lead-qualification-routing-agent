---
name: Slack notification safety
description: Product policy for Slack setup, credentials, direct messages, and channel fallback.
---

Slack tokens must only be entered in Corral's secure token form. Channel alerts use the selected channel. A direct message requires an explicit per-recipient or per-notification choice, the recipient's Slack member ID, and `chat:write` plus optional `im:write`. Never request `chat:write.public` by default. A failed DM may use a configured channel only when fallback was explicitly selected for that notification.

**Why:** The user requires Slack DMs never to be a surprise broadcast and Slack tokens to remain in the secure form.

**How to apply:** Keep channel delivery as the default; label `im:write` optional; make DM and fallback choices explicit; surface useful delivery errors without silently posting elsewhere.
