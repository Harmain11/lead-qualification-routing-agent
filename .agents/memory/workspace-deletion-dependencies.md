---
name: Workspace deletion dependencies
description: Account deletion must handle restrictive foreign keys below workspace-owned rows.
---

Deleting a workspace can fail even when every table directly referencing `workspaces` uses CASCADE. Check indirect child relationships for RESTRICT constraints and remove only the workspace-owned link rows before deleting their parent records.

**Why:** Production account deletion failed because workspace-cascaded contacts were still referenced by `lead_contacts`.

**How to apply:** Before adding another permanent workspace/account deletion path, inspect the full foreign-key chain and test it with database foreign-key checks enabled. Keep the cleanup scoped to the workspace being removed.
