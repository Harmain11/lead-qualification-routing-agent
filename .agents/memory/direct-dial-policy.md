---
name: Manual direct dialing
description: Product rules for calling a manually entered phone number without first creating a lead.
---

Allow a representative to manually dial one international number without creating a lead first. Keep the call unlinked, and offer lead creation only as an explicit follow-up; never create a lead automatically. Require an admin to grant Dialer calling access per member; do not grant it by default to non-admin roles.

**Why:** The user asked to dial numbers that are not already leads, chose an optional Add lead step afterward, and chose explicit per-person admin grants for calling.

**How to apply:** Enforce the Dialer permission for outbound calls in both UI and API. Require explicit per-call confirmation, enforce workspace consent and residency rules, and honor any opt-out found on an existing lead. If a strict recorded-consent policy is enabled and an unlinked number has no recorded permission, block the call.
