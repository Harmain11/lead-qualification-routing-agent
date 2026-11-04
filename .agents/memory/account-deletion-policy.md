---
name: Account deletion policy
description: Self-only account deletion and protection of other workspace members.
---

Only the signed-in person may delete their own Corral account. Workspace admins must not gain permission to delete another person's account.

**Why:** The user explicitly selected “Only the signed-in user” for the Settings deletion option.

**How to apply:** Bind deletion to the authenticated identity, not an account ID supplied by the browser. Preview the permanent effects and require explicit confirmation. Preserve shared workspaces when a member leaves, and block an owner's deletion while other current members would lose their workspace.
