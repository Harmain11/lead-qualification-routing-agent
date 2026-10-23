---
name: Workspace manual-review settings
description: Product and configuration rules for workspace-configurable lead analysis review criteria.
---

Analysis Manual Review criteria are workspace-configurable and remain separate from Integration provider import-approval settings. If a workspace has no explicit stored review override, preserve the shared routing configuration as the base. Changing criteria applies to new and requalified leads; it does not silently recalculate existing saved review decisions.

**Why:** The user chose to change analysis review rules while keeping import-approval settings separate, and existing review states should not be rewritten without an explicit request.

**How to apply:** Keep both policies independent. Treat missing or empty workspace review settings as no override, and do not bulk-re-evaluate existing leads unless the user asks.
