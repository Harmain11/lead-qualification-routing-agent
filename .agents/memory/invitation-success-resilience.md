---
name: Invitation success resilience
description: Keep invitation creation success distinct from subsequent roster refresh failures and correlate server errors safely.
---

After an invitation-creation request succeeds, show the one-time access code immediately and treat member/invitation/audit refreshes as a separate background operation. A refresh failure must not make a successfully created invitation appear to have failed. Avoid unnecessary database reads after committing invitation changes when all response fields are already available.

**Why:** Production showed a pending invitation and audit record alongside an internal-error report, while the available telemetry could not identify the workspace or request outcome.

**How to apply:** Use this behavior for invitation creation and resend flows. For unhandled server errors, retain the request ID and safe workspace/actor context, but never log invitee emails, access codes, or tokens.
