---
name: Meeting scheduling safety
description: Product requirements for Gmail-based meeting recommendations in Corral.
---

Read the tracked Gmail conversation to detect meeting intent and suggest availability from workspace-connected calendar accounts. Do not send a meeting email or create an event automatically; create the event only after an admin explicitly confirms a suggested slot.

**Why:** The user clarified the desired flow: read first, recommend slots to the admin, and add the meeting to an account only after confirmation.

**How to apply:** Keep Gmail analysis read-only, scope provider connections to the workspace, and preserve the final human confirmation step before any calendar write.
