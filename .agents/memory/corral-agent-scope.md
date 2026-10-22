---
name: Corral Agent scope and approvals
description: The requested scope and confirmation rule for Corral's app-wide assistant.
---

Corral Agent is intended to be available throughout the workspace and support guidance, content generation, and tasks across leads/qualification/routing/follow-ups; email/meetings/CRM; dashboard/analytics/reports/automations; and ICP/integrations/settings/team access. Email requests should produce an editable draft with a clear send button and optional editable Cc/Bcc fields; only include Cc/Bcc recipients when the user asks or supplies them. The user must explicitly approve sending, the sender mailbox must remain selectable when multiple Gmail accounts are connected, and the assistant must not claim success until sending succeeds. Every other data or configuration mutation must also be proposed and receive explicit user approval before execution. Chat answers and generated drafts may be returned without changing workspace state.

Customize the assistant using the saved workspace business profile and identity. General chat may retrieve relevant records on demand only within the current workspace and the user's permissions. Search is limited to bounded matches from records already stored in Corral; do not imply a complete history or a live search of external Gmail, CRM, or dialer archives.

**Why:** the user asked for a workspace-customized assistant that can search all records they can access on demand, take actions across nearly all app areas, and include Cc/Bcc in reviewed email drafts; external sends and other mutations still require explicit approval.

**How to apply:** Add capabilities through typed, permission-scoped tools backed by existing APIs. Retrieve only relevant stored records for each request, disclose when results are bounded, use the saved business profile for workspace identity, and keep a clear review-and-approve step before every mutation.
