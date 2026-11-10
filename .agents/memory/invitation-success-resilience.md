---
name: Invitation success resilience
description: Keep invitation creation success distinct from subsequent roster refresh failures and correlate server errors safely.
---

After an invitation-creation request succeeds, show the one-time access code immediately and treat member/invitation/audit refreshes as a separate background operation. A refresh failure must not make a successfully created invitation appear to have failed. Avoid unnecessary database reads after committing invitation changes when all response fields are already available.

**Why:** Production showed a pending invitation and audit record alongside an internal-error report, while the available telemetry could not identify the workspace or request outcome.

**How to apply:** Use this behavior for invitation creation and resend flows. For unhandled server errors, retain the request ID and safe workspace/actor context, but never log invitee emails, access codes, or tokens.

Invitation lifecycle regression tests must enable database foreign-key enforcement and cover both existing-account recipients and recipients who register after invitation.

**Why:** SQLite disables foreign-key enforcement by default. Tests therefore passed while production rejected a dependent audit insert for a newly created membership. New-account invitations take a different path and can succeed despite this bug.

**How to apply:** Exercise create, repeat/resend, acceptance, and transaction rollback with foreign keys enabled. Keep parent and audit writes atomic; do not weaken production constraints to make a test pass.
