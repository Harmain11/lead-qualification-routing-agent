---
name: Corral Agent scope and approvals
description: The requested scope and confirmation rule for Corral's app-wide assistant.
---

Corral Agent is intended to be available throughout the workspace and support guidance, content generation, and tasks across leads/qualification/routing/follow-ups; email/meetings/CRM; dashboard/analytics/reports/automations; and ICP/integrations/settings/team access. Email requests should produce an editable draft with a clear send button and optional editable Cc/Bcc fields; only include Cc/Bcc recipients when the user asks or supplies them. The user must explicitly approve sending, the sender mailbox must remain selectable when multiple Gmail accounts are connected, and the assistant must not claim success until sending succeeds. Every other data or configuration mutation must also be proposed and receive explicit user approval before execution. Chat answers and generated drafts may be returned without changing workspace state.

Customize the assistant using the saved workspace business profile and identity. General chat may retrieve relevant records on demand only within the current workspace and the user's permissions. Search is limited to bounded matches from records already stored in Corral; do not imply a complete history or a live search of external Gmail, CRM, or dialer archives.

The user wants information added to existing leads directly from workspace chat, not only navigation to a lead page. Keep lead detail changes, notes, progress, and re-evaluation behind explicit review of the target and values. Save evidenced facts as profile data; do not silently turn AI recommendations into facts.

Lead follow-up drafting, review, editing, and sending should stay inside the AI agent conversation. Do not route the user to the separate Follow-ups section for these steps.

For direct call requests, state that Corral Agent cannot place or start the call. When the user has Dialer permission, provide a clickable action to open Dialer so they can choose the lead or number and start the call themselves.

**Why:** the user asked for call requests to explain that the AI cannot place calls and to provide a clickable route to Dialer.

**How to apply:** Only show the Dialer action when the current user has dialer.call access. Keep call-history questions read-only and never invoke a call-start action from chat.

Workspace snapshots must come from the server under the current permissions, not from browser-supplied context. UI-reported action outcomes may inform follow-up conversation, but never authorize a write or replace the target API's permission checks.

**Why:** the user asked for a workspace-customized assistant that can search all records they can access on demand, update leads directly from chat, keep follow-up email work in the agent, take actions across nearly all app areas, and include Cc/Bcc in reviewed email drafts; external sends and other mutations still require explicit approval. Browser-supplied snapshots are not authoritative, and custom permission overrides can separate agent execution from lead viewing.

**How to apply:** Add capabilities through typed, permission-scoped tools backed by existing APIs. Retrieve only relevant stored records for each request, disclose when results are bounded, use the saved business profile for workspace identity, and keep a clear review-and-approve step before every mutation.

For read-only questions about whether a lead exists or for its details, search stored leads within the current workspace and the user's lead-view permission. Report matching details; when several leads match, list their full names and details and ask which full name the user means. Distinguish no match from search failure or missing permission.

**Why:** the user expects Corral Agent to verify lead records and answer with details rather than returning a generic action-target error.

**How to apply:** Use the server's scoped lead search for lookup questions, do not require an AI-generated open-lead action to answer, and never report no match when search failed.
